"""Refresh-token store with rotation + replay detection.

Refresh tokens are 32-byte secure-random values. We store only the
SHA-256 hash so a DB leak doesn't equal a token leak. Every refresh
mints a new token and revokes the previous one (rotation).

If a client presents a refresh token that we have on file *as already
revoked*, that's a strong signal the token was stolen and replayed
after the legitimate user already used it once. We then revoke EVERY
descendant in that chain so the attacker is locked out (and the
legitimate user simply logs in again on next access-token expiry).

Tokens are URL-safe base64. Default lifetime: 7 days, env-overridable
via REFRESH_TOKEN_EXPIRE_DAYS.

Public API:
    mint(db, user, request) -> str (plaintext, only here)
    verify_and_rotate(db, plaintext, request) -> (str, User) | None
    revoke(db, plaintext) -> bool
    revoke_all_for_user(db, user_id) -> int
    gc_expired(db) -> int
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
    from ..models import User as _User


def _expiry_days() -> int:
    try:
        return max(1, int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7")))
    except ValueError:
        return 7


def _hash_token(plaintext: str) -> str:
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


def _user_agent(request: Optional["Request"]) -> Optional[str]:
    if request is None:
        return None
    return (request.headers.get("user-agent") or "")[:500]


def mint(
    db: Session,
    user: "_User",
    request: Optional["Request"] = None,
    parent_id: Optional[int] = None,
) -> tuple[str, "RefreshToken"]:  # type: ignore[name-defined]
    """Generate a new refresh token, persist its hash, return plaintext."""
    from ..models import RefreshToken
    plaintext = secrets.token_urlsafe(32)  # 256-bit
    row = RefreshToken(
        token_hash=_hash_token(plaintext),
        user_id=user.id,
        issued_at=datetime.utcnow(),
        expires_at=datetime.utcnow() + timedelta(days=_expiry_days()),
        parent_id=parent_id,
        client_ip=_client_ip(request),
        user_agent=_user_agent(request),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return plaintext, row


def lookup(db: Session, plaintext: str):
    """Return the RefreshToken row for `plaintext`, or None."""
    from ..models import RefreshToken
    return (
        db.query(RefreshToken)
        .filter(RefreshToken.token_hash == _hash_token(plaintext))
        .one_or_none()
    )


def _revoke_chain(db: Session, row) -> int:
    """Revoke `row` and every descendant. Returns count revoked."""
    from ..models import RefreshToken
    count = 0
    visited: set[int] = set()
    queue = [row.id]
    while queue:
        cur_id = queue.pop()
        if cur_id in visited:
            continue
        visited.add(cur_id)
        cur = db.query(RefreshToken).filter(RefreshToken.id == cur_id).one_or_none()
        if cur and cur.revoked_at is None:
            cur.revoked_at = datetime.utcnow()
            count += 1
        children = db.query(RefreshToken).filter(RefreshToken.parent_id == cur_id).all()
        for c in children:
            queue.append(c.id)
    db.commit()
    return count


def verify_and_rotate(
    db: Session,
    plaintext: str,
    request: Optional["Request"] = None,
) -> Optional[tuple[str, "_User"]]:
    """Validate `plaintext`, rotate it, and return (new_plaintext, user).

    Returns None if the token is unknown, expired, or already revoked.
    Replay of an already-revoked token in a chain triggers chain-wide
    revocation (theft detection).
    """
    from ..models import RefreshToken, User
    row = lookup(db, plaintext)
    if row is None:
        logger.warning("[refresh] unknown token presented")
        return None
    now = datetime.utcnow()
    if row.expires_at < now:
        logger.warning("[refresh] expired token id=%s", row.id)
        return None
    if row.revoked_at is not None:
        # Replay of a revoked token — assume theft, blow up the chain.
        logger.warning(
            "[refresh] REPLAY DETECTED on revoked token id=%s user_id=%s — "
            "revoking entire chain.",
            row.id, row.user_id,
        )
        # Walk up to the root then revoke everything reachable.
        root = row
        from ..models import RefreshToken as _RT
        while root.parent_id is not None:
            parent = db.query(_RT).filter(_RT.id == root.parent_id).one_or_none()
            if parent is None:
                break
            root = parent
        _revoke_chain(db, root)
        return None
    user = db.query(User).filter(User.id == row.user_id).one_or_none()
    if user is None or not user.is_active:
        logger.warning("[refresh] user %s missing or disabled", row.user_id)
        return None
    # Mark this token used + revoke + mint a new one as its child.
    row.last_used_at = now
    row.revoked_at = now
    db.commit()
    new_plain, _new_row = mint(db, user, request, parent_id=row.id)
    return new_plain, user


def revoke(db: Session, plaintext: str) -> bool:
    """Revoke a single refresh token. Returns True if it existed."""
    row = lookup(db, plaintext)
    if row is None:
        return False
    if row.revoked_at is None:
        row.revoked_at = datetime.utcnow()
        db.commit()
    return True


def revoke_all_for_user(db: Session, user_id: int) -> int:
    """Revoke EVERY active refresh token for a user. Returns count."""
    from ..models import RefreshToken
    count = (
        db.query(RefreshToken)
        .filter(RefreshToken.user_id == user_id)
        .filter(RefreshToken.revoked_at.is_(None))
        .update({"revoked_at": datetime.utcnow()}, synchronize_session=False)
    )
    db.commit()
    return int(count or 0)


def gc_expired(db: Session) -> int:
    """Delete refresh tokens that have been expired or revoked > 30 days.
    Keeps the audit trail short while not losing recent forensic data.
    """
    from ..models import RefreshToken
    cutoff = datetime.utcnow() - timedelta(days=30)
    n = (
        db.query(RefreshToken)
        .filter(
            (RefreshToken.expires_at < cutoff)
            | (RefreshToken.revoked_at < cutoff)
        )
        .delete(synchronize_session=False)
    )
    db.commit()
    return int(n or 0)
