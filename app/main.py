from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
import os
import logging
from dotenv import load_dotenv

from .database import engine, Base
from .logging_config import setup_logging
from .error_handlers import exception_handlers
from .config import validate_runtime_config, check_model_db_drift
from .routers import (
    orchestrator, competitive_research, parsons_sme, conflict_detector,
    document_composer, requirement_extractor, proposals, auth, personas, prompts, executions, parsons_data,
    scoring, settings, competitors, dashboard, ingestion, intelligence, knowledge, pricing, schedule,
    questions, rfp_diff, response_scoring, logs, response_workbench, parsons_knowledge,
    parsons_response, diff_question_analysis, baseline_consolidation, jobs,
    audit as audit_router, llm_usage as llm_usage_router,
    flashcards as flashcards_router,
    wait_time_ab as wait_time_ab_router,
    gaps as gaps_router,
)

# Load environment variables
load_dotenv()

# Initialize Sentry as early as possible so init-time errors get reported.
# No-op if SENTRY_DSN is not set.
from .observability import init_sentry  # noqa: E402
init_sentry()

# Setup logging
log_level = os.getenv("LOG_LEVEL", "INFO")
setup_logging(level=log_level)

# Validate critical config (SECRET_KEY, DEBUG, CORS, AI providers).
# Hard-fails the process if SECRET_KEY is missing or the placeholder.
validate_runtime_config()

# Create database tables (additive only — never drops columns).
Base.metadata.create_all(bind=engine)

# Backfill columns added to existing tables (SQLAlchemy's create_all does
# NOT add columns to tables that already exist). Same pattern as
# seed_admin.py::ensure_columns. Kept tiny + idempotent — failures here
# are logged but never crash startup.
def _ensure_columns(table: str, expected: dict) -> None:
    from sqlalchemy import inspect as _sa_inspect, text as _sql_text
    try:
        insp = _sa_inspect(engine)
        existing = {c["name"] for c in insp.get_columns(table)}
    except Exception:
        return
    alters = []
    for col, spec in expected.items():
        if col not in existing:
            alters.append(f"ALTER TABLE {table} ADD COLUMN {col} {spec}")
    if alters:
        try:
            with engine.begin() as conn:
                for stmt in alters:
                    conn.execute(_sql_text(stmt))
                    logging.getLogger(__name__).info(f"[migrate] applied: {stmt}")
        except Exception as e:
            logging.getLogger(__name__).warning(f"[migrate] failed for {table}: {e}")


# Relative-deadline columns added 2026-04-28 (#401).
_ensure_columns("rfp_schedule_events", {
    "offset_days": "INTEGER",
    "offset_anchor": "VARCHAR",
    "is_draft_with_quote": "BOOLEAN DEFAULT 0",
    "section_refs": "TEXT",
    "event_date_resolved": "BOOLEAN DEFAULT 0",
})
_ensure_columns("proposals", {
    "contract_effective_date": "DATETIME",
})

# Flashcards table backfill (idempotent — only adds columns, never the
# whole table; the table itself is created by Base.metadata.create_all).
_ensure_columns("flashcards", {
    "card_type": "VARCHAR DEFAULT 'recall'",
    "difficulty": "VARCHAR DEFAULT 'medium'",
    "review_count": "INTEGER DEFAULT 0",
    "correct_count": "INTEGER DEFAULT 0",
    "last_reviewed_at": "DATETIME",
    "next_review_at": "DATETIME",
    "ease_factor": "FLOAT DEFAULT 2.5",
    "interval_days": "INTEGER DEFAULT 0",
    "last_rating": "VARCHAR",
    "generated_by_model": "VARCHAR",
})

# Read-only sanity check: warn (don't fail) if the DB has columns the SA
# model doesn't declare, or vice versa. Catches the IngestedDocument-style
# regression at startup instead of mid-request.
check_model_db_drift(engine, Base)

# Seed default scoring rubric + competitors
from .database import SessionLocal
from .routers.scoring import seed_default_rubric
from .models import Competitor
import json as _json

_seed_db = SessionLocal()
try:
    seed_default_rubric(_seed_db)
    # Seed known competitors if table is empty
    if _seed_db.query(Competitor).count() == 0:
        _seed_competitors = [
            ("IDEMIA", "https://www.idemia.com", ["IDEMIA Identity & Security"], "Global identity & security; incumbent NJ MVC vendor for REAL ID solutions"),
            ("FAST Enterprises", "https://www.fastenterprises.com", ["FAST"], "DMV modernization software (GenTax, FastDS); active in NJ and 25+ states"),
            ("Conduent", "https://www.conduent.com", ["Conduent Government Solutions"], "Business process services for government; NJ toll and DMV processing"),
            ("Deloitte", "https://www.deloitte.com", ["Deloitte Consulting"], "Big-4 consultancy with large NJ government IT practice"),
            ("CGI Group", "https://www.cgi.com", ["CGI Federal"], "IT services and consulting for government; active in mid-Atlantic DMV contracts"),
        ]
        for name, website, aliases, desc in _seed_competitors:
            _seed_db.add(Competitor(
                name=name,
                website=website,
                aliases=_json.dumps(aliases),
                description=desc,
                watchlist=True,
            ))
        _seed_db.commit()
    # Seed system personas
    from .services.persona_factory import seed_system_personas
    seed_system_personas(_seed_db)
finally:
    _seed_db.close()

# ── APScheduler: daily competitor news refresh at 06:00 ──────────────
from apscheduler.schedulers.background import BackgroundScheduler

_scheduler = BackgroundScheduler()
_news_logger = logging.getLogger("competitor_news_scheduler")

def _daily_news_refresh():
    """Scheduled job: refresh news for all watchlisted competitors."""
    from .services.competitor_news import refresh_all_watchlisted
    db = SessionLocal()
    try:
        results = refresh_all_watchlisted(db)
        _news_logger.info(f"Daily news refresh complete: {results}")
    except Exception as e:
        _news_logger.error(f"Daily news refresh failed: {e}")
    finally:
        db.close()

_scheduler.add_job(_daily_news_refresh, "cron", hour=6, minute=0, id="daily_news_refresh", replace_existing=True)

@asynccontextmanager
async def lifespan(app):
    _scheduler.start()
    _news_logger.info("Competitor news scheduler started (daily at 06:00)")
    yield
    _scheduler.shutdown(wait=False)

app = FastAPI(
    title="Parsons RFP Response Platform",
    description="AI-powered RFP response generation system for Parsons Corporation",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware — origins driven by CORS_ORIGINS env var.
# Wildcard "*" is incompatible with allow_credentials=True (browsers reject
# the response) AND is unsafe in any shared deployment. We strip "*" and
# log a warning so misconfiguration doesn't silently disable auth.
_cors_origins_raw = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:3001")
_cors_origins_split = [o.strip() for o in _cors_origins_raw.split(",") if o.strip()]
_cors_origins = [o for o in _cors_origins_split if o != "*"]
if "*" in _cors_origins_split:
    logging.getLogger(__name__).warning(
        "CORS_ORIGINS contained '*' — stripped. Pin to specific origins."
    )
if not _cors_origins:
    _cors_origins = ["http://localhost:3000", "http://localhost:3001"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "X-Requested-With"],
)


# ── Security headers middleware ─────────────────────────────────────
# Adds defensive HTTP response headers on every response. HSTS is only
# emitted when the request appears to have arrived over HTTPS (a reverse
# proxy is expected to set X-Forwarded-Proto when terminating TLS).
# CSP is intentionally NOT set here — it interacts heavily with the React
# bundle (inline styles, dynamic chunks) and will be added together with
# the production frontend build step.
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), interest-cohort=()",
        )
        # HSTS only when behind HTTPS (avoid pinning HTTP-only dev to HTTPS).
        proto = request.headers.get("x-forwarded-proto", request.url.scheme)
        if proto == "https":
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        return response


app.add_middleware(SecurityHeadersMiddleware)


# ── Request id middleware ───────────────────────────────────────────
# Assigns a short uuid to each request, stores it in a contextvar so
# every logger.info() call within the request automatically records it
# (see app/logging_config.py::RequestIdFilter), and echoes it back to
# the client as `X-Request-Id` so users can quote it when reporting bugs.
import uuid as _uuid
from .logging_config import request_id_ctx as _request_id_ctx


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("x-request-id") or _uuid.uuid4().hex[:12]
        token = _request_id_ctx.set(rid)
        try:
            response = await call_next(request)
        finally:
            _request_id_ctx.reset(token)
        response.headers["X-Request-Id"] = rid
        return response


app.add_middleware(RequestIdMiddleware)

# Add exception handlers
for exception_class, handler in exception_handlers.items():
    app.add_exception_handler(exception_class, handler)

# Include routers
app.include_router(auth.router, prefix="/api/auth", tags=["Authentication"])
app.include_router(orchestrator.router, prefix="/api/orchestrator", tags=["Orchestrator"])
app.include_router(competitive_research.router, prefix="/api/competitive-research", tags=["Competitive Research"])
app.include_router(parsons_sme.router, prefix="/api/parsons-sme", tags=["Parsons SME"])
app.include_router(conflict_detector.router, prefix="/api/conflict-detector", tags=["Conflict Detector"])
app.include_router(document_composer.router, prefix="/api/document-composer", tags=["Document Composer"])
app.include_router(requirement_extractor.router, prefix="/api/requirement-extractor", tags=["Requirement Extractor"])
app.include_router(proposals.router, prefix="/api/proposals", tags=["Proposals"])
app.include_router(personas.router, prefix="/api/personas", tags=["Personas"])
app.include_router(prompts.router, prefix="/api/prompts", tags=["Prompts"])
app.include_router(executions.router, prefix="/api/executions", tags=["Executions"])
app.include_router(parsons_data.router, prefix="/api", tags=["Parsons Data"])
app.include_router(parsons_knowledge.router, prefix="/api/parsons-knowledge", tags=["Parsons Knowledge"])
app.include_router(parsons_response.router, prefix="/api/parsons-response", tags=["Parsons Response"])
app.include_router(diff_question_analysis.router, prefix="/api/diff-question-analysis", tags=["Diff Question Analysis"])
app.include_router(baseline_consolidation.router, prefix="/api/baseline-consolidation", tags=["Baseline Consolidation"])
app.include_router(scoring.router, prefix="/api/scoring", tags=["Scoring"])
app.include_router(settings.router, prefix="/api/settings", tags=["Settings"])
app.include_router(competitors.router, prefix="/api/competitors", tags=["Competitors"])
app.include_router(dashboard.router, prefix="/api/dashboard", tags=["Dashboard"])
app.include_router(ingestion.router, prefix="/api/documents", tags=["Document Ingestion"])
app.include_router(intelligence.router, prefix="/api/intelligence", tags=["Competitive Intelligence"])
app.include_router(knowledge.router, prefix="/api/knowledge", tags=["Knowledge Base"])
app.include_router(pricing.router, prefix="/api/pricing", tags=["Pricing"])
app.include_router(schedule.router, prefix="/api/schedule", tags=["Schedule"])
app.include_router(questions.router, prefix="/api/questions", tags=["RFP Questions"])
app.include_router(rfp_diff.router, prefix="/api/rfp-diff", tags=["RFP Diff"])
app.include_router(response_scoring.router, prefix="/api/response-scoring", tags=["Response Scoring"])
app.include_router(response_workbench.router, prefix="/api/workbench", tags=["Response Workbench"])
app.include_router(logs.router, prefix="/api/logs", tags=["Logs"])
app.include_router(jobs.router, prefix="/api/jobs", tags=["Jobs"])
app.include_router(audit_router.router, prefix="/api/audit", tags=["Audit"])
app.include_router(llm_usage_router.router, prefix="/api/llm-usage", tags=["LLM Usage"])
app.include_router(flashcards_router.router, prefix="/api/flashcards", tags=["Flashcards"])
app.include_router(wait_time_ab_router.router, prefix="/api/wait-time-ab", tags=["Wait Time A/B"])
app.include_router(gaps_router.router, prefix="/api/gaps", tags=["Gap Workspace"])


# ── Liveness / readiness probes (unauthenticated) ───────────────────
# /healthz — process is up and serving (used by load balancers).
# /readyz  — process can talk to its dependencies (DB + chroma dir).
@app.get("/healthz", tags=["Health"])
def healthz():
    return {"status": "ok"}


@app.get("/readyz", tags=["Health"])
def readyz():
    from fastapi.responses import JSONResponse
    from sqlalchemy import text as _sql_text
    from . import paths as _paths_mod
    checks: dict = {}
    ok = True
    # DB
    try:
        _db = SessionLocal()
        try:
            _db.execute(_sql_text("SELECT 1"))
            checks["db"] = "ok"
        finally:
            _db.close()
    except Exception as e:
        ok = False
        checks["db"] = f"error: {type(e).__name__}: {e}"
    # Chroma directory
    try:
        cd = _paths_mod.chroma_dir()
        checks["chroma_dir"] = "ok" if cd.exists() else f"missing: {cd}"
        if not cd.exists():
            ok = False
    except Exception as e:
        ok = False
        checks["chroma_dir"] = f"error: {type(e).__name__}: {e}"
    body = {"status": "ok" if ok else "fail", "checks": checks}
    return JSONResponse(content=body, status_code=200 if ok else 503)


@app.get("/")
def read_root():
    return {"message": "Welcome to Parsons RFP Response Platform API", "version": "1.0.0"}