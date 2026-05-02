"""
Response Scorer — uses the configured LLM to score an RFP section response
against the active scoring rubric. Returns a 0-100 score plus strengths and
weaknesses, written from the perspective of the NJ T1628 evaluation committee.

Used by the Parsons response slide-out panel to compare Parsons' draft against
the targeted competitor's predicted response on the same section.

Reuses _call_ai / _load_ai_settings from competitor_analyst so SSL, key
resolution, and provider-priority behaviour stay consistent across the app.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from ..models import (
    CompetitorPrediction,
    Proposal,
    ProposalSection,
    ScoringRubric,
    ScoringRubricSection,
    SectionScore,
)
from .competitor_analyst import _call_ai, get_last_ai_error

logger = logging.getLogger(__name__)

# Limit how much section text we send to the LLM in a single scoring call.
# Sections longer than this are head/tail sampled so we still capture the opening
# and closing positioning without blowing past the model's context window.
_MAX_RESPONSE_CHARS = 12000


def _truncate(text: str, limit: int = _MAX_RESPONSE_CHARS) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    head = text[: int(limit * 0.7)]
    tail = text[-int(limit * 0.25):]
    return f"{head}\n\n...[middle truncated, {len(text) - limit} chars]...\n\n{tail}"


def _flatten_section_content(content: str | None) -> str:
    """Turn a Parsons section's stored JSON blob into readable narrative text.

    `ProposalSection.content` is stored as a JSON dict of `{field_key: value}`
    coming from `ParsonsServices.js`. The LLM scores the narrative, not the keys.
    """
    if not content:
        return ""
    try:
        data = json.loads(content)
        if isinstance(data, dict):
            parts = []
            for k, v in data.items():
                if v is None or (isinstance(v, str) and not v.strip()):
                    continue
                label = k.replace("_", " ").strip().title()
                parts.append(f"### {label}\n{v}")
            return "\n\n".join(parts) if parts else content
        return str(data)
    except (json.JSONDecodeError, TypeError):
        return content


def _parse_score_payload(raw: str) -> dict:
    """Pull a JSON object out of the LLM response. Tolerates code fences and prose."""
    if not raw:
        return {}
    # Strip code fences if present
    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)

    # First try direct parse
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Find the first {...} block
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return {}


def _coerce_score(value, default: int = 50) -> int:
    try:
        n = int(round(float(value)))
        return max(0, min(100, n))
    except (TypeError, ValueError):
        return default


def _stringify_list(items) -> str:
    if not items:
        return ""
    if isinstance(items, list):
        return "\n".join(f"- {str(x).strip()}" for x in items if str(x).strip())
    return str(items)


# ── Public API ───────────────────────────────────────────────────────


def score_section_response(
    *,
    db: Session,
    proposal_id: int,
    section_id: str,
    scorer_type: str,  # "parsons" or "competitor"
    scorer_name: Optional[str] = None,
    response_text: Optional[str] = None,
    rubric_section_id: Optional[str] = None,
) -> Optional[SectionScore]:
    """Score a single section's response against the active rubric.

    - For `scorer_type="parsons"` the response is loaded from `ProposalSection.content`
      (or `refined_content` if available) unless `response_text` is supplied.
    - For `scorer_type="competitor"` the response is loaded from `CompetitorPrediction.predicted_response`
      for the named competitor.

    When `section_id` is a rubric-level identifier (e.g. "technicalApproach") the
    rubric row is looked up by `section_id` directly. When scoring an extracted
    RFP section (e.g. "7.1") the caller should pass `rubric_section_id` so we
    load the correct weight/pass-fail context for the prompt. The persisted
    `SectionScore` row always uses the caller-supplied `section_id` as its key.

    Persists the result as a `SectionScore` row (upserted on
    `(proposal_id, section_id, scorer_type, scorer_name)`).
    """
    proposal = db.query(Proposal).filter(Proposal.id == proposal_id).first()
    if not proposal:
        logger.warning(f"score_section_response: proposal {proposal_id} not found")
        return None

    rubric = db.query(ScoringRubric).filter(ScoringRubric.is_default == True).first()
    if not rubric:
        logger.warning("score_section_response: no default rubric")
        return None

    rubric_lookup_id = rubric_section_id or section_id
    rubric_section = (
        db.query(ScoringRubricSection)
        .filter(
            ScoringRubricSection.rubric_id == rubric.id,
            ScoringRubricSection.section_id == rubric_lookup_id,
        )
        .first()
    )

    section_title = rubric_section.title if rubric_section else section_id
    weight_points = rubric_section.weight_points if rubric_section else 0
    pass_fail = rubric_section.pass_fail if rubric_section else False

    # Resolve response text
    if response_text is None:
        if scorer_type == "parsons":
            ps = (
                db.query(ProposalSection)
                .filter(
                    ProposalSection.proposal_id == proposal_id,
                    ProposalSection.section_id == section_id,
                )
                .first()
            )
            if ps:
                response_text = ps.refined_content or _flatten_section_content(ps.content)
        elif scorer_type == "competitor":
            from ..models import Competitor
            comp_q = db.query(Competitor).filter(Competitor.name == scorer_name) if scorer_name else None
            comp = comp_q.first() if comp_q else None
            if comp:
                pred = (
                    db.query(CompetitorPrediction)
                    .filter(
                        CompetitorPrediction.competitor_id == comp.id,
                        CompetitorPrediction.section_id == section_id,
                    )
                    .first()
                )
                if pred:
                    response_text = pred.predicted_response

    if not response_text or not response_text.strip():
        logger.info(
            f"score_section_response: no response content for "
            f"proposal={proposal_id} section={section_id} scorer={scorer_type}/{scorer_name}"
        )
        # Persist a 0 with a clear rationale so the UI can show "no content yet"
        return _upsert_score(
            db=db,
            proposal_id=proposal_id,
            section_id=section_id,
            scorer_type=scorer_type,
            scorer_name=scorer_name,
            score=0,
            rationale="No response content available for this section yet.",
        )

    # Build scoring prompt
    pass_fail_instruction = (
        "This is a PASS/FAIL gate. Award 100 if the response satisfies every mandatory requirement; "
        "otherwise award 0 and explain the specific failure."
        if pass_fail
        else f"This section is worth {weight_points} weighted points out of 100 in the rubric."
    )

    system = (
        "You are a senior procurement evaluator on the NJ Treasury / MVC evaluation committee scoring "
        "the Enhanced Motor Vehicle Inspection (T1628 / 20DPP00471) program. Score on the rubric "
        "1-10 scale (1-2 Poor, 3-4 Fair, 5-6 Good, 7-8 Very Good, 9-10 Excellent), then convert to a "
        "0-100 normalized score by multiplying by 10. Be honest and specific — vague responses score "
        "in the 30-50 range, generic boilerplate scores 50-65, well-structured responses with concrete "
        "evidence score 70-85, and only proposals with detailed evidence, named personnel, specific "
        "metrics, and proven past performance crack 85+. Cite missing items explicitly."
    )

    prompt = f"""Score this RFP response section.

## Rubric Section
- Section ID: `{section_id}`
- Title: {section_title}
- {pass_fail_instruction}

## Response Author
{"Parsons (incumbent)" if scorer_type == "parsons" else f"Competitor: {scorer_name or 'unknown'}"}

## Response Text
{_truncate(response_text)}

## Output Format — RETURN VALID JSON ONLY (no prose, no code fences)
{{
  "score": <integer 0-100>,
  "score_band": "<Poor|Fair|Good|Very Good|Excellent>",
  "summary": "<one-sentence verdict, max 200 chars>",
  "strengths": ["<concrete strength tied to the response text>", ...],
  "weaknesses": ["<concrete weakness or missing element>", ...],
  "evaluator_notes": "<2-3 sentence rationale referencing rubric criteria>"
}}
Return at most 5 strengths and 5 weaknesses. Strengths and weaknesses MUST quote or paraphrase the response text — do not invent facts."""

    raw = _call_ai(prompt, system=system)
    payload = _parse_score_payload(raw)

    if not payload:
        err = get_last_ai_error()
        rationale = (
            f"LLM scoring unavailable: {err.get('provider') or 'no provider'} "
            f"raised {err.get('type') or 'unknown error'} — {err.get('message') or 'no detail'}."
            if err and err.get("type")
            else "LLM scoring returned no parseable JSON."
        )
        return _upsert_score(
            db=db,
            proposal_id=proposal_id,
            section_id=section_id,
            scorer_type=scorer_type,
            scorer_name=scorer_name,
            score=0,
            rationale=rationale,
        )

    score = _coerce_score(payload.get("score"))
    summary = (payload.get("summary") or "").strip()
    notes = (payload.get("evaluator_notes") or "").strip()
    strengths_md = _stringify_list(payload.get("strengths"))
    weaknesses_md = _stringify_list(payload.get("weaknesses"))
    band = (payload.get("score_band") or "").strip()

    rationale_parts = []
    if summary:
        rationale_parts.append(f"**{band + ' — ' if band else ''}{summary}**")
    if notes:
        rationale_parts.append(notes)
    if strengths_md:
        rationale_parts.append(f"**Strengths**\n{strengths_md}")
    if weaknesses_md:
        rationale_parts.append(f"**Weaknesses**\n{weaknesses_md}")
    rationale = "\n\n".join(rationale_parts) if rationale_parts else None

    return _upsert_score(
        db=db,
        proposal_id=proposal_id,
        section_id=section_id,
        scorer_type=scorer_type,
        scorer_name=scorer_name,
        score=score,
        rationale=rationale,
    )


def _upsert_score(
    *,
    db: Session,
    proposal_id: int,
    section_id: str,
    scorer_type: str,
    scorer_name: Optional[str],
    score: int,
    rationale: Optional[str],
) -> SectionScore:
    existing = (
        db.query(SectionScore)
        .filter(
            SectionScore.proposal_id == proposal_id,
            SectionScore.section_id == section_id,
            SectionScore.scorer_type == scorer_type,
            SectionScore.scorer_name == scorer_name,
        )
        .first()
    )
    if existing:
        existing.score = score
        existing.rationale = rationale
        existing.scored_at = datetime.utcnow()
        db.commit()
        db.refresh(existing)
        return existing

    new_row = SectionScore(
        proposal_id=proposal_id,
        section_id=section_id,
        scorer_type=scorer_type,
        scorer_name=scorer_name,
        score=score,
        rationale=rationale,
    )
    db.add(new_row)
    db.commit()
    db.refresh(new_row)
    return new_row


def compute_aggregate(
    db: Session, proposal_id: int, target_competitor_name: Optional[str] = None
) -> dict:
    """Roll up section scores into a weighted total for Parsons vs the target competitor.

    Pass/fail sections do not contribute to the weighted total but are reported
    separately so the UI can flag a gate failure regardless of the weighted score.
    """
    rubric = db.query(ScoringRubric).filter(ScoringRubric.is_default == True).first()
    if not rubric:
        return {"error": "No rubric defined"}

    sections = (
        db.query(ScoringRubricSection)
        .filter(ScoringRubricSection.rubric_id == rubric.id)
        .order_by(ScoringRubricSection.sort_order)
        .all()
    )

    # Pull all relevant scores in one query
    parsons_scores = {
        s.section_id: s
        for s in db.query(SectionScore)
        .filter(
            SectionScore.proposal_id == proposal_id,
            SectionScore.scorer_type == "parsons",
        )
        .all()
    }
    comp_scores = {}
    if target_competitor_name:
        comp_scores = {
            s.section_id: s
            for s in db.query(SectionScore)
            .filter(
                SectionScore.proposal_id == proposal_id,
                SectionScore.scorer_type == "competitor",
                SectionScore.scorer_name == target_competitor_name,
            )
            .all()
        }

    parsons_weighted = 0.0
    comp_weighted = 0.0
    rows = []
    parsons_pf_pass = True
    comp_pf_pass = True
    parsons_pf_failures = []
    comp_pf_failures = []

    for sec in sections:
        p = parsons_scores.get(sec.section_id)
        c = comp_scores.get(sec.section_id)
        p_score = p.score if p else None
        c_score = c.score if c else None

        if sec.pass_fail:
            if p_score is not None and p_score < 100:
                parsons_pf_pass = False
                parsons_pf_failures.append(sec.section_id)
            if c_score is not None and c_score < 100:
                comp_pf_pass = False
                comp_pf_failures.append(sec.section_id)
        else:
            # Weighted contribution = (score/100) * weight_points
            if p_score is not None:
                parsons_weighted += (p_score / 100.0) * sec.weight_points
            if c_score is not None:
                comp_weighted += (c_score / 100.0) * sec.weight_points

        rows.append({
            "section_id": sec.section_id,
            "title": sec.title,
            "weight_points": sec.weight_points,
            "pass_fail": sec.pass_fail,
            "parsons_score": p_score,
            "competitor_score": c_score,
            "delta": (p_score - c_score) if (p_score is not None and c_score is not None) else None,
        })

    parsons_total = round(parsons_weighted, 1)
    comp_total = round(comp_weighted, 1) if target_competitor_name else None
    delta_total = round(parsons_total - comp_total, 1) if comp_total is not None else None

    if comp_total is None:
        verdict = "no_competitor"
    elif not parsons_pf_pass:
        verdict = "disqualified"
    elif not comp_pf_pass:
        verdict = "winning"  # competitor disqualified on a gate
    elif delta_total is None:
        verdict = "incomplete"
    elif delta_total >= 5:
        verdict = "winning"
    elif delta_total <= -5:
        verdict = "losing"
    else:
        verdict = "tossup"

    return {
        "proposal_id": proposal_id,
        "target_competitor_name": target_competitor_name,
        "rubric_max_weighted": 100,
        "parsons_weighted_total": parsons_total,
        "competitor_weighted_total": comp_total,
        "delta": delta_total,
        "parsons_passes_gates": parsons_pf_pass,
        "competitor_passes_gates": comp_pf_pass,
        "parsons_pf_failures": parsons_pf_failures,
        "competitor_pf_failures": comp_pf_failures,
        "verdict": verdict,  # winning | losing | tossup | disqualified | no_competitor | incomplete
        "sections": rows,
    }
