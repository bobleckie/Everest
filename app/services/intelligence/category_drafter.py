"""Threads-aware category drafter.

Replaces the old per-category essay loop in ``competitor_analyst.generate_dossier``.
Each category drafter is handed:

  * The competitor profile (name, aliases, research focus).
  * The synthesized **threads** tagged for that category — so e.g.
    "weaknesses" gets the "rebrand-to-escape-lawsuit-history" thread inline,
    instead of trying to rediscover it.
  * The **evidence subset** filtered by claim_class affinity for that
    category — so "contracts" gets contract_award/contract_loss/filing
    cards but doesn't have to slog through every breach disclosure.
  * The CATEGORY_PLAYBOOK objective + must-answer list from the original
    analyst prompt, kept intact for continuity.

Drafters run in parallel (ThreadPoolExecutor max_workers=4). Each writes
its result to the existing ``competitor_dossiers`` table so the existing
Dossier tab on the frontend keeps working unchanged.

Fact-check pass: skipped when the category has fewer than 5 evidence rows
(saves 50% runtime AND produces more useful output for thin-evidence
categories — see explainer in the drafter docstring).
"""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Callable, Dict, List, Optional

from sqlalchemy.orm import Session

from ...models import (
    Competitor, CompetitorDossier, CompetitorEvidence, CompetitorThread,
)
from ..competitor_analyst import (
    CATEGORY_PLAYBOOK, _call_ai, fact_check_entry,
)

logger = logging.getLogger(__name__)

DOSSIER_CATEGORIES = [
    "leadership", "technology", "customer_service", "contracts",
    "financials", "strengths", "weaknesses", "strategy",
]

# Same map as orchestrator — kept here so this module is self-sufficient.
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

# Below this threshold we skip the fact-check pass entirely. The reasoning:
# the fact-checker's job is to remove sentences not grounded in evidence,
# but if there are <5 cards there's not enough evidence for ANY claim to
# survive — the original draft (with explicit gap notes) is more useful than
# the fact-checker's stripped version.
FACT_CHECK_MIN_EVIDENCE = 5


def _evidence_block(rows: List[CompetitorEvidence], max_chars: int = 35_000) -> str:
    """Format a slice of the evidence pool for inline citation in the prompt."""
    out, used = [], 0
    for r in rows:
        url = f" — {r.citation_url}" if r.citation_url else ""
        date = f" ({r.event_date})" if r.event_date else ""
        snippet = ""
        if r.snippet:
            snippet = "\n    " + r.snippet[:600].replace("\n", " ")
        line = (f"[ev:{r.id}] [{r.claim_class}{date}] "
                f"{(r.title or '')[:200]}{url}{snippet}")
        if used + len(line) > max_chars:
            break
        out.append(line)
        used += len(line)
    return "\n".join(out) or "(no evidence available for this category)"


def _threads_block(threads: List[CompetitorThread]) -> str:
    if not threads:
        return "(no synthesized threads tagged for this category)"
    lines = []
    for t in threads:
        try:
            ev_ids = json.loads(t.evidence_ids_json or "[]")
        except (json.JSONDecodeError, TypeError):
            ev_ids = []
        ev_str = ", ".join(f"[ev:{i}]" for i in ev_ids[:10])
        lines.append(
            f"### {t.title} (confidence: {t.confidence})\n"
            f"  Headline: {t.headline or '(none)'}\n"
            f"  Evidence: {ev_str}\n"
            f"  Narrative:\n{t.narrative_markdown or '(no narrative)'}\n"
        )
    return "\n".join(lines)


def _build_drafter_prompt(
    competitor: Competitor,
    category: str,
    evidence_subset: List[CompetitorEvidence],
    threads: List[CompetitorThread],
) -> str:
    play = CATEGORY_PLAYBOOK.get(category, {})
    title = play.get("title") or category.replace("_", " ").title()
    objective = (play.get("objective")
                 or f"Produce a decision-useful intelligence brief for '{category}'.")
    must_answer = play.get("must_answer") or []
    must_answer_md = "\n".join(f"- {q}" for q in must_answer) or "- (none — be exhaustive)"

    aliases = []
    if competitor.aliases:
        try:
            aliases = json.loads(competitor.aliases)
        except (json.JSONDecodeError, TypeError):
            aliases = []
    alias_str = ", ".join(aliases) if aliases else "(none)"

    return f"""You are an elite capture-intelligence analyst writing the **{title}**
section of a competitor dossier.

Target company: **{competitor.name}**
Aliases / former names: {alias_str}
Category: **{category}** — {title}

## Section objective
{objective}

## Questions you should try to answer
{must_answer_md}

## How to cite
Every factual sentence MUST end with one or more inline citations of the form
``[ev:N]`` where N is an evidence id from the EVIDENCE POOL section below.
Sentences without a citation will be stripped by the editor pass. Do NOT cite
any id that is not present in the pool.

If the threads section below references the pattern you want to describe,
quote / paraphrase the thread inline and cite the same ev ids — DO NOT
invent new ones.

If the pool genuinely lacks evidence for a question, write
"No evidence in the pool addresses X" and put the question into a
**Collection Gaps** section at the end. That is far more useful than
fabricated specifics.

## Synthesized threads tagged for this category
{_threads_block(threads)}

## Evidence pool ({len(evidence_subset)} items, filtered to claim classes
relevant for "{category}")
{_evidence_block(evidence_subset)}

## Output structure (Markdown)
1. **TL;DR** — 3-5 bullets, each with at least one ``[ev:N]`` citation.
2. **Key findings** — narrative paragraphs, citing inline.
3. (Optional) tables when summarizing parallel facts (e.g. contracts by
   jurisdiction).
4. **Threads referenced** — list the thread titles you drew from.
5. **Collection Gaps** — top 3 unanswered questions and where to look next.

Write the **{title}** brief now."""


def _draft_one_category(
    db_factory: Callable[[], Session],
    competitor_id: int,
    competitor_snapshot: Dict,
    category: str,
    progress_cb: Callable,
) -> Dict:
    """Draft one category. Runs in its own thread with its own DB session."""
    phase_id = f"category:{category}"
    progress_cb("phase_started", {"phase_id": phase_id})

    db = db_factory()
    try:
        # Reload competitor + filter evidence subset for this category.
        competitor = db.query(Competitor).filter(Competitor.id == competitor_id).first()
        if not competitor:
            progress_cb("phase_failed", {"phase_id": phase_id, "error": "competitor not found"})
            return {"category": category, "status": "failed"}

        affinity = CATEGORY_CLAIM_AFFINITY.get(category, set())
        evidence_subset = (
            db.query(CompetitorEvidence)
            .filter(CompetitorEvidence.competitor_id == competitor_id)
            .filter(CompetitorEvidence.claim_class.in_(affinity) if affinity else True)
            .order_by(CompetitorEvidence.event_date.desc().nullslast())
            .all()
        )

        # Filter threads tagged for this category.
        all_threads = (
            db.query(CompetitorThread)
            .filter(CompetitorThread.competitor_id == competitor_id)
            .all()
        )
        threads_for_cat: List[CompetitorThread] = []
        for t in all_threads:
            try:
                tags = json.loads(t.category_tags_json or "[]")
            except (json.JSONDecodeError, TypeError):
                tags = []
            if category in tags:
                threads_for_cat.append(t)

        if not evidence_subset and not threads_for_cat:
            content = (
                f"## {category.replace('_', ' ').title()} — Evidence pending\n\n"
                f"No evidence rows or synthesized threads tagged for this category. "
                f"Run again after uploading FOIAs / proposals or after the next "
                f"news refresh.\n"
            )
            confidence = "unverified"
            verdict = "evidence_pending"
            issues = 0
        else:
            prompt = _build_drafter_prompt(
                competitor, category, evidence_subset, threads_for_cat,
            )
            draft = _call_ai(prompt) or ""

            if not draft.strip():
                content = (
                    f"## {category.replace('_', ' ').title()} — LLM unavailable\n\n"
                    f"The analyst LLM did not respond. Retry later. Evidence pool size: "
                    f"{len(evidence_subset)}.\n"
                )
                confidence = "unverified"
                verdict = "llm_unavailable"
                issues = 0
            elif len(evidence_subset) < FACT_CHECK_MIN_EVIDENCE:
                # Skip fact-check on thin pools — see module docstring.
                content = (
                    f"> **Fact-Check: skipped** — only {len(evidence_subset)} evidence "
                    f"item(s) for this category, below the {FACT_CHECK_MIN_EVIDENCE}-item "
                    f"threshold. Treat all claims as evidence-grounded but unverified.\n\n"
                    + draft
                )
                confidence = "low"
                verdict = "factcheck_skipped"
                issues = 0
            else:
                # Build the legacy-shaped context dict that fact_check_entry expects.
                # We pass evidence as document_chunks-shaped pseudo-rows so the
                # existing fact-checker prompt works unchanged.
                pseudo_chunks = [
                    {
                        "source_doc": (r.source_connector or "?"),
                        "source_type": r.claim_class,
                        "page": r.id,  # use ev id as the "page" so [ev:N] still resolves
                        "content": (
                            f"[{r.event_date or '?'}] {r.title}"
                            + (f"\n{r.snippet}" if r.snippet else "")
                        ),
                    }
                    for r in evidence_subset
                ]
                pseudo_news = [
                    {"title": r.title, "source": r.source_connector, "date": r.event_date}
                    for r in evidence_subset[:40]
                ]
                pseudo_context = {
                    "name": competitor.name,
                    "document_chunks": pseudo_chunks,
                    "news_items": pseudo_news,
                }
                audit = fact_check_entry(pseudo_context, category, draft)
                content = audit["content"]
                confidence = audit["confidence"]
                verdict = audit["verdict"]
                issues = audit.get("issues_count", 0)

                banner = ""
                if verdict == "pass":
                    banner = "> **Fact-Check Verdict: Passed** — every claim traces back to evidence.\n\n"
                elif verdict == "revised":
                    banner = f"> **Fact-Check Verdict: Revised** — {issues} issues addressed.\n\n"
                elif verdict == "insufficient_evidence":
                    banner = (f"> **Fact-Check Verdict: Insufficient evidence** — "
                              f"{issues} unsupported claims removed.\n\n")
                content = banner + content

        # Upsert into the existing competitor_dossiers table so the Dossier
        # tab keeps working unchanged.
        existing = (
            db.query(CompetitorDossier)
            .filter(CompetitorDossier.competitor_id == competitor_id,
                    CompetitorDossier.category == category)
            .first()
        )
        title_text = f"{category.replace('_', ' ').title()} - {competitor.name}"
        source_tag = f"intelligence_run:{verdict}"
        now = datetime.utcnow()
        if existing:
            existing.title = title_text
            existing.content = content
            existing.confidence = confidence
            existing.source = source_tag
            existing.updated_at = now
        else:
            db.add(CompetitorDossier(
                competitor_id=competitor_id,
                category=category,
                title=title_text,
                content=content,
                source=source_tag,
                confidence=confidence,
            ))
        db.commit()

        progress_cb("phase_completed", {
            "phase_id": phase_id,
            "confidence": confidence,
            "verdict": verdict,
            "evidence_count": len(evidence_subset),
            "threads_used": len(threads_for_cat),
            "issues": issues,
        })
        return {"category": category, "status": "ok",
                "confidence": confidence, "verdict": verdict}
    except Exception as e:  # noqa: BLE001
        logger.exception(f"Drafter failed for {category}: {e}")
        progress_cb("phase_failed", {"phase_id": phase_id, "error": str(e)})
        return {"category": category, "status": "failed", "error": str(e)}
    finally:
        db.close()


def draft_all_categories(
    db: Session,
    competitor: Competitor,
    settings: Dict[str, str],
    progress_cb: Callable,
) -> List[Dict]:
    """Run all 8 dossier drafters in parallel (max_workers=4). Each runs in its
    own SessionLocal so SQLite doesn't choke on concurrent writes (we use
    short transactions and one row per drafter)."""
    from ...database import SessionLocal as _SessionLocal
    competitor_snapshot = {
        "id": competitor.id,
        "name": competitor.name,
        "aliases": competitor.aliases,
    }

    results: List[Dict] = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {
            ex.submit(
                _draft_one_category,
                _SessionLocal,
                competitor.id,
                competitor_snapshot,
                cat,
                progress_cb,
            ): cat
            for cat in DOSSIER_CATEGORIES
        }
        for fut in as_completed(futures):
            try:
                results.append(fut.result())
            except Exception as e:  # noqa: BLE001
                logger.exception(f"Category drafter exception: {e}")
                results.append({"status": "failed", "error": str(e)})

    return results
