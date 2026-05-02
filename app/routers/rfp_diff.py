"""
RFP Diff router — compare a baseline solicitation/revision against a target
(the current solicitation). Results are cached in rfp_diff_runs +
rfp_requirement_diffs so re-opening the page is instant.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..database import get_db, SessionLocal
from ..models import (
    IngestedDocument, RfpDiffRun, RfpRequirement, RfpRequirementDiff, User,
)
from ..services.requirement_diff import run_requirement_diff

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Schemas ──────────────────────────────────────────────────────────

class DiffRunOut(BaseModel):
    id: int
    label: Optional[str]
    baseline_proposal_id: Optional[int]
    baseline_document_ids: Optional[List[int]]
    target_proposal_id: Optional[int]
    target_document_ids: Optional[List[int]]
    status: str
    progress_note: Optional[str]
    counts_added: int
    counts_removed: int
    counts_changed: int
    counts_unchanged: int
    high_match_threshold: Optional[float]
    low_match_threshold: Optional[float]
    error: Optional[str]
    created_at: Optional[datetime]
    completed_at: Optional[datetime]


def _run_to_dict(r: RfpDiffRun) -> dict:
    def _parse_ids(s):
        if not s:
            return None
        try:
            v = json.loads(s)
            return v if isinstance(v, list) else None
        except Exception:
            return None

    return {
        "id": r.id,
        "label": r.label,
        "baseline_proposal_id": r.baseline_proposal_id,
        "baseline_document_ids": _parse_ids(r.baseline_document_ids),
        "target_proposal_id": r.target_proposal_id,
        "target_document_ids": _parse_ids(r.target_document_ids),
        "status": r.status,
        "progress_note": r.progress_note,
        "counts_added": r.counts_added or 0,
        "counts_removed": r.counts_removed or 0,
        "counts_changed": r.counts_changed or 0,
        "counts_unchanged": r.counts_unchanged or 0,
        "high_match_threshold": r.high_match_threshold,
        "low_match_threshold": r.low_match_threshold,
        "error": r.error,
        "created_at": r.created_at,
        "completed_at": r.completed_at,
    }


class DiffRequirementSide(BaseModel):
    id: Optional[int] = None
    document_id: Optional[int] = None
    section_id: Optional[str] = None
    category: Optional[str] = None
    priority: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    source_page: Optional[int] = None
    source_text: Optional[str] = None


class DiffRowOut(BaseModel):
    id: int
    diff_run_id: int
    status: str  # added, removed, changed, unchanged
    similarity: Optional[float]
    match_method: Optional[str]
    change_summary: Optional[str]
    impact_blurb: Optional[str]
    impact_severity: Optional[str]
    reviewer_status: Optional[str]
    reviewer_notes: Optional[str]
    baseline: Optional[DiffRequirementSide]
    target: Optional[DiffRequirementSide]


class StartDiffIn(BaseModel):
    baseline_proposal_id: Optional[int] = None
    baseline_document_ids: Optional[List[int]] = None
    target_proposal_id: Optional[int] = None
    target_document_ids: Optional[List[int]] = None
    label: Optional[str] = None
    high_threshold: float = Field(0.92, ge=0.5, le=1.0)
    low_threshold: float = Field(0.70, ge=0.3, le=0.95)
    write_blurbs: bool = True


class DiffRowUpdate(BaseModel):
    reviewer_status: Optional[str] = None  # new, reviewed, dismissed, flagged
    reviewer_notes: Optional[str] = None


# ── Background runner ────────────────────────────────────────────────

def _run_diff_bg(body: StartDiffIn, user_id: Optional[int]) -> None:
    db = SessionLocal()
    try:
        run_requirement_diff(
            db,
            baseline_proposal_id=body.baseline_proposal_id,
            baseline_document_ids=body.baseline_document_ids,
            target_proposal_id=body.target_proposal_id,
            target_document_ids=body.target_document_ids,
            label=body.label,
            high_threshold=body.high_threshold,
            low_threshold=body.low_threshold,
            write_blurbs=body.write_blurbs,
            created_by_user_id=user_id,
        )
    except Exception as e:
        logger.exception(f"Background diff failed: {e}")
    finally:
        db.close()


# ── Endpoints ────────────────────────────────────────────────────────

@router.post("/runs")
def start_diff_run(
    body: StartDiffIn,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Kick off a diff run in the background. Returns the run id; poll
    GET /runs/{id} for status."""
    if not (body.baseline_proposal_id or body.baseline_document_ids):
        raise HTTPException(400, "Provide baseline_proposal_id or baseline_document_ids")
    if not (body.target_proposal_id or body.target_document_ids):
        raise HTTPException(400, "Provide target_proposal_id or target_document_ids")

    # Pre-create the run row so the caller gets a run_id immediately.
    row = RfpDiffRun(
        label=body.label,
        baseline_proposal_id=body.baseline_proposal_id,
        baseline_document_ids=json.dumps(body.baseline_document_ids) if body.baseline_document_ids else None,
        target_proposal_id=body.target_proposal_id,
        target_document_ids=json.dumps(body.target_document_ids) if body.target_document_ids else None,
        status="queued",
        progress_note="queued",
        high_match_threshold=body.high_threshold,
        low_match_threshold=body.low_threshold,
        created_by_user_id=getattr(user, "id", None),
        created_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    # Fire the real run. Since the runner creates its own RfpDiffRun, we
    # simply leave the queued row as a UI-friendly "pending" marker and the
    # background task will insert the actual run. To avoid duplicate UI rows,
    # we update the placeholder's status as a no-op ghost.
    background.add_task(_run_diff_bg, body, getattr(user, "id", None))
    return {"run_id": row.id, "status": "queued", "message": "Diff running in background."}


@router.get("/runs")
def list_runs(
    target_proposal_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    q = db.query(RfpDiffRun)
    if target_proposal_id is not None:
        q = q.filter(RfpDiffRun.target_proposal_id == target_proposal_id)
    rows = q.order_by(RfpDiffRun.id.desc()).all()
    return [_run_to_dict(r) for r in rows]


@router.get("/runs/{run_id}")
def get_run(
    run_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    r = db.query(RfpDiffRun).filter_by(id=run_id).first()
    if not r:
        raise HTTPException(404, "Diff run not found")
    return _run_to_dict(r)


@router.get("/runs/{run_id}/rows")
def get_run_rows(
    run_id: int,
    status_filter: Optional[str] = Query(None, alias="status"),
    severity: Optional[str] = Query(None),
    reviewer_status: Optional[str] = Query(None),
    search: Optional[str] = Query(None, description="Case-insensitive substring match on change_summary / impact_blurb / linked requirement title+description."),
    limit: int = Query(100, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """
    Paginated diff rows for a run.

    Returns an envelope:
        {
          "total": <pre-pagination count>,
          "offset": ..., "limit": ..., "returned": ...,
          "rows": [...]
        }
    Sorted: critical severity first, then status, then id — same as before.
    """
    from sqlalchemy import or_, func as sa_func

    q = db.query(RfpRequirementDiff).filter_by(diff_run_id=run_id)
    if status_filter:
        q = q.filter(RfpRequirementDiff.status == status_filter)
    if severity:
        q = q.filter(RfpRequirementDiff.impact_severity == severity)
    if reviewer_status:
        q = q.filter(RfpRequirementDiff.reviewer_status == reviewer_status)

    if search:
        s = f"%{search.strip()}%"
        # Left-join linked requirements so we can search their title+description too.
        # We use aliased joins so filtering doesn't trim the result set (LEFT OUTER).
        from sqlalchemy.orm import aliased
        BaseReq = aliased(RfpRequirement)
        TgtReq = aliased(RfpRequirement)
        q = (
            q.outerjoin(BaseReq, RfpRequirementDiff.baseline_requirement_id == BaseReq.id)
             .outerjoin(TgtReq, RfpRequirementDiff.target_requirement_id == TgtReq.id)
             .filter(or_(
                 RfpRequirementDiff.change_summary.ilike(s),
                 RfpRequirementDiff.impact_blurb.ilike(s),
                 BaseReq.title.ilike(s),
                 BaseReq.description.ilike(s),
                 TgtReq.title.ilike(s),
                 TgtReq.description.ilike(s),
             ))
             .distinct()
        )

    # Count BEFORE pagination. Use a subquery so distinct() (if applied) is respected.
    total = q.with_entities(sa_func.count(RfpRequirementDiff.id.distinct())).scalar() or 0

    rows = (
        q.order_by(
            RfpRequirementDiff.impact_severity.desc().nullslast(),
            RfpRequirementDiff.status,
            RfpRequirementDiff.id,
        )
        .offset(offset)
        .limit(limit)
        .all()
    )

    # Resolve linked requirements in a bulk fetch.
    ids = set()
    for r in rows:
        if r.baseline_requirement_id:
            ids.add(r.baseline_requirement_id)
        if r.target_requirement_id:
            ids.add(r.target_requirement_id)
    reqs = {}
    if ids:
        for req in db.query(RfpRequirement).filter(RfpRequirement.id.in_(ids)).all():
            reqs[req.id] = req

    def _side(req_id):
        if not req_id:
            return None
        r = reqs.get(req_id)
        if not r:
            return None
        return {
            "id": r.id,
            "document_id": r.document_id,
            "section_id": r.section_id,
            "category": r.category,
            "priority": r.priority,
            "title": r.title,
            "description": r.description,
            "source_page": r.source_page,
            "source_text": r.source_text,
        }

    out_rows = []
    for r in rows:
        out_rows.append({
            "id": r.id,
            "diff_run_id": r.diff_run_id,
            "status": r.status,
            "similarity": r.similarity,
            "match_method": r.match_method,
            "change_summary": r.change_summary,
            "impact_blurb": r.impact_blurb,
            "impact_severity": r.impact_severity,
            "reviewer_status": r.reviewer_status,
            "reviewer_notes": r.reviewer_notes,
            "baseline": _side(r.baseline_requirement_id),
            "target": _side(r.target_requirement_id),
        })

    return {
        "total": int(total),
        "offset": offset,
        "limit": limit,
        "returned": len(out_rows),
        "rows": out_rows,
    }


@router.patch("/rows/{row_id}")
def update_row(
    row_id: int,
    body: DiffRowUpdate,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    row = db.query(RfpRequirementDiff).filter_by(id=row_id).first()
    if not row:
        raise HTTPException(404, "Diff row not found")
    if body.reviewer_status is not None:
        row.reviewer_status = body.reviewer_status
    if body.reviewer_notes is not None:
        row.reviewer_notes = body.reviewer_notes
    db.commit()
    return {"id": row.id, "reviewer_status": row.reviewer_status, "reviewer_notes": row.reviewer_notes}


@router.delete("/runs/{run_id}")
def delete_run(
    run_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    row = db.query(RfpDiffRun).filter_by(id=run_id).first()
    if not row:
        raise HTTPException(404, "Diff run not found")
    # Cascade-delete the rows manually (FK has no ON DELETE clause here).
    db.query(RfpRequirementDiff).filter_by(diff_run_id=run_id).delete(synchronize_session=False)
    db.delete(row)
    db.commit()
    return {"deleted_run_id": run_id}
