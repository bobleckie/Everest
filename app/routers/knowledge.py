"""Knowledge Base + Orchestrator endpoints."""
import json
import time
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Any, Dict, List, Optional

from ..database import get_db
from ..models import (
    IngestedDocument, DocumentChunk, RfpRequirement, ExtractedFact,
    AgentConversation, AgentMessage, AppSetting, User,
)
from ..auth import get_current_user
from ..services.knowledge_base import enrich_chunks, semantic_search, assess_readiness
from ..services.persona_factory import seed_system_personas
from ..services.orchestrator_agent import (
    extract_rfp_requirements, run_persona_task, run_consensus_loop,
    get_conversation_history,
)

router = APIRouter()


# ── Per-document extraction-duration cache ───────────────────────────
# We persist the wall-clock duration of the most recent successful extraction
# per document so the frontend can render an ETA on the re-run confirmation
# dialog ("ETA based on the previous run was N minutes"). Stored in
# AppSetting under key ``extraction_duration::doc_<id>`` as a JSON object
# {seconds: int, finished_at: ISO8601}.

def _extraction_duration_key(document_id: int) -> str:
    return f"extraction_duration::doc_{document_id}"


def _save_extraction_duration(db: Session, document_id: int, seconds: float) -> None:
    """Persist most recent extraction wall-clock so a re-run can show an ETA."""
    from datetime import datetime
    payload = {"seconds": int(seconds), "finished_at": datetime.utcnow().isoformat()}
    key = _extraction_duration_key(document_id)
    row = db.query(AppSetting).filter(AppSetting.key == key).first()
    value_json = json.dumps(payload)
    if row:
        row.value_json = value_json
    else:
        db.add(AppSetting(key=key, value_json=value_json))
    db.commit()


def _load_extraction_duration(db: Session, document_id: int) -> Optional[Dict[str, Any]]:
    row = (db.query(AppSetting)
           .filter(AppSetting.key == _extraction_duration_key(document_id))
           .first())
    if not row or not row.value_json:
        return None
    try:
        return json.loads(row.value_json)
    except (json.JSONDecodeError, TypeError):
        return None


# ── Enrichment ───────────────────────────────────────────────────────

@router.post("/enrich/{document_id}")
def enrich_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Run smart enrichment on a document: embeddings, classification, section tagging, fact extraction."""
    result = enrich_chunks(db, document_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


# ── Semantic Search ──────────────────────────────────────────────────

@router.get("/search")
def knowledge_search(
    q: str = Query(..., min_length=2),
    source_type: Optional[str] = Query(None),
    competitor_id: Optional[int] = Query(None),
    section_id: Optional[str] = Query(None),
    top_k: int = Query(10, ge=1, le=50),
    document_ids: Optional[str] = Query(
        None,
        description="Comma-separated list of document IDs to restrict the search to (e.g. '5,6,9'). Omit for the full knowledge base.",
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Semantic search across the knowledge base.

    Scope can be narrowed by any combination of:
      - `source_type` (parsons / rfp / competitor_foia / …)
      - `competitor_id`
      - `section_id`
      - `document_ids` — explicit list of documents for targeted multi-doc search
    """
    doc_id_list: Optional[List[int]] = None
    if document_ids:
        try:
            doc_id_list = [int(x) for x in document_ids.split(",") if x.strip()]
        except ValueError:
            raise HTTPException(status_code=400, detail="document_ids must be comma-separated integers")
        if not doc_id_list:
            doc_id_list = None
    results = semantic_search(
        db, q, source_type, competitor_id, section_id, top_k,
        document_ids=doc_id_list,
    )
    return {
        "query": q,
        "scope": {
            "source_type": source_type,
            "competitor_id": competitor_id,
            "section_id": section_id,
            "document_ids": doc_id_list,
        },
        "results": results,
    }


# ── Readiness Assessment ─────────────────────────────────────────────

@router.get("/readiness")
def get_readiness(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Assess knowledge base coverage per RFP section."""
    return assess_readiness(db)


# ── RFP Requirement Extraction (triple-checked) ─────────────────────

def _extract_and_persist(document_id: int, db: Session) -> dict:
    """Shared extraction body used by both sync and async paths.

    Runs the triple-checked pipeline, records duration, kicks off
    Parsons-coverage assessment. Returns the result dict with
    `elapsed_seconds` and (when available) `parsons_coverage` attached.
    """
    started = time.time()
    result = extract_rfp_requirements(db, document_id)
    elapsed = time.time() - started
    if isinstance(result, dict) and result.get("error"):
        # Surface inline so caller (sync path) can raise HTTPException.
        return result
    try:
        _save_extraction_duration(db, document_id, elapsed)
    except Exception:  # noqa: BLE001 — duration is informational
        pass
    try:
        from ..services.parsons_coverage import assess_coverage_for_document
        coverage = assess_coverage_for_document(db, document_id)
        if isinstance(coverage, dict) and not coverage.get("error"):
            if isinstance(result, dict):
                result["parsons_coverage"] = coverage
    except Exception as e:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning(
            f"Auto coverage assessment failed for doc {document_id}: {e}"
        )
    if isinstance(result, dict):
        result["elapsed_seconds"] = int(elapsed)
    return result


from ..services.rate_limit import enforce_rate_limit  # noqa: E402  (placed near use site)


@router.post("/extract-requirements/{document_id}", dependencies=[Depends(enforce_rate_limit("llm"))])
def extract_requirements(
    document_id: int,
    async_: bool = Query(False, alias="async", description="Run in a background job; returns 202 with job id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Run the triple-checked RFP extraction pipeline on a document.

    Default is SYNCHRONOUS for backwards compatibility — request blocks
    until the extraction finishes. Pass `?async=true` to fire a
    background job and poll `GET /api/jobs/{id}` for status. The async
    path is the recommended one for browsers (the sync version can take
    5+ minutes and exceed proxy/browser timeouts).
    """
    doc = db.query(IngestedDocument).filter(IngestedDocument.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.source_type != "rfp":
        raise HTTPException(status_code=400, detail="Document must be tagged as source_type='rfp'")

    # ── Async path ──────────────────────────────────────────────────
    # Spawn a daemon thread with its OWN SQLAlchemy session — the
    # request-scoped `db` here is closed as soon as we return.
    if async_:
        from ..services import jobs as job_registry
        from ..database import SessionLocal
        from fastapi.responses import JSONResponse

        uid = getattr(current_user, "id", None)

        def _target(_job):
            worker_db = SessionLocal()
            try:
                return _extract_and_persist(document_id, worker_db)
            finally:
                worker_db.close()

        job = job_registry.submit(
            "extract_requirements", _target,
            doc_id=document_id, user_id=uid,
            meta={"filename": getattr(doc, "filename", None)},
        )
        return JSONResponse(status_code=202, content={
            "accepted": True,
            "job_id": job.id,
            "status": job.status,
            "doc_id": document_id,
            "poll_url": f"/api/jobs/{job.id}",
        })

    # ── Sync path (legacy default) ──────────────────────────────────
    started = time.time()
    result = extract_rfp_requirements(db, document_id)
    elapsed = time.time() - started
    if isinstance(result, dict) and result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    # Successful run — record duration so the next re-run has an ETA basis.
    try:
        _save_extraction_duration(db, document_id, elapsed)
    except Exception:  # noqa: BLE001 — duration is informational, never block on it
        pass

    # Auto-run Parsons coverage assessment so the user lands in a useful
    # state with coverage chips already populated. Failures are non-fatal —
    # the user can manually re-run from the Parsons Knowledge page.
    try:
        from ..services.parsons_coverage import assess_coverage_for_document
        coverage = assess_coverage_for_document(db, document_id)
        if isinstance(coverage, dict) and not coverage.get("error"):
            if isinstance(result, dict):
                result["parsons_coverage"] = coverage
    except Exception as e:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning(
            f"Auto coverage assessment failed for doc {document_id}: {e}"
        )

    if isinstance(result, dict):
        result["elapsed_seconds"] = int(elapsed)
    return result


@router.get("/extract-requirements/{document_id}/last-run")
def get_extract_last_run(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return the most recent extraction duration (seconds + finished_at) for
    the given document, or 404 if it has never been extracted. The frontend
    uses this to render an ETA on the re-run confirmation dialog."""
    doc = db.query(IngestedDocument).filter(IngestedDocument.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    duration = _load_extraction_duration(db, document_id)
    # Also surface the latest requirement-row created_at as a secondary signal —
    # the duration cache may be empty (e.g. extraction ran before this code shipped).
    latest_req_at = (
        db.query(sa_func.max(RfpRequirement.created_at))
        .filter(RfpRequirement.document_id == document_id)
        .scalar()
    )
    req_count = (
        db.query(sa_func.count(RfpRequirement.id))
        .filter(RfpRequirement.document_id == document_id)
        .scalar()
    )
    return {
        "document_id": document_id,
        "requirement_count": req_count or 0,
        "last_extracted_at": latest_req_at.isoformat() if latest_req_at else None,
        "last_duration_seconds": (duration or {}).get("seconds"),
        "last_duration_finished_at": (duration or {}).get("finished_at"),
    }


# ── Requirements CRUD ────────────────────────────────────────────────

@router.get("/requirements")
def list_requirements(
    proposal_id: Optional[int] = Query(None),
    document_id: Optional[int] = Query(None),
    section_id: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    compliance_status: Optional[str] = Query(None),
    parsons_coverage_status: Optional[str] = Query(None,
        description="Filter by Parsons-evidence coverage: covered | partial | gap | uncertain | not_assessed"),
    search: Optional[str] = Query(None, description="Case-insensitive substring match against title/description/source_text/section_id/requirement_id"),
    limit: Optional[int] = Query(None, ge=1, le=10000, description="Max rows to return (omit for all)"),
    offset: int = Query(0, ge=0, description="Rows to skip"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from sqlalchemy import or_, func as sa_func

    q = db.query(RfpRequirement)
    if proposal_id is not None:
        q = q.filter(RfpRequirement.proposal_id == proposal_id)
    if document_id is not None:
        q = q.filter(RfpRequirement.document_id == document_id)
    if section_id:
        q = q.filter(RfpRequirement.section_id == section_id)
    if category:
        q = q.filter(RfpRequirement.category == category)
    if priority:
        q = q.filter(RfpRequirement.priority == priority)
    if compliance_status:
        q = q.filter(RfpRequirement.compliance_status == compliance_status)
    if parsons_coverage_status:
        q = q.filter(RfpRequirement.parsons_coverage_status == parsons_coverage_status)
    if search:
        term = f"%{search.strip()}%"
        q = q.filter(or_(
            RfpRequirement.title.ilike(term),
            RfpRequirement.description.ilike(term),
            RfpRequirement.source_text.ilike(term),
            RfpRequirement.section_id.ilike(term),
            RfpRequirement.requirement_id.ilike(term),
        ))

    # Total count (before pagination) so the UI can render page controls
    total = q.with_entities(sa_func.count(RfpRequirement.id)).scalar()

    q = q.order_by(RfpRequirement.document_id, RfpRequirement.requirement_id)
    if offset:
        q = q.offset(offset)
    if limit is not None:
        q = q.limit(limit)
    reqs = q.all()

    # Batch-load document names for display.
    doc_ids = {r.document_id for r in reqs if r.document_id is not None}
    docs = (
        db.query(IngestedDocument)
        .filter(IngestedDocument.id.in_(doc_ids))
        .all()
        if doc_ids else []
    )
    doc_name = {d.id: (d.original_filename or d.filename) for d in docs}

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "returned": len(reqs),
        "requirements": [
            {
                "id": r.id,
                "proposal_id": r.proposal_id,
                "document_id": r.document_id,
                "document_name": doc_name.get(r.document_id),
                "requirement_id": r.requirement_id,
                "section_id": r.section_id,
                "category": r.category,
                "priority": r.priority,
                "title": r.title,
                "description": r.description,
                "source_text": r.source_text,
                "source_page": r.source_page,
                "extraction_pass": r.extraction_pass,
                "reviewer_confidence": r.reviewer_confidence,
                "notes": r.notes,
                "compliance_status": r.compliance_status,
                "parsons_evidence": r.parsons_evidence,
                "parsons_coverage_status": r.parsons_coverage_status,
                "parsons_coverage_notes": r.parsons_coverage_notes,
                "parsons_evidence_doc_ids": (
                    json.loads(r.parsons_evidence_doc_ids)
                    if r.parsons_evidence_doc_ids else []
                ),
                "verified": r.verified,
                "verified_by": r.verified_by,
                "verification_notes": r.verification_notes,
            }
            for r in reqs
        ],
    }


@router.get("/requirements/summary")
def requirements_summary(
    proposal_id: int = Query(..., description="Proposal ID to summarize"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Compliance Matrix summary: per-document + per-category rollups + totals."""
    reqs = (
        db.query(RfpRequirement)
        .filter(RfpRequirement.proposal_id == proposal_id)
        .all()
    )
    docs = (
        db.query(IngestedDocument)
        .filter(IngestedDocument.id.in_({r.document_id for r in reqs if r.document_id}))
        .all()
    )
    doc_name = {d.id: (d.original_filename or d.filename) for d in docs}

    # Per-document rollup
    by_doc: dict = {}
    for r in reqs:
        d = by_doc.setdefault(r.document_id, {
            "document_id": r.document_id,
            "document_name": doc_name.get(r.document_id, f"Doc {r.document_id}"),
            "total": 0,
            "by_category": {},
            "by_priority": {},
            "by_compliance": {},
            "verified": 0,
        })
        d["total"] += 1
        d["by_category"][r.category or "unknown"] = d["by_category"].get(r.category or "unknown", 0) + 1
        d["by_priority"][r.priority or "unknown"] = d["by_priority"].get(r.priority or "unknown", 0) + 1
        cs = r.compliance_status or "not_reviewed"
        d["by_compliance"][cs] = d["by_compliance"].get(cs, 0) + 1
        if r.verified:
            d["verified"] += 1

    totals = {
        "total": len(reqs),
        "by_category": {},
        "by_priority": {},
        "by_compliance": {},
        "by_section": {},
        "verified": sum(1 for r in reqs if r.verified),
    }
    for r in reqs:
        totals["by_category"][r.category or "unknown"] = totals["by_category"].get(r.category or "unknown", 0) + 1
        totals["by_priority"][r.priority or "unknown"] = totals["by_priority"].get(r.priority or "unknown", 0) + 1
        cs = r.compliance_status or "not_reviewed"
        totals["by_compliance"][cs] = totals["by_compliance"].get(cs, 0) + 1
        if r.section_id:
            totals["by_section"][r.section_id] = totals["by_section"].get(r.section_id, 0) + 1

    # Annotate each per-document row with the latest requirement timestamp
    # (a proxy for "when was this document last extracted") and the cached
    # wall-clock duration so the UI can compute an ETA before re-running.
    if by_doc:
        latest_per_doc = dict(
            db.query(RfpRequirement.document_id,
                     sa_func.max(RfpRequirement.created_at))
            .filter(RfpRequirement.document_id.in_(by_doc.keys()))
            .group_by(RfpRequirement.document_id)
            .all()
        )
        for doc_id, d in by_doc.items():
            ts = latest_per_doc.get(doc_id)
            d["last_extracted_at"] = ts.isoformat() if ts else None
            duration = _load_extraction_duration(db, doc_id) if doc_id else None
            d["last_duration_seconds"] = (duration or {}).get("seconds")

    return {
        "proposal_id": proposal_id,
        "totals": totals,
        "by_document": sorted(by_doc.values(), key=lambda x: x["document_id"] or 0),
    }


# ── Requirements grouped by section (read-only) ──────────────────────

def _section_sort_key(section_id: str):
    """
    Natural sort for section identifiers like "3", "3.2", "3.2H", "3.39.2.2",
    "Appendix 3", "Appendix F - Table F-1". Numeric parts sort numerically;
    everything else falls back to case-insensitive string order AFTER numeric
    sections so "Appendix …" lands at the end.
    """
    if not section_id:
        return (1, "", ())
    s = section_id.strip()
    # Numeric-dotted prefix (e.g. "3.39.2.2H" -> [3, 39, 2, 2]); anything with
    # no leading digit is treated as non-numeric and sorts after numerics.
    import re
    m = re.match(r"^(\d+(?:\.\d+)*)(.*)$", s)
    if m:
        nums = tuple(int(p) for p in m.group(1).split("."))
        tail = m.group(2).lower()
        return (0, nums, tail)
    return (1, s.lower(), ())


@router.get("/requirements/by-section")
def list_requirements_by_section(
    proposal_id: Optional[int] = Query(None),
    document_id: Optional[int] = Query(None),
    category: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    compliance_status: Optional[str] = Query(None),
    search: Optional[str] = Query(None, description="Case-insensitive substring match against title/description/source_text/section_id/requirement_id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Read-only view of RfpRequirement grouped hierarchically by section_id and
    then by category. Intended for accordion-style UIs.

    Filters match /requirements (same semantics). No pagination — the grouping
    is meant to be loaded once per filter set and rendered client-side.
    Rows with no section_id are bucketed under "(Unsectioned)" so nothing is
    silently dropped.
    """
    from sqlalchemy import or_

    q = db.query(RfpRequirement)
    if proposal_id is not None:
        q = q.filter(RfpRequirement.proposal_id == proposal_id)
    if document_id is not None:
        q = q.filter(RfpRequirement.document_id == document_id)
    if category:
        q = q.filter(RfpRequirement.category == category)
    if priority:
        q = q.filter(RfpRequirement.priority == priority)
    if compliance_status:
        q = q.filter(RfpRequirement.compliance_status == compliance_status)
    if search:
        term = f"%{search.strip()}%"
        q = q.filter(or_(
            RfpRequirement.title.ilike(term),
            RfpRequirement.description.ilike(term),
            RfpRequirement.source_text.ilike(term),
            RfpRequirement.section_id.ilike(term),
            RfpRequirement.requirement_id.ilike(term),
        ))
    # Stable per-section row order: by document, then requirement_id.
    reqs = q.order_by(
        RfpRequirement.document_id, RfpRequirement.requirement_id
    ).all()

    # Batch-load document names.
    doc_ids = {r.document_id for r in reqs if r.document_id is not None}
    docs = (
        db.query(IngestedDocument)
        .filter(IngestedDocument.id.in_(doc_ids))
        .all()
        if doc_ids else []
    )
    doc_name = {d.id: (d.original_filename or d.filename) for d in docs}

    # Bucket by section, then by category inside each section.
    UNSECTIONED = "(Unsectioned)"
    sections: dict = {}
    for r in reqs:
        sid = (r.section_id or "").strip() or UNSECTIONED
        bucket = sections.setdefault(sid, {
            "section_id": sid,
            "total": 0,
            "by_category": {},  # category -> count
            "by_priority": {},
            "by_compliance": {},
            "verified": 0,
            "documents": set(),
            "pages": set(),
            "categories": {},  # category -> list of requirement dicts (ordered)
        })
        bucket["total"] += 1
        cat = (r.category or "unknown")
        bucket["by_category"][cat] = bucket["by_category"].get(cat, 0) + 1
        pri = (r.priority or "unknown")
        bucket["by_priority"][pri] = bucket["by_priority"].get(pri, 0) + 1
        cs = r.compliance_status or "not_reviewed"
        bucket["by_compliance"][cs] = bucket["by_compliance"].get(cs, 0) + 1
        if r.verified:
            bucket["verified"] += 1
        if r.document_id is not None:
            bucket["documents"].add(r.document_id)
        if r.source_page is not None:
            bucket["pages"].add(r.source_page)
        bucket["categories"].setdefault(cat, []).append({
            "id": r.id,
            "proposal_id": r.proposal_id,
            "document_id": r.document_id,
            "document_name": doc_name.get(r.document_id),
            "requirement_id": r.requirement_id,
            "section_id": r.section_id,
            "category": r.category,
            "priority": r.priority,
            "title": r.title,
            "description": r.description,
            "source_text": r.source_text,
            "source_page": r.source_page,
            "extraction_pass": r.extraction_pass,
            "reviewer_confidence": r.reviewer_confidence,
            "notes": r.notes,
            "compliance_status": r.compliance_status,
            "parsons_evidence": r.parsons_evidence,
            "verified": r.verified,
            "verified_by": r.verified_by,
            "verification_notes": r.verification_notes,
        })

    # Category priority for rendering: put the categories users care about first.
    CAT_ORDER = [
        "mandatory", "scored", "deadline", "certification", "form",
        "signature", "approval", "legal_reference", "legal", "limitation",
        "restriction", "exclusion", "consequence", "definition",
        "informational", "information", "unknown",
    ]
    cat_rank = {c: i for i, c in enumerate(CAT_ORDER)}

    def _cat_key(cat: str):
        return (cat_rank.get(cat, len(CAT_ORDER)), cat)

    out_sections = []
    for sid, b in sections.items():
        # Flatten categories dict into an ordered list with counts.
        cat_groups = []
        for cat in sorted(b["categories"].keys(), key=_cat_key):
            items = b["categories"][cat]
            cat_groups.append({
                "category": cat,
                "count": len(items),
                "requirements": items,
            })
        # Page range string (e.g. "3-17") for quick scan.
        page_range = None
        if b["pages"]:
            pages_sorted = sorted(p for p in b["pages"] if isinstance(p, int))
            if pages_sorted:
                page_range = (
                    f"{pages_sorted[0]}" if pages_sorted[0] == pages_sorted[-1]
                    else f"{pages_sorted[0]}-{pages_sorted[-1]}"
                )
        out_sections.append({
            "section_id": sid,
            "total": b["total"],
            "by_category": b["by_category"],
            "by_priority": b["by_priority"],
            "by_compliance": b["by_compliance"],
            "verified": b["verified"],
            "document_ids": sorted(b["documents"]),
            "document_names": [doc_name[d] for d in sorted(b["documents"]) if d in doc_name],
            "page_range": page_range,
            "categories": cat_groups,
        })

    # Natural-sort sections. Unsectioned goes last.
    out_sections.sort(key=lambda s: (s["section_id"] == UNSECTIONED, _section_sort_key(s["section_id"])))

    return {
        "proposal_id": proposal_id,
        "document_id": document_id,
        "filters": {
            "category": category,
            "priority": priority,
            "compliance_status": compliance_status,
            "search": search,
        },
        "total_sections": len(out_sections),
        "total_requirements": sum(s["total"] for s in out_sections),
        "sections": out_sections,
    }


class RequirementUpdate(BaseModel):
    compliance_status: Optional[str] = None
    parsons_evidence: Optional[str] = None
    verified: Optional[bool] = None
    verification_notes: Optional[str] = None

@router.put("/requirements/{requirement_id}")
def update_requirement(
    requirement_id: int,
    payload: RequirementUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    req = db.query(RfpRequirement).filter(RfpRequirement.id == requirement_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Requirement not found")
    if payload.compliance_status is not None:
        req.compliance_status = payload.compliance_status
    if payload.parsons_evidence is not None:
        req.parsons_evidence = payload.parsons_evidence
    if payload.verified is not None:
        req.verified = payload.verified
        req.verified_by = current_user.username if current_user else None
    if payload.verification_notes is not None:
        req.verification_notes = payload.verification_notes
    db.commit()
    return {"id": req.id, "status": "updated"}


@router.get("/requirements/{requirement_id}/citations")
def get_requirement_citations(
    requirement_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Extract internal document cross-references from the requirement's text
    ("Section 3.2", "Attachment 4", "Appendix B", etc.) and resolve each one
    against the rest of the proposal — matching sibling requirements and/or
    document chunks that contain the referenced heading.

    Returns a list of citations, each with the matches the UI can link to.
    Fast (regex + existing indexes only — no LLM calls).
    """
    from ..services.citation_resolver import resolve_citations_as_dict
    req = db.query(RfpRequirement).filter(RfpRequirement.id == requirement_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Requirement not found")
    return {
        "requirement_id": req.id,
        "citations": resolve_citations_as_dict(db, req),
    }


@router.get("/requirements/{requirement_id}/source")
def get_requirement_source(
    requirement_id: int,
    context_chunks: int = Query(1, ge=0, le=5),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Resolve a requirement back to its verbatim source paragraph.

    The extractor stores a short ``source_text`` snippet (intended verbatim,
    sometimes layout-compressed). Reconstructing the full paragraph requires
    a join through ``requirement_source_links`` (built by
    ``scripts/build_requirement_source_links.py``) to ``document_chunks``.

    For requirements stored under the synthetic "consolidation_run" document,
    the linker walks ``consolidated_requirements.source_document_id`` back
    to the real source document.

    Set ``context_chunks`` > 0 to also return N chunks before and after the
    matched chunk (same document, ordered by chunk_index) — useful when the
    requirement spans a chunk boundary.
    """
    req = db.query(RfpRequirement).filter(RfpRequirement.id == requirement_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Requirement not found")

    from sqlalchemy import text
    link_row = db.execute(
        text(
            """SELECT chunk_id, chunk_doc_id, match_quality, redirected_from_doc_id
               FROM requirement_source_links WHERE requirement_id = :rid"""
        ),
        {"rid": requirement_id},
    ).first()

    response: Dict[str, Any] = {
        "requirement_id": req.id,
        "stored_doc_id": req.document_id,
        "section_id": req.section_id,
        "title": req.title,
        "description": req.description,
        "llm_source_text": req.source_text,
        "source_page": req.source_page,
        "match_quality": link_row.match_quality if link_row else "unlinked",
        "redirected_from_doc_id": link_row.redirected_from_doc_id if link_row else None,
        "linked_chunk": None,
        "context_before": [],
        "context_after": [],
    }

    if not link_row or not link_row.chunk_id:
        return response

    main = db.query(DocumentChunk).filter(DocumentChunk.id == link_row.chunk_id).first()
    if not main:
        return response

    doc = db.query(IngestedDocument).filter(IngestedDocument.id == main.document_id).first()
    response["linked_chunk"] = {
        "id": main.id,
        "document_id": main.document_id,
        "document_filename": doc.original_filename if doc else None,
        "chunk_index": main.chunk_index,
        "page_number": main.page_number,
        "content": main.content,
    }

    if context_chunks > 0:
        before = (
            db.query(DocumentChunk)
            .filter(DocumentChunk.document_id == main.document_id)
            .filter(DocumentChunk.chunk_index < main.chunk_index)
            .order_by(DocumentChunk.chunk_index.desc())
            .limit(context_chunks)
            .all()
        )
        after = (
            db.query(DocumentChunk)
            .filter(DocumentChunk.document_id == main.document_id)
            .filter(DocumentChunk.chunk_index > main.chunk_index)
            .order_by(DocumentChunk.chunk_index.asc())
            .limit(context_chunks)
            .all()
        )
        response["context_before"] = [
            {"id": c.id, "chunk_index": c.chunk_index, "page_number": c.page_number, "content": c.content}
            for c in reversed(before)
        ]
        response["context_after"] = [
            {"id": c.id, "chunk_index": c.chunk_index, "page_number": c.page_number, "content": c.content}
            for c in after
        ]

    return response


# ── Facts ────────────────────────────────────────────────────────────

@router.get("/facts")
def list_facts(
    owner_type: Optional[str] = Query(None),
    fact_type: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = db.query(ExtractedFact).order_by(ExtractedFact.created_at.desc())
    if owner_type:
        q = q.filter(ExtractedFact.owner_type == owner_type)
    if fact_type:
        q = q.filter(ExtractedFact.fact_type == fact_type)
    facts = q.limit(limit).all()
    return {
        "total": len(facts),
        "facts": [
            {
                "id": f.id,
                "owner_type": f.owner_type,
                "fact_type": f.fact_type,
                "fact_key": f.fact_key,
                "fact_value": f.fact_value,
                "confidence": f.confidence,
                "verified": f.verified,
            }
            for f in facts
        ],
    }


# ── Agent Conversations ──────────────────────────────────────────────

@router.get("/conversations")
def list_conversations(
    conversation_type: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = db.query(AgentConversation).order_by(AgentConversation.created_at.desc())
    if conversation_type:
        q = q.filter(AgentConversation.conversation_type == conversation_type)
    convs = q.limit(limit).all()
    return {
        "conversations": [
            {
                "id": c.id,
                "type": c.conversation_type,
                "status": c.status,
                "current_loop": c.current_loop,
                "max_loops": c.max_loops,
                "summary": c.summary,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in convs
        ],
    }


@router.get("/conversations/{conversation_id}")
def get_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    conv = db.query(AgentConversation).filter(AgentConversation.id == conversation_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = get_conversation_history(db, conversation_id)
    return {
        "id": conv.id,
        "type": conv.conversation_type,
        "status": conv.status,
        "current_loop": conv.current_loop,
        "summary": conv.summary,
        "messages": messages,
    }


# ── Persona Tasks ────────────────────────────────────────────────────

class PersonaTaskRequest(BaseModel):
    persona_type: str
    task_prompt: str
    context: str = ""
    conversation_type: str = "general"

@router.post("/agent/task", dependencies=[Depends(enforce_rate_limit("llm"))])
def run_agent_task(
    payload: PersonaTaskRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = run_persona_task(
        db, payload.persona_type, payload.task_prompt,
        payload.context, payload.conversation_type,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


class ConsensusRequest(BaseModel):
    persona_types: List[str]
    task_prompt: str
    context: str = ""
    max_loops: int = 3

@router.post("/agent/consensus")
def run_agent_consensus(
    payload: ConsensusRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = run_consensus_loop(
        db, payload.persona_types, payload.task_prompt,
        payload.context, max_loops=payload.max_loops,
    )
    return result


# ── Seed personas ────────────────────────────────────────────────────

@router.post("/seed-personas")
def seed_personas(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create or update all 10 system personas."""
    results = seed_system_personas(db)
    return {"personas": results}
