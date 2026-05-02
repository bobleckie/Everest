"""Competitor intelligence endpoints — dossier generation, predictions, persona management."""
import json
import logging
import threading
import uuid
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Any, Dict, List, Optional

from ..database import get_db, SessionLocal
from ..models import (
    Competitor, CompetitorDossier, CompetitorPrediction, Persona, User,
    ScoringRubric, ScoringRubricSection, IngestedDocument,
    CompetitorEvidence, CompetitorThread, CompetitorTimelineEvent,
)
from ..auth import get_current_user, get_current_admin_user
from ..services.competitor_analyst import (
    generate_competitor_persona, predict_competitor_response,
    DOSSIER_CATEGORIES,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Dossier ──────────────────────────────────────────────────────────

@router.get("/competitors/{competitor_id}/dossier")
def get_dossier(
    competitor_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get the full intelligence dossier for a competitor."""
    comp = db.query(Competitor).filter(Competitor.id == competitor_id).first()
    if not comp:
        raise HTTPException(status_code=404, detail="Competitor not found")

    entries = (
        db.query(CompetitorDossier)
        .filter(CompetitorDossier.competitor_id == competitor_id)
        .order_by(CompetitorDossier.category)
        .all()
    )
    return {
        "competitor_id": competitor_id,
        "competitor_name": comp.name,
        "categories": DOSSIER_CATEGORIES,
        "entries": [
            {
                "id": e.id,
                "category": e.category,
                "title": e.title,
                "content": e.content,
                "source": e.source,
                "confidence": e.confidence,
                "verified": e.verified,
                "updated_at": e.updated_at.isoformat() if e.updated_at else None,
            }
            for e in entries
        ],
    }


@router.post("/competitors/{competitor_id}/dossier/generate", deprecated=True)
def generate_dossier_endpoint(competitor_id: int):
    """RETIRED — use POST /competitors/{id}/intelligence-run instead.

    The previous synchronous dossier path has been replaced by the unified
    intelligence run that includes web research, thread synthesis, and a
    chronological timeline. Returns HTTP 410.
    """
    raise HTTPException(
        status_code=410,
        detail={
            "message": "POST /dossier/generate is retired. Use the unified intelligence run instead.",
            "new_path": f"/api/intelligence/competitors/{competitor_id}/intelligence-run",
        },
    )


@router.post("/competitors/{competitor_id}/dossier/generate-async", deprecated=True)
def generate_dossier_async(competitor_id: int):
    """RETIRED — use POST /competitors/{id}/intelligence-run instead.

    The new endpoint runs the full pipeline (every connector + threads +
    timeline + threads-aware category drafts) and emits a 16+ phase progress
    plan via GET /intelligence-run/{job_id}. Returns HTTP 410.
    """
    raise HTTPException(
        status_code=410,
        detail={
            "message": "POST /dossier/generate-async is retired. Use the unified intelligence run instead.",
            "new_path": f"/api/intelligence/competitors/{competitor_id}/intelligence-run",
        },
    )


# ─────────────────────────────────────────────────────────────────────
# Unified Intelligence Run — replaces both /dossier/generate-async and
# /api/orchestrator/start-vendor-analysis with a single end-to-end pipeline
# (web research + thread synthesis + threads-aware category drafts).
# ─────────────────────────────────────────────────────────────────────

# Separate job tracker for the unified pipeline. The phase plan is much
# longer than the old 8-category run (16+ phases grouped into Setup /
# Research / Synthesis / Drafting), so keeping the two trackers separate
# avoids confusing the existing dossier-progress UI.
_INTEL_JOBS: dict = {}
_INTEL_JOBS_LOCK = threading.Lock()


def _make_intel_job(competitor_id: int) -> str:
    """Create a new intel-run job seeded with the static phase plan from the
    orchestrator. The UI will render whatever phases this list contains, so
    adding a new connector / pass means appending one entry there — zero
    frontend change."""
    from ..services.intelligence.orchestrator import build_phase_plan
    plan = build_phase_plan()
    job_id = f"intel-{competitor_id}-{uuid.uuid4().hex[:8]}"
    with _INTEL_JOBS_LOCK:
        _INTEL_JOBS[job_id] = {
            "job_id": job_id,
            "competitor_id": competitor_id,
            "status": "queued",                     # queued | running | done | error
            "started_at": None,
            "completed_at": None,
            "error": None,
            "phases": [
                {**p,
                 "status": "queued",
                 "started_at": None, "completed_at": None,
                 "detail": None}
                for p in plan
            ],
            "summary": None,
        }
    return job_id


def _intel_progress_cb(job_id: str):
    """Progress callback bound to a specific intel-run job_id. The
    orchestrator emits four event types: phase_started, phase_completed,
    phase_failed, all_done."""
    def _find(job, phase_id):
        for p in job["phases"]:
            if p["id"] == phase_id:
                return p
        return None

    def cb(event: str, payload: dict) -> None:
        with _INTEL_JOBS_LOCK:
            job = _INTEL_JOBS.get(job_id)
            if not job:
                return
            now = datetime.utcnow().isoformat()
            if event == "phase_started":
                phase = _find(job, payload.get("phase_id"))
                if phase:
                    phase["status"] = "in_progress"
                    phase["started_at"] = now
                # First phase moves whole-job to running.
                if job["status"] == "queued":
                    job["status"] = "running"
                    job["started_at"] = now
            elif event == "phase_completed":
                phase = _find(job, payload.get("phase_id"))
                if phase:
                    phase["status"] = "complete"
                    phase["completed_at"] = now
                    detail = {k: v for k, v in payload.items() if k != "phase_id"}
                    phase["detail"] = detail or None
            elif event == "phase_failed":
                phase = _find(job, payload.get("phase_id"))
                if phase:
                    phase["status"] = "failed"
                    phase["completed_at"] = now
                    phase["detail"] = {"error": payload.get("error")}
            elif event == "all_done":
                job["status"] = "done"
                job["completed_at"] = now
                job["summary"] = {k: v for k, v in payload.items()}
    return cb


def _run_intel_job(job_id: str, competitor_id: int) -> None:
    """Background task entry point. Owns its own DB session."""
    from ..services.intelligence.orchestrator import run_intelligence
    db = SessionLocal()
    try:
        run_intelligence(db, competitor_id, progress_cb=_intel_progress_cb(job_id))
    except Exception as e:  # noqa: BLE001
        logger.exception(f"Intelligence job {job_id} failed: {e}")
        with _INTEL_JOBS_LOCK:
            job = _INTEL_JOBS.get(job_id)
            if job:
                job["status"] = "error"
                job["error"] = str(e)
                job["completed_at"] = datetime.utcnow().isoformat()
                for p in job["phases"]:
                    if p["status"] == "in_progress":
                        p["status"] = "failed"
                        p["completed_at"] = job["completed_at"]
    finally:
        db.close()


@router.post("/competitors/{competitor_id}/intelligence-run")
def start_intelligence_run(
    competitor_id: int,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Kick off the unified intelligence run for a competitor.

    This is the ONLY entry point for new dossier generation going forward.
    It runs every connector (CourtListener, SEC EDGAR, OpenCorporates, GLEIF,
    UK Contracts Finder, Wikidata, Google Places, internal docs, internal
    news, Anthropic web_search), synthesizes threads + a chronological
    timeline, and drafts the 8 dossier categories with full evidence
    citations. Polling endpoint is GET /intelligence-run/{job_id}.
    """
    comp = db.query(Competitor).filter(Competitor.id == competitor_id).first()
    if not comp:
        raise HTTPException(status_code=404, detail="Competitor not found")
    job_id = _make_intel_job(competitor_id)
    background.add_task(_run_intel_job, job_id, competitor_id)
    return {
        "job_id": job_id,
        "competitor_id": competitor_id,
        "competitor_name": comp.name,
        "status": "queued",
        "phase_count": len(_INTEL_JOBS[job_id]["phases"]),
    }


@router.get("/intelligence-run/{job_id}")
def get_intelligence_run(
    job_id: str,
    _user: User = Depends(get_current_user),
):
    """Poll the status of an intelligence run."""
    with _INTEL_JOBS_LOCK:
        job = _INTEL_JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return {**job, "phases": [dict(p) for p in job["phases"]]}


# ── Read endpoints for Threads + Timeline + Evidence tabs ────────────

def _row_to_evidence_dict(row: CompetitorEvidence) -> Dict[str, Any]:
    try:
        payload = json.loads(row.payload_json) if row.payload_json else {}
    except (json.JSONDecodeError, TypeError):
        payload = {}
    return {
        "id": row.id,
        "source_connector": row.source_connector,
        "source_ref": row.source_ref,
        "citation_url": row.citation_url,
        "title": row.title,
        "snippet": row.snippet,
        "claim_class": row.claim_class,
        "event_date": row.event_date,
        "jurisdiction": row.jurisdiction,
        "amount_usd": row.amount_usd,
        "confidence": row.confidence,
        "fetched_at": row.fetched_at.isoformat() if row.fetched_at else None,
        "payload": payload,
    }


def _row_to_thread_dict(row: CompetitorThread) -> Dict[str, Any]:
    try:
        ev_ids = json.loads(row.evidence_ids_json or "[]")
    except (json.JSONDecodeError, TypeError):
        ev_ids = []
    try:
        cats = json.loads(row.category_tags_json or "[]")
    except (json.JSONDecodeError, TypeError):
        cats = []
    return {
        "id": row.id,
        "slug": row.slug,
        "title": row.title,
        "headline": row.headline,
        "narrative_markdown": row.narrative_markdown,
        "evidence_ids": ev_ids,
        "category_tags": cats,
        "confidence": row.confidence,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _row_to_timeline_dict(row: CompetitorTimelineEvent) -> Dict[str, Any]:
    try:
        ev_ids = json.loads(row.evidence_ids_json or "[]")
    except (json.JSONDecodeError, TypeError):
        ev_ids = []
    return {
        "id": row.id,
        "event_date": row.event_date,
        "event_type": row.event_type,
        "title": row.title,
        "description": row.description,
        "jurisdiction": row.jurisdiction,
        "amount_usd": row.amount_usd,
        "evidence_ids": ev_ids,
        "thread_id": row.thread_id,
        "confidence": row.confidence,
    }


@router.get("/competitors/{competitor_id}/evidence")
def list_evidence(
    competitor_id: int,
    claim_class: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """List evidence cards for a competitor. Optional filter by claim_class."""
    q = db.query(CompetitorEvidence).filter(
        CompetitorEvidence.competitor_id == competitor_id
    )
    if claim_class:
        q = q.filter(CompetitorEvidence.claim_class == claim_class)
    q = q.order_by(CompetitorEvidence.event_date.desc().nullslast(),
                   CompetitorEvidence.id.desc())
    rows = q.all()
    return {
        "competitor_id": competitor_id,
        "count": len(rows),
        "evidence": [_row_to_evidence_dict(r) for r in rows],
    }


@router.get("/competitors/{competitor_id}/threads")
def list_threads(
    competitor_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """List synthesized threads for a competitor."""
    rows = (
        db.query(CompetitorThread)
        .filter(CompetitorThread.competitor_id == competitor_id)
        .order_by(CompetitorThread.confidence, CompetitorThread.title)
        .all()
    )
    return {
        "competitor_id": competitor_id,
        "count": len(rows),
        "threads": [_row_to_thread_dict(r) for r in rows],
    }


@router.get("/competitors/{competitor_id}/timeline")
def list_timeline(
    competitor_id: int,
    event_type: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """List the chronological event timeline for a competitor."""
    q = db.query(CompetitorTimelineEvent).filter(
        CompetitorTimelineEvent.competitor_id == competitor_id
    )
    if event_type:
        q = q.filter(CompetitorTimelineEvent.event_type == event_type)
    q = q.order_by(CompetitorTimelineEvent.event_date.asc())
    rows = q.all()
    return {
        "competitor_id": competitor_id,
        "count": len(rows),
        "events": [_row_to_timeline_dict(r) for r in rows],
    }


class DossierEntryUpdate(BaseModel):
    content: Optional[str] = None
    confidence: Optional[str] = None
    verified: Optional[bool] = None

@router.put("/dossier/{entry_id}")
def update_dossier_entry(
    entry_id: int,
    payload: DossierEntryUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Manually update a dossier entry (edit content, verify, change confidence)."""
    entry = db.query(CompetitorDossier).filter(CompetitorDossier.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Dossier entry not found")
    if payload.content is not None:
        entry.content = payload.content
    if payload.confidence is not None:
        entry.confidence = payload.confidence
    if payload.verified is not None:
        entry.verified = payload.verified
        if payload.verified:
            entry.verified_by = current_user.id
            from datetime import datetime
            entry.verified_at = datetime.utcnow()
    db.commit()
    return {"id": entry.id, "status": "updated"}


@router.delete("/dossier/{entry_id}")
def delete_dossier_entry(
    entry_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a single dossier entry — use when an intel item is stale or wrong."""
    entry = db.query(CompetitorDossier).filter(CompetitorDossier.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Dossier entry not found")
    db.delete(entry)
    db.commit()
    return {"id": entry_id, "status": "deleted"}


@router.delete("/competitors/{competitor_id}/dossier")
def clear_competitor_dossier(
    competitor_id: int,
    category: Optional[str] = Query(None, description="If supplied, only delete entries in this category"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete all (or one-category's) dossier entries for a competitor.

    Useful for wiping stale analysis so a regeneration starts clean.
    """
    comp = db.query(Competitor).filter(Competitor.id == competitor_id).first()
    if not comp:
        raise HTTPException(status_code=404, detail="Competitor not found")
    q = db.query(CompetitorDossier).filter(CompetitorDossier.competitor_id == competitor_id)
    if category:
        q = q.filter(CompetitorDossier.category == category)
    deleted = q.delete(synchronize_session=False)
    db.commit()
    return {"competitor_id": competitor_id, "deleted": deleted, "category": category}


# ── Competitor Personas ──────────────────────────────────────────────

@router.get("/competitors/{competitor_id}/persona")
def get_competitor_persona(
    competitor_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get the competitor-writer persona for a competitor."""
    comp = db.query(Competitor).filter(Competitor.id == competitor_id).first()
    if not comp:
        raise HTTPException(status_code=404, detail="Competitor not found")
    persona = db.query(Persona).filter(
        Persona.persona_type == "competitor_writer",
        Persona.competitor_id == competitor_id,
    ).first()
    if not persona:
        return {"competitor_id": competitor_id, "persona": None, "message": "No persona generated yet"}
    return {
        "competitor_id": competitor_id,
        "persona": {
            "id": persona.id,
            "name": persona.name,
            "description": persona.description,
            "system_prompt": persona.system_prompt,
            "created_at": persona.created_at.isoformat() if persona.created_at else None,
            "updated_at": persona.updated_at.isoformat() if persona.updated_at else None,
        },
    }


@router.post("/competitors/{competitor_id}/persona/generate")
def generate_persona_endpoint(
    competitor_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Generate or update the competitor-writer persona from the dossier."""
    comp = db.query(Competitor).filter(Competitor.id == competitor_id).first()
    if not comp:
        raise HTTPException(status_code=404, detail="Competitor not found")
    persona = generate_competitor_persona(db, competitor_id)
    return {
        "competitor_id": competitor_id,
        "persona_id": persona.id if persona else None,
        "persona_name": persona.name if persona else None,
        "status": "generated",
    }


# ── Predictions ──────────────────────────────────────────────────────

@router.get("/competitors/{competitor_id}/predictions")
def get_predictions(
    competitor_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get all predicted responses for a competitor."""
    comp = db.query(Competitor).filter(Competitor.id == competitor_id).first()
    if not comp:
        raise HTTPException(status_code=404, detail="Competitor not found")
    preds = (
        db.query(CompetitorPrediction)
        .filter(CompetitorPrediction.competitor_id == competitor_id)
        .order_by(CompetitorPrediction.section_id)
        .all()
    )
    return {
        "competitor_id": competitor_id,
        "predictions": [
            {
                "id": p.id,
                "section_id": p.section_id,
                "predicted_response": p.predicted_response,
                "reasoning": p.reasoning,
                "confidence_score": p.confidence_score,
                "model_used": p.model_used,
                "updated_at": p.updated_at.isoformat() if p.updated_at else None,
            }
            for p in preds
        ],
    }


class PredictRequest(BaseModel):
    section_ids: Optional[List[str]] = None  # None = all scored sections

@router.post("/competitors/{competitor_id}/predictions/generate")
def generate_predictions_endpoint(
    competitor_id: int,
    payload: PredictRequest = PredictRequest(),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Generate predicted responses for one or all sections."""
    comp = db.query(Competitor).filter(Competitor.id == competitor_id).first()
    if not comp:
        raise HTTPException(status_code=404, detail="Competitor not found")

    section_ids = payload.section_ids
    if not section_ids:
        # Get all scored sections from rubric
        rubric = db.query(ScoringRubric).filter(ScoringRubric.is_default == True).first()
        if rubric:
            sections = (
                db.query(ScoringRubricSection)
                .filter(ScoringRubricSection.rubric_id == rubric.id)
                .order_by(ScoringRubricSection.sort_order)
                .all()
            )
            section_ids = [s.section_id for s in sections]
        else:
            raise HTTPException(status_code=500, detail="No scoring rubric found")

    results = {}
    for sid in section_ids:
        pred = predict_competitor_response(db, competitor_id, sid)
        results[sid] = {
            "id": pred.id if pred else None,
            "status": "generated" if pred else "failed",
            "confidence": pred.confidence_score if pred else 0,
        }

    return {"competitor_id": competitor_id, "generated": len(results), "results": results}


@router.delete("/predictions/{prediction_id}")
def delete_prediction(
    prediction_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a single predicted response."""
    pred = db.query(CompetitorPrediction).filter(CompetitorPrediction.id == prediction_id).first()
    if not pred:
        raise HTTPException(status_code=404, detail="Prediction not found")
    db.delete(pred)
    db.commit()
    return {"id": prediction_id, "status": "deleted"}


@router.delete("/competitors/{competitor_id}/predictions")
def clear_competitor_predictions(
    competitor_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete all predicted responses for a competitor."""
    comp = db.query(Competitor).filter(Competitor.id == competitor_id).first()
    if not comp:
        raise HTTPException(status_code=404, detail="Competitor not found")
    deleted = (
        db.query(CompetitorPrediction)
        .filter(CompetitorPrediction.competitor_id == competitor_id)
        .delete(synchronize_session=False)
    )
    db.commit()
    return {"competitor_id": competitor_id, "deleted": deleted}


@router.delete("/competitors/{competitor_id}/persona")
def clear_competitor_persona(
    competitor_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete the writer persona for a competitor so it can be rebuilt from scratch."""
    comp = db.query(Competitor).filter(Competitor.id == competitor_id).first()
    if not comp:
        raise HTTPException(status_code=404, detail="Competitor not found")
    deleted = (
        db.query(Persona)
        .filter(Persona.persona_type == "competitor_writer", Persona.competitor_id == competitor_id)
        .delete(synchronize_session=False)
    )
    db.commit()
    return {"competitor_id": competitor_id, "deleted": deleted}


@router.delete("/competitors/{competitor_id}/analysis")
def clear_all_competitor_analysis(
    competitor_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Wipe all AI-generated analysis for a competitor (dossier + persona + predictions).

    Does NOT delete the competitor record itself or any ingested documents — those
    stay in place so you can regenerate analysis against the same source material.
    """
    comp = db.query(Competitor).filter(Competitor.id == competitor_id).first()
    if not comp:
        raise HTTPException(status_code=404, detail="Competitor not found")
    dossier_deleted = (
        db.query(CompetitorDossier)
        .filter(CompetitorDossier.competitor_id == competitor_id)
        .delete(synchronize_session=False)
    )
    predictions_deleted = (
        db.query(CompetitorPrediction)
        .filter(CompetitorPrediction.competitor_id == competitor_id)
        .delete(synchronize_session=False)
    )
    persona_deleted = (
        db.query(Persona)
        .filter(Persona.persona_type == "competitor_writer", Persona.competitor_id == competitor_id)
        .delete(synchronize_session=False)
    )
    db.commit()
    return {
        "competitor_id": competitor_id,
        "dossier_entries_deleted": dossier_deleted,
        "predictions_deleted": predictions_deleted,
        "persona_deleted": persona_deleted,
    }


# ── Intelligence summary (for Dashboard) ─────────────────────────────

@router.get("/summary")
def intelligence_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Summary of intelligence coverage across all competitors."""
    competitors = db.query(Competitor).filter(Competitor.watchlist == True).all()
    summary = []
    for comp in competitors:
        dossier_count = db.query(CompetitorDossier).filter(CompetitorDossier.competitor_id == comp.id).count()
        verified_count = db.query(CompetitorDossier).filter(
            CompetitorDossier.competitor_id == comp.id, CompetitorDossier.verified == True
        ).count()
        prediction_count = db.query(CompetitorPrediction).filter(CompetitorPrediction.competitor_id == comp.id).count()
        has_persona = db.query(Persona).filter(
            Persona.persona_type == "competitor_writer", Persona.competitor_id == comp.id
        ).count() > 0
        doc_count = db.query(IngestedDocument).filter(
            IngestedDocument.competitor_id == comp.id
        ).count()

        summary.append({
            "id": comp.id,
            "name": comp.name,
            "dossier_entries": dossier_count,
            "verified_entries": verified_count,
            "total_categories": len(DOSSIER_CATEGORIES),
            "predictions_generated": prediction_count,
            "has_writer_persona": has_persona,
        })

    return {"competitors": summary}
