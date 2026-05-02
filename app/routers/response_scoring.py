"""
Response Scoring router — endpoints powering the right-side slide-out panel
on the Parsons response page.

Provides:
  - GET  /api/response-scoring/proposals/{pid}/target-competitor
  - PUT  /api/response-scoring/proposals/{pid}/target-competitor
  - POST /api/response-scoring/proposals/{pid}/sections/{sid}/score
  - POST /api/response-scoring/proposals/{pid}/score-all
  - GET  /api/response-scoring/proposals/{pid}/aggregate
  - GET  /api/response-scoring/proposals/{pid}/sections/{sid}/competitor-prediction
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional, List

from ..auth import get_current_user
from ..database import get_db
from ..models import (
    Competitor,
    CompetitorPrediction,
    Proposal,
    ProposalSection,
    ScoringRubric,
    ScoringRubricSection,
    SectionScore,
    User,
)
from ..services.response_scorer import (
    compute_aggregate,
    score_section_response,
)
from ..services.competitor_analyst import predict_competitor_response

router = APIRouter()


# ── Schemas ──────────────────────────────────────────────────────────


class TargetCompetitorIn(BaseModel):
    competitor_id: Optional[int] = None  # null clears the target


class ScoreSectionIn(BaseModel):
    # If true, score Parsons' current saved section. If competitor_id is set,
    # also (re)score that competitor's predicted response for the same section.
    score_parsons: bool = True
    competitor_id: Optional[int] = None
    # If competitor_id is set and no prediction exists yet, generate one first.
    auto_predict_if_missing: bool = True


class SectionScoreOut(BaseModel):
    section_id: str
    scorer_type: str
    scorer_name: Optional[str]
    score: int
    rationale: Optional[str]


# ── Target competitor ────────────────────────────────────────────────


@router.get("/proposals/{proposal_id}/target-competitor")
def get_target_competitor(
    proposal_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    proposal = db.query(Proposal).filter(Proposal.id == proposal_id).first()
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")
    comp = None
    if proposal.target_competitor_id:
        comp = db.query(Competitor).filter(Competitor.id == proposal.target_competitor_id).first()
    return {
        "proposal_id": proposal_id,
        "competitor": (
            {"id": comp.id, "name": comp.name} if comp else None
        ),
    }


@router.put("/proposals/{proposal_id}/target-competitor")
def set_target_competitor(
    proposal_id: int,
    payload: TargetCompetitorIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    proposal = db.query(Proposal).filter(Proposal.id == proposal_id).first()
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")

    if payload.competitor_id is not None:
        comp = db.query(Competitor).filter(Competitor.id == payload.competitor_id).first()
        if not comp:
            raise HTTPException(status_code=404, detail="Competitor not found")
        proposal.target_competitor_id = comp.id
    else:
        proposal.target_competitor_id = None

    db.commit()
    db.refresh(proposal)
    return {
        "proposal_id": proposal_id,
        "target_competitor_id": proposal.target_competitor_id,
    }


# ── Score one section ────────────────────────────────────────────────


@router.post("/proposals/{proposal_id}/sections/{section_id}/score")
def score_section(
    proposal_id: int,
    section_id: str,
    payload: ScoreSectionIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Score Parsons + (optionally) the chosen competitor on a single section.

    Called automatically after the Parsons response page saves a section, so the
    slide-out panel always reflects current content. Returns updated scores plus
    the refreshed aggregate roll-up.
    """
    proposal = db.query(Proposal).filter(Proposal.id == proposal_id).first()
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")

    rubric_section = (
        db.query(ScoringRubricSection)
        .join(ScoringRubric, ScoringRubric.id == ScoringRubricSection.rubric_id)
        .filter(
            ScoringRubric.is_default == True,
            ScoringRubricSection.section_id == section_id,
        )
        .first()
    )
    # We allow scoring sections that aren't in the rubric (the ParsonsServices UI
    # has more sections than the legal rubric). They'll just contribute 0 weight.

    results: List[SectionScoreOut] = []

    # Parsons
    if payload.score_parsons:
        ps_score = score_section_response(
            db=db,
            proposal_id=proposal_id,
            section_id=section_id,
            scorer_type="parsons",
            scorer_name=None,
        )
        if ps_score:
            results.append(SectionScoreOut(
                section_id=ps_score.section_id,
                scorer_type=ps_score.scorer_type,
                scorer_name=ps_score.scorer_name,
                score=ps_score.score,
                rationale=ps_score.rationale,
            ))

    # Competitor — explicit override or fall back to proposal's target
    comp_id = payload.competitor_id or proposal.target_competitor_id
    if comp_id:
        comp = db.query(Competitor).filter(Competitor.id == comp_id).first()
        if comp:
            # Make sure a prediction exists (if not, optionally generate one)
            pred = (
                db.query(CompetitorPrediction)
                .filter(
                    CompetitorPrediction.competitor_id == comp.id,
                    CompetitorPrediction.section_id == section_id,
                )
                .first()
            )
            if not pred and payload.auto_predict_if_missing:
                try:
                    pred = predict_competitor_response(db, comp.id, section_id, proposal_id)
                except Exception as e:
                    # Don't fail the whole call — the panel will show "no prediction yet"
                    pred = None

            if pred and pred.predicted_response:
                cs_score = score_section_response(
                    db=db,
                    proposal_id=proposal_id,
                    section_id=section_id,
                    scorer_type="competitor",
                    scorer_name=comp.name,
                )
                if cs_score:
                    results.append(SectionScoreOut(
                        section_id=cs_score.section_id,
                        scorer_type=cs_score.scorer_type,
                        scorer_name=cs_score.scorer_name,
                        score=cs_score.score,
                        rationale=cs_score.rationale,
                    ))

    # Refresh aggregate
    target_name = None
    if proposal.target_competitor_id:
        tc = db.query(Competitor).filter(Competitor.id == proposal.target_competitor_id).first()
        target_name = tc.name if tc else None
    aggregate = compute_aggregate(db, proposal_id, target_name)

    return {
        "proposal_id": proposal_id,
        "section_id": section_id,
        "results": [r.model_dump() for r in results],
        "aggregate": aggregate,
    }


# ── Score every rubric section in one shot ──────────────────────────


@router.post("/proposals/{proposal_id}/score-all")
def score_all_sections(
    proposal_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Score Parsons (and target competitor, if any) on every rubric section.

    Heavy — calls the LLM once per section per scorer. Use sparingly (e.g., when
    the user opens the panel for the first time on a proposal, or hits "Score All").
    """
    proposal = db.query(Proposal).filter(Proposal.id == proposal_id).first()
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")

    rubric = db.query(ScoringRubric).filter(ScoringRubric.is_default == True).first()
    if not rubric:
        raise HTTPException(status_code=500, detail="No default rubric")

    sections = (
        db.query(ScoringRubricSection)
        .filter(ScoringRubricSection.rubric_id == rubric.id)
        .order_by(ScoringRubricSection.sort_order)
        .all()
    )

    target_comp = None
    if proposal.target_competitor_id:
        target_comp = db.query(Competitor).filter(Competitor.id == proposal.target_competitor_id).first()

    sections_scored = 0
    for sec in sections:
        score_section_response(
            db=db,
            proposal_id=proposal_id,
            section_id=sec.section_id,
            scorer_type="parsons",
            scorer_name=None,
        )
        sections_scored += 1

        if target_comp:
            # Ensure a prediction exists
            pred = (
                db.query(CompetitorPrediction)
                .filter(
                    CompetitorPrediction.competitor_id == target_comp.id,
                    CompetitorPrediction.section_id == sec.section_id,
                )
                .first()
            )
            if not pred:
                try:
                    pred = predict_competitor_response(db, target_comp.id, sec.section_id, proposal_id)
                except Exception:
                    pred = None
            if pred and pred.predicted_response:
                score_section_response(
                    db=db,
                    proposal_id=proposal_id,
                    section_id=sec.section_id,
                    scorer_type="competitor",
                    scorer_name=target_comp.name,
                )

    aggregate = compute_aggregate(
        db, proposal_id, target_comp.name if target_comp else None
    )
    return {
        "proposal_id": proposal_id,
        "sections_scored": sections_scored,
        "aggregate": aggregate,
    }


# ── Aggregate roll-up ────────────────────────────────────────────────


@router.get("/proposals/{proposal_id}/aggregate")
def get_aggregate(
    proposal_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    proposal = db.query(Proposal).filter(Proposal.id == proposal_id).first()
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")

    target_name = None
    if proposal.target_competitor_id:
        tc = db.query(Competitor).filter(Competitor.id == proposal.target_competitor_id).first()
        target_name = tc.name if tc else None

    return compute_aggregate(db, proposal_id, target_name)


# ── Competitor prediction for a single section (for the panel body) ─


@router.get("/proposals/{proposal_id}/sections/{section_id}/competitor-prediction")
def get_competitor_prediction(
    proposal_id: int,
    section_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return the target competitor's predicted response for this section, if any."""
    proposal = db.query(Proposal).filter(Proposal.id == proposal_id).first()
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")
    if not proposal.target_competitor_id:
        return {"competitor": None, "prediction": None}

    comp = db.query(Competitor).filter(Competitor.id == proposal.target_competitor_id).first()
    if not comp:
        return {"competitor": None, "prediction": None}

    pred = (
        db.query(CompetitorPrediction)
        .filter(
            CompetitorPrediction.competitor_id == comp.id,
            CompetitorPrediction.section_id == section_id,
        )
        .first()
    )
    return {
        "competitor": {"id": comp.id, "name": comp.name},
        "prediction": (
            {
                "id": pred.id,
                "predicted_response": pred.predicted_response,
                "reasoning": pred.reasoning,
                "confidence_score": pred.confidence_score,
                "model_used": pred.model_used,
                "updated_at": pred.updated_at.isoformat() if pred.updated_at else None,
            }
            if pred
            else None
        ),
    }
