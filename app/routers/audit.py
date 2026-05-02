"""Read API for the audit log (admin only)."""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..auth import get_current_admin_user
from ..database import get_db
from ..models import AuditLogEntry, User

router = APIRouter()


@router.get("")
def list_audit(
    user_id: Optional[int] = Query(None),
    username: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    target_type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    since: Optional[datetime] = Query(None, description="ISO datetime; entries on/after"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    _admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    """List audit-log rows newest-first. Admin only."""
    q = db.query(AuditLogEntry)
    if user_id is not None:
        q = q.filter(AuditLogEntry.user_id == user_id)
    if username:
        q = q.filter(AuditLogEntry.username == username)
    if action:
        q = q.filter(AuditLogEntry.action == action)
    if target_type:
        q = q.filter(AuditLogEntry.target_type == target_type)
    if status:
        q = q.filter(AuditLogEntry.status == status)
    if since:
        q = q.filter(AuditLogEntry.ts >= since)
    total = q.count()
    rows = (
        q.order_by(AuditLogEntry.ts.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    def _row(r: AuditLogEntry) -> dict:
        import json as _json
        try:
            payload = _json.loads(r.payload) if r.payload else None
        except Exception:  # noqa: BLE001
            payload = r.payload
        return {
            "id": r.id,
            "ts": r.ts.isoformat() if r.ts else None,
            "user_id": r.user_id,
            "username": r.username,
            "action": r.action,
            "target_type": r.target_type,
            "target_id": r.target_id,
            "ip": r.ip,
            "request_id": r.request_id,
            "status": r.status,
            "payload": payload,
        }

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [_row(r) for r in rows],
    }
