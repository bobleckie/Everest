"""Flashcards API.

  GET    /api/flashcards                       - list (filterable)
  GET    /api/flashcards/sections              - card counts by section_id
  POST   /api/flashcards/generate              - kick off background generation
  POST   /api/flashcards/{id}/review           - record SM-2 review
  DELETE /api/flashcards/{id}                  - delete a card
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import func

from ..auth import get_current_user
from ..database import get_db, SessionLocal
from ..models import Flashcard, User
from ..services import flashcards as _fc
from ..services import jobs as _jobs
from ..services.rate_limit import enforce_rate_limit

router = APIRouter()


def _to_dict(c: Flashcard) -> dict:
    return {
        "id": c.id,
        "proposal_id": c.proposal_id,
        "requirement_id": c.requirement_id,
        "section_id": c.section_id,
        "question": c.question,
        "answer": c.answer,
        "card_type": c.card_type,
        "difficulty": c.difficulty,
        "source_page": c.source_page,
        "source_text": c.source_text,
        "review_count": c.review_count or 0,
        "correct_count": c.correct_count or 0,
        "last_reviewed_at": c.last_reviewed_at.isoformat() if c.last_reviewed_at else None,
        "next_review_at": c.next_review_at.isoformat() if c.next_review_at else None,
        "ease_factor": c.ease_factor,
        "interval_days": c.interval_days or 0,
        "last_rating": c.last_rating,
        "generated_by_model": c.generated_by_model,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


@router.get("")
def list_flashcards(
    proposal_id: Optional[int] = Query(None),
    section_prefix: Optional[str] = Query(None, description="e.g. '4', '4.10'"),
    card_type: Optional[str] = Query(None),
    due_only: bool = Query(False, description="Only cards due for review now"),
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = db.query(Flashcard)
    if proposal_id is not None:
        q = q.filter(Flashcard.proposal_id == proposal_id)
    if section_prefix:
        q = q.filter(Flashcard.section_id.like(f"{section_prefix}%"))
    if card_type:
        q = q.filter(Flashcard.card_type == card_type)
    if due_only:
        now = datetime.utcnow()
        # New (never reviewed) OR due now.
        q = q.filter(
            (Flashcard.next_review_at.is_(None)) | (Flashcard.next_review_at <= now)
        )
    total = q.count()
    rows = (
        q.order_by(Flashcard.section_id.asc(), Flashcard.id.asc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [_to_dict(c) for c in rows],
    }


@router.get("/sections")
def list_sections(
    proposal_id: int = Query(..., description="Proposal to summarize"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return per-section card counts so the deck picker can render
    "Section 4.10 — 23 cards (4 due)" style chips."""
    now = datetime.utcnow()
    rows = (
        db.query(
            Flashcard.section_id,
            func.count(Flashcard.id).label("total"),
            func.sum(
                func.coalesce(
                    (Flashcard.next_review_at.is_(None)) | (Flashcard.next_review_at <= now),
                    0,
                )
            ).label("due"),
        )
        .filter(Flashcard.proposal_id == proposal_id)
        .group_by(Flashcard.section_id)
        .order_by(Flashcard.section_id.asc())
        .all()
    )
    out = []
    for sec, total, due in rows:
        out.append({
            "section_id": sec or "(none)",
            "total": int(total or 0),
            "due": int(due or 0),
        })
    return {"proposal_id": proposal_id, "sections": out}


# ── Generate ────────────────────────────────────────────────────────

class GenerateBody(BaseModel):
    proposal_id: int
    section_prefix: str = Field(default="4", description="Default '4' = SOW only")
    max_requirements: Optional[int] = Field(
        default=None,
        description="Cap how many reqs to process this run (useful for a small first batch)",
    )


@router.post("/generate", dependencies=[Depends(enforce_rate_limit("llm"))])
def generate_flashcards(
    body: GenerateBody,
    current_user: User = Depends(get_current_user),
):
    """Spawn a background job that walks Section-`section_prefix` requirements
    on the proposal and generates flashcards for each one that doesn't already
    have one. Returns 202 + job id; poll `/api/jobs/{id}`.
    """
    uid = getattr(current_user, "id", None)

    def _target(job):
        worker_db = SessionLocal()
        try:
            def _progress(p):
                # Surface progress on the job for the UI to poll.
                try:
                    job.meta["progress"] = p
                except Exception:  # noqa: BLE001
                    pass
            return _fc.generate_for_proposal(
                worker_db,
                proposal_id=body.proposal_id,
                section_prefix=body.section_prefix,
                max_requirements=body.max_requirements,
                progress_cb=_progress,
            )
        finally:
            worker_db.close()

    job = _jobs.submit(
        "flashcards.generate", _target,
        user_id=uid,
        meta={
            "proposal_id": body.proposal_id,
            "section_prefix": body.section_prefix,
            "max_requirements": body.max_requirements,
        },
    )
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=202, content={
        "accepted": True,
        "job_id": job.id,
        "status": job.status,
        "poll_url": f"/api/jobs/{job.id}",
    })


# ── Review (SM-2) ───────────────────────────────────────────────────

class ReviewBody(BaseModel):
    rating: str  # "again" | "good" | "easy"


@router.post("/{card_id}/review")
def review_flashcard(
    card_id: int,
    body: ReviewBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    card = db.query(Flashcard).filter(Flashcard.id == card_id).first()
    if not card:
        raise HTTPException(404, f"Flashcard {card_id} not found")
    try:
        summary = _fc.apply_review(card, body.rating)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return {"card_id": card.id, **summary}


@router.delete("/{card_id}")
def delete_flashcard(
    card_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    card = db.query(Flashcard).filter(Flashcard.id == card_id).first()
    if not card:
        raise HTTPException(404, f"Flashcard {card_id} not found")
    db.delete(card)
    db.commit()
    return {"deleted": card_id}
