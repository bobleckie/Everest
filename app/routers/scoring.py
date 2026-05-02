"""Scoring rubric and section-score endpoints."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
import json

from ..database import get_db
from ..models import (
    ScoringRubric, ScoringRubricSection, SectionScore, Proposal, User,
)
from ..auth import get_current_user, get_current_admin_user

router = APIRouter()

# ── Pydantic schemas ────────────────────────────────────────────────

class RubricSectionOut(BaseModel):
    id: int
    section_id: str
    title: str
    weight_points: int
    pass_fail: bool
    sort_order: int

class RubricOut(BaseModel):
    id: int
    name: str
    is_default: bool
    sections: List[RubricSectionOut]

class RubricSectionIn(BaseModel):
    section_id: str
    title: str
    weight_points: int = 0
    pass_fail: bool = False
    sort_order: int = 0

class RubricUpdateIn(BaseModel):
    name: Optional[str] = None
    sections: List[RubricSectionIn]

class SectionScoreOut(BaseModel):
    id: int
    proposal_id: int
    section_id: str
    scorer_type: str
    scorer_name: Optional[str]
    score: int
    rationale: Optional[str]

class SectionScoreIn(BaseModel):
    section_id: str
    scorer_type: str  # "parsons" or "competitor"
    scorer_name: Optional[str] = None
    score: int  # 0-100
    rationale: Optional[str] = None

class BulkScoreIn(BaseModel):
    scores: List[SectionScoreIn]

# ── Helpers ──────────────────────────────────────────────────────────

def _rubric_to_out(rubric: ScoringRubric, sections: list) -> dict:
    return {
        "id": rubric.id,
        "name": rubric.name,
        "is_default": rubric.is_default,
        "sections": sorted(
            [
                {
                    "id": s.id,
                    "section_id": s.section_id,
                    "title": s.title,
                    "weight_points": s.weight_points,
                    "pass_fail": s.pass_fail,
                    "sort_order": s.sort_order,
                }
                for s in sections
            ],
            key=lambda x: x["sort_order"],
        ),
    }

# ── Default rubric seed ─────────────────────────────────────────────
#
# Rubric transcribed from the 2021 NJ T1628 / 20DPP00471 Enhanced MVI/M
# Evaluation Committee Report (Nov 22, 2021) — Bid Solicitation §6.7.1,
# Technical Evaluation Criteria. The scoring methodology (report §IV.B):
#
#   • Each voting member scores every criterion 1-10:
#         9-10 Excellent, 7-8 Very Good, 5-6 Good, 3-4 Fair, 1-2 Poor
#   • That score is multiplied by the criterion weight shown below.
#   • Per-member max = 10 × (10 + 10 + 80) = 1000 points.
#   • Seven (7) voting members → program-wide max = 7000.
#
# `weight_points` on each section doubles as the SECTION MAXIMUM in the
# UI (section_score_1_to_10 × weight_points/10 is the contribution).
# The three weighted criteria below sum to 100; the two pass/fail gates
# capture the mandatory-elements and Source Disclosure Form (N.J.S.A.
# 52:34-13.2) screens that disqualified Parsons' 2021 quote.

DEFAULT_SECTIONS = [
    # section_id,     title,                                        weight, pass_fail, sort_order
    ("mandatoryElements", "Mandatory Elements (PRU compliance review)", 0, True, 0),
    ("sourceDisclosure",  "Source Disclosure / U.S. Performance (N.J.S.A. 52:34-13.2)", 0, True, 1),
    ("personnel",    "Criterion A — Personnel: qualifications & experience of management, supervisory, and key personnel assigned to the Blanket P.O.", 10, False, 2),
    ("experience",   "Criterion B — Experience of Firm: documented success on contracts of similar size and scope",                                    10, False, 3),
    ("technicalApproach", "Criterion C — Ability to Complete Scope of Work (Technical Quote): demonstrated understanding of requirements and approach that would permit successful performance",                   80, False, 4),
]
# Weighted sections sum: 10 + 10 + 80 = 100  ✓
# Max per-member technical score: 100 × 10 = 1000
# Max committee technical score (7 voting members): 7000


# Identifier used to detect an outdated/legacy rubric still stored in the DB
# from prior releases. When we find the is_default rubric was seeded before
# the 2021 evaluation-report alignment, we rewrite its section rows so the
# criteria and their maximum points reflect the actual solicitation.
_RUBRIC_NAME = "NJ T1628 Technical Evaluation Rubric (2021)"
_EXPECTED_SECTION_IDS = {sid for sid, *_ in DEFAULT_SECTIONS}


def seed_default_rubric(db: Session):
    """Create (or realign) the default scoring rubric.

    If no default rubric exists yet, create it from `DEFAULT_SECTIONS`.
    If one exists but its section_ids do not match the canonical T1628
    criteria, rewrite the section rows in place so the rubric reflects
    the real evaluation report. SectionScore rows keyed to old
    section_ids are left untouched — they remain historical records.
    """
    rubric = db.query(ScoringRubric).filter(ScoringRubric.is_default == True).first()
    if rubric is None:
        rubric = ScoringRubric(name=_RUBRIC_NAME, is_default=True)
        db.add(rubric)
        db.flush()
        for sid, title, weight, pf, order in DEFAULT_SECTIONS:
            db.add(ScoringRubricSection(
                rubric_id=rubric.id,
                section_id=sid,
                title=title,
                weight_points=weight,
                pass_fail=pf,
                sort_order=order,
            ))
        db.commit()
        db.refresh(rubric)
        return rubric

    # Existing rubric: check whether it already matches the canonical set
    current_sections = (
        db.query(ScoringRubricSection)
        .filter(ScoringRubricSection.rubric_id == rubric.id)
        .all()
    )
    current_ids = {s.section_id for s in current_sections}
    if current_ids == _EXPECTED_SECTION_IDS:
        return rubric

    # Realign: replace section rows with the canonical T1628 criteria.
    db.query(ScoringRubricSection).filter(
        ScoringRubricSection.rubric_id == rubric.id
    ).delete()
    rubric.name = _RUBRIC_NAME
    for sid, title, weight, pf, order in DEFAULT_SECTIONS:
        db.add(ScoringRubricSection(
            rubric_id=rubric.id,
            section_id=sid,
            title=title,
            weight_points=weight,
            pass_fail=pf,
            sort_order=order,
        ))
    db.commit()
    db.refresh(rubric)
    return rubric


# ── GET default rubric ───────────────────────────────────────────────

@router.get("/rubric")
def get_rubric(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rubric = db.query(ScoringRubric).filter(ScoringRubric.is_default == True).first()
    if not rubric:
        rubric = seed_default_rubric(db)
    sections = (
        db.query(ScoringRubricSection)
        .filter(ScoringRubricSection.rubric_id == rubric.id)
        .all()
    )
    return _rubric_to_out(rubric, sections)


# ── PUT default rubric (admin only) ─────────────────────────────────

@router.put("/rubric")
def update_rubric(
    payload: RubricUpdateIn,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin_user),
):
    rubric = db.query(ScoringRubric).filter(ScoringRubric.is_default == True).first()
    if not rubric:
        rubric = seed_default_rubric(db)

    if payload.name:
        rubric.name = payload.name

    # Validate: weighted sections must sum to 100
    weighted_sum = sum(s.weight_points for s in payload.sections if not s.pass_fail)
    if weighted_sum != 100:
        raise HTTPException(
            status_code=422,
            detail=f"Weighted section points must sum to 100 (got {weighted_sum})",
        )

    # Delete old sections and replace
    db.query(ScoringRubricSection).filter(
        ScoringRubricSection.rubric_id == rubric.id
    ).delete()

    for s in payload.sections:
        db.add(ScoringRubricSection(
            rubric_id=rubric.id,
            section_id=s.section_id,
            title=s.title,
            weight_points=s.weight_points,
            pass_fail=s.pass_fail,
            sort_order=s.sort_order,
        ))

    db.commit()
    db.refresh(rubric)

    sections = (
        db.query(ScoringRubricSection)
        .filter(ScoringRubricSection.rubric_id == rubric.id)
        .all()
    )
    return _rubric_to_out(rubric, sections)


# ── POST validate rubric (preview only) ─────────────────────────────

@router.post("/rubric/validate")
def validate_rubric(
    payload: RubricUpdateIn,
    current_user: User = Depends(get_current_user),
):
    weighted = [s for s in payload.sections if not s.pass_fail]
    total = sum(s.weight_points for s in weighted)
    errors = []
    if total != 100:
        errors.append(f"Weighted points sum to {total}, must be 100")
    for s in payload.sections:
        if s.weight_points < 0 or s.weight_points > 100:
            errors.append(f"Section {s.section_id}: weight must be 0-100")
    return {"valid": len(errors) == 0, "total_weighted": total, "errors": errors}


# ── GET proposal scores ─────────────────────────────────────────────

@router.get("/proposals/{proposal_id}/scores")
def get_proposal_scores(
    proposal_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    proposal = db.query(Proposal).filter(Proposal.id == proposal_id).first()
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")

    scores = (
        db.query(SectionScore)
        .filter(SectionScore.proposal_id == proposal_id)
        .all()
    )
    return {
        "proposal_id": proposal_id,
        "scores": [
            {
                "id": s.id,
                "section_id": s.section_id,
                "scorer_type": s.scorer_type,
                "scorer_name": s.scorer_name,
                "score": s.score,
                "rationale": s.rationale,
            }
            for s in scores
        ],
    }


# ── PUT proposal scores (bulk upsert) ───────────────────────────────

@router.put("/proposals/{proposal_id}/scores")
def save_proposal_scores(
    proposal_id: int,
    payload: BulkScoreIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    proposal = db.query(Proposal).filter(Proposal.id == proposal_id).first()
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")

    saved = []
    for entry in payload.scores:
        if entry.score < 0 or entry.score > 100:
            raise HTTPException(status_code=422, detail=f"Score must be 0-100 for {entry.section_id}")

        # Upsert: find existing by (proposal_id, section_id, scorer_type, scorer_name)
        existing = (
            db.query(SectionScore)
            .filter(
                SectionScore.proposal_id == proposal_id,
                SectionScore.section_id == entry.section_id,
                SectionScore.scorer_type == entry.scorer_type,
                SectionScore.scorer_name == entry.scorer_name,
            )
            .first()
        )
        if existing:
            existing.score = entry.score
            existing.rationale = entry.rationale
            existing.scored_by = current_user.id
            saved.append(existing)
        else:
            new_score = SectionScore(
                proposal_id=proposal_id,
                section_id=entry.section_id,
                scorer_type=entry.scorer_type,
                scorer_name=entry.scorer_name,
                score=entry.score,
                rationale=entry.rationale,
                scored_by=current_user.id,
            )
            db.add(new_score)
            saved.append(new_score)

    db.commit()
    return {"saved": len(saved), "proposal_id": proposal_id}
