"""User-invitation service.

Replaces the legacy "admin sets a temp password and emails it" flow.

Workflow:
  1. Admin calls POST /api/auth/users/{id}/invite.
  2. We mint a 32-byte URL-safe random token, store its sha256 hash.
  3. Admin sees the redeem URL (and email body if SMTP isn't wired up).
  4. User clicks the URL, sets their own password, and lands signed in.

No admin ever sees a user's password.
"""
from __future__ import annotations

import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta
from typing import Optional, TYPE_CHECKING

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from fastapi import Request
    from ..models import User as _User, UserInvitation as _Invitation


def _expiry_days() -> int:
    try:
        return max(1, int(os.getenv("INVITE_EXPIRE_DAYS", "7")))
    except ValueError:
        return 7


def _hash(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def _client_ip(request: Optional["Request"]) -> Optional[str]:
    if request is None:
        return None
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return None


def public_url() -> str:
    """Best-guess base URL for the redeem link.
    Operators set EVEREST_PUBLIC_URL in deploys.
    """
    return os.getenv("EVEREST_PUBLIC_URL", "http://localhost:3000").rstrip("/")


def redeem_url(plaintext: str) -> str:
    return f"{public_url()}/redeem-invite/{plaintext}"


def mint(
    db: Session,
    user: "_User",
    invited_by: Optional["_User"] = None,
    request: Optional["Request"] = None,
) -> tuple[str, "_Invitation"]:
    """Generate a new invitation token, persist its hash, return plaintext."""
    from ..models import UserInvitation
    plaintext = secrets.token_urlsafe(32)
    row = UserInvitation(
        token_hash=_hash(plaintext),
        user_id=user.id,
        email=user.email,
        invited_by=getattr(invited_by, "id", None),
        created_at=datetime.utcnow(),
        expires_at=datetime.utcnow() + timedelta(days=_expiry_days()),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return plaintext, row


def lookup(db: Session, plaintext: str):
    from ..models import UserInvitation
    return (
        db.query(UserInvitation)
        .filter(UserInvitation.token_hash == _hash(plaintext))
        .one_or_none()
    )


def consume(
    db: Session,
    plaintext: str,
    new_password: str,
    request: Optional["Request"] = None,
) -> Optional["_User"]:
    """Validate the invitation, set the user's password, mark consumed.
    Returns the User on success; None on any validation failure.

    Caller must enforce password-strength rules; this module just stores
    whatever it's handed via the auth helpers.
    """
    from ..models import UserInvitation, User
    from ..auth import get_password_hash
    row = lookup(db, plaintext)
    if row is None:
        logger.warning("[invitations] unknown token presented")
        return None
    now = datetime.utcnow()
    if row.consumed_at is not None:
        logger.warning("[invitations] already-consumed token replayed id=%s", row.id)
        return None
    if row.expires_at < now:
        logger.warning("[invitations] expired token id=%s", row.id)
        return None
    user = db.query(User).filter(User.id == row.user_id).one_or_none()
    if user is None or not user.is_active:
        logger.warning("[invitations] user %s missing or disabled", row.user_id)
        return None
    user.hashed_password = get_password_hash(new_password)
    user.must_change_password = False
    row.consumed_at = now
    row.consumed_ip = _client_ip(request)
    db.commit()
    db.refresh(user)
    return user


def gc_expired(db: Session) -> int:
    """Delete invitations that have been expired or consumed > 30 days."""
    from ..models import UserInvitation
    cutoff = datetime.utcnow() - timedelta(days=30)
    n = (
        db.query(UserInvitation)
        .filter(
            (UserInvitation.expires_at < cutoff)
            | (UserInvitation.consumed_at < cutoff)
        )
        .delete(synchronize_session=False)
    )
    db.commit()
    return int(n or 0)
