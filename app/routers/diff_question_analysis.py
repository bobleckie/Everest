"""Diff-question analysis pipeline endpoints.

  POST   /api/diff-question-analysis/runs            — start full pipeline
  GET    /api/diff-question-analysis/runs/{id}       — poll status
  GET    /api/diff-question-analysis/runs            — list for proposal
  GET    /api/diff-question-analysis/runs/{id}/candidates
                                                     — list candidate questions
                                                       with reconciliation links
  POST   /api/diff-question-analysis/candidates/{question_id}/accept
  POST   /api/diff-question-analysis/candidates/{question_id}/reject
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..database import SessionLocal, get_db
from ..models import (
    DiffQuestionAnalysisRun, RfpQuestion, RfpRequirementDiff, User,
)
from ..services.diff_question_analyst import (
    execute_pipeline, generate_questions_from_diff,
    reconcile_candidates, start_pipeline_run,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ─────────────────────────────────────────────────────────────────────
# Background runner (one thread per pipeline run)
# ─────────────────────────────────────────────────────────────────────

_RUNNING_PIPELINES: Dict[int, str] = {}
_RUNNING_LOCK = threading.Lock()


def _runner(run_id: int) -> None:
    db = SessionLocal()
    try:
        with _RUNNING_LOCK:
            _RUNNING_PIPELINES[run_id] = "running"
        execute_pipeline(db, run_id)
    except Exception as e:  # noqa: BLE001
        logger.exception(f"pipeline run {run_id} crashed: {e}")
    finally:
        with _RUNNING_LOCK:
            _RUNNING_PIPELINES.pop(run_id, None)
        db.close()


# ─────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────

class StartPipelineBody(BaseModel):
    proposal_id: int
    baseline_proposal_id: Optional[int] = None
    baseline_document_ids: Optional[List[int]] = None
    # Preferred baseline source: a completed consolidation run. When
    # supplied, the pipeline materializes its as-amended baseline as a
    # synthetic document and uses that as baseline_document_ids — this is
    # the apples-to-apples option the user asked for.
    baseline_consolidation_run_id: Optional[int] = None
    target_document_ids: Optional[List[int]] = None
    label: Optional[str] = None
    high_threshold: float = 0.92
    low_threshold: float = 0.70


class StartGenerationOnlyBody(BaseModel):
    proposal_id: int
    diff_run_id: int
    severity_floor: str = "medium"
    label: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────

def _run_to_dict(r: DiffQuestionAnalysisRun) -> Dict[str, Any]:
    return {
        "id": r.id,
        "proposal_id": r.proposal_id,
        "diff_run_id": r.diff_run_id,
        "label": r.label,
        "status": r.status,
        "progress_note": (
            r.progress_note.split("::")[0] if r.progress_note and r.progress_note.startswith("queued::")
            else r.progress_note
        ),
        "diff_started_at": r.diff_started_at.isoformat() if r.diff_started_at else None,
        "diff_completed_at": r.diff_completed_at.isoformat() if r.diff_completed_at else None,
        "generation_started_at": r.generation_started_at.isoformat() if r.generation_started_at else None,
        "generation_completed_at": r.generation_completed_at.isoformat() if r.generation_completed_at else None,
        "reconcile_started_at": r.reconcile_started_at.isoformat() if r.reconcile_started_at else None,
        "reconcile_completed_at": r.reconcile_completed_at.isoformat() if r.reconcile_completed_at else None,
        "candidates_generated": r.candidates_generated or 0,
        "high_confidence_count": r.high_confidence_count or 0,
        "inference_count": r.inference_count or 0,
        "reconciled_added": r.reconciled_added or 0,
        "reconciled_replaces": r.reconciled_replaces or 0,
        "reconciled_updates": r.reconciled_updates or 0,
        "reconciled_duplicates": r.reconciled_duplicates or 0,
        "error": r.error,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "completed_at": r.completed_at.isoformat() if r.completed_at else None,
    }


def _question_to_dict(q: RfpQuestion) -> Dict[str, Any]:
    try:
        diff_row_ids = json.loads(q.diff_row_ids) if q.diff_row_ids else []
    except (json.JSONDecodeError, TypeError):
        diff_row_ids = []
    try:
        related_req_ids = json.loads(q.related_requirement_ids) if q.related_requirement_ids else []
    except (json.JSONDecodeError, TypeError):
        related_req_ids = []
    return {
        "id": q.id,
        "proposal_id": q.proposal_id,
        "source_section": q.source_section,
        "source_page": q.source_page,
        "category": q.category,
        "priority": q.priority,
        "question_text": q.question_text,
        "rationale": q.rationale,
        "source_quote": q.source_quote,
        "status": q.status,
        "review_notes": q.review_notes,
        "source_kind": q.source_kind,
        "diff_run_id": q.diff_run_id,
        "diff_row_ids": diff_row_ids,
        "related_requirement_ids": related_req_ids,
        "inference_flag": bool(q.inference_flag),
        "inference_confidence": q.inference_confidence,
        "operation_change_summary": q.operation_change_summary,
        "reconciliation_action": q.reconciliation_action,
        "replaces_question_id": q.replaces_question_id,
        "superseded_by_question_id": q.superseded_by_question_id,
        "created_at": q.created_at.isoformat() if q.created_at else None,
        "updated_at": q.updated_at.isoformat() if q.updated_at else None,
    }


# ─────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────

@router.post("/runs")
def start_full_pipeline(
    body: StartPipelineBody,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Kick off the full pipeline:
       Stage 3 diff → question generation → reconciliation.

    Returns the analysis run id; UI polls
    ``GET /runs/{id}`` for status updates.

    If ``baseline_consolidation_run_id`` is supplied, the pipeline
    materializes the as-amended consolidated baseline into a synthetic
    document and uses that as the baseline source — apples-to-apples diff
    against the master + amendments instead of pairing every doc
    independently."""
    baseline_doc_ids = body.baseline_document_ids
    consol_label_suffix = ""
    if body.baseline_consolidation_run_id is not None:
        from ..services.baseline_consolidator import (
            materialize_baseline_as_documents,
        )
        result = materialize_baseline_as_documents(
            db, body.baseline_consolidation_run_id)
        if isinstance(result, dict) and result.get("error"):
            raise HTTPException(400, result["error"])
        baseline_doc_ids = [result["synthetic_document_id"]]
        consol_label_suffix = (f" (consolidation #{body.baseline_consolidation_run_id}, "
                               f"{result['materialized_requirement_count']} reqs)")

    if not (body.baseline_proposal_id or baseline_doc_ids):
        raise HTTPException(400,
            "Provide baseline_proposal_id, baseline_document_ids, or "
            "baseline_consolidation_run_id")
    run = start_pipeline_run(
        db,
        proposal_id=body.proposal_id,
        baseline_proposal_id=body.baseline_proposal_id,
        baseline_document_ids=baseline_doc_ids,
        target_proposal_id=body.proposal_id,
        target_document_ids=body.target_document_ids,
        label=(body.label or "") + consol_label_suffix,
        created_by_user_id=getattr(current_user, "id", None),
        high_threshold=body.high_threshold,
        low_threshold=body.low_threshold,
    )
    background.add_task(_runner, run.id)
    return {"id": run.id, "status": run.status}


@router.post("/runs/from-existing-diff")
def start_from_existing_diff(
    body: StartGenerationOnlyBody,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Skip the diff stage and run only generation + reconciliation against
    a diff_run_id that already completed. Useful when the diff is fresh
    but you want to re-run question analysis with different settings."""
    run = DiffQuestionAnalysisRun(
        proposal_id=body.proposal_id,
        diff_run_id=body.diff_run_id,
        label=body.label or f"questions-only diff #{body.diff_run_id}",
        status="generating_questions",
        progress_note="starting generation",
        created_by_user_id=getattr(current_user, "id", None),
        diff_started_at=datetime.utcnow(),
        diff_completed_at=datetime.utcnow(),
        candidates_generated=0,
        high_confidence_count=0,
        inference_count=0,
        reconciled_added=0,
        reconciled_replaces=0,
        reconciled_updates=0,
        reconciled_duplicates=0,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    def _runner_skip_diff(run_id: int) -> None:
        d = SessionLocal()
        try:
            gen = generate_questions_from_diff(d, run_id, severity_floor=body.severity_floor)
            if isinstance(gen, dict) and gen.get("error"):
                rr = d.query(DiffQuestionAnalysisRun).filter_by(id=run_id).first()
                if rr:
                    rr.status = "failed"
                    rr.error = f"generation stage: {gen['error']}"
                    rr.completed_at = datetime.utcnow()
                    d.commit()
                return
            recon = reconcile_candidates(d, run_id)
            rr = d.query(DiffQuestionAnalysisRun).filter_by(id=run_id).first()
            if not rr:
                return
            if isinstance(recon, dict) and recon.get("error"):
                rr.status = "failed"
                rr.error = f"reconcile stage: {recon['error']}"
            else:
                rr.status = "complete"
            rr.completed_at = datetime.utcnow()
            d.commit()
        except Exception as e:  # noqa: BLE001
            logger.exception(f"questions-only run {run_id} crashed: {e}")
        finally:
            d.close()

    background.add_task(_runner_skip_diff, run.id)
    return _run_to_dict(run)


@router.get("/runs")
def list_runs(
    proposal_id: int = Query(...),
    limit: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    rows = (db.query(DiffQuestionAnalysisRun)
            .filter(DiffQuestionAnalysisRun.proposal_id == proposal_id)
            .order_by(DiffQuestionAnalysisRun.created_at.desc())
            .limit(limit).all())
    return {"runs": [_run_to_dict(r) for r in rows]}


@router.get("/runs/{run_id}")
def get_run(
    run_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    r = db.query(DiffQuestionAnalysisRun).filter(
        DiffQuestionAnalysisRun.id == run_id).first()
    if not r:
        raise HTTPException(404, "Run not found")
    return _run_to_dict(r)


@router.get("/runs/{run_id}/candidates")
def list_candidates(
    run_id: int,
    action: Optional[str] = Query(None,
        description="Filter: added | replaces | updates | duplicate"),
    only_inferences: bool = Query(False),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """List candidate questions generated by this analysis run, with their
    reconciliation actions, the related diff rows, and any
    superseded/replaced existing question pointers resolved."""
    r = db.query(DiffQuestionAnalysisRun).filter(
        DiffQuestionAnalysisRun.id == run_id).first()
    if not r:
        raise HTTPException(404, "Run not found")
    if not r.diff_run_id:
        return {"run_id": run_id, "candidates": [], "warning": "no diff_run_id yet"}

    q = (db.query(RfpQuestion)
         .filter(RfpQuestion.proposal_id == r.proposal_id)
         .filter(RfpQuestion.diff_run_id == r.diff_run_id))
    if action:
        q = q.filter(RfpQuestion.reconciliation_action == action)
    if only_inferences:
        q = q.filter(RfpQuestion.inference_flag.is_(True))
    candidates = q.order_by(RfpQuestion.created_at).all()

    # Resolve replaces/supersedes targets in one pass
    referenced_ids = set()
    for c in candidates:
        if c.replaces_question_id:
            referenced_ids.add(c.replaces_question_id)
        if c.superseded_by_question_id:
            referenced_ids.add(c.superseded_by_question_id)
    referenced: Dict[int, Dict[str, Any]] = {}
    if referenced_ids:
        for q2 in db.query(RfpQuestion).filter(
                RfpQuestion.id.in_(referenced_ids)).all():
            referenced[q2.id] = {
                "id": q2.id,
                "question_text": q2.question_text,
                "category": q2.category,
                "priority": q2.priority,
                "status": q2.status,
            }

    # Resolve diff rows for context
    diff_row_ids: List[int] = []
    for c in candidates:
        try:
            diff_row_ids.extend(json.loads(c.diff_row_ids) if c.diff_row_ids else [])
        except (json.JSONDecodeError, TypeError):
            pass
    diff_row_ids = list(set(diff_row_ids))
    diff_rows: Dict[int, Dict[str, Any]] = {}
    if diff_row_ids:
        for d in db.query(RfpRequirementDiff).filter(
                RfpRequirementDiff.id.in_(diff_row_ids)).all():
            diff_rows[d.id] = {
                "id": d.id,
                "status": d.status,
                "change_summary": d.change_summary,
                "impact_severity": d.impact_severity,
                "impact_blurb": d.impact_blurb,
            }

    # Resolve coupling findings — second-order effects keyed to the diff_rows
    from ..models import DiffCouplingFinding, RfpRequirement
    couplings_by_source_diff_row: Dict[int, List[Dict[str, Any]]] = {}
    if diff_row_ids:
        coupling_rows = (db.query(DiffCouplingFinding)
                         .filter(DiffCouplingFinding.diff_run_id == r.diff_run_id)
                         .filter(DiffCouplingFinding.source_diff_row_id.in_(diff_row_ids))
                         .filter(DiffCouplingFinding.status == "open")
                         .all())
        # Resolve affected requirement summaries in one query
        affected_ids = {cr.affected_target_requirement_id for cr in coupling_rows}
        affected_meta: Dict[int, Dict[str, Any]] = {}
        if affected_ids:
            for ar in db.query(RfpRequirement).filter(
                    RfpRequirement.id.in_(affected_ids)).all():
                affected_meta[ar.id] = {
                    "id": ar.id, "section_id": ar.section_id,
                    "title": ar.title,
                    "source_page": ar.source_page,
                }
        for cr in coupling_rows:
            couplings_by_source_diff_row.setdefault(cr.source_diff_row_id, []).append({
                "id": cr.id,
                "affected_requirement": affected_meta.get(cr.affected_target_requirement_id),
                "coupling_kind": cr.coupling_kind,
                "severity": cr.severity,
                "summary": cr.summary,
                "detail": cr.detail,
                "suggested_question": cr.suggested_question,
                "similarity": cr.coupling_similarity,
            })

    out = []
    for c in candidates:
        cd = _question_to_dict(c)
        cd["replaces_question"] = (
            referenced.get(c.replaces_question_id) if c.replaces_question_id else None
        )
        cd["superseded_by_question"] = (
            referenced.get(c.superseded_by_question_id) if c.superseded_by_question_id else None
        )
        cd["related_diff_rows"] = [diff_rows[i] for i in cd["diff_row_ids"]
                                    if i in diff_rows]
        # Attach coupling findings keyed to any of this candidate's diff rows
        couplings: List[Dict[str, Any]] = []
        for drid in cd["diff_row_ids"]:
            couplings.extend(couplings_by_source_diff_row.get(drid, []))
        cd["coupling_findings"] = couplings
        out.append(cd)

    return {
        "run_id": run_id,
        "diff_run_id": r.diff_run_id,
        "candidates": out,
        "total": len(out),
    }


@router.get("/runs/{run_id}/couplings")
def list_couplings(
    run_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """List all coupling findings (second-order effects) for this diff run."""
    from ..models import DiffCouplingFinding, RfpRequirement
    r = db.query(DiffQuestionAnalysisRun).filter(
        DiffQuestionAnalysisRun.id == run_id).first()
    if not r or not r.diff_run_id:
        raise HTTPException(404, "Run not found or has no diff yet")
    rows = (db.query(DiffCouplingFinding)
            .filter(DiffCouplingFinding.diff_run_id == r.diff_run_id)
            .order_by(DiffCouplingFinding.created_at.desc())
            .all())
    affected_ids = {x.affected_target_requirement_id for x in rows}
    affected: Dict[int, Dict[str, Any]] = {}
    if affected_ids:
        for ar in db.query(RfpRequirement).filter(
                RfpRequirement.id.in_(affected_ids)).all():
            affected[ar.id] = {
                "id": ar.id, "section_id": ar.section_id,
                "title": ar.title, "source_page": ar.source_page,
            }
    return {
        "run_id": run_id,
        "diff_run_id": r.diff_run_id,
        "total": len(rows),
        "couplings": [{
            "id": x.id,
            "source_diff_row_id": x.source_diff_row_id,
            "affected_requirement": affected.get(x.affected_target_requirement_id),
            "coupling_kind": x.coupling_kind,
            "severity": x.severity,
            "summary": x.summary,
            "detail": x.detail,
            "suggested_question": x.suggested_question,
            "similarity": x.coupling_similarity,
            "status": x.status,
            "created_at": x.created_at.isoformat() if x.created_at else None,
        } for x in rows],
    }


@router.post("/candidates/{question_id}/accept")
def accept_candidate(
    question_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Promote a diff-derived candidate to ``status='reviewed'`` so it
    enters the normal questions approval workflow."""
    q = db.query(RfpQuestion).filter(RfpQuestion.id == question_id).first()
    if not q:
        raise HTTPException(404, "Question not found")
    if not q.source_kind or not q.source_kind.startswith("diff_"):
        raise HTTPException(400, "Not a diff-derived candidate")
    if q.status == "rejected":
        # Un-reject and accept
        q.status = "reviewed"
    else:
        q.status = "reviewed"
    q.reviewer_user_id = getattr(current_user, "id", None)
    q.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(q)
    return _question_to_dict(q)


class RejectBody(BaseModel):
    review_notes: Optional[str] = None


@router.post("/candidates/{question_id}/reject")
def reject_candidate(
    question_id: int,
    body: RejectBody = Body(default_factory=RejectBody),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Reject a diff-derived candidate. If the candidate had ``replaces``
    a prior question, the prior question is restored to its previous
    state (status=draft) since the supersession is being undone."""
    q = db.query(RfpQuestion).filter(RfpQuestion.id == question_id).first()
    if not q:
        raise HTTPException(404, "Question not found")
    if not q.source_kind or not q.source_kind.startswith("diff_"):
        raise HTTPException(400, "Not a diff-derived candidate")
    q.status = "rejected"
    q.reviewer_user_id = getattr(current_user, "id", None)
    if body.review_notes:
        q.review_notes = (
            (q.review_notes or "") + "\n" + body.review_notes
        ).strip()
    q.updated_at = datetime.utcnow()

    # Undo the supersession on a replaced question (restore to draft).
    if q.replaces_question_id and q.reconciliation_action == "replaces":
        prior = db.query(RfpQuestion).filter(
            RfpQuestion.id == q.replaces_question_id).first()
        if prior and prior.superseded_by_question_id == q.id:
            prior.superseded_by_question_id = None
            if prior.status == "rejected":
                prior.status = "draft"
            prior.review_notes = (
                (prior.review_notes or "")
                + f"\nSupersession undone (Q#{q.id} rejected)."
            ).strip()
    db.commit()
    db.refresh(q)
    return _question_to_dict(q)


class EditBody(BaseModel):
    question_text: Optional[str] = None
    rationale: Optional[str] = None
    category: Optional[str] = None
    priority: Optional[str] = None


@router.patch("/candidates/{question_id}")
def edit_candidate(
    question_id: int,
    body: EditBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Edit a diff-derived candidate before accepting (the standard
    questions PATCH endpoint also works, but this one is candidate-aware
    and only allows a small whitelist of fields the reviewer should
    touch in this workflow)."""
    q = db.query(RfpQuestion).filter(RfpQuestion.id == question_id).first()
    if not q:
        raise HTTPException(404, "Question not found")
    if not q.source_kind or not q.source_kind.startswith("diff_"):
        raise HTTPException(400, "Not a diff-derived candidate")
    if body.question_text is not None:
        text = body.question_text.strip()
        if text and not text.endswith("?"):
            text += "?"
        q.question_text = text[:4000]
    if body.rationale is not None:
        q.rationale = body.rationale.strip()[:2000] or None
    if body.category is not None:
        q.category = body.category.strip().lower() or q.category
    if body.priority is not None:
        q.priority = body.priority.strip().lower() or q.priority
    q.reviewer_user_id = getattr(current_user, "id", None)
    q.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(q)
    return _question_to_dict(q)
