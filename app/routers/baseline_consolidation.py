"""Baseline consolidation endpoints.

  POST   /api/baseline-consolidation/runs
  GET    /api/baseline-consolidation/runs
  GET    /api/baseline-consolidation/runs/{id}
  GET    /api/baseline-consolidation/runs/{id}/requirements
  POST   /api/baseline-consolidation/runs/{id}/materialize
  DELETE /api/baseline-consolidation/runs/{id}
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..database import SessionLocal, get_db
from ..models import (
    ConsolidatedBaselineRun, ConsolidatedRequirement, IngestedDocument,
    RfpRequirement, User,
)
from ..services.baseline_consolidator import (
    CONSOLIDATION_DOC_PREFIX, execute_consolidation,
    materialize_baseline_as_documents, start_consolidation_run,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ─────────────────────────────────────────────────────────────────────
# Background runner
# ─────────────────────────────────────────────────────────────────────

_RUNNING: Dict[int, str] = {}
_RUNNING_LOCK = threading.Lock()


def _runner(run_id: int) -> None:
    db = SessionLocal()
    try:
        with _RUNNING_LOCK:
            _RUNNING[run_id] = "running"
        execute_consolidation(db, run_id)
    except Exception as e:  # noqa: BLE001
        logger.exception(f"consolidation run {run_id} crashed: {e}")
    finally:
        with _RUNNING_LOCK:
            _RUNNING.pop(run_id, None)
        db.close()


# ─────────────────────────────────────────────────────────────────────
# Schemas + serializers
# ─────────────────────────────────────────────────────────────────────

class StartBody(BaseModel):
    master_document_id: int
    amendment_document_ids: List[int]
    label: Optional[str] = None


def _run_to_dict(r: ConsolidatedBaselineRun) -> Dict[str, Any]:
    try:
        amend_ids = json.loads(r.amendment_document_ids) if r.amendment_document_ids else []
    except (json.JSONDecodeError, TypeError):
        amend_ids = []
    return {
        "id": r.id,
        "label": r.label,
        "master_document_id": r.master_document_id,
        "amendment_document_ids": amend_ids,
        "status": r.status,
        "progress_note": r.progress_note,
        "total_master_requirements": r.total_master_requirements or 0,
        "total_amendment_actions": r.total_amendment_actions or 0,
        "actions_modified": r.actions_modified or 0,
        "actions_added": r.actions_added or 0,
        "actions_removed": r.actions_removed or 0,
        "actions_skipped": r.actions_skipped or 0,
        "actions_unchanged": r.actions_unchanged or 0,
        "error": r.error,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "completed_at": r.completed_at.isoformat() if r.completed_at else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def _consolidated_to_dict(c: ConsolidatedRequirement) -> Dict[str, Any]:
    try:
        history = json.loads(c.amendment_history) if c.amendment_history else []
    except (json.JSONDecodeError, TypeError):
        history = []
    return {
        "id": c.id,
        "consolidation_run_id": c.consolidation_run_id,
        "requirement_id": c.requirement_id,
        "section_id": c.section_id,
        "category": c.category,
        "priority": c.priority,
        "title": c.title,
        "description": c.description,
        "source_text": c.source_text,
        "source_page": c.source_page,
        "original_requirement_id": c.original_requirement_id,
        "source_document_id": c.source_document_id,
        "is_removed": bool(c.is_removed),
        "is_added_by_amendment": bool(c.is_added_by_amendment),
        "amendment_history": history,
        "amendment_count": len(history),
    }


# ─────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────

class SuggestOrderBody(BaseModel):
    document_ids: List[int]
    use_llm_fallback: bool = True


@router.post("/suggest-order")
def suggest_order(
    body: SuggestOrderBody,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Extract each document's own issuance/effective date (from the body
    text first, then filename, then LLM fallback) and return them sorted
    chronologically. UI uses this to auto-fill the amendment-order
    field so the consolidator processes amendments in the order they
    were actually issued."""
    from ..services.document_date_extractor import suggest_amendment_order
    if not body.document_ids:
        raise HTTPException(400, "document_ids cannot be empty")
    # Verify they all exist
    found = {d.id for d in db.query(IngestedDocument)
             .filter(IngestedDocument.id.in_(body.document_ids)).all()}
    missing = [d for d in body.document_ids if d not in found]
    if missing:
        raise HTTPException(400, f"Document ids not found: {missing}")
    return suggest_amendment_order(
        db, body.document_ids, use_llm_fallback=body.use_llm_fallback)


@router.post("/runs")
def create_run(
    body: StartBody,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Start a consolidation run in the background. Returns the run id;
    poll /runs/{id} for status."""
    if not body.amendment_document_ids:
        raise HTTPException(400, "amendment_document_ids cannot be empty")
    # Verify all docs exist
    needed = [body.master_document_id] + list(body.amendment_document_ids)
    found = {d.id for d in db.query(IngestedDocument)
             .filter(IngestedDocument.id.in_(needed)).all()}
    missing = [d for d in needed if d not in found]
    if missing:
        raise HTTPException(400, f"Document ids not found: {missing}")
    run = start_consolidation_run(
        db,
        master_document_id=body.master_document_id,
        amendment_document_ids=body.amendment_document_ids,
        label=body.label,
        created_by_user_id=getattr(current_user, "id", None),
    )
    background.add_task(_runner, run.id)
    return _run_to_dict(run)


@router.get("/runs")
def list_runs(
    limit: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    rows = (db.query(ConsolidatedBaselineRun)
            .order_by(ConsolidatedBaselineRun.created_at.desc())
            .limit(limit).all())
    return {"runs": [_run_to_dict(r) for r in rows]}


@router.get("/runs/{run_id}")
def get_run(
    run_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    r = db.query(ConsolidatedBaselineRun).filter(
        ConsolidatedBaselineRun.id == run_id).first()
    if not r:
        raise HTTPException(404, "Run not found")
    return _run_to_dict(r)


@router.get("/runs/{run_id}/requirements")
def list_requirements(
    run_id: int,
    include_removed: bool = Query(False),
    section_id: Optional[str] = Query(None),
    only_amended: bool = Query(False, description=
                                "Only show rows actually touched by amendments"),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    r = db.query(ConsolidatedBaselineRun).filter(
        ConsolidatedBaselineRun.id == run_id).first()
    if not r:
        raise HTTPException(404, "Run not found")
    q = (db.query(ConsolidatedRequirement)
         .filter(ConsolidatedRequirement.consolidation_run_id == run_id))
    if not include_removed:
        q = q.filter(ConsolidatedRequirement.is_removed.is_(False))
    if section_id:
        q = q.filter(ConsolidatedRequirement.section_id == section_id)
    if only_amended:
        q = q.filter(ConsolidatedRequirement.amendment_history.isnot(None))
    total = q.count()
    rows = (q.order_by(ConsolidatedRequirement.section_id,
                        ConsolidatedRequirement.id)
            .offset(offset).limit(limit).all())
    return {
        "run_id": run_id,
        "total": total,
        "limit": limit,
        "offset": offset,
        "requirements": [_consolidated_to_dict(c) for c in rows],
    }


@router.post("/runs/{run_id}/materialize")
def materialize_run(
    run_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Materialize the consolidated baseline as a synthetic
    IngestedDocument + RfpRequirement rows so the existing diff endpoint
    can consume it via baseline_document_ids=[<synthetic_doc_id>]."""
    result = materialize_baseline_as_documents(db, run_id)
    if isinstance(result, dict) and result.get("error"):
        raise HTTPException(400, result["error"])
    return result


@router.delete("/runs/{run_id}")
def delete_run(
    run_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Delete a consolidation run plus its consolidated requirements +
    any synthetic materialized document. Does NOT delete the source
    master/amendment documents."""
    r = db.query(ConsolidatedBaselineRun).filter(
        ConsolidatedBaselineRun.id == run_id).first()
    if not r:
        raise HTTPException(404, "Run not found")
    # Drop materialized synthetic doc if any
    syn_filename = f"{CONSOLIDATION_DOC_PREFIX}{run_id}"
    syn = (db.query(IngestedDocument)
           .filter(IngestedDocument.filename == syn_filename).first())
    if syn:
        (db.query(RfpRequirement)
         .filter(RfpRequirement.document_id == syn.id)
         .delete(synchronize_session=False))
        db.delete(syn)
    # Drop consolidated requirements
    (db.query(ConsolidatedRequirement)
     .filter(ConsolidatedRequirement.consolidation_run_id == run_id)
     .delete(synchronize_session=False))
    db.delete(r)
    db.commit()
    return {"ok": True, "id": run_id}
