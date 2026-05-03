"""Parsons response writing endpoints.

  GET    /api/parsons-response/proposals/{id}/readiness        — agent-readiness preflight
  GET    /api/parsons-response/proposals/{id}/submission       — submission readiness rollup
  POST   /api/parsons-response/proposals/{id}/repair-embeddings — fix missing embeddings on Parsons docs

  GET    /api/parsons-response/requirements?proposal_id=&section_root=&status=
  PATCH  /api/parsons-response/requirements/{id}                — manual edit + status update
  POST   /api/parsons-response/requirements/{id}/draft          — AI-draft this requirement
  POST   /api/parsons-response/requirements/{id}/approve        — mark approved

  POST   /api/parsons-response/proposals/{id}/draft-batch       — bulk AI-draft (background task)
  GET    /api/parsons-response/draft-jobs/{job_id}              — poll bulk-draft status

  POST   /api/parsons-response/proposals/{id}/sections/{root}/assemble
                                                                — preview assembled section narrative
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..database import SessionLocal, get_db
from ..models import (
    DocumentChunk, IngestedDocument, RfpRequirement, User,
)
from ..services.knowledge_base import enrich_chunks
from ..services.parsons_response import (
    _mark_section_narrative_stale, _section_label, _section_root, _DISPOSITIONS,
    assemble_section_narrative, check_agent_readiness,
    compute_submission_readiness, draft_response_for_requirement,
)
from ..services.proposal_export import (
    compose_full_proposal, render_docx, render_markdown,
)
from ..services.response_conflicts import scan_proposal_for_conflicts
from ..services.submission_structure import (
    extract_submission_structure, map_requirements_to_submission,
)
from ..services.competitive_positioning import (
    assess_all_requirements, summarize_competitive_position,
)
from ..services.parsons_coverage import assess_coverage_for_proposal
from ..models import (
    CrossSectionConflict, ProposalSectionNarrative,
    ProposalSubmissionSection, RequirementComment,
)
from fastapi.responses import Response

logger = logging.getLogger(__name__)
router = APIRouter()


# ─────────────────────────────────────────────────────────────────────
# Telemetry — readiness + submission rollup
# ─────────────────────────────────────────────────────────────────────

@router.get("/proposals/{proposal_id}/readiness")
def get_readiness(
    proposal_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Agent-readiness preflight — Parsons corpus health, embeddings,
    coverage gaps, plus an actionable issue list."""
    return check_agent_readiness(db, proposal_id)


@router.get("/proposals/{proposal_id}/submission")
def get_submission_readiness(
    proposal_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Per-section submission rollup using the RFP's own section taxonomy
    (no manufactured categories)."""
    return compute_submission_readiness(db, proposal_id)


# ─────────────────────────────────────────────────────────────────────
# Submission structure (the RFP's OWN definition of how to submit)
# ─────────────────────────────────────────────────────────────────────

class StructureExtractBody(BaseModel):
    replace_existing: bool = False


@router.post("/proposals/{proposal_id}/structure/extract")
def trigger_structure_extraction(
    proposal_id: int,
    body: StructureExtractBody = Body(default_factory=StructureExtractBody),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Detect the RFP's own vendor-submission structure (e.g. "Forms /
    Technical Quote / State-Supplied Price Sheet") and persist it.
    Synchronous (single LLM call). Subsequent calls return the existing
    structure unless ``replace_existing=true``."""
    result = extract_submission_structure(
        db, proposal_id, replace_existing=body.replace_existing)
    if isinstance(result, dict) and result.get("error"):
        raise HTTPException(400, result["error"])
    return result


class StructureMapBody(BaseModel):
    only_unmapped: bool = True
    max_requirements: Optional[int] = None
    batch_size: int = 8


@router.post("/proposals/{proposal_id}/structure/map-requirements")
def trigger_requirement_mapping(
    proposal_id: int,
    body: StructureMapBody = Body(default_factory=StructureMapBody),
    background: BackgroundTasks = None,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Map every requirement on the proposal into one of the extracted
    submission buckets. Heuristic + LLM hybrid; runs synchronously for
    small proposals (<200 requirements), background for larger.

    Idempotent: by default skips already-mapped requirements."""
    # Count outstanding work first
    pending_count = (db.query(RfpRequirement)
                     .filter(RfpRequirement.proposal_id == proposal_id)
                     .filter(RfpRequirement.submission_section_slug.is_(None))
                     .count() if body.only_unmapped else
                     db.query(RfpRequirement)
                     .filter(RfpRequirement.proposal_id == proposal_id).count())

    if pending_count <= 200 or background is None:
        # Sync — small enough or no background available
        result = map_requirements_to_submission(
            db, proposal_id,
            batch_size=body.batch_size,
            only_unmapped=body.only_unmapped,
            max_requirements=body.max_requirements,
        )
        if isinstance(result, dict) and result.get("error"):
            raise HTTPException(400, result["error"])
        return {**result, "mode": "sync"}

    # Background path
    def _bg() -> None:
        d = SessionLocal()
        try:
            map_requirements_to_submission(
                d, proposal_id,
                batch_size=body.batch_size,
                only_unmapped=body.only_unmapped,
                max_requirements=body.max_requirements,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception(f"requirement mapping failed: {e}")
        finally:
            d.close()
    background.add_task(_bg)
    return {"proposal_id": proposal_id,
            "mode": "background",
            "pending_count": pending_count,
            "message": "Mapping started in background. Poll the submission "
                        "endpoint to see buckets fill in."}


@router.get("/proposals/{proposal_id}/structure")
def get_structure(
    proposal_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Return the extracted submission structure as a tree."""
    rows = (db.query(ProposalSubmissionSection)
            .filter(ProposalSubmissionSection.proposal_id == proposal_id)
            .order_by(ProposalSubmissionSection.is_top_level.desc(),
                       ProposalSubmissionSection.order_index)
            .all())
    if not rows:
        return {"proposal_id": proposal_id, "extracted": False,
                "top_level": []}

    by_id = {r.id: r for r in rows}
    children: Dict[int, List[Any]] = {}
    for r in rows:
        if r.parent_id:
            children.setdefault(r.parent_id, []).append(r)

    def _to_dict(r: ProposalSubmissionSection) -> Dict[str, Any]:
        return {
            "id": r.id,
            "slug": r.slug,
            "label": r.label,
            "rfp_section_ref": r.rfp_section_ref,
            "description": r.description,
            "source_page": r.source_page,
            "source_quote": r.source_quote,
            "order_index": r.order_index,
            "items": [_to_dict(c) for c in
                      sorted(children.get(r.id, []), key=lambda x: x.order_index)],
        }

    top = [r for r in rows if r.is_top_level]
    return {
        "proposal_id": proposal_id,
        "extracted": True,
        "top_level": [_to_dict(r) for r in
                       sorted(top, key=lambda x: x.order_index)],
    }


# ─────────────────────────────────────────────────────────────────────
# Coverage assessment trigger (background)
# ─────────────────────────────────────────────────────────────────────

_COVERAGE_JOBS: Dict[str, Dict[str, Any]] = {}
_COVERAGE_JOBS_LOCK = threading.Lock()


def _coverage_runner(job_id: str, proposal_id: int, resume: bool = True) -> None:
    db = SessionLocal()
    try:
        with _COVERAGE_JOBS_LOCK:
            _COVERAGE_JOBS[job_id]["status"] = "running"
            _COVERAGE_JOBS[job_id]["started_at"] = datetime.utcnow().isoformat()

        def _progress(done: int, total: int) -> None:
            with _COVERAGE_JOBS_LOCK:
                _COVERAGE_JOBS[job_id]["progress_done"] = done
                _COVERAGE_JOBS[job_id]["progress_total"] = total

        result = assess_coverage_for_proposal(
            db, proposal_id, progress_cb=_progress, resume=resume)
        with _COVERAGE_JOBS_LOCK:
            _COVERAGE_JOBS[job_id].update({
                "status": "complete",
                "completed_at": datetime.utcnow().isoformat(),
                "result": result,
            })
    except Exception as e:  # noqa: BLE001
        logger.exception(f"coverage job {job_id} failed: {e}")
        with _COVERAGE_JOBS_LOCK:
            _COVERAGE_JOBS[job_id].update({
                "status": "error", "error": str(e),
                "completed_at": datetime.utcnow().isoformat(),
            })
    finally:
        db.close()


class CoverageAssessBody(BaseModel):
    resume: bool = True  # default skips already-graded reqs


from ..services.rate_limit import enforce_rate_limit  # noqa: E402


@router.post("/proposals/{proposal_id}/coverage/assess", dependencies=[Depends(enforce_rate_limit("llm"))])
def trigger_coverage_assessment(
    proposal_id: int,
    background: BackgroundTasks,
    body: CoverageAssessBody = Body(default_factory=CoverageAssessBody),
    _user: User = Depends(get_current_user),
):
    """Run Parsons-coverage assessment across every requirement on the
    proposal in the background. UI polls /coverage/jobs/{job_id} for
    progress. This is what populates parsons_coverage_status (covered /
    partial / gap / uncertain) on each requirement.

    By default ``resume=True`` — already-graded reqs are skipped so a
    crashed/restarted job picks up where it left off. Pass ``resume=false``
    to force a full re-grade."""
    job_id = f"coverage-{proposal_id}-{uuid.uuid4().hex[:8]}"
    with _COVERAGE_JOBS_LOCK:
        _COVERAGE_JOBS[job_id] = {
            "job_id": job_id, "proposal_id": proposal_id,
            "resume": body.resume,
            "status": "queued", "started_at": None, "completed_at": None,
            "progress_done": 0, "progress_total": 0,
            "result": None, "error": None,
        }
    background.add_task(_coverage_runner, job_id, proposal_id, body.resume)
    return {"job_id": job_id, "status": "queued", "resume": body.resume}


@router.get("/coverage/jobs/{job_id}")
def get_coverage_job(job_id: str, _user: User = Depends(get_current_user)):
    with _COVERAGE_JOBS_LOCK:
        job = _COVERAGE_JOBS.get(job_id)
        if not job:
            raise HTTPException(404, "Coverage job not found")
        return dict(job)


# ─────────────────────────────────────────────────────────────────────
# Ask the RFP — vector-grounded Q&A on the current proposal's RFP docs
# ─────────────────────────────────────────────────────────────────────

class AskRfpHistoryTurn(BaseModel):
    role: str  # 'user' | 'agent'
    content: str


class AskRfpBody(BaseModel):
    question: str
    top_k: int = 10
    history: Optional[List[AskRfpHistoryTurn]] = None


@router.post("/proposals/{proposal_id}/ask", dependencies=[Depends(enforce_rate_limit("llm"))])
def ask_rfp_endpoint(
    proposal_id: int,
    body: AskRfpBody,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Answer a user's question about the current proposal's RFP using
    query expansion + multi-query vector search + grounded LLM synthesis.
    Pass ``history`` (recent user/agent turns) so the agent has memory of
    the prior conversation."""
    from ..services.rfp_qa import ask_rfp
    if not body.question or not body.question.strip():
        raise HTTPException(400, "question is required")
    history_dicts = (
        [{"role": h.role, "content": h.content} for h in body.history]
        if body.history else None
    )
    result = ask_rfp(db, proposal_id, body.question,
                     history=history_dicts, top_k=body.top_k)
    if isinstance(result, dict) and result.get("error"):
        raise HTTPException(400, result["error"])
    return result


# ─────────────────────────────────────────────────────────────────────
# Promote a chat finding into the agency-question bank
# ─────────────────────────────────────────────────────────────────────

class PromoteQuestionCitation(BaseModel):
    document_id: Optional[int] = None
    page: Optional[int] = None
    snippet: Optional[str] = None
    similarity: Optional[float] = None


class PromoteQuestionBody(BaseModel):
    """Promote something the user discovered while chatting with the RFP
    agent into an agency-submission-ready question.

    The user supplies the gist of the issue (``user_question`` — what they
    asked the agent) plus optionally the agent's answer text and the
    citations the agent showed. The endpoint:

      1. Polishes the issue into ONE well-formed interrogative clarifying
         question grounded in the agent's citations (LLM call).
      2. Runs a semantic dedup search against existing ``RfpQuestion``
         rows on the proposal.
      3. If a dedup hit exceeds ``dup_threshold``, returns it WITHOUT
         saving (mode="duplicate") so the UI can confirm before forcing.
      4. Otherwise persists a new ``RfpQuestion`` (``source_kind="ask_agent"``,
         ``created_by_ai=True``) at status="approved" — i.e. it lands in
         the bank already approved for submission, since the user is
         explicitly asking for it.

    To force-save through a dedup hit, send ``force=true``.
    """
    user_question: str
    agent_answer: Optional[str] = None
    citations: Optional[List[PromoteQuestionCitation]] = None
    category: Optional[str] = None
    priority: Optional[str] = None
    dup_threshold: float = 0.78
    force: bool = False
    polish: bool = True


def _promote_polish_question(
    user_question: str,
    agent_answer: Optional[str],
    citations: Optional[List[PromoteQuestionCitation]],
) -> Dict[str, Any]:
    """Ask the LLM to convert (user_question + agent context) into a single
    submission-ready interrogative clarifying question grounded in the
    agent's citations. Returns dict with question_text, source_section,
    source_page, source_quote, category, priority, rationale."""
    from ..services.orchestrator_agent import _call_ai

    cite_blocks: List[str] = []
    for i, c in enumerate(citations or [], start=1):
        cite_blocks.append(
            f"[Cite {i} | doc={c.document_id} | page={c.page}]\n"
            f"{(c.snippet or '').strip()[:1000]}"
        )
    cite_text = "\n\n".join(cite_blocks) if cite_blocks else "(no citations supplied)"

    sys_prompt = (
        "You are a senior Parsons capture analyst preparing a clarifying "
        "question for submission to the procurement agency during the "
        "official Q&A period. You ground every question in the RFP "
        "passages provided. You phrase questions as interrogative "
        "sentences (ending in '?') — never as imperatives. You respond "
        "with a single JSON object only."
    )
    answer_block = (agent_answer or "").strip()
    if len(answer_block) > 3000:
        answer_block = answer_block[:3000] + "…"
    user_prompt = f"""While reviewing the RFP, a Parsons capture team member discovered an issue and wants to ask the agency about it. Your job: rewrite their question into ONE submission-ready clarifying question.

USER'S ORIGINAL ASK:
{user_question!r}

THE RFP AGENT'S ANSWER (context only — DO NOT submit this back to the agency):
{answer_block or '(no answer provided)'}

RFP PASSAGES THE AGENT CITED:
{cite_text}

Produce a JSON object with these keys:
  "question_text"   (string, MUST end with '?', under 400 chars; if you can identify a section/page from the citations, prefix with 'Ref. Section X.Y (p.Z): ')
  "source_section"  (string or null — best section identifier from citations)
  "source_page"     (integer or null — best page from citations)
  "source_quote"    (string or null — short verbatim quote from a citation)
  "category"        (one of: clarification | risk | pricing | scope | competitive | form)
  "priority"        (one of: critical | high | medium | low)
  "rationale"       (1–2 sentences, internal-only, why this matters to Parsons)

Rules:
- The question must be interrogative and answerable by the agency.
- Never reveal Parsons strategy in the question text itself.
- If the citations are empty or do not support the user's ask, set "question_text" to null and explain in "rationale".

Output ONLY the JSON object. No preamble, no code fences, no commentary."""
    raw = _call_ai(user_prompt, sys_prompt, max_tokens=1200) or ""
    parsed: Dict[str, Any] = {}
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            start = raw.find("{")
            end = raw.rfind("}")
            if start >= 0 and end > start:
                try:
                    parsed = json.loads(raw[start:end + 1])
                except json.JSONDecodeError:
                    parsed = {}
    return parsed if isinstance(parsed, dict) else {}


@router.post("/proposals/{proposal_id}/promote-question")
def promote_chat_to_question(
    proposal_id: int,
    body: PromoteQuestionBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Promote a finding from an RFP-agent chat into the agency-question
    bank. Dedups against existing questions, optionally LLM-polishes the
    text, and saves at status='approved' (submission-ready)."""
    from ..models import RfpQuestion
    from ..routers.questions import (
        _score_questions_against_query,
        _get_approval_roles,
        QuestionOut,
    )
    from ..services.question_drafter import (
        ALLOWED_CATEGORIES, ALLOWED_PRIORITIES, _coerce_to_interrogative,
    )

    user_q = (body.user_question or "").strip()
    if not user_q:
        raise HTTPException(400, "user_question is required")

    # Phase 1 — polish into a submission-ready interrogative (or fall
    # back to the user's literal text if polish=False / LLM fails).
    polished: Dict[str, Any] = {}
    if body.polish:
        try:
            polished = _promote_polish_question(
                user_q, body.agent_answer, body.citations,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception(f"promote-question polish failed: {e}")
            polished = {}

    qtext = (polished.get("question_text") or "").strip()
    if not qtext:
        # Fall back to user's literal phrasing — coerce to interrogative.
        qtext = _coerce_to_interrogative(user_q)
    qtext = _coerce_to_interrogative(qtext)[:4000]

    cat = (polished.get("category") or body.category or "clarification").lower().strip()
    if cat not in ALLOWED_CATEGORIES:
        cat = "clarification"
    pri = (polished.get("priority") or body.priority or "medium").lower().strip()
    if pri not in ALLOWED_PRIORITIES:
        pri = "medium"

    src_section = polished.get("source_section") or None
    src_page = polished.get("source_page")
    if not isinstance(src_page, int):
        src_page = None
    src_quote = polished.get("source_quote") or None
    rationale = polished.get("rationale") or None

    # If polish didn't set section/page but the user supplied citations,
    # take the highest-similarity citation as the anchor.
    if (not src_section or not src_page) and body.citations:
        best = max(
            body.citations,
            key=lambda c: c.similarity if c.similarity is not None else 0.0,
        )
        if not src_page and isinstance(best.page, int):
            src_page = best.page
        if not src_quote and best.snippet:
            src_quote = best.snippet[:1500]

    # Phase 2 — dedup against existing RfpQuestion rows on this proposal.
    existing = (
        db.query(RfpQuestion)
        .filter(RfpQuestion.proposal_id == proposal_id)
        .all()
    )
    scored = _score_questions_against_query(qtext, existing, top_k=5, min_sim=0.0)
    candidates = [
        {
            "id": r.id,
            "similarity": float(s),
            "question_text": r.question_text,
            "status": r.status,
            "category": r.category,
            "priority": r.priority,
            "source_section": r.source_section,
            "source_page": r.source_page,
        }
        for r, s in scored[:5]
    ]
    duplicate = None
    if scored and scored[0][1] >= body.dup_threshold:
        top_row, top_sim = scored[0]
        duplicate = {
            "id": top_row.id,
            "similarity": float(top_sim),
            "question_text": top_row.question_text,
            "status": top_row.status,
        }
        if not body.force:
            return {
                "mode": "duplicate",
                "polished_question_text": qtext,
                "duplicate": duplicate,
                "candidates": candidates,
                "saved_question": None,
                "notes": (
                    f"Existing question #{top_row.id} matches at "
                    f"{top_sim:.2f} ≥ threshold {body.dup_threshold:.2f}. "
                    "Send force=true to save anyway."
                ),
            }

    # Phase 3 — save. Approval-role gating: if the user has an approval
    # role (or is admin), land at status='approved'. Otherwise land at
    # 'reviewed' so an authorized reviewer can sign off.
    approval_roles = _get_approval_roles(db)
    user_role = getattr(user, "role", None)
    # Use the shared helper for consistent rules across promote +
    # update + bulk-status flows.
    from .questions import _can_approve
    can_approve = _can_approve(user_role, approval_roles)
    new_status = "approved" if can_approve else "reviewed"

    row = RfpQuestion(
        document_id=None,
        proposal_id=proposal_id,
        source_section=src_section,
        source_page=src_page,
        category=cat,
        priority=pri,
        question_text=qtext,
        rationale=rationale,
        source_quote=src_quote,
        status=new_status,
        source_kind="ask_agent",
        created_by_ai=True,
        created_by_user_id=getattr(user, "id", None),
        reviewer_user_id=getattr(user, "id", None) if can_approve else None,
        review_notes=(
            "Promoted from RFP-agent chat finding."
            + (f" Original ask: {user_q[:240]}" if user_q else "")
        ),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    return {
        "mode": "saved",
        "polished_question_text": qtext,
        "duplicate": duplicate,
        "candidates": candidates,
        "saved_question": QuestionOut.model_validate(row).model_dump(),
        "status": new_status,
        "notes": (
            f"Saved as question #{row.id} at status='{new_status}'."
            + ("" if can_approve else " (Approval gating prevented direct 'approved' — "
                                       "a capture manager must sign off.)")
        ),
    }


# ─────────────────────────────────────────────────────────────────────
# Competitive positioning
# ─────────────────────────────────────────────────────────────────────

_POSITIONING_JOBS: Dict[str, Dict[str, Any]] = {}
_POSITIONING_JOBS_LOCK = threading.Lock()


class CompetitivePositioningBody(BaseModel):
    rerun: bool = False
    max_requirements: Optional[int] = None


def _positioning_runner(
    job_id: str, proposal_id: int, rerun: bool,
    max_requirements: Optional[int],
) -> None:
    db = SessionLocal()
    try:
        with _POSITIONING_JOBS_LOCK:
            _POSITIONING_JOBS[job_id]["status"] = "running"
            _POSITIONING_JOBS[job_id]["started_at"] = datetime.utcnow().isoformat()

        def _progress(done: int, total: int) -> None:
            with _POSITIONING_JOBS_LOCK:
                _POSITIONING_JOBS[job_id]["progress_done"] = done
                _POSITIONING_JOBS[job_id]["progress_total"] = total

        result = assess_all_requirements(
            db, proposal_id,
            rerun=rerun,
            max_requirements=max_requirements,
            progress_cb=_progress,
        )
        with _POSITIONING_JOBS_LOCK:
            _POSITIONING_JOBS[job_id].update({
                "status": "complete",
                "completed_at": datetime.utcnow().isoformat(),
                "result": result,
            })
    except Exception as e:  # noqa: BLE001
        logger.exception(f"positioning job {job_id} failed: {e}")
        with _POSITIONING_JOBS_LOCK:
            _POSITIONING_JOBS[job_id].update({
                "status": "error", "error": str(e),
                "completed_at": datetime.utcnow().isoformat(),
            })
    finally:
        db.close()


@router.post("/proposals/{proposal_id}/competitive/assess")
def trigger_competitive_positioning(
    proposal_id: int,
    body: CompetitivePositioningBody,
    background: BackgroundTasks,
    _user: User = Depends(get_current_user),
):
    """Run competitive positioning across all (or unassessed) requirements.
    Tags each as strong / parity / weak / neutral with named advantage and
    risk competitors. Background job; poll /competitive/jobs/{job_id}."""
    job_id = f"positioning-{proposal_id}-{uuid.uuid4().hex[:8]}"
    with _POSITIONING_JOBS_LOCK:
        _POSITIONING_JOBS[job_id] = {
            "job_id": job_id, "proposal_id": proposal_id,
            "rerun": body.rerun, "max_requirements": body.max_requirements,
            "status": "queued", "started_at": None, "completed_at": None,
            "progress_done": 0, "progress_total": 0,
            "result": None, "error": None,
        }
    background.add_task(_positioning_runner, job_id, proposal_id,
                        body.rerun, body.max_requirements)
    return {"job_id": job_id, "status": "queued"}


@router.get("/competitive/jobs/{job_id}")
def get_positioning_job(job_id: str, _user: User = Depends(get_current_user)):
    with _POSITIONING_JOBS_LOCK:
        job = _POSITIONING_JOBS.get(job_id)
        if not job:
            raise HTTPException(404, "Positioning job not found")
        return dict(job)


@router.get("/proposals/{proposal_id}/competitive/summary")
def get_competitive_summary(
    proposal_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Roll up competitive positioning across all requirements on this
    proposal — counts (strong/parity/weak/neutral/not_assessed), per-section
    breakdown, and per-competitor advantage/risk hit counts."""
    return summarize_competitive_position(db, proposal_id)


@router.post("/proposals/{proposal_id}/repair-embeddings")
def repair_embeddings(
    proposal_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Re-run enrich_chunks on every Parsons document visible to this
    proposal whose chunks are missing embeddings. Synchronous — typical
    completion is <60s for a few docs, but the user can keep clicking
    other things while it runs because each enrichment is a single doc."""
    docs = (db.query(IngestedDocument)
            .filter(IngestedDocument.source_type == "parsons")
            .filter(or_(
                IngestedDocument.parsons_scope_proposal_id.is_(None),
                IngestedDocument.parsons_scope_proposal_id == proposal_id,
            )).all())
    fixed: List[Dict[str, Any]] = []
    for d in docs:
        # Skip docs whose chunks all already have embeddings
        missing = (db.query(DocumentChunk)
                   .filter(DocumentChunk.document_id == d.id,
                           DocumentChunk.embedding.is_(None))
                   .count())
        if not missing:
            continue
        try:
            r = enrich_chunks(db, d.id)
            fixed.append({"doc_id": d.id,
                          "filename": d.original_filename,
                          "before_missing": missing,
                          "result": r})
        except Exception as e:  # noqa: BLE001
            logger.warning(f"repair_embeddings failed on doc {d.id}: {e}")
            fixed.append({"doc_id": d.id, "error": str(e)})
    return {"proposal_id": proposal_id, "repaired": fixed}


# ─────────────────────────────────────────────────────────────────────
# Per-requirement response CRUD
# ─────────────────────────────────────────────────────────────────────

def _requirement_to_dict(r: RfpRequirement) -> Dict[str, Any]:
    try:
        ev_ids = json.loads(r.parsons_evidence_doc_ids) if r.parsons_evidence_doc_ids else []
    except (json.JSONDecodeError, TypeError):
        ev_ids = []
    try:
        cited = (json.loads(r.parsons_response_cited_evidence)
                 if r.parsons_response_cited_evidence else [])
    except (json.JSONDecodeError, TypeError):
        cited = []
    return {
        "id": r.id,
        "proposal_id": r.proposal_id,
        "document_id": r.document_id,
        "requirement_id": r.requirement_id,
        "section_id": r.section_id,
        "section_root": _section_root(r.section_id),
        "category": r.category,
        "priority": r.priority,
        "title": r.title,
        "description": r.description,
        "source_text": r.source_text,
        "source_page": r.source_page,
        "compliance_status": r.compliance_status,
        "compliance_disposition": r.compliance_disposition,
        "parsons_response": r.parsons_response,
        "parsons_response_ai_original": r.parsons_response_ai_original,
        "parsons_response_status": r.parsons_response_status,
        "parsons_response_authored_by_user_id": r.parsons_response_authored_by_user_id,
        "parsons_response_assignee_id": r.parsons_response_assignee_id,
        "parsons_response_review_feedback": r.parsons_response_review_feedback,
        "parsons_response_updated_at": (
            r.parsons_response_updated_at.isoformat()
            if r.parsons_response_updated_at else None),
        "parsons_coverage_status": r.parsons_coverage_status,
        "parsons_coverage_notes": r.parsons_coverage_notes,
        "parsons_evidence_doc_ids": ev_ids,
        "parsons_response_cited_evidence_count": len(cited),
        "has_user_edits": bool(r.parsons_response_ai_original
                               and r.parsons_response
                               and r.parsons_response != r.parsons_response_ai_original),
    }


@router.get("/requirements")
def list_requirements_for_response(
    proposal_id: int = Query(...),
    section_root: Optional[str] = Query(None,
        description=("Filter by section bucket. When the proposal has an "
                     "extracted RFP submission structure, this is the slug "
                     "(e.g. 'technical_quote', 'forms', 'state_supplied_price_sheet', "
                     "'(unmapped)'). Otherwise it's the regex-derived root "
                     "(e.g. 'Section 3', 'Appendix 3').")),
    response_status: Optional[str] = Query(None,
        description="not_started | ai_drafted | user_edited | approved | exported"),
    coverage_status: Optional[str] = Query(None,
        description="covered | partial | gap | uncertain | not_assessed"),
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    q = db.query(RfpRequirement).filter(
        RfpRequirement.proposal_id == proposal_id)
    if response_status:
        q = q.filter(RfpRequirement.parsons_response_status == response_status)
    if coverage_status:
        q = q.filter(RfpRequirement.parsons_coverage_status == coverage_status)
    rows = q.order_by(RfpRequirement.section_id, RfpRequirement.id).all()

    # Bucket selection MUST match compute_submission_readiness exactly, or
    # the sidebar will show a count of N while the right pane shows zero
    # rows for that bucket. That readiness service uses RFP-defined
    # submission slugs whenever (a) the proposal has an extracted
    # ProposalSubmissionSection structure AND (b) at least one requirement
    # has been mapped (submission_section_slug not null). Otherwise it
    # falls back to the regex-derived section_root. Apply the same rule.
    if section_root:
        from ..models import ProposalSubmissionSection
        have_structure = (db.query(ProposalSubmissionSection)
                          .filter(ProposalSubmissionSection.proposal_id == proposal_id)
                          .filter(ProposalSubmissionSection.is_top_level.is_(True))
                          .count()) > 0
        mapped_count = (db.query(RfpRequirement)
                        .filter(RfpRequirement.proposal_id == proposal_id)
                        .filter(RfpRequirement.submission_section_slug.isnot(None))
                        .count())
        use_structure = have_structure and mapped_count > 0
        if use_structure:
            UNMAPPED_SLUG = "(unmapped)"
            if section_root == UNMAPPED_SLUG:
                rows = [r for r in rows if not r.submission_section_slug]
            else:
                rows = [r for r in rows if r.submission_section_slug == section_root]
        else:
            rows = [r for r in rows if _section_root(r.section_id) == section_root]
    total = len(rows)
    page = rows[offset:offset + limit]
    return {
        "proposal_id": proposal_id,
        "total": total,
        "limit": limit,
        "offset": offset,
        "requirements": [_requirement_to_dict(r) for r in page],
    }


class RequirementPatchBody(BaseModel):
    parsons_response: Optional[str] = None
    compliance_disposition: Optional[str] = None
    parsons_response_status: Optional[str] = None  # ai_drafted | user_edited | approved | exported | rejected
    parsons_response_assignee_id: Optional[int] = None


@router.patch("/requirements/{requirement_id}")
def patch_requirement_response(
    requirement_id: int,
    body: RequirementPatchBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Manual edit of a per-requirement response. The status auto-promotes
    to 'user_edited' on any text change unless an explicit status is supplied.
    Marks the matching section narrative stale on any response edit."""
    r = db.query(RfpRequirement).filter(
        RfpRequirement.id == requirement_id).first()
    if not r:
        raise HTTPException(404, "Requirement not found")

    text_changed = False
    if body.parsons_response is not None:
        r.parsons_response = body.parsons_response.strip() or None
        text_changed = True
    if body.compliance_disposition is not None:
        d = body.compliance_disposition.strip()
        if d and d not in _DISPOSITIONS:
            raise HTTPException(400, f"compliance_disposition must be one of {sorted(_DISPOSITIONS)}")
        r.compliance_disposition = d or None
    if body.parsons_response_status is not None:
        new_status = body.parsons_response_status.strip().lower()
        valid = {"not_started", "ai_drafted", "user_edited", "approved",
                 "exported", "rejected"}
        if new_status not in valid:
            raise HTTPException(400, f"status must be one of {sorted(valid)}")
        r.parsons_response_status = new_status
    elif text_changed:
        # Default promotion: AI-drafted text the user touched becomes user_edited
        r.parsons_response_status = "user_edited"

    if body.parsons_response_assignee_id is not None:
        if body.parsons_response_assignee_id == 0:
            r.parsons_response_assignee_id = None
        else:
            assignee = db.query(User).filter(
                User.id == body.parsons_response_assignee_id).first()
            if not assignee:
                raise HTTPException(400, "assignee user not found")
            r.parsons_response_assignee_id = assignee.id

    r.parsons_response_authored_by_user_id = getattr(current_user, "id", None)
    r.parsons_response_updated_at = datetime.utcnow()
    if text_changed:
        _mark_section_narrative_stale(db, r.proposal_id, _section_root(r.section_id))
    db.commit()
    db.refresh(r)
    return _requirement_to_dict(r)


class DraftRequirementBody(BaseModel):
    force: bool = False
    review_feedback: Optional[str] = None


@router.post("/requirements/{requirement_id}/draft")
def draft_requirement(
    requirement_id: int,
    body: Optional[DraftRequirementBody] = Body(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """AI-draft (or re-draft) the response for one requirement.

    By default refuses to overwrite user_edited / approved / exported
    responses. Pass `force=true` to override (useful after an explicit
    Reject + feedback). If `review_feedback` is present it's woven into
    the drafter prompt so the next draft addresses the kickback."""
    body = body or DraftRequirementBody()
    result = draft_response_for_requirement(
        db, requirement_id,
        actor_user_id=getattr(current_user, "id", None),
        force=body.force,
        review_feedback=body.review_feedback,
    )
    if isinstance(result, dict) and result.get("error"):
        raise HTTPException(400, result["error"])
    return result


class RejectRequirementBody(BaseModel):
    feedback: str
    redraft: bool = False


@router.post("/requirements/{requirement_id}/reject")
def reject_requirement(
    requirement_id: int,
    body: RejectRequirementBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Reviewer rejection: stores the feedback so the AI can address it
    on the next draft, sets status to 'rejected', and logs a system
    comment. If `redraft=true`, immediately runs a forced re-draft."""
    r = db.query(RfpRequirement).filter(
        RfpRequirement.id == requirement_id).first()
    if not r:
        raise HTTPException(404, "Requirement not found")
    feedback = (body.feedback or "").strip()
    if not feedback:
        raise HTTPException(400, "feedback text is required")
    r.parsons_response_review_feedback = feedback
    r.parsons_response_status = "rejected"
    r.parsons_response_updated_at = datetime.utcnow()
    db.add(RequirementComment(
        requirement_id=r.id,
        author_user_id=getattr(current_user, "id", None),
        author_label=getattr(current_user, "username", None) or "reviewer",
        body=f"Rejected: {feedback}",
        kind="rejection",
        created_at=datetime.utcnow(),
    ))
    db.commit()
    db.refresh(r)

    redraft_result = None
    if body.redraft:
        redraft_result = draft_response_for_requirement(
            db, requirement_id,
            actor_user_id=getattr(current_user, "id", None),
            force=True,
            review_feedback=feedback,
        )

    return {
        "requirement": _requirement_to_dict(r),
        "redraft": redraft_result,
    }


@router.post("/requirements/{requirement_id}/approve")
def approve_requirement(
    requirement_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    r = db.query(RfpRequirement).filter(
        RfpRequirement.id == requirement_id).first()
    if not r:
        raise HTTPException(404, "Requirement not found")
    if not r.parsons_response:
        raise HTTPException(400, "Cannot approve a requirement with no response yet.")
    r.parsons_response_status = "approved"
    r.parsons_response_authored_by_user_id = getattr(current_user, "id", None)
    r.parsons_response_updated_at = datetime.utcnow()
    db.commit()
    db.refresh(r)
    return _requirement_to_dict(r)


# ─────────────────────────────────────────────────────────────────────
# Bulk drafting — runs in a background thread; the UI polls a job
# ─────────────────────────────────────────────────────────────────────

_BULK_JOBS: Dict[str, Dict[str, Any]] = {}
_BULK_JOBS_LOCK = threading.Lock()


def _bulk_draft_runner(job_id: str, proposal_id: int,
                        section_root: Optional[str],
                        only_status: Optional[str]) -> None:
    db = SessionLocal()
    try:
        q = db.query(RfpRequirement).filter(
            RfpRequirement.proposal_id == proposal_id)
        if only_status:
            q = q.filter(RfpRequirement.parsons_response_status == only_status)
        else:
            # Default: only draft requirements that haven't been touched yet
            q = q.filter(or_(
                RfpRequirement.parsons_response_status.is_(None),
                RfpRequirement.parsons_response_status == "not_started",
            ))
        rows = q.all()
        if section_root:
            rows = [r for r in rows if _section_root(r.section_id) == section_root]
        with _BULK_JOBS_LOCK:
            _BULK_JOBS[job_id]["total"] = len(rows)
            _BULK_JOBS[job_id]["status"] = "running"
            _BULK_JOBS[job_id]["started_at"] = datetime.utcnow().isoformat()

        completed = 0
        errors = 0
        for r in rows:
            try:
                draft_response_for_requirement(db, r.id)
                completed += 1
            except Exception as e:  # noqa: BLE001
                logger.warning(f"bulk-draft requirement {r.id} failed: {e}")
                errors += 1
            with _BULK_JOBS_LOCK:
                _BULK_JOBS[job_id]["completed"] = completed
                _BULK_JOBS[job_id]["errors"] = errors

        with _BULK_JOBS_LOCK:
            _BULK_JOBS[job_id]["status"] = "done"
            _BULK_JOBS[job_id]["completed_at"] = datetime.utcnow().isoformat()
    except Exception as e:  # noqa: BLE001
        logger.exception(f"bulk-draft job {job_id} crashed: {e}")
        with _BULK_JOBS_LOCK:
            _BULK_JOBS[job_id]["status"] = "error"
            _BULK_JOBS[job_id]["error"] = str(e)
            _BULK_JOBS[job_id]["completed_at"] = datetime.utcnow().isoformat()
    finally:
        db.close()


class BulkDraftBody(BaseModel):
    section_root: Optional[str] = None  # restrict to one RFP-section bucket
    only_status: Optional[str] = None  # e.g. "ai_drafted" to re-draft existing


class BulkApproveBody(BaseModel):
    proposal_id: int
    only_disposition: Optional[str] = "Comply"   # Comply by default — safest
    require_evidence: bool = True                # at least one cited chunk
    section_id: Optional[str] = None             # restrict to one section
    dry_run: bool = False                        # preview the count before doing it


@router.post("/requirements/bulk-approve")
def bulk_approve_drafts(
    body: BulkApproveBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Bulk-approve drafts that meet a safety bar.

    Default policy is conservative: only AI-drafted rows with disposition
    "Comply" AND at least one cited Parsons evidence chunk get promoted
    to status='approved'. Pass dry_run=true to preview the count without
    changing anything.

    The user can flip ``only_disposition`` to None to approve all
    dispositions, or set a specific section_id to scope to one part of
    the proposal.
    """
    from sqlalchemy import or_, and_
    q = db.query(RfpRequirement).filter(
        RfpRequirement.proposal_id == body.proposal_id,
        RfpRequirement.parsons_response_status == "ai_drafted",
        RfpRequirement.parsons_response.isnot(None),
        RfpRequirement.parsons_response != "",
    )
    if body.only_disposition:
        q = q.filter(RfpRequirement.compliance_disposition == body.only_disposition)
    if body.require_evidence:
        q = q.filter(RfpRequirement.parsons_response_cited_evidence.isnot(None))
        q = q.filter(RfpRequirement.parsons_response_cited_evidence != "")
        q = q.filter(RfpRequirement.parsons_response_cited_evidence != "[]")
    if body.section_id:
        q = q.filter(RfpRequirement.section_id == body.section_id)
    rows = q.all()
    n = len(rows)
    if body.dry_run:
        return {"would_approve": n, "dry_run": True}
    now = datetime.utcnow()
    for r in rows:
        r.parsons_response_status = "approved"
        r.parsons_response_updated_at = now
    db.commit()
    return {"approved": n, "dry_run": False}


@router.post("/proposals/{proposal_id}/draft-batch")
def start_bulk_draft(
    proposal_id: int,
    body: BulkDraftBody,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Kick off a background job that AI-drafts every (or filtered)
    requirement's per-requirement response. Returns a job_id; UI polls
    GET /draft-jobs/{job_id} for status."""
    job_id = f"bulk-draft-{proposal_id}-{uuid.uuid4().hex[:8]}"
    with _BULK_JOBS_LOCK:
        _BULK_JOBS[job_id] = {
            "job_id": job_id, "proposal_id": proposal_id,
            "section_root": body.section_root, "only_status": body.only_status,
            "status": "queued", "total": 0, "completed": 0, "errors": 0,
            "started_at": None, "completed_at": None, "error": None,
        }
    background.add_task(_bulk_draft_runner, job_id, proposal_id,
                        body.section_root, body.only_status)
    return {"job_id": job_id, "status": "queued"}


@router.get("/draft-jobs/{job_id}")
def get_bulk_draft_status(
    job_id: str,
    _user: User = Depends(get_current_user),
):
    with _BULK_JOBS_LOCK:
        job = _BULK_JOBS.get(job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        return dict(job)


# ─────────────────────────────────────────────────────────────────────
# Section narrative assembly
# ─────────────────────────────────────────────────────────────────────

@router.post("/proposals/{proposal_id}/sections/{section_root}/assemble")
def assemble_section(
    proposal_id: int,
    section_root: str,
    persist: bool = Query(False, description="Persist into proposal_section_narratives"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Preview (or persist) assembled section narrative built from every
    drafted per-requirement response in this RFP-section bucket."""
    result = assemble_section_narrative(
        db, proposal_id, section_root,
        persist=persist,
        actor_user_id=getattr(current_user, "id", None),
    )
    return result


# ─────────────────────────────────────────────────────────────────────
# Evidence transparency — what chunks did the AI actually read?
# ─────────────────────────────────────────────────────────────────────

@router.get("/requirements/{requirement_id}/evidence")
def get_requirement_evidence(
    requirement_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Return the snapshot of Parsons evidence chunks the AI was shown
    when drafting this response. The user can audit each chunk's snippet,
    source document, page, and similarity to verify the draft is
    grounded in real evidence."""
    r = db.query(RfpRequirement).filter(
        RfpRequirement.id == requirement_id).first()
    if not r:
        raise HTTPException(404, "Requirement not found")
    try:
        cited = (json.loads(r.parsons_response_cited_evidence)
                 if r.parsons_response_cited_evidence else [])
    except (json.JSONDecodeError, TypeError):
        cited = []
    return {
        "requirement_id": requirement_id,
        "evidence_count": len(cited),
        "evidence": cited,
        "captured_at": (r.parsons_response_updated_at.isoformat()
                        if r.parsons_response_updated_at else None),
    }


# ─────────────────────────────────────────────────────────────────────
# Per-requirement chat — iterate on the draft with the agent
# ─────────────────────────────────────────────────────────────────────

class RequirementChatHistoryTurn(BaseModel):
    role: str  # 'user' | 'agent'
    content: str


class RequirementChatBody(BaseModel):
    message: str
    history: Optional[List[RequirementChatHistoryTurn]] = None
    top_k: int = 6


@router.post("/requirements/{requirement_id}/chat",
             dependencies=[Depends(enforce_rate_limit("llm"))])
def chat_requirement(
    requirement_id: int,
    body: RequirementChatBody,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Conversational chat scoped to ONE requirement. The agent sees the
    requirement, the current draft, and the top-K Parsons-knowledge
    snippets. Returns ``{ answer, suggested_rewrite, citations }`` so the
    UI can offer a one-click 'apply rewrite' on top of dialogue."""
    from ..services.parsons_response import chat_about_requirement
    if not body.message or not body.message.strip():
        raise HTTPException(400, "message is required")
    history = (
        [{"role": h.role, "content": h.content} for h in body.history]
        if body.history else None
    )
    result = chat_about_requirement(
        db, requirement_id, body.message,
        history=history, top_k=body.top_k,
    )
    if isinstance(result, dict) and result.get("error"):
        raise HTTPException(404 if "not found" in result["error"].lower() else 400,
                            result["error"])
    return result


# ─────────────────────────────────────────────────────────────────────
# Per-requirement competitor-prediction lookup (silent if none exist)
# ─────────────────────────────────────────────────────────────────────

@router.get("/requirements/{requirement_id}/competitor-predictions")
def list_competitor_predictions_for_requirement(
    requirement_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Return any existing competitor predictions for the SAME RFP section
    as this requirement. This is independent of the Parsons response —
    silent (empty list) when no predictions exist, never errors. The
    competitor-persona pipeline (Competitive Intel page) populates this
    table; we just surface what's already there so the user can compare
    Parsons' draft side-by-side without leaving the requirement."""
    from ..models import Competitor, CompetitorPrediction
    r = db.query(RfpRequirement).filter(
        RfpRequirement.id == requirement_id).first()
    if not r:
        raise HTTPException(404, "Requirement not found")
    if not r.section_id:
        return {"requirement_id": requirement_id,
                "section_id": None, "predictions": []}

    rows = (db.query(CompetitorPrediction)
            .filter(CompetitorPrediction.section_id == r.section_id)
            .filter(or_(CompetitorPrediction.proposal_id == r.proposal_id,
                        CompetitorPrediction.proposal_id.is_(None)))
            .order_by(CompetitorPrediction.confidence_score.desc().nullslast(),
                      CompetitorPrediction.updated_at.desc())
            .all())
    out: List[Dict[str, Any]] = []
    for p in rows:
        comp = (db.query(Competitor)
                .filter(Competitor.id == p.competitor_id).first())
        out.append({
            "id": p.id,
            "competitor_id": p.competitor_id,
            "competitor_name": comp.name if comp else f"Competitor #{p.competitor_id}",
            "section_id": p.section_id,
            "predicted_response": p.predicted_response,
            "reasoning": p.reasoning,
            "confidence_score": p.confidence_score,
            "model_used": p.model_used,
            "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        })
    return {
        "requirement_id": requirement_id,
        "section_id": r.section_id,
        "predictions": out,
    }


# ─────────────────────────────────────────────────────────────────────
# Persisted section narratives — list, fetch, edit
# ─────────────────────────────────────────────────────────────────────

def _narrative_to_dict(n: ProposalSectionNarrative) -> Dict[str, Any]:
    try:
        ids = json.loads(n.requirement_ids) if n.requirement_ids else []
    except (json.JSONDecodeError, TypeError):
        ids = []
    return {
        "id": n.id,
        "proposal_id": n.proposal_id,
        "section_root": n.section_root,
        "section_label": _section_label(n.section_root),
        "narrative_md": n.narrative_md,
        "requirement_ids": ids,
        "is_stale": bool(n.is_stale),
        "locked_by_user_id": n.locked_by_user_id,
        "last_assembled_at": n.last_assembled_at.isoformat() if n.last_assembled_at else None,
        "last_edited_at": n.last_edited_at.isoformat() if n.last_edited_at else None,
        "last_edited_by_user_id": n.last_edited_by_user_id,
    }


@router.get("/proposals/{proposal_id}/narratives")
def list_section_narratives(
    proposal_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    rows = (db.query(ProposalSectionNarrative)
            .filter(ProposalSectionNarrative.proposal_id == proposal_id)
            .order_by(ProposalSectionNarrative.section_root)
            .all())
    return {
        "proposal_id": proposal_id,
        "narratives": [_narrative_to_dict(n) for n in rows],
    }


@router.get("/proposals/{proposal_id}/narratives/{section_root:path}")
def get_section_narrative(
    proposal_id: int,
    section_root: str,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    n = (db.query(ProposalSectionNarrative)
         .filter(ProposalSectionNarrative.proposal_id == proposal_id,
                 ProposalSectionNarrative.section_root == section_root)
         .first())
    if not n:
        raise HTTPException(404, "Narrative not found")
    return _narrative_to_dict(n)


class NarrativePatchBody(BaseModel):
    narrative_md: Optional[str] = None
    is_stale: Optional[bool] = None


@router.patch("/proposals/{proposal_id}/narratives/{section_root:path}")
def patch_section_narrative(
    proposal_id: int,
    section_root: str,
    body: NarrativePatchBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Manual edit of an assembled section narrative. Once edited, it
    won't auto-overwrite on re-assembly unless the user explicitly
    re-assembles via the assemble endpoint."""
    n = (db.query(ProposalSectionNarrative)
         .filter(ProposalSectionNarrative.proposal_id == proposal_id,
                 ProposalSectionNarrative.section_root == section_root)
         .first())
    if not n:
        # Create on edit
        n = ProposalSectionNarrative(
            proposal_id=proposal_id, section_root=section_root,
        )
        db.add(n)
    if body.narrative_md is not None:
        n.narrative_md = body.narrative_md
        n.last_edited_at = datetime.utcnow()
        n.last_edited_by_user_id = getattr(current_user, "id", None)
        # User edit clears stale flag — they've now rectified it manually
        n.is_stale = False
    if body.is_stale is not None:
        n.is_stale = bool(body.is_stale)
    db.commit()
    db.refresh(n)
    return _narrative_to_dict(n)


# ─────────────────────────────────────────────────────────────────────
# Comments thread per requirement
# ─────────────────────────────────────────────────────────────────────

def _comment_to_dict(c: RequirementComment) -> Dict[str, Any]:
    return {
        "id": c.id,
        "requirement_id": c.requirement_id,
        "author_user_id": c.author_user_id,
        "author_label": c.author_label,
        "body": c.body,
        "kind": c.kind,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


@router.get("/requirements/{requirement_id}/comments")
def list_comments(
    requirement_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    rows = (db.query(RequirementComment)
            .filter(RequirementComment.requirement_id == requirement_id)
            .order_by(RequirementComment.created_at.asc())
            .all())
    return {"requirement_id": requirement_id,
            "comments": [_comment_to_dict(c) for c in rows]}


class CommentBody(BaseModel):
    body: str
    kind: Optional[str] = "comment"


@router.post("/requirements/{requirement_id}/comments")
def add_comment(
    requirement_id: int,
    body: CommentBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    text = (body.body or "").strip()
    if not text:
        raise HTTPException(400, "body is required")
    r = db.query(RfpRequirement).filter(
        RfpRequirement.id == requirement_id).first()
    if not r:
        raise HTTPException(404, "Requirement not found")
    kind = (body.kind or "comment").strip().lower()
    if kind not in ("comment", "rejection", "system"):
        kind = "comment"
    c = RequirementComment(
        requirement_id=requirement_id,
        author_user_id=getattr(current_user, "id", None),
        author_label=getattr(current_user, "username", None) or "user",
        body=text,
        kind=kind,
        created_at=datetime.utcnow(),
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return _comment_to_dict(c)


@router.delete("/comments/{comment_id}")
def delete_comment(
    comment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    c = db.query(RequirementComment).filter(
        RequirementComment.id == comment_id).first()
    if not c:
        raise HTTPException(404, "Comment not found")
    if c.author_user_id != getattr(current_user, "id", None):
        # Allow admins via role check if available; otherwise restrict.
        if not getattr(current_user, "is_admin", False):
            raise HTTPException(403, "Cannot delete other users' comments")
    db.delete(c)
    db.commit()
    return {"ok": True, "id": comment_id}


# ─────────────────────────────────────────────────────────────────────
# Cross-section conflict scanner
# ─────────────────────────────────────────────────────────────────────

_CONFLICT_JOBS: Dict[str, Dict[str, Any]] = {}
_CONFLICT_JOBS_LOCK = threading.Lock()


def _conflict_scan_runner(job_id: str, proposal_id: int) -> None:
    db = SessionLocal()
    try:
        with _CONFLICT_JOBS_LOCK:
            _CONFLICT_JOBS[job_id]["status"] = "running"
            _CONFLICT_JOBS[job_id]["started_at"] = datetime.utcnow().isoformat()
        result = scan_proposal_for_conflicts(db, proposal_id)
        with _CONFLICT_JOBS_LOCK:
            _CONFLICT_JOBS[job_id].update({
                "status": "done",
                "completed_at": datetime.utcnow().isoformat(),
                "result": result,
            })
    except Exception as e:  # noqa: BLE001
        logger.exception(f"conflict-scan job {job_id} failed: {e}")
        with _CONFLICT_JOBS_LOCK:
            _CONFLICT_JOBS[job_id].update({
                "status": "error",
                "error": str(e),
                "completed_at": datetime.utcnow().isoformat(),
            })
    finally:
        db.close()


@router.post("/proposals/{proposal_id}/conflicts/scan")
def start_conflict_scan(
    proposal_id: int,
    background: BackgroundTasks,
    _user: User = Depends(get_current_user),
):
    """Kick off a cross-section conflict scan in the background.
    Returns a job_id; UI polls /conflicts/jobs/{job_id} for completion."""
    job_id = f"conflict-{proposal_id}-{uuid.uuid4().hex[:8]}"
    with _CONFLICT_JOBS_LOCK:
        _CONFLICT_JOBS[job_id] = {
            "job_id": job_id,
            "proposal_id": proposal_id,
            "status": "queued",
            "started_at": None,
            "completed_at": None,
            "result": None,
            "error": None,
        }
    background.add_task(_conflict_scan_runner, job_id, proposal_id)
    return {"job_id": job_id, "status": "queued"}


@router.get("/conflicts/jobs/{job_id}")
def get_conflict_job(job_id: str, _user: User = Depends(get_current_user)):
    with _CONFLICT_JOBS_LOCK:
        job = _CONFLICT_JOBS.get(job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        return dict(job)


def _conflict_to_dict(c: CrossSectionConflict) -> Dict[str, Any]:
    try:
        ids_a = json.loads(c.requirement_ids_a) if c.requirement_ids_a else []
    except (json.JSONDecodeError, TypeError):
        ids_a = []
    try:
        ids_b = json.loads(c.requirement_ids_b) if c.requirement_ids_b else []
    except (json.JSONDecodeError, TypeError):
        ids_b = []
    return {
        "id": c.id,
        "proposal_id": c.proposal_id,
        "section_root_a": c.section_root_a,
        "section_root_b": c.section_root_b,
        "section_label_a": _section_label(c.section_root_a),
        "section_label_b": _section_label(c.section_root_b),
        "severity": c.severity,
        "summary": c.summary,
        "detail": c.detail,
        "requirement_ids_a": ids_a,
        "requirement_ids_b": ids_b,
        "status": c.status,
        "scan_id": c.scan_id,
        "resolved_by_user_id": c.resolved_by_user_id,
        "resolved_at": c.resolved_at.isoformat() if c.resolved_at else None,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


@router.get("/proposals/{proposal_id}/conflicts")
def list_conflicts(
    proposal_id: int,
    status: Optional[str] = Query(None, description="open | dismissed | resolved"),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    q = (db.query(CrossSectionConflict)
         .filter(CrossSectionConflict.proposal_id == proposal_id))
    if status:
        q = q.filter(CrossSectionConflict.status == status)
    rows = q.order_by(CrossSectionConflict.created_at.desc()).all()
    return {
        "proposal_id": proposal_id,
        "total": len(rows),
        "conflicts": [_conflict_to_dict(c) for c in rows],
    }


class ConflictPatchBody(BaseModel):
    status: str  # open | dismissed | resolved


@router.patch("/conflicts/{conflict_id}")
def patch_conflict(
    conflict_id: int,
    body: ConflictPatchBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    c = db.query(CrossSectionConflict).filter(
        CrossSectionConflict.id == conflict_id).first()
    if not c:
        raise HTTPException(404, "Conflict not found")
    new_status = body.status.strip().lower()
    if new_status not in ("open", "dismissed", "resolved"):
        raise HTTPException(400, "status must be open|dismissed|resolved")
    c.status = new_status
    if new_status in ("dismissed", "resolved"):
        c.resolved_by_user_id = getattr(current_user, "id", None)
        c.resolved_at = datetime.utcnow()
    else:
        c.resolved_by_user_id = None
        c.resolved_at = None
    db.commit()
    db.refresh(c)
    return _conflict_to_dict(c)


# ─────────────────────────────────────────────────────────────────────
# Full proposal export
# ─────────────────────────────────────────────────────────────────────

class ExportBody(BaseModel):
    format: str = "markdown"  # markdown | docx | json
    only_approved: bool = False
    reassemble_stale: bool = True
    mark_exported: bool = False  # promote included responses to status=exported


@router.post("/proposals/{proposal_id}/export")
def export_proposal(
    proposal_id: int,
    body: ExportBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Export the full proposal draft as Markdown, DOCX, or structured JSON.

    For each section:
      * uses the persisted assembled narrative when fresh
      * re-assembles when stale (unless ``reassemble_stale=false``)
      * falls back to deterministic concatenation if the LLM call fails

    When ``mark_exported=true``, every requirement included in the export
    has its status promoted to ``exported`` so subsequent edits surface
    a "this is shipped" warning."""
    payload = compose_full_proposal(
        db, proposal_id,
        reassemble_stale=body.reassemble_stale,
        only_approved=body.only_approved,
    )
    if isinstance(payload, dict) and payload.get("error"):
        raise HTTPException(404, payload["error"])

    if body.mark_exported:
        included_ids: List[int] = []
        for sec in payload.get("sections", []):
            for r in sec.get("requirements", []):
                if (r.get("response")
                        and (r.get("status") or "") in
                        ("ai_drafted", "user_edited", "approved")):
                    included_ids.append(r["id"])
        if included_ids:
            (db.query(RfpRequirement)
             .filter(RfpRequirement.id.in_(included_ids))
             .update({"parsons_response_status": "exported",
                      "parsons_response_updated_at": datetime.utcnow()},
                     synchronize_session=False))
            db.commit()

    fmt = (body.format or "markdown").lower()
    safe_name = "".join(c for c in (payload.get("proposal_name", "proposal"))
                        if c.isalnum() or c in (" ", "_", "-")).strip().replace(" ", "_") or "proposal"

    if fmt == "json":
        return payload
    if fmt == "markdown":
        md = render_markdown(payload)
        return Response(
            content=md,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition":
                     f'attachment; filename="{safe_name}_draft.md"'},
        )
    if fmt == "docx":
        try:
            blob = render_docx(payload)
        except Exception as e:  # noqa: BLE001
            logger.exception(f"DOCX render failed: {e}")
            raise HTTPException(500, f"DOCX render failed: {e}")
        return Response(
            content=blob,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition":
                     f'attachment; filename="{safe_name}_draft.docx"'},
        )
    raise HTTPException(400, "format must be markdown | docx | json")


@router.get("/proposals/{proposal_id}/export/preview")
def preview_export(
    proposal_id: int,
    only_approved: bool = Query(False),
    reassemble_stale: bool = Query(False),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Cheap preview of the export payload without re-assembling stale
    sections (default). Useful for the export button to show counts /
    confirm before triggering the (potentially expensive) full export."""
    payload = compose_full_proposal(
        db, proposal_id,
        reassemble_stale=reassemble_stale,
        only_approved=only_approved,
    )
    if isinstance(payload, dict) and payload.get("error"):
        raise HTTPException(404, payload["error"])
    # Strip narrative bodies for cheap response — UI just needs counts/sources
    summary = {
        "proposal_id": payload["proposal_id"],
        "proposal_name": payload["proposal_name"],
        "section_count": len(payload["sections"]),
        "sections": [
            {
                "section_root": s["section_root"],
                "label": s["label"],
                "stats": s["stats"],
                "source": s["source"],
                "has_narrative": bool(s.get("narrative_md")),
            }
            for s in payload["sections"]
        ],
        "totals": {
            "requirements": sum(s["stats"]["total"] for s in payload["sections"]),
            "drafted": sum(s["stats"]["drafted"] for s in payload["sections"]),
            "approved": sum(s["stats"]["approved"] for s in payload["sections"]),
            "included_in_export": sum(s["stats"]["included_in_export"] for s in payload["sections"]),
        },
    }
    return summary
