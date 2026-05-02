"""Best-effort audit-log writer.

Use from any router to record a security-relevant event:

    from ..services import audit
    audit.write(db, action="login_success", target_type="user",
                target_id=user.id, request=request, current_user=user)

Writes a row to the `audit_log` table. Failures are LOGGED but never
raised — the audit log must never block real work.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional, TYPE_CHECKING

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

if TYPE_CHECKING:  # avoid runtime circular import
    from fastapi import Request
    from ..models import User


def _client_ip(request: Optional["Request"]) -> Optional[str]:
    if request is None:
        return None
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return None


def _request_id(request: Optional["Request"]) -> Optional[str]:
    """Pull the per-request id set by RequestIdMiddleware."""
    try:
        from ..logging_config import request_id_ctx
        rid = request_id_ctx.get()
        if rid and rid != "-":
            return rid
    except Exception:  # noqa: BLE001
        pass
    if request is not None:
        return request.headers.get("x-request-id")
    return None


def write(
    db: Session,
    *,
    action: str,
    target_type: Optional[str] = None,
    target_id: Optional[int] = None,
    status: str = "ok",
    request: Optional["Request"] = None,
    current_user: Optional["User"] = None,
    payload: Optional[dict] = None,
) -> None:
    """Append one audit-log row. Best-effort; never raises."""
    try:
        from ..models import AuditLogEntry
        entry = AuditLogEntry(
            user_id=getattr(current_user, "id", None),
            username=getattr(current_user, "username", None),
            action=action,
            target_type=target_type,
            target_id=target_id,
            status=status,
            ip=_client_ip(request),
            request_id=_request_id(request),
            payload=json.dumps(payload, default=str) if payload else None,
        )
        db.add(entry)
        db.commit()
    except Exception as e:  # noqa: BLE001
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        logger.warning(
            "[audit] failed to write audit row action=%s target=%s/%s: %s",
            action, target_type, target_id, e,
        )


def write_with_username(
    db: Session,
    *,
    action: str,
    username: str,
    status: str = "ok",
    request: Optional["Request"] = None,
    payload: Optional[dict] = None,
) -> None:
    """For events where the actor isn't authenticated yet (e.g. failed
    login attempt). Stores the attempted username, no user_id.
    """
    try:
        from ..models import AuditLogEntry
        entry = AuditLogEntry(
            user_id=None,
            username=username,
            action=action,
            target_type="user",
            target_id=None,
            status=status,
            ip=_client_ip(request),
            request_id=_request_id(request),
            payload=json.dumps(payload, default=str) if payload else None,
        )
        db.add(entry)
        db.commit()
    except Exception as e:  # noqa: BLE001
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        logger.warning(
            "[audit] failed to write audit row (unauth) action=%s username=%s: %s",
            action, username, e,
        )
