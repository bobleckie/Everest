"""Unified competitor-intelligence orchestrator.

Drives the full pipeline end-to-end:

  Phase 1  load_context        — pull competitor row, settings, aliases.
  Phase 2  9 connectors        — run in parallel (ThreadPoolExecutor).
  Phase 3  dedupe + persist    — write deduplicated cards to competitor_evidence.
  Phase 4  synthesizer         — Opus pass → threads + timeline events.
  Phase 5  persist threads     — write competitor_threads + competitor_timeline_events.
  Phase 6  category drafts     — 8 drafters in parallel (max_workers=4),
                                  each handed the threads + evidence subset
                                  relevant to its category.
  Phase 7  done                — return summary.

Progress is reported via a callback that the router uses to update the
in-process job tracker so the UI can render a live timeline.
"""
from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy.orm import Session

from ...models import (
    AppSetting, Competitor, CompetitorEvidence, CompetitorThread,
    CompetitorTimelineEvent,
)
from .evidence_card import EvidenceCard
from .connectors import (
    courtlistener,
    sec_edgar,
    opencorporates,
    gleif,
    uk_contracts_finder,
    wikidata,
    google_places,
    internal_documents,
    internal_news,
    anthropic_websearch,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Phase descriptors — single source of truth for what the UI shows.
# Keeping these in code (not the DB) means the frontend timeline component
# stays data-driven: the router emits whichever phases the run actually
# walked through, and the UI simply renders them.
# ─────────────────────────────────────────────────────────────────────

# Connector phases run in parallel during phase 2; we still list them so the
# UI can show 9 mini-tiles flipping from queued → running → complete.
CONNECTOR_PHASES = [
    ("courtlistener", "Federal litigation (CourtListener)"),
    ("sec_edgar", "SEC EDGAR filings"),
    ("opencorporates", "Corporate registries (OpenCorporates)"),
    ("gleif", "Legal entity identifiers (GLEIF)"),
    ("uk_contracts_finder", "UK procurement awards"),
    ("wikidata", "Wikidata corporate graph"),
    ("google_places", "Google Places reviews"),
    ("internal_documents", "Uploaded documents"),
    ("internal_news", "News history"),
    ("anthropic_websearch", "Autonomous web research (Claude)"),
]

DOSSIER_CATEGORIES = [
    "leadership", "technology", "customer_service", "contracts",
    "financials", "strengths", "weaknesses", "strategy",
]

# Map dossier category → claim_classes that the drafter for that category
# should prioritize. The orchestrator hands each drafter its filtered slice
# of the evidence pool so categories don't have to slog through everything.
CATEGORY_CLAIM_AFFINITY = {
    "leadership":       {"leadership", "filing", "m_and_a", "news"},
    "technology":       {"breach", "filing", "regulatory", "news", "other"},
    "customer_service": {"review_signal", "litigation", "regulatory", "news"},
    "contracts":        {"contract_award", "contract_loss", "filing", "news"},
    "financials":       {"financial", "filing", "m_and_a", "news"},
    "strengths":        {"contract_award", "m_and_a", "filing", "news"},
    "weaknesses":       {"litigation", "breach", "regulatory", "contract_loss",
                         "review_signal", "news"},
    "strategy":         {"m_and_a", "contract_award", "contract_loss", "filing",
                         "news"},
}


# ─────────────────────────────────────────────────────────────────────
# Settings / aliases
# ─────────────────────────────────────────────────────────────────────

def _load_settings(db: Session) -> Dict[str, str]:
    """Read AppSetting rows into a flat dict, decoding JSON-encoded values."""
    out: Dict[str, str] = {}
    for row in db.query(AppSetting).all():
        try:
            v = json.loads(row.value_json) if row.value_json else None
        except (json.JSONDecodeError, TypeError):
            v = row.value_json
        if v:
            out[row.key] = v
    return out


def _resolve_aliases(comp: Competitor) -> List[str]:
    """Competitor.aliases is a JSON array column. Return a clean list of names."""
    raw = comp.aliases
    if not raw:
        return []
    try:
        v = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(v, list):
        return []
    return [str(x).strip() for x in v if x and str(x).strip()]


# ─────────────────────────────────────────────────────────────────────
# Connector fan-out
# ─────────────────────────────────────────────────────────────────────

def _summarize_for_websearch(cards: List[EvidenceCard], max_chars: int = 6000) -> str:
    """Tiny structured summary fed to the Anthropic web_search connector so
    Claude can see what's already been found and avoid duplicating effort."""
    if not cards:
        return "(no upstream evidence yet)"
    buckets: Dict[str, List[str]] = {}
    for c in cards:
        line = (
            f"- [{c.source_connector}] "
            f"{c.title[:120]}"
            + (f" ({c.event_date})" if c.event_date else "")
        )
        buckets.setdefault(c.claim_class, []).append(line)
    parts = []
    for klass, lines in sorted(buckets.items()):
        parts.append(f"### {klass} ({len(lines)})")
        # Cap each bucket so a flood from one connector doesn't drown the rest.
        parts.extend(lines[:20])
        if len(lines) > 20:
            parts.append(f"  …and {len(lines) - 20} more {klass} items")
    text = "\n".join(parts)
    return text[:max_chars]


def _run_connector(
    name: str,
    runner: Callable,
    cb: Callable,
    *args, **kwargs,
) -> List[EvidenceCard]:
    """Call one connector, emitting per-phase progress events."""
    cb("phase_started", {"phase_id": name})
    try:
        cards = runner(*args, **kwargs) or []
        cb("phase_completed", {"phase_id": name, "card_count": len(cards)})
        return cards
    except Exception as e:  # noqa: BLE001
        logger.exception(f"Connector {name} crashed: {e}")
        cb("phase_failed", {"phase_id": name, "error": str(e)})
        return []


def _run_all_connectors(
    db: Session,
    competitor: Competitor,
    aliases: List[str],
    settings: Dict[str, str],
    progress_cb: Callable,
) -> List[EvidenceCard]:
    """Fan out 9 deterministic connectors in parallel, then run the
    web_search connector serially so it sees the others' output as context."""
    # The 7 web-API connectors and 2 internal DB connectors all run in parallel.
    api_tasks = {
        "courtlistener":        (courtlistener.fetch,        (competitor.name, aliases, settings), {}),
        "sec_edgar":            (sec_edgar.fetch,            (competitor.name, aliases, settings), {}),
        "opencorporates":       (opencorporates.fetch,       (competitor.name, aliases, settings), {}),
        "gleif":                (gleif.fetch,                (competitor.name, aliases, settings), {}),
        "uk_contracts_finder":  (uk_contracts_finder.fetch,  (competitor.name, aliases, settings), {}),
        "wikidata":             (wikidata.fetch,             (competitor.name, aliases, settings), {}),
        "google_places":        (google_places.fetch,        (competitor.name, aliases, settings), {}),
    }

    all_cards: List[EvidenceCard] = []

    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {
            ex.submit(_run_connector, name, fn, progress_cb, *args, **kw): name
            for name, (fn, args, kw) in api_tasks.items()
        }
        for fut in as_completed(futures):
            try:
                all_cards.extend(fut.result())
            except Exception as e:  # noqa: BLE001
                logger.exception(f"Connector future failed: {e}")

    # Internal DB connectors — quick, sequential.
    all_cards.extend(_run_connector(
        "internal_documents",
        internal_documents.fetch_for_competitor,
        progress_cb, db, competitor.id,
    ))
    all_cards.extend(_run_connector(
        "internal_news",
        internal_news.fetch_for_competitor,
        progress_cb, db, competitor.id,
    ))

    # Web-search connector runs LAST, with the others' output as context.
    structured_summary = _summarize_for_websearch(all_cards)
    all_cards.extend(_run_connector(
        "anthropic_websearch",
        anthropic_websearch.fetch,
        progress_cb,
        competitor.name, aliases, settings,
        structured_summary=structured_summary,
    ))

    return all_cards


# ─────────────────────────────────────────────────────────────────────
# Persist evidence (with dedupe)
# ─────────────────────────────────────────────────────────────────────

def _dedupe_cards(cards: List[EvidenceCard]) -> List[EvidenceCard]:
    seen: set[str] = set()
    out: List[EvidenceCard] = []
    for c in cards:
        k = c.dedupe_key()
        if k in seen:
            continue
        seen.add(k)
        out.append(c)
    return out


def _persist_evidence(
    db: Session,
    competitor_id: int,
    cards: List[EvidenceCard],
) -> List[int]:
    """Write deduped cards to competitor_evidence. Existing rows for the
    same (competitor_id, source_connector, source_ref OR citation_url) are
    UPDATED in place so re-runs are idempotent.

    Returns the list of evidence ids in the same order as ``cards``.
    """
    now = datetime.utcnow()
    ids: List[int] = []

    # Build a lookup of existing rows so we can update in place. A re-run
    # should not double-write the same fact.
    existing = (
        db.query(CompetitorEvidence)
        .filter(CompetitorEvidence.competitor_id == competitor_id)
        .all()
    )
    by_key: Dict[str, CompetitorEvidence] = {}
    for row in existing:
        anchor = row.citation_url or row.source_ref or row.title
        if anchor:
            by_key[f"{row.source_connector}::{anchor}".lower()] = row

    for c in cards:
        key = c.dedupe_key()
        row = by_key.get(key)
        if row is None:
            row = CompetitorEvidence(
                competitor_id=competitor_id,
                source_connector=c.source_connector,
                source_ref=c.source_ref,
                citation_url=c.citation_url,
                title=c.title,
                snippet=c.snippet,
                claim_class=c.claim_class,
                event_date=c.event_date,
                jurisdiction=c.jurisdiction,
                amount_usd=c.amount_usd,
                payload_json=json.dumps(c.payload) if c.payload else None,
                confidence=c.confidence,
                fetched_at=now,
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            db.flush()  # populate row.id
        else:
            # Update in place — connectors may have richer data than last run.
            row.source_ref = c.source_ref or row.source_ref
            row.citation_url = c.citation_url or row.citation_url
            row.title = c.title or row.title
            row.snippet = c.snippet or row.snippet
            row.claim_class = c.claim_class or row.claim_class
            row.event_date = c.event_date or row.event_date
            row.jurisdiction = c.jurisdiction or row.jurisdiction
            row.amount_usd = c.amount_usd if c.amount_usd is not None else row.amount_usd
            if c.payload:
                row.payload_json = json.dumps(c.payload)
            row.confidence = c.confidence or row.confidence
            row.fetched_at = now
            row.updated_at = now
        ids.append(row.id)

    db.commit()
    return ids


# ─────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────

def build_phase_plan() -> List[Dict[str, str]]:
    """Static phase descriptor list the router uses to seed a new job.

    Returning a list of dicts keeps the UI fully data-driven — adding a new
    connector means appending here, no frontend change required.
    """
    plan: List[Dict[str, str]] = []
    plan.append({"id": "load_context", "label": "Load competitor context", "group": "Setup"})
    for name, label in CONNECTOR_PHASES:
        plan.append({"id": name, "label": label, "group": "Research"})
    plan.append({"id": "persist_evidence", "label": "Persist evidence pool", "group": "Research"})
    plan.append({"id": "synthesize_threads", "label": "Synthesize threads & timeline", "group": "Synthesis"})
    plan.append({"id": "persist_threads", "label": "Persist threads & timeline", "group": "Synthesis"})
    for cat in DOSSIER_CATEGORIES:
        plan.append({"id": f"category:{cat}", "label": f"Draft {cat}", "group": "Drafting"})
    return plan


def run_intelligence(
    db: Session,
    competitor_id: int,
    progress_cb: Optional[Callable] = None,
) -> Dict[str, Any]:
    """End-to-end run. Returns a summary dict for the router to log/return."""
    cb = progress_cb or (lambda event, payload: None)

    cb("phase_started", {"phase_id": "load_context"})
    competitor = db.query(Competitor).filter(Competitor.id == competitor_id).first()
    if not competitor:
        cb("phase_failed", {"phase_id": "load_context", "error": "competitor not found"})
        return {"error": "competitor not found"}
    aliases = _resolve_aliases(competitor)
    settings = _load_settings(db)
    cb("phase_completed", {
        "phase_id": "load_context",
        "competitor_name": competitor.name,
        "alias_count": len(aliases),
    })

    # Phase 2-3: connectors + persist.
    all_cards = _run_all_connectors(db, competitor, aliases, settings, cb)
    deduped = _dedupe_cards(all_cards)
    logger.info(
        f"Intelligence run for {competitor.name}: "
        f"{len(all_cards)} raw cards → {len(deduped)} unique"
    )

    cb("phase_started", {"phase_id": "persist_evidence"})
    evidence_ids = _persist_evidence(db, competitor_id, deduped)
    cb("phase_completed", {
        "phase_id": "persist_evidence",
        "evidence_count": len(evidence_ids),
    })

    # Phase 4-5: synthesizer + persist threads. Imported lazily so a missing
    # synthesizer module doesn't break the whole pipeline (we ship it next).
    threads_count = 0
    timeline_count = 0
    try:
        from .synthesizer import synthesize_threads_and_timeline, persist_threads
        cb("phase_started", {"phase_id": "synthesize_threads"})
        result = synthesize_threads_and_timeline(
            competitor=competitor,
            evidence_rows=db.query(CompetitorEvidence).filter(
                CompetitorEvidence.competitor_id == competitor_id
            ).all(),
            settings=settings,
        )
        cb("phase_completed", {
            "phase_id": "synthesize_threads",
            "thread_count": len(result.get("threads") or []),
            "timeline_count": len(result.get("timeline") or []),
        })

        cb("phase_started", {"phase_id": "persist_threads"})
        threads_count, timeline_count = persist_threads(db, competitor_id, result)
        cb("phase_completed", {
            "phase_id": "persist_threads",
            "thread_count": threads_count,
            "timeline_count": timeline_count,
        })
    except ImportError:
        logger.info("Synthesizer not yet available — skipping threads phase.")
    except Exception as e:  # noqa: BLE001
        logger.exception(f"Synthesizer failed: {e}")
        cb("phase_failed", {"phase_id": "synthesize_threads", "error": str(e)})

    # Phase 6: category drafters (parallel). Lazy import so a missing module
    # doesn't break the run (synthesizer ships in same change).
    try:
        from .category_drafter import draft_all_categories
        draft_all_categories(
            db=db,
            competitor=competitor,
            settings=settings,
            progress_cb=cb,
        )
    except ImportError:
        logger.info("Category drafter not yet available — skipping drafting phase.")
    except Exception as e:  # noqa: BLE001
        logger.exception(f"Category drafter failed: {e}")
        for cat in DOSSIER_CATEGORIES:
            cb("phase_failed", {"phase_id": f"category:{cat}", "error": str(e)})

    cb("all_done", {
        "evidence_count": len(evidence_ids),
        "thread_count": threads_count,
        "timeline_count": timeline_count,
    })
    return {
        "competitor_id": competitor_id,
        "competitor_name": competitor.name,
        "evidence_count": len(evidence_ids),
        "thread_count": threads_count,
        "timeline_count": timeline_count,
    }
