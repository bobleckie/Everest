"""Read API for LLM usage / cost data (admin only).

Endpoints:
  GET /api/llm-usage           - paginated raw rows
  GET /api/llm-usage/summary   - aggregated totals
"""
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import get_current_admin_user
from ..database import get_db
from ..models import LlmUsage, User

router = APIRouter()


@router.get("")
def list_llm_usage(
    user_id: Optional[int] = Query(None),
    model: Optional[str] = Query(None),
    provider: Optional[str] = Query(None),
    endpoint: Optional[str] = Query(None),
    since: Optional[datetime] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    _admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    """Paginated raw usage rows, newest first."""
    q = db.query(LlmUsage)
    if user_id is not None:
        q = q.filter(LlmUsage.user_id == user_id)
    if model:
        q = q.filter(LlmUsage.model == model)
    if provider:
        q = q.filter(LlmUsage.provider == provider)
    if endpoint:
        q = q.filter(LlmUsage.endpoint == endpoint)
    if since:
        q = q.filter(LlmUsage.ts >= since)
    total = q.count()
    rows = q.order_by(LlmUsage.ts.desc()).offset(offset).limit(limit).all()

    def _row(r: LlmUsage) -> dict:
        return {
            "id": r.id,
            "ts": r.ts.isoformat() if r.ts else None,
            "user_id": r.user_id,
            "username": r.username,
            "request_id": r.request_id,
            "endpoint": r.endpoint,
            "provider": r.provider,
            "model": r.model,
            "input_tokens": r.input_tokens,
            "output_tokens": r.output_tokens,
            "total_tokens": r.total_tokens,
            "cost_estimate_usd": r.cost_estimate_usd,
            "latency_ms": r.latency_ms,
            "status": r.status,
            "error": r.error,
        }

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [_row(r) for r in rows],
    }


@router.get("/summary")
def llm_usage_summary(
    days: int = Query(30, ge=1, le=365, description="Window: last N days"),
    _admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    """Aggregate token + cost totals for the trailing N days, broken down
    several ways. Cheap query — single table, indexed columns.
    """
    since = datetime.utcnow() - timedelta(days=days)
    base = db.query(LlmUsage).filter(LlmUsage.ts >= since)

    totals = base.with_entities(
        func.count(LlmUsage.id).label("calls"),
        func.coalesce(func.sum(LlmUsage.input_tokens), 0).label("input_tokens"),
        func.coalesce(func.sum(LlmUsage.output_tokens), 0).label("output_tokens"),
        func.coalesce(func.sum(LlmUsage.total_tokens), 0).label("total_tokens"),
        func.coalesce(func.sum(LlmUsage.cost_estimate_usd), 0.0).label("cost_usd"),
    ).one()

    by_user = (
        base.with_entities(
            LlmUsage.user_id,
            LlmUsage.username,
            func.count(LlmUsage.id).label("calls"),
            func.coalesce(func.sum(LlmUsage.total_tokens), 0).label("tokens"),
            func.coalesce(func.sum(LlmUsage.cost_estimate_usd), 0.0).label("cost_usd"),
        )
        .group_by(LlmUsage.user_id, LlmUsage.username)
        .order_by(func.sum(LlmUsage.cost_estimate_usd).desc().nullslast())
        .all()
    )

    by_model = (
        base.with_entities(
            LlmUsage.model,
            LlmUsage.provider,
            func.count(LlmUsage.id).label("calls"),
            func.coalesce(func.sum(LlmUsage.total_tokens), 0).label("tokens"),
            func.coalesce(func.sum(LlmUsage.cost_estimate_usd), 0.0).label("cost_usd"),
        )
        .group_by(LlmUsage.model, LlmUsage.provider)
        .order_by(func.sum(LlmUsage.cost_estimate_usd).desc().nullslast())
        .all()
    )

    by_endpoint = (
        base.with_entities(
            LlmUsage.endpoint,
            func.count(LlmUsage.id).label("calls"),
            func.coalesce(func.sum(LlmUsage.total_tokens), 0).label("tokens"),
            func.coalesce(func.sum(LlmUsage.cost_estimate_usd), 0.0).label("cost_usd"),
        )
        .group_by(LlmUsage.endpoint)
        .order_by(func.sum(LlmUsage.cost_estimate_usd).desc().nullslast())
        .all()
    )

    return {
        "window_days": days,
        "since": since.isoformat() + "Z",
        "totals": {
            "calls": int(totals.calls or 0),
            "input_tokens": int(totals.input_tokens or 0),
            "output_tokens": int(totals.output_tokens or 0),
            "total_tokens": int(totals.total_tokens or 0),
            "cost_usd": round(float(totals.cost_usd or 0.0), 4),
        },
        "by_user": [
            {
                "user_id": r.user_id, "username": r.username,
                "calls": int(r.calls or 0),
                "tokens": int(r.tokens or 0),
                "cost_usd": round(float(r.cost_usd or 0.0), 4),
            }
            for r in by_user
        ],
        "by_model": [
            {
                "provider": r.provider, "model": r.model,
                "calls": int(r.calls or 0),
                "tokens": int(r.tokens or 0),
                "cost_usd": round(float(r.cost_usd or 0.0), 4),
            }
            for r in by_model
        ],
        "by_endpoint": [
            {
                "endpoint": r.endpoint or "(unlabeled)",
                "calls": int(r.calls or 0),
                "tokens": int(r.tokens or 0),
                "cost_usd": round(float(r.cost_usd or 0.0), 4),
            }
            for r in by_endpoint
        ],
    }
