"""LLM usage / cost tracking.

Best-effort. Failures NEVER block real LLM calls.

Per-million-token rates are baked in for the models we actively use
(Anthropic Claude 4.x family, OpenAI gpt-4o & gpt-4o-mini, embeddings).
For unknown models we still record the call (with cost_estimate_usd=NULL)
so usage isn't lost — operators can update pricing later and recompute.

Public API:
    set_endpoint(label)            # context manager — label the next call
    record(...)                    # write one usage row
    extract_anthropic_usage(resp)  # helper to pull tokens from a response
    extract_openai_usage(resp)
"""
from __future__ import annotations

import contextlib
import contextvars
import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ── Endpoint correlation ─────────────────────────────────────────────
endpoint_ctx: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "llm_endpoint", default=None
)


@contextlib.contextmanager
def set_endpoint(label: str):
    """Tag any LLM calls made inside this block with `label`.

    Useful so the dashboard can group costs by feature, e.g.
        with llm_usage.set_endpoint("extract_requirements"):
            extract_rfp_requirements(db, doc_id)
    """
    token = endpoint_ctx.set(label)
    try:
        yield
    finally:
        endpoint_ctx.reset(token)


# ── Cost table ────────────────────────────────────────────────────────
# Rates are USD per 1,000,000 tokens (input, output). Update as needed.
# Sources: provider pricing pages as of 2026-04. Unknown models => None.
_COST_PER_MTOK: dict[str, tuple[float, float]] = {
    # Anthropic Claude (Apr 2026 pricing)
    "claude-opus-4-7":              (15.0, 75.0),
    "claude-opus-4-1":              (15.0, 75.0),
    "claude-opus-4":                (15.0, 75.0),
    "claude-sonnet-4-5":            (3.0,  15.0),
    "claude-sonnet-4-5-20250929":   (3.0,  15.0),
    "claude-sonnet-4":              (3.0,  15.0),
    "claude-haiku-4":               (0.25, 1.25),
    "claude-3-5-sonnet-20241022":   (3.0,  15.0),
    "claude-3-5-haiku-20241022":    (0.25, 1.25),
    # OpenAI (Apr 2026)
    "gpt-4o":                       (2.5,  10.0),
    "gpt-4o-mini":                  (0.15, 0.60),
    "gpt-4-turbo":                  (10.0, 30.0),
    # Embeddings (output cost = 0)
    "text-embedding-3-small":       (0.02, 0.0),
    "text-embedding-3-large":       (0.13, 0.0),
}


def _resolve_rate(model: str) -> Optional[tuple[float, float]]:
    """Return (input_per_mtok, output_per_mtok) for the given model.
    Tries exact match first, then a longest-prefix match (helps when
    Anthropic appends date suffixes to model names).
    """
    if model in _COST_PER_MTOK:
        return _COST_PER_MTOK[model]
    candidates = [k for k in _COST_PER_MTOK if model.startswith(k) or k.startswith(model)]
    if candidates:
        # Prefer the longest matching key.
        candidates.sort(key=len, reverse=True)
        return _COST_PER_MTOK[candidates[0]]
    return None


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> Optional[float]:
    rate = _resolve_rate(model)
    if rate is None:
        return None
    in_rate, out_rate = rate
    return round(
        (input_tokens / 1_000_000.0) * in_rate
        + (output_tokens / 1_000_000.0) * out_rate,
        6,
    )


# ── Token extraction helpers ─────────────────────────────────────────
def extract_anthropic_usage(resp: Any) -> tuple[int, int]:
    """Pull (input_tokens, output_tokens) from an Anthropic Messages
    response. Returns (0, 0) on failure.
    """
    try:
        u = getattr(resp, "usage", None)
        if u is None:
            return (0, 0)
        return (
            int(getattr(u, "input_tokens", 0) or 0),
            int(getattr(u, "output_tokens", 0) or 0),
        )
    except Exception:  # noqa: BLE001
        return (0, 0)


def extract_openai_usage(resp: Any) -> tuple[int, int]:
    try:
        u = getattr(resp, "usage", None)
        if u is None:
            return (0, 0)
        return (
            int(getattr(u, "prompt_tokens", 0) or 0),
            int(getattr(u, "completion_tokens", 0) or 0),
        )
    except Exception:  # noqa: BLE001
        return (0, 0)


# ── Recorder ──────────────────────────────────────────────────────────
def record(
    *,
    provider: str,
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    latency_ms: Optional[int] = None,
    status: str = "ok",
    error: Optional[str] = None,
    user_id: Optional[int] = None,
    username: Optional[str] = None,
    endpoint: Optional[str] = None,
) -> None:
    """Append one llm_usage row. Best-effort; never raises."""
    try:
        # Defer DB import so a missing model doesn't break LLM-only modules.
        from ..database import SessionLocal
        from ..models import LlmUsage
        # Fall back to context-vars when explicit args weren't passed.
        if endpoint is None:
            endpoint = endpoint_ctx.get()
        # Pull request_id from the logging contextvar.
        try:
            from ..logging_config import request_id_ctx
            rid = request_id_ctx.get()
            if rid == "-":
                rid = None
        except Exception:  # noqa: BLE001
            rid = None
        cost = estimate_cost(model, input_tokens, output_tokens)
        total = (input_tokens or 0) + (output_tokens or 0)
        db = SessionLocal()
        try:
            db.add(LlmUsage(
                user_id=user_id,
                username=username,
                request_id=rid,
                endpoint=endpoint,
                provider=provider,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total,
                cost_estimate_usd=cost,
                latency_ms=latency_ms,
                status=status,
                error=error,
            ))
            db.commit()
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[llm_usage] failed to record %s %s: %s", provider, model, e
        )


def time_block_ms(start: float) -> int:
    return int((time.time() - start) * 1000)
