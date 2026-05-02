"""Read API for the in-memory background job registry.

Endpoints:
  GET /api/jobs/{job_id} - poll a single job
  GET /api/jobs           - list recent jobs (filterable by kind / doc_id)

Auth: every endpoint requires a logged-in user (same as the rest of the
internal API). Job result payloads are not user-scoped today; this is
acceptable for the current single-tenant use. When we add multi-tenant
RFP isolation we'll filter by user_id here.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional

from ..auth import get_current_user
from ..models import User
from ..services import jobs as job_registry

router = APIRouter()


@router.get("/{job_id}")
def get_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
):
    job = job_registry.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found or expired")
    return job.to_dict()


@router.get("")
def list_jobs(
    kind: Optional[str] = Query(None, description="Filter by job kind, e.g. 'extract_requirements'"),
    doc_id: Optional[int] = Query(None, description="Filter by associated document id"),
    limit: int = Query(50, ge=1, le=500),
    current_user: User = Depends(get_current_user),
):
    items = job_registry.list_jobs(kind=kind, doc_id=doc_id, limit=limit)
    return {"items": [j.to_dict() for j in items], "count": len(items)}
