"""Strategic question curator.

After the diff-question pipeline produces ~thousands of candidate questions,
this service answers a single question for each one:

    *Should we ACTUALLY send this to the procurement agency?*

This is distinct from:
  * **quality_guard** in `diff_question_analyst` — asks "is the question
    coherent / specific / non-vague" (drafting-quality filter).
  * **reconcile** — asks "is this redundant with our existing question bank"
    (deduplication filter).
  * **The curator** — asks the harder, bid-strategic questions:

      - Is the answer ALREADY IN the final 2026 RFP text? (most common false
        positive, especially for legacy questions written against an older
        version of the document)
      - Would asking this REVEAL OUR COMPETITIVE STRATEGY?  (forms of "do
        you know we're partnering with X", or "we plan to use technology
        Y, will the agency accept it")
      - Is this a TRICK REMOVAL question?  (the prior solicitation said X
        but X is gone — most of the time the agency removed it on purpose
        and will reply "see updated RFP")
      - Is the wording TOO VAGUE to elicit a useful answer?
      - Is it REDUNDANT with another higher-scored question on the same
        topic?
      - Is the answer NOT ACTIONABLE for our bid?  (curiosity questions
        whose answer wouldn't change pricing, scope, staffing, or risk)

Output for each question:
  ``curator_recommended``  — bool
  ``curator_score``        — 0-100  (higher = better submission candidate)
  ``curator_reason``       — one of the enum strings above
  ``curator_improved_text`` — optional sharper rewording

The curator does NOT auto-reject. It writes its verdict to the four columns
on `rfp_questions` and updates ``status`` only to ``reviewed`` (so the
human reviewer still owns the final accept/reject decision).
"""
from __future__ import annotations

import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from ..models import DocumentChunk, IngestedDocument, RfpQuestion
from .embedding_service import cosine_similarity, embed_text
from .orchestrator_agent import _call_ai

logger = logging.getLogger(__name__)


ALLOWED_REASONS = {
    "recommended",
    "answer_in_rfp",
    "reveals_strategy",
    "trick_removal",
    "vague",
    "redundant",
    "not_actionable",
}


def _curator_workers() -> int:
    """Parallel worker count. LLM-bound so threads are appropriate."""
    try:
        return max(1, min(32, int(os.environ.get("QCURATOR_PARALLEL", "8"))))
    except ValueError:
        return 8


# ─────────────────────────────────────────────────────────────────────
# Prompt
# ─────────────────────────────────────────────────────────────────────

_CURATOR_SYSTEM = (
    "You are a senior Parsons capture manager performing the FINAL strategic "
    "review of a candidate clarification question before it is submitted to "
    "the agency. Parsons submits roughly 50–300 questions in a typical "
    "Q&A round. Sending unnecessary, already-answered, or strategically "
    "harmful questions damages our standing with the evaluator AND "
    "wastes our limited question budget.\n\n"
    "Your job is to decide ONE thing per candidate: should this question "
    "go in the final submission YES or NO, with a calibrated 0-100 score "
    "and a precise reason from the controlled list.\n\n"
    "REJECTION REASONS (use the most-applicable ONE):\n"
    "  * answer_in_rfp     — A careful read of the RFP excerpts already\n"
    "                        answers the question. The agency will reply\n"
    "                        'see Section X.Y' and we will look careless.\n"
    "  * reveals_strategy  — The question telegraphs our solution approach,\n"
    "                        partner choice, technology pick, pricing tactic,\n"
    "                        or capture posture. NEVER send these.\n"
    "  * trick_removal     — Asks about language that the prior solicitation\n"
    "                        had but the new one doesn't. Unless removal\n"
    "                        creates a SPECIFIC operational gap, the agency\n"
    "                        intentionally cut it; asking is wasteful.\n"
    "  * vague             — Phrasing is so soft ('please clarify',\n"
    "                        'what are the requirements for') that any answer\n"
    "                        the agency gives won't change our bid.\n"
    "  * redundant         — The same substantive question is implied by\n"
    "                        another that we will obviously also ask.\n"
    "  * not_actionable    — Even a perfect agency answer wouldn't change\n"
    "                        pricing, scope, schedule, staffing, risk, or\n"
    "                        compliance posture. Curiosity, not capture.\n"
    "  * recommended       — Strong include. The question is specific,\n"
    "                        citable, can only be answered by the agency,\n"
    "                        and the answer materially affects our bid.\n\n"
    "SCORING (0-100):\n"
    "  90-100  Must-ask. Bid-changing answer. Clear citation, sharp wording.\n"
    "  70-89   Should-ask. Useful answer, well-formed.\n"
    "  50-69   Borderline. Worth asking only if budget allows.\n"
    "  30-49   Probably skip. Marginal value or partly answered.\n"
    "   0-29   Reject. Falls into one of the rejection reasons.\n\n"
    "If you can sharpen the wording without changing intent, set\n"
    "improved_question_text. Otherwise leave it null.\n\n"
    "Output VALID JSON only — no preamble, no fences:\n"
    '{ "recommended": true|false, "score": 0-100,\n'
    '  "reason": "<one of: recommended|answer_in_rfp|reveals_strategy|'
    "trick_removal|vague|redundant|not_actionable>\",\n"
    '  "rationale": "<one sentence explaining the verdict>",\n'
    '  "improved_question_text": "<optional sharper rewording or null>" }'
)


def _parse_json(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.+?\})\s*```", text, re.DOTALL)
    cand = (fenced.group(1) if fenced else text).strip()
    if not cand.startswith("{"):
        m = re.search(r"\{.+\}", cand, re.DOTALL)
        if m:
            cand = m.group(0)
    try:
        v = json.loads(cand)
        return v if isinstance(v, dict) else None
    except json.JSONDecodeError:
        return None


def _validate_verdict(env: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Validate and normalize the LLM's verdict. Return None to discard."""
    if not env:
        return None
    try:
        score = int(env.get("score", -1))
    except (TypeError, ValueError):
        return None
    if not 0 <= score <= 100:
        return None
    reason = str(env.get("reason", "")).lower().strip()
    if reason not in ALLOWED_REASONS:
        return None
    recommended = bool(env.get("recommended"))
    # Cross-check: score >= 70 should have reason='recommended'; lower
    # scores should have a rejection reason. We DON'T silently coerce —
    # if the LLM contradicts itself, we keep its stated reason but force
    # `recommended` to match the score band so downstream filters are
    # consistent.
    inferred_recommended = score >= 70 and reason == "recommended"
    if recommended != inferred_recommended:
        logger.debug(
            f"curator self-inconsistency (score={score} reason={reason} "
            f"recommended={recommended}); using score-derived value"
        )
        recommended = inferred_recommended

    rationale = str(env.get("rationale") or "").strip()[:600]
    improved = env.get("improved_question_text")
    if improved is not None:
        improved = str(improved).strip()
        if not improved or improved.lower() in ("null", "none", "n/a"):
            improved = None
        else:
            improved = improved[:1200]
    return {
        "recommended": recommended,
        "score": score,
        "reason": reason,
        "rationale": rationale,
        "improved_question_text": improved,
    }


# ─────────────────────────────────────────────────────────────────────
# RFP context retrieval — per-question, top-K chunks
# ─────────────────────────────────────────────────────────────────────

def _load_rfp_chunks(db: Session, proposal_id: int) -> List[Dict[str, Any]]:
    """Pull the proposal's document chunks into a pure-dict pool. Workers
    use this snapshot — no ORM access in worker threads."""
    doc_ids = [
        d.id for d in db.query(IngestedDocument)
        .filter(IngestedDocument.proposal_id == proposal_id)
        .filter(IngestedDocument.superseded_by_document_id.is_(None))
        .all()
    ]
    if not doc_ids:
        return []
    chunks = (
        db.query(DocumentChunk)
        .filter(DocumentChunk.document_id.in_(doc_ids))
        .filter(DocumentChunk.embedding.isnot(None))
        .all()
    )
    pool: List[Dict[str, Any]] = []
    for c in chunks:
        try:
            vec = json.loads(c.embedding) if c.embedding else None
        except (json.JSONDecodeError, TypeError):
            vec = None
        if not vec:
            continue
        pool.append({
            "document_id": c.document_id,
            "page": c.page_number,
            "content": (c.content or "")[:1500],
            "embedding": vec,
        })
    logger.info(f"curator: loaded {len(pool)} embedded chunks for proposal {proposal_id}")
    return pool


def _top_k_rfp_excerpts(
    pool: List[Dict[str, Any]],
    query: str,
    *,
    k: int = 5,
    min_sim: float = 0.20,
) -> List[Dict[str, Any]]:
    """Return the top-K chunks most similar to ``query`` (a question + its
    source quote). Works on the pure-dict pool so it's thread-safe."""
    if not query or not pool:
        return []
    try:
        q_vec = embed_text(query)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"curator embed failed: {e}")
        return []
    scored = []
    for item in pool:
        try:
            sim = cosine_similarity(q_vec, item["embedding"])
        except Exception:
            continue
        if sim >= min_sim:
            scored.append((sim, item))
    scored.sort(key=lambda x: x[0], reverse=True)
    out = []
    for sim, item in scored[:k]:
        out.append({
            "document_id": item["document_id"],
            "page": item["page"],
            "similarity": round(float(sim), 4),
            "snippet": item["content"][:900],
        })
    return out


def _question_snapshot(q: RfpQuestion) -> Dict[str, Any]:
    """Pure-dict snapshot of an RfpQuestion. Workers operate on these
    only — never on ORM-attached objects."""
    return {
        "id": q.id,
        "proposal_id": q.proposal_id,
        "question_text": q.question_text or "",
        "rationale": q.rationale or "",
        "source_section": q.source_section or "",
        "source_page": q.source_page,
        "source_quote": q.source_quote or "",
        "category": q.category or "",
        "priority": q.priority or "medium",
        "source_kind": q.source_kind,
        "inference_flag": bool(q.inference_flag),
        "reconciliation_action": q.reconciliation_action,
        "status": q.status,
    }


def _build_user_prompt(qsnap: Dict[str, Any], excerpts: List[Dict[str, Any]]) -> str:
    excerpt_block = "\n\n".join(
        f"-- RFP Excerpt {i+1} (doc={e['document_id']} p.{e['page']} sim={e['similarity']:.2f}) --\n"
        f"{e['snippet']}"
        for i, e in enumerate(excerpts)
    ) or "(no RFP excerpts retrieved — likely a removal-only or inference question)"

    return (
        f"CANDIDATE QUESTION (id={qsnap['id']})\n"
        f"  text: {qsnap['question_text']}\n"
        f"  source_section: {qsnap['source_section']} "
        f"(page {qsnap['source_page'] if qsnap['source_page'] is not None else '?'})\n"
        f"  source_kind: {qsnap['source_kind']} "
        f"(inference={qsnap['inference_flag']}, "
        f"reconciliation={qsnap['reconciliation_action']})\n"
        f"  category: {qsnap['category']} | priority: {qsnap['priority']}\n"
        f"  rationale (analyst-internal): {qsnap['rationale'][:600]}\n"
        f"  source_quote: {qsnap['source_quote'][:600]}\n\n"
        f"RFP TEXT THAT MOST RESEMBLES THIS QUESTION (current 2026 solicitation)\n"
        f"{excerpt_block}\n\n"
        f"Decide. Output JSON only."
    )


# ─────────────────────────────────────────────────────────────────────
# Public entry point — curate one batch
# ─────────────────────────────────────────────────────────────────────

def curate_proposal_questions(
    db: Session,
    proposal_id: int,
    *,
    only_statuses: Optional[List[str]] = None,
    limit: Optional[int] = None,
    progress_cb: Optional[Any] = None,
) -> Dict[str, Any]:
    """Run the curator over every eligible question on a proposal.

    Args:
        db: SQLAlchemy session (main thread only).
        proposal_id: scope.
        only_statuses: list of statuses to include (default: ["draft", "reviewed"]).
        limit: optional cap (handy for testing).
        progress_cb: optional callable(processed, total, recommended_count)
                     invoked from the main thread every commit boundary.

    Returns a dict with summary counts.
    """
    statuses = only_statuses or ["draft", "reviewed"]

    rows: List[RfpQuestion] = (
        db.query(RfpQuestion)
        .filter(RfpQuestion.proposal_id == proposal_id)
        .filter(RfpQuestion.status.in_(statuses))
        .order_by(RfpQuestion.id)
        .all()
    )
    if limit:
        rows = rows[:limit]

    total = len(rows)
    if total == 0:
        return {"processed": 0, "recommended": 0, "rejected": 0, "errors": 0}

    pool = _load_rfp_chunks(db, proposal_id)

    # Build snapshots and pre-compute top-K excerpts in MAIN thread (because
    # the chunk pool can be hundreds of MB and we don't want every worker
    # holding a copy). Excerpt selection IS embedding-based and slow, so
    # it's worth doing once per question rather than re-scoring all chunks
    # per worker call.
    snaps: List[Dict[str, Any]] = []
    for q in rows:
        qs = _question_snapshot(q)
        # Compose a query that mixes question + source_quote — the source
        # quote is often the cleanest match against the RFP text.
        query = f"{qs['question_text']}\n{qs['source_quote']}".strip()
        excerpts = _top_k_rfp_excerpts(pool, query, k=5)
        snaps.append({"qsnap": qs, "excerpts": excerpts})

    workers = _curator_workers()
    logger.info(f"curator starting: {total} questions, {workers} workers")

    def _worker(item: Dict[str, Any]) -> Dict[str, Any]:
        qsnap = item["qsnap"]
        excerpts = item["excerpts"]
        try:
            prompt = _build_user_prompt(qsnap, excerpts)
            raw = _call_ai(prompt, _CURATOR_SYSTEM, max_tokens=400)
            env = _parse_json(raw)
            verdict = _validate_verdict(env or {})
            if not verdict:
                return {"id": qsnap["id"], "ok": False, "error": "invalid verdict"}
            return {"id": qsnap["id"], "ok": True, "verdict": verdict}
        except Exception as e:  # noqa: BLE001
            return {"id": qsnap["id"], "ok": False, "error": str(e)[:300]}

    processed = 0
    recommended = 0
    rejected = 0
    errors = 0
    commit_every = 25
    pending_updates: List[Dict[str, Any]] = []

    def _flush(updates: List[Dict[str, Any]]) -> None:
        if not updates:
            return
        for u in updates:
            db.query(RfpQuestion).filter(RfpQuestion.id == u["id"]).update({
                RfpQuestion.curator_score: u["score"],
                RfpQuestion.curator_recommended: u["recommended"],
                RfpQuestion.curator_reason: u["reason"],
                RfpQuestion.curator_improved_text: u["improved"],
                RfpQuestion.curator_run_at: datetime.utcnow(),
                # Move from draft → reviewed if currently draft (preserves
                # the human-set reviewed/approved/rejected). Don't auto-reject.
                RfpQuestion.status: u["status_new"],
                RfpQuestion.updated_at: datetime.utcnow(),
            }, synchronize_session=False)
        db.commit()

    if workers <= 1 or total <= 1:
        results_iter = (_worker(s) for s in snaps)
    else:
        ex = ThreadPoolExecutor(max_workers=workers)
        futures = [ex.submit(_worker, s) for s in snaps]
        results_iter = (fut.result() for fut in as_completed(futures))

    # Map from id → original status so we can decide status_new without
    # hitting the DB per-row.
    status_by_id = {q.id: (q.status or "draft") for q in rows}

    for res in results_iter:
        processed += 1
        if not res["ok"]:
            errors += 1
            if processed % 50 == 0:
                logger.warning(f"curator: {errors} errors so far ({res.get('error')})")
            continue
        v = res["verdict"]
        cur_status = status_by_id.get(res["id"], "draft")
        # Only nudge draft → reviewed; never overwrite approved/rejected/etc.
        status_new = "reviewed" if cur_status == "draft" else cur_status
        pending_updates.append({
            "id": res["id"],
            "score": v["score"],
            "recommended": v["recommended"],
            "reason": v["reason"],
            "improved": v["improved_question_text"],
            "status_new": status_new,
        })
        if v["recommended"]:
            recommended += 1
        else:
            rejected += 1

        if len(pending_updates) >= commit_every:
            _flush(pending_updates)
            pending_updates = []
            if progress_cb:
                try:
                    progress_cb(processed, total, recommended)
                except Exception:
                    pass

    _flush(pending_updates)
    if progress_cb:
        try:
            progress_cb(processed, total, recommended)
        except Exception:
            pass

    logger.info(
        f"curator complete: processed={processed} recommended={recommended} "
        f"rejected={rejected} errors={errors}"
    )
    return {
        "processed": processed,
        "recommended": recommended,
        "rejected": rejected,
        "errors": errors,
        "total": total,
    }
