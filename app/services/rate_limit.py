"""In-memory per-user rate limiter for expensive endpoints.

Token-bucket: each (user_id, bucket) gets a bucket of `capacity` tokens
that refills at `capacity / window_seconds` tokens per second. A request
takes 1 token; if the bucket is empty, we reject with HTTP 429.

This is suitable for our single-process deploy. When we move to multiple
workers (gunicorn -w / replicas), swap the storage layer for Redis. The
public API (`enforce_rate_limit`) doesn't change.

Defaults (env-overridable, evaluated lazily so tests can patch):
  EVEREST_RATE_LIMIT_LLM_PER_HOUR        (default 30) -- baseline cap
  EVEREST_RATE_LIMIT_LLM_VENDOR_PER_HOUR (default 10)
  EVEREST_RATE_LIMIT_LLM_EVALUATOR_PER_HOUR (default 10)
  -- admins are always unlimited.

Usage in a router:
    from ..services.rate_limit import enforce_rate_limit
    @router.post("/ask", dependencies=[Depends(enforce_rate_limit("llm"))])
    def ask(...): ...
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, HTTPException, Request

logger = logging.getLogger(__name__)


# ── Bucket state ──────────────────────────────────────────────────────
@dataclass
class _BucketState:
    tokens: float
    last_refill: float


_state: dict[tuple[int, str], _BucketState] = {}
_lock = threading.Lock()


def _capacity_for(role: str, bucket: str) -> Optional[int]:
    """Return the capacity (max tokens) for the given role+bucket, or
    None to mean 'unlimited'.
    """
    if role == "admin":
        return None  # admins bypass rate limits
    if bucket == "llm":
        if role == "vendor":
            return int(os.getenv("EVEREST_RATE_LIMIT_LLM_VENDOR_PER_HOUR", "10"))
        if role == "evaluator":
            return int(os.getenv("EVEREST_RATE_LIMIT_LLM_EVALUATOR_PER_HOUR", "10"))
        return int(os.getenv("EVEREST_RATE_LIMIT_LLM_PER_HOUR", "30"))
    # Unknown bucket -> generous default (don't accidentally block)
    return 1000


_WINDOW_SECONDS = 60 * 60  # 1 hour for all "per-hour" buckets


def _consume(user_id: int, bucket: str, capacity: int) -> tuple[bool, float, float]:
    """Try to consume 1 token. Returns (allowed, tokens_left, retry_after_s).

    `retry_after_s` is meaningful only when allowed is False.
    """
    now = time.time()
    refill_per_sec = capacity / _WINDOW_SECONDS
    key = (user_id, bucket)
    with _lock:
        st = _state.get(key)
        if st is None:
            st = _BucketState(tokens=float(capacity), last_refill=now)
            _state[key] = st
        # Refill since last touch (cap at capacity)
        elapsed = max(0.0, now - st.last_refill)
        st.tokens = min(float(capacity), st.tokens + elapsed * refill_per_sec)
        st.last_refill = now

        if st.tokens >= 1.0:
            st.tokens -= 1.0
            return True, st.tokens, 0.0
        # Not enough: how long until 1 token regenerates?
        deficit = 1.0 - st.tokens
        retry_after = deficit / refill_per_sec if refill_per_sec > 0 else _WINDOW_SECONDS
        return False, st.tokens, retry_after


def enforce_rate_limit(bucket: str = "llm"):
    """FastAPI dependency factory. Returns a dependency that raises 429
    when the calling user exceeds `bucket`.

    The dependency leans on `get_current_user` so the user is already
    authenticated by the time we get here.
    """
    from ..auth import get_current_user
    from ..models import User

    def _dep(
        request: Request,
        current_user: User = Depends(get_current_user),
    ) -> None:
        role = getattr(current_user, "role", None) or "proposal_manager"
        cap = _capacity_for(role, bucket)
        if cap is None:
            return  # admin / unlimited
        uid = getattr(current_user, "id", None)
        if uid is None:
            return  # can't bucket-key without a stable id; let it through
        allowed, left, retry = _consume(uid, bucket, cap)
        if not allowed:
            retry_int = max(1, int(retry) + 1)
            logger.warning(
                "[rate_limit] user_id=%s role=%s bucket=%s LIMIT EXCEEDED — retry in %ss",
                uid, role, bucket, retry_int,
            )
            raise HTTPException(
                status_code=429,
                detail={
                    "detail": (
                        f"Rate limit exceeded for {bucket}. Try again in "
                        f"{retry_int} second(s)."
                    ),
                    "bucket": bucket,
                    "limit": cap,
                    "window_seconds": _WINDOW_SECONDS,
                    "tokens_remaining": round(left, 2),
                    "retry_after_seconds": retry_int,
                },
                headers={"Retry-After": str(retry_int)},
            )

    return _dep


# ── Test/admin helpers ────────────────────────────────────────────────
def _reset_all() -> None:
    """Clear all buckets — only for tests."""
    with _lock:
        _state.clear()


def get_bucket_state(user_id: int, bucket: str) -> Optional[dict]:
    """Inspect a user's bucket. Returns None if the bucket has never
    been touched.
    """
    with _lock:
        st = _state.get((user_id, bucket))
        if st is None:
            return None
        return {"tokens": round(st.tokens, 2), "last_refill": st.last_refill}
