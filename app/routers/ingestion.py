"""Document ingestion endpoints — upload, parse, chunk, store."""
import os
import uuid
import logging
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from sqlalchemy.orm import Session
from typing import Optional, List
from datetime import datetime

from ..database import get_db
from ..models import IngestedDocument, DocumentChunk, User, Competitor
from ..auth import get_current_user
from ..services.document_parser import parse_and_chunk, detect_file_type
from .. import paths as _paths

logger = logging.getLogger(__name__)
router = APIRouter()

# Resolved via app/paths.py — env-overridable, defaults to <workspace>/uploads.
UPLOAD_DIR = str(_paths.uploads_dir())

MAX_FILE_SIZE = 200 * 1024 * 1024  # 200 MB


# ── Upload + ingest ──────────────────────────────────────────────────

@router.post("/upload")
async def upload_and_ingest(
    file: UploadFile = File(...),
    source_type: str = Form("reference"),  # parsons, competitor_foia, competitor_proposal, rfp, reference
    competitor_id: Optional[int] = Form(None),
    proposal_id: Optional[int] = Form(None),
    description: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Upload a document, parse it, chunk it, and store everything."""
    # Validate file type
    file_type = detect_file_type(file.filename or "unknown")
    if file_type not in ("pdf", "docx", "doc", "txt", "md", "csv", "xlsx", "xls", "pptx", "ppt"):
        raise HTTPException(status_code=400, detail=f"Unsupported file type: .{file_type}")

    # Read file
    file_bytes = await file.read()
    if len(file_bytes) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail=f"File too large (max {MAX_FILE_SIZE // 1024 // 1024} MB)")
    if len(file_bytes) == 0:
        raise HTTPException(status_code=400, detail="Empty file")

    # Validate competitor_id if provided
    if competitor_id:
        comp = db.query(Competitor).filter(Competitor.id == competitor_id).first()
        if not comp:
            raise HTTPException(status_code=404, detail=f"Competitor {competitor_id} not found")

    # Save file to disk
    safe_name = f"{uuid.uuid4().hex}_{file.filename}"
    file_path = os.path.join(UPLOAD_DIR, safe_name)
    with open(file_path, "wb") as f:
        f.write(file_bytes)

    # Create DB record
    doc = IngestedDocument(
        filename=safe_name,
        original_filename=file.filename,
        file_type=file_type,
        file_size=len(file_bytes),
        source_type=source_type,
        competitor_id=competitor_id,
        proposal_id=proposal_id,
        description=description,
        status="processing",
        uploaded_by=current_user.id,
    )
    db.add(doc)
    db.flush()  # get doc.id

    # Parse and chunk
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
        logger.info(f"Ingested '{file.filename}': {doc.total_pages} pages, {doc.total_chunks} chunks")

        # Auto-enrich: embeddings, classification, section tagging, fact extraction
        try:
            from ..services.knowledge_base import enrich_chunks
            enrich_result = enrich_chunks(db, doc.id)
            logger.info(f"Auto-enriched doc {doc.id}: {enrich_result}")
        except Exception as enrich_err:
            logger.warning(f"Auto-enrichment failed (non-fatal): {enrich_err}")

    except Exception as e:
        doc.status = "failed"
        doc.error_message = str(e)
        db.commit()
        logger.error(f"Ingestion failed for '{file.filename}': {e}")
        raise HTTPException(status_code=422, detail=f"Document parsing failed: {e}")

    return {
        "id": doc.id,
        "filename": doc.original_filename,
        "file_type": doc.file_type,
        "source_type": doc.source_type,
        "total_pages": doc.total_pages,
        "total_chunks": doc.total_chunks,
        "status": doc.status,
    }


# ── List documents ───────────────────────────────────────────────────

@router.get("")
def list_ingested_documents(
    source_type: Optional[str] = Query(None),
    competitor_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = db.query(IngestedDocument).order_by(IngestedDocument.created_at.desc())
    if source_type:
        q = q.filter(IngestedDocument.source_type == source_type)
    if competitor_id:
        q = q.filter(IngestedDocument.competitor_id == competitor_id)
    docs = q.all()
    return {
        "documents": [
            {
                "id": d.id,
                "filename": d.original_filename,
                "file_type": d.file_type,
                "file_size": d.file_size,
                "source_type": d.source_type,
                "competitor_id": d.competitor_id,
                "proposal_id": d.proposal_id,
                "description": d.description,
                "total_pages": d.total_pages,
                "total_chunks": d.total_chunks,
                "status": d.status,
                "error_message": d.error_message,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
            for d in docs
        ]
    }


# ── Get single document with chunks ─────────────────────────────────

@router.get("/{document_id}")
def get_document_detail(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    doc = db.query(IngestedDocument).filter(IngestedDocument.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    chunks = (
        db.query(DocumentChunk)
        .filter(DocumentChunk.document_id == document_id)
        .order_by(DocumentChunk.chunk_index)
        .all()
    )
    return {
        "id": doc.id,
        "filename": doc.original_filename,
        "file_type": doc.file_type,
        "file_size": doc.file_size,
        "source_type": doc.source_type,
        "competitor_id": doc.competitor_id,
        "description": doc.description,
        "total_pages": doc.total_pages,
        "total_chunks": doc.total_chunks,
        "status": doc.status,
        "chunks": [
            {
                "id": c.id,
                "chunk_index": c.chunk_index,
                "page_number": c.page_number,
                "content": c.content,
                "char_count": c.char_count,
            }
            for c in chunks
        ],
    }


# ── Get chunks for a document (paginated) ───────────────────────────

@router.get("/{document_id}/chunks")
def get_document_chunks(
    document_id: int,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    doc = db.query(IngestedDocument).filter(IngestedDocument.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    chunks = (
        db.query(DocumentChunk)
        .filter(DocumentChunk.document_id == document_id)
        .order_by(DocumentChunk.chunk_index)
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "document_id": document_id,
        "total_chunks": doc.total_chunks,
        "offset": offset,
        "chunks": [
            {
                "id": c.id,
                "chunk_index": c.chunk_index,
                "page_number": c.page_number,
                "content": c.content,
                "char_count": c.char_count,
            }
            for c in chunks
        ],
    }


# ── Search across chunks (must be scoped) ───────────────────────────
# Search MUST be explicitly scoped to a proposal, source_type, or
# competitor — we do NOT allow unscoped cross-corpus full-text search
# because that would mix prior solicitations and other proposals into
# results without the user knowing what they're seeing.

@router.get("/search/chunks")
def search_chunks(
    q: str = Query(..., min_length=2, description="Search text"),
    proposal_id: Optional[int] = Query(None,
        description="Restrict search to documents belonging to this proposal"),
    source_type: Optional[str] = Query(None,
        description="Restrict search to documents with this source_type"),
    competitor_id: Optional[int] = Query(None,
        description="Restrict search to documents tagged to this competitor"),
    document_id: Optional[int] = Query(None,
        description="Restrict search to a single document"),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Full-text search across document chunks (SQLite LIKE).

    REQUIRES at least one scope filter (proposal_id, source_type,
    competitor_id, or document_id) to prevent cross-corpus contamination.
    """
    if not any([proposal_id, source_type, competitor_id, document_id]):
        raise HTTPException(
            status_code=400,
            detail=(
                "search_chunks requires at least one scope: proposal_id, "
                "source_type, competitor_id, or document_id. Unscoped "
                "search is disabled to prevent cross-corpus contamination."
            ),
        )

    query = db.query(DocumentChunk).join(
        IngestedDocument, DocumentChunk.document_id == IngestedDocument.id
    )
    if proposal_id is not None:
        query = query.filter(IngestedDocument.proposal_id == proposal_id)
    if source_type:
        query = query.filter(IngestedDocument.source_type == source_type)
    if competitor_id:
        query = query.filter(IngestedDocument.competitor_id == competitor_id)
    if document_id is not None:
        query = query.filter(DocumentChunk.document_id == document_id)

    # SQLite case-insensitive LIKE search
    query = query.filter(DocumentChunk.content.ilike(f"%{q}%"))
    results = query.limit(limit).all()

    # Surface document name + source_type so callers can see what corpus
    # each hit came from.
    doc_ids = {c.document_id for c in results if c.document_id is not None}
    doc_meta = {}
    if doc_ids:
        for d in db.query(IngestedDocument).filter(IngestedDocument.id.in_(doc_ids)).all():
            doc_meta[d.id] = {
                "filename": d.original_filename or d.filename,
                "source_type": d.source_type,
                "proposal_id": d.proposal_id,
            }

    return {
        "query": q,
        "scope": {
            "proposal_id": proposal_id, "source_type": source_type,
            "competitor_id": competitor_id, "document_id": document_id,
        },
        "results": [
            {
                "chunk_id": c.id,
                "document_id": c.document_id,
                "document_name": doc_meta.get(c.document_id, {}).get("filename"),
                "source_type": doc_meta.get(c.document_id, {}).get("source_type"),
                "proposal_id": doc_meta.get(c.document_id, {}).get("proposal_id"),
                "chunk_index": c.chunk_index,
                "page_number": c.page_number,
                "content": c.content[:500],  # truncate for listing
                "char_count": c.char_count,
            }
            for c in results
        ],
    }


# ── Delete document ──────────────────────────────────────────────────

@router.delete("/{document_id}")
def delete_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    doc = db.query(IngestedDocument).filter(IngestedDocument.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    # Delete chunks
    db.query(DocumentChunk).filter(DocumentChunk.document_id == document_id).delete()
    # Delete file from disk
    file_path = os.path.join(UPLOAD_DIR, doc.filename)
    if os.path.exists(file_path):
        os.remove(file_path)
    db.delete(doc)
    db.commit()
    return {"deleted": document_id}
