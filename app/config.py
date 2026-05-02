"""Runtime configuration validation for the Everest backend.

Called once at startup from `app.main`. Its job is *not* to introduce a new
configuration framework — `os.getenv()` is still the source of truth — but
to fail fast on misconfiguration (placeholder SECRET_KEY in production,
DEBUG=true with a non-localhost CORS origin, etc.) and to emit a redacted
summary so operators can see at a glance what the running process believes
its configuration to be.

Read-only. Never writes to .env or any data file.
"""
from __future__ import annotations

import logging
import os
import sys

logger = logging.getLogger(__name__)

# Sentinel value shipped in app/auth.py as the fallback when SECRET_KEY is
# missing. Must match exactly.
_PLACEHOLDER_SECRET = "your-secret-key-here-change-in-production"


def _truthy(val: str | None) -> bool:
    return (val or "").strip().lower() in ("1", "true", "yes", "on")


def _redact(val: str | None, keep: int = 4) -> str:
    if not val:
        return "(unset)"
    if len(val) <= keep * 2:
        return "*" * len(val)
    return f"{val[:keep]}…{val[-keep:]} ({len(val)} chars)"


def validate_runtime_config() -> None:
    """Validate critical env vars. Hard-fail on unsafe configurations.

    Called from `app.main` during module import, *after* `load_dotenv()`.

    Hard failures (raise `SystemExit`):
      * SECRET_KEY missing or equal to the placeholder.

    Warnings (logged, non-fatal):
      * DEBUG=true (dev bypass token enabled).
      * No AI provider key set.
      * CORS_ORIGINS contains "*" or is empty.

    Always logs a redacted summary at INFO level.
    """
    secret = os.getenv("SECRET_KEY", "").strip()
    debug = _truthy(os.getenv("DEBUG"))
    cors = os.getenv("CORS_ORIGINS", "").strip()
    db_url = os.getenv("DATABASE_URL", "").strip()
    has_openai = bool(os.getenv("OPENAI_API_KEY", "").strip())
    has_anthropic = bool(os.getenv("ANTHROPIC_API_KEY", "").strip())
    token_minutes = os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "").strip() or "(default 480)"

    # ── Hard failures ────────────────────────────────────────────────
    if not secret or secret == _PLACEHOLDER_SECRET:
        logger.critical(
            "SECRET_KEY is missing or set to the placeholder value. "
            "Refusing to start. Generate one with:\n"
            "    py -c \"import secrets; print(secrets.token_hex(32))\"\n"
            "and put it in your .env as SECRET_KEY=..."
        )
        # SystemExit so tests / CI catch it cleanly; raises before any
        # request handler binds.
        raise SystemExit(2)

    if len(secret) < 32:
        logger.warning(
            "SECRET_KEY is shorter than 32 characters (%d). "
            "Recommend at least 64 hex chars (`secrets.token_hex(32)`).",
            len(secret),
        )

    # ── Warnings (non-fatal) ─────────────────────────────────────────
    if debug:
        logger.warning(
            "DEBUG=true — dev bypass token is ENABLED. "
            "Never enable this in a shared/hosted environment."
        )

    if not has_openai and not has_anthropic:
        logger.warning(
            "No LLM provider key found (OPENAI_API_KEY / ANTHROPIC_API_KEY). "
            "AI features will fall back to mock responses."
        )

    if not cors:
        logger.warning(
            "CORS_ORIGINS is empty — frontend will be unable to call /api. "
            "Set CORS_ORIGINS=http://localhost:3000 (or your real origin)."
        )
    elif "*" in cors:
        logger.warning(
            "CORS_ORIGINS contains '*' — wildcard origins are unsafe with "
            "credentialed requests. Pin to specific origins in production."
        )

    # ── Redacted summary ────────────────────────────────────────────
    logger.info(
        "[config] DATABASE_URL=%s | SECRET_KEY=%s | DEBUG=%s | "
        "ACCESS_TOKEN_EXPIRE_MINUTES=%s | CORS_ORIGINS=%s | "
        "OpenAI=%s | Anthropic=%s",
        _redact(db_url, keep=10) if db_url else "(unset → sqlite default)",
        _redact(secret, keep=4),
        debug,
        token_minutes,
        cors or "(unset)",
        "set" if has_openai else "MISSING",
        "set" if has_anthropic else "MISSING",
    )

    # Inform on stdout too — uvicorn's startup log can be noisy.
    print(
        f"[everest.config] startup OK  "
        f"DEBUG={debug}  "
        f"token_min={token_minutes}  "
        f"cors={cors or '(unset)'}  "
        f"openai={'y' if has_openai else 'N'}  "
        f"anthropic={'y' if has_anthropic else 'N'}",
        file=sys.stderr,
    )


def check_model_db_drift(engine, Base) -> None:
    """Warn on any drift between SQLAlchemy models and the live DB schema.

    READ-ONLY. Walks every table in `Base.metadata.tables` and compares
    the columns declared on the model against the columns the database
    reports via `sqlalchemy.inspect()`. Mismatches in either direction
    are logged at WARNING level (not raised — startup must not fail on
    drift; that would block emergency fixes).

    The classic regression this catches: a column was added to the DB
    via Alembic / ALTER TABLE but never declared on the ORM model, so
    `Model.created_at` raises AttributeError at request time. (See
    `IngestedDocument.created_at` incident, 2026-04-27.)

    No-op if `engine` is unreachable.
    """
    try:
        from sqlalchemy import inspect as _sa_inspect
        insp = _sa_inspect(engine)
        existing_tables = set(insp.get_table_names())
    except Exception as e:
        logger.debug("Skipping model<->DB drift check (inspect failed: %s)", e)
        return

    issues: list[str] = []
    for table_name, table in Base.metadata.tables.items():
        if table_name not in existing_tables:
            # Table not yet created in the DB; create_all will handle it.
            continue
        try:
            db_cols = {c["name"] for c in insp.get_columns(table_name)}
        except Exception as e:
            logger.debug("Skipping drift check for %s (%s)", table_name, e)
            continue
        model_cols = {c.name for c in table.columns}

        missing_in_db = model_cols - db_cols
        missing_in_model = db_cols - model_cols
        for col in sorted(missing_in_db):
            issues.append(f"{table_name}.{col}: declared on model but missing from DB")
        for col in sorted(missing_in_model):
            issues.append(f"{table_name}.{col}: present in DB but not declared on SA model")

    if issues:
        logger.warning(
            "[config] model<->DB schema drift detected (%d issue%s):",
            len(issues), "" if len(issues) == 1 else "s",
        )
        for line in issues:
            logger.warning("  - %s", line)
        logger.warning(
            "  Drift can cause AttributeError-on-attribute-access at request time. "
            "Add the missing Column declaration to app/models.py."
        )
    else:
        logger.info("[config] model<->DB schema check OK (no drift)")
