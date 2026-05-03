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
    limit: Optional[int] = Query(200, ge=1, le=10000, description="Max rows to return. Default 200 — pass higher explicitly if you really need a full dump."),
    offset: int = Query(0, ge=0, description="Rows to skip"),
    include_children: bool = Query(False, description="If true, include requirements rolled up under a parent (sub-parts)."),
    include_superseded: bool = Query(False, description="If true, include requirements marked as duplicates of another canonical row."),
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
    if not include_children:
        q = q.filter(or_(
            RfpRequirement.rollup_role.is_(None),
            RfpRequirement.rollup_role == "parent",
        ))
    if not include_superseded:
        q = q.filter(RfpRequirement.superseded_by_requirement_id.is_(None))

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
    include_children: bool = Query(False, description="If true, include requirements rolled up under a parent (sub-parts). Default false hides them — they're shown inline on the parent's detail view."),
    include_superseded: bool = Query(False, description="If true, include requirements marked as duplicates of another canonical row."),
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
    if not include_children:
        q = q.filter(or_(
            RfpRequirement.rollup_role.is_(None),
            RfpRequirement.rollup_role == "parent",
        ))
    if not include_superseded:
        q = q.filter(RfpRequirement.superseded_by_requirement_id.is_(None))
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


@router.get("/requirements/{requirement_id}/bundle")
def get_requirement_bundle(
    requirement_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Return the full context bundle for a single requirement.

    The bundle is precomputed by ``scripts/context/run_all.py`` and stored in
    ``requirement_context_bundle.bundle_json``. It contains everything the
    Requirement Detail UI needs to render in one round-trip:

      - The requirement itself (title, description, source_text, classifiers).
      - Its theme and breadcrumb section path.
      - The verbatim source paragraph plus N chunks before/after.
      - All resolved cross-references (Section X.Y, Appendix C, etc.) with
        target document/section/excerpt populated.
      - Reverse references (other requirements that point at this one).
      - Glossary terms appearing in the requirement text.
      - Tables nearby in the same section (with header + preview rows).

    If the pipeline has not been run, returns 503 with a hint.
    """
    from sqlalchemy import text
    row = db.execute(
        text("SELECT bundle_json FROM requirement_context_bundle WHERE requirement_id=:rid"),
        {"rid": requirement_id},
    ).first()
    if not row:
        # Bundle missing — either pipeline not run or this is a superseded row.
        existing = db.query(RfpRequirement).filter(RfpRequirement.id == requirement_id).first()
        if not existing:
            raise HTTPException(status_code=404, detail="Requirement not found")
        raise HTTPException(
            status_code=503,
            detail=(
                "Context bundle not built for this requirement. Run "
                "`python -m scripts.context.run_all` to (re)build the pipeline."
            ),
        )
    bundle = json.loads(row[0])

    # Live overlay: the bundle is precomputed but the AI-drafted Parsons
    # response evolves as the batch runs. Overlay the latest values from
    # rfp_requirements so the UI reflects current state without rebuild.
    live = db.execute(
        text(
            """SELECT parsons_response, compliance_disposition,
                       parsons_response_status, parsons_response_suggestions,
                       parsons_response_cited_evidence,
                       parsons_response_review_feedback,
                       parsons_response_updated_at
               FROM rfp_requirements WHERE id=:rid"""
        ),
        {"rid": requirement_id},
    ).first()
    if live:
        try:
            sug = json.loads(live[3]) if live[3] else []
        except (json.JSONDecodeError, TypeError):
            sug = []
        try:
            cite = json.loads(live[4]) if live[4] else []
        except (json.JSONDecodeError, TypeError):
            cite = []
        bundle["requirement"]["parsons_response"] = live[0]
        bundle["requirement"]["parsons_response_disposition"] = live[1]
        bundle["requirement"]["parsons_response_status"] = live[2]
        bundle["requirement"]["parsons_response_suggestions"] = sug
        bundle["requirement"]["parsons_response_cited_evidence"] = cite
        bundle["requirement"]["parsons_response_review_feedback"] = live[5]
        bundle["requirement"]["parsons_response_updated_at"] = (
            live[6].isoformat() if hasattr(live[6], "isoformat") else live[6]
        )
    return bundle


@router.get("/requirements/tree")
def get_requirement_tree(
    proposal_id: Optional[int] = Query(None),
    summary: bool = Query(True, description="If true (default), return only the theme/doc/section skeleton with counts; load requirements per section via /sections/{key}/requirements."),
    category: Optional[str] = Query(None, description="Filter to one top-level category: Technical | Operational | Commercial | Compliance | Other"),
    document_ids: Optional[str] = Query(None, description="Comma-separated document_ids to scope the tree to."),
    requirement_kind: str = Query("obligation", description="Row kind to include. Default 'obligation' hides checklist items and XML-schema specs. Pass 'all' to include everything, or 'checklist_item' / 'data_element_spec' to drill into one kind."),
    response_effort: str = Query("writeup", description="Effort level. Default 'writeup' (substantive proposal content). Pass 'attestation' for forms/yes-comply rows, 'info' for read-only context, or 'all' for everything."),
    procurement_scope: str = Query("current_2026", description="Procurement scope. Default 'current_2026' restricts to docs incorporated by the live RFP. Pass 'archive_2021_compare' for the prior cycle's docs, or 'all' for both."),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Return the browsing tree: theme → document → section [→ requirement].

    The default ``summary=true`` returns just theme/document/section nodes
    with requirement counts — small, fast, ~tens of KB. The UI then
    lazy-loads requirements per section via
    ``GET /requirements/by-bundle?theme_id=&doc_id=&section_id=&offset=&limit=``
    when the user expands the section.

    Pass ``summary=false`` to get the legacy fully-hydrated tree (slow,
    multi-MB on large RFPs — only use for export tooling).
    """
    from sqlalchemy import text
    params: Dict[str, Any] = {}
    clauses = ["(r.rollup_role IS NULL OR r.rollup_role = 'parent')"]
    if requirement_kind and requirement_kind != "all":
        clauses.append("(r.requirement_kind = :rkind OR r.requirement_kind IS NULL)" if requirement_kind == "obligation" else "r.requirement_kind = :rkind")
        params["rkind"] = requirement_kind
    if response_effort and response_effort != "all":
        clauses.append("(r.response_effort = :reff OR r.response_effort IS NULL)" if response_effort == "writeup" else "r.response_effort = :reff")
        params["reff"] = response_effort
    if procurement_scope and procurement_scope != "all":
        clauses.append("b.source_doc_id IN (SELECT id FROM ingested_documents WHERE procurement_scope = :pscope)")
        params["pscope"] = procurement_scope
    if proposal_id is not None:
        clauses.append("(r.proposal_id = :pid OR r.proposal_id IS NULL)")
        params["pid"] = proposal_id
    if category:
        clauses.append("rt.category = :cat")
        params["cat"] = category
    doc_id_list: Optional[List[int]] = None
    if document_ids:
        try:
            doc_id_list = [int(x) for x in document_ids.split(",") if x.strip()]
        except ValueError:
            raise HTTPException(status_code=400, detail="document_ids must be comma-separated integers")
        if doc_id_list:
            placeholders = ",".join(f":did_{i}" for i in range(len(doc_id_list)))
            clauses.append(f"b.source_doc_id IN ({placeholders})")
            for i, did in enumerate(doc_id_list):
                params[f"did_{i}"] = did
    where_full = "WHERE " + " AND ".join(clauses)

    if summary:
        agg = db.execute(text(f"""
            SELECT b.theme_id, b.theme_label, rt.category AS theme_category,
                   b.source_doc_id, b.source_doc_name,
                   r.section_id, b.breadcrumb,
                   COUNT(*) AS n_reqs,
                   SUM(CASE WHEN b.n_resolved_refs > 0 THEN 1 ELSE 0 END) AS n_with_refs,
                   SUM(CASE WHEN b.n_glossary > 0 THEN 1 ELSE 0 END) AS n_with_gloss,
                   SUM(CASE WHEN b.n_tables > 0 THEN 1 ELSE 0 END) AS n_with_tables
            FROM requirement_context_bundle b
            JOIN rfp_requirements r ON r.id = b.requirement_id
            LEFT JOIN requirement_themes rt ON rt.id = b.theme_id
            {where_full}
            GROUP BY b.theme_id, b.theme_label, rt.category,
                     b.source_doc_id, b.source_doc_name,
                     r.section_id, b.breadcrumb
            ORDER BY b.theme_id, b.source_doc_id, r.section_id
        """), params).fetchall()

        themes: Dict[Any, Dict] = {}
        total = 0
        for row in agg:
            tid = row.theme_id or 0
            theme = themes.setdefault(tid, {
                "theme_id": tid,
                "theme_label": row.theme_label or "Uncategorized",
                "category": row.theme_category or "Other",
                "documents": {},
                "n_requirements": 0,
            })
            theme["n_requirements"] += int(row.n_reqs)
            total += int(row.n_reqs)
            doc = theme["documents"].setdefault(row.source_doc_id, {
                "document_id": row.source_doc_id,
                "document_name": row.source_doc_name,
                "sections": [],
                "n_requirements": 0,
            })
            doc["n_requirements"] += int(row.n_reqs)
            doc["sections"].append({
                "section_id": row.section_id or "(no section)",
                "breadcrumb": row.breadcrumb,
                "n_requirements": int(row.n_reqs),
                "n_with_refs": int(row.n_with_refs or 0),
                "n_with_gloss": int(row.n_with_gloss or 0),
                "n_with_tables": int(row.n_with_tables or 0),
            })

        out = []
        for tid in sorted(themes.keys()):
            theme = themes[tid]
            doc_list = sorted(theme["documents"].values(),
                              key=lambda d: (d["document_id"] or 0))
            theme["documents"] = doc_list
            out.append(theme)
        return {"themes": out, "total_requirements": total, "summary": True}

    rows = db.execute(text(f"""
        SELECT r.id AS req_id, r.title, r.section_id, r.priority, r.category,
               r.compliance_status, r.requirement_class, r.rollup_role,
               b.theme_id, b.theme_label, b.source_doc_id, b.source_doc_name,
               b.breadcrumb,
               b.n_refs, b.n_resolved_refs, b.n_glossary, b.n_reverse_refs, b.n_tables,
               (SELECT COUNT(*) FROM requirement_groups rg
                  WHERE rg.parent_requirement_id = r.id) AS n_children
        FROM requirement_context_bundle b
        JOIN rfp_requirements r ON r.id = b.requirement_id
        {where_full}
        ORDER BY b.theme_id, b.source_doc_id, r.section_id, r.id
    """), params).fetchall()

    themes: Dict[Any, Dict] = {}
    for r in rows:
        tid = r.theme_id or 0
        theme = themes.setdefault(tid, {
            "theme_id": tid, "theme_label": r.theme_label or "Uncategorized",
            "documents": {},
            "n_requirements": 0,
        })
        theme["n_requirements"] += 1
        doc = theme["documents"].setdefault(r.source_doc_id, {
            "document_id": r.source_doc_id,
            "document_name": r.source_doc_name,
            "sections": {},
            "n_requirements": 0,
        })
        doc["n_requirements"] += 1
        sec_key = r.section_id or "(no section)"
        sec = doc["sections"].setdefault(sec_key, {
            "section_id": sec_key,
            "breadcrumb": r.breadcrumb,
            "requirements": [],
        })
        sec["requirements"].append({
            "requirement_id": r.req_id,
            "title": r.title,
            "priority": r.priority,
            "category": r.category,
            "compliance_status": r.compliance_status,
            "requirement_class": r.requirement_class,
            "rollup_role": r.rollup_role,
            "n_children": int(r.n_children or 0),
            "n_refs": r.n_refs, "n_resolved_refs": r.n_resolved_refs,
            "n_glossary": r.n_glossary, "n_reverse_refs": r.n_reverse_refs,
            "n_tables": r.n_tables,
        })

    # Convert dicts to lists ordered by their natural key
    out = []
    for tid in sorted(themes.keys()):
        theme = themes[tid]
        doc_list = sorted(theme["documents"].values(),
                          key=lambda d: (d["document_id"] or 0))
        for d in doc_list:
            d["sections"] = sorted(d["sections"].values(),
                                    key=lambda s: s["section_id"])
        theme["documents"] = doc_list
        out.append(theme)
    return {"themes": out, "total_requirements": len(rows)}


@router.get("/requirements/filters")
def get_requirement_filters(
    proposal_id: Optional[int] = Query(None),
    procurement_scope: str = Query("current_2026", description="Scope to count within. 'all' for everything."),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Lightweight filter options for the requirement browser:
    available top-level categories with their requirement counts, the
    list of source documents the user can scope to (within the chosen
    procurement scope), and per-scope counts.
    """
    from sqlalchemy import text
    params: Dict[str, Any] = {}
    where = "(r.rollup_role IS NULL OR r.rollup_role = 'parent')"
    if proposal_id is not None:
        where += " AND (r.proposal_id = :pid OR r.proposal_id IS NULL)"
        params["pid"] = proposal_id
    if procurement_scope and procurement_scope != "all":
        where += " AND b.source_doc_id IN (SELECT id FROM ingested_documents WHERE procurement_scope = :pscope)"
        params["pscope"] = procurement_scope

    # Count by row kind
    kinds = db.execute(text(f"""
        SELECT COALESCE(r.requirement_kind, 'obligation') AS kind, COUNT(*) AS n
        FROM requirement_context_bundle b
        JOIN rfp_requirements r ON r.id = b.requirement_id
        WHERE {where}
        GROUP BY COALESCE(r.requirement_kind, 'obligation')
        ORDER BY n DESC
    """), params).fetchall()

    # Count by response effort (only meaningful for the obligation bucket)
    effort_where = where + " AND (r.requirement_kind = 'obligation' OR r.requirement_kind IS NULL)"
    efforts = db.execute(text(f"""
        SELECT COALESCE(r.response_effort, 'writeup') AS effort, COUNT(*) AS n
        FROM requirement_context_bundle b
        JOIN rfp_requirements r ON r.id = b.requirement_id
        WHERE {effort_where}
        GROUP BY COALESCE(r.response_effort, 'writeup')
        ORDER BY n DESC
    """), params).fetchall()

    # Categories — only count obligations so the headline matches the default view.
    cat_where = where + " AND (r.requirement_kind = 'obligation' OR r.requirement_kind IS NULL)"
    cats = db.execute(text(f"""
        SELECT COALESCE(rt.category, 'Other') AS category, COUNT(*) AS n
        FROM requirement_context_bundle b
        JOIN rfp_requirements r ON r.id = b.requirement_id
        LEFT JOIN requirement_themes rt ON rt.id = b.theme_id
        WHERE {cat_where}
        GROUP BY COALESCE(rt.category, 'Other')
        ORDER BY n DESC
    """), params).fetchall()

    docs = db.execute(text(f"""
        SELECT b.source_doc_id AS document_id,
               b.source_doc_name AS document_name,
               COUNT(*) AS n_requirements
        FROM requirement_context_bundle b
        JOIN rfp_requirements r ON r.id = b.requirement_id
        WHERE {where} AND b.source_doc_id IS NOT NULL
        GROUP BY b.source_doc_id, b.source_doc_name
        ORDER BY n_requirements DESC
    """), params).fetchall()

    # Scope counts (always cross all scopes — independent of the current filter)
    base_scope_where = "(r.rollup_role IS NULL OR r.rollup_role = 'parent')"
    base_scope_params: Dict[str, Any] = {}
    if proposal_id is not None:
        base_scope_where += " AND (r.proposal_id = :pid OR r.proposal_id IS NULL)"
        base_scope_params["pid"] = proposal_id
    scopes = db.execute(text(f"""
        SELECT COALESCE(ind.procurement_scope, 'unscoped') AS scope, COUNT(*) AS n
        FROM requirement_context_bundle b
        JOIN rfp_requirements r ON r.id = b.requirement_id
        LEFT JOIN ingested_documents ind ON ind.id = b.source_doc_id
        WHERE {base_scope_where}
          AND (r.requirement_kind = 'obligation' OR r.requirement_kind IS NULL)
          AND (r.response_effort = 'writeup' OR r.response_effort IS NULL)
        GROUP BY COALESCE(ind.procurement_scope, 'unscoped')
        ORDER BY n DESC
    """), base_scope_params).fetchall()

    return {
        "scopes": [
            {"scope": r.scope, "n_writeups": int(r.n)} for r in scopes
        ],
        "kinds": [
            {"kind": r.kind, "n_requirements": int(r.n)} for r in kinds
        ],
        "efforts": [
            {"effort": r.effort, "n_requirements": int(r.n)} for r in efforts
        ],
        "categories": [
            {"category": r.category, "n_requirements": int(r.n)}
            for r in cats
        ],
        "documents": [
            {
                "document_id": r.document_id,
                "document_name": r.document_name,
                "n_requirements": int(r.n_requirements),
            }
            for r in docs
        ],
    }


@router.get("/requirements/{requirement_id}/neighbors")
def get_requirement_neighbors(
    requirement_id: int,
    proposal_id: Optional[int] = Query(None),
    requirement_kind: str = Query("obligation"),
    response_effort: str = Query("writeup"),
    procurement_scope: str = Query("current_2026"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return the previous/next requirement IDs for sequential review.

    Same default scoping as the browser tree so navigation stays inside
    the same working set.
    """
    from sqlalchemy import text
    clauses = [
        "(r.rollup_role IS NULL OR r.rollup_role = 'parent')",
        "r.superseded_by_requirement_id IS NULL",
    ]
    params: Dict[str, Any] = {"rid": requirement_id}
    if requirement_kind and requirement_kind != "all":
        clauses.append("(r.requirement_kind = :rkind OR r.requirement_kind IS NULL)" if requirement_kind == "obligation" else "r.requirement_kind = :rkind")
        params["rkind"] = requirement_kind
    if response_effort and response_effort != "all":
        clauses.append("(r.response_effort = :reff OR r.response_effort IS NULL)" if response_effort == "writeup" else "r.response_effort = :reff")
        params["reff"] = response_effort
    if procurement_scope and procurement_scope != "all":
        clauses.append("r.document_id IN (SELECT id FROM ingested_documents WHERE procurement_scope = :pscope)")
        params["pscope"] = procurement_scope
    if proposal_id is not None:
        clauses.append("(r.proposal_id = :pid OR r.proposal_id IS NULL)")
        params["pid"] = proposal_id
    where = " AND ".join(clauses)

    prev = db.execute(text(f"""
        SELECT r.id, r.title FROM rfp_requirements r
        WHERE {where} AND r.id < :rid
        ORDER BY r.id DESC LIMIT 1
    """), params).first()
    nxt = db.execute(text(f"""
        SELECT r.id, r.title FROM rfp_requirements r
        WHERE {where} AND r.id > :rid
        ORDER BY r.id ASC LIMIT 1
    """), params).first()
    pos = db.execute(text(f"""
        SELECT COUNT(*) FROM rfp_requirements r
        WHERE {where} AND r.id <= :rid
    """), params).scalar()
    total = db.execute(text(f"""
        SELECT COUNT(*) FROM rfp_requirements r
        WHERE {where}
    """), params).scalar()
    return {
        "current": {"requirement_id": requirement_id, "position": int(pos or 0), "total": int(total or 0)},
        "prev": {"requirement_id": prev[0], "title": prev[1]} if prev else None,
        "next": {"requirement_id": nxt[0], "title": nxt[1]} if nxt else None,
    }


@router.get("/requirements/needs-attention")
def list_drafts_needing_attention(
    proposal_id: Optional[int] = Query(None),
    procurement_scope: str = Query("current_2026"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Drafts that warrant human review first.

    Flags any of:
      - disposition is Comply-with-exception, Take-exception, or
        Needs-Clarification (Parsons doesn't fully comply or the LLM
        wasn't sure)
      - no evidence chunks were cited (LLM had to draft without
        Parsons grounding)
      - response_status is rejected (a previous review kicked it back)

    Each row carries the SAME shape as the in-section list so the UI
    can render it inline.
    """
    from sqlalchemy import text
    clauses = [
        "(r.rollup_role IS NULL OR r.rollup_role = 'parent')",
        "r.superseded_by_requirement_id IS NULL",
        "(r.requirement_kind = 'obligation' OR r.requirement_kind IS NULL)",
        "(r.response_effort = 'writeup' OR r.response_effort IS NULL)",
    ]
    params: Dict[str, Any] = {"limit": limit, "offset": offset}
    if procurement_scope and procurement_scope != "all":
        clauses.append("r.document_id IN (SELECT id FROM ingested_documents WHERE procurement_scope = :pscope)")
        params["pscope"] = procurement_scope
    if proposal_id is not None:
        clauses.append("(r.proposal_id = :pid OR r.proposal_id IS NULL)")
        params["pid"] = proposal_id

    needs = "(" + " OR ".join([
        "r.compliance_disposition IN ('Comply-with-exception','Take-exception','Needs-Clarification')",
        "r.parsons_response_cited_evidence IS NULL",
        "r.parsons_response_status = 'rejected'",
    ]) + ")"
    clauses.append("r.parsons_response_status IS NOT NULL")  # only consider drafted rows
    clauses.append(needs)

    where = "WHERE " + " AND ".join(clauses)
    total = db.execute(text(f"""
        SELECT COUNT(*) FROM rfp_requirements r
        {where}
    """), params).scalar()

    rows = db.execute(text(f"""
        SELECT r.id, r.title, r.section_id, r.priority, r.category,
               r.compliance_disposition AS disposition,
               r.parsons_response_status AS status,
               (CASE WHEN r.parsons_response_cited_evidence IS NULL THEN 1 ELSE 0 END) AS no_evidence,
               (CASE WHEN r.parsons_response IS NULL OR r.parsons_response = '' THEN 1 ELSE 0 END) AS empty_response,
               substr(r.parsons_response, 1, 200) AS response_preview
        FROM rfp_requirements r
        {where}
        ORDER BY r.id
        LIMIT :limit OFFSET :offset
    """), params).fetchall()

    return {
        "total": int(total or 0),
        "offset": offset, "limit": limit,
        "requirements": [
            {
                "requirement_id": r.id, "title": r.title, "section_id": r.section_id,
                "priority": r.priority, "category": r.category,
                "compliance_disposition": r.disposition,
                "parsons_response_status": r.status,
                "no_evidence": bool(r.no_evidence),
                "empty_response": bool(r.empty_response),
                "response_preview": r.response_preview,
            } for r in rows
        ],
    }


@router.get("/requirements/in-section")
def list_requirements_in_section(
    proposal_id: Optional[int] = Query(None),
    theme_id: Optional[int] = Query(None),
    document_id: Optional[int] = Query(None),
    section_id: Optional[str] = Query(None),
    search: Optional[str] = Query(None, description="Substring filter on title"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    requirement_kind: str = Query("obligation"),
    response_effort: str = Query("writeup"),
    procurement_scope: str = Query("current_2026"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Paginated requirements list for a single section in the browse tree.

    Filters mirror the tree node identity (theme_id + document_id + section_id).
    Children rolled up under a parent are excluded by default; the detail view
    shows them inline on the parent.
    """
    from sqlalchemy import text
    clauses = ["(r.rollup_role IS NULL OR r.rollup_role = 'parent')"]
    params: Dict[str, Any] = {"offset": offset, "limit": limit}
    if requirement_kind and requirement_kind != "all":
        clauses.append("(r.requirement_kind = :rkind OR r.requirement_kind IS NULL)" if requirement_kind == "obligation" else "r.requirement_kind = :rkind")
        params["rkind"] = requirement_kind
    if response_effort and response_effort != "all":
        clauses.append("(r.response_effort = :reff OR r.response_effort IS NULL)" if response_effort == "writeup" else "r.response_effort = :reff")
        params["reff"] = response_effort
    if procurement_scope and procurement_scope != "all":
        clauses.append("b.source_doc_id IN (SELECT id FROM ingested_documents WHERE procurement_scope = :pscope)")
        params["pscope"] = procurement_scope
    if proposal_id is not None:
        clauses.append("(r.proposal_id = :pid OR r.proposal_id IS NULL)")
        params["pid"] = proposal_id
    if theme_id is not None:
        clauses.append("b.theme_id = :tid")
        params["tid"] = theme_id
    if document_id is not None:
        clauses.append("b.source_doc_id = :did")
        params["did"] = document_id
    if section_id is not None:
        if section_id == "(no section)":
            clauses.append("(r.section_id IS NULL OR r.section_id = '')")
        else:
            clauses.append("r.section_id = :sec")
            params["sec"] = section_id
    if search:
        clauses.append("r.title LIKE :search")
        params["search"] = f"%{search}%"
    where_full = "WHERE " + " AND ".join(clauses)

    total = db.execute(text(f"""
        SELECT COUNT(*) FROM requirement_context_bundle b
        JOIN rfp_requirements r ON r.id = b.requirement_id
        {where_full}
    """), params).scalar()

    rows = db.execute(text(f"""
        SELECT r.id AS req_id, r.title, r.section_id, r.priority, r.category,
               r.compliance_status, r.requirement_class, r.rollup_role,
               b.n_refs, b.n_resolved_refs, b.n_glossary, b.n_reverse_refs, b.n_tables,
               (SELECT COUNT(*) FROM requirement_groups rg
                  WHERE rg.parent_requirement_id = r.id) AS n_children
        FROM requirement_context_bundle b
        JOIN rfp_requirements r ON r.id = b.requirement_id
        {where_full}
        ORDER BY r.id
        LIMIT :limit OFFSET :offset
    """), params).fetchall()

    return {
        "total": int(total or 0),
        "offset": offset, "limit": limit,
        "requirements": [
            {
                "requirement_id": r.req_id,
                "title": r.title,
                "section_id": r.section_id,
                "priority": r.priority,
                "category": r.category,
                "compliance_status": r.compliance_status,
                "requirement_class": r.requirement_class,
                "rollup_role": r.rollup_role,
                "n_children": int(r.n_children or 0),
                "n_refs": r.n_refs, "n_resolved_refs": r.n_resolved_refs,
                "n_glossary": r.n_glossary, "n_reverse_refs": r.n_reverse_refs,
                "n_tables": r.n_tables,
            } for r in rows
        ],
    }


@router.get("/solution-catalog")
def list_solution_catalog(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return all Solution Catalog entries (one per requirement theme)."""
    from sqlalchemy import text
    rows = db.execute(text("""
        SELECT id, theme_id, theme_label, category, title,
               capability_statement, named_past_performance,
               quantified_outcomes, differentiators, gap_notes,
               source_chunk_ids, n_requirements_addressed,
               state_neutral, generated_at
        FROM solution_catalog_entries
        ORDER BY category, theme_id
    """)).fetchall()
    out = []
    for r in rows:
        out.append({
            "id": r.id,
            "theme_id": r.theme_id,
            "theme_label": r.theme_label,
            "category": r.category,
            "title": r.title,
            "capability_statement": r.capability_statement,
            "named_past_performance": json.loads(r.named_past_performance or "[]"),
            "quantified_outcomes": json.loads(r.quantified_outcomes or "[]"),
            "differentiators": r.differentiators,
            "gap_notes": r.gap_notes,
            "source_chunk_ids": json.loads(r.source_chunk_ids or "[]"),
            "n_requirements_addressed": r.n_requirements_addressed,
            "state_neutral": bool(r.state_neutral),
            "generated_at": (r.generated_at.isoformat()
                              if hasattr(r.generated_at, "isoformat") else r.generated_at),
        })
    return {"entries": out, "total": len(out)}


@router.get("/solution-catalog/{entry_id}")
def get_solution_catalog_entry(
    entry_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from sqlalchemy import text
    r = db.execute(text("""
        SELECT id, theme_id, theme_label, category, title,
               capability_statement, named_past_performance,
               quantified_outcomes, differentiators, gap_notes,
               source_chunk_ids, n_requirements_addressed,
               state_neutral, generated_at
        FROM solution_catalog_entries WHERE id = :eid
    """), {"eid": entry_id}).first()
    if not r:
        raise HTTPException(404, "Catalog entry not found")
    return {
        "id": r.id, "theme_id": r.theme_id, "theme_label": r.theme_label,
        "category": r.category, "title": r.title,
        "capability_statement": r.capability_statement,
        "named_past_performance": json.loads(r.named_past_performance or "[]"),
        "quantified_outcomes": json.loads(r.quantified_outcomes or "[]"),
        "differentiators": r.differentiators,
        "gap_notes": r.gap_notes,
        "source_chunk_ids": json.loads(r.source_chunk_ids or "[]"),
        "n_requirements_addressed": r.n_requirements_addressed,
        "state_neutral": bool(r.state_neutral),
        "generated_at": (r.generated_at.isoformat()
                          if hasattr(r.generated_at, "isoformat") else r.generated_at),
    }


@router.get("/glossary")
def get_glossary(
    proposal_id: Optional[int] = Query(None),
    document_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List defined terms extracted from the corpus."""
    from sqlalchemy import text
    where = []
    params: Dict[str, Any] = {}
    if document_id is not None:
        where.append("gt.document_id = :did")
        params["did"] = document_id
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    rows = db.execute(text(f"""
        SELECT gt.id, gt.term, gt.definition, gt.scope,
               gt.document_id, ind.original_filename AS document_name,
               gt.section_code,
               (SELECT COUNT(*) FROM requirement_glossary_links rgl
                WHERE rgl.glossary_term_id = gt.id) AS n_uses
        FROM glossary_terms gt
        LEFT JOIN ingested_documents ind ON ind.id = gt.document_id
        {where_sql}
        ORDER BY gt.term COLLATE NOCASE
    """), params).fetchall()
    return {
        "terms": [
            {
                "id": r.id, "term": r.term, "definition": r.definition,
                "scope": r.scope, "document_id": r.document_id,
                "document_name": r.document_name, "section_code": r.section_code,
                "n_uses": r.n_uses,
            }
            for r in rows
        ]
    }


@router.get("/tables/{table_id}")
def get_table(
    table_id: int,
    highlight_rows: Optional[str] = Query(None, description="Comma-separated row indexes to highlight"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return a structured table with all rows. Use highlight_rows to mark in-scope rows for the UI."""
    from sqlalchemy import text
    head = db.execute(text("""
        SELECT et.id, et.document_id, et.section_code, et.title,
               et.header_row_json, et.n_rows, et.n_cols, ind.original_filename
        FROM extracted_tables et
        LEFT JOIN ingested_documents ind ON ind.id = et.document_id
        WHERE et.id = :tid
    """), {"tid": table_id}).first()
    if not head:
        raise HTTPException(status_code=404, detail="Table not found")
    rows = db.execute(text("""
        SELECT row_index, cells_json FROM extracted_table_rows
        WHERE table_id = :tid ORDER BY row_index
    """), {"tid": table_id}).fetchall()
    highlight_set = set()
    if highlight_rows:
        for s in highlight_rows.split(","):
            s = s.strip()
            if s.isdigit():
                highlight_set.add(int(s))
    return {
        "table_id": head.id,
        "document_id": head.document_id,
        "document_name": head.original_filename,
        "section_code": head.section_code,
        "title": head.title,
        "header": json.loads(head.header_row_json) if head.header_row_json else None,
        "n_rows": head.n_rows, "n_cols": head.n_cols,
        "rows": [
            {
                "row_index": r.row_index,
                "cells": json.loads(r.cells_json),
                "highlighted": r.row_index in highlight_set,
            }
            for r in rows
        ],
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
