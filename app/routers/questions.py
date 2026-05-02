"""
RFP Questions router — draft, review, approve, submit, and export questions
for the solicitation's Q&A period.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..database import get_db, SessionLocal
from ..models import (
    AppSetting, DocumentChunk, IngestedDocument, RfpQuestion, User,
)
from ..services.question_drafter import (
    draft_questions_from_document,
    _coerce_to_interrogative,
    ALLOWED_CATEGORIES,
    ALLOWED_PRIORITIES,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Configurable approval settings (stored as AppSetting rows) ───────

QUESTION_APPROVAL_ROLES_KEY = "question_approval_roles"
# Sensible defaults for the actual roles in this system. `capture_manager`
# was the previous default but doesn't exist in the user table — so out
# of the box NOBODY could approve. `admin` is ALWAYS implicitly allowed
# (see _can_approve below), so we don't need to list it here.
DEFAULT_APPROVAL_ROLES = ["proposal_manager", "evaluator"]


def _can_approve(user_role: Optional[str], allowed_roles: List[str]) -> bool:
    """Return True if `user_role` is permitted to approve a question.

    Rules (in order):
      1. `admin` ALWAYS approves (super-user override).
      2. If the configured allow-list contains the literal string "any",
         every authenticated role is allowed.
      3. Otherwise the user's role must be in the allow-list.
    """
    role = (user_role or "").strip().lower()
    if role == "admin":
        return True
    if not allowed_roles:
        return True  # empty config -> behave like "any"
    if "any" in [r.lower() for r in allowed_roles]:
        return True
    return role in [r.lower() for r in allowed_roles]


def _get_approval_roles(db: Session) -> List[str]:
    row = db.query(AppSetting).filter_by(key=QUESTION_APPROVAL_ROLES_KEY).first()
    if not row or not row.value_json:
        return list(DEFAULT_APPROVAL_ROLES)
    try:
        v = json.loads(row.value_json)
        if isinstance(v, list):
            return [str(x) for x in v]
    except Exception:
        pass
    return list(DEFAULT_APPROVAL_ROLES)


def _set_approval_roles(db: Session, roles: List[str]) -> None:
    row = db.query(AppSetting).filter_by(key=QUESTION_APPROVAL_ROLES_KEY).first()
    if not row:
        row = AppSetting(key=QUESTION_APPROVAL_ROLES_KEY, value_json=json.dumps(roles))
        db.add(row)
    else:
        row.value_json = json.dumps(roles)
    db.commit()


# ── Schemas ──────────────────────────────────────────────────────────

class QuestionOut(BaseModel):
    id: int
    document_id: Optional[int]
    proposal_id: Optional[int]
    source_section: Optional[str]
    source_page: Optional[int]
    category: str
    priority: Optional[str]
    question_text: str
    rationale: Optional[str]
    source_quote: Optional[str]
    status: str
    answer_text: Optional[str]
    answered_date: Optional[datetime]
    submitted_date: Optional[datetime]
    reviewer_user_id: Optional[int]
    review_notes: Optional[str]
    created_by_ai: bool
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


class QuestionCreate(BaseModel):
    document_id: Optional[int] = None
    proposal_id: Optional[int] = None
    source_section: Optional[str] = None
    source_page: Optional[int] = None
    category: str = "clarification"
    priority: str = "medium"
    question_text: str
    rationale: Optional[str] = None
    source_quote: Optional[str] = None


class QuestionUpdate(BaseModel):
    source_section: Optional[str] = None
    source_page: Optional[int] = None
    category: Optional[str] = None
    priority: Optional[str] = None
    question_text: Optional[str] = None
    rationale: Optional[str] = None
    source_quote: Optional[str] = None
    status: Optional[str] = None
    answer_text: Optional[str] = None
    review_notes: Optional[str] = None


class ApprovalRolesIn(BaseModel):
    roles: List[str]


class DraftJobResult(BaseModel):
    job_id: str
    document_id: int
    proposal_id: Optional[int]
    status: str
    message: str


# ── In-process job tracker (simple — single-worker) ──────────────────

_DRAFT_JOBS: dict = {}


def _run_drafter_job(job_id: str, document_id: int, proposal_id: Optional[int]) -> None:
    _DRAFT_JOBS[job_id] = {"status": "running", "saved": 0, "error": None, "started_at": datetime.utcnow()}
    db = SessionLocal()
    try:
        result = draft_questions_from_document(db, document_id, proposal_id=proposal_id)
        _DRAFT_JOBS[job_id].update({
            "status": "complete",
            "saved": result.get("questions_saved", 0),
            "windows_processed": result.get("windows_processed", 0),
            "completed_at": datetime.utcnow(),
        })
    except Exception as e:
        logger.exception(f"Draft job {job_id} failed: {e}")
        _DRAFT_JOBS[job_id].update({"status": "failed", "error": str(e), "completed_at": datetime.utcnow()})
    finally:
        db.close()


# ── Endpoints ────────────────────────────────────────────────────────

class PaginatedQuestions(BaseModel):
    total: int
    limit: int
    offset: int
    items: List[QuestionOut]


@router.get("/", response_model=PaginatedQuestions)
def list_questions(
    proposal_id: Optional[int] = Query(None),
    document_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Paginated list of RFP questions.

    Query params:
      proposal_id, document_id, status, category, priority — optional filters
      limit  (default 50, max 500) — page size
      offset (default 0)           — starting row
    Returns {total, limit, offset, items}.
    """
    q = db.query(RfpQuestion)
    if proposal_id is not None:
        q = q.filter(RfpQuestion.proposal_id == proposal_id)
    if document_id is not None:
        q = q.filter(RfpQuestion.document_id == document_id)
    if status:
        q = q.filter(RfpQuestion.status == status)
    if category:
        q = q.filter(RfpQuestion.category == category)
    if priority:
        q = q.filter(RfpQuestion.priority == priority)

    total = q.count()
    q = q.order_by(RfpQuestion.priority.desc(), RfpQuestion.source_page, RfpQuestion.id)
    items = q.offset(offset).limit(limit).all()
    return PaginatedQuestions(total=total, limit=limit, offset=offset, items=items)


@router.post("/", response_model=QuestionOut)
def create_question(
    body: QuestionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = RfpQuestion(
        document_id=body.document_id,
        proposal_id=body.proposal_id,
        source_section=body.source_section,
        source_page=body.source_page,
        category=body.category,
        priority=body.priority,
        question_text=body.question_text,
        rationale=body.rationale,
        source_quote=body.source_quote,
        status="draft",
        created_by_ai=False,
        created_by_user_id=getattr(user, "id", None),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.patch("/{qid}", response_model=QuestionOut)
def update_question(
    qid: int,
    body: QuestionUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = db.query(RfpQuestion).filter_by(id=qid).first()
    if not row:
        raise HTTPException(404, "Question not found")

    # Status-change rules
    if body.status and body.status != row.status:
        new_status = body.status
        allowed_roles = _get_approval_roles(db)
        if new_status == "approved":
            user_role = getattr(user, "role", None)
            if not _can_approve(user_role, allowed_roles):
                raise HTTPException(
                    403,
                    f"Approval requires admin or one of: {', '.join(allowed_roles)}. "
                    f"Your role: {user_role or '(none)'}.",
                )
            row.reviewer_user_id = getattr(user, "id", None)
        if new_status == "submitted":
            row.submitted_date = datetime.utcnow()
        if new_status == "answered" and body.answer_text:
            row.answered_date = datetime.utcnow()

    for field in ("source_section", "source_page", "category", "priority",
                  "question_text", "rationale", "source_quote", "status",
                  "answer_text", "review_notes"):
        v = getattr(body, field)
        if v is not None:
            setattr(row, field, v)

    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{qid}")
def delete_question(
    qid: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    row = db.query(RfpQuestion).filter_by(id=qid).first()
    if not row:
        raise HTTPException(404, "Question not found")
    db.delete(row)
    db.commit()
    return {"deleted": qid}


# ── Bulk triage operations ──────────────────────────────────────────
# These endpoints power the "narrow 4,000 candidates down to ~150
# submission-ready questions" workflow. Filter-based mass actions are
# the only sane way to triage at this scale.

class BulkStatusFilters(BaseModel):
    """Filter set that selects which rfp_questions a bulk action targets.

    All filters are AND-ed together. Lists become IN-clauses. None means
    "don't filter on this column." Dangerous: omitting all filters would
    target every question — we require ``proposal_id`` to be set.
    """
    proposal_id: int  # required, prevents accidentally hitting all proposals
    source_kind: Optional[List[str]] = None
    priority: Optional[List[str]] = None
    status: Optional[List[str]] = None
    reconciliation_action: Optional[List[str]] = None
    inference_flag: Optional[bool] = None
    curator_recommended: Optional[bool] = None
    curator_reason: Optional[List[str]] = None
    curator_score_lt: Optional[int] = None  # score strictly less than
    curator_score_gte: Optional[int] = None  # score greater-or-equal


class BulkStatusBody(BaseModel):
    filters: BulkStatusFilters
    new_status: str  # one of STATUSES (validated below)
    review_notes: Optional[str] = None
    dry_run: bool = False


def _apply_bulk_filters(q, f: BulkStatusFilters):
    """Apply a BulkStatusFilters set to a SQLAlchemy query."""
    q = q.filter(RfpQuestion.proposal_id == f.proposal_id)
    if f.source_kind:
        q = q.filter(RfpQuestion.source_kind.in_(f.source_kind))
    if f.priority:
        q = q.filter(RfpQuestion.priority.in_(f.priority))
    if f.status:
        q = q.filter(RfpQuestion.status.in_(f.status))
    if f.reconciliation_action:
        q = q.filter(RfpQuestion.reconciliation_action.in_(f.reconciliation_action))
    if f.inference_flag is not None:
        q = q.filter(RfpQuestion.inference_flag == f.inference_flag)
    if f.curator_recommended is not None:
        q = q.filter(RfpQuestion.curator_recommended == f.curator_recommended)
    if f.curator_reason:
        q = q.filter(RfpQuestion.curator_reason.in_(f.curator_reason))
    if f.curator_score_lt is not None:
        q = q.filter(RfpQuestion.curator_score < f.curator_score_lt)
    if f.curator_score_gte is not None:
        q = q.filter(RfpQuestion.curator_score >= f.curator_score_gte)
    return q


@router.post("/bulk-status")
def bulk_status_change(
    body: BulkStatusBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Change ``status`` (and optionally ``review_notes``) for every
    question matching the supplied filter set. Returns the affected count.

    Use ``dry_run=true`` to preview the count without writing.

    Approval gating: if ``new_status='approved'``, the same role check that
    `update_question` enforces is applied to the bulk action.
    """
    valid_statuses = {"draft", "reviewed", "approved", "rejected", "submitted", "answered"}
    if body.new_status not in valid_statuses:
        raise HTTPException(400, f"new_status must be one of {sorted(valid_statuses)}")

    if body.new_status == "approved":
        allowed_roles = _get_approval_roles(db)
        user_role = getattr(user, "role", None)
        if not _can_approve(user_role, allowed_roles):
            raise HTTPException(
                403,
                f"Bulk approval requires admin or one of: {', '.join(allowed_roles)}. "
                f"Your role: {user_role or '(none)'}.",
            )

    q = _apply_bulk_filters(db.query(RfpQuestion), body.filters)
    matched_ids = [r.id for r in q.with_entities(RfpQuestion.id).all()]

    if body.dry_run:
        return {"dry_run": True, "matched": len(matched_ids), "ids_sample": matched_ids[:20]}

    if not matched_ids:
        return {"updated": 0, "matched": 0}

    now = datetime.utcnow()
    update_map: dict = {
        RfpQuestion.status: body.new_status,
        RfpQuestion.updated_at: now,
    }
    if body.review_notes is not None:
        update_map[RfpQuestion.review_notes] = body.review_notes
    if body.new_status == "approved":
        update_map[RfpQuestion.reviewer_user_id] = getattr(user, "id", None)
    if body.new_status == "submitted":
        update_map[RfpQuestion.submitted_date] = now

    # Use a single UPDATE for performance — at 4k rows, ORM-loop is too slow.
    db.query(RfpQuestion).filter(RfpQuestion.id.in_(matched_ids)).update(
        update_map, synchronize_session=False)
    db.commit()
    return {"updated": len(matched_ids), "matched": len(matched_ids),
            "new_status": body.new_status}


@router.get("/triage-stats")
def triage_stats(
    proposal_id: int = Query(...),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Return a snapshot of the question triage state for a proposal.

    The Triage UI uses this to render the funnel + filter chip counts.
    Single endpoint to avoid 12 separate GETs from the UI.
    """
    base = db.query(RfpQuestion).filter(RfpQuestion.proposal_id == proposal_id)

    total = base.count()
    if total == 0:
        return {
            "total": 0, "by_source_kind": {}, "by_priority": {}, "by_status": {},
            "by_reconciliation": {}, "by_curator_reason": {},
            "active_inferences": 0, "curator_run_total": 0,
            "curator_recommended": 0, "curator_not_recommended": 0,
            "submission_ready": 0,
        }

    from sqlalchemy import func as sa_func
    def _grp(col):
        out: dict = {}
        for row in base.with_entities(col, sa_func.count()).group_by(col).all():
            key = str(row[0]) if row[0] is not None else "(none)"
            out[key] = int(row[1])
        return out

    by_source_kind = _grp(RfpQuestion.source_kind)
    by_priority = _grp(RfpQuestion.priority)
    by_status = _grp(RfpQuestion.status)
    by_reconciliation = _grp(RfpQuestion.reconciliation_action)
    by_curator_reason = _grp(RfpQuestion.curator_reason)

    active_inferences = base.filter(
        RfpQuestion.inference_flag == True,  # noqa: E712
        RfpQuestion.status != "rejected",
    ).count()
    curator_run_total = base.filter(RfpQuestion.curator_run_at.isnot(None)).count()
    curator_recommended = base.filter(RfpQuestion.curator_recommended == True).count()  # noqa: E712
    curator_not_recommended = base.filter(RfpQuestion.curator_recommended == False).count()  # noqa: E712
    submission_ready = base.filter(RfpQuestion.status == "approved").count()

    return {
        "proposal_id": proposal_id,
        "total": total,
        "by_source_kind": by_source_kind,
        "by_priority": by_priority,
        "by_status": by_status,
        "by_reconciliation": by_reconciliation,
        "by_curator_reason": by_curator_reason,
        "active_inferences": active_inferences,
        "curator_run_total": curator_run_total,
        "curator_recommended": curator_recommended,
        "curator_not_recommended": curator_not_recommended,
        "submission_ready": submission_ready,
    }


# ── Strategic Curator (decide which questions to actually submit) ───

class CurateBody(BaseModel):
    proposal_id: int
    only_statuses: Optional[List[str]] = None  # default: ["draft","reviewed"]
    limit: Optional[int] = None  # cap for testing


_CURATE_JOBS: dict = {}


def _run_curate_job(job_id: str, body: CurateBody) -> None:
    from ..services.question_curator import curate_proposal_questions
    db = SessionLocal()
    _CURATE_JOBS[job_id] = {
        "status": "running",
        "proposal_id": body.proposal_id,
        "started_at": datetime.utcnow().isoformat(timespec="seconds"),
        "processed": 0, "total": 0, "recommended": 0,
        "error": None,
    }

    def _progress(processed: int, total: int, recommended: int) -> None:
        _CURATE_JOBS[job_id].update({
            "processed": processed,
            "total": total,
            "recommended": recommended,
        })

    try:
        result = curate_proposal_questions(
            db,
            body.proposal_id,
            only_statuses=body.only_statuses,
            limit=body.limit,
            progress_cb=_progress,
        )
        _CURATE_JOBS[job_id].update({
            "status": "complete",
            "completed_at": datetime.utcnow().isoformat(timespec="seconds"),
            **result,
        })
    except Exception as e:  # noqa: BLE001
        logger.exception(f"curate job {job_id} failed")
        _CURATE_JOBS[job_id].update({
            "status": "failed",
            "error": str(e),
            "completed_at": datetime.utcnow().isoformat(timespec="seconds"),
        })
    finally:
        db.close()


@router.post("/curate")
def start_curate(
    body: CurateBody,
    background: BackgroundTasks,
    _user: User = Depends(get_current_user),
):
    """Kick off the strategic curator over every draft/reviewed question
    on the proposal. Runs in background — poll /curate/{job_id} for status.

    The curator writes to ``curator_score``, ``curator_recommended``,
    ``curator_reason``, ``curator_improved_text`` on each question and
    nudges status from ``draft`` → ``reviewed``. It does NOT auto-reject;
    the human reviewer makes the final call.
    """
    job_id = f"curate-{body.proposal_id}-{int(datetime.utcnow().timestamp())}"
    _CURATE_JOBS[job_id] = {
        "status": "queued",
        "proposal_id": body.proposal_id,
        "processed": 0, "total": 0, "recommended": 0,
    }
    background.add_task(_run_curate_job, job_id, body)
    return {"job_id": job_id, "status": "queued"}


@router.get("/curate/{job_id}")
def get_curate_job(job_id: str, _user: User = Depends(get_current_user)):
    job = _CURATE_JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return {"job_id": job_id, **job}


@router.get("/curate")
def list_curate_jobs(_user: User = Depends(get_current_user)):
    """Return every curate job tracked by this backend process. Useful for
    the Triage UI to find an in-flight job after a refresh."""
    return [{"job_id": jid, **j} for jid, j in _CURATE_JOBS.items()]


@router.post("/draft/{document_id}", response_model=DraftJobResult)
def start_drafting(
    document_id: int,
    background: BackgroundTasks,
    proposal_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Kick off the AI drafter for a document. Runs in the background; poll
    GET /draft-job/{job_id} for status."""
    doc = db.query(IngestedDocument).filter_by(id=document_id).first()
    if not doc:
        raise HTTPException(404, f"Document {document_id} not found")
    if proposal_id is None:
        proposal_id = doc.proposal_id

    job_id = f"draft-{document_id}-{int(datetime.utcnow().timestamp())}"
    _DRAFT_JOBS[job_id] = {"status": "queued", "saved": 0, "error": None}
    background.add_task(_run_drafter_job, job_id, document_id, proposal_id)
    return DraftJobResult(
        job_id=job_id,
        document_id=document_id,
        proposal_id=proposal_id,
        status="queued",
        message="Drafting in background. Poll /api/questions/draft-job/{job_id}.",
    )


@router.get("/draft-job/{job_id}")
def get_draft_job(
    job_id: str,
    _user: User = Depends(get_current_user),
):
    job = _DRAFT_JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return {"job_id": job_id, **job}


@router.get("/approval-roles")
def get_approval_roles(
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    return {"roles": _get_approval_roles(db)}


# ── Consolidation (cluster + merge near-duplicate drafts) ─────────────

class ConsolidatePlanIn(BaseModel):
    proposal_id: Optional[int] = None
    document_id: Optional[int] = None
    similarity_threshold: float = 0.80
    only_statuses: Optional[List[str]] = ["draft"]
    only_priorities: Optional[List[str]] = None


class ConsolidateApplyIn(BaseModel):
    plan: dict


class ConsolidateRevertIn(BaseModel):
    merged_question_ids: List[int]


@router.post("/consolidate/plan")
def consolidate_plan(
    body: ConsolidatePlanIn,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Preview a consolidation plan without writing anything.

    Buckets candidate questions by section, clusters near-matches by embedding
    cosine similarity, and asks the LLM to merge each cluster into one
    consolidated question. Returns the full plan for user review.
    """
    from ..services.question_consolidator import build_plan
    try:
        plan = build_plan(
            db,
            proposal_id=body.proposal_id,
            document_id=body.document_id,
            similarity_threshold=body.similarity_threshold,
            only_statuses=body.only_statuses or ("draft",),
            only_priorities=body.only_priorities,
        )
    except Exception as e:
        logger.exception("consolidate_plan failed")
        raise HTTPException(500, f"Plan generation failed: {e}")
    return plan


@router.post("/consolidate/apply")
def consolidate_apply(
    body: ConsolidateApplyIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Execute a plan previously returned by `/consolidate/plan`.

    Creates merged question rows and marks originals as `status="superseded"`
    (non-destructive — they can be restored via `/consolidate/revert`).
    """
    from ..services.question_consolidator import apply_plan
    try:
        result = apply_plan(db, body.plan, actor_user_id=getattr(user, "id", None))
    except Exception as e:
        logger.exception("consolidate_apply failed")
        raise HTTPException(500, f"Apply failed: {e}")
    return result


@router.post("/consolidate/revert")
def consolidate_revert(
    body: ConsolidateRevertIn,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Undo one or more merges. Deletes the merged question(s) and flips each
    superseded original back to `status="draft"`."""
    from ..services.question_consolidator import revert_consolidation
    try:
        result = revert_consolidation(db, body.merged_question_ids)
    except Exception as e:
        logger.exception("consolidate_revert failed")
        raise HTTPException(500, f"Revert failed: {e}")
    return result


# ── LLM-bucket consolidation (no embeddings needed) ─────────────────

class ConsolidatePlanLlmIn(BaseModel):
    proposal_id: Optional[int] = None
    document_id: Optional[int] = None
    only_statuses: Optional[List[str]] = ["draft"]
    only_priorities: Optional[List[str]] = None
    target_per_bucket: Optional[int] = None
    max_workers: int = 6


class ConsolidateDropIn(BaseModel):
    dropped_ids: List[int]


# In-memory job store for LLM consolidation (mirrors the drafter pattern).
_CONSOLIDATE_JOBS: dict[str, dict] = {}


def _run_consolidate_llm_job(job_id: str, body: ConsolidatePlanLlmIn) -> None:
    """Background worker: builds an LLM-bucket plan and stashes it under
    `_CONSOLIDATE_JOBS[job_id]`."""
    from ..services.question_consolidator import build_plan_llm
    db = SessionLocal()
    try:
        _CONSOLIDATE_JOBS[job_id] = {
            "status": "running",
            "started_at": datetime.utcnow().isoformat(timespec="seconds"),
            "plan": None,
            "error": None,
        }
        plan = build_plan_llm(
            db,
            proposal_id=body.proposal_id,
            document_id=body.document_id,
            only_statuses=body.only_statuses or ("draft",),
            only_priorities=body.only_priorities,
            target_per_bucket=body.target_per_bucket,
            max_workers=max(1, int(body.max_workers or 6)),
        )
        _CONSOLIDATE_JOBS[job_id].update({
            "status": "done",
            "plan": plan,
            "finished_at": datetime.utcnow().isoformat(timespec="seconds"),
        })
    except Exception as e:
        logger.exception("consolidate-llm job failed")
        _CONSOLIDATE_JOBS[job_id].update({
            "status": "error",
            "error": str(e),
            "finished_at": datetime.utcnow().isoformat(timespec="seconds"),
        })
    finally:
        db.close()


@router.post("/consolidate/plan-llm")
def consolidate_plan_llm(
    body: ConsolidatePlanLlmIn,
    background: BackgroundTasks,
    _user: User = Depends(get_current_user),
):
    """Kick off an LLM-bucket consolidation plan (runs in background).

    Returns a job_id; poll `/consolidate/plan-llm/{job_id}` for the plan.
    This path doesn't need embeddings — it asks Claude to review each
    section bucket directly, which works reliably in env where sbert is
    blocked.
    """
    job_id = f"consolidate-{int(datetime.utcnow().timestamp())}"
    _CONSOLIDATE_JOBS[job_id] = {"status": "queued", "plan": None, "error": None}
    background.add_task(_run_consolidate_llm_job, job_id, body)
    return {"job_id": job_id, "status": "queued"}


@router.get("/consolidate/plan-llm/{job_id}")
def consolidate_plan_llm_status(
    job_id: str,
    _user: User = Depends(get_current_user),
):
    job = _CONSOLIDATE_JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return {"job_id": job_id, **job}


@router.post("/consolidate/apply-dropped")
def consolidate_apply_dropped(
    body: ConsolidateDropIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Mark the LLM-recommended 'drop' list as superseded. Separate from
    /consolidate/apply because dropping is a stronger action — the user
    should confirm it explicitly."""
    from ..services.question_consolidator import apply_dropped
    try:
        result = apply_dropped(db, body.dropped_ids, actor_user_id=getattr(user, "id", None))
    except Exception as e:
        logger.exception("consolidate_apply_dropped failed")
        raise HTTPException(500, f"Apply-dropped failed: {e}")
    return result


@router.put("/approval-roles")
def set_approval_roles(
    body: ApprovalRolesIn,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    _set_approval_roles(db, body.roles)
    return {"roles": body.roles}


@router.get("/export/{proposal_id}")
def export_questions(
    proposal_id: int,
    format: str = Query("text", pattern="^(text|njstart|docx)$"),
    status: str = Query("approved"),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Export questions in NJSTART-friendly format.

    format=text   — plain text, ready to paste into NJSTART question field
    format=njstart — same as text, but numbered with "Question N:" prefixes
    format=docx   — generate a Word doc (returned as downloadable response)
    """
    rows = (
        db.query(RfpQuestion)
        .filter(RfpQuestion.proposal_id == proposal_id)
        .filter(RfpQuestion.status == status)
        .order_by(RfpQuestion.priority.desc(), RfpQuestion.source_page, RfpQuestion.id)
        .all()
    )

    if format == "docx":
        from fastapi.responses import StreamingResponse
        from io import BytesIO
        try:
            from docx import Document as DocxDocument
        except Exception as e:
            raise HTTPException(500, f"python-docx not installed: {e}")

        d = DocxDocument()
        d.add_heading("RFP Clarifying Questions — For Submission", level=1)
        d.add_paragraph(f"Proposal ID: {proposal_id}")
        d.add_paragraph(f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
        d.add_paragraph(f"Status filter: {status}")
        d.add_paragraph("")
        for i, r in enumerate(rows, start=1):
            d.add_heading(f"Question {i}", level=2)
            if r.source_section or r.source_page:
                cite = []
                if r.source_section:
                    cite.append(f"Section {r.source_section}")
                if r.source_page:
                    cite.append(f"Page {r.source_page}")
                d.add_paragraph(" | ".join(cite))
            d.add_paragraph(r.question_text or "")
        buf = BytesIO()
        d.save(buf)
        buf.seek(0)
        headers = {"Content-Disposition": f'attachment; filename="questions_proposal_{proposal_id}.docx"'}
        return StreamingResponse(
            buf,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers=headers,
        )

    # text / njstart
    lines = []
    if format == "njstart":
        lines.append(f"RFP Clarifying Questions — Proposal #{proposal_id}")
        lines.append(f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
        lines.append("")
    for i, r in enumerate(rows, start=1):
        prefix = f"Question {i}: " if format == "njstart" else ""
        lines.append(f"{prefix}{r.question_text or ''}")
        if format == "njstart":
            lines.append("")
    return {"count": len(rows), "text": "\n".join(lines)}

# ── Semantic search over existing questions ────────────────────────────────

class QuestionSearchIn(BaseModel):
    query: str
    proposal_id: Optional[int] = None
    top_k: int = 8
    min_similarity: float = 0.25


class QuestionSearchHit(BaseModel):
    question: QuestionOut
    similarity: float


class QuestionSearchOut(BaseModel):
    query: str
    hits: List[QuestionSearchHit]
    count: int


def _score_questions_against_query(
    query: str, rows: List[RfpQuestion], top_k: int, min_sim: float
) -> List[tuple]:
    """Return [(row, similarity), ...] sorted by descending similarity.

    Uses embedding-based cosine similarity when available, falls back to a
    simple lexical Jaccard overlap so the endpoint always works even if the
    embedding backend is misconfigured.
    """
    try:
        from ..services.embedding_service import embed_text, cosine_similarity
        q_vec = embed_text(query)
        scored: List[tuple] = []
        for r in rows:
            text = " ".join([
                r.question_text or "",
                r.source_section or "",
                r.rationale or "",
                r.source_quote or "",
            ]).strip()
            if not text:
                continue
            r_vec = embed_text(text)
            sim = cosine_similarity(q_vec, r_vec)
            if sim >= min_sim:
                scored.append((r, float(sim)))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]
    except Exception as e:  # pragma: no cover - defensive fallback
        logger.warning("Embedding search failed (%s); falling back to lexical.", e)

    # Lexical fallback
    q_tokens = {t.lower() for t in query.split() if len(t) > 2}
    scored: List[tuple] = []
    for r in rows:
        text = " ".join([r.question_text or "", r.source_section or "", r.rationale or ""]).lower()
        tokens = {t for t in text.split() if len(t) > 2}
        if not tokens or not q_tokens:
            continue
        inter = len(q_tokens & tokens)
        union = len(q_tokens | tokens)
        sim = inter / union if union else 0.0
        if sim >= min_sim:
            scored.append((r, float(sim)))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]


@router.post("/search", response_model=QuestionSearchOut)
def search_questions(
    body: QuestionSearchIn,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Semantic search over drafted questions. Returns the top_k most similar
    questions to the supplied query string, ranked by cosine similarity of
    their embeddings (falls back to lexical overlap if embeddings unavailable)."""
    if not body.query or not body.query.strip():
        raise HTTPException(400, "query is required")

    q = db.query(RfpQuestion)
    if body.proposal_id is not None:
        q = q.filter(RfpQuestion.proposal_id == body.proposal_id)
    rows = q.all()

    top_k = max(1, min(int(body.top_k or 8), 50))
    scored = _score_questions_against_query(body.query, rows, top_k, body.min_similarity)
    return QuestionSearchOut(
        query=body.query,
        hits=[QuestionSearchHit(question=r, similarity=s) for r, s in scored],
        count=len(scored),
    )


# ── "Ask the Agent" — search, or research+draft if no close match ──────────

class AskAgentIn(BaseModel):
    query: str
    proposal_id: Optional[int] = None
    match_threshold: float = 0.72   # ≥ this similarity ⇒ treat as already-asked
    save_if_new: bool = True        # persist the drafted question
    context_chunks: int = 6         # how many doc chunks to feed the drafter
    category: Optional[str] = None  # default category if the drafter doesn't set one
    priority: Optional[str] = None  # default priority if the drafter doesn't set one


class AskAgentOut(BaseModel):
    mode: str                        # "matched" | "drafted"
    query: str
    best_match: Optional[QuestionSearchHit] = None
    candidates: List[QuestionSearchHit] = []
    drafted_question: Optional[QuestionOut] = None
    rationale: Optional[str] = None
    context_used: List[dict] = []    # [{document_id, page, snippet}]
    notes: Optional[str] = None


def _retrieve_context_chunks(
    db: Session, query: str, proposal_id: Optional[int], k: int
) -> List[dict]:
    """Pull the top_k DocumentChunks most relevant to the query using stored
    embeddings. Returns dicts with document_id, page, content."""
    try:
        from ..services.embedding_service import embed_text, cosine_similarity
        q_vec = embed_text(query)
    except Exception as e:
        logger.warning("Embedding unavailable for context retrieval: %s", e)
        return []

    # Limit candidate set to chunks on this proposal's documents (if scoped).
    chunk_query = db.query(DocumentChunk)
    if proposal_id is not None:
        doc_ids = [
            d.id for d in db.query(IngestedDocument)
            .filter(IngestedDocument.proposal_id == proposal_id).all()
        ]
        if doc_ids:
            chunk_query = chunk_query.filter(DocumentChunk.document_id.in_(doc_ids))

    # Filter to chunks with embeddings — avoid pulling multi-GB chunk bodies by
    # only loading text when we keep a chunk.
    chunks = chunk_query.filter(DocumentChunk.embedding.isnot(None)).all()
    scored: List[tuple] = []
    for c in chunks:
        try:
            vec = json.loads(c.embedding) if c.embedding else None
        except Exception:
            vec = None
        if not vec:
            continue
        try:
            sim = cosine_similarity(q_vec, vec)
        except Exception:
            continue
        scored.append((sim, c))

    scored.sort(key=lambda x: x[0], reverse=True)
    out = []
    for sim, c in scored[:k]:
        out.append({
            "document_id": c.document_id,
            "page": c.page_number,
            "similarity": float(sim),
            "snippet": (c.content or "")[:1200],
        })
    return out


def _draft_question_from_query(
    query: str,
    context_chunks: List[dict],
    proposal_id: Optional[int],
) -> dict:
    """Call the LLM once, using retrieved RFP context, to produce a single
    interrogative clarifying question grounded in cited RFP text. Returns a
    dict with keys matching RfpQuestion columns (question_text, rationale,
    source_section, source_page, source_quote, category, priority)."""
    from ..services.orchestrator_agent import _call_ai
    from .. import services  # noqa: F401  (ensures submodule imports initialize)

    context_blocks = []
    for i, c in enumerate(context_chunks, start=1):
        context_blocks.append(
            f"[Ctx #{i} | doc={c.get('document_id')} | page={c.get('page')} | "
            f"sim={c.get('similarity'):.2f}]\n{c.get('snippet','')}"
        )
    context_text = "\n\n".join(context_blocks) if context_blocks else "(no context retrieved)"

    sys_prompt = (
        "You are a senior capture analyst drafting ONE clarifying question to "
        "submit to a procurement agency. You ground every question in the RFP "
        "text snippets provided. You phrase questions as interrogative sentences "
        "(ending in '?') — never as imperatives ('Please provide ...'). You "
        "respond with a single JSON object only."
    )
    user_prompt = f"""A capture team member wants to ask the following about the RFP:

USER QUESTION: {query!r}

Here is the most relevant RFP text retrieved from the document set:

{context_text}

Draft ONE well-formed clarifying question suitable for submission to the agency.
Requirements:
- Ground the question in the retrieved context; if the context is empty or does not
  support the user's question, return: {{"question_text": null, "rationale": "no supporting RFP text found"}}.
- question_text MUST begin with a section citation in the form "Ref. Section X.Y (p.Z): ..."
  using the most relevant section/page you can identify from the context.
- question_text MUST be an interrogative sentence ending with "?".
- Keep it under 400 characters.
- Ask ONE specific thing.

Output a single JSON object with keys:
  "question_text"   (string, interrogative, or null if unsupported)
  "source_section"  (string or null)
  "source_page"     (integer or null)
  "source_quote"    (exact quote from context, or null)
  "category"        (clarification | risk | pricing | scope | competitive | form)
  "priority"        (critical | high | medium | low)
  "rationale"       (internal-only, why this matters)
Output ONLY the JSON object — no preamble, no code fences, no commentary.
"""
    raw = _call_ai(user_prompt, sys_prompt, max_tokens=1200)
    # Tolerant JSON parse
    parsed: dict = {}
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            # Try to salvage the first {...} block.
            start = raw.find("{")
            end = raw.rfind("}")
            if start >= 0 and end > start:
                try:
                    parsed = json.loads(raw[start:end + 1])
                except json.JSONDecodeError:
                    parsed = {}
    return parsed if isinstance(parsed, dict) else {}


from ..services.rate_limit import enforce_rate_limit  # noqa: E402


@router.post("/ask", response_model=AskAgentOut, dependencies=[Depends(enforce_rate_limit("llm"))])
def ask_agent(
    body: AskAgentIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Answer a natural-language question from a user:
      1. Search existing drafted questions. If one has similarity >=
         match_threshold, return it as an already-asked match.
      2. Otherwise, retrieve the most relevant RFP context chunks and ask the
         LLM to draft a single well-formed clarifying question grounded in
         that context. If save_if_new, persist it as a new draft row.
    """
    if not body.query or not body.query.strip():
        raise HTTPException(400, "query is required")

    # ── Phase 1: search existing questions
    q = db.query(RfpQuestion)
    if body.proposal_id is not None:
        q = q.filter(RfpQuestion.proposal_id == body.proposal_id)
    rows = q.all()
    scored = _score_questions_against_query(body.query, rows, top_k=8, min_sim=0.0)
    candidates = [QuestionSearchHit(question=r, similarity=s) for r, s in scored[:5]]

    if scored and scored[0][1] >= body.match_threshold:
        top_row, top_sim = scored[0]
        return AskAgentOut(
            mode="matched",
            query=body.query,
            best_match=QuestionSearchHit(question=top_row, similarity=top_sim),
            candidates=candidates,
            notes=(
                f"Close match found (similarity={top_sim:.2f} ≥ threshold={body.match_threshold:.2f}). "
                "Lower match_threshold or set save_if_new=true to force drafting a new question."
            ),
        )

    # ── Phase 2: retrieve RFP context
    ctx = _retrieve_context_chunks(db, body.query, body.proposal_id, max(1, body.context_chunks))

    # ── Phase 3: draft
    drafted = _draft_question_from_query(body.query, ctx, body.proposal_id)
    qtext = (drafted.get("question_text") or "").strip() if isinstance(drafted, dict) else ""
    if not qtext:
        return AskAgentOut(
            mode="drafted",
            query=body.query,
            best_match=candidates[0] if candidates else None,
            candidates=candidates,
            drafted_question=None,
            rationale=(drafted.get("rationale") if isinstance(drafted, dict) else None) or
                      "No supporting RFP text was found; unable to draft a grounded question.",
            context_used=ctx,
            notes="Drafter returned no question_text.",
        )

    # Normalize + validate the drafted question
    qtext = _coerce_to_interrogative(qtext)[:4000]
    cat = (drafted.get("category") or body.category or "clarification").lower().strip()
    if cat not in ALLOWED_CATEGORIES:
        cat = "clarification"
    pri = (drafted.get("priority") or body.priority or "medium").lower().strip()
    if pri not in ALLOWED_PRIORITIES:
        pri = "medium"

    drafted_row: Optional[RfpQuestion] = None
    if body.save_if_new:
        drafted_row = RfpQuestion(
            document_id=None,
            proposal_id=body.proposal_id,
            source_section=drafted.get("source_section") or None,
            source_page=(drafted.get("source_page") if isinstance(drafted.get("source_page"), int) else None),
            category=cat,
            priority=pri,
            question_text=qtext,
            rationale=drafted.get("rationale") or None,
            source_quote=drafted.get("source_quote") or None,
            status="draft",
            created_by_ai=True,
            created_by_user_id=getattr(user, "id", None),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(drafted_row)
        db.commit()
        db.refresh(drafted_row)

    return AskAgentOut(
        mode="drafted",
        query=body.query,
        best_match=candidates[0] if candidates else None,
        candidates=candidates,
        drafted_question=drafted_row,
        rationale=drafted.get("rationale") or None,
        context_used=ctx,
        notes=(
            "Drafted a new question grounded in RFP context."
            if body.save_if_new and drafted_row is not None
            else "Drafted in-memory only (save_if_new=false)."
        ),
    )