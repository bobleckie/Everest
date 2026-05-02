"""Optional Sentry error reporting.

Wired up at backend startup if `SENTRY_DSN` is set in the environment.
Silent no-op otherwise — adding the dep does not change behavior.

Privacy-respecting defaults:
  * send_default_pii=False        (no IPs / cookies / bodies auto-attached)
  * traces_sample_rate=0.0        (perf tracing off — opt-in)
  * profiles_sample_rate=0.0      (profiling off — opt-in)
  * server_name=hostname only

Env vars:
  SENTRY_DSN                       (required to enable)
  SENTRY_ENVIRONMENT               default "development"
  SENTRY_RELEASE                   default unset (Sentry will auto-detect)
  SENTRY_TRACES_SAMPLE_RATE        default "0.0"
  SENTRY_SEND_DEFAULT_PII          default "false"
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def init_sentry() -> Optional[bool]:
    """Initialize Sentry if SENTRY_DSN is set. Returns True when enabled,
    False when skipped, None on error.
    """
    dsn = os.getenv("SENTRY_DSN", "").strip()
    if not dsn:
        logger.debug("Sentry skipped (SENTRY_DSN not set).")
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration
    except Exception as e:  # noqa: BLE001 — defensive
        logger.warning("Sentry SDK import failed: %s", e)
        return None

    def _f(name: str, default: str) -> float:
        try:
            return float(os.getenv(name, default))
        except ValueError:
            return float(default)

    def _b(name: str, default: str) -> bool:
        return os.getenv(name, default).strip().lower() in ("1", "true", "yes")

    sentry_sdk.init(
        dsn=dsn,
        environment=os.getenv("SENTRY_ENVIRONMENT", "development"),
        release=os.getenv("SENTRY_RELEASE") or None,
        traces_sample_rate=_f("SENTRY_TRACES_SAMPLE_RATE", "0.0"),
        profiles_sample_rate=_f("SENTRY_PROFILES_SAMPLE_RATE", "0.0"),
        send_default_pii=_b("SENTRY_SEND_DEFAULT_PII", "false"),
        integrations=[
            StarletteIntegration(),
            FastApiIntegration(),
            SqlalchemyIntegration(),
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        ],
        # Drop low-signal noise.
        ignore_errors=[KeyboardInterrupt, SystemExit],
    )
    logger.info(
        "[observability] Sentry initialized env=%s traces=%s pii=%s",
        os.getenv("SENTRY_ENVIRONMENT", "development"),
        _f("SENTRY_TRACES_SAMPLE_RATE", "0.0"),
        _b("SENTRY_SEND_DEFAULT_PII", "false"),
    )
    return True


def capture_exception(exc: BaseException) -> None:
    """Best-effort — silently no-ops if Sentry isn't initialized."""
    try:
        import sentry_sdk
        sentry_sdk.capture_exception(exc)
    except Exception:  # noqa: BLE001
        pass
