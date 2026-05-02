"""Diff coupling scanner.

The pair-by-pair diff already classifies every requirement as
unchanged/changed/added/removed. But changes can have **second-order
effects**: a change in one requirement can implicitly shift the meaning
of an unchanged requirement that depends on shared definitions,
thresholds, deadlines, or scope.

Example: 2021 said "Contractor shall provide adequate staffing" plus a
separate row defined "adequate" as 60 staff. 2026 keeps the first
sentence verbatim (sim≈0.99 → unchanged) but raises the staffing
definition to 80. The diff sees two independent rows and never
connects them — a human reading the new RFP would.

What this scanner does
----------------------
For each CHANGED diff row, find the top-K most similar requirements in
the new RFP that DIDN'T change (or that the diff classified
"unchanged"). Ask the LLM to decide whether the change has a real
second-order effect on each candidate. Persist findings as
``DiffCouplingFinding`` rows.

The LLM is told to be conservative — only flag a coupling when the
downstream effect is concrete and bid-relevant. The output is a
suggested question the team could ask the agency for clarification.

Pure-dict workers, threadpool parallelism, idempotent — same patterns
as our other LLM passes.
"""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from ..models import (
    DiffCouplingFinding, RfpDiffRun, RfpRequirement, RfpRequirementDiff,
)
from .embedding_service import cosine_similarity, embed_text
from .orchestrator_agent import _call_ai

logger = logging.getLogger(__name__)


# Tuning knobs
TOP_K_NEIGHBORS = 6              # candidate downstream rows per changed row
MIN_NEIGHBOR_SIM = 0.55           # below this we're not even close enough to bother
MAX_CHANGED_ROWS = 200            # cap LLM cost per run; prefer high-severity
ALLOWED_KINDS = {"definition_drift", "shared_term",
                 "scope_overlap", "schedule_dependency"}
ALLOWED_SEVERITY = {"critical", "high", "medium", "low"}


def _workers() -> int:
    try:
        return max(1, min(32, int(os.environ.get("COUPLING_PARALLEL", "6"))))
    except ValueError:
        return 6


# ─────────────────────────────────────────────────────────────────────
# JSON parser
# ─────────────────────────────────────────────────────────────────────

def _parse_json_obj(text: str) -> Optional[Dict[str, Any]]:
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
# Severity ranking — only consider higher-impact changed rows
# ─────────────────────────────────────────────────────────────────────

_SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1,
                  "informational": 0}


# ─────────────────────────────────────────────────────────────────────
# LLM coupling-pair prompt
# ─────────────────────────────────────────────────────────────────────

_SYSTEM = (
    "You are a senior Parsons capture analyst hunting SECOND-ORDER "
    "effects in an RFP diff. You receive (a) a CHANGED requirement "
    "from the new RFP (with what changed and why it matters) and "
    "(b) one CANDIDATE other requirement in the new RFP that may be "
    "implicitly affected by that change. Your job: decide whether the "
    "change has a concrete, bid-relevant effect on the candidate.\n\n"
    "BE CONSERVATIVE. Only flag a coupling when:\n"
    "  * the changed row redefines a term or threshold the candidate "
    "    relies on, OR\n"
    "  * the changed row moves a deadline / quantity that the candidate "
    "    references implicitly, OR\n"
    "  * both rows describe the same scope and the change in one creates "
    "    an inconsistency / ambiguity in the other.\n\n"
    "Do NOT flag a coupling when the rows merely share a few keywords "
    "but address different obligations. False couplings produce "
    "nonsensical questions and erode the team's credibility.\n\n"
    "Output VALID JSON ONLY. NO preamble. NO code fences."
)


def _format_changed_row(
    diff_row: Dict[str, Any],
    changed_baseline: Dict[str, Any],
    changed_target: Dict[str, Any],
) -> str:
    return (
        f"CHANGED REQUIREMENT (the source of the change)\n"
        f"  diff status: {diff_row.get('status')}\n"
        f"  change summary: {diff_row.get('change_summary') or '(none)'}\n"
        f"  impact severity: {diff_row.get('impact_severity') or '(none)'}\n"
        f"  impact blurb: {diff_row.get('impact_blurb') or '(none)'}\n"
        f"\n  -- Baseline (old) --\n"
        f"  section: {changed_baseline.get('section_id') or '(none)'}\n"
        f"  title: {(changed_baseline.get('title') or '').strip()[:200]}\n"
        f"  text: {(changed_baseline.get('source_text') or changed_baseline.get('description') or '').strip()[:1000]}\n"
        f"\n  -- Target (new) --\n"
        f"  section: {changed_target.get('section_id') or '(none)'}\n"
        f"  title: {(changed_target.get('title') or '').strip()[:200]}\n"
        f"  text: {(changed_target.get('source_text') or changed_target.get('description') or '').strip()[:1000]}"
    )


def _format_candidate(neighbor: Dict[str, Any], sim: float) -> str:
    return (
        f"CANDIDATE NEIGHBOR (in the new RFP, may be affected)\n"
        f"  similarity to changed row: {sim:.2f}\n"
        f"  requirement_id: {neighbor.get('id')}\n"
        f"  section: {neighbor.get('section_id') or '(none)'}\n"
        f"  title: {(neighbor.get('title') or '').strip()[:240]}\n"
        f"  text: {(neighbor.get('source_text') or neighbor.get('description') or '').strip()[:1200]}"
    )


def _build_prompt(
    diff_row: Dict[str, Any],
    changed_baseline: Dict[str, Any],
    changed_target: Dict[str, Any],
    neighbor: Dict[str, Any],
    sim: float,
) -> str:
    return f"""{_format_changed_row(diff_row, changed_baseline, changed_target)}

{_format_candidate(neighbor, sim)}

Decide whether the change above has a CONCRETE second-order effect on
the candidate. Output a SINGLE JSON object EXACTLY this shape:
{{
  "is_coupled": true | false,
  "coupling_kind": "definition_drift" | "shared_term" | "scope_overlap" | "schedule_dependency" | null,
  "severity": "critical" | "high" | "medium" | "low" | null,
  "summary": "<one short sentence: how the change affects this candidate>",
  "detail": "<2-3 sentences with specifics; cite text from both rows>",
  "suggested_question": "<empty if is_coupled=false; otherwise ONE interrogative question to the agency that surfaces the ambiguity. Begin with 'Ref. Section X.Y: ...'>"
}}
Rules:
* If the rows are merely on the same topic but the change doesn't move
  anything in the candidate, set is_coupled=false.
* Suggested questions must be specific, grounded in the cited text, and
  end with '?'. Generic 'please clarify' questions are nonsense — return
  is_coupled=false instead.
* JSON ONLY."""


def _validate(env: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(env, dict):
        return None
    is_coupled = bool(env.get("is_coupled"))
    if not is_coupled:
        return {"is_coupled": False}
    kind = (env.get("coupling_kind") or "").lower().strip()
    if kind not in ALLOWED_KINDS:
        return None
    severity = (env.get("severity") or "medium").lower().strip()
    if severity not in ALLOWED_SEVERITY:
        severity = "medium"
    summary = (env.get("summary") or "").strip()[:300]
    if not summary:
        return None
    detail = (env.get("detail") or "").strip()[:1500] or None
    sq = (env.get("suggested_question") or "").strip()
    if sq and (not sq.endswith("?") or len(sq) > 1500):
        sq = None
    if sq and len(sq) < 12:  # too short to be a real question
        sq = None
    return {
        "is_coupled": True,
        "coupling_kind": kind,
        "severity": severity,
        "summary": summary,
        "detail": detail,
        "suggested_question": sq or None,
    }


# ─────────────────────────────────────────────────────────────────────
# Loaders
# ─────────────────────────────────────────────────────────────────────

def _req_text_for_embed(r) -> str:
    """Same shape as requirement_diff embed inputs so similarities are comparable."""
    parts = []
    if r.section_id: parts.append(str(r.section_id))
    if r.title: parts.append(str(r.title))
    if r.description: parts.append(str(r.description))
    if r.source_text: parts.append(str(r.source_text))
    return " \n ".join(parts).strip() or (r.title or "")


def _req_to_snap(r: RfpRequirement) -> Dict[str, Any]:
    return {
        "id": r.id, "requirement_id": r.requirement_id,
        "section_id": r.section_id, "category": r.category,
        "title": r.title, "description": r.description,
        "source_text": r.source_text,
    }


# ─────────────────────────────────────────────────────────────────────
# Public entry — run a coupling scan over a completed diff run
# ─────────────────────────────────────────────────────────────────────

def scan_diff_for_couplings(
    db: Session,
    diff_run_id: int,
    *,
    severity_floor: str = "medium",
    max_changed_rows: int = MAX_CHANGED_ROWS,
) -> Dict[str, Any]:
    """Scan a completed diff for second-order coupling effects.

    For each changed/added diff row above ``severity_floor``, finds the
    top-K most similar UNCHANGED target rows in the new RFP, asks the
    LLM whether each is concretely affected by the change, persists the
    confirmed couplings.
    """
    run = db.query(RfpDiffRun).filter(RfpDiffRun.id == diff_run_id).first()
    if not run:
        return {"error": "diff run not found"}
    if run.status != "complete":
        return {"error": f"diff run not complete (status={run.status})"}

    floor_rank = _SEVERITY_RANK.get(severity_floor.lower(), 2)

    # Pull all diff rows. We treat 'changed' as the primary source
    # (added rows have nothing to compare against on the baseline side
    # for definition-drift logic), but added rows can still affect a
    # downstream unchanged row, so we include them too.
    diff_rows = (db.query(RfpRequirementDiff)
                 .filter(RfpRequirementDiff.diff_run_id == diff_run_id)
                 .all())
    sources = [d for d in diff_rows
               if d.status in ("changed", "added")
               and _SEVERITY_RANK.get((d.impact_severity or "medium").lower(), 2)
                   >= floor_rank][:max_changed_rows]
    if not sources:
        return {"diff_run_id": diff_run_id,
                "scanned": 0, "findings": 0,
                "warning": f"No changed/added rows at severity >= {severity_floor}."}

    # Pre-fetch target-side requirements by id for fast lookup
    target_ids = set()
    for d in diff_rows:
        if d.target_requirement_id:
            target_ids.add(d.target_requirement_id)
    target_reqs = {
        r.id: r for r in
        db.query(RfpRequirement).filter(RfpRequirement.id.in_(target_ids)).all()
    }

    # The "unchanged universe" — target rows the diff classified unchanged
    unchanged_target_ids = [
        d.target_requirement_id for d in diff_rows
        if d.status == "unchanged" and d.target_requirement_id in target_reqs
    ]
    unchanged_reqs = [target_reqs[i] for i in unchanged_target_ids
                       if i in target_reqs]
    if not unchanged_reqs:
        return {"diff_run_id": diff_run_id, "scanned": 0, "findings": 0,
                "warning": "No unchanged target rows to couple against."}

    # Build embedding cache for the unchanged universe (one shot per run)
    logger.info(
        f"Coupling scan diff_run={diff_run_id}: {len(sources)} source rows, "
        f"{len(unchanged_reqs)} unchanged neighbors"
    )
    unchanged_vecs: List[Tuple[RfpRequirement, List[float]]] = []
    for r in unchanged_reqs:
        text = _req_text_for_embed(r)
        if not text:
            continue
        try:
            v = embed_text(text)
            unchanged_vecs.append((r, v))
        except Exception:
            continue

    # Pre-fetch baseline reqs that pair with changed sources
    baseline_ids = {d.baseline_requirement_id for d in sources
                    if d.baseline_requirement_id}
    baseline_reqs = {
        r.id: r for r in
        db.query(RfpRequirement).filter(RfpRequirement.id.in_(baseline_ids)).all()
    }

    scan_id = f"coupling-{uuid.uuid4().hex[:10]}"
    now = datetime.utcnow()
    workers = _workers()

    # Build pure-dict snapshots for workers (no SQLAlchemy session contact)
    work_items: List[Dict[str, Any]] = []
    for d in sources:
        target = target_reqs.get(d.target_requirement_id)
        baseline = baseline_reqs.get(d.baseline_requirement_id)
        if not target:
            continue
        target_text = _req_text_for_embed(target)
        if not target_text:
            continue
        try:
            t_vec = embed_text(target_text)
        except Exception:
            continue
        # Rank neighbors
        scored: List[Tuple[float, RfpRequirement]] = []
        for r, v in unchanged_vecs:
            if r.id == target.id:
                continue
            sim = cosine_similarity(t_vec, v)
            if sim >= MIN_NEIGHBOR_SIM:
                scored.append((sim, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        for sim, r in scored[:TOP_K_NEIGHBORS]:
            work_items.append({
                "diff_row": {
                    "id": d.id, "status": d.status,
                    "change_summary": d.change_summary,
                    "impact_severity": d.impact_severity,
                    "impact_blurb": d.impact_blurb,
                },
                "changed_baseline": _req_to_snap(baseline) if baseline else {},
                "changed_target": _req_to_snap(target),
                "neighbor": _req_to_snap(r),
                "similarity": float(sim),
            })

    if not work_items:
        return {"diff_run_id": diff_run_id, "scan_id": scan_id,
                "scanned": 0, "findings": 0,
                "warning": "No candidate neighbor pairs above the similarity floor."}

    logger.info(f"Coupling scan {scan_id}: {len(work_items)} pairs to evaluate")

    def _worker(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            prompt = _build_prompt(
                item["diff_row"], item["changed_baseline"],
                item["changed_target"], item["neighbor"], item["similarity"])
            raw = _call_ai(prompt, _SYSTEM, max_tokens=900)
            env = _parse_json_obj(raw)
            decision = _validate(env or {})
            if not decision or not decision.get("is_coupled"):
                return None
            return {**item, "decision": decision}
        except Exception as e:  # noqa: BLE001
            logger.warning(f"coupling worker failed: {e}")
            return None

    results: List[Dict[str, Any]] = []
    if workers <= 1:
        for it in work_items:
            r = _worker(it)
            if r:
                results.append(r)
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(_worker, it) for it in work_items]
            for fut in as_completed(futures):
                r = fut.result()
                if r:
                    results.append(r)

    # Persist findings (main thread)
    saved = 0
    for r in results:
        d = r["decision"]
        row = DiffCouplingFinding(
            diff_run_id=diff_run_id,
            source_diff_row_id=r["diff_row"]["id"],
            affected_target_requirement_id=r["neighbor"]["id"],
            coupling_similarity=r["similarity"],
            coupling_kind=d["coupling_kind"],
            severity=d["severity"],
            summary=d["summary"],
            detail=d["detail"],
            suggested_question=d["suggested_question"],
            status="open",
            scan_id=scan_id,
            created_at=now,
        )
        db.add(row)
        saved += 1
    db.commit()

    return {
        "diff_run_id": diff_run_id,
        "scan_id": scan_id,
        "scanned": len(work_items),
        "source_rows": len(sources),
        "unchanged_neighbors": len(unchanged_reqs),
        "findings": saved,
    }
