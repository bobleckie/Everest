"""
Requirement Diff — compare a baseline (prior solicitation / revision) against a
target (new one) at the requirement level.

Pipeline:
  1. Load baseline + target RfpRequirement rows (by proposal_id or doc_ids).
  2. Embed each requirement (title + description + source_text) locally via
     embedding_service.embed_texts (no network, deterministic).
  3. Greedy bipartite match: for each target req, pick the best-scoring baseline
     req above low_match_threshold that hasn't been claimed yet.
  4. Classify:
       sim >= high  → "unchanged"  (cheap: text equality / very high sim)
       low <= sim < high → LLM adjudication returns same | changed | different
       unmatched target → "added"
       unmatched baseline → "removed"
  5. For every "changed" pair, LLM writes:
       - change_summary (1 sentence: what changed)
       - impact_blurb    (2-3 sentences: why it matters for the bid)
       - impact_severity (critical | high | medium | low | informational)

Persisted as RfpRequirementDiff rows keyed to a RfpDiffRun.

Public entry points:
  - run_requirement_diff(db, baseline_scope, target_scope, label=None, ...)
  - get_diff_results(db, run_id, status_filter=None)
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from ..models import (
    RfpDiffRun, RfpRequirement, RfpRequirementDiff,
)
from .embedding_service import embed_texts, cosine_similarity
from .orchestrator_agent import _call_ai

logger = logging.getLogger(__name__)


# ── Scope loading ────────────────────────────────────────────────────

def _load_requirements(
    db: Session,
    proposal_id: Optional[int],
    document_ids: Optional[List[int]],
) -> List[RfpRequirement]:
    q = db.query(RfpRequirement)
    if document_ids:
        q = q.filter(RfpRequirement.document_id.in_(document_ids))
    elif proposal_id is not None:
        q = q.filter(RfpRequirement.proposal_id == proposal_id)
    else:
        return []
    return q.all()


def _req_text_for_embed(r: RfpRequirement) -> str:
    parts = []
    if r.section_id:
        parts.append(str(r.section_id))
    if r.title:
        parts.append(str(r.title))
    if r.description:
        parts.append(str(r.description))
    if r.source_text:
        parts.append(str(r.source_text))
    return " \n ".join(parts).strip() or (r.title or "")


# ── Lightweight text-normalization for the "effectively identical" check ──

_WS = re.compile(r"\s+")

def _norm_for_equality(s: Optional[str]) -> str:
    if not s:
        return ""
    return _WS.sub(" ", s.strip().lower())


def _effectively_identical(a: RfpRequirement, b: RfpRequirement) -> bool:
    """Return True if the two requirements are clearly the same text, used to
    short-circuit LLM adjudication for obvious unchanged matches."""
    if _norm_for_equality(a.title) == _norm_for_equality(b.title):
        # Titles match; confirm source_text or description also matches closely.
        if _norm_for_equality(a.source_text) == _norm_for_equality(b.source_text):
            return True
        if _norm_for_equality(a.description) == _norm_for_equality(b.description):
            return True
    return False


# ── LLM adjudication (borderline pairs) ──────────────────────────────

_ADJUDICATOR_SYSTEM = (
    "You are a senior RFP analyst comparing two requirements from a prior and "
    "a new solicitation. You decide whether they are the same requirement, a "
    "changed version of the same requirement, or different requirements. You "
    "always respond with a single JSON object only."
)


def _adjudicate_pair(baseline: RfpRequirement, target: RfpRequirement) -> dict:
    """Ask the LLM whether baseline and target are same / changed / different.
    Returns dict with keys: verdict, change_summary (if changed)."""
    def _fmt(r: RfpRequirement) -> str:
        return (
            f"  section_id: {r.section_id or '(none)'}\n"
            f"  category:   {r.category or '(none)'}\n"
            f"  priority:   {r.priority or '(none)'}\n"
            f"  title:      {r.title or ''}\n"
            f"  description: {r.description or ''}\n"
            f"  source_text: {r.source_text or ''}"
        )

    prompt = f"""Compare these two RFP requirements.

--- BASELINE (prior solicitation) ---
{_fmt(baseline)}

--- TARGET (new solicitation) ---
{_fmt(target)}

Decide:
- verdict: "same" if they express the same obligation with no material change
           (wording tweaks only, same thresholds, same forms, same dates).
           "changed" if they are the same obligation but with a MATERIAL change
           (different threshold, different deadline, different form name, scope
           added/removed, new exceptions, changed party, changed certification).
           "different" if they are actually two different requirements that only
           happen to share some keywords.
- change_summary: if verdict is "changed", ONE concise sentence describing what
  changed (e.g. "Insurance minimum raised from $1M to $2M per occurrence").
  Empty string otherwise.

Output a single JSON object only:
{{"verdict":"same|changed|different","change_summary":"..."}}
No preamble, no code fences.
"""
    try:
        resp = _call_ai(prompt, _ADJUDICATOR_SYSTEM, max_tokens=500)
        obj = _parse_single_json_object(resp)
        verdict = (obj.get("verdict") or "").strip().lower()
        if verdict not in {"same", "changed", "different"}:
            verdict = "changed"  # safest default when ambiguous
        return {
            "verdict": verdict,
            "change_summary": (obj.get("change_summary") or "").strip(),
        }
    except Exception as e:
        logger.warning(f"Adjudication failed for baseline={baseline.id} target={target.id}: {e}")
        # Fail-safe: treat as "changed" so the pair is flagged for human review.
        return {"verdict": "changed", "change_summary": ""}


# ── LLM impact blurb for changed pairs ───────────────────────────────

_BLURB_SYSTEM = (
    "You are the proposal capture lead. You explain to reviewers what changed "
    "between a prior RFP requirement and its new version, and why it matters "
    "for the bid. You are specific, bid-impact-focused, and brief. You always "
    "respond with a single JSON object only."
)


def _write_impact_blurb(baseline: RfpRequirement, target: RfpRequirement, change_summary: str) -> dict:
    """Returns dict with keys: impact_blurb, impact_severity."""
    def _fmt(r: RfpRequirement) -> str:
        return (
            f"  section: {r.section_id or ''}\n"
            f"  title:   {r.title or ''}\n"
            f"  text:    {r.source_text or r.description or ''}"
        )

    prompt = f"""Two versions of the same RFP requirement, prior and new:

--- PRIOR ---
{_fmt(baseline)}

--- NEW ---
{_fmt(target)}

Short change summary: {change_summary or '(not provided; infer from the two texts)'}

Produce the reviewer-facing explanation:
- impact_blurb: 2-3 sentences. State (a) what concretely changed, (b) why it
  matters for our bid (pricing, scope, compliance, staffing, schedule), and
  (c) what action the team should take to address it. Avoid restating both
  versions in full; the reviewer already sees them side-by-side.
- impact_severity: one of "critical" (DQ risk or major pricing/scope impact),
  "high" (meaningful rework or pricing impact), "medium" (worth noting,
  limited impact), "low" (minor wording/clarification), "informational"
  (no action needed).

Output a single JSON object only:
{{"impact_blurb":"...","impact_severity":"critical|high|medium|low|informational"}}
No preamble, no code fences.
"""
    try:
        resp = _call_ai(prompt, _BLURB_SYSTEM, max_tokens=600)
        obj = _parse_single_json_object(resp)
        sev = (obj.get("impact_severity") or "medium").strip().lower()
        if sev not in {"critical", "high", "medium", "low", "informational"}:
            sev = "medium"
        return {
            "impact_blurb": (obj.get("impact_blurb") or "").strip(),
            "impact_severity": sev,
        }
    except Exception as e:
        logger.warning(f"Blurb write failed for baseline={baseline.id} target={target.id}: {e}")
        return {"impact_blurb": "", "impact_severity": "medium"}


def _write_added_blurb(target: RfpRequirement) -> dict:
    """Explain why a newly-added requirement matters."""
    prompt = f"""A NEW requirement appears in the new solicitation that did NOT exist in the prior one:

  section: {target.section_id or ''}
  title:   {target.title or ''}
  text:    {target.source_text or target.description or ''}

Explain in 1-2 sentences why this addition matters for our bid and what action
the team should take. Then assign impact_severity.

Output a single JSON object only:
{{"impact_blurb":"...","impact_severity":"critical|high|medium|low|informational"}}
No preamble, no code fences.
"""
    try:
        resp = _call_ai(prompt, _BLURB_SYSTEM, max_tokens=400)
        obj = _parse_single_json_object(resp)
        sev = (obj.get("impact_severity") or "medium").strip().lower()
        if sev not in {"critical", "high", "medium", "low", "informational"}:
            sev = "medium"
        return {
            "impact_blurb": (obj.get("impact_blurb") or "").strip(),
            "impact_severity": sev,
        }
    except Exception as e:
        logger.warning(f"Added-blurb failed for target={target.id}: {e}")
        return {"impact_blurb": "", "impact_severity": "medium"}


def _write_removed_blurb(baseline: RfpRequirement) -> dict:
    """Explain why a removed requirement matters (or doesn't)."""
    prompt = f"""A requirement from the PRIOR solicitation is NOT present in the new one:

  section: {baseline.section_id or ''}
  title:   {baseline.title or ''}
  text:    {baseline.source_text or baseline.description or ''}

Explain in 1-2 sentences: is this a true removal (the obligation is gone), or
is it likely relocated/renamed elsewhere in the new doc that our matcher missed?
What action should the team take (verify removal, update compliance matrix)?
Then assign impact_severity.

Output a single JSON object only:
{{"impact_blurb":"...","impact_severity":"critical|high|medium|low|informational"}}
No preamble, no code fences.
"""
    try:
        resp = _call_ai(prompt, _BLURB_SYSTEM, max_tokens=400)
        obj = _parse_single_json_object(resp)
        sev = (obj.get("impact_severity") or "low").strip().lower()
        if sev not in {"critical", "high", "medium", "low", "informational"}:
            sev = "low"
        return {
            "impact_blurb": (obj.get("impact_blurb") or "").strip(),
            "impact_severity": sev,
        }
    except Exception as e:
        logger.warning(f"Removed-blurb failed for baseline={baseline.id}: {e}")
        return {"impact_blurb": "", "impact_severity": "low"}


# ── JSON parsing helper (single object, not array) ───────────────────

def _parse_single_json_object(s: str) -> dict:
    if not s:
        return {}
    t = s.strip()
    # strip code fences
    if t.startswith("```"):
        t = t.strip("`")
        # strip optional "json" language tag
        if t.lower().startswith("json"):
            t = t[4:].lstrip()
    # find first { and last }
    i = t.find("{")
    j = t.rfind("}")
    if i == -1 or j == -1 or j <= i:
        return {}
    try:
        return json.loads(t[i : j + 1])
    except json.JSONDecodeError:
        return {}


# ── Greedy matcher ───────────────────────────────────────────────────

def _greedy_match(
    baseline: List[RfpRequirement],
    target: List[RfpRequirement],
    low_threshold: float,
) -> Tuple[List[Tuple[RfpRequirement, RfpRequirement, float]], List[RfpRequirement], List[RfpRequirement]]:
    """
    Greedy bipartite matching.

    Returns (pairs, unmatched_baseline, unmatched_target) where:
      pairs is list of (baseline_req, target_req, similarity)
      unmatched_baseline and unmatched_target are the orphans on each side.
    """
    if not baseline or not target:
        return [], list(baseline), list(target)

    b_texts = [_req_text_for_embed(r) for r in baseline]
    t_texts = [_req_text_for_embed(r) for r in target]
    b_embs = embed_texts(b_texts)
    t_embs = embed_texts(t_texts)

    # Score matrix: target x baseline
    # For each target, find best baseline not yet claimed.
    claimed_b: set = set()
    pairs: List[Tuple[RfpRequirement, RfpRequirement, float]] = []
    unmatched_t: List[RfpRequirement] = []

    # Greedy in descending order of best-score for stability. Compute best for each
    # target first, then process targets from highest-best-score down.
    scored = []
    for ti, t_emb in enumerate(t_embs):
        best_bi = -1
        best_sim = -1.0
        for bi, b_emb in enumerate(b_embs):
            s = cosine_similarity(t_emb, b_emb)
            if s > best_sim:
                best_sim = s
                best_bi = bi
        scored.append((best_sim, ti, best_bi))

    scored.sort(reverse=True)  # highest similarity first

    # On each iteration, if the chosen baseline is claimed, re-scan.
    for _, ti, best_bi in scored:
        t_emb = t_embs[ti]
        # Re-scan among unclaimed to find the actual current-best, since earlier
        # matches may have claimed the original best_bi.
        cur_best_bi = -1
        cur_best_sim = -1.0
        for bi, b_emb in enumerate(b_embs):
            if bi in claimed_b:
                continue
            s = cosine_similarity(t_emb, b_emb)
            if s > cur_best_sim:
                cur_best_sim = s
                cur_best_bi = bi
        if cur_best_bi == -1 or cur_best_sim < low_threshold:
            unmatched_t.append(target[ti])
            continue
        claimed_b.add(cur_best_bi)
        pairs.append((baseline[cur_best_bi], target[ti], cur_best_sim))

    unmatched_b = [baseline[bi] for bi in range(len(baseline)) if bi not in claimed_b]
    return pairs, unmatched_b, unmatched_t


# ── Public: run the full diff ────────────────────────────────────────

def run_requirement_diff(
    db: Session,
    baseline_proposal_id: Optional[int] = None,
    baseline_document_ids: Optional[List[int]] = None,
    target_proposal_id: Optional[int] = None,
    target_document_ids: Optional[List[int]] = None,
    label: Optional[str] = None,
    high_threshold: float = 0.92,
    low_threshold: float = 0.70,
    write_blurbs: bool = True,
    created_by_user_id: Optional[int] = None,
) -> int:
    """
    Execute a full diff run. Returns the RfpDiffRun.id.

    `write_blurbs=False` skips the per-change impact-blurb LLM call to save time;
    blurbs can be generated lazily later from the API.
    """
    run = RfpDiffRun(
        label=label,
        baseline_proposal_id=baseline_proposal_id,
        baseline_document_ids=json.dumps(baseline_document_ids) if baseline_document_ids else None,
        target_proposal_id=target_proposal_id,
        target_document_ids=json.dumps(target_document_ids) if target_document_ids else None,
        status="running",
        progress_note="loading requirements",
        high_match_threshold=high_threshold,
        low_match_threshold=low_threshold,
        created_by_user_id=created_by_user_id,
        created_at=datetime.utcnow(),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    try:
        baseline = _load_requirements(db, baseline_proposal_id, baseline_document_ids)
        target = _load_requirements(db, target_proposal_id, target_document_ids)
        logger.info(f"Diff run {run.id}: baseline={len(baseline)} reqs, target={len(target)} reqs")
        run.progress_note = f"matching {len(target)} target vs {len(baseline)} baseline"
        db.commit()

        pairs, unmatched_b, unmatched_t = _greedy_match(baseline, target, low_threshold)
        logger.info(
            f"Diff run {run.id}: {len(pairs)} pairs, "
            f"{len(unmatched_b)} removed, {len(unmatched_t)} added"
        )

        counts = {"unchanged": 0, "changed": 0, "added": 0, "removed": 0}

        # Classify each pair.
        for b, t, sim in pairs:
            if sim >= high_threshold or _effectively_identical(b, t):
                status = "unchanged"
                change_summary = None
                method = "embedding"
            else:
                # Borderline — ask the adjudicator.
                verdict = _adjudicate_pair(b, t)
                method = "llm_adjudicated"
                if verdict["verdict"] == "same":
                    status = "unchanged"
                    change_summary = None
                elif verdict["verdict"] == "different":
                    # The matcher was wrong — split into added + removed.
                    db.add(RfpRequirementDiff(
                        diff_run_id=run.id,
                        baseline_requirement_id=b.id,
                        target_requirement_id=None,
                        status="removed",
                        similarity=sim,
                        match_method=method,
                        change_summary=None,
                        created_at=datetime.utcnow(),
                    ))
                    db.add(RfpRequirementDiff(
                        diff_run_id=run.id,
                        baseline_requirement_id=None,
                        target_requirement_id=t.id,
                        status="added",
                        similarity=sim,
                        match_method=method,
                        change_summary=None,
                        created_at=datetime.utcnow(),
                    ))
                    counts["removed"] += 1
                    counts["added"] += 1
                    continue
                else:
                    status = "changed"
                    change_summary = verdict.get("change_summary") or None

            row = RfpRequirementDiff(
                diff_run_id=run.id,
                baseline_requirement_id=b.id,
                target_requirement_id=t.id,
                status=status,
                similarity=sim,
                match_method=method,
                change_summary=change_summary,
                created_at=datetime.utcnow(),
            )
            db.add(row)
            counts[status] += 1

            if status == "changed" and write_blurbs:
                blurb = _write_impact_blurb(b, t, change_summary or "")
                row.impact_blurb = blurb["impact_blurb"]
                row.impact_severity = blurb["impact_severity"]

        # Unmatched baselines → removed
        for b in unmatched_b:
            row = RfpRequirementDiff(
                diff_run_id=run.id,
                baseline_requirement_id=b.id,
                target_requirement_id=None,
                status="removed",
                similarity=None,
                match_method="embedding",
                created_at=datetime.utcnow(),
            )
            db.add(row)
            counts["removed"] += 1
            if write_blurbs:
                blurb = _write_removed_blurb(b)
                row.impact_blurb = blurb["impact_blurb"]
                row.impact_severity = blurb["impact_severity"]

        # Unmatched targets → added
        for t in unmatched_t:
            row = RfpRequirementDiff(
                diff_run_id=run.id,
                baseline_requirement_id=None,
                target_requirement_id=t.id,
                status="added",
                similarity=None,
                match_method="embedding",
                created_at=datetime.utcnow(),
            )
            db.add(row)
            counts["added"] += 1
            if write_blurbs:
                blurb = _write_added_blurb(t)
                row.impact_blurb = blurb["impact_blurb"]
                row.impact_severity = blurb["impact_severity"]

        run.counts_added = counts["added"]
        run.counts_removed = counts["removed"]
        run.counts_changed = counts["changed"]
        run.counts_unchanged = counts["unchanged"]
        run.status = "complete"
        run.progress_note = "done"
        run.completed_at = datetime.utcnow()
        db.commit()
        logger.info(
            f"Diff run {run.id} complete: "
            f"added={counts['added']} removed={counts['removed']} "
            f"changed={counts['changed']} unchanged={counts['unchanged']}"
        )
        return run.id

    except Exception as e:
        logger.exception(f"Diff run {run.id} failed: {e}")
        run.status = "failed"
        run.error = str(e)
        run.completed_at = datetime.utcnow()
        db.commit()
        raise
