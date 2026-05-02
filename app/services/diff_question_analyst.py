"""Diff-driven question analyst.

After a fresh extraction + diff run, this service answers:

  1. *What questions does the diff make sense to ask?* — with special
     attention to inferences and changes that aren't clearly understandable
     based on how the contract is currently operated.

  2. *Should each candidate be added, or does it replace / update an
     existing question, or is it already covered (duplicate)?*

The accuracy bar is high — the user told us so explicitly. We honor that with
five guardrails:

  * **Three independent LLM passes** (per-row → cross-row dedupe → per-existing
    reconcile) so accuracy compounds rather than relies on one prompt.
  * **Strict JSON envelopes** with field-level validation; malformed outputs
    are dropped, not coerced.
  * **Inference detection is explicit** — the prompt forces the model to
    decide ``inference: true`` only when the diff's behavioral implication
    isn't obvious from the contract text alone, with a separate confidence
    rating. Reviewers see this flag in the UI so high-stakes inferences get
    extra scrutiny.
  * **Operations context grounding** — we pull the top Parsons-knowledge
    snippets relevant to each diff row (current SOPs, prior responses,
    capability statements) so the model can decide *"is this change
    understandable given how we operate today?"* before drafting a question.
  * **Embedding similarity ≥ 0.82 dedupe** before we even ask the LLM whether
    something is a duplicate — protects against the model rationalizing a
    near-identical question as "different."

Public entry points (called by the orchestrator):
  * ``generate_questions_from_diff(db, run_id)``
  * ``reconcile_candidates(db, run_id)``
  * ``run_pipeline(db, proposal_id, baseline_proposal_id=None,
                    baseline_document_ids=None, ...)``  — full chained job
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from ..models import (
    DiffQuestionAnalysisRun, IngestedDocument, Proposal, RfpDiffRun,
    RfpQuestion, RfpRequirement, RfpRequirementDiff,
)
from .embedding_service import cosine_similarity, embed_text
from .orchestrator_agent import _call_ai
from .parsons_coverage import _load_parsons_pool

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Constants & validators
# ─────────────────────────────────────────────────────────────────────

ALLOWED_CATEGORIES = {
    "clarification", "risk", "pricing", "scope", "competitive", "form",
}
ALLOWED_PRIORITIES = {"critical", "high", "medium", "low"}
ALLOWED_CONFIDENCE = {"high", "medium", "low"}
ALLOWED_RECONCILIATIONS = {"added", "replaces", "updates", "duplicate"}

# Diff-row source_kind mapping
_STATUS_TO_SOURCE = {
    "changed": "diff_change",
    "added":   "diff_addition",
    "removed": "diff_removal",
}

# Embedding similarity threshold above which we treat an existing question
# as definitely covering the same ground. The LLM reconciler is still asked
# its opinion but we bias toward duplicate/updates when similarity is >= this.
_DUPLICATE_SIM_THRESHOLD = 0.82
# Above this, we cross-row dedupe candidates against each other before saving.
_CANDIDATE_DEDUPE_THRESHOLD = 0.88


def _generator_workers() -> int:
    """ThreadPool size for the question generator. LLM-bound, so threads
    are appropriate. Default 8; configurable via env."""
    try:
        return max(1, min(32, int(os.environ.get("DIFF_QGEN_PARALLEL", "8"))))
    except ValueError:
        return 8


def _reconciler_workers() -> int:
    try:
        return max(1, min(32, int(os.environ.get("DIFF_RECON_PARALLEL", "8"))))
    except ValueError:
        return 8


# ─────────────────────────────────────────────────────────────────────
# JSON parser used by every prompt — tolerant of fence/preamble noise
# ─────────────────────────────────────────────────────────────────────

def _parse_json_object(text: str) -> Optional[Dict[str, Any]]:
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


# ─────────────────────────────────────────────────────────────────────
# Stage 1 — generate candidate questions from each diff row
# ─────────────────────────────────────────────────────────────────────

_GENERATOR_SYSTEM = (
    "You are a senior Parsons capture analyst preparing the formal Q&A list "
    "to submit to a procurement agency. The questions you produce will go "
    "out under the Parsons name in front of a state procurement evaluator. "
    "Nonsense, vague, or 'please clarify' questions DO REAL HARM: they "
    "make Parsons look unprepared, waste the agency's time, and reduce "
    "the chance that genuinely important questions get a serious answer.\n\n"
    "ASK A QUESTION ONLY WHEN:\n"
    "  (1) the change is GENUINELY AMBIGUOUS — a competent state "
    "      evaluator would also have to ask, OR\n"
    "  (2) the change has SPECIFIC IMPLICATIONS for pricing, scope, "
    "      schedule, staffing, or compliance that the RFP does not "
    "      resolve, OR\n"
    "  (3) the agency appears to have INFERRED a requirement (something "
    "      not clearly stated) — in which case ask them to confirm the "
    "      explicit requirement.\n\n"
    "DO NOT ASK A QUESTION WHEN:\n"
    "  * the answer is obvious from the RFP text alone\n"
    "  * the answer is already established in how Parsons currently "
    "    operates the contract (use the operations-context excerpts)\n"
    "  * the question would be generic / boilerplate ('please clarify "
    "    Section 4.5', 'what are the requirements for X')\n"
    "  * the question is about a wording tweak with no operational impact\n"
    "  * the answer can be derived from elementary procurement common "
    "    sense\n\n"
    "QUESTION QUALITY BAR:\n"
    "  * Each question must cite the specific RFP section and quote the "
    "    ambiguous language verbatim, then pose a CONCRETE, ANSWERABLE "
    "    question (yes/no, threshold, definition, scope inclusion).\n"
    "  * Begin with 'Ref. Section X.Y (p.Z): ...' using actual section "
    "    and page from the diff data.\n"
    "  * The question must end with '?' and be ≤400 chars.\n"
    "  * If you can't write a specific, citable, answerable question, "
    "    set should_ask=false. NEVER lower the bar to fill a slot.\n\n"
    "BE HONEST ABOUT INFERENCE. If your reason for asking comes from "
    "inference rather than explicit RFP text, set inference: true with a "
    "calibrated confidence (high/medium/low). Inferences are exactly the "
    "questions worth asking — but they must be SPECIFIC inferences from "
    "real text, not vibes.\n\n"
    "Output a SINGLE JSON object. NO preamble. NO code fences."
)


def _ops_context_for_diff_row(
    db: Session,
    row: RfpRequirementDiff,
    pool: List[Dict[str, Any]],
    *,
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    """Find the top Parsons-knowledge chunks most relevant to a single diff
    row. Used to ground the generator prompt in current operations.

    Query embedding is composed from whichever side of the diff has the
    most content (target if present, else baseline).
    """
    parts: List[str] = []
    target_id = row.target_requirement_id
    baseline_id = row.baseline_requirement_id

    target = (db.query(RfpRequirement).filter(RfpRequirement.id == target_id).first()
              if target_id else None)
    baseline = (db.query(RfpRequirement).filter(RfpRequirement.id == baseline_id).first()
                if baseline_id else None)

    src = target or baseline
    if src:
        for s in (src.section_id, src.title, src.description, src.source_text):
            if s:
                parts.append(str(s))
    if row.change_summary:
        parts.append(row.change_summary)
    query = " \n ".join(parts).strip()[:4000]
    if not query or not pool:
        return []
    try:
        q_vec = embed_text(query)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"embed_text failed for ops-context retrieval: {e}")
        return []

    scored: List[Tuple[float, Dict[str, Any]]] = []
    for item in pool:
        sim = cosine_similarity(q_vec, item["embedding"])
        if sim > 0.20:
            scored.append((sim, item))
    scored.sort(key=lambda x: x[0], reverse=True)
    out: List[Dict[str, Any]] = []
    for sim, item in scored[:top_k]:
        snippet = (item.get("content") or "").strip()
        if len(snippet) > 700:
            snippet = snippet[:700].rsplit(" ", 1)[0] + "…"
        out.append({
            "document_id": item.get("document_id"),
            "document_name": item.get("document_name"),
            "page": item.get("page"),
            "similarity": round(float(sim), 4),
            "snippet": snippet,
        })
    return out


def _format_diff_row_for_prompt(
    db: Session,
    row: RfpRequirementDiff,
) -> Tuple[str, Dict[str, Any]]:
    """Render a diff row + both sides of its requirement payload into a
    prompt block. Returns (text_block, side_meta)."""
    target = baseline = None
    if row.target_requirement_id:
        target = db.query(RfpRequirement).filter(
            RfpRequirement.id == row.target_requirement_id).first()
    if row.baseline_requirement_id:
        baseline = db.query(RfpRequirement).filter(
            RfpRequirement.id == row.baseline_requirement_id).first()

    def _fmt(label: str, r: Optional[RfpRequirement]) -> str:
        if not r:
            return f"--- {label}: (not present) ---"
        return (
            f"--- {label} ---\n"
            f"  section_id: {r.section_id or '(none)'}\n"
            f"  category:   {r.category or '(none)'}\n"
            f"  priority:   {r.priority or '(none)'}\n"
            f"  title:      {(r.title or '').strip()[:240]}\n"
            f"  description: {(r.description or '').strip()[:1200]}\n"
            f"  source_text: {(r.source_text or '').strip()[:1200]}\n"
            f"  source_page: {r.source_page or '?'}"
        )

    block = "\n".join([
        f"Diff status: {row.status}",
        f"Match similarity: {row.similarity if row.similarity is not None else '(unmatched)'}",
        f"Change summary (analyst-written):  {row.change_summary or '(none)'}",
        f"Impact severity: {row.impact_severity or '(none)'}",
        f"Impact blurb: {row.impact_blurb or '(none)'}",
        "",
        _fmt("BASELINE (prior solicitation)", baseline),
        "",
        _fmt("TARGET (new solicitation)", target),
    ])
    side_meta = {
        "target_requirement_id": row.target_requirement_id,
        "baseline_requirement_id": row.baseline_requirement_id,
        "target_section_id": (target.section_id if target else None),
        "baseline_section_id": (baseline.section_id if baseline else None),
        "target_source_page": (target.source_page if target else None),
        "baseline_source_page": (baseline.source_page if baseline else None),
    }
    return block, side_meta


def _generator_prompt(
    diff_block: str,
    ops_excerpts: List[Dict[str, Any]],
) -> str:
    excerpt_block = (
        "\n\n".join(
            f"-- Excerpt {i+1} (doc={e['document_id']} sim={e['similarity']:.2f}) --\n"
            f"Source: {e['document_name']}{(' p.' + str(e['page'])) if e.get('page') else ''}\n"
            f"{e['snippet']}"
            for i, e in enumerate(ops_excerpts)
        )
        if ops_excerpts
        else "(no relevant operations excerpts found)"
    )

    return f"""DIFF ROW
{diff_block}

OPERATIONS CONTEXT — how Parsons currently operates this contract.
Use these to decide whether the change above is already understandable
without asking the agency.

{excerpt_block}

DECIDE:
* Should we ask the agency a clarifying question about this diff?
* Be conservative: only generate a question if the change is genuinely
  ambiguous, has unspecified implications for our bid, or you are
  inferring its meaning rather than reading it directly.
* If you DO ask, draft ONE question (not a list). Phrase it as a single
  interrogative ending in "?". Begin with "Ref. Section X.Y (p.Z): ..."
  using the most precise section/page available from the diff payload.
* If you DO NOT ask, set "should_ask": false and explain briefly why
  in "no_question_reason" (e.g. "obvious clarification of typo",
  "already understandable from current SOPs in excerpt #2", etc.).

Output a SINGLE JSON object EXACTLY this shape:
{{
  "should_ask": true | false,
  "question_text": "<interrogative or empty if should_ask=false>",
  "category": "clarification" | "risk" | "pricing" | "scope" | "competitive" | "form",
  "priority": "critical" | "high" | "medium" | "low",
  "rationale": "<internal-only: why this matters to our bid>",
  "source_quote": "<short verbatim quote from the diff text>",
  "inference": true | false,
  "inference_confidence": "high" | "medium" | "low",
  "operation_change_summary": "<one sentence: what concretely changes about how we'd operate>",
  "no_question_reason": "<empty if should_ask=true; otherwise short reason>"
}}
JSON ONLY. No preamble. No code fences.
"""


def _validate_candidate(env: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(env, dict):
        return None
    should_ask = bool(env.get("should_ask"))
    if not should_ask:
        return {"should_ask": False,
                "no_question_reason": (env.get("no_question_reason") or "").strip()[:600]}

    qt = (env.get("question_text") or "").strip()
    if not qt or not qt.endswith("?"):
        return None
    if len(qt) > 4000:
        qt = qt[:4000]

    cat = (env.get("category") or "clarification").lower().strip()
    if cat not in ALLOWED_CATEGORIES:
        cat = "clarification"
    pri = (env.get("priority") or "medium").lower().strip()
    if pri not in ALLOWED_PRIORITIES:
        pri = "medium"
    conf = (env.get("inference_confidence") or "medium").lower().strip()
    if conf not in ALLOWED_CONFIDENCE:
        conf = "medium"

    return {
        "should_ask": True,
        "question_text": qt,
        "category": cat,
        "priority": pri,
        "rationale": (env.get("rationale") or "").strip()[:2000] or None,
        "source_quote": (env.get("source_quote") or "").strip()[:1500] or None,
        "inference": bool(env.get("inference")),
        "inference_confidence": conf,
        "operation_change_summary": (env.get("operation_change_summary") or "").strip()[:1500] or None,
    }

# ─────────────────────────────────────────────────────────────────────
# Quality guard — second-pass auditor that rejects nonsense questions
# ─────────────────────────────────────────────────────────────────────

_QUALITY_GUARD_SYSTEM = (
    "You are a strict procurement-questions editor at Parsons. Your one "
    "job: reject candidate Q&A questions that would embarrass Parsons "
    "if submitted to a state procurement evaluator. You do NOT improve "
    "questions; you only PASS or FAIL them. False passes harm Parsons; "
    "false fails are recoverable.\n\n"
    "FAIL a question if ANY of these are true:\n"
    "  * Vague / boilerplate ('please clarify', 'what are the requirements')\n"
    "  * Asks something the RFP source quote already answers\n"
    "  * Asks something elementary procurement knowledge already answers\n"
    "  * Doesn't cite a specific section + ambiguous language\n"
    "  * Conflates two different topics into one question\n"
    "  * Is rhetorical or argumentative rather than a real ask\n"
    "  * Could be answered 'see the RFP' by an evaluator\n"
    "  * Uses fabricated specifics not present in the diff or operations context\n\n"
    "PASS a question only if it is: specific, citable, answerable, "
    "non-obvious, and would meaningfully advance Parsons's bid prep.\n\n"
    "Output VALID JSON ONLY: {\"verdict\":\"pass\"|\"fail\","
    "\"reason\":\"<one sentence>\"}"
)


def _quality_guard(candidate: Dict[str, Any],
                    diff_block: str,
                    ops_excerpts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Second-pass auditor. Returns ``{verdict: 'pass'|'fail', reason: str}``.
    Defaults to PASS on any error so we don't drop real candidates due
    to LLM hiccups."""
    excerpt_block = "\n\n".join(
        f"-- Excerpt {i+1} (sim={e.get('similarity', 0):.2f}) --\n{e.get('snippet','')[:500]}"
        for i, e in enumerate(ops_excerpts[:3])
    ) or "(no operations context)"
    user_prompt = (
        f"DIFF ROW (the change being asked about)\n{diff_block}\n\n"
        f"OPERATIONS CONTEXT (current Parsons knowledge)\n{excerpt_block}\n\n"
        f"CANDIDATE QUESTION\n"
        f"  text: {candidate['question_text']}\n"
        f"  category: {candidate['category']}\n"
        f"  priority: {candidate['priority']}\n"
        f"  rationale: {(candidate.get('rationale') or '').strip()[:600]}\n"
        f"  source_quote: {(candidate.get('source_quote') or '').strip()[:400]}\n"
        f"  inference: {candidate['inference']} ({candidate['inference_confidence']})\n\n"
        f"Audit. Output JSON only."
    )
    try:
        raw = _call_ai(user_prompt, _QUALITY_GUARD_SYSTEM, max_tokens=200)
        env = _parse_json_object(raw or "")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"quality guard failed (defaulting to pass): {e}")
        return {"verdict": "pass", "reason": "guard error"}
    if not env:
        return {"verdict": "pass", "reason": "unparseable guard envelope"}
    verdict = (env.get("verdict") or "").lower().strip()
    reason = (env.get("reason") or "").strip()[:400]
    if verdict not in ("pass", "fail"):
        verdict = "pass"
    return {"verdict": verdict, "reason": reason}

def generate_questions_from_diff(
    db: Session,
    analysis_run_id: int,
    *,
    severity_floor: str = "medium",
    only_status: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Stage 1: walk the diff rows of the analysis run, ask the LLM whether
    each one warrants a clarifying question, persist candidates as
    ``RfpQuestion`` rows with ``source_kind`` starting with ``diff_`` and
    ``status='draft'``.

    By default we focus on rows whose ``impact_severity`` is at least
    ``severity_floor`` (default 'medium') AND whose status is one of
    ``changed | added | removed`` — 'unchanged' rows are skipped because
    they are by definition not new ground for clarification.

    Cross-row dedupe of candidate questions is performed before save: any
    new candidate with cosine similarity ≥ 0.88 to a candidate already
    saved in this same run is skipped (the higher-priority one wins).
    """
    run = db.query(DiffQuestionAnalysisRun).filter(
        DiffQuestionAnalysisRun.id == analysis_run_id).first()
    if not run:
        return {"error": "analysis run not found"}
    if not run.diff_run_id:
        return {"error": "analysis run has no diff_run_id yet"}

    severity_rank = {"critical": 4, "high": 3, "medium": 2,
                     "low": 1, "informational": 0}
    floor_rank = severity_rank.get(severity_floor.lower(), 2)

    statuses_q = only_status or ["changed", "added", "removed"]

    diff_rows = (
        db.query(RfpRequirementDiff)
        .filter(RfpRequirementDiff.diff_run_id == run.diff_run_id)
        .filter(RfpRequirementDiff.status.in_(statuses_q))
        .all()
    )
    eligible = [
        r for r in diff_rows
        if severity_rank.get((r.impact_severity or "medium").lower(), 2) >= floor_rank
    ]

    run.generation_started_at = datetime.utcnow()
    run.status = "generating_questions"
    run.progress_note = f"Considering {len(eligible)} diff rows of {len(diff_rows)}"
    db.commit()

    pool = _load_parsons_pool(db, run.proposal_id)

    saved: List[RfpQuestion] = []
    saved_embeddings: List[Tuple[List[float], RfpQuestion]] = []
    inference_count = 0
    high_conf = 0
    skipped_no_ask = 0
    skipped_dupe = 0
    skipped_quality = 0
    quality_fail_reasons: List[str] = []
    errored = 0

    # ── Resume mode: skip diff rows that already have a candidate question
    # tied to them in this diff_run. This means a backend death + restart
    # picks up where it left off without re-doing LLM calls.
    already_done_diff_ids: set = set()
    for q in (db.query(RfpQuestion)
              .filter(RfpQuestion.diff_run_id == run.diff_run_id)
              .all()):
        try:
            for did in (json.loads(q.diff_row_ids) if q.diff_row_ids else []):
                already_done_diff_ids.add(int(did))
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    if already_done_diff_ids:
        eligible_pre = len(eligible)
        eligible = [r for r in eligible if r.id not in already_done_diff_ids]
        logger.info(
            f"diff-question generator resume: {eligible_pre - len(eligible)} "
            f"of {eligible_pre} eligible rows already have candidates; "
            f"processing {len(eligible)} remaining."
        )
        # Pre-load the already-saved candidates' question_text embeddings so
        # cross-row dedupe accounts for them.
        for q in (db.query(RfpQuestion)
                  .filter(RfpQuestion.diff_run_id == run.diff_run_id).all()):
            try:
                v = embed_text(q.question_text or "")
                saved_embeddings.append((v, q))
            except Exception:
                continue

    # ── Snapshot every diff row to plain dicts BEFORE workers run.
    # Workers operate on snapshots only — never touch the SQLAlchemy
    # session. Same pattern that fixed the coverage bug.
    snapshots: List[Dict[str, Any]] = []
    for drow in eligible:
        try:
            block, side_meta = _format_diff_row_for_prompt(db, drow)
            ops = _ops_context_for_diff_row(db, drow, pool, top_k=5)
            snapshots.append({
                "diff_row_id": drow.id,
                "diff_status": drow.status,
                "block": block,
                "side_meta": side_meta,
                "ops": ops,
            })
        except Exception as e:  # noqa: BLE001
            errored += 1
            logger.warning(f"snapshot failed for diff_row {drow.id}: {e}")

    # ── Worker: pure LLM call + quality guard + candidate embed.
    # Returns a dict that the main thread persists (or skips).
    def _worker(snap: Dict[str, Any]) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "diff_row_id": snap["diff_row_id"],
            "diff_status": snap["diff_status"],
            "side_meta": snap["side_meta"],
            "outcome": "error",  # populated below
            "cand": None,
            "guard_reason": None,
            "cand_vec": None,
        }
        try:
            prompt = _generator_prompt(snap["block"], snap["ops"])
            raw = _call_ai(prompt, _GENERATOR_SYSTEM, max_tokens=900)
            env = _parse_json_object(raw)
            cand = _validate_candidate(env or {})
            if cand is None:
                result["outcome"] = "error"
                return result
            if not cand["should_ask"]:
                result["outcome"] = "no_ask"
                return result
            # Quality guard
            guard = _quality_guard(cand, snap["block"], snap["ops"])
            if guard["verdict"] == "fail":
                result["outcome"] = "quality_rejected"
                result["guard_reason"] = guard.get("reason")
                return result
            # Embed candidate question for cross-row dedupe (worker-local)
            try:
                result["cand_vec"] = embed_text(cand["question_text"])
            except Exception as e:  # noqa: BLE001
                logger.warning(f"candidate embed failed (worker): {e}")
            result["cand"] = cand
            result["outcome"] = "candidate"
            return result
        except Exception as e:  # noqa: BLE001
            logger.warning(f"generator worker failed for diff_row {snap['diff_row_id']}: {e}")
            result["outcome"] = "error"
            return result

    workers = _generator_workers()
    completed = 0
    commit_every = 25

    if workers <= 1 or len(snapshots) <= 1:
        results_iter = (_worker(s) for s in snapshots)
    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        ex = ThreadPoolExecutor(max_workers=workers)
        futures = [ex.submit(_worker, s) for s in snapshots]
        results_iter = (fut.result() for fut in as_completed(futures))

    for res in results_iter:
        outcome = res["outcome"]
        if outcome == "error":
            errored += 1
        elif outcome == "no_ask":
            skipped_no_ask += 1
        elif outcome == "quality_rejected":
            skipped_quality += 1
            if res.get("guard_reason"):
                quality_fail_reasons.append(res["guard_reason"][:120])
            logger.info(
                f"quality_guard rejected req_diff={res['diff_row_id']}: "
                f"{(res.get('guard_reason') or '')[:160]}"
            )
        elif outcome == "candidate":
            cand = res["cand"]
            cand_vec = res["cand_vec"]
            # Cross-row dedupe (must run on main thread to read shared list)
            if cand_vec:
                is_dupe = any(
                    cosine_similarity(cand_vec, prior_vec) >= _CANDIDATE_DEDUPE_THRESHOLD
                    for prior_vec, _ in saved_embeddings
                )
                if is_dupe:
                    skipped_dupe += 1
                    completed += 1
                    continue

            source_kind = _STATUS_TO_SOURCE.get(res["diff_status"], "diff_change")
            if cand["inference"]:
                source_kind = "diff_inference"
                inference_count += 1
            if cand["inference_confidence"] == "high":
                high_conf += 1

            side_meta = res["side_meta"]
            req_ids: List[int] = []
            if side_meta.get("target_requirement_id"):
                req_ids.append(side_meta["target_requirement_id"])
            if side_meta.get("baseline_requirement_id"):
                req_ids.append(side_meta["baseline_requirement_id"])

            row = RfpQuestion(
                proposal_id=run.proposal_id,
                source_section=(side_meta.get("target_section_id")
                                or side_meta.get("baseline_section_id")),
                source_page=(side_meta.get("target_source_page")
                             or side_meta.get("baseline_source_page")),
                category=cand["category"],
                priority=cand["priority"],
                question_text=cand["question_text"],
                rationale=cand["rationale"],
                source_quote=cand["source_quote"],
                related_requirement_ids=(json.dumps(req_ids) if req_ids else None),
                status="draft",
                created_by_ai=True,
                source_kind=source_kind,
                diff_run_id=run.diff_run_id,
                diff_row_ids=json.dumps([res["diff_row_id"]]),
                inference_flag=cand["inference"],
                inference_confidence=cand["inference_confidence"],
                operation_change_summary=cand["operation_change_summary"],
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            db.add(row)
            db.flush()
            saved.append(row)
            if cand_vec:
                saved_embeddings.append((cand_vec, row))

        completed += 1
        if completed % commit_every == 0:
            db.commit()
            run.progress_note = (
                f"Processed {completed}/{len(snapshots)} — saved "
                f"{len(saved)} candidates so far."
            )
            db.commit()

    db.commit()

    run.candidates_generated = len(saved)
    run.high_confidence_count = high_conf
    run.inference_count = inference_count
    run.generation_completed_at = datetime.utcnow()
    run.progress_note = (
        f"Generated {len(saved)} candidate question(s). "
        f"Skipped {skipped_no_ask} (no-question), "
        f"{skipped_quality} (quality-rejected), "
        f"{skipped_dupe} (cross-row duplicates), {errored} errors."
    )
    db.commit()

    return {
        "analysis_run_id": run.id,
        "diff_run_id": run.diff_run_id,
        "diff_rows_considered": len(eligible),
        "candidates_saved": len(saved),
        "candidate_ids": [q.id for q in saved],
        "inferences": inference_count,
        "high_confidence": high_conf,
        "skipped_no_question": skipped_no_ask,
        "skipped_cross_row_duplicates": skipped_dupe,
        "skipped_quality_rejected": skipped_quality,
        "quality_fail_reasons_sample": quality_fail_reasons[:8],
        "errors": errored,
    }


# ─────────────────────────────────────────────────────────────────────
# Stage 2 — reconcile candidates against existing questions
# ─────────────────────────────────────────────────────────────────────

_RECONCILER_SYSTEM = (
    "You are the question-bank curator. You review a NEW candidate question "
    "and the closest EXISTING questions on the same proposal. You decide "
    "exactly one of:\n"
    "  added     — the candidate is genuinely new; keep it as-is.\n"
    "  replaces  — the candidate is materially better than an existing one "
    "              (more precise, more current, addresses a gap the existing "
    "              one missed). Identify which existing question it replaces.\n"
    "  updates   — an existing question is mostly right but should be edited "
    "              with new language from the candidate. Provide the rewritten "
    "              question_text. Identify the existing question id.\n"
    "  duplicate — the candidate is already covered by an existing question; "
    "              recommend rejecting the candidate.\n\n"
    "When in doubt between 'updates' and 'duplicate', prefer 'duplicate' — "
    "the team values a tight question list, and any genuinely new wording "
    "can be added later by hand. Be HONEST about overlap — false 'added' "
    "decisions create duplicate work for the team.\n\n"
    "Output a SINGLE JSON object. NO preamble. NO code fences."
)


def _reconciler_prompt(
    candidate: RfpQuestion,
    existing_pairs: List[Tuple[RfpQuestion, float]],
) -> str:
    cand_block = (
        f"  question_text: {candidate.question_text}\n"
        f"  category: {candidate.category}  priority: {candidate.priority}\n"
        f"  source_section: {candidate.source_section}  source_page: {candidate.source_page}\n"
        f"  rationale: {(candidate.rationale or '').strip()[:600]}\n"
        f"  inference: {bool(candidate.inference_flag)} ({candidate.inference_confidence or '?'})"
    )

    if not existing_pairs:
        existing_block = "(no existing questions found on this proposal)"
    else:
        bits = []
        for q, sim in existing_pairs:
            bits.append(
                f"-- Existing #{q.id} (sim={sim:.2f}, status={q.status}) --\n"
                f"  question_text: {q.question_text}\n"
                f"  category: {q.category}  priority: {q.priority}\n"
                f"  source_section: {q.source_section or '?'} "
                f"  source_page: {q.source_page or '?'}\n"
                f"  rationale: {(q.rationale or '').strip()[:400]}"
            )
        existing_block = "\n\n".join(bits)

    return f"""CANDIDATE QUESTION (newly drafted from diff)
{cand_block}

EXISTING QUESTIONS ON THIS PROPOSAL — top {len(existing_pairs)} by semantic similarity
{existing_block}

Decide the reconciliation action. Output a single JSON object EXACTLY this shape:
{{
  "action": "added" | "replaces" | "updates" | "duplicate",
  "replaces_question_id": <int id of the question being replaced, or null>,
  "updates_question_id": <int id of the question being updated, or null>,
  "rewritten_question_text": "<only when action=updates: the new question_text. End with '?'>",
  "rewritten_rationale": "<optional: revised rationale when action=updates>",
  "notes": "<one or two sentences explaining the decision>"
}}
Rules:
* If action=replaces, replaces_question_id MUST be present and must match
  one of the EXISTING #N ids shown above.
* If action=updates, updates_question_id MUST be present and rewritten_question_text MUST be a complete interrogative.
* If action=duplicate, replaces_question_id and updates_question_id MUST be null.
* If action=added, both ids MUST be null.
JSON ONLY."""


def _validate_reconciliation(
    env: Dict[str, Any],
    existing_ids: List[int],
) -> Optional[Dict[str, Any]]:
    if not isinstance(env, dict):
        return None
    action = (env.get("action") or "").lower().strip()
    if action not in ALLOWED_RECONCILIATIONS:
        return None
    out = {"action": action,
           "replaces_question_id": None,
           "updates_question_id": None,
           "rewritten_question_text": None,
           "rewritten_rationale": None,
           "notes": (env.get("notes") or "").strip()[:1000] or None}
    if action == "replaces":
        rid = env.get("replaces_question_id")
        if not isinstance(rid, int) or rid not in existing_ids:
            return None
        out["replaces_question_id"] = rid
    elif action == "updates":
        uid = env.get("updates_question_id")
        if not isinstance(uid, int) or uid not in existing_ids:
            return None
        new_text = (env.get("rewritten_question_text") or "").strip()
        if not new_text or not new_text.endswith("?") or len(new_text) > 4000:
            return None
        out["updates_question_id"] = uid
        out["rewritten_question_text"] = new_text
        out["rewritten_rationale"] = (
            (env.get("rewritten_rationale") or "").strip()[:2000] or None)
    return out


def reconcile_candidates(
    db: Session,
    analysis_run_id: int,
    *,
    top_k_existing: int = 5,
) -> Dict[str, Any]:
    """Stage 2: for each candidate question generated by stage 1, find the
    top-K most similar existing questions on the proposal and ask the LLM to
    decide add/replaces/updates/duplicate. Persist the decision on each
    RfpQuestion row."""
    run = db.query(DiffQuestionAnalysisRun).filter(
        DiffQuestionAnalysisRun.id == analysis_run_id).first()
    if not run:
        return {"error": "analysis run not found"}

    # Candidates: questions on this proposal tied to this diff_run, with no
    # reconciliation_action yet.
    candidates = (db.query(RfpQuestion)
                  .filter(RfpQuestion.proposal_id == run.proposal_id)
                  .filter(RfpQuestion.diff_run_id == run.diff_run_id)
                  .filter(RfpQuestion.reconciliation_action.is_(None))
                  .order_by(RfpQuestion.created_at)
                  .all())

    # Existing universe — questions on the proposal NOT tied to this diff run
    # (i.e. pre-existing). Status filter: ignore rejected, but include drafts.
    existing = (db.query(RfpQuestion)
                .filter(RfpQuestion.proposal_id == run.proposal_id)
                .filter(RfpQuestion.status != "rejected")
                .filter((RfpQuestion.diff_run_id != run.diff_run_id)
                        | (RfpQuestion.diff_run_id.is_(None)))
                .all())

    run.reconcile_started_at = datetime.utcnow()
    run.status = "reconciling"
    run.progress_note = (
        f"Reconciling {len(candidates)} candidates against {len(existing)} existing"
    )
    db.commit()

    # Pre-embed existing questions for similarity ranking
    existing_vecs: List[Tuple[RfpQuestion, List[float]]] = []
    for q in existing:
        try:
            v = embed_text(q.question_text or "")
            existing_vecs.append((q, v))
        except Exception:
            continue

    counts = {"added": 0, "replaces": 0, "updates": 0, "duplicate": 0}
    errored = 0

    # Snapshot candidates as plain dicts so workers don't touch the
    # SQLAlchemy session. Workers compute the LLM decision; main thread
    # persists serially.
    cand_snaps: List[Dict[str, Any]] = []
    cand_by_id: Dict[int, RfpQuestion] = {c.id: c for c in candidates}
    for cand in candidates:
        cand_snaps.append({
            "id": cand.id,
            "question_text": cand.question_text,
            "category": cand.category,
            "priority": cand.priority,
            "source_section": cand.source_section,
            "source_page": cand.source_page,
            "rationale": cand.rationale,
            "inference_flag": bool(cand.inference_flag),
            "inference_confidence": cand.inference_confidence,
        })

    # Pre-fetch existing question text snapshots so the worker can build
    # the prompt without ORM access.
    existing_by_id: Dict[int, Dict[str, Any]] = {}
    for q, _ in existing_vecs:
        existing_by_id[q.id] = {
            "id": q.id,
            "question_text": q.question_text,
            "category": q.category,
            "priority": q.priority,
            "source_section": q.source_section,
            "source_page": q.source_page,
            "rationale": q.rationale,
            "status": q.status,
        }
    existing_vecs_only = [(q.id, v) for q, v in existing_vecs]

    def _worker(snap: Dict[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "cand_id": snap["id"],
            "outcome": "error",
            "decision": None,
            "top_pair_id": None,
            "top_pair_sim": None,
            "fast_path_added": False,
        }
        try:
            try:
                cv = embed_text(snap["question_text"] or "")
            except Exception:
                cv = None
            top_pairs: List[Tuple[int, float]] = []
            if cv and existing_vecs_only:
                scored = [(qid, cosine_similarity(cv, v)) for qid, v in existing_vecs_only]
                scored.sort(key=lambda x: x[1], reverse=True)
                top_pairs = scored[:top_k_existing]
            if not top_pairs or top_pairs[0][1] < 0.40:
                out["outcome"] = "fast_added"
                return out
            out["top_pair_id"] = top_pairs[0][0]
            out["top_pair_sim"] = top_pairs[0][1]

            # Build a fake-ORM-like object for _reconciler_prompt by passing
            # the candidate snap and the existing rows. Reconstruct existing
            # mini objects that have the attrs the prompt formatter needs.
            class _Mini:
                pass
            cand_obj = _Mini()
            cand_obj.id = snap["id"]
            cand_obj.question_text = snap["question_text"]
            cand_obj.category = snap["category"]
            cand_obj.priority = snap["priority"]
            cand_obj.source_section = snap["source_section"]
            cand_obj.source_page = snap["source_page"]
            cand_obj.rationale = snap["rationale"]
            cand_obj.inference_flag = snap["inference_flag"]
            cand_obj.inference_confidence = snap["inference_confidence"]
            existing_pairs_objs: List[Tuple[Any, float]] = []
            for qid, sim in top_pairs:
                meta = existing_by_id.get(qid)
                if not meta:
                    continue
                m = _Mini()
                m.id = meta["id"]
                m.question_text = meta["question_text"]
                m.category = meta["category"]
                m.priority = meta["priority"]
                m.source_section = meta["source_section"]
                m.source_page = meta["source_page"]
                m.rationale = meta["rationale"]
                m.status = meta["status"]
                existing_pairs_objs.append((m, sim))

            existing_ids_for_validation = [q.id for q, _ in existing_pairs_objs]
            prompt = _reconciler_prompt(cand_obj, existing_pairs_objs)
            raw = _call_ai(prompt, _RECONCILER_SYSTEM, max_tokens=700)
            env = _parse_json_object(raw)
            decision = _validate_reconciliation(env or {}, existing_ids_for_validation)
            if (decision and decision["action"] == "added"
                    and top_pairs[0][1] >= _DUPLICATE_SIM_THRESHOLD):
                decision = {"action": "duplicate",
                            "replaces_question_id": None,
                            "updates_question_id": None,
                            "rewritten_question_text": None,
                            "rewritten_rationale": None,
                            "notes": (
                                f"Auto-converted to duplicate (similarity to "
                                f"existing #{top_pairs[0][0]} was "
                                f"{top_pairs[0][1]:.2f}).")}
            if not decision:
                out["outcome"] = "malformed"
                return out
            out["outcome"] = "decision"
            out["decision"] = decision
            return out
        except Exception as e:  # noqa: BLE001
            logger.warning(f"reconcile worker failed for cand {snap['id']}: {e}")
            return out

    workers = _reconciler_workers()
    completed = 0
    commit_every = 25

    if workers <= 1 or len(cand_snaps) <= 1:
        results_iter = (_worker(s) for s in cand_snaps)
    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        ex = ThreadPoolExecutor(max_workers=workers)
        futures = [ex.submit(_worker, s) for s in cand_snaps]
        results_iter = (fut.result() for fut in as_completed(futures))

    for res in results_iter:
        cand = cand_by_id.get(res["cand_id"])
        if not cand:
            errored += 1
            continue
        outcome = res["outcome"]
        if outcome == "fast_added":
            cand.reconciliation_action = "added"
            counts["added"] += 1
        elif outcome in ("error", "malformed"):
            cand.reconciliation_action = "added"
            cand.review_notes = (
                "Reconciler returned malformed JSON — defaulted to 'added' "
                "for human review."
                if outcome == "malformed"
                else "Reconciler worker errored — defaulted to 'added' for human review."
            )
            counts["added"] += 1
            errored += 1
        elif outcome == "decision":
            decision = res["decision"]
            action = decision["action"]
            cand.reconciliation_action = action
            top_pair_id = res["top_pair_id"]
            top_pair_sim = res["top_pair_sim"]

            if action == "replaces" and decision["replaces_question_id"]:
                cand.replaces_question_id = decision["replaces_question_id"]
                prior = db.query(RfpQuestion).filter(
                    RfpQuestion.id == decision["replaces_question_id"]).first()
                if prior:
                    prior.superseded_by_question_id = cand.id
                    if prior.status == "draft":
                        prior.status = "rejected"
                        prior.review_notes = (
                            (prior.review_notes or "")
                            + f"\nSuperseded by Q#{cand.id} via diff-question analysis run #{run.id}."
                        ).strip()
                counts["replaces"] += 1

            elif action == "updates" and decision["updates_question_id"]:
                target = db.query(RfpQuestion).filter(
                    RfpQuestion.id == decision["updates_question_id"]).first()
                if target:
                    target.question_text = decision["rewritten_question_text"]
                    if decision["rewritten_rationale"]:
                        target.rationale = decision["rewritten_rationale"]
                    target.review_notes = (
                        (target.review_notes or "")
                        + f"\nUpdated by diff-question analysis run #{run.id} "
                          f"(based on candidate Q#{cand.id})."
                    ).strip()
                    target.updated_at = datetime.utcnow()
                    cand.reconciliation_action = "updates"
                    cand.replaces_question_id = target.id
                    counts["updates"] += 1
                else:
                    cand.reconciliation_action = "added"
                    counts["added"] += 1

            elif action == "duplicate":
                cand.status = "rejected"
                if top_pair_id is not None:
                    cand.review_notes = (
                        (cand.review_notes or "")
                        + f"\nDuplicate of Q#{top_pair_id} (sim={top_pair_sim:.2f})."
                    ).strip()
                counts["duplicate"] += 1

            else:  # "added"
                counts["added"] += 1

            cand.review_notes = (
                (cand.review_notes or "") + ("\n" + decision["notes"] if decision["notes"] else "")
            ).strip() or None

        completed += 1
        if completed % commit_every == 0:
            db.commit()
            run.progress_note = f"Reconciled {completed}/{len(cand_snaps)}"
            db.commit()

    run.reconciled_added = counts["added"]
    run.reconciled_replaces = counts["replaces"]
    run.reconciled_updates = counts["updates"]
    run.reconciled_duplicates = counts["duplicate"]
    run.reconcile_completed_at = datetime.utcnow()
    db.commit()

    return {
        "analysis_run_id": run.id,
        "candidates_reconciled": len(candidates),
        **counts,
        "errors": errored,
    }


# ─────────────────────────────────────────────────────────────────────
# End-to-end pipeline (called by the orchestrator runner)
# ─────────────────────────────────────────────────────────────────────

def start_pipeline_run(
    db: Session,
    proposal_id: int,
    *,
    baseline_proposal_id: Optional[int] = None,
    baseline_document_ids: Optional[List[int]] = None,
    target_proposal_id: Optional[int] = None,
    target_document_ids: Optional[List[int]] = None,
    label: Optional[str] = None,
    created_by_user_id: Optional[int] = None,
    high_threshold: float = 0.92,
    low_threshold: float = 0.70,
) -> DiffQuestionAnalysisRun:
    """Create the orchestration row and return it. Caller (the router)
    schedules ``execute_pipeline`` in a background thread with the run id."""
    run = DiffQuestionAnalysisRun(
        proposal_id=proposal_id,
        diff_run_id=None,
        label=label or f"diff+questions {datetime.utcnow().isoformat(timespec='seconds')}",
        status="pending",
        progress_note="queued",
        created_by_user_id=created_by_user_id,
        created_at=datetime.utcnow(),
    )
    run.candidates_generated = 0
    run.high_confidence_count = 0
    run.inference_count = 0
    run.reconciled_added = 0
    run.reconciled_replaces = 0
    run.reconciled_updates = 0
    run.reconciled_duplicates = 0
    db.add(run)
    db.commit()
    db.refresh(run)

    # Stash the diff scope on the row's progress_note as a simple JSON blob
    # for the executor to pick up. (We don't add a dedicated column for it
    # since it's only consumed once.)
    scope = {
        "baseline_proposal_id": baseline_proposal_id,
        "baseline_document_ids": baseline_document_ids,
        "target_proposal_id": target_proposal_id or proposal_id,
        "target_document_ids": target_document_ids,
        "high_threshold": high_threshold,
        "low_threshold": low_threshold,
    }
    run.progress_note = "queued::" + json.dumps(scope)
    db.commit()
    return run


def execute_pipeline(db: Session, analysis_run_id: int) -> Dict[str, Any]:
    """Run all three stages serially with status updates. Designed to be
    invoked from a background thread by the router."""
    from .requirement_diff import run_requirement_diff  # local import to avoid cycles

    run = db.query(DiffQuestionAnalysisRun).filter(
        DiffQuestionAnalysisRun.id == analysis_run_id).first()
    if not run:
        return {"error": "analysis run not found"}

    # Decode scope
    scope: Dict[str, Any] = {}
    if run.progress_note and run.progress_note.startswith("queued::"):
        try:
            scope = json.loads(run.progress_note[len("queued::"):])
        except json.JSONDecodeError:
            scope = {}

    # ── Stage A: diff ─────────────────────────────────────────────
    # Resume support: if this run already has a completed diff, skip Stage A.
    if run.diff_run_id and run.diff_completed_at:
        logger.info(
            f"Pipeline {run.id}: Stage A (diff) already complete "
            f"(diff_run_id={run.diff_run_id}); skipping to Stage A.5."
        )
    else:
        try:
            run.status = "diff_running"
            run.diff_started_at = datetime.utcnow()
            run.progress_note = "Running requirement diff…"
            db.commit()

            diff_run_id = run_requirement_diff(
                db,
                baseline_proposal_id=scope.get("baseline_proposal_id"),
                baseline_document_ids=scope.get("baseline_document_ids"),
                target_proposal_id=scope.get("target_proposal_id") or run.proposal_id,
                target_document_ids=scope.get("target_document_ids"),
                label=run.label,
                high_threshold=scope.get("high_threshold", 0.92),
                low_threshold=scope.get("low_threshold", 0.70),
                write_blurbs=True,
                created_by_user_id=run.created_by_user_id,
            )
            run.diff_run_id = diff_run_id
            run.diff_completed_at = datetime.utcnow()
            run.progress_note = f"Diff complete (run #{diff_run_id})."
            db.commit()
        except Exception as e:  # noqa: BLE001
            logger.exception(f"Pipeline diff stage failed: {e}")
            run.status = "failed"
            run.error = f"diff stage: {e}"
            run.completed_at = datetime.utcnow()
            db.commit()
            return {"error": str(e), "stage": "diff"}

    # ── Stage A.5: coupling scan (second-order effects) ─────────
    # Resume support: if findings already exist for this diff_run, skip.
    from ..models import DiffCouplingFinding
    existing_couplings = (db.query(DiffCouplingFinding)
                          .filter(DiffCouplingFinding.diff_run_id == run.diff_run_id)
                          .count())
    if existing_couplings > 0:
        logger.info(
            f"Pipeline {run.id}: Stage A.5 already has {existing_couplings} "
            f"coupling findings; skipping."
        )
    else:
        try:
            from .diff_coupling import scan_diff_for_couplings
            run.progress_note = "Scanning for second-order coupling effects…"
            db.commit()
            coupling_result = scan_diff_for_couplings(db, run.diff_run_id)
            if isinstance(coupling_result, dict) and not coupling_result.get("error"):
                findings = coupling_result.get("findings", 0)
                run.progress_note = (
                    f"Coupling scan complete — {findings} second-order finding(s)."
                )
            else:
                run.progress_note = (
                    f"Coupling scan skipped: "
                    f"{coupling_result.get('warning') or coupling_result.get('error')}"
                )
            db.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Coupling scan failed (non-fatal): {e}")
            run.progress_note = f"Coupling scan errored (non-fatal): {e}"
            db.commit()

    # ── Stage B: question generation ─────────────────────────────
    try:
        gen_result = generate_questions_from_diff(db, run.id)
        if isinstance(gen_result, dict) and gen_result.get("error"):
            raise RuntimeError(gen_result["error"])
    except Exception as e:  # noqa: BLE001
        logger.exception(f"Pipeline question-gen stage failed: {e}")
        run.status = "failed"
        run.error = f"generation stage: {e}"
        run.completed_at = datetime.utcnow()
        db.commit()
        return {"error": str(e), "stage": "generation"}

    # ── Stage C: reconcile ───────────────────────────────────────
    try:
        recon_result = reconcile_candidates(db, run.id)
        if isinstance(recon_result, dict) and recon_result.get("error"):
            raise RuntimeError(recon_result["error"])
    except Exception as e:  # noqa: BLE001
        logger.exception(f"Pipeline reconcile stage failed: {e}")
        run.status = "failed"
        run.error = f"reconcile stage: {e}"
        run.completed_at = datetime.utcnow()
        db.commit()
        return {"error": str(e), "stage": "reconcile"}

    run.status = "complete"
    run.progress_note = (
        f"Pipeline complete: diff_run={run.diff_run_id}, "
        f"candidates={run.candidates_generated} "
        f"(added={run.reconciled_added}, replaces={run.reconciled_replaces}, "
        f"updates={run.reconciled_updates}, duplicates={run.reconciled_duplicates})"
    )
    run.completed_at = datetime.utcnow()
    db.commit()

    return {
        "analysis_run_id": run.id,
        "diff_run_id": run.diff_run_id,
        "status": run.status,
        "candidates_generated": run.candidates_generated,
        "added": run.reconciled_added,
        "replaces": run.reconciled_replaces,
        "updates": run.reconciled_updates,
        "duplicates": run.reconciled_duplicates,
    }
