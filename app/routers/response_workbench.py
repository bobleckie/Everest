"""
Response Workbench — the single, consolidated surface for authoring RFP
responses at the EXTRACTED-SECTION granularity.

One conceptual "section of work" = one extracted RFP section_id (e.g. "7.1",
"3.13.8.1"). For each section the workbench tracks:

  * The extracted requirements (read-only, from RfpRequirement).
  * A Parsons narrative response (RfpSectionResponse, author_type='parsons').
  * Competitor "what would they write" responses (either from
    CompetitorPrediction — existing infrastructure — or from
    RfpSectionResponse.author_type='competitor' after the workbench lets
    the user refine them).
  * Per-scorer scores (SectionScore, reused without schema change).
  * Per-requirement compliance tags (JSON on RfpSectionResponse).
  * Cure-the-gap suggestions (SectionCureSuggestion, with approve/deny flow).

The router is deliberately additive: it does not mutate or remove any of the
existing compliance matrix / scoring / competitive-response data. The
frontend Response Workbench page reads from here; the legacy pages continue
to work as-is until we flip a feature flag to retire them.

URL prefix: /api/workbench
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..database import get_db
from ..models import (
    Competitor,
    CompetitorPrediction,
    IngestedDocument,
    Persona,
    Proposal,
    RfpRequirement,
    RfpSectionResponse,
    RfpSectionRubricMap,
    ScoringRubric,
    ScoringRubricSection,
    SectionCureSuggestion,
    SectionScore,
    User,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Helpers ──────────────────────────────────────────────────────────


def _section_sort_key(section_id: str):
    """Natural sort for section identifiers like "3", "3.2", "3.2H", "Appendix F"."""
    if not section_id:
        return (1, "", ())
    s = section_id.strip()
    m = re.match(r"^(\d+(?:\.\d+)*)(.*)$", s)
    if m:
        nums = tuple(int(p) for p in m.group(1).split("."))
        tail = m.group(2).lower()
        return (0, nums, tail)
    return (1, s.lower(), ())


# Extracted RFP categories that indicate a section needs an actual narrative
# response (as opposed to being purely informational / admin). Any requirement
# row in a section with one of these categories flips the section to
# "needs_response=True".
_RESPONSE_TRIGGERING_CATEGORIES = {
    "mandatory",
    "scored",
    "certification",
    "form",
    "signature",
    "approval",
}


def _auto_map_to_rubric(section_id: str) -> str:
    """Heuristic fallback: map an extracted RFP section_id to a rubric section_id.

    This is a seed mapping that the user can override via
    PUT /workbench/section-rubric-map. Pattern-based because the NJ T1628
    rubric has only 5 criteria but the RFP has ~2,500 extracted sections.
    """
    sid = (section_id or "").strip().lower()
    if not sid:
        return "technicalApproach"
    # Section numbers starting with specific prefixes in the NJ T1628 RFP map
    # to known rubric criteria. These are defaults only — users override
    # per-section via the UI.
    if sid.startswith(("1.", "2.", "cover", "toc")):
        return "mandatoryElements"
    if sid.startswith(("3.", "4.", "subcontractor")):
        return "personnel"
    if sid.startswith(("5.", "past", "experience", "reference")):
        return "experience"
    if "disclosure" in sid or "source" in sid:
        return "sourceDisclosure"
    return "technicalApproach"


def _resolve_rubric_section(
    db: Session, proposal_id: int, rfp_section_id: str
) -> tuple[str, Optional[ScoringRubricSection]]:
    """Return (rubric_section_id, rubric_section_row) for a given extracted section."""
    mapping = (
        db.query(RfpSectionRubricMap)
        .filter(
            RfpSectionRubricMap.proposal_id == proposal_id,
            RfpSectionRubricMap.rfp_section_id == rfp_section_id,
        )
        .first()
    )
    rubric_sid = mapping.rubric_section_id if mapping else _auto_map_to_rubric(rfp_section_id)

    rubric = db.query(ScoringRubric).filter(ScoringRubric.is_default == True).first()
    rubric_row = None
    if rubric:
        rubric_row = (
            db.query(ScoringRubricSection)
            .filter(
                ScoringRubricSection.rubric_id == rubric.id,
                ScoringRubricSection.section_id == rubric_sid,
            )
            .first()
        )
    return rubric_sid, rubric_row


def _section_needs_response(reqs: List[RfpRequirement]) -> bool:
    """True if any requirement in the section has a response-triggering category."""
    for r in reqs:
        cat = (r.category or "").strip().lower()
        if cat in _RESPONSE_TRIGGERING_CATEGORIES:
            return True
        # Also treat anything classified as a proposal_obligation or
        # sf_form requirement_class as needing a response.
        rc = (r.requirement_class or "").strip().lower()
        if rc in ("proposal_obligation", "sf_form", "technical_spec"):
            return True
    return False


# ── Schemas ──────────────────────────────────────────────────────────


class ParsonsResponseIn(BaseModel):
    content: str
    compliance_tags: Optional[dict] = None  # {requirement_id: "Comply"|...}
    status: Optional[str] = None  # draft|in_review|approved|exported


class CompetitorResponseIn(BaseModel):
    competitor_id: int
    content: str
    status: Optional[str] = None


class RubricMapIn(BaseModel):
    rubric_section_id: str
    source: Optional[str] = "manual"
    confidence: Optional[str] = None
    notes: Optional[str] = None


class CureSuggestionDecision(BaseModel):
    decision: str  # "approve" | "deny"


class CureSuggestionGenerateIn(BaseModel):
    competitor_id: Optional[int] = None  # which competitor gap to close; None = overall
    include_rewrite: bool = False  # if True, also ask the LLM for a full suggested_rewrite


class CureApplyIn(BaseModel):
    use_rewrite: bool = False  # if True, replace draft with suggested_rewrite; else append suggestion_text as an addendum
    rescore: bool = True  # re-run the scorer after applying and store rescore_delta


class RewriteIn(BaseModel):
    competitor_id: Optional[int] = None  # optional: compete against this competitor's response
    instructions: Optional[str] = None  # freeform user guidance


# ── GET /workbench/sections — tree of sections for a proposal ────────


@router.get("/sections")
def list_sections(
    proposal_id: int = Query(..., description="Required"),
    include_informational: bool = Query(
        False,
        description="If true, include sections whose requirements are all informational/admin.",
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Section tree for the Response Workbench.

    For every extracted RFP section that has at least one requirement in this
    proposal, returns a summary with:

      * total requirements + category/priority/compliance breakdowns
      * whether the section needs a response (has any mandatory/scored/form item)
      * the rubric section this rolls up to (+ weight_points)
      * response status per author (parsons / each competitor with a response)
      * latest score per scorer

    Use this to render the left-hand section tree in the workbench.
    """
    proposal = db.query(Proposal).filter(Proposal.id == proposal_id).first()
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")

    reqs = (
        db.query(RfpRequirement)
        .filter(RfpRequirement.proposal_id == proposal_id)
        .order_by(RfpRequirement.document_id, RfpRequirement.requirement_id)
        .all()
    )

    # Bucket requirements by section_id
    UNSECTIONED = "(Unsectioned)"
    by_section: dict[str, List[RfpRequirement]] = {}
    for r in reqs:
        sid = (r.section_id or "").strip() or UNSECTIONED
        by_section.setdefault(sid, []).append(r)

    # Batch-load all responses & scores for this proposal
    responses = (
        db.query(RfpSectionResponse)
        .filter(RfpSectionResponse.proposal_id == proposal_id)
        .all()
    )
    resp_by_section: dict[str, list[RfpSectionResponse]] = {}
    for r in responses:
        resp_by_section.setdefault(r.rfp_section_id, []).append(r)

    scores = (
        db.query(SectionScore)
        .filter(SectionScore.proposal_id == proposal_id)
        .all()
    )
    scores_by_section: dict[str, list[SectionScore]] = {}
    for s in scores:
        scores_by_section.setdefault(s.section_id, []).append(s)

    # Preload rubric row lookups + mappings
    rubric = db.query(ScoringRubric).filter(ScoringRubric.is_default == True).first()
    rubric_sections = {}
    if rubric:
        for rs in db.query(ScoringRubricSection).filter(
            ScoringRubricSection.rubric_id == rubric.id
        ).all():
            rubric_sections[rs.section_id] = rs

    mappings = {
        m.rfp_section_id: m.rubric_section_id
        for m in db.query(RfpSectionRubricMap).filter(
            RfpSectionRubricMap.proposal_id == proposal_id
        ).all()
    }

    # Competitor name lookup
    comp_names = {c.id: c.name for c in db.query(Competitor).all()}

    out_sections = []
    total_needs_response = 0
    total_has_parsons_draft = 0
    total_sections = 0
    total_requirements = 0

    for sid, rs in by_section.items():
        total_sections += 1
        total_requirements += len(rs)

        needs_response = _section_needs_response(rs) if sid != UNSECTIONED else False
        if needs_response:
            total_needs_response += 1

        if not needs_response and not include_informational:
            # Still include it in the response, but marked so the UI can
            # collapse/hide informational sections by default.
            pass

        # Rubric rollup
        rubric_sid = mappings.get(sid) or _auto_map_to_rubric(sid)
        rubric_row = rubric_sections.get(rubric_sid)

        # Responses
        sec_responses = resp_by_section.get(sid, [])
        parsons_response = next(
            (r for r in sec_responses if r.author_type == "parsons"), None
        )
        competitor_responses = [
            {
                "author_id": r.author_id,
                "competitor_name": comp_names.get(r.author_id) if r.author_id else None,
                "status": r.status,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                "char_count": len(r.content or ""),
            }
            for r in sec_responses
            if r.author_type == "competitor"
        ]
        if parsons_response and (parsons_response.content or "").strip():
            total_has_parsons_draft += 1

        # Scores
        sec_scores = scores_by_section.get(sid, [])
        parsons_score = next(
            (s.score for s in sec_scores if s.scorer_type == "parsons"), None
        )
        competitor_scores = [
            {
                "competitor_name": s.scorer_name,
                "score": s.score,
                "scored_at": s.scored_at.isoformat() if s.scored_at else None,
            }
            for s in sec_scores
            if s.scorer_type == "competitor"
        ]

        # Also include CompetitorPrediction existence for each known
        # competitor so the UI can show "prediction available" vs not.
        predictions = (
            db.query(CompetitorPrediction)
            .filter(CompetitorPrediction.section_id == sid)
            .all()
        )
        predictions_out = [
            {
                "competitor_id": p.competitor_id,
                "competitor_name": comp_names.get(p.competitor_id),
                "has_content": bool((p.predicted_response or "").strip()),
                "confidence_score": p.confidence_score,
                "updated_at": p.updated_at.isoformat() if p.updated_at else None,
            }
            for p in predictions
        ]

        # Category breakdown (same keys as knowledge router for UI consistency)
        by_category: dict[str, int] = {}
        by_compliance: dict[str, int] = {}
        for r in rs:
            c = (r.category or "unknown")
            by_category[c] = by_category.get(c, 0) + 1
            cs = r.compliance_status or "not_reviewed"
            by_compliance[cs] = by_compliance.get(cs, 0) + 1

        out_sections.append({
            "section_id": sid,
            "total_requirements": len(rs),
            "needs_response": needs_response,
            "by_category": by_category,
            "by_compliance": by_compliance,
            "rubric_section_id": rubric_sid,
            "rubric_title": rubric_row.title if rubric_row else None,
            "rubric_weight_points": rubric_row.weight_points if rubric_row else 0,
            "rubric_pass_fail": rubric_row.pass_fail if rubric_row else False,
            "parsons_response_status": parsons_response.status if parsons_response else None,
            "parsons_response_char_count": (
                len(parsons_response.content or "") if parsons_response else 0
            ),
            "parsons_response_updated_at": (
                parsons_response.updated_at.isoformat()
                if parsons_response and parsons_response.updated_at
                else None
            ),
            "parsons_score": parsons_score,
            "competitor_responses": competitor_responses,
            "competitor_scores": competitor_scores,
            "competitor_predictions": predictions_out,
        })

    # Filter informational if requested
    if not include_informational:
        out_sections = [
            s for s in out_sections if s["needs_response"] or s["parsons_response_status"]
        ]

    out_sections.sort(
        key=lambda s: (s["section_id"] == UNSECTIONED, _section_sort_key(s["section_id"]))
    )

    return {
        "proposal_id": proposal_id,
        "total_sections": total_sections,
        "total_requirements": total_requirements,
        "sections_needing_response": total_needs_response,
        "sections_with_parsons_draft": total_has_parsons_draft,
        "returned_sections": len(out_sections),
        "include_informational": include_informational,
        "sections": out_sections,
    }


# ── GET /workbench/sections/{section_id} — one section in detail ─────


@router.get("/sections/{rfp_section_id}")
def get_section(
    rfp_section_id: str,
    proposal_id: int = Query(..., description="Required"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Everything the workbench needs to render the right-hand panel for one section."""
    proposal = db.query(Proposal).filter(Proposal.id == proposal_id).first()
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")

    reqs = (
        db.query(RfpRequirement)
        .filter(
            RfpRequirement.proposal_id == proposal_id,
            RfpRequirement.section_id == rfp_section_id,
        )
        .order_by(RfpRequirement.requirement_id)
        .all()
    )
    if not reqs and rfp_section_id != "(Unsectioned)":
        # Still allow — user might be initialising a response before extraction
        logger.info(
            f"get_section: no requirements for proposal={proposal_id} section={rfp_section_id}"
        )

    doc_ids = {r.document_id for r in reqs if r.document_id is not None}
    docs = (
        db.query(IngestedDocument)
        .filter(IngestedDocument.id.in_(doc_ids))
        .all()
        if doc_ids
        else []
    )
    doc_name = {d.id: (d.original_filename or d.filename) for d in docs}

    # Rubric rollup
    rubric_sid, rubric_row = _resolve_rubric_section(db, proposal_id, rfp_section_id)

    # Parsons response
    parsons_response = (
        db.query(RfpSectionResponse)
        .filter(
            RfpSectionResponse.proposal_id == proposal_id,
            RfpSectionResponse.rfp_section_id == rfp_section_id,
            RfpSectionResponse.author_type == "parsons",
        )
        .first()
    )

    # Competitor responses stored in the workbench table
    workbench_comp_responses = (
        db.query(RfpSectionResponse)
        .filter(
            RfpSectionResponse.proposal_id == proposal_id,
            RfpSectionResponse.rfp_section_id == rfp_section_id,
            RfpSectionResponse.author_type == "competitor",
        )
        .all()
    )

    # Competitor predictions (the upstream "what would they write" data)
    predictions = (
        db.query(CompetitorPrediction)
        .filter(CompetitorPrediction.section_id == rfp_section_id)
        .all()
    )

    comp_by_id = {c.id: c for c in db.query(Competitor).all()}

    def _wbench_competitor_out(r: RfpSectionResponse) -> dict:
        comp = comp_by_id.get(r.author_id) if r.author_id else None
        return {
            "id": r.id,
            "competitor_id": r.author_id,
            "competitor_name": comp.name if comp else None,
            "content": r.content,
            "status": r.status,
            "version": r.version,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }

    def _prediction_out(p: CompetitorPrediction) -> dict:
        comp = comp_by_id.get(p.competitor_id)
        return {
            "id": p.id,
            "competitor_id": p.competitor_id,
            "competitor_name": comp.name if comp else None,
            "predicted_response": p.predicted_response,
            "reasoning": p.reasoning,
            "confidence_score": p.confidence_score,
            "model_used": p.model_used,
            "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        }

    # Scores
    parsons_scores = (
        db.query(SectionScore)
        .filter(
            SectionScore.proposal_id == proposal_id,
            SectionScore.section_id == rfp_section_id,
            SectionScore.scorer_type == "parsons",
        )
        .order_by(SectionScore.scored_at.desc())
        .all()
    )
    competitor_scores = (
        db.query(SectionScore)
        .filter(
            SectionScore.proposal_id == proposal_id,
            SectionScore.section_id == rfp_section_id,
            SectionScore.scorer_type == "competitor",
        )
        .order_by(SectionScore.scored_at.desc())
        .all()
    )

    def _score_out(s: SectionScore) -> dict:
        return {
            "id": s.id,
            "scorer_type": s.scorer_type,
            "scorer_name": s.scorer_name,
            "score": s.score,
            "rationale": s.rationale,
            "scored_at": s.scored_at.isoformat() if s.scored_at else None,
        }

    # Cure suggestions
    cures = (
        db.query(SectionCureSuggestion)
        .filter(
            SectionCureSuggestion.proposal_id == proposal_id,
            SectionCureSuggestion.rfp_section_id == rfp_section_id,
        )
        .order_by(SectionCureSuggestion.created_at.desc())
        .all()
    )

    def _cure_out(c: SectionCureSuggestion) -> dict:
        return {
            "id": c.id,
            "competitor_id": c.competitor_id,
            "competitor_name": (
                comp_by_id[c.competitor_id].name
                if c.competitor_id in comp_by_id
                else None
            ),
            "based_on_section_score_id": c.based_on_section_score_id,
            "suggestion_text": c.suggestion_text,
            "has_rewrite": bool(c.suggested_rewrite),
            "status": c.status,
            "approved_at": c.approved_at.isoformat() if c.approved_at else None,
            "applied_response_version": c.applied_response_version,
            "rescore_delta": c.rescore_delta,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }

    return {
        "proposal_id": proposal_id,
        "rfp_section_id": rfp_section_id,
        "rubric_section_id": rubric_sid,
        "rubric_title": rubric_row.title if rubric_row else None,
        "rubric_weight_points": rubric_row.weight_points if rubric_row else 0,
        "rubric_pass_fail": rubric_row.pass_fail if rubric_row else False,
        "needs_response": _section_needs_response(reqs),
        "requirements": [
            {
                "id": r.id,
                "requirement_id": r.requirement_id,
                "document_id": r.document_id,
                "document_name": doc_name.get(r.document_id),
                "source_page": r.source_page,
                "category": r.category,
                "priority": r.priority,
                "title": r.title,
                "description": r.description,
                "source_text": r.source_text,
                "compliance_status": r.compliance_status,
                "requirement_class": r.requirement_class,
                "verified": r.verified,
            }
            for r in reqs
        ],
        "parsons_response": (
            {
                "id": parsons_response.id,
                "content": parsons_response.content,
                "compliance_tags": (
                    json.loads(parsons_response.compliance_tags_json)
                    if parsons_response.compliance_tags_json
                    else {}
                ),
                "status": parsons_response.status,
                "version": parsons_response.version,
                "updated_at": (
                    parsons_response.updated_at.isoformat()
                    if parsons_response.updated_at
                    else None
                ),
            }
            if parsons_response
            else None
        ),
        "workbench_competitor_responses": [
            _wbench_competitor_out(r) for r in workbench_comp_responses
        ],
        "competitor_predictions": [_prediction_out(p) for p in predictions],
        "parsons_scores": [_score_out(s) for s in parsons_scores],
        "competitor_scores": [_score_out(s) for s in competitor_scores],
        "cure_suggestions": [_cure_out(c) for c in cures],
    }


# ── PUT /workbench/sections/{section_id}/parsons-response ────────────


@router.put("/sections/{rfp_section_id}/parsons-response")
def upsert_parsons_response(
    rfp_section_id: str,
    payload: ParsonsResponseIn,
    proposal_id: int = Query(..., description="Required"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create or update the Parsons narrative for this section.

    Version bumps in place on every save (so history can be reconstructed
    from audit logs if ever needed). Status defaults to "draft".
    """
    proposal = db.query(Proposal).filter(Proposal.id == proposal_id).first()
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")

    existing = (
        db.query(RfpSectionResponse)
        .filter(
            RfpSectionResponse.proposal_id == proposal_id,
            RfpSectionResponse.rfp_section_id == rfp_section_id,
            RfpSectionResponse.author_type == "parsons",
        )
        .first()
    )
    compliance_json = (
        json.dumps(payload.compliance_tags)
        if payload.compliance_tags is not None
        else None
    )
    if existing:
        existing.content = payload.content
        if compliance_json is not None:
            existing.compliance_tags_json = compliance_json
        if payload.status:
            existing.status = payload.status
        existing.version = (existing.version or 1) + 1
        existing.last_edited_by = current_user.id
        existing.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(existing)
        row = existing
    else:
        row = RfpSectionResponse(
            proposal_id=proposal_id,
            rfp_section_id=rfp_section_id,
            author_type="parsons",
            author_id=None,
            content=payload.content,
            compliance_tags_json=compliance_json,
            status=payload.status or "draft",
            version=1,
            last_edited_by=current_user.id,
        )
        db.add(row)
        db.commit()
        db.refresh(row)

    return {
        "id": row.id,
        "version": row.version,
        "status": row.status,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


# ── PUT /workbench/sections/{section_id}/competitor-response ─────────


@router.put("/sections/{rfp_section_id}/competitor-response")
def upsert_competitor_response(
    rfp_section_id: str,
    payload: CompetitorResponseIn,
    proposal_id: int = Query(..., description="Required"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Persist an edited/refined competitor response.

    Use this when the user has accepted or refined the auto-generated
    CompetitorPrediction and wants a stable workbench copy to score against.
    Initial population: POST /generate-competitor-response runs the
    predictor and stores the output here.
    """
    proposal = db.query(Proposal).filter(Proposal.id == proposal_id).first()
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")
    comp = db.query(Competitor).filter(Competitor.id == payload.competitor_id).first()
    if not comp:
        raise HTTPException(status_code=404, detail="Competitor not found")

    existing = (
        db.query(RfpSectionResponse)
        .filter(
            RfpSectionResponse.proposal_id == proposal_id,
            RfpSectionResponse.rfp_section_id == rfp_section_id,
            RfpSectionResponse.author_type == "competitor",
            RfpSectionResponse.author_id == payload.competitor_id,
        )
        .first()
    )
    if existing:
        existing.content = payload.content
        if payload.status:
            existing.status = payload.status
        existing.version = (existing.version or 1) + 1
        existing.last_edited_by = current_user.id
        existing.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(existing)
        row = existing
    else:
        row = RfpSectionResponse(
            proposal_id=proposal_id,
            rfp_section_id=rfp_section_id,
            author_type="competitor",
            author_id=payload.competitor_id,
            content=payload.content,
            status=payload.status or "draft",
            version=1,
            last_edited_by=current_user.id,
        )
        db.add(row)
        db.commit()
        db.refresh(row)

    return {
        "id": row.id,
        "competitor_id": payload.competitor_id,
        "version": row.version,
        "status": row.status,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


# ── POST /workbench/sections/{section_id}/generate-competitor-response ─


@router.post("/sections/{rfp_section_id}/generate-competitor-response")
def generate_competitor_response(
    rfp_section_id: str,
    competitor_id: int = Query(..., description="Which competitor to generate for"),
    proposal_id: int = Query(..., description="Required"),
    persist_to_workbench: bool = Query(
        True,
        description="If true, also copy the generated text into the workbench competitor-response store",
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Run the existing competitor-persona predictor for this section.

    Reuses `services.competitor_analyst.predict_competitor_response` so the
    same persona + prompt logic that powers the existing intelligence page
    is used here too. Returns the upstream CompetitorPrediction row and
    (optionally) also writes a workbench copy.
    """
    from ..services.competitor_analyst import predict_competitor_response

    proposal = db.query(Proposal).filter(Proposal.id == proposal_id).first()
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")
    comp = db.query(Competitor).filter(Competitor.id == competitor_id).first()
    if not comp:
        raise HTTPException(status_code=404, detail="Competitor not found")

    try:
        pred = predict_competitor_response(db, competitor_id, rfp_section_id, proposal_id)
    except Exception as e:
        logger.exception("predict_competitor_response failed")
        raise HTTPException(status_code=500, detail=f"Generation failed: {e}")
    if not pred:
        raise HTTPException(
            status_code=500, detail="Predictor returned no result"
        )

    if persist_to_workbench and pred.predicted_response:
        existing = (
            db.query(RfpSectionResponse)
            .filter(
                RfpSectionResponse.proposal_id == proposal_id,
                RfpSectionResponse.rfp_section_id == rfp_section_id,
                RfpSectionResponse.author_type == "competitor",
                RfpSectionResponse.author_id == competitor_id,
            )
            .first()
        )
        if existing:
            existing.content = pred.predicted_response
            existing.version = (existing.version or 1) + 1
            existing.updated_at = datetime.utcnow()
        else:
            db.add(
                RfpSectionResponse(
                    proposal_id=proposal_id,
                    rfp_section_id=rfp_section_id,
                    author_type="competitor",
                    author_id=competitor_id,
                    content=pred.predicted_response,
                    status="draft",
                    version=1,
                )
            )
        db.commit()

    return {
        "competitor_id": competitor_id,
        "competitor_name": comp.name,
        "prediction_id": pred.id,
        "char_count": len(pred.predicted_response or ""),
        "reasoning": pred.reasoning,
        "confidence_score": pred.confidence_score,
    }


# ── POST /workbench/sections/{section_id}/score ──────────────────────


@router.post("/sections/{rfp_section_id}/score")
def score_section(
    rfp_section_id: str,
    proposal_id: int = Query(..., description="Required"),
    score_parsons: bool = Query(True),
    competitor_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Score Parsons and/or one competitor on this extracted section.

    Thin wrapper around `services.response_scorer.score_section_response`
    that pulls content from the workbench's own response tables (falling
    back to the existing `CompetitorPrediction` row if no workbench copy
    exists yet).
    """
    from ..services.response_scorer import score_section_response

    proposal = db.query(Proposal).filter(Proposal.id == proposal_id).first()
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")

    # Resolve the rubric rollup once for this extracted section
    rubric_sid, _ = _resolve_rubric_section(db, proposal_id, rfp_section_id)

    results = []

    # ── Parsons ──────────────────────────────────────────────────────
    if score_parsons:
        ps = (
            db.query(RfpSectionResponse)
            .filter(
                RfpSectionResponse.proposal_id == proposal_id,
                RfpSectionResponse.rfp_section_id == rfp_section_id,
                RfpSectionResponse.author_type == "parsons",
            )
            .first()
        )
        parsons_text = ps.content if ps else ""
        ps_score = score_section_response(
            db=db,
            proposal_id=proposal_id,
            section_id=rfp_section_id,
            scorer_type="parsons",
            scorer_name=None,
            response_text=parsons_text,
            rubric_section_id=rubric_sid,
        )
        if ps_score:
            results.append(
                {
                    "scorer_type": "parsons",
                    "scorer_name": None,
                    "score": ps_score.score,
                    "rationale": ps_score.rationale,
                }
            )

    # ── Competitor ───────────────────────────────────────────────────
    if competitor_id:
        comp = db.query(Competitor).filter(Competitor.id == competitor_id).first()
        if not comp:
            raise HTTPException(status_code=404, detail="Competitor not found")

        # Prefer workbench-stored competitor response; fall back to prediction
        wb = (
            db.query(RfpSectionResponse)
            .filter(
                RfpSectionResponse.proposal_id == proposal_id,
                RfpSectionResponse.rfp_section_id == rfp_section_id,
                RfpSectionResponse.author_type == "competitor",
                RfpSectionResponse.author_id == competitor_id,
            )
            .first()
        )
        competitor_text = wb.content if wb else None
        if not competitor_text:
            pred = (
                db.query(CompetitorPrediction)
                .filter(
                    CompetitorPrediction.competitor_id == competitor_id,
                    CompetitorPrediction.section_id == rfp_section_id,
                )
                .first()
            )
            competitor_text = pred.predicted_response if pred else ""

        cs_score = score_section_response(
            db=db,
            proposal_id=proposal_id,
            section_id=rfp_section_id,
            scorer_type="competitor",
            scorer_name=comp.name,
            response_text=competitor_text,
            rubric_section_id=rubric_sid,
        )
        if cs_score:
            results.append(
                {
                    "scorer_type": "competitor",
                    "scorer_name": comp.name,
                    "score": cs_score.score,
                    "rationale": cs_score.rationale,
                }
            )

    return {
        "proposal_id": proposal_id,
        "rfp_section_id": rfp_section_id,
        "scores": results,
    }


# ── Rubric map CRUD ──────────────────────────────────────────────────


@router.get("/section-rubric-map")
def list_rubric_map(
    proposal_id: int = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all rfp_section → rubric_section mappings for a proposal."""
    rows = (
        db.query(RfpSectionRubricMap)
        .filter(RfpSectionRubricMap.proposal_id == proposal_id)
        .order_by(RfpSectionRubricMap.rfp_section_id)
        .all()
    )
    return {
        "proposal_id": proposal_id,
        "mappings": [
            {
                "id": r.id,
                "rfp_section_id": r.rfp_section_id,
                "rubric_section_id": r.rubric_section_id,
                "source": r.source,
                "confidence": r.confidence,
                "notes": r.notes,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rows
        ],
    }


@router.put("/section-rubric-map/{rfp_section_id}")
def upsert_rubric_map(
    rfp_section_id: str,
    payload: RubricMapIn,
    proposal_id: int = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Override (or create) the rubric rollup mapping for an extracted section."""
    existing = (
        db.query(RfpSectionRubricMap)
        .filter(
            RfpSectionRubricMap.proposal_id == proposal_id,
            RfpSectionRubricMap.rfp_section_id == rfp_section_id,
        )
        .first()
    )
    if existing:
        existing.rubric_section_id = payload.rubric_section_id
        existing.source = payload.source or existing.source or "manual"
        if payload.confidence is not None:
            existing.confidence = payload.confidence
        if payload.notes is not None:
            existing.notes = payload.notes
        existing.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(existing)
        row = existing
    else:
        row = RfpSectionRubricMap(
            proposal_id=proposal_id,
            rfp_section_id=rfp_section_id,
            rubric_section_id=payload.rubric_section_id,
            source=payload.source or "manual",
            confidence=payload.confidence,
            notes=payload.notes,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
    return {
        "id": row.id,
        "rfp_section_id": row.rfp_section_id,
        "rubric_section_id": row.rubric_section_id,
        "source": row.source,
    }


# ── Cure suggestion stubs (generation + approve/deny to be wired next) ─


@router.get("/sections/{rfp_section_id}/cure-suggestions")
def list_cure_suggestions(
    rfp_section_id: str,
    proposal_id: int = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = (
        db.query(SectionCureSuggestion)
        .filter(
            SectionCureSuggestion.proposal_id == proposal_id,
            SectionCureSuggestion.rfp_section_id == rfp_section_id,
        )
        .order_by(SectionCureSuggestion.created_at.desc())
        .all()
    )
    comp_names = {c.id: c.name for c in db.query(Competitor).all()}
    return {
        "proposal_id": proposal_id,
        "rfp_section_id": rfp_section_id,
        "suggestions": [
            {
                "id": r.id,
                "competitor_id": r.competitor_id,
                "competitor_name": comp_names.get(r.competitor_id),
                "suggestion_text": r.suggestion_text,
                "has_rewrite": bool(r.suggested_rewrite),
                "status": r.status,
                "approved_at": r.approved_at.isoformat() if r.approved_at else None,
                "applied_response_version": r.applied_response_version,
                "rescore_delta": r.rescore_delta,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }


@router.put("/cure-suggestions/{suggestion_id}/decision")
def decide_cure_suggestion(
    suggestion_id: int,
    payload: CureSuggestionDecision,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Approve or deny a cure suggestion. Application + rescore is a separate step."""
    row = (
        db.query(SectionCureSuggestion)
        .filter(SectionCureSuggestion.id == suggestion_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    decision = (payload.decision or "").strip().lower()
    if decision not in ("approve", "deny"):
        raise HTTPException(
            status_code=400, detail="decision must be 'approve' or 'deny'"
        )
    row.status = "approved" if decision == "approve" else "denied"
    row.approved_by = current_user.id
    row.approved_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return {
        "id": row.id,
        "status": row.status,
        "approved_by": row.approved_by,
        "approved_at": row.approved_at.isoformat() if row.approved_at else None,
    }


# ── LLM helpers for cure + rewrite ───────────────────────────────────


_DEFAULT_CURE_SYSTEM = (
    "You are the Cure Advisor on the Parsons capture team for the NJ Treasury T1628 "
    "Enhanced Motor Vehicle Inspection pursuit. Your job is adversarial: assume the "
    "competitor you are scoring against could realistically outscore Parsons on the "
    "section you are reviewing, and propose the SPECIFIC moves that flip the outcome.\n\n"
    "Your suggestions must be concrete, evidence-backed, and directly tied to the RFP's "
    "stated scoring criteria. Cite requirement IDs. Quote specific sentences from the "
    "current Parsons draft and state EXACTLY what to add, replace, or remove. Suggest "
    "real Parsons capabilities (technologies, methodologies, past performance) when the "
    "supplied context supports it — never invent claims, never paper over compliance "
    "risk. Differentiation, verbose context, quantified outcomes — those win sections. "
    "Compliance boilerplate ('Parsons will comply') loses sections."
)

_DEFAULT_REWRITE_SYSTEM = (
    "You are a senior Parsons proposal writer on the NJ Treasury T1628 Enhanced Motor "
    "Vehicle Inspection pursuit. Produce a polished, evaluator-ready narrative for one "
    "RFP section that BEATS the modeled competitor response on every rubric criterion. "
    "Differentiation > compliance: surface Parsons's actual capabilities (named systems, "
    "past performance with quantified outcomes, named methodologies, key personnel "
    "disciplines). Address every mandatory requirement with substance, not boilerplate. "
    "Match the RFP's required section structure. Never invent capabilities Parsons does "
    "not actually have, but always present what they DO have in the most evaluator-"
    "favorable framing supported by evidence."
)


def _load_persona_prompt(db: Session, persona_type: str, fallback: str) -> str:
    """Return the active Persona.system_prompt for a given persona_type, or the fallback."""
    p = (
        db.query(Persona)
        .filter(Persona.persona_type == persona_type, Persona.is_active == True)
        .order_by(Persona.id)
        .first()
    )
    if p and (p.system_prompt or "").strip():
        return p.system_prompt
    return fallback


# ── POST /workbench/sections/{sid}/cure-suggestion ───────────────────


@router.post("/sections/{rfp_section_id}/cure-suggestion")
def generate_cure_suggestion(
    rfp_section_id: str,
    payload: CureSuggestionGenerateIn,
    proposal_id: int = Query(..., description="Required"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Generate a cure suggestion via the Cure Advisor persona.

    Pulls the latest Parsons SectionScore, the optional competitor's
    SectionScore + response, and the current Parsons draft. Stores the
    LLM output as a proposed SectionCureSuggestion row that the user
    can then approve/deny and optionally apply.
    """
    from ..services.competitor_analyst import _call_ai, get_last_ai_error

    proposal = db.query(Proposal).filter(Proposal.id == proposal_id).first()
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")

    # Pull Parsons draft
    parsons_resp = (
        db.query(RfpSectionResponse)
        .filter(
            RfpSectionResponse.proposal_id == proposal_id,
            RfpSectionResponse.rfp_section_id == rfp_section_id,
            RfpSectionResponse.author_type == "parsons",
        )
        .first()
    )
    parsons_text = (parsons_resp.content if parsons_resp else "") or ""

    # Pull latest Parsons score
    parsons_score = (
        db.query(SectionScore)
        .filter(
            SectionScore.proposal_id == proposal_id,
            SectionScore.section_id == rfp_section_id,
            SectionScore.scorer_type == "parsons",
        )
        .order_by(SectionScore.scored_at.desc())
        .first()
    )

    # Optional competitor context
    comp = None
    comp_resp_text = ""
    comp_score_row = None
    if payload.competitor_id:
        comp = db.query(Competitor).filter(Competitor.id == payload.competitor_id).first()
        if not comp:
            raise HTTPException(status_code=404, detail="Competitor not found")
        # Workbench copy first, fall back to CompetitorPrediction
        wb = (
            db.query(RfpSectionResponse)
            .filter(
                RfpSectionResponse.proposal_id == proposal_id,
                RfpSectionResponse.rfp_section_id == rfp_section_id,
                RfpSectionResponse.author_type == "competitor",
                RfpSectionResponse.author_id == comp.id,
            )
            .first()
        )
        if wb and wb.content:
            comp_resp_text = wb.content
        else:
            pred = (
                db.query(CompetitorPrediction)
                .filter(
                    CompetitorPrediction.competitor_id == comp.id,
                    CompetitorPrediction.section_id == rfp_section_id,
                )
                .first()
            )
            comp_resp_text = (pred.predicted_response if pred else "") or ""
        comp_score_row = (
            db.query(SectionScore)
            .filter(
                SectionScore.proposal_id == proposal_id,
                SectionScore.section_id == rfp_section_id,
                SectionScore.scorer_type == "competitor",
                SectionScore.scorer_name == comp.name,
            )
            .order_by(SectionScore.scored_at.desc())
            .first()
        )

    # Requirements + rubric rollup for grounding the prompt
    reqs = (
        db.query(RfpRequirement)
        .filter(
            RfpRequirement.proposal_id == proposal_id,
            RfpRequirement.section_id == rfp_section_id,
        )
        .order_by(RfpRequirement.requirement_id)
        .all()
    )
    rubric_sid, rubric_row = _resolve_rubric_section(db, proposal_id, rfp_section_id)

    def _truncate(s: str, n: int = 6000) -> str:
        if not s:
            return ""
        return s if len(s) <= n else (s[: int(n * 0.7)] + "\n\n...[truncated]...\n\n" + s[-int(n * 0.25):])

    req_lines = []
    for r in reqs[:40]:
        req_lines.append(
            f"- [{r.requirement_id}] ({r.category or '?'}/{r.priority or '?'}) {r.title or ''}: "
            f"{(r.description or r.source_text or '').strip()[:400]}"
        )
    requirements_block = "\n".join(req_lines) if req_lines else "(no requirements extracted for this section)"

    parsons_score_block = (
        f"Parsons score: {parsons_score.score}/100\nRationale:\n{parsons_score.rationale or ''}"
        if parsons_score
        else "Parsons score: not yet scored."
    )
    comp_block = ""
    if comp:
        comp_score_line = (
            f"{comp.name} score: {comp_score_row.score}/100" if comp_score_row else f"{comp.name} score: not yet scored."
        )
        comp_block = (
            f"\n\n## Competitor ({comp.name}) reference\n{comp_score_line}\n"
            f"Rationale: {comp_score_row.rationale if comp_score_row else '(none)'}\n"
            f"Competitor response excerpt:\n{_truncate(comp_resp_text, 4000)}"
        )

    rewrite_instructions = (
        "\nAlso produce a full rewritten Parsons response for this section in "
        "`suggested_rewrite`, suitable for direct inclusion in the proposal. "
        "If you cannot responsibly produce one (missing info, compliance risk), "
        "set `suggested_rewrite` to null and explain why in `suggestion_text`."
        if payload.include_rewrite
        else "\nDo NOT produce a full rewrite. Leave `suggested_rewrite` as null."
    )

    system = _load_persona_prompt(db, "cure_advisor", _DEFAULT_CURE_SYSTEM)
    prompt = f"""Analyze the Parsons response for this RFP section and propose a concrete cure.

## Section
- Extracted RFP section: `{rfp_section_id}`
- Rubric rollup: `{rubric_sid}` ({rubric_row.title if rubric_row else 'unknown'}, weight {rubric_row.weight_points if rubric_row else 0} pts, pass_fail={bool(rubric_row.pass_fail) if rubric_row else False})

## Requirements in this section
{requirements_block}

## Current Parsons draft
{_truncate(parsons_text, 6000) if parsons_text else '(no draft yet)'}

## Current Parsons scoring
{parsons_score_block}
{comp_block}
{rewrite_instructions}

## Output — RETURN VALID JSON ONLY (no prose, no code fences)
{{
  "suggestion_text": "<markdown — gap analysis + specific cure instructions; cite requirement IDs; bullet the exact additions/edits Parsons should make>",
  "expected_score_delta": <integer, how many points this cure should gain on the 0-100 scale>,
  "risk_notes": "<short risk callouts: overcommitment, contradiction, compliance risk — or 'None'>",
  "suggested_rewrite": {'"<full rewritten Parsons section narrative>" or null' if payload.include_rewrite else 'null'}
}}"""

    raw = _call_ai(prompt, system=system)

    import re as _re
    data = {}
    if raw:
        cleaned = _re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=_re.IGNORECASE)
        cleaned = _re.sub(r"\s*```$", "", cleaned)
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            m = _re.search(r"\{.*\}", cleaned, flags=_re.DOTALL)
            if m:
                try:
                    data = json.loads(m.group(0))
                except json.JSONDecodeError:
                    data = {}

    if not data:
        err = get_last_ai_error()
        detail = (
            f"Cure LLM call failed: {err.get('provider') or 'no provider'} / "
            f"{err.get('type') or 'unknown'} — {err.get('message') or 'no detail'}"
            if err and err.get("type")
            else "Cure LLM returned no parseable JSON."
        )
        raise HTTPException(status_code=502, detail=detail)

    suggestion_text = (data.get("suggestion_text") or "").strip()
    if not suggestion_text:
        raise HTTPException(status_code=502, detail="Cure LLM returned no suggestion_text")
    suggested_rewrite = data.get("suggested_rewrite")
    if isinstance(suggested_rewrite, str) and not suggested_rewrite.strip():
        suggested_rewrite = None
    expected_delta = data.get("expected_score_delta")
    risk_notes = (data.get("risk_notes") or "").strip() or None

    # Append risk notes / expected delta onto the stored suggestion for UI visibility
    stored_text = suggestion_text
    if expected_delta is not None or risk_notes:
        stored_text += "\n\n---\n"
        if expected_delta is not None:
            stored_text += f"_Expected score delta: **{expected_delta}** points_\n"
        if risk_notes:
            stored_text += f"_Risk notes: {risk_notes}_\n"

    row = SectionCureSuggestion(
        proposal_id=proposal_id,
        rfp_section_id=rfp_section_id,
        based_on_section_score_id=parsons_score.id if parsons_score else None,
        competitor_id=comp.id if comp else None,
        suggestion_text=stored_text,
        suggested_rewrite=suggested_rewrite if isinstance(suggested_rewrite, str) else None,
        status="proposed",
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    return {
        "id": row.id,
        "proposal_id": row.proposal_id,
        "rfp_section_id": row.rfp_section_id,
        "competitor_id": row.competitor_id,
        "competitor_name": comp.name if comp else None,
        "suggestion_text": row.suggestion_text,
        "has_rewrite": bool(row.suggested_rewrite),
        "status": row.status,
        "based_on_section_score_id": row.based_on_section_score_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


# ── POST /workbench/cure-suggestions/{id}/apply ──────────────────────


@router.post("/cure-suggestions/{suggestion_id}/apply")
def apply_cure_suggestion(
    suggestion_id: int,
    payload: CureApplyIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Merge an APPROVED cure suggestion into the Parsons draft and (optionally) rescore.

    Modes:
      - `use_rewrite=False` (default): append the cure suggestion text to the
        Parsons draft as an addendum labelled "Cure applied".
      - `use_rewrite=True`: replace the Parsons draft with `suggested_rewrite`
        (requires the row to have one; generate with include_rewrite=True first).

    Bumps `RfpSectionResponse.version`. If `rescore=True`, reruns the scorer
    against the new draft and writes `rescore_delta` onto the cure row.
    """
    row = db.query(SectionCureSuggestion).filter(SectionCureSuggestion.id == suggestion_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    if row.status != "approved":
        raise HTTPException(
            status_code=400,
            detail=f"Suggestion must be 'approved' before applying (current status: {row.status})",
        )
    if payload.use_rewrite and not (row.suggested_rewrite or "").strip():
        raise HTTPException(
            status_code=400,
            detail="This suggestion has no suggested_rewrite. Generate it with include_rewrite=True first.",
        )

    # Capture prior Parsons score for delta
    prior_score_val = None
    prior_score = (
        db.query(SectionScore)
        .filter(
            SectionScore.proposal_id == row.proposal_id,
            SectionScore.section_id == row.rfp_section_id,
            SectionScore.scorer_type == "parsons",
        )
        .order_by(SectionScore.scored_at.desc())
        .first()
    )
    if prior_score:
        prior_score_val = prior_score.score

    # Merge into Parsons draft
    parsons_resp = (
        db.query(RfpSectionResponse)
        .filter(
            RfpSectionResponse.proposal_id == row.proposal_id,
            RfpSectionResponse.rfp_section_id == row.rfp_section_id,
            RfpSectionResponse.author_type == "parsons",
        )
        .first()
    )

    if payload.use_rewrite:
        new_content = row.suggested_rewrite
    else:
        existing_text = (parsons_resp.content if parsons_resp else "") or ""
        addendum = (
            f"\n\n---\n\n"
            f"**Cure applied ({datetime.utcnow().strftime('%Y-%m-%d')}) — suggestion #{row.id}**\n\n"
            f"{row.suggestion_text}"
        )
        new_content = (existing_text + addendum).strip()

    if parsons_resp:
        parsons_resp.content = new_content
        parsons_resp.version = (parsons_resp.version or 1) + 1
        parsons_resp.last_edited_by = current_user.id
        parsons_resp.updated_at = datetime.utcnow()
        new_version = parsons_resp.version
    else:
        parsons_resp = RfpSectionResponse(
            proposal_id=row.proposal_id,
            rfp_section_id=row.rfp_section_id,
            author_type="parsons",
            author_id=None,
            content=new_content,
            status="draft",
            version=1,
            last_edited_by=current_user.id,
        )
        db.add(parsons_resp)
        db.flush()
        new_version = parsons_resp.version

    # Mark the cure row as applied
    row.status = "applied"
    row.applied_response_version = new_version
    db.commit()
    db.refresh(row)
    db.refresh(parsons_resp)

    # Optional rescore
    new_score_val = None
    rescore_delta = None
    if payload.rescore:
        from ..services.response_scorer import score_section_response

        rubric_sid, _ = _resolve_rubric_section(db, row.proposal_id, row.rfp_section_id)
        new_score_row = score_section_response(
            db=db,
            proposal_id=row.proposal_id,
            section_id=row.rfp_section_id,
            scorer_type="parsons",
            scorer_name=None,
            response_text=parsons_resp.content,
            rubric_section_id=rubric_sid,
        )
        if new_score_row:
            new_score_val = new_score_row.score
            if prior_score_val is not None:
                rescore_delta = new_score_val - prior_score_val
            row.rescore_delta = rescore_delta
            db.commit()
            db.refresh(row)

    return {
        "id": row.id,
        "status": row.status,
        "applied_response_version": row.applied_response_version,
        "parsons_response_id": parsons_resp.id,
        "parsons_response_version": parsons_resp.version,
        "prior_score": prior_score_val,
        "new_score": new_score_val,
        "rescore_delta": rescore_delta,
    }


# ── POST /workbench/sections/{sid}/rewrite ───────────────────────────


@router.post("/sections/{rfp_section_id}/rewrite")
def rewrite_parsons_section(
    rfp_section_id: str,
    payload: RewriteIn,
    proposal_id: int = Query(..., description="Required"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Generate a full Parsons rewrite for this section and stage it as a cure row.

    The rewrite lands in `SectionCureSuggestion.suggested_rewrite` with
    `status='proposed'` so the user reviews it with the same approve/deny
    flow as a cure. Approve + apply(use_rewrite=True) replaces the Parsons
    draft and bumps version.
    """
    from ..services.competitor_analyst import _call_ai, get_last_ai_error

    proposal = db.query(Proposal).filter(Proposal.id == proposal_id).first()
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")

    # Gather context
    reqs = (
        db.query(RfpRequirement)
        .filter(
            RfpRequirement.proposal_id == proposal_id,
            RfpRequirement.section_id == rfp_section_id,
        )
        .order_by(RfpRequirement.requirement_id)
        .all()
    )
    rubric_sid, rubric_row = _resolve_rubric_section(db, proposal_id, rfp_section_id)

    parsons_resp = (
        db.query(RfpSectionResponse)
        .filter(
            RfpSectionResponse.proposal_id == proposal_id,
            RfpSectionResponse.rfp_section_id == rfp_section_id,
            RfpSectionResponse.author_type == "parsons",
        )
        .first()
    )
    parsons_text = (parsons_resp.content if parsons_resp else "") or ""

    comp = None
    comp_text = ""
    if payload.competitor_id:
        comp = db.query(Competitor).filter(Competitor.id == payload.competitor_id).first()
        if not comp:
            raise HTTPException(status_code=404, detail="Competitor not found")
        wb = (
            db.query(RfpSectionResponse)
            .filter(
                RfpSectionResponse.proposal_id == proposal_id,
                RfpSectionResponse.rfp_section_id == rfp_section_id,
                RfpSectionResponse.author_type == "competitor",
                RfpSectionResponse.author_id == comp.id,
            )
            .first()
        )
        if wb and wb.content:
            comp_text = wb.content
        else:
            pred = (
                db.query(CompetitorPrediction)
                .filter(
                    CompetitorPrediction.competitor_id == comp.id,
                    CompetitorPrediction.section_id == rfp_section_id,
                )
                .first()
            )
            comp_text = (pred.predicted_response if pred else "") or ""

    def _truncate(s: str, n: int = 6000) -> str:
        if not s:
            return ""
        return s if len(s) <= n else (s[: int(n * 0.7)] + "\n\n...[truncated]...\n\n" + s[-int(n * 0.25):])

    req_lines = []
    for r in reqs[:40]:
        req_lines.append(
            f"- [{r.requirement_id}] ({r.category or '?'}/{r.priority or '?'}) {r.title or ''}: "
            f"{(r.description or r.source_text or '').strip()[:400]}"
        )
    requirements_block = "\n".join(req_lines) if req_lines else "(no requirements extracted)"

    comp_block = ""
    if comp:
        comp_block = f"\n\n## Competitor benchmark ({comp.name})\n{_truncate(comp_text, 4000) if comp_text else '(no competitor response available)'}\n\nOutscore this response on specificity, evidence, and commitments."

    user_guidance = f"\n\n## User guidance\n{payload.instructions.strip()}" if payload.instructions else ""

    system = _load_persona_prompt(db, "parsons_writer", _DEFAULT_REWRITE_SYSTEM)
    prompt = f"""Write a full Parsons response for this RFP section. The output will be reviewed,
approved, then merged into the proposal as version N+1.

## Section
- Extracted RFP section: `{rfp_section_id}`
- Rubric rollup: `{rubric_sid}` ({rubric_row.title if rubric_row else 'unknown'}, weight {rubric_row.weight_points if rubric_row else 0} pts)

## Requirements to address
{requirements_block}

## Current Parsons draft (to improve upon)
{_truncate(parsons_text, 6000) if parsons_text else '(no existing draft)'}
{comp_block}{user_guidance}

## Output — RETURN VALID JSON ONLY
{{
  "rewrite": "<full markdown narrative addressing every requirement above; structured with headings matching RFP conventions; specific, evidence-backed, no generic boilerplate>",
  "summary": "<one-sentence description of what changed vs. the current draft>",
  "open_questions": ["<any unresolved item that needs SME input before this can be approved>", ...]
}}"""

    raw = _call_ai(prompt, system=system)

    import re as _re
    data = {}
    if raw:
        cleaned = _re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=_re.IGNORECASE)
        cleaned = _re.sub(r"\s*```$", "", cleaned)
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            m = _re.search(r"\{.*\}", cleaned, flags=_re.DOTALL)
            if m:
                try:
                    data = json.loads(m.group(0))
                except json.JSONDecodeError:
                    data = {}

    if not data:
        err = get_last_ai_error()
        detail = (
            f"Rewrite LLM call failed: {err.get('provider') or 'no provider'} / "
            f"{err.get('type') or 'unknown'} — {err.get('message') or 'no detail'}"
            if err and err.get("type")
            else "Rewrite LLM returned no parseable JSON."
        )
        raise HTTPException(status_code=502, detail=detail)

    rewrite_text = (data.get("rewrite") or "").strip()
    if not rewrite_text:
        raise HTTPException(status_code=502, detail="Rewrite LLM returned empty rewrite")
    summary = (data.get("summary") or "").strip()
    open_qs = data.get("open_questions") or []
    if not isinstance(open_qs, list):
        open_qs = []

    suggestion_text_parts = [
        "**Full rewrite proposed.**",
        f"Summary: {summary}" if summary else "",
    ]
    if open_qs:
        suggestion_text_parts.append(
            "Open questions for SME review:\n"
            + "\n".join(f"- {str(q).strip()}" for q in open_qs if str(q).strip())
        )
    suggestion_text = "\n\n".join(p for p in suggestion_text_parts if p)

    # Pull latest Parsons score to anchor `based_on_section_score_id`
    parsons_score = (
        db.query(SectionScore)
        .filter(
            SectionScore.proposal_id == proposal_id,
            SectionScore.section_id == rfp_section_id,
            SectionScore.scorer_type == "parsons",
        )
        .order_by(SectionScore.scored_at.desc())
        .first()
    )

    row = SectionCureSuggestion(
        proposal_id=proposal_id,
        rfp_section_id=rfp_section_id,
        based_on_section_score_id=parsons_score.id if parsons_score else None,
        competitor_id=comp.id if comp else None,
        suggestion_text=suggestion_text or "Full rewrite proposed.",
        suggested_rewrite=rewrite_text,
        status="proposed",
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    return {
        "id": row.id,
        "proposal_id": row.proposal_id,
        "rfp_section_id": row.rfp_section_id,
        "competitor_id": row.competitor_id,
        "suggestion_text": row.suggestion_text,
        "has_rewrite": True,
        "rewrite_char_count": len(rewrite_text),
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }

# ─────────────────────────────────────────────────────────────────────────────
# Exports
#
# Three export formats, all keyed to the extracted RFP section tree:
#   * Excel   (/export.xlsx)  — one row per section (compliance-matrix style)
#   * Word    (/export.docx)  — RFP-volume style narrative document
#   * PDF     (/export.pdf)   — same volume-style layout, rendered via reportlab
#
# Each export only includes sections that need a response (same filter the
# workbench UI uses). If `include_informational=true` the full tree is used.
# ─────────────────────────────────────────────────────────────────────────────


def _gather_export_rows(
    db: Session,
    proposal_id: int,
    include_informational: bool = False,
):
    """Build the section-by-section payload used by every export format."""
    rubric_sections = {
        rs.section_id: rs
        for rs in db.query(ScoringRubricSection)
        .join(ScoringRubric, ScoringRubric.id == ScoringRubricSection.rubric_id)
        .filter(ScoringRubric.is_active == True)  # noqa: E712
        .all()
    }
    total_points = sum(
        (rs.weight_points or 0) for rs in rubric_sections.values() if not rs.is_pass_fail
    ) or 1

    requirements = (
        db.query(RfpRequirement)
        .filter(RfpRequirement.proposal_id == proposal_id)
        .order_by(RfpRequirement.source_section, RfpRequirement.id)
        .all()
    )
    by_section: dict = {}
    for r in requirements:
        sid = (r.source_section or "UNASSIGNED").strip()
        by_section.setdefault(sid, []).append(r)

    maps = {
        m.rfp_section_id: m
        for m in db.query(RfpSectionRubricMap)
        .filter(RfpSectionRubricMap.proposal_id == proposal_id)
        .all()
    }
    parsons_rows = {
        r.rfp_section_id: r
        for r in db.query(RfpSectionResponse)
        .filter(
            RfpSectionResponse.proposal_id == proposal_id,
            RfpSectionResponse.author_type == "parsons",
        )
        .all()
    }
    parsons_scores = {}
    for s in (
        db.query(SectionScore)
        .filter(
            SectionScore.proposal_id == proposal_id,
            SectionScore.scorer_type == "parsons",
        )
        .order_by(SectionScore.scored_at.desc())
        .all()
    ):
        parsons_scores.setdefault(s.section_id, s)

    rows = []
    for sid in sorted(by_section.keys(), key=_section_sort_key):
        reqs = by_section[sid]
        needs = any(_section_needs_response(r) for r in reqs)
        if not needs and not include_informational:
            continue
        rubric_sid = _resolve_rubric_section(sid, reqs, maps)
        rubric_row = rubric_sections.get(rubric_sid)
        weight_pts = (rubric_row.weight_points or 0) if rubric_row else 0
        pass_fail = bool(rubric_row and rubric_row.is_pass_fail)
        pct_of_total = (
            round(100.0 * weight_pts / total_points, 1) if (weight_pts and not pass_fail) else None
        )
        presp = parsons_rows.get(sid)
        pscore = parsons_scores.get(sid)
        compliance_map = {}
        if presp and presp.compliance_tags_json:
            try:
                compliance_map = json.loads(presp.compliance_tags_json) or {}
            except Exception:
                compliance_map = {}
        rows.append({
            "section_id": sid,
            "requirements": reqs,
            "needs_response": needs,
            "rubric_section_id": rubric_sid,
            "rubric_weight_points": weight_pts,
            "rubric_pass_fail": pass_fail,
            "rubric_pct_of_total": pct_of_total,
            "parsons_response": presp,
            "parsons_score": pscore,
            "compliance_tags": compliance_map,
        })
    return {
        "proposal_id": proposal_id,
        "total_points": total_points,
        "rows": rows,
    }


@router.get("/export.xlsx")
def export_workbench_xlsx(
    proposal_id: int = Query(..., description="Required"),
    include_informational: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Excel compliance-matrix-style export.

    One row per extracted section with the Parsons response, score, rubric
    mapping, weight, and per-requirement compliance tags serialized to a
    readable summary column.
    """
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
    except Exception as e:
        raise HTTPException(500, f"openpyxl not installed: {e}")
    from fastapi.responses import StreamingResponse
    from io import BytesIO

    data = _gather_export_rows(db, proposal_id, include_informational)
    wb = Workbook()
    ws = wb.active
    ws.title = "Response Workbench"

    headers = [
        "Section",
        "# Reqs",
        "Needs Response",
        "Rubric Section",
        "Weight (pts)",
        "% of Total",
        "Pass/Fail",
        "Parsons Score",
        "Response Status",
        "Response Version",
        "Parsons Response",
        "Compliance Tags",
        "Requirements Summary",
    ]
    ws.append(headers)
    header_fill = PatternFill(start_color="00AEE6", end_color="00AEE6", fill_type="solid")
    header_font = Font(bold=True, color="FFFFFF")
    for col in range(1, len(headers) + 1):
        c = ws.cell(row=1, column=col)
        c.fill = header_fill
        c.font = header_font
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    for r in data["rows"]:
        presp = r["parsons_response"]
        pscore = r["parsons_score"]
        req_summary = "\n".join(
            f"[{q.requirement_id}] {(q.title or q.source_text or q.description or '')[:200]}"
            for q in r["requirements"][:20]
        )
        compliance_summary = "; ".join(
            f"{rid}: {tag}" for rid, tag in (r["compliance_tags"] or {}).items()
        )
        ws.append([
            r["section_id"],
            len(r["requirements"]),
            "Yes" if r["needs_response"] else "No",
            r["rubric_section_id"] or "",
            r["rubric_weight_points"] or 0,
            f"{r['rubric_pct_of_total']}%" if r["rubric_pct_of_total"] is not None else ("Pass/Fail" if r["rubric_pass_fail"] else ""),
            "Yes" if r["rubric_pass_fail"] else "No",
            pscore.score if pscore else "",
            presp.status if presp else "",
            presp.version if presp else "",
            (presp.content if presp else "")[:32000],
            compliance_summary[:32000],
            req_summary[:32000],
        ])

    widths = [12, 8, 14, 22, 12, 12, 10, 14, 14, 10, 80, 40, 60]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = w
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(vertical="top", wrap_text=True)

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    headers_out = {
        "Content-Disposition": f'attachment; filename="response_workbench_proposal_{proposal_id}.xlsx"'
    }
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers_out,
    )


@router.get("/export.docx")
def export_workbench_docx(
    proposal_id: int = Query(..., description="Required"),
    include_informational: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Word export in RFP volume-style layout."""
    try:
        from docx import Document as DocxDocument
        from docx.shared import Pt
    except Exception as e:
        raise HTTPException(500, f"python-docx not installed: {e}")
    from fastapi.responses import StreamingResponse
    from io import BytesIO

    data = _gather_export_rows(db, proposal_id, include_informational)
    d = DocxDocument()
    d.add_heading("Parsons — RFP Response", level=0)
    d.add_paragraph(f"Proposal ID: {proposal_id}")
    d.add_paragraph(f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    d.add_paragraph(f"Sections included: {len(data['rows'])}")
    d.add_paragraph("")

    for r in data["rows"]:
        d.add_heading(f"Section {r['section_id']}", level=1)
        meta_parts = []
        if r["rubric_section_id"]:
            meta_parts.append(f"Rubric: {r['rubric_section_id']}")
        if r["rubric_weight_points"]:
            meta_parts.append(f"Weight: {r['rubric_weight_points']} pts")
        if r["rubric_pct_of_total"] is not None:
            meta_parts.append(f"{r['rubric_pct_of_total']}% of total")
        if r["rubric_pass_fail"]:
            meta_parts.append("Pass/Fail")
        if meta_parts:
            p = d.add_paragraph(" · ".join(meta_parts))
            for run in p.runs:
                run.font.size = Pt(9)
                run.italic = True

        presp = r["parsons_response"]
        if presp and presp.content:
            d.add_heading("Parsons Response", level=2)
            for line in (presp.content or "").splitlines() or [""]:
                d.add_paragraph(line)
        else:
            d.add_paragraph("(no Parsons response drafted yet)").italic = True

        if r["compliance_tags"]:
            d.add_heading("Compliance Tags", level=3)
            for rid, tag in r["compliance_tags"].items():
                d.add_paragraph(f"{rid}: {tag}", style="List Bullet")

        if r["requirements"]:
            d.add_heading("Source Requirements", level=3)
            for q in r["requirements"]:
                txt = q.title or q.source_text or q.description or ""
                d.add_paragraph(f"[{q.requirement_id}] {txt[:400]}", style="List Bullet")

        d.add_paragraph("")

    buf = BytesIO()
    d.save(buf)
    buf.seek(0)
    headers_out = {
        "Content-Disposition": f'attachment; filename="response_workbench_proposal_{proposal_id}.docx"'
    }
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers=headers_out,
    )


@router.get("/export.pdf")
def export_workbench_pdf(
    proposal_id: int = Query(..., description="Required"),
    include_informational: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """PDF export in volume-style layout via reportlab."""
    try:
        from reportlab.lib.pagesizes import LETTER
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.platypus import (
            SimpleDocTemplate,
            Paragraph,
            Spacer,
            PageBreak,
        )
        from reportlab.lib.enums import TA_LEFT
    except Exception as e:
        raise HTTPException(500, f"reportlab not installed: {e}")
    from fastapi.responses import StreamingResponse
    from io import BytesIO

    data = _gather_export_rows(db, proposal_id, include_informational)

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=LETTER,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        title=f"Response Workbench — Proposal {proposal_id}",
    )
    styles = getSampleStyleSheet()
    meta_style = ParagraphStyle(
        "meta", parent=styles["Normal"], fontSize=9, textColor="#777777", italic=True
    )
    body_style = ParagraphStyle(
        "body", parent=styles["BodyText"], fontSize=10, leading=14, alignment=TA_LEFT
    )

    story = []
    story.append(Paragraph("Parsons — RFP Response", styles["Title"]))
    story.append(Paragraph(f"Proposal ID: {proposal_id}", styles["Normal"]))
    story.append(Paragraph(
        f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
        styles["Normal"],
    ))
    story.append(Paragraph(f"Sections included: {len(data['rows'])}", styles["Normal"]))
    story.append(Spacer(1, 0.2 * inch))

    def _esc(s: str) -> str:
        if not s:
            return ""
        return (
            s.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    for idx, r in enumerate(data["rows"]):
        story.append(Paragraph(f"Section {_esc(r['section_id'])}", styles["Heading1"]))
        meta_parts = []
        if r["rubric_section_id"]:
            meta_parts.append(f"Rubric: {r['rubric_section_id']}")
        if r["rubric_weight_points"]:
            meta_parts.append(f"Weight: {r['rubric_weight_points']} pts")
        if r["rubric_pct_of_total"] is not None:
            meta_parts.append(f"{r['rubric_pct_of_total']}% of total")
        if r["rubric_pass_fail"]:
            meta_parts.append("Pass/Fail")
        if meta_parts:
            story.append(Paragraph(" · ".join(_esc(m) for m in meta_parts), meta_style))

        presp = r["parsons_response"]
        story.append(Paragraph("Parsons Response", styles["Heading2"]))
        if presp and presp.content:
            for line in presp.content.splitlines() or [""]:
                story.append(Paragraph(_esc(line) or "&nbsp;", body_style))
        else:
            story.append(Paragraph("<i>(no Parsons response drafted yet)</i>", body_style))

        if r["compliance_tags"]:
            story.append(Paragraph("Compliance Tags", styles["Heading3"]))
            for rid, tag in r["compliance_tags"].items():
                story.append(Paragraph(f"• {_esc(rid)}: {_esc(tag)}", body_style))

        if r["requirements"]:
            story.append(Paragraph("Source Requirements", styles["Heading3"]))
            for q in r["requirements"]:
                txt = q.title or q.source_text or q.description or ""
                story.append(Paragraph(
                    f"• [{_esc(q.requirement_id)}] {_esc(txt[:400])}",
                    body_style,
                ))

        story.append(Spacer(1, 0.25 * inch))
        if idx < len(data["rows"]) - 1:
            story.append(PageBreak())

    doc.build(story)
    buf.seek(0)
    headers_out = {
        "Content-Disposition": f'attachment; filename="response_workbench_proposal_{proposal_id}.pdf"'
    }
    return StreamingResponse(buf, media_type="application/pdf", headers=headers_out)