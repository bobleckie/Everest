"""Parsons knowledge upload pipeline.

Orchestrates the multi-stage flow that fires when a user uploads a Parsons
knowledge document via the wizard. Stages run sequentially and write
timestamps + status to ``ParsonsUploadJob`` so the wizard UI can render a
live progress timeline.

Stages:
  1. parsing         — ingest the file, split into chunks (already done
                       BEFORE this orchestrator runs — IngestedDocument
                       row exists with status='completed')
  2. embedding       — enrich_chunks (embeddings + section tags)
  3. quality_assess  — assess_quality_for_document for THIS doc
  4. supersession    — apply user-chosen predecessors (older docs in
                       same category that this new doc replaces)
  5. coverage_rerun  — reassess_open_gaps_for_proposal for every proposal
                       that this doc is in scope for (global = all, or
                       only the parsons_scope_proposal_id)
  6. classify        — re-run response disposition classifier on any
                       requirement whose coverage status changed

Design tenets:
  * Never mutate work in flight: if a stage fails, the job is marked
    'failed' and remaining stages are skipped — the user can retry.
  * Idempotent supersession: re-running with the same predecessor list
    is a no-op (we only set the supersede fields if they're currently
    NULL, so a previous run's supersession is preserved).
  * Pre-existing manual corrections are NEVER overwritten:
      - response_disposition_classifier='manual' is preserved
      - Already-superseded docs are not re-stamped
  * Cost-aware: coverage_rerun only re-grades gap/partial/uncertain
    requirements (covered ones are kept). Classifier re-runs only on
    requirements whose coverage status actually changed.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models import (
    IngestedDocument, ParsonsUploadJob, RfpRequirement,
)
from .knowledge_base import enrich_chunks
from .parsons_coverage import reassess_open_gaps_for_proposal
from .parsons_doc_quality import assess_quality_for_document
from .response_disposition_classifier import classify_requirements

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Job lifecycle helpers
# ─────────────────────────────────────────────────────────────────────

def _stamp_stage(
    db: Session,
    job: ParsonsUploadJob,
    stage: str,
    *,
    started: bool = False,
    completed: bool = False,
    note: Optional[str] = None,
) -> None:
    """Write started_at / completed_at for a named stage and update
    current_stage + progress_note. Caller is responsible for db.commit()."""
    now = datetime.utcnow()
    if started:
        setattr(job, f"{stage}_started_at", now)
        job.current_stage = stage
        job.status = "running"
    if completed:
        setattr(job, f"{stage}_completed_at", now)
    if note is not None:
        job.progress_note = note[:500]


def create_job(
    db: Session,
    *,
    document_id: int,
    user_id: Optional[int],
    form_payload: Dict[str, Any],
) -> ParsonsUploadJob:
    """Create a new upload job. The IngestedDocument must already exist
    (created by the upload-document endpoint before this is called)."""
    job = ParsonsUploadJob(
        document_id=document_id,
        user_id=user_id,
        status="queued",
        current_stage=None,
        form_payload_json=json.dumps(form_payload, default=str),
        coverage_reqs_reassessed=0,
        coverage_reqs_promoted=0,
        classify_reqs_classified=0,
        created_at=datetime.utcnow(),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


# ─────────────────────────────────────────────────────────────────────
# Stage runners — each receives a fresh DB session so background threads
# don't collide with the request session.
# ─────────────────────────────────────────────────────────────────────

def _run_embedding(db: Session, job: ParsonsUploadJob) -> bool:
    _stamp_stage(db, job, "embedding", started=True,
                 note="Generating chunk embeddings...")
    db.commit()
    try:
        result = enrich_chunks(db, job.document_id)
        if isinstance(result, dict) and result.get("error"):
            raise RuntimeError(result["error"])
        n = result.get("chunks_enriched", 0) if isinstance(result, dict) else 0
        _stamp_stage(db, job, "embedding", completed=True,
                     note=f"Embedded {n} chunks.")
        db.commit()
        return True
    except Exception as e:  # noqa: BLE001
        logger.exception("upload pipeline: embedding failed")
        job.error = f"Embedding failed: {e}"
        job.status = "failed"
        db.commit()
        return False


def _run_quality(db: Session, job: ParsonsUploadJob) -> bool:
    _stamp_stage(db, job, "quality", started=True,
                 note="Assessing document quality...")
    db.commit()
    try:
        result = assess_quality_for_document(db, job.document_id)
        if isinstance(result, dict) and result.get("error"):
            # Quality is best-effort — don't fail the whole pipeline
            logger.warning(f"Quality assess returned error: {result['error']}")
            _stamp_stage(db, job, "quality", completed=True,
                         note=f"Quality skipped: {result['error']}")
        else:
            score = result.get("quality_score") if isinstance(result, dict) else None
            _stamp_stage(db, job, "quality", completed=True,
                         note=f"Quality scored: {score}/100" if score is not None
                              else "Quality assessment complete.")
        db.commit()
        return True
    except Exception as e:  # noqa: BLE001
        # Don't fail the pipeline for a quality blip — log + continue
        logger.warning(f"Quality assess threw: {e}")
        _stamp_stage(db, job, "quality", completed=True,
                     note=f"Quality skipped (error: {str(e)[:80]}).")
        db.commit()
        return True


def _run_supersession(
    db: Session,
    job: ParsonsUploadJob,
    superseded_doc_ids: List[int],
    reason: Optional[str] = None,
) -> bool:
    _stamp_stage(db, job, "supersession", started=True,
                 note=(f"Marking {len(superseded_doc_ids)} predecessor doc(s) "
                       f"as superseded..."))
    db.commit()
    try:
        if not superseded_doc_ids:
            _stamp_stage(db, job, "supersession", completed=True,
                         note="No predecessor documents specified.")
            db.commit()
            return True

        # Fetch each doc and stamp the supersession fields ONLY if not
        # already set (so a re-run of this stage is idempotent).
        now = datetime.utcnow()
        actually_marked = []
        for pred_id in superseded_doc_ids:
            pred = db.query(IngestedDocument).filter(
                IngestedDocument.id == pred_id).first()
            if not pred:
                logger.warning(f"Predecessor doc {pred_id} not found; skipping")
                continue
            # Don't supersede the new doc with itself
            if pred.id == job.document_id:
                logger.warning(f"Predecessor {pred_id} == new doc; skipping")
                continue
            # Don't reset an existing supersession
            if pred.superseded_by_document_id is not None:
                logger.info(f"Predecessor {pred_id} already superseded; skipping")
                continue
            pred.superseded_by_document_id = job.document_id
            pred.superseded_at = now
            pred.superseded_by_user_id = job.user_id
            pred.superseded_reason = (reason or "")[:1000] or "Replaced by newer upload."
            actually_marked.append(pred_id)
        job.superseded_doc_ids = json.dumps(actually_marked)
        _stamp_stage(db, job, "supersession", completed=True,
                     note=f"Marked {len(actually_marked)} doc(s) as superseded.")
        db.commit()
        return True
    except Exception as e:  # noqa: BLE001
        logger.exception("upload pipeline: supersession failed")
        job.error = f"Supersession failed: {e}"
        job.status = "failed"
        db.commit()
        return False


def _run_coverage_rerun(
    db: Session,
    job: ParsonsUploadJob,
    proposal_ids: List[int],
) -> bool:
    _stamp_stage(db, job, "coverage", started=True,
                 note=f"Re-running coverage on {len(proposal_ids)} proposal(s)...")
    db.commit()
    try:
        if not proposal_ids:
            _stamp_stage(db, job, "coverage", completed=True,
                         note="No proposals to reassess.")
            db.commit()
            return True

        total_reassessed = 0
        total_promoted = 0
        for pid in proposal_ids:
            def _cb(processed: int, total: int) -> None:
                # Best-effort progress note while a single proposal is grading
                if total:
                    pct = int(round(100 * processed / total))
                    job.progress_note = (
                        f"Coverage on proposal {pid}: {processed}/{total} ({pct}%)"
                    )[:500]
                    try:
                        db.commit()
                    except Exception:
                        pass
            res = reassess_open_gaps_for_proposal(db, pid, progress_cb=_cb)
            total_reassessed += res.get("reassessed", 0)
            total_promoted += res.get("promoted", 0)
        job.coverage_reqs_reassessed = total_reassessed
        job.coverage_reqs_promoted = total_promoted
        _stamp_stage(db, job, "coverage", completed=True,
                     note=(f"Coverage rerun: {total_reassessed} reassessed, "
                           f"{total_promoted} promoted out of gap."))
        db.commit()
        return True
    except Exception as e:  # noqa: BLE001
        logger.exception("upload pipeline: coverage failed")
        job.error = f"Coverage rerun failed: {e}"
        job.status = "failed"
        db.commit()
        return False


def _run_classify(
    db: Session,
    job: ParsonsUploadJob,
    proposal_ids: List[int],
) -> bool:
    _stamp_stage(db, job, "classify", started=True,
                 note="Classifying any newly-affected gap requirements...")
    db.commit()
    try:
        total_classified = 0
        for pid in proposal_ids:
            # Only classify gap/uncertain reqs that don't already have a
            # disposition (or were classified but moved categories during
            # coverage rerun). Skip manual-set rows. The classifier itself
            # filters out manual rows via its own filter.
            #
            # We also want to RE-classify any reqs whose coverage status
            # changed during the coverage stage. To keep this simple and
            # safe, we classify only reqs with NULL response_disposition.
            from sqlalchemy import or_
            target_count = (db.query(RfpRequirement)
                .filter(RfpRequirement.proposal_id == pid)
                .filter(RfpRequirement.parsons_coverage_status.in_(
                    ["gap", "uncertain"]))
                .filter(RfpRequirement.response_disposition.is_(None))
                .count())
            if target_count == 0:
                continue
            result = classify_requirements(
                db, proposal_id=pid,
                coverage_statuses=["gap", "uncertain"],
                use_llm_fallback=True,
            )
            total_classified += result.get("processed", 0)
        job.classify_reqs_classified = total_classified
        _stamp_stage(db, job, "classify", completed=True,
                     note=f"Classified {total_classified} new requirement(s).")
        db.commit()
        return True
    except Exception as e:  # noqa: BLE001
        logger.exception("upload pipeline: classify failed")
        job.error = f"Classify failed: {e}"
        job.status = "failed"
        db.commit()
        return False


# ─────────────────────────────────────────────────────────────────────
# Public entry point — runs the whole pipeline in a background thread
# ─────────────────────────────────────────────────────────────────────

def run_pipeline(
    job_id: int,
    superseded_doc_ids: Optional[List[int]] = None,
    supersession_reason: Optional[str] = None,
    rerun_coverage: bool = True,
    run_classifier: bool = True,
) -> None:
    """Run the full upload pipeline. This function is meant to be called
    in a background thread (e.g. via FastAPI BackgroundTasks)."""
    db = SessionLocal()
    try:
        job = db.query(ParsonsUploadJob).filter(
            ParsonsUploadJob.id == job_id).first()
        if not job:
            logger.error(f"upload pipeline: job {job_id} not found")
            return
        doc = db.query(IngestedDocument).filter(
            IngestedDocument.id == job.document_id).first()
        if not doc:
            job.status = "failed"
            job.error = f"Document {job.document_id} not found"
            db.commit()
            return

        # Determine proposal scope: if the doc is global (parsons_scope_proposal_id
        # IS NULL), all RFP proposals get reassessed. If scoped, only that
        # proposal.
        from ..models import Proposal
        if doc.parsons_scope_proposal_id:
            proposal_ids = [doc.parsons_scope_proposal_id]
        else:
            proposal_ids = [p.id for p in db.query(Proposal).all()]

        # Stage 2 — Embedding
        if not _run_embedding(db, job):
            job.completed_at = datetime.utcnow()
            db.commit()
            return

        # Stage 3 — Quality
        _run_quality(db, job)  # never fails the pipeline

        # Stage 4 — Supersession (always run, no-op if list is empty)
        if not _run_supersession(db, job, superseded_doc_ids or [],
                                  supersession_reason):
            job.completed_at = datetime.utcnow()
            db.commit()
            return

        # Stage 5 — Coverage rerun (optional, defaults to True)
        if rerun_coverage:
            if not _run_coverage_rerun(db, job, proposal_ids):
                job.completed_at = datetime.utcnow()
                db.commit()
                return
        else:
            _stamp_stage(db, job, "coverage", started=True, completed=True,
                         note="Coverage rerun skipped by user.")
            db.commit()

        # Stage 6 — Classifier (optional, defaults to True)
        if run_classifier:
            if not _run_classify(db, job, proposal_ids):
                job.completed_at = datetime.utcnow()
                db.commit()
                return
        else:
            _stamp_stage(db, job, "classify", started=True, completed=True,
                         note="Classifier skipped by user.")
            db.commit()

        # Success
        job.status = "complete"
        job.current_stage = "complete"
        job.completed_at = datetime.utcnow()
        job.progress_note = "All stages complete."
        db.commit()
    except Exception as e:  # noqa: BLE001
        logger.exception(f"upload pipeline {job_id}: unhandled error")
        try:
            job = db.query(ParsonsUploadJob).filter(
                ParsonsUploadJob.id == job_id).first()
            if job:
                job.status = "failed"
                job.error = str(e)[:1000]
                job.completed_at = datetime.utcnow()
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


# Background-thread launcher — makes the pipeline fire-and-forget so the
# wizard's "start" endpoint returns quickly and the UI starts polling
# the job row for live progress updates.
def launch_pipeline(
    job_id: int,
    superseded_doc_ids: Optional[List[int]] = None,
    supersession_reason: Optional[str] = None,
    rerun_coverage: bool = True,
    run_classifier: bool = True,
) -> None:
    t = threading.Thread(
        target=run_pipeline,
        args=(job_id,),
        kwargs={
            "superseded_doc_ids": superseded_doc_ids or [],
            "supersession_reason": supersession_reason,
            "rerun_coverage": rerun_coverage,
            "run_classifier": run_classifier,
        },
        daemon=True,
    )
    t.start()
    logger.info(f"upload pipeline {job_id}: launched in background thread")
