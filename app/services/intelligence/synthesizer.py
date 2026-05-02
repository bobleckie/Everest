"""Thread + timeline synthesizer.

One Claude Opus 4.7 call. Input: every persisted evidence row for the
competitor. Output: a JSON object describing:
  * THREADS — cross-category narratives like "acquire-the-winner pattern",
    "rebrand to escape lawsuit history", "M&A-inherited cyber risk". Each
    thread carries the evidence_ids that prove it and the dossier-category
    tags it should surface under.
  * TIMELINE — chronological events (M&A close, contract award, lawsuit
    filing, breach disclosure, leadership change). Each event optionally
    points to the thread it's part of.

The drafter pass downstream uses these instead of starting from scratch.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from ...models import (
    Competitor, CompetitorEvidence, CompetitorThread, CompetitorTimelineEvent,
)
from ..deep_vendor_research import _run_opus_with_search

logger = logging.getLogger(__name__)


# Hard cap on evidence items we shove into the prompt. With ~150 cards
# (which is what OPUS would yield), a tight 1-line summary stays well
# under Opus's input window.
MAX_EVIDENCE_FOR_PROMPT = 250


_SYSTEM = (
    "You are a senior capture-intelligence synthesizer. You read a deduplicated "
    "evidence pool about a competitor — court records, SEC filings, M&A graph "
    "edges, news items, web-research citations — and surface THREADS that "
    "connect facts across categories. You also produce a strictly chronological "
    "TIMELINE of significant events.\n\n"
    "RULES:\n"
    "1. Every thread and every timeline event MUST cite at least one evidence id "
    "   (the integers in the input). No claim without a citation.\n"
    "2. Produce 3-8 high-signal threads — not one per category. A great thread is "
    "   one that no single category drafter could have written alone, e.g. "
    "   'lost AZ contract → acquired the winner 11 months later' (which crosses "
    "   contracts + strategy + financials).\n"
    "3. Timeline events are chronological data points (one per ISO date). Cap at "
    "   60 events — pick the most decision-useful.\n"
    "4. Output VALID JSON ONLY, matching the schema below. No markdown, no prose "
    "   outside the JSON.\n\n"
    "Schema:\n"
    "{\n"
    '  "threads": [\n'
    "    {\n"
    '      "slug": "snake_case_id",                # stable across re-runs\n'
    '      "title": "Short title <80 chars",\n'
    '      "headline": "One-sentence decision-useful summary",\n'
    '      "narrative_markdown": "Markdown. Cite ev ids inline as [ev:42].",\n'
    '      "evidence_ids": [int, ...],             # MUST be from the input pool\n'
    '      "category_tags": ["weaknesses", "strategy"],  # which dossier categories\n'
    '                                              # this thread should surface in\n'
    '      "confidence": "high" | "medium" | "low"\n'
    "    }\n"
    "  ],\n"
    '  "timeline": [\n'
    "    {\n"
    '      "event_date": "YYYY-MM-DD",\n'
    '      "event_type": "m_and_a" | "contract_award" | "contract_loss" |\n'
    '                    "lawsuit_filed" | "lawsuit_resolved" | "regulatory_action" |\n'
    '                    "leadership_change" | "breach" | "financial" |\n'
    '                    "press" | "other",\n'
    '      "title": "Short title",\n'
    '      "description": "1-3 sentence description, cite ev ids inline.",\n'
    '      "jurisdiction": "AZ" | "UK" | null,\n'
    '      "amount_usd": float | null,\n'
    '      "evidence_ids": [int, ...],\n'
    '      "thread_slug": "<slug>" | null,         # if part of a named thread\n'
    '      "confidence": "verified" | "reported" | "inferred"\n'
    "    }\n"
    "  ]\n"
    "}\n"
)


def _evidence_summary_line(row: CompetitorEvidence) -> str:
    """One-line representation of an evidence row for the prompt."""
    bits = [f"id={row.id}", f"[{row.claim_class}]"]
    if row.event_date:
        bits.append(row.event_date)
    if row.jurisdiction:
        bits.append(row.jurisdiction)
    bits.append(f"({row.source_connector})")
    title = (row.title or "")[:160]
    bits.append(title)
    return " ".join(bits)


def _build_user_prompt(competitor: Competitor, rows: List[CompetitorEvidence]) -> str:
    aliases = []
    if competitor.aliases:
        try:
            aliases = json.loads(competitor.aliases)
        except (json.JSONDecodeError, TypeError):
            aliases = []
    alias_str = ", ".join(aliases) if aliases else "(none)"
    lines = [_evidence_summary_line(r) for r in rows[:MAX_EVIDENCE_FOR_PROMPT]]
    omitted = max(0, len(rows) - MAX_EVIDENCE_FOR_PROMPT)
    pool = "\n".join(lines)
    if omitted:
        pool += f"\n…and {omitted} more evidence items not shown."

    return (
        f"Competitor: {competitor.name}\n"
        f"Aliases / former names: {alias_str}\n\n"
        f"## Evidence pool ({len(rows)} items, showing {len(lines)})\n"
        f"{pool}\n\n"
        f"## Your task\n"
        f"Synthesize the threads + timeline per the system rules. Output JSON only."
    )


def _parse_json_envelope(text: str) -> Optional[Dict]:
    """Claude usually returns clean JSON when told to, but sometimes wraps in
    a ```json fence. Strip fences if present and json.loads."""
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.+?\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Last-ditch: try the largest balanced top-level object.
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
    return None


def synthesize_threads_and_timeline(
    competitor: Competitor,
    evidence_rows: List[CompetitorEvidence],
    settings: Dict[str, str],
) -> Dict[str, Any]:
    """Run one synthesizer pass. Returns
    ``{threads: [...], timeline: [...], raw_response_text: ...}``.
    """
    api_key = (settings.get("anthropic_api_key") or "").strip()
    model = settings.get("claude_research_model") or "claude-opus-4-7"

    if not api_key:
        logger.warning("Synthesizer skipped — no anthropic_api_key set.")
        return {"threads": [], "timeline": [], "raw_response_text": ""}
    if not evidence_rows:
        logger.info("Synthesizer skipped — no evidence rows.")
        return {"threads": [], "timeline": [], "raw_response_text": ""}

    user_prompt = _build_user_prompt(competitor, evidence_rows)

    try:
        # Synthesizer doesn't need web_search; max_searches=0 disables it.
        result = _run_opus_with_search(
            anthropic_key=api_key,
            model=model,
            system=_SYSTEM,
            user_content=user_prompt,
            max_searches=0,
            max_tokens=8000,
            timeout=300.0,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception(f"Synthesizer LLM call failed: {e}")
        return {"threads": [], "timeline": [], "raw_response_text": "",
                "error": str(e)}

    text = result.get("text") or ""
    parsed = _parse_json_envelope(text) or {}

    valid_ids = {r.id for r in evidence_rows}

    # Validate / normalize threads.
    threads_out: List[Dict] = []
    for t in (parsed.get("threads") or []):
        if not isinstance(t, dict):
            continue
        slug = (t.get("slug") or "").strip().lower()
        slug = re.sub(r"[^a-z0-9_]+", "_", slug).strip("_")
        if not slug:
            continue
        ev_ids = [int(x) for x in (t.get("evidence_ids") or []) if isinstance(x, (int, str)) and str(x).isdigit()]
        ev_ids = [i for i in ev_ids if i in valid_ids]
        if not ev_ids:
            # Threads without verifiable citations get dropped — the entire
            # point is to never invent.
            continue
        cats = [c for c in (t.get("category_tags") or []) if isinstance(c, str)]
        confidence = (t.get("confidence") or "medium").lower()
        if confidence not in {"high", "medium", "low"}:
            confidence = "medium"
        threads_out.append({
            "slug": slug,
            "title": (t.get("title") or slug)[:240],
            "headline": (t.get("headline") or None),
            "narrative_markdown": t.get("narrative_markdown") or "",
            "evidence_ids": ev_ids,
            "category_tags": cats,
            "confidence": confidence,
        })

    # Validate / normalize timeline events.
    timeline_out: List[Dict] = []
    for ev in (parsed.get("timeline") or []):
        if not isinstance(ev, dict):
            continue
        date = (ev.get("event_date") or "").strip()
        # Coerce to YYYY-MM-DD if possible; drop if not parseable.
        m = re.match(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?", date)
        if not m:
            continue
        date10 = "-".join(g or "01" for g in m.groups())
        ev_type = (ev.get("event_type") or "other").strip().lower()
        ev_ids = [int(x) for x in (ev.get("evidence_ids") or [])
                  if isinstance(x, (int, str)) and str(x).isdigit()]
        ev_ids = [i for i in ev_ids if i in valid_ids]
        if not ev_ids:
            continue
        amount = ev.get("amount_usd")
        try:
            amount = float(amount) if amount is not None else None
        except (TypeError, ValueError):
            amount = None
        confidence = (ev.get("confidence") or "reported").lower()
        if confidence not in {"verified", "reported", "inferred", "unverified"}:
            confidence = "reported"
        timeline_out.append({
            "event_date": date10,
            "event_type": ev_type,
            "title": (ev.get("title") or "(untitled)")[:240],
            "description": ev.get("description"),
            "jurisdiction": ev.get("jurisdiction"),
            "amount_usd": amount,
            "evidence_ids": ev_ids,
            "thread_slug": ev.get("thread_slug"),
            "confidence": confidence,
        })
    timeline_out.sort(key=lambda x: x["event_date"])

    return {
        "threads": threads_out,
        "timeline": timeline_out,
        "raw_response_text": text,
    }


def persist_threads(db: Session, competitor_id: int, synth: Dict[str, Any]) -> Tuple[int, int]:
    """Write threads + timeline events. Re-runs replace prior content for the
    same (competitor_id, slug) so we don't accumulate stale narratives."""
    now = datetime.utcnow()

    threads_in = synth.get("threads") or []
    slugs_in = {t["slug"] for t in threads_in}

    # Delete prior threads + their timeline events whose slug isn't in the new
    # set. (Threads that ARE in the new set we update in place to preserve ids.)
    prior_threads = (
        db.query(CompetitorThread)
        .filter(CompetitorThread.competitor_id == competitor_id)
        .all()
    )
    prior_by_slug = {t.slug: t for t in prior_threads}
    for slug, row in list(prior_by_slug.items()):
        if slug not in slugs_in:
            # Cascade-clean any timeline events tied to this thread.
            db.query(CompetitorTimelineEvent).filter(
                CompetitorTimelineEvent.thread_id == row.id
            ).update({"thread_id": None})
            db.delete(row)
            del prior_by_slug[slug]
    db.flush()

    # Upsert threads.
    slug_to_id: Dict[str, int] = {}
    for t in threads_in:
        existing = prior_by_slug.get(t["slug"])
        if existing:
            existing.title = t["title"]
            existing.headline = t.get("headline")
            existing.narrative_markdown = t.get("narrative_markdown")
            existing.evidence_ids_json = json.dumps(t.get("evidence_ids") or [])
            existing.category_tags_json = json.dumps(t.get("category_tags") or [])
            existing.confidence = t.get("confidence") or "medium"
            existing.updated_at = now
            slug_to_id[t["slug"]] = existing.id
        else:
            row = CompetitorThread(
                competitor_id=competitor_id,
                slug=t["slug"],
                title=t["title"],
                headline=t.get("headline"),
                narrative_markdown=t.get("narrative_markdown"),
                evidence_ids_json=json.dumps(t.get("evidence_ids") or []),
                category_tags_json=json.dumps(t.get("category_tags") or []),
                confidence=t.get("confidence") or "medium",
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            db.flush()
            slug_to_id[t["slug"]] = row.id

    # Replace timeline wholesale — much simpler than diffing, and the synthesizer
    # produces a complete chronology each run anyway.
    db.query(CompetitorTimelineEvent).filter(
        CompetitorTimelineEvent.competitor_id == competitor_id
    ).delete()

    timeline_in = synth.get("timeline") or []
    for ev in timeline_in:
        thread_slug = ev.get("thread_slug")
        thread_id = slug_to_id.get(thread_slug) if thread_slug else None
        db.add(CompetitorTimelineEvent(
            competitor_id=competitor_id,
            event_date=ev["event_date"],
            event_type=ev["event_type"],
            title=ev["title"],
            description=ev.get("description"),
            jurisdiction=ev.get("jurisdiction"),
            amount_usd=ev.get("amount_usd"),
            evidence_ids_json=json.dumps(ev.get("evidence_ids") or []),
            thread_id=thread_id,
            confidence=ev.get("confidence") or "reported",
            created_at=now,
            updated_at=now,
        ))
    db.commit()

    return len(threads_in), len(timeline_in)
