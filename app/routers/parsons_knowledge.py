"""Parsons Knowledge router.

Endpoints for the new top-level "Parsons Knowledge" workspace plus the
coverage assessment that ties it back into RFP requirements.

  GET    /api/parsons-knowledge/documents
  POST   /api/parsons-knowledge/documents          (multipart upload)
  PATCH  /api/parsons-knowledge/documents/{id}     (edit category / scope etc.)
  DELETE /api/parsons-knowledge/documents/{id}

  GET    /api/parsons-knowledge/categories
  POST   /api/parsons-knowledge/categories
  DELETE /api/parsons-knowledge/categories/{slug}  (only non-system)

  POST   /api/parsons-knowledge/assess-coverage/{document_id}
  POST   /api/parsons-knowledge/assess-coverage-proposal/{proposal_id}

  GET    /api/parsons-knowledge/coverage-summary?proposal_id=&document_id=
"""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import (
    APIRouter, BackgroundTasks, Body, Depends, File, Form, HTTPException,
    Query, UploadFile,
)
from pydantic import BaseModel
from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..database import SessionLocal, get_db
from ..models import (
    DocumentChunk, IngestedDocument, ParsonsDocApproval, ParsonsDocAuditLog,
    ParsonsDocCategory, ParsonsDocSuggestion, ParsonsUploadJob,
    RfpRequirement, User,
)
from ..services.document_parser import parse_and_chunk, detect_file_type
from ..services.knowledge_base import enrich_chunks
from ..services.parsons_coverage import (
    assess_coverage_for_document, assess_coverage_for_proposal,
)
from ..services.parsons_doc_quality import (
    assess_quality_for_document, ensure_auto_approval, write_audit,
)
from ..services.parsons_upload_pipeline import create_job, launch_pipeline
from ..services.embedding_service import embed_text, cosine_similarity

logger = logging.getLogger(__name__)
router = APIRouter()


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────

# Resolved via app/paths.py — env-overridable, defaults to <workspace>/uploads.
from .. import paths as _paths
UPLOAD_DIR = str(_paths.uploads_dir())
MAX_FILE_SIZE = 200 * 1024 * 1024  # 200 MB


def _slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9_]+", "_", (s or "").lower()).strip("_")
    return s[:64] or f"cat_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"


def _doc_to_dict(d: IngestedDocument, chunk_count: Optional[int] = None,
                  pending_suggestion_count: Optional[int] = None,
                  approval_status: Optional[str] = None) -> Dict[str, Any]:
    try:
        jurisdictions = json.loads(d.jurisdictions_json) if d.jurisdictions_json else []
    except (json.JSONDecodeError, TypeError):
        jurisdictions = []
    return {
        "id": d.id,
        "filename": d.filename,
        "original_filename": d.original_filename,
        "file_type": d.file_type,
        "file_size": d.file_size,
        "description": d.description,
        "parsons_category": d.parsons_category,
        "practice_area": d.practice_area,
        "jurisdictions": jurisdictions,
        "scope_proposal_id": d.parsons_scope_proposal_id,
        "scope": "proposal" if d.parsons_scope_proposal_id else "global",
        "total_pages": d.total_pages,
        "total_chunks": chunk_count if chunk_count is not None else d.total_chunks,
        "status": d.status,
        "uploaded_by": d.uploaded_by,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        # Quality + governance metadata
        "quality_score": d.quality_score,
        "quality_headline": d.quality_headline,
        "quality_assessed_at": d.quality_assessed_at.isoformat() if d.quality_assessed_at else None,
        "pending_suggestion_count": pending_suggestion_count or 0,
        "approval_status": approval_status,
    }


# ─────────────────────────────────────────────────────────────────────
# Categories
# ─────────────────────────────────────────────────────────────────────

class CategoryCreateBody(BaseModel):
    label: str
    slug: Optional[str] = None
    description: Optional[str] = None
    icon_hint: Optional[str] = None
    sort_order: Optional[int] = None


@router.get("/categories")
def list_categories(
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    rows = (db.query(ParsonsDocCategory)
            .order_by(ParsonsDocCategory.sort_order, ParsonsDocCategory.label)
            .all())
    return {
        "count": len(rows),
        "categories": [{
            "id": r.id, "slug": r.slug, "label": r.label,
            "description": r.description, "icon_hint": r.icon_hint,
            "is_system": bool(r.is_system), "sort_order": r.sort_order,
        } for r in rows],
    }


@router.post("/categories")
def create_category(
    body: CategoryCreateBody,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Add a new user-defined category. System categories are managed via
    the seed migration; user additions here are always non-system so they
    can be deleted later."""
    label = (body.label or "").strip()
    if not label:
        raise HTTPException(400, "Label is required")
    slug = _slugify(body.slug or label)
    existing = (db.query(ParsonsDocCategory)
                .filter(ParsonsDocCategory.slug == slug).first())
    if existing:
        raise HTTPException(409, f"Category slug '{slug}' already exists")
    row = ParsonsDocCategory(
        slug=slug, label=label,
        description=(body.description or "").strip() or None,
        icon_hint=body.icon_hint, is_system=False,
        sort_order=body.sort_order if body.sort_order is not None else 200,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "id": row.id, "slug": row.slug, "label": row.label,
        "description": row.description, "icon_hint": row.icon_hint,
        "is_system": False, "sort_order": row.sort_order,
    }


@router.delete("/categories/{slug}")
def delete_category(
    slug: str,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    row = (db.query(ParsonsDocCategory)
           .filter(ParsonsDocCategory.slug == slug).first())
    if not row:
        raise HTTPException(404, "Category not found")
    if row.is_system:
        raise HTTPException(400, "System categories cannot be deleted")
    # Don't strand documents that still reference this slug.
    in_use = (db.query(sa_func.count(IngestedDocument.id))
              .filter(IngestedDocument.parsons_category == slug).scalar() or 0)
    if in_use:
        raise HTTPException(
            400,
            f"{in_use} document(s) still tagged with this category. "
            f"Reassign them first."
        )
    db.delete(row)
    db.commit()
    return {"deleted": slug}


# ─────────────────────────────────────────────────────────────────────
# Documents
# ─────────────────────────────────────────────────────────────────────

@router.get("/documents")
def list_documents(
    scope: Optional[str] = Query(None, description="'global' | 'proposal' | 'all' (default)"),
    proposal_id: Optional[int] = Query(None,
        description="When scope='proposal', limit to docs scoped to this proposal id; "
                    "when scope='all' (default) include both global + this proposal's docs"),
    parsons_category: Optional[str] = Query(None),
    practice_area: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """List Parsons-knowledge documents.

    Default behavior (scope omitted): returns the union of global Parsons
    docs + (if proposal_id is given) docs scoped to that proposal. This is
    what the per-RFP workspace wants to display.
    """
    q = (db.query(IngestedDocument)
         .filter(IngestedDocument.source_type == "parsons"))
    if scope == "global":
        q = q.filter(IngestedDocument.parsons_scope_proposal_id.is_(None))
    elif scope == "proposal":
        if proposal_id is None:
            raise HTTPException(400, "scope=proposal requires proposal_id")
        q = q.filter(IngestedDocument.parsons_scope_proposal_id == proposal_id)
    else:
        # default: union — global plus this proposal's scoped docs.
        if proposal_id is not None:
            from sqlalchemy import or_
            q = q.filter(or_(
                IngestedDocument.parsons_scope_proposal_id.is_(None),
                IngestedDocument.parsons_scope_proposal_id == proposal_id,
            ))
        else:
            q = q.filter(IngestedDocument.parsons_scope_proposal_id.is_(None))
    if parsons_category:
        q = q.filter(IngestedDocument.parsons_category == parsons_category)
    if practice_area:
        q = q.filter(IngestedDocument.practice_area == practice_area)
    docs = q.order_by(IngestedDocument.created_at.desc()).all()
    if not docs:
        return {"count": 0, "documents": []}

    # Pull pending-suggestion counts and latest approval status in batch
    # so we don't N+1 the DB.
    doc_ids = [d.id for d in docs]
    from sqlalchemy import func as _func
    pending_counts = dict(
        db.query(ParsonsDocSuggestion.document_id, _func.count(ParsonsDocSuggestion.id))
        .filter(ParsonsDocSuggestion.document_id.in_(doc_ids),
                ParsonsDocSuggestion.status == "pending")
        .group_by(ParsonsDocSuggestion.document_id).all()
    )
    approvals: Dict[int, str] = {}
    for ap in (db.query(ParsonsDocApproval)
               .filter(ParsonsDocApproval.document_id.in_(doc_ids))
               .order_by(ParsonsDocApproval.created_at.desc()).all()):
        approvals.setdefault(ap.document_id, ap.status)

    return {"count": len(docs), "documents": [
        _doc_to_dict(d,
                     pending_suggestion_count=pending_counts.get(d.id, 0),
                     approval_status=approvals.get(d.id))
        for d in docs
    ]}


@router.post("/documents")
async def upload_document(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    parsons_category: str = Form(...),
    description: Optional[str] = Form(None),
    practice_area: Optional[str] = Form(None),
    jurisdictions: Optional[str] = Form(None,
        description="JSON array of jurisdiction codes, e.g. '[\"NJ\",\"MD\"]'"),
    scope_proposal_id: Optional[int] = Form(None,
        description="Omit for global; include to scope to a specific proposal"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Upload a Parsons-knowledge document.

    The uploaded file is saved, ingested into chunks via the existing
    document_ingestion service, then enriched (embeddings + section tagging)
    in the background so coverage assessment can use it immediately on its
    next run.
    """
    # Validate category
    cat = (db.query(ParsonsDocCategory)
           .filter(ParsonsDocCategory.slug == parsons_category).first())
    if not cat:
        raise HTTPException(400, f"Unknown parsons_category: {parsons_category}")

    # Parse jurisdictions JSON if provided
    jurisdictions_list: List[str] = []
    if jurisdictions:
        try:
            parsed = json.loads(jurisdictions)
            if isinstance(parsed, list):
                jurisdictions_list = [str(x).strip().upper() for x in parsed if str(x).strip()]
        except (json.JSONDecodeError, TypeError):
            raise HTTPException(400, "jurisdictions must be a JSON array of strings")

    # Validate file type via the shared detector so we accept the same
    # extensions /api/documents/upload does.
    file_type = detect_file_type(file.filename or "unknown")
    if file_type not in ("pdf", "docx", "doc", "txt", "md", "csv", "xlsx", "xls", "pptx", "ppt"):
        raise HTTPException(400, f"Unsupported file type: .{file_type}")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(400, "Empty file")
    if len(file_bytes) > MAX_FILE_SIZE:
        raise HTTPException(400, f"File too large (max {MAX_FILE_SIZE // 1024 // 1024} MB)")

    safe_name = f"{uuid.uuid4().hex}_{file.filename}"
    saved_path = os.path.join(UPLOAD_DIR, safe_name)
    with open(saved_path, "wb") as f:
        f.write(file_bytes)

    # Persist the IngestedDocument row first so we have an id to attach chunks to.
    doc = IngestedDocument(
        filename=safe_name,
        original_filename=file.filename,
        file_type=file_type,
        file_size=len(file_bytes),
        source_type="parsons",
        proposal_id=None,                    # Parsons docs aren't owned by an RFP proposal
        description=(description or "").strip() or None,
        parsons_category=parsons_category,
        practice_area=(practice_area or "").strip() or None,
        jurisdictions_json=json.dumps(jurisdictions_list) if jurisdictions_list else None,
        parsons_scope_proposal_id=scope_proposal_id,
        status="processing",
        uploaded_by=getattr(current_user, "id", None),
    )
    db.add(doc)
    db.flush()  # populate doc.id

    try:
        result = parse_and_chunk(file_bytes, file.filename or "unknown.txt")
        doc.total_pages = result["total_pages"]
        doc.total_chunks = result["total_chunks"]
        doc.status = "completed"
        for chunk in result["chunks"]:
            db.add(DocumentChunk(
                document_id=doc.id,
                chunk_index=chunk["chunk_index"],
                page_number=chunk["page_number"],
                content=chunk["content"],
                char_count=chunk["char_count"],
                metadata_json=chunk["metadata_json"],
            ))
        db.commit()
        db.refresh(doc)
    except Exception as e:  # noqa: BLE001
        doc.status = "failed"
        doc.error_message = str(e)
        db.commit()
        logger.exception(f"Parsons knowledge ingestion failed: {e}")
        raise HTTPException(422, f"Document parsing failed: {e}")

    # Audit + auto-approval — every Parsons doc is auto-approved today;
    # the row exists so the future manual workflow drops in cleanly.
    write_audit(db, doc.id, "uploaded",
                actor_user_id=getattr(current_user, "id", None),
                actor_label=getattr(current_user, "username", None),
                payload={"original_filename": file.filename,
                         "category": parsons_category,
                         "scope_proposal_id": scope_proposal_id,
                         "size_bytes": len(file_bytes)})
    ensure_auto_approval(db, doc.id,
                         actor_user_id=getattr(current_user, "id", None),
                         actor_label=getattr(current_user, "username", None))
    db.commit()

    # Generate embeddings AND assess quality asynchronously so coverage
    # assessment + the suggestions UI work the next time the user looks.
    # Fire-and-forget — failures get logged but don't block the upload
    # response.
    def _enrich_and_assess(doc_id_: int) -> None:
        local = SessionLocal()
        try:
            enrich_chunks(local, doc_id_)
            try:
                assess_quality_for_document(local, doc_id_)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Background quality assess failed for doc {doc_id_}: {e}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Background enrich failed for doc {doc_id_}: {e}")
        finally:
            local.close()
    background.add_task(_enrich_and_assess, doc.id)

    return _doc_to_dict(doc, approval_status="auto_approved")


class DocumentPatchBody(BaseModel):
    parsons_category: Optional[str] = None
    description: Optional[str] = None
    practice_area: Optional[str] = None
    jurisdictions: Optional[List[str]] = None
    scope_proposal_id: Optional[int] = None  # set null to make global


@router.patch("/documents/{doc_id}")
def update_document(
    doc_id: int,
    body: DocumentPatchBody,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    d = (db.query(IngestedDocument)
         .filter(IngestedDocument.id == doc_id,
                 IngestedDocument.source_type == "parsons").first())
    if not d:
        raise HTTPException(404, "Parsons document not found")

    if body.parsons_category is not None:
        cat = (db.query(ParsonsDocCategory)
               .filter(ParsonsDocCategory.slug == body.parsons_category).first())
        if not cat:
            raise HTTPException(400, f"Unknown parsons_category: {body.parsons_category}")
        d.parsons_category = body.parsons_category
    if body.description is not None:
        d.description = body.description.strip() or None
    if body.practice_area is not None:
        d.practice_area = body.practice_area.strip() or None
    if body.jurisdictions is not None:
        clean = [str(x).strip().upper() for x in body.jurisdictions if str(x).strip()]
        d.jurisdictions_json = json.dumps(clean) if clean else None
    # Allow nulling the scope to make a doc global.
    if body.scope_proposal_id is not None or "scope_proposal_id" in body.model_fields_set:
        d.parsons_scope_proposal_id = body.scope_proposal_id
    db.commit()
    db.refresh(d)
    return _doc_to_dict(d)


@router.delete("/documents/{doc_id}")
def delete_document(
    doc_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    d = (db.query(IngestedDocument)
         .filter(IngestedDocument.id == doc_id,
                 IngestedDocument.source_type == "parsons").first())
    if not d:
        raise HTTPException(404, "Parsons document not found")
    # Cascade-delete chunks; the file on disk we leave (uploads dir is
    # the rest of the app's source of truth).
    db.query(DocumentChunk).filter(DocumentChunk.document_id == doc_id).delete()
    db.delete(d)
    db.commit()
    return {"deleted": doc_id}


# ─────────────────────────────────────────────────────────────────────
# Coverage assessment
# ─────────────────────────────────────────────────────────────────────

from ..services.rate_limit import enforce_rate_limit  # noqa: E402


@router.post("/assess-coverage/{document_id}", dependencies=[Depends(enforce_rate_limit("llm"))])
def assess_coverage_endpoint(
    document_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Run the coverage rubric on every requirement extracted from the
    given RFP document. Synchronous — typical doc completes in <60s for a
    few hundred requirements; for huge sets the orchestrator can move this
    behind a background task later."""
    result = assess_coverage_for_document(db, document_id)
    if isinstance(result, dict) and result.get("error"):
        raise HTTPException(400, result["error"])
    return result


@router.post("/assess-coverage-proposal/{proposal_id}", dependencies=[Depends(enforce_rate_limit("llm"))])
def assess_coverage_proposal_endpoint(
    proposal_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Run coverage across every RFP document in a proposal in one shot."""
    result = assess_coverage_for_proposal(db, proposal_id)
    if isinstance(result, dict) and result.get("error"):
        raise HTTPException(400, result["error"])
    return result


@router.get("/coverage-summary")
def coverage_summary(
    proposal_id: Optional[int] = Query(None),
    document_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Return the {covered/partial/gap/uncertain/not_assessed} counts for
    the supplied scope. Used by the dashboard donut + matrix filter."""
    q = db.query(RfpRequirement)
    if proposal_id is not None:
        q = q.filter(RfpRequirement.proposal_id == proposal_id)
    if document_id is not None:
        q = q.filter(RfpRequirement.document_id == document_id)
    rows = q.all()
    counts = {"covered": 0, "partial": 0, "gap": 0,
              "uncertain": 0, "not_assessed": 0}
    for r in rows:
        k = r.parsons_coverage_status or "not_assessed"
        counts[k] = counts.get(k, 0) + 1
    return {
        "proposal_id": proposal_id,
        "document_id": document_id,
        "total": len(rows),
        "counts": counts,
    }

# ─────────────────────────────────────────────────────────────────────
# Document content + semantic search (drives the expanded accordion)
# ─────────────────────────────────────────────────────────────────────

@router.get("/documents/{doc_id}/content")
def get_document_content(
    doc_id: int,
    q: Optional[str] = Query(None,
        description="Optional semantic query — when provided, returns chunks "
                    "ranked by cosine similarity to the query embedding"),
    limit: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Return the chunk text for a Parsons doc, optionally filtered/ranked
    by a semantic search query. The frontend's expanded-doc accordion uses
    this for both the initial content view and the in-doc search."""
    doc = (db.query(IngestedDocument)
           .filter(IngestedDocument.id == doc_id,
                   IngestedDocument.source_type == "parsons").first())
    if not doc:
        raise HTTPException(404, "Parsons document not found")

    chunks = (db.query(DocumentChunk)
              .filter(DocumentChunk.document_id == doc_id)
              .order_by(DocumentChunk.chunk_index)
              .all())
    if not chunks:
        return {"document_id": doc_id, "query": q, "chunk_count": 0,
                "chunks": [], "total_chunks": 0}

    if q:
        # Vector-rank against the query. Chunks without embeddings still get
        # returned but with similarity=null so the UI can still surface them.
        try:
            q_vec = embed_text(q)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"embed_text failed in doc-search: {e}")
            q_vec = None
        ranked = []
        for c in chunks:
            sim = None
            if q_vec is not None and c.embedding:
                try:
                    vec = json.loads(c.embedding)
                    sim = cosine_similarity(q_vec, vec)
                except (json.JSONDecodeError, TypeError):
                    sim = None
            ranked.append((c, sim))
        # Stable sort: highest similarity first, NULLs last in original order.
        ranked.sort(key=lambda x: (x[1] is None, -(x[1] or 0)))
        out = ranked[:limit]
        return {
            "document_id": doc_id, "query": q,
            "chunk_count": len(out), "total_chunks": len(chunks),
            "chunks": [
                {
                    "id": c.id, "page_number": c.page_number,
                    "chunk_index": c.chunk_index,
                    "content": c.content,
                    "similarity": sim,
                }
                for (c, sim) in out
            ],
        }

    # No query — return the first `limit` chunks in document order.
    out = chunks[:limit]
    return {
        "document_id": doc_id, "query": None,
        "chunk_count": len(out), "total_chunks": len(chunks),
        "chunks": [
            {"id": c.id, "page_number": c.page_number,
             "chunk_index": c.chunk_index, "content": c.content,
             "similarity": None}
            for c in out
        ],
    }


# ─────────────────────────────────────────────────────────────────────
# Quality assessment + suggestions
# ─────────────────────────────────────────────────────────────────────

@router.post("/documents/{doc_id}/assess-quality")
def assess_document_quality(
    doc_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Run the LLM rubric over the document. Replaces existing PENDING
    suggestions; preserves accepted/rejected/ignored ones for history."""
    result = assess_quality_for_document(
        db, doc_id,
        actor_user_id=getattr(current_user, "id", None),
        actor_label=getattr(current_user, "username", None),
    )
    if isinstance(result, dict) and result.get("error"):
        raise HTTPException(400, result["error"])
    return result


def _suggestion_to_dict(s: ParsonsDocSuggestion) -> Dict[str, Any]:
    return {
        "id": s.id,
        "document_id": s.document_id,
        "severity": s.severity,
        "title": s.title,
        "rationale": s.rationale,
        "suggested_text": s.suggested_text,
        "edit_kind": s.edit_kind,
        "target_chunk_id": s.target_chunk_id,
        "status": s.status,
        "applied_text": s.applied_text,
        "applied_at": s.applied_at.isoformat() if s.applied_at else None,
        "applied_by_user_id": s.applied_by_user_id,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


@router.get("/documents/{doc_id}/suggestions")
def list_document_suggestions(
    doc_id: int,
    status: Optional[str] = Query(None,
        description="Filter by status: pending | accepted | rejected | ignored"),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    q = db.query(ParsonsDocSuggestion).filter(
        ParsonsDocSuggestion.document_id == doc_id)
    if status:
        q = q.filter(ParsonsDocSuggestion.status == status)
    rows = q.order_by(ParsonsDocSuggestion.severity.desc(),
                      ParsonsDocSuggestion.created_at.desc()).all()
    return {"document_id": doc_id, "count": len(rows),
            "suggestions": [_suggestion_to_dict(s) for s in rows]}


class SuggestionDispositionBody(BaseModel):
    action: str  # accept | reject | ignore
    applied_text: Optional[str] = None  # required when action == "accept"
    note: Optional[str] = None


@router.post("/documents/{doc_id}/suggestions/{suggestion_id}/disposition")
def disposition_suggestion(
    doc_id: int,
    suggestion_id: int,
    body: SuggestionDispositionBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Apply a disposition to a suggestion: accept (with text), reject, or ignore.

    On accept: appends/replaces a chunk in the document with the user-
    supplied applied_text (defaults to suggested_text), re-embeds the
    affected chunk so semantic search picks it up, and writes an audit
    row tagged with both the doc and the suggestion."""
    s = (db.query(ParsonsDocSuggestion)
         .filter(ParsonsDocSuggestion.id == suggestion_id,
                 ParsonsDocSuggestion.document_id == doc_id).first())
    if not s:
        raise HTTPException(404, "Suggestion not found")
    if s.status != "pending":
        raise HTTPException(400, f"Suggestion is already {s.status}; "
                                  f"reassess the doc to surface fresh ones.")

    action = (body.action or "").lower()
    if action not in {"accept", "reject", "ignore"}:
        raise HTTPException(400, "action must be one of: accept, reject, ignore")

    now = datetime.utcnow()
    actor_id = getattr(current_user, "id", None)
    actor_label = getattr(current_user, "username", None)

    if action == "accept":
        applied_text = (body.applied_text or s.suggested_text or "").strip()
        if not applied_text:
            raise HTTPException(400, "Cannot accept a suggestion with no applied_text "
                                      "and no suggested_text on file.")
        # Apply to a chunk: either edit the target_chunk_id chunk or append a
        # new chunk to the doc. We leave the original DocumentChunk metadata
        # intact and update the embedding lazily via enrich_chunks below.
        target = None
        if s.target_chunk_id:
            target = (db.query(DocumentChunk)
                      .filter(DocumentChunk.id == s.target_chunk_id,
                              DocumentChunk.document_id == doc_id).first())
        before_text = target.content if target else None
        if target and s.edit_kind == "replace":
            target.content = applied_text
        elif target and s.edit_kind == "prepend":
            target.content = applied_text + "\n\n" + (target.content or "")
        elif target:  # default to append on the target
            target.content = (target.content or "") + "\n\n" + applied_text
        else:
            # No target chunk — create a new "edit" chunk at the end.
            doc = db.query(IngestedDocument).filter(
                IngestedDocument.id == doc_id).first()
            next_index = (doc.total_chunks or 0)
            new_chunk = DocumentChunk(
                document_id=doc_id,
                chunk_index=next_index,
                page_number=None,
                content=applied_text,
                char_count=len(applied_text),
                metadata_json=json.dumps({"origin": "user_edit",
                                          "suggestion_id": s.id}),
            )
            db.add(new_chunk)
            doc.total_chunks = next_index + 1
            db.flush()
            target = new_chunk

        s.status = "accepted"
        s.applied_text = applied_text
        s.applied_at = now
        s.applied_by_user_id = actor_id
        s.updated_at = now

        write_audit(db, doc_id, "suggestion_accepted",
                    suggestion_id=s.id, actor_user_id=actor_id,
                    actor_label=actor_label,
                    payload={"target_chunk_id": getattr(target, "id", None),
                             "before_text": before_text,
                             "after_text": applied_text,
                             "edit_kind": s.edit_kind})
        # Audit a separate content_edited row so the timeline is clean.
        write_audit(db, doc_id, "content_edited",
                    suggestion_id=s.id, actor_user_id=actor_id,
                    actor_label=actor_label,
                    payload={"chunk_id": getattr(target, "id", None),
                             "edit_kind": s.edit_kind})

        db.commit()

        # Re-embed the affected chunk(s) lazily — we just commit first then
        # ask the knowledge_base service to refresh the embedding for the
        # whole doc. Cheap because most chunks already have embeddings.
        try:
            enrich_chunks(db, doc_id)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Re-enrichment after suggestion accept failed: {e}")
    elif action == "reject":
        s.status = "rejected"
        s.updated_at = now
        write_audit(db, doc_id, "suggestion_rejected",
                    suggestion_id=s.id, actor_user_id=actor_id,
                    actor_label=actor_label,
                    note=(body.note or "").strip() or None)
        db.commit()
    else:  # ignore
        s.status = "ignored"
        s.updated_at = now
        write_audit(db, doc_id, "suggestion_ignored",
                    suggestion_id=s.id, actor_user_id=actor_id,
                    actor_label=actor_label,
                    note=(body.note or "").strip() or None)
        db.commit()

    db.refresh(s)
    return _suggestion_to_dict(s)


# ─────────────────────────────────────────────────────────────────────
# Audit log
# ─────────────────────────────────────────────────────────────────────

@router.get("/documents/{doc_id}/audit-log")
def get_document_audit_log(
    doc_id: int,
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    rows = (db.query(ParsonsDocAuditLog)
            .filter(ParsonsDocAuditLog.document_id == doc_id)
            .order_by(ParsonsDocAuditLog.created_at.desc())
            .limit(limit).all())
    out = []
    for r in rows:
        try:
            payload = json.loads(r.payload_json) if r.payload_json else {}
        except (json.JSONDecodeError, TypeError):
            payload = {}
        out.append({
            "id": r.id,
            "action": r.action,
            "actor_user_id": r.actor_user_id,
            "actor_label": r.actor_label,
            "suggestion_id": r.suggestion_id,
            "note": r.note,
            "payload": payload,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })
    return {"document_id": doc_id, "count": len(out), "events": out}


# ─────────────────────────────────────────────────────────────────────
# Upload Wizard endpoints
# ─────────────────────────────────────────────────────────────────────
# The wizard guides the user through a 4-step flow for uploading a new
# Parsons knowledge document:
#   Step 1 (frontend) — pick file + category + scope
#   Step 2 (frontend) — review predecessor candidates + flag any to supersede
#   Step 3 (backend)  — POST /upload-wizard/start kicks off the pipeline
#   Step 4 (frontend) — poll GET /upload-wizard/jobs/{id} for live progress
#
# The classic POST /documents endpoint stays as-is for any caller that
# wants the lighter path (no supersession + no coverage rerun).


def _candidate_predecessors(
    db: Session,
    parsons_category: str,
    new_doc_filename: str,
    practice_area: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return existing Parsons docs in the same category that look like
    plausible predecessors of the file the user is uploading. Used by
    the wizard to populate the 'Does this supersede an existing doc?'
    step. We never auto-mark anything — the user picks."""
    # IngestedDocument doesn't have a created_at column today, so we sort
    # by id (autoincrement = upload order) as a proxy. Newer docs first.
    candidates = (
        db.query(IngestedDocument)
        .filter(IngestedDocument.source_type == "parsons")
        .filter(IngestedDocument.parsons_category == parsons_category)
        .filter(IngestedDocument.superseded_by_document_id.is_(None))
        .order_by(IngestedDocument.id.desc())
        .limit(20)
        .all()
    )
    out: List[Dict[str, Any]] = []
    new_lc = (new_doc_filename or "").lower()
    for d in candidates:
        old_lc = (d.original_filename or d.filename or "").lower()
        # Filename-similarity hint: do the names share a meaningful prefix
        # (e.g. both start with "T1628 ..." or "PARSONS Capabilities ...")?
        prefix_match_len = 0
        for a, b in zip(new_lc, old_lc):
            if a == b:
                prefix_match_len += 1
            else:
                break
        likely = prefix_match_len >= 8 or (
            practice_area and d.practice_area
            and practice_area.lower() == d.practice_area.lower()
        )
        # quality_assessed_at is the closest "uploaded around when" signal
        # we have today; if missing fall back to None.
        uploaded_proxy = (d.quality_assessed_at.isoformat()
                          if d.quality_assessed_at else None)
        out.append({
            "id": d.id,
            "filename": d.original_filename or d.filename,
            "parsons_category": d.parsons_category,
            "practice_area": d.practice_area,
            "created_at": uploaded_proxy,
            "quality_score": d.quality_score,
            "total_chunks": d.total_chunks,
            "is_likely_predecessor": likely,
            "filename_prefix_match": prefix_match_len,
        })
    # Sort: likely-first, then most recent (highest id wins for ties)
    out.sort(key=lambda x: (not x["is_likely_predecessor"], -x["id"]))
    return out


@router.get("/upload-wizard/predecessors")
def wizard_predecessors(
    parsons_category: str = Query(...),
    new_doc_filename: str = Query(""),
    practice_area: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Step 2 of the wizard: list existing docs in the same category
    that the new upload might supersede. The UI shows these with checkboxes;
    user picks any that the new doc replaces."""
    cat = (db.query(ParsonsDocCategory)
           .filter(ParsonsDocCategory.slug == parsons_category).first())
    if not cat:
        raise HTTPException(400, f"Unknown parsons_category: {parsons_category}")
    return {
        "parsons_category": parsons_category,
        "candidates": _candidate_predecessors(
            db, parsons_category, new_doc_filename, practice_area),
    }


@router.post("/upload-wizard/start")
async def wizard_start(
    file: UploadFile = File(...),
    parsons_category: str = Form(...),
    description: Optional[str] = Form(None),
    practice_area: Optional[str] = Form(None),
    jurisdictions: Optional[str] = Form(None),
    scope_proposal_id: Optional[int] = Form(None),
    superseded_doc_ids: Optional[str] = Form(
        None, description="JSON array of doc ids this upload supersedes"),
    supersession_reason: Optional[str] = Form(None),
    rerun_coverage: bool = Form(True),
    run_classifier: bool = Form(True),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Step 3: receive the file + form, save+chunk it, create an
    IngestedDocument and a ParsonsUploadJob, then launch the background
    pipeline. Returns ``{job_id, document_id}`` immediately so the UI
    can start polling /jobs/{id} for live progress."""
    # ── Validate inputs ──────────────────────────────────────────────
    cat = (db.query(ParsonsDocCategory)
           .filter(ParsonsDocCategory.slug == parsons_category).first())
    if not cat:
        raise HTTPException(400, f"Unknown parsons_category: {parsons_category}")

    jurisdictions_list: List[str] = []
    if jurisdictions:
        try:
            parsed = json.loads(jurisdictions)
            if isinstance(parsed, list):
                jurisdictions_list = [str(x).strip().upper() for x in parsed if str(x).strip()]
        except (json.JSONDecodeError, TypeError):
            raise HTTPException(400, "jurisdictions must be a JSON array of strings")

    superseded_ids_list: List[int] = []
    if superseded_doc_ids:
        try:
            parsed = json.loads(superseded_doc_ids)
            if isinstance(parsed, list):
                superseded_ids_list = [int(x) for x in parsed]
        except (json.JSONDecodeError, TypeError, ValueError):
            raise HTTPException(400,
                "superseded_doc_ids must be a JSON array of integers")

    file_type = detect_file_type(file.filename or "unknown")
    if file_type not in ("pdf", "docx", "doc", "txt", "md", "csv", "xlsx", "xls", "pptx", "ppt"):
        raise HTTPException(400, f"Unsupported file type: .{file_type}")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(400, "Empty file")
    if len(file_bytes) > MAX_FILE_SIZE:
        raise HTTPException(400, f"File too large (max {MAX_FILE_SIZE // 1024 // 1024} MB)")

    # ── Save file + create IngestedDocument ──────────────────────────
    safe_name = f"{uuid.uuid4().hex}_{file.filename}"
    saved_path = os.path.join(UPLOAD_DIR, safe_name)
    with open(saved_path, "wb") as f:
        f.write(file_bytes)

    doc = IngestedDocument(
        filename=safe_name,
        original_filename=file.filename,
        file_type=file_type,
        file_size=len(file_bytes),
        source_type="parsons",
        proposal_id=None,
        description=(description or "").strip() or None,
        parsons_category=parsons_category,
        practice_area=(practice_area or "").strip() or None,
        jurisdictions_json=json.dumps(jurisdictions_list) if jurisdictions_list else None,
        parsons_scope_proposal_id=scope_proposal_id,
        status="processing",
        uploaded_by=getattr(current_user, "id", None),
    )
    db.add(doc)
    db.flush()  # populate doc.id

    # Chunk the file synchronously so the job has a doc to work on
    try:
        result = parse_and_chunk(file_bytes, file.filename or "unknown.txt")
        doc.total_pages = result["total_pages"]
        doc.total_chunks = result["total_chunks"]
        doc.status = "completed"
        for chunk in result["chunks"]:
            db.add(DocumentChunk(
                document_id=doc.id,
                chunk_index=chunk["chunk_index"],
                page_number=chunk["page_number"],
                content=chunk["content"],
                char_count=chunk["char_count"],
                metadata_json=chunk["metadata_json"],
            ))
        db.commit()
        db.refresh(doc)
    except Exception as e:  # noqa: BLE001
        doc.status = "failed"
        doc.error_message = str(e)
        db.commit()
        logger.exception(f"wizard ingestion failed: {e}")
        raise HTTPException(422, f"Document parsing failed: {e}")

    # Audit + auto-approval
    write_audit(db, doc.id, "uploaded",
                actor_user_id=getattr(current_user, "id", None),
                actor_label=getattr(current_user, "username", None),
                payload={"original_filename": file.filename,
                         "category": parsons_category,
                         "scope_proposal_id": scope_proposal_id,
                         "size_bytes": len(file_bytes),
                         "via": "upload_wizard"})
    ensure_auto_approval(db, doc.id,
                         actor_user_id=getattr(current_user, "id", None),
                         actor_label=getattr(current_user, "username", None))
    db.commit()

    # ── Create the upload job + launch the pipeline ──────────────────
    job = create_job(
        db,
        document_id=doc.id,
        user_id=getattr(current_user, "id", None),
        form_payload={
            "parsons_category": parsons_category,
            "description": description,
            "practice_area": practice_area,
            "jurisdictions": jurisdictions_list,
            "scope_proposal_id": scope_proposal_id,
            "superseded_doc_ids": superseded_ids_list,
            "supersession_reason": supersession_reason,
            "rerun_coverage": bool(rerun_coverage),
            "run_classifier": bool(run_classifier),
        },
    )
    # Mark the parsing stage as already complete (we did it synchronously)
    job.parsing_started_at = datetime.utcnow()
    job.parsing_completed_at = datetime.utcnow()
    db.commit()

    launch_pipeline(
        job.id,
        superseded_doc_ids=superseded_ids_list,
        supersession_reason=supersession_reason,
        rerun_coverage=bool(rerun_coverage),
        run_classifier=bool(run_classifier),
    )

    return {
        "job_id": job.id,
        "document_id": doc.id,
        "status": "running",
        "message": "Upload accepted. Pipeline running in background.",
    }


def _job_to_dict(j: ParsonsUploadJob) -> Dict[str, Any]:
    """Render a ParsonsUploadJob row for the wizard's progress UI."""
    def _iso(dt):
        return dt.isoformat() if dt else None
    try:
        superseded = json.loads(j.superseded_doc_ids) if j.superseded_doc_ids else []
    except (json.JSONDecodeError, TypeError):
        superseded = []
    return {
        "id": j.id,
        "document_id": j.document_id,
        "status": j.status,
        "current_stage": j.current_stage,
        "progress_note": j.progress_note,
        "error": j.error,
        "stages": {
            "parsing":      {"started_at": _iso(j.parsing_started_at),
                             "completed_at": _iso(j.parsing_completed_at)},
            "embedding":    {"started_at": _iso(j.embedding_started_at),
                             "completed_at": _iso(j.embedding_completed_at)},
            "quality":      {"started_at": _iso(j.quality_started_at),
                             "completed_at": _iso(j.quality_completed_at)},
            "supersession": {"started_at": _iso(j.supersession_started_at),
                             "completed_at": _iso(j.supersession_completed_at)},
            "coverage":     {"started_at": _iso(j.coverage_started_at),
                             "completed_at": _iso(j.coverage_completed_at)},
            "classify":     {"started_at": _iso(j.classify_started_at),
                             "completed_at": _iso(j.classify_completed_at)},
        },
        "summary": {
            "coverage_reqs_reassessed": j.coverage_reqs_reassessed or 0,
            "coverage_reqs_promoted":   j.coverage_reqs_promoted or 0,
            "classify_reqs_classified": j.classify_reqs_classified or 0,
            "superseded_doc_ids":       superseded,
        },
        "created_at": _iso(j.created_at),
        "completed_at": _iso(j.completed_at),
    }


@router.get("/upload-wizard/jobs/{job_id}")
def wizard_job_status(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Step 4: poll for live status. UI hits this every 2s while the
    job is running and renders the timeline of stages."""
    j = db.query(ParsonsUploadJob).filter(ParsonsUploadJob.id == job_id).first()
    if not j:
        raise HTTPException(404, f"Job {job_id} not found")
    return _job_to_dict(j)


@router.get("/upload-wizard/jobs")
def wizard_recent_jobs(
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List recent upload jobs so the wizard can show 'Recent uploads'
    after a refresh / page navigation."""
    rows = (db.query(ParsonsUploadJob)
            .order_by(ParsonsUploadJob.created_at.desc())
            .limit(limit).all())
    return [_job_to_dict(j) for j in rows]
 