# Production-Hardening Rollback Log

Every production-hardening change MUST add an entry here BEFORE it runs.
Each entry: what changed, exactly how to undo it, and how to verify the undo.

Backups for this initiative live at:
`db_backups/pre-prod-hardening-2026-04-28/`

Verification gate: `py _fingerprint.py --out /tmp/fp.json` and diff
against `db_backups/pre-prod-hardening-2026-04-28/fingerprint.json`.

> **Hard rule (from `/memories/repo/data-safety-policy.md`):** No change ships
> until row-counts + sample-row hashes for `proposals, ingested_documents,
> document_chunks, rfp_questions, rfp_requirements, users` match (or the
> diff is *expected and explained* in the entry below).

---

## Step #100 — Baseline backup (2026-04-28) — DONE

**What changed:** New folder `db_backups/pre-prod-hardening-2026-04-28/`
containing:
- `rfp.db` (atomic sqlite3 .backup snapshot, 73,240,576 bytes, 71 tables,
  integrity_check = ok)
- `rfp_database.db` (legacy, 106,496 bytes)
- `chroma_db/` (recursive copy)
- `uploads/` (recursive copy, 45 files, ~213 MB)
- `.env`, `.env.example`

**Source data touched:** none. WAL checkpoint (`PRAGMA
wal_checkpoint(TRUNCATE)`) was issued on the live `rfp.db` before the snapshot.
This is a non-destructive flush of the write-ahead log into the main file.

**Rollback:** `Remove-Item -Recurse -Force db_backups/pre-prod-hardening-2026-04-28`.
But you don't want to — this is the safety net for everything that follows.

---

## Step #101 — Verification fingerprint (2026-04-28) — DONE

**What changed:**
- New file: `_fingerprint.py` (read-only, prints JSON of row counts +
  sample-row hashes; can be re-run any time).
- New file:
  `db_backups/pre-prod-hardening-2026-04-28/fingerprint.json` — the baseline
  fingerprint.

**Baseline highlights:**
- `rfp.db`: 70 tables, 43,099 total rows, integrity_check = ok
- `rfp_requirements`: 13,295
- `rfp_requirement_diffs`: 11,498
- `document_chunks`: 7,257
- `rfp_questions`: 4,335
- `consolidated_requirements`: 2,670
- `ingested_documents`: 39
- `proposals`: 1, `users`: 1
- `uploads/`: 45 files, 223,619,766 bytes
- `chroma_db/chroma.sqlite3`: 196,608 bytes

**Source data touched:** none (read-only PRAGMA + SELECT only).

**Rollback:** `Remove-Item _fingerprint.py db_backups/pre-prod-hardening-2026-04-28/fingerprint.json`.
Again, no reason to.

**How to use it going forward:**
```powershell
py _fingerprint.py --out fp_after_<step>.json
# Compare against the baseline; investigate any unexpected drift.
```

---

## Step #1 — Token UX: extend lifetime + graceful expiry (2026-04-28) — DONE

**Verification gate result:** Fingerprint IDENTICAL to baseline. rfp.db
row totals 43,099 → 43,099. integrity_check ok → ok. uploads 45 files →
45 files.


**What changed:**
- `app/auth.py`: default `ACCESS_TOKEN_EXPIRE_MINUTES` 30 → 480 (8 h).
  Still env-overridable; existing `.env` files are unaffected if they set it.
- `.env.example`: documented new default.
- `frontend/rfp-ui/src/auth/AuthContext.js`:
  - On 401 from `/api/auth/me` or `/api/auth/token`, capture
    `window.location.pathname + search + hash` to
    `localStorage.rfp_post_login_redirect` (only if it isn't already the
    login screen) before clearing the token.
  - Set a `sessionExpired` flag exposed via context.
- `frontend/rfp-ui/src/App.js`:
  - Global MUI `Snackbar` driven by `sessionExpired` — shows
    "Your session expired — please sign in again." once after a 401.
  - In `AuthenticatedApp`, on mount, navigate to the saved redirect and
    clear it.

**Source data touched:** NONE. Pure code change. No DB schema, no migration,
no row touches. Only `localStorage` keys: `rfp_post_login_redirect` (new).
JWT signing key is unchanged → existing tokens remain valid; we are only
extending newly-issued tokens.

**Backups taken before:** `db_backups/pre-prod-hardening-2026-04-28/`
(baseline). No new backup needed for this step.

**Rollback procedure:**
1. `git diff` shows the three files. `git checkout -- app/auth.py
   frontend/rfp-ui/src/auth/AuthContext.js frontend/rfp-ui/src/App.js
   .env.example`
2. Restart backend (`py -m uvicorn app.main:app --port 8000 --host 127.0.0.1`).
3. Frontend hot-reloads.
4. Existing 8-hour tokens in users' browsers continue to be valid until
   their natural expiry. No revocation needed.

**Rollback verification:** Run `py _fingerprint.py --out /tmp/fp.json` and
diff against baseline → expect *zero* diff (no data touched). Smoke test:
log in, navigate, refresh — still logged in.

---

## Step #2 — Secrets & config hygiene (2026-04-28) — DONE

**Verification gate result:** Fingerprint IDENTICAL to baseline. 43,099 →
43,099 rows. integrity_check ok → ok. Validator unit-tested with 5
scenarios (missing key, placeholder key, short key, DEBUG=true, CORS=*) —
all behave as expected.


**What changed:**
- New file `app/config.py`: centralized config loader. Validates SECRET_KEY
  (rejects placeholder), warns on `DEBUG=true`, emits a redacted summary at
  startup so misconfiguration is loud.
- `app/main.py`: imports + calls `validate_runtime_config()` early during
  startup (after `load_dotenv`, before any router handles a request).
- `.env.example`: expanded — documents every supported variable, generation
  commands, recommended values for dev / pilot / prod.
- `.vscode/settings.json` (NEW): `python.terminal.useEnvFile=true` so VS
  Code injects `.env` into integrated terminals (was emitting a warning).

**Source data touched:** NONE. No DB, no schema, no migration. Pure
configuration plumbing. The user's existing `.env` is *not* edited.

**Backups taken before:** `db_backups/pre-prod-hardening-2026-04-28/`
(baseline still valid; nothing data-touching here).

**Rollback procedure:**
1. `git rm app/config.py .vscode/settings.json` (or `git checkout -- ...`
   if already tracked).
2. `git checkout -- app/main.py .env.example`.
3. Restart backend.

**Rollback verification:** Fingerprint diff vs baseline should be IDENTICAL.

---

## Step #4 — Externalize data dirs (2026-04-28) — DONE

**Verification gate result:** Fingerprint IDENTICAL. uploads files
45 → 45, chroma 196,608 → 196,608 bytes, rfp.db 43,099 rows. Default
resolved paths confirmed byte-identical to legacy hardcoded paths
(`C:\Users\p002298J\Everest\{uploads,chroma_db,logs,logs\.qdraft}`).


**What changed:**
- New `app/paths.py`: helper that resolves `uploads_dir()`,
  `chroma_dir()`, `logs_dir()`, `qdraft_dir()`. Reads env vars
  `EVEREST_UPLOADS_DIR`, `EVEREST_CHROMA_DIR`, `EVEREST_LOGS_DIR`.
  Defaults preserve today's exact paths (`<workspace>/uploads`,
  `<workspace>/chroma_db`, `<workspace>/logs`).
- `app/routers/ingestion.py`: replaces hardcoded `UPLOAD_DIR` with
  `paths.uploads_dir()`.
- `app/routers/parsons_knowledge.py`: same.
- `app/services/embeddings.py`: replaces `path="./chroma_db"` with
  `paths.chroma_dir()`.
- `app/routers/logs.py`: replaces `_LOGS_DIR` constant.
- `app/services/question_drafter.py`: replaces hardcoded
  `parents[2]/"logs"/".qdraft"`.
- `.env.example`: documents the three new env vars (commented out).

**Source data touched:** NONE. The default-resolved paths are byte-for-byte
identical to today's hardcoded paths. Existing `uploads/` and `chroma_db/`
files are untouched. No DB writes, no schema changes.

**Backups taken before:** `db_backups/pre-prod-hardening-2026-04-28/`.

**Rollback procedure:**
1. `git checkout -- app/routers/ingestion.py app/routers/parsons_knowledge.py
   app/services/embeddings.py app/routers/logs.py
   app/services/question_drafter.py .env.example`.
2. `git rm app/paths.py`.
3. Restart backend.

**Rollback verification:** Fingerprint identical to baseline. Smoke test:
upload a small file → it lands in `uploads/` (same as before).

---

## Step #14 — Health / readiness endpoints (2026-04-28) — DONE

**Verification gate result:** Fingerprint IDENTICAL. New routes
`GET /healthz` (always 200) and `GET /readyz` (200 when DB+chroma
reachable, 503 otherwise) ready for load-balancer probes.

**What changed:**
- `app/main.py`: adds two unauthenticated endpoints:
  - `GET /healthz` → always `{"status":"ok"}`. Liveness probe.
  - `GET /readyz` → checks DB connectivity (`SELECT 1`) and chroma
    directory existence; returns 200 with details when ready, 503 with
    failure reasons otherwise.

**Source data touched:** NONE. `/readyz` runs a single `SELECT 1`. No
writes, no schema changes.

**Backups taken before:** `db_backups/pre-prod-hardening-2026-04-28/`.

**Rollback procedure:** `git checkout -- app/main.py`. Restart backend.

**Rollback verification:** Fingerprint identical.

---

## Step #7 — CORS + security headers (2026-04-28) — DONE

**Verification gate result:** Fingerprint IDENTICAL. Middleware
unit-tested with TestClient: plain HTTP gets X-Content-Type-Options /
X-Frame-Options / Referrer-Policy / Permissions-Policy; HSTS only
appears when X-Forwarded-Proto=https. CORS now strips '*' with a
logged warning and falls back to localhost defaults if empty.

**What changed:**
- `app/main.py`:
  - CORS: if `CORS_ORIGINS` is unset OR contains a literal `*`, drop the
    `*` and emit a warning. Empty list short-circuits to localhost dev
    defaults so existing dev workflows are unaffected.
  - New `SecurityHeadersMiddleware` adds: `X-Content-Type-Options=nosniff`,
    `X-Frame-Options=DENY`, `Referrer-Policy=strict-origin-when-cross-origin`,
    `Permissions-Policy=camera=(), microphone=(), geolocation=()`,
    and `Strict-Transport-Security=max-age=31536000; includeSubDomains`
    only when the request was forwarded as HTTPS (X-Forwarded-Proto=https).

**Source data touched:** NONE. Pure response-header middleware. No DB,
no file writes, no schema. CORS allow-list is a runtime check, not stored.

**Backups taken before:** `db_backups/pre-prod-hardening-2026-04-28/`.

**Rollback procedure:**
1. `git checkout -- app/main.py`
2. Restart backend.

**Rollback verification:** Fingerprint identical. Smoke test: log in,
open Documents page → all API calls succeed.

---

## Step #2b — Startup model<->DB drift guard (2026-04-28) — DONE

**Verification gate result:** Fingerprint IDENTICAL. Tested live against
the running DB: 70 tables walked, 0 drift issues found, INFO log line
"[config] model<->DB schema check OK (no drift)" emitted. Backend
restart required to start emitting the check at startup.


**What changed:**
- New function `app.config.check_model_db_drift(engine, Base)`: read-only.
  Walks `Base.metadata.tables`, inspects the live DB via SQLAlchemy
  `inspect()`, and logs WARNING for:
  - Any column declared on the SA model that doesn't exist in the DB.
  - Any column in the DB that's not declared on the SA model (THE bug
    class that caused the IngestedDocument.created_at 500).
- `app/main.py`: calls it once after `Base.metadata.create_all(...)`.

**Source data touched:** NONE. Pure introspection (`PRAGMA table_info` /
`information_schema`). No writes.

**Backups taken before:** `db_backups/pre-prod-hardening-2026-04-28/`.

**Rollback procedure:** `git checkout -- app/config.py app/main.py`. Restart.

**Rollback verification:** Fingerprint identical.

---

## Step #12 — Background job runner for long extractions (2026-04-28) — DONE

**Verification gate result:** Fingerprint IDENTICAL. Registry self-tested
end-to-end: submit/run/done path, exception path (ValueError captured as
`error: "ValueError: boom"`), list-by-kind, list-by-doc_id, GC of
unknown ids — all assertions passed. Backend restart picks up new
routes (`POST /api/knowledge/extract-requirements/{id}?async=true`,
`GET /api/jobs/{id}`, `GET /api/jobs`).


**What changed:**
- New module `app/services/jobs.py`: in-memory job registry. Each job has
  `id, kind, status, submitted_at, started_at, finished_at, result,
  error, meta`. Threadsafe via `asyncio.Lock` + threading.Lock.
- New router `app/routers/jobs.py`: `GET /api/jobs/{id}` and
  `GET /api/jobs?kind=&doc_id=&limit=`.
- `app/routers/knowledge.py::extract_requirements`: accepts optional
  `?async_=true` query (camelCase `async` is reserved). Default remains
  SYNCHRONOUS — this is purely additive so existing callers (frontend,
  scripts) keep their current behavior.
  - When `async_=true`: spawns a daemon thread, returns 202 with
    `{job_id, kind, status, doc_id}`. Thread runs the same
    `extract_rfp_requirements` + coverage code, writes result/error
    into the job entry.
- `app/main.py`: registers the new `/api/jobs` router.

**Source data touched:** NONE in the routing/registry layer. The async
thread runs the EXACT same `extract_rfp_requirements` code path that
already runs synchronously today — same SQLAlchemy session pattern, same
inserts, same coverage rerun. No new DB writes are introduced; the
async wrapper is purely a control-plane change.

In-memory job state is volatile (lost on backend restart). Phase-3
upgrade to a `jobs` table / Celery is tracked separately.

**Backups taken before:** `db_backups/pre-prod-hardening-2026-04-28/`.

**Rollback procedure:**
1. `git rm app/services/jobs.py app/routers/jobs.py`
2. `git checkout -- app/main.py app/routers/knowledge.py`
3. Restart backend.

**Rollback verification:** Fingerprint identical. Smoke: re-run
`extract-requirements/{id}` synchronously — same result as before.

---

## Step #15 — Structured logging + request-id correlation (2026-04-28) — DONE

**Verification gate result:** Fingerprint IDENTICAL. ContextVar
propagation tested end-to-end: outside-request rid="-", inside-request
rid="abc123def456" on every log line, after-reset rid="-" again — all
three cases observed in test output. Backend restart picks up the
middleware and new log format.


**Existing state:** `app/logging_config.py` already does JSON formatting
to `logs/rfp_platform.log`, RotatingFileHandler 10MB × 5. Pretty good!

**What changed:**
- `app/logging_config.py`:
  - Default `log_file` now resolved via `app.paths.logs_dir()` so it
    honors `EVEREST_LOGS_DIR` env var (consistency with todo #4).
  - JSON formatter learns `request_id` field (defaults to "-" when not in
    a request context).
- `app/main.py`: new `RequestIdMiddleware` assigns a uuid4-hex-12 id to
  every request, exposes it as `X-Request-Id` response header, and stores
  it in a `contextvars.ContextVar` so any logger.info() call within the
  request automatically picks it up.

**Source data touched:** NONE. Pure observability.

**Backups taken before:** `db_backups/pre-prod-hardening-2026-04-28/`.

**Rollback procedure:** `git checkout -- app/logging_config.py app/main.py`.

**Rollback verification:** Fingerprint identical.

---

## Step #10 — Per-user rate limits on LLM endpoints (2026-04-28) — DONE

**Verification gate result:** Fingerprint IDENTICAL. End-to-end tested
via TestClient: a `proposal_manager` with cap=2 gets the first 2
requests through and is 429'd on the 3rd, with `Retry-After: 1800`
header surfaced (had to fix `app/error_handlers.py::http_exception_handler`
to forward `exc.headers` — previously dropped). An `admin` user can
fire 5 requests in a row without any 429. Defaults: 30/h
proposal_manager, 10/h vendor/evaluator, unlimited admin.

**Bonus fix:** [app/error_handlers.py](app/error_handlers.py#L83) now
preserves `exc.headers` on every HTTPException response — also fixes a
latent bug where `WWW-Authenticate` was getting stripped from 401s
issued by `OAuth2PasswordRequestForm` paths.


**What changed:**
- New module `app/services/rate_limit.py`: in-memory token-bucket
  rate limiter keyed on `user.id + bucket_name`. Threadsafe via
  `threading.Lock`. Defaults configurable via env
  `EVEREST_RATE_LIMIT_LLM_PER_HOUR` (per-role overridable).
- New FastAPI dependency `enforce_rate_limit(bucket="llm")` that 429s
  with a `Retry-After` header and a JSON body
  `{detail, bucket, limit, used, retry_after_seconds}`.
- Applied to 7 LLM-heavy endpoints:
  - `app/routers/knowledge.py`: `POST /extract-requirements/{id}`,
    `POST /agent/task`.
  - `app/routers/parsons_knowledge.py`: `POST /assess-coverage/{id}`,
    `POST /assess-coverage-proposal/{id}`.
  - `app/routers/parsons_response.py`: `POST /proposals/{id}/coverage/assess`,
    `POST /proposals/{id}/ask`.
  - `app/routers/questions.py`: `POST /ask`.
- Admins bypass (limit = unlimited).

**Source data touched:** NONE. Pure in-memory accounting; nothing
written to the DB.

**Backups taken before:** `db_backups/pre-prod-hardening-2026-04-28/`.

**Rollback procedure:**
1. `git rm app/services/rate_limit.py`
2. `git checkout -- app/routers/knowledge.py
   app/routers/parsons_knowledge.py app/routers/parsons_response.py
   app/routers/questions.py`
3. Restart backend.

**Rollback verification:** Fingerprint identical. Smoke: hit any of
the affected endpoints — same response shape as before.

---

## Step #16 — Error reporting (Sentry, optional) (2026-04-28) — DONE

**Verification gate result:** Fingerprint IDENTICAL. SDK confirmed
no-op without `SENTRY_DSN` (returns `False`). With a (fake) DSN the
init logs the right env / traces / pii values and `capture_exception`
runs cleanly. Backend restart required after setting `SENTRY_DSN`.


**What changed:**
- New module `app/observability.py`: thin wrapper around `sentry_sdk.init`.
  No-op if `SENTRY_DSN` env var is absent (default).
- `app/main.py`: calls `init_sentry()` at startup AFTER load_dotenv but
  BEFORE app construction (per Sentry docs).
- `requirements.txt`: adds `sentry-sdk[fastapi]`.
- `.env.example`: documents the new env vars.
- `app/error_handlers.py::generic_exception_handler`: now also calls
  `sentry_sdk.capture_exception(exc)` so unhandled errors land in Sentry
  even though the central handler swallows them into a 500 JSON.

**Defaults (privacy-respecting):**
- `send_default_pii=False` — Sentry SDK won't auto-attach IPs / cookies /
  request bodies.
- `traces_sample_rate=0.0` — performance tracing OFF unless explicitly
  set via `SENTRY_TRACES_SAMPLE_RATE`. Free Sentry tier doesn't include
  much volume, and we don't want to surprise the user with a bill.
- `environment` defaults to "development". Set
  `SENTRY_ENVIRONMENT=production` (or "pilot") in the deploy.

**Source data touched:** NONE. SDK is a no-op without DSN; nothing
written to DB, no schema changes. Existing logs / responses unchanged.

**Backups taken before:** `db_backups/pre-prod-hardening-2026-04-28/`.

**Rollback procedure:**
1. `git rm app/observability.py`
2. `git checkout -- app/main.py app/error_handlers.py
   requirements.txt .env.example`
3. `pip uninstall sentry-sdk` (optional)
4. Restart backend.

**Rollback verification:** Fingerprint identical.

---

## Step #19 — Smoke-test suite (2026-04-28) — DONE

**Verification gate result:** Fingerprint IDENTICAL after a full test
run. **15 tests passing** in 22s. Coverage:
healthz/readyz, security headers (HTTP + HSTS-on-HTTPS), request-id
echo + passthrough, login + /me roundtrip, bad-password 401, protected
endpoint requires auth, rate-limiter trips at cap=1 for vendor (with
Retry-After header), admin bypasses rate-limit, /api/jobs lifecycle
(404 unknown, list envelope), config validator rejects placeholder /
accepts real key. Tests use a fresh temp SQLite DB; the real `rfp.db`
is never opened. Run with `py -m pytest`.


**What changed:**
- New `tests/` package: `tests/__init__.py`, `tests/conftest.py`,
  `tests/test_smoke.py`.
- `conftest.py` builds a FRESH SQLite DB at a temp path PER TEST RUN
  (never touches `rfp.db`), overrides `get_db` to use it, and provides
  fixtures: `client`, `admin_token`, `admin_headers`.
- Tests cover: health, readiness, security headers, request-id
  correlation, register/login/me flow, 401 on unauth endpoint, 429
  trip on the rate limiter, /api/jobs lifecycle (submit→poll), config
  validator behavior.
- New `pytest.ini` with the right env vars (SECRET_KEY, DATABASE_URL,
  EVEREST_RATE_LIMIT_LLM_PER_HOUR=2 for testability).
- Added `pytest` to requirements.txt.

**Source data touched:** NONE. Tests run against a brand-new sqlite file
in `%TEMP%`, blown away after the run. Real `rfp.db` is untouched.

**Backups taken before:** `db_backups/pre-prod-hardening-2026-04-28/`.

**Rollback procedure:** `git rm -r tests/ pytest.ini`.

**Rollback verification:** Fingerprint identical (tests don't read live DB).

---

## Step #9 — Audit log (2026-04-28) — DONE

**Verification gate result:** Fingerprint of the on-disk DB is byte-for-byte
IDENTICAL to baseline (the new `audit_log` table is created at next
backend startup via `Base.metadata.create_all`; until then the DB file
has not been touched). All pre-existing 70 tables and their sample-row
hashes unchanged. `audit_log` table will appear with 0 rows on first
restart and grow from there. **18 of 18 tests pass** including the new
audit-specific tests (login_success / login_failure / non-admin
forbidden). Backend restart will create the table.


**What changed:**
- New `AuditLogEntry` SA model (`app/models.py`) → table `audit_log`.
  Columns: `id, ts (default utcnow, indexed), user_id, username,
  action, target_type, target_id, ip, request_id, status, payload`
  (JSON text). Indexed on `(ts, user_id, action)`.
- `Base.metadata.create_all` (already in main.py) creates the new table
  on next start. Additive — no other tables touched.
- New helper `app/services/audit.py::write(...)`. Best-effort: failures
  to write the audit row are logged at WARNING but never raised — never
  block a real action just because audit log writing failed.
- `app/routers/auth.py`: writes audit entries for:
  - `login_success` / `login_failure` (token endpoint)
  - `register_user`, `update_user`, `activate_user`, `admin_reset_password`,
    `change_password`
- New router `app/routers/audit.py`: `GET /api/audit` (admin-only),
  filterable on user_id / action / target_type / since.

**Source data touched:** Adds ONE new table (`audit_log`). Does NOT
modify any existing rows or tables. Existing data is untouched.

**Pre-step backup:**
`db_backups/pre-prod-hardening-2026-04-28/rfp.db.before_audit_log`
(WAL-checkpointed snapshot taken immediately before this step).

**Rollback procedure:**
1. Stop backend.
2. `git checkout -- app/models.py app/main.py app/routers/auth.py`
3. `git rm app/services/audit.py app/routers/audit.py`
4. (Optional) Drop the `audit_log` table from the DB:
   `py -c "import sqlite3; sqlite3.connect('rfp.db').execute('DROP TABLE IF EXISTS audit_log').connection.commit()"`
   — only do this if you actually want to remove it; leaving it
   in place is harmless and cheaper to revert.
5. Restart backend.

**Rollback verification:** Fingerprint diff allowed in EXACTLY ONE
place: a new table `audit_log` appearing, with row count >= 0. All
pre-existing tables unchanged.

---

## Step #11 — LLM cost / usage dashboard (2026-04-28) — DONE

**Verification gate result:** **21/21 tests pass**, including 3 new
LLM-usage tests (summary envelope, recorder writes a row with cost,
unknown model records null cost). Live DB unchanged (the new
`llm_usage` table is created at next backend startup; on-disk file is
byte-identical to baseline). Pre-existing tables verified unchanged
(integrity ok, 70 tables + audit_log = 71). Confirmed `audit_log`
correctly captured the post-restart sign-in (1 row, request_id
correlated to the log).


**What changed:**
- New SA model `LlmUsage` (table `llm_usage`). Columns: `id, ts, user_id,
  username, request_id, endpoint, provider, model, input_tokens,
  output_tokens, total_tokens, cost_estimate_usd, latency_ms, status,
  error`. Indexed on `(ts, user_id, model)`.
- New helper `app/services/llm_usage.py`:
  - `record(...)` — best-effort insert; never raises.
  - `endpoint_ctx` ContextVar so router code can label calls
    (`with set_endpoint("/api/questions/ask"): ...`).
  - Static cost-estimate table for the few models we use (Opus/Sonnet/
    Haiku 4.x, gpt-4o, gpt-4o-mini, text-embedding-3-*). Unknown
    models record cost=0, NOT an error.
- `app/services/orchestrator_agent.py::_call_ai`: records usage on
  success and on terminal failure for both Anthropic and OpenAI paths
  (input_tokens / output_tokens pulled from the response object).
- `app/services/competitor_analyst.py::_call_ai`: same wiring.
- `app/routers/prompts.py::_call_claude_refiner` and
  `_call_openai_refiner`: same wiring.
- New router `app/routers/llm_usage.py`:
  - `GET /api/llm-usage` — paginated raw rows (admin)
  - `GET /api/llm-usage/summary` — aggregated by user/model/day (admin)
- `app/services/audit.py` left untouched.

**Source data touched:** Adds ONE new table (`llm_usage`). No existing
tables / rows modified. New writes happen only at LLM-call time; the
DB file is unchanged at startup until the first LLM call after restart.

**Pre-step backup:**
`db_backups/pre-prod-hardening-2026-04-28/rfp.db.before_llm_usage`.

**Rollback procedure:**
1. Stop backend.
2. `git checkout -- app/models.py app/main.py
   app/services/orchestrator_agent.py app/services/competitor_analyst.py
   app/routers/prompts.py`
3. `git rm app/services/llm_usage.py app/routers/llm_usage.py`
4. (Optional) `DROP TABLE llm_usage;` — leaving it is harmless.
5. Restart.

**Rollback verification:** Fingerprint diff allowed in EXACTLY ONE place:
new table `llm_usage` (with N rows from any LLM calls made between this
step and rollback). All pre-existing tables unchanged.

---

## Step #13 — Automated DB backups + restore drill (2026-04-28) — DONE

**Verification gate result:** Live DB integrity ok, 71 tables (no
schema change). PowerShell scripts parse cleanly. Drill executed end-to-end
against the existing baseline backup (`db_backups/pre-prod-hardening-2026-04-28/rfp.db`):
the restored DB passed `PRAGMA integrity_check`, and the diff vs live
correctly shows ONLY `audit_log` as new (consistent with step #9).

**Note for the user:** The Windows scheduled task is NOT installed on
this machine — `Get-ScheduledTask` confirmed no `EverestRFP-DBBackup`.
Run `install_backup_task.ps1` as Administrator once to enable nightly
backups. See [docs/BACKUPS.md](BACKUPS.md).


**Existing state:** `backup_db.ps1` exists (uses sqlite3 backup API,
30-day retention, prunes old). `install_backup_task.ps1` exists but the
task was never registered (verified — no EverestRFP-DBBackup task
present on this machine).

**What changed:**
- `backup_db.ps1` extended to also back up `chroma_db/` (small,
  bundled with each daily run) and `uploads/` (gated behind a
  `-IncludeUploads` switch — defaults to weekly to avoid 213 MB nightly
  copies). Backups go to `<BackupDest>/rfp_<timestamp>.db` /
  `chroma_<timestamp>.zip` / `uploads_<timestamp>.zip`.
- New `restore_db.ps1` — explicit restore script with `-DryRun` (default)
  and `-Apply` flags. Lists exactly what would be replaced; will not
  touch live files unless `-Apply` is passed.
- New `_run_restore_drill.py` — pure-read drill: takes the most recent
  backup, restores it into a temp dir, runs `_fingerprint.py` on it,
  prints a row-count diff vs the live `rfp.db`. Never touches live.
  This is the verification that backups *actually work*.
- New `docs/BACKUPS.md` — short runbook (when, where, how to restore).

**Source data touched:** NONE. The improved `backup_db.ps1` is a strict
superset of the existing one and only writes to `<BackupDest>`. The
restore script defaults to dry-run. The drill script is read-only.

**Backups taken before:** N/A (read-only / write-only-to-backups).

**Rollback procedure:**
1. `git checkout -- backup_db.ps1`
2. `git rm restore_db.ps1 _run_restore_drill.py docs/BACKUPS.md`

**Rollback verification:** Fingerprint identical (no schema or data
changes).

---

## Step #20 — Deployment runbook + env matrix (2026-04-28) — DONE

**Verification:** Pure docs. Cross-checked against the actual
`.env.example`, `app/config.py`, and the per-step entries above.


**What changed:**
- New `docs/DEPLOYMENT.md` — single source of truth for deploying
  Everest. Covers local dev, single-VM pilot, and the path to a
  managed host. Includes: required prerequisites, env-var matrix
  (dev / pilot / prod), one-shot bring-up commands, smoke-test
  checklist, log/diagnose pointers, on-call cheatsheet, and a
  cross-reference index of every other doc / script in the repo.

**Source data touched:** NONE. Pure documentation.

**Backups taken before:** N/A.

**Rollback procedure:** `git rm docs/DEPLOYMENT.md`.

**Rollback verification:** N/A.

---

## Step #8 — Refresh tokens + server-side revocation (2026-04-28) — DONE

**Verification gate result:** **26/26 tests pass** including 5 new
refresh-flow tests (token rotation, old-token revoked after rotation,
**replay-detection chain takedown**, single-token logout, logout-all
across multiple sessions). Live DB integrity ok, 71 tables, no schema
change yet (refresh_tokens table will be created on next restart).
audit_log = 1 row (unchanged from earlier).


**What changed:**
- New SA model `RefreshToken` (table `refresh_tokens`). Columns:
  `id, token_hash (sha256, indexed unique), user_id, issued_at,
  expires_at, revoked_at, last_used_at, parent_id, client_ip, user_agent`.
- New helper `app/services/refresh_tokens.py`:
  - `mint(db, user, request)` — generates a 256-bit secret, stores its
    sha256 hash, returns the plaintext to the caller (only place it's
    ever exposed).
  - `verify_and_rotate(db, plaintext, request)` — checks existence /
    expiry / revocation, marks the old row revoked, mints a new one,
    returns (new_plaintext, user). Implements **token rotation**:
    using a refresh token consumes it. If a stolen token is replayed
    after the legitimate user already rotated, we detect the reuse and
    revoke the *entire* token chain.
  - `revoke(db, plaintext)` — single logout.
  - `revoke_all_for_user(db, user_id)` — admin / "log out everywhere".
  - `gc_expired(db)` — sweep job.
- `app/routers/auth.py`:
  - `POST /api/auth/token` — now also returns `refresh_token` and
    `refresh_token_expires_in_minutes` alongside the existing fields.
    Backward compatible: existing frontend ignores the new fields.
  - `POST /api/auth/refresh` — body `{refresh_token}`, returns a new
    `{access_token, refresh_token}` pair. Old refresh token is revoked.
  - `POST /api/auth/logout` — revokes the supplied refresh token.
  - `POST /api/auth/logout-all` — revokes EVERY refresh token for the
    current user.
  - All four wired into the audit log with action codes `refresh`,
    `refresh_replay_detected`, `logout`, `logout_all`.

**Source data touched:** Adds ONE new table (`refresh_tokens`). No
existing tables / rows modified. `users` table unchanged. JWT signing
key unchanged → **all currently-issued access tokens stay valid until
their natural expiry**.

**Default behavior unchanged:**
- Access token lifetime stays 8 h (`ACCESS_TOKEN_EXPIRE_MINUTES=480`).
- Refresh token lifetime defaults to 7 days
  (`REFRESH_TOKEN_EXPIRE_DAYS=7`).
- The frontend hasn't been updated to USE refresh yet — it just
  ignores the new fields. That follow-up will land separately so we
  can prove the backend works first.

**Pre-step backup:**
`db_backups/pre-prod-hardening-2026-04-28/rfp.db.before_refresh_tokens`.

**Rollback procedure:**
1. Stop backend.
2. `git checkout -- app/models.py app/routers/auth.py`
3. `git rm app/services/refresh_tokens.py`
4. (Optional) `DROP TABLE refresh_tokens;` — leaving it is harmless.
5. Restart.

**Rollback verification:** Fingerprint diff allowed in EXACTLY ONE
place: new table `refresh_tokens` with 0 rows pre-restart, growing
afterwards. All pre-existing tables unchanged.

---

## Step #17 — User onboarding (invitations, optional SMTP) (2026-04-28) — DONE

**Verification gate result:** **31/31 tests pass** including 5 new
invitation tests (admin creates → returns URL + email_body + smtp_configured
flag, redeem signs user in with new password, short password rejected,
one-shot replay protection, non-admin gets 401/403). Live DB integrity
ok, 71 tables (no schema change yet — `user_invitations`, `refresh_tokens`,
and `llm_usage` all create on next restart together). Pre-existing
`audit_log = 1 row` unchanged.


**What changed:**
- New SA model `UserInvitation` (table `user_invitations`):
  `id, token_hash (sha256, unique, indexed), user_id, email, invited_by,
  created_at, expires_at, consumed_at, consumed_ip`. Default lifetime
  7 days, env-overridable via `INVITE_EXPIRE_DAYS`.
- New helper `app/services/invitations.py`:
  - `mint(db, user, invited_by, request)` → returns plaintext token
    (only place it's exposed) + the row.
  - `lookup(db, plaintext)`, `consume(db, plaintext, new_password, request)`
    — atomic: validates, sets the password, clears `must_change_password`,
    marks consumed, returns the user.
  - `gc_expired(db)` — sweep.
- New helper `app/services/smtp_mailer.py`:
  - `is_configured()` — True iff `SMTP_HOST` is set.
  - `send_invite(user_email, invite_url, inviter_name)` — sends a
    plaintext email through Parsons SMTP if configured. Logs and
    returns False on failure; never raises (admin still gets the URL
    in the API response so they can hand-deliver).
- `app/routers/auth.py`:
  - `POST /api/auth/users/{user_id}/invite` (admin) — mints a token,
    optionally sends email, ALWAYS returns
    `{token, redeem_url, expires_at, email_sent: bool, email_body}`.
    Admin can copy/paste if SMTP isn't wired up.
  - `POST /api/auth/redeem-invite` (PUBLIC) — body
    `{token, new_password}`, validates+consumes, returns a logged-in
    `{access_token, refresh_token, ...}` pair.
  - Audit-log entries: `invite_created`, `invite_redeemed`,
    `invite_redeem_failed`.
- `.env.example`: documents `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`,
  `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_USE_TLS`, `EVEREST_PUBLIC_URL`,
  `INVITE_EXPIRE_DAYS`.

**Source data touched:** Adds ONE new table (`user_invitations`). No
existing tables / rows modified. `users` table unchanged. Admin's
existing `POST /api/auth/register` and `POST /users/{id}/reset-password`
endpoints continue to work unchanged — invitations are an additive
parallel path.

**Pre-step backup:**
`db_backups/pre-prod-hardening-2026-04-28/rfp.db.before_invitations`.

**Rollback procedure:**
1. Stop backend.
2. `git checkout -- app/models.py app/routers/auth.py .env.example`
3. `git rm app/services/invitations.py app/services/smtp_mailer.py`
4. (Optional) `DROP TABLE user_invitations;` — leaving it is harmless.
5. Restart.

**Rollback verification:** Fingerprint diff allowed in EXACTLY ONE
place: new table `user_invitations`. Pre-existing tables unchanged.

---

## Step #200 — Frontend follow-ups: refresh + invite (2026-04-28) — DONE

**Verification gate result:** Frontend hot-reloads. Backend unchanged.
31/31 backend tests still pass (regression check). The new frontend
flow exercises the existing backend endpoints from #8 (refresh / logout)
and #17 (invite create / redeem); no new server contracts.


**What changed:**
- `frontend/rfp-ui/src/auth/AuthContext.js`:
  - Stores `refresh_token` from `/api/auth/token` response.
  - On any 401, **first** tries `/api/auth/refresh` once. If that
    succeeds, retries the original request transparently. Only kicks
    the user out if refresh fails too.
  - On explicit `logout()`, calls `/api/auth/logout` server-side so
    the refresh token is revoked.
  - One `refreshInFlight` promise prevents the thundering-herd of
    parallel 401s all firing simultaneous refresh calls.
- `frontend/rfp-ui/src/pages/Settings.js`:
  - New "Invite" action in the Users table (admin-only). Calls
    `POST /api/auth/users/{id}/invite` and renders a dialog with
    the redeem URL + "Copy URL" + "Copy email body" buttons.
  - Toast says whether SMTP actually sent the email.

**Source data touched:** NONE. Frontend only. The new server endpoints
already shipped in steps #8 and #17.

**Backups taken before:** N/A.

**Rollback procedure:**
1. `git checkout -- frontend/rfp-ui/src/auth/AuthContext.js
   frontend/rfp-ui/src/pages/Settings.js`

**Rollback verification:** Frontend hot-reloads. Backend unchanged.

---

## Step #3 — SQLite -> Postgres tooling (2026-04-28) — DONE (tooling only; cutover deferred)

**Verification gate result:** End-to-end self-test of the migrator
against the live 43,099-row SQLite as the source and a fresh empty
target — **ALL 71 TABLES MATCH** (row count + sample-row hash on every
single one). The largest tables verified: `rfp_requirements: 13,295`,
`rfp_requirement_diffs: 11,498`, `document_chunks: 7,257`,
`rfp_questions: 4,335`, `consolidated_requirements: 2,670`,
`extracted_facts: 1,487`, `pricing_coverage_suggestions: 1,484`. Live
`rfp.db` integrity ok, 71 tables, 1 audit_log row — byte-identical to
before. SQLite is read-only on the source side.

**What is NOT done yet (deliberate):** the actual cutover to Postgres.
Follow `docs/POSTGRES_MIGRATION.md` whenever you're ready —
self-contained, fully-rollback-safe procedure.


**What this step ships (code only — does NOT run the migration):**
- New `docker-compose.postgres.yml` — runs Postgres 16 only (separate
  from the existing `docker-compose.yml` which spawns the app too).
  Named volume `everest_pgdata`, port 5432, password from
  `POSTGRES_PASSWORD` env (defaults to a clearly-bogus value to force
  the operator to set one).
- New `_migrate_sqlite_to_postgres.py` — the migrator. **Defaults to
  dry-run.** Reads from SQLite, writes to an EMPTY Postgres DB
  (refuses to run if the target has any user tables already).
  Validates row counts and sample-row hashes per table after copy.
  Exits non-zero on any mismatch. The live SQLite file is **never**
  modified.
- New `docs/POSTGRES_MIGRATION.md` — step-by-step runbook.

**What this step does NOT do (deliberately):**
- Does NOT change `DATABASE_URL` in your `.env`.
- Does NOT start Postgres for you. You run `docker compose -f
  docker-compose.postgres.yml up -d` when you're ready.
- Does NOT copy data into a real DB until you run the script with
  `--apply` and a non-default target URL.
- Does NOT touch `rfp.db` ever.

**Source data touched:** NONE in this commit. The migrator, when later
invoked with `--apply`, READS sqlite (unchanged) and WRITES to a fresh
Postgres DB. SQLite stays in place as the rollback path.

**Rollback procedure:** `git rm` the four new files. No DB changes.

---

## Step #21+#5+#6+#300 — Local-prod stack (containerized + reverse proxy + TLS) (2026-04-28) — DONE

**Verification gate result:** Compose file structurally valid
(4 services + 3 named volumes; dependency chain
postgres → backend → frontend → proxy; all critical services
healthchecked). 31/31 backend tests still pass — no app code changed.
Live `rfp.db` unchanged (integrity ok, 71 tables, 1 audit_log row).
Local Docker isn't on PATH so I couldn't run `docker compose up`
end-to-end — that's the user's first verification step (see
docs/LOCAL_PROD.md "First-time bring-up").


**What changed (all NEW files; no changes to existing app code):**
- `Dockerfile.backend` — multi-stage Python 3.13-slim image for the
  FastAPI backend (replaces the existing `Dockerfile` which is left
  in place for backward compatibility with the legacy `docker-compose.yml`).
  Wires `EVEREST_DATA_DIR=/data` so uploads/chroma/logs land on a
  mounted volume.
- `frontend/rfp-ui/Dockerfile` — multi-stage build:
  Node 20 → `npm ci` + `npm run build` → nginx 1.27-alpine serving
  `/build` on port 80. Includes our `nginx.conf` snippet.
- `frontend/rfp-ui/nginx.conf` — serves the SPA with proper
  React-Router support (try_files → index.html), proxies `/api/*` to
  `backend:8000`, sets sensible client_max_body_size for uploads.
- `infra/proxy/Dockerfile` + `infra/proxy/nginx.conf` — the OUTER
  reverse proxy that does TLS termination + sets X-Forwarded-Proto so
  the FastAPI security middleware emits HSTS. Listens on 8443 (HTTPS)
  and 8080 (HTTP -> 8443 redirect). Self-signed cert generated on
  first boot via `infra/proxy/generate-cert.sh`.
- `docker-compose.local-prod.yml` — single-command bring-up of:
  Postgres + Backend + Frontend (nginx static) + Outer Proxy (nginx
  TLS). All services healthchecked. Named volumes for postgres data,
  app data (uploads/chroma/logs), and TLS cert.
- `.dockerignore` — keeps the build context small.
- `docs/LOCAL_PROD.md` — runbook: bring up, smoke test, tear down,
  troubleshoot.

**Source data touched:** NONE. The local-prod stack uses its OWN
Postgres in its OWN named volume. Nothing reads or writes the
existing `rfp.db` / `chroma_db/` / `uploads/` unless you explicitly
bind-mount them — and the runbook says don't.

**Backups taken before:** N/A (no live data touched).

**Rollback procedure:**
1. `docker compose -f docker-compose.local-prod.yml down -v` (purge
   the local-prod volumes)
2. `git rm -r Dockerfile.backend frontend/rfp-ui/Dockerfile
   frontend/rfp-ui/nginx.conf infra/ docker-compose.local-prod.yml
   .dockerignore docs/LOCAL_PROD.md`
3. Your dev workflow (`py -m uvicorn ...` + `npm start`) is
   untouched.

**Rollback verification:** Fingerprint of live `rfp.db` unchanged.
Existing dev tasks still work.

---

## Step #401 — Relative implementation deadlines (Start + Nd) (2026-04-28) — DONE

**Verification:** Backend lints clean, 31/31 tests still pass. Live DB
integrity ok, schedule events at 10 rows (down from 16 — user ran the
dedupe from step #400). New columns will appear on next backend
restart via the new `_ensure_columns` helper in `app/main.py`. Frontend
re-renders the Schedule page as two tabs (Solicitation / Implementation
Plan) with a dependency-free SVG Gantt that shows day-offsets when no
contract anchor is set, and switches to absolute dates the moment the
user enters one. Re-extraction on the RFP picks up plan deliverables as
`offset_days` rows; a click on the new "Save & Compute Dates" button
fires `PUT /api/schedule/proposal/{id}/contract-anchor` which sets
`proposals.contract_effective_date` and computes every relative event_date.


**Context:** the T1628 RFP table titled "ADDITIONAL PLANS" lists ~30
deliverables with deadlines expressed as days after Contract Effective
Date ("Start + 30d", "Start + 530d"). The existing schema has only an
absolute `event_date`, so these can't be stored faithfully. Result: the
AI either invents dates or fills in NULLs, and the user sees nothing on
the schedule.

**What changed (additive only — no existing rows touched):**
- New columns on `rfp_schedule_events` (added to the SA model so
  `Base.metadata.create_all` adds them on next restart, and a tiny
  `ensure_columns` helper backfills the live DB on startup):
  * `offset_days INTEGER NULL` — number of days from the anchor.
    Negative values are valid for "End - 360d" closeout deliverables.
  * `offset_anchor TEXT NULL` — `'contract_start'` | `'pop_end'`.
  * `is_draft_with_quote BOOLEAN DEFAULT 0` — flag for
    "Draft Plan Required with Quote Submission" column.
  * `section_refs TEXT NULL` — RFP section numbers ("4.2.1, 4.29.3 ...").
  * `event_date_resolved BOOLEAN DEFAULT 0` — whether the
    `event_date` was computed from the offset (1) or is the original
    parsed date (0).
- New column on `proposals`:
  * `contract_effective_date DATETIME NULL` — once known, the schedule
    page recomputes every relative deadline from this anchor.
  Backfilled via the same `ensure_columns` helper.
- Helper `app/services/schedule_extractor.py::_resolve_relative_dates(db, proposal_id)`:
  * Reads `proposals.contract_effective_date` (or
    `period_of_performance_start.event_date` as a fallback).
  * For every event with `offset_days IS NOT NULL` and
    `offset_anchor='contract_start'`, sets
    `event_date = anchor + offset_days days` and
    `event_date_resolved=1`.
  * Same for `offset_anchor='pop_end'` against
    `period_of_performance_end.event_date`.
  * Idempotent. Best-effort — never raises.
  Called automatically after extraction and after manual edits to
  `contract_effective_date`.
- Updated AI system prompt: instructs the model to populate
  `offset_days` / `offset_anchor` for plan deadlines expressed as
  "Start + Nd" / "End - Nd" instead of inventing absolute dates.
- New endpoint `PUT /api/schedule/proposal/{id}/contract-anchor` —
  admin/proposal_manager. Body `{contract_effective_date: ISO}`. Saves
  it and triggers `_resolve_relative_dates`.
- Schedule.js:
  * If the active proposal has no `contract_effective_date`, shows a
    small banner with a date-picker to set it. After save, all
    relative deadlines compute and appear in the table.
  * Each event row now shows the original "Start + 30d" expression in
    the expanded view, alongside the computed date.
  * `implementation_milestone` events render with a distinctive icon.
- Smoke tests added for the new endpoint and the resolver.

**Source data touched:** Adds 5 new columns to `rfp_schedule_events`
and 1 new column to `proposals` — all NULL/0 by default. Does NOT
modify any existing rows. The drift guard from #2b will see the new
columns and accept them on next restart.

**Pre-step backup:**
`db_backups/pre-prod-hardening-2026-04-28/rfp.db.before_relative_deadlines`.

**Rollback procedure:**
1. Stop backend.
2. `git checkout -- app/models.py app/services/schedule_extractor.py
   app/routers/schedule.py app/main.py
   frontend/rfp-ui/src/pages/Schedule.js tests/test_smoke.py`
3. (Optional) drop the new columns:
   `ALTER TABLE rfp_schedule_events DROP COLUMN offset_days;` ... etc.
   SQLite needs a table rebuild for column drops; safer to leave them.
4. Restart backend.

**Rollback verification:** Fingerprint diff should show ONLY new
columns on `rfp_schedule_events` and `proposals` — no row-count or
hash changes on existing tables/rows.

---

## Step #500/#501 — Flashcards (Phase A backend + Phase B UI) (2026-04-28) — DONE

**Verification:** 34/34 tests pass (3 new flashcard tests). Live DB
integrity ok, schedule untouched at 41 rows; flashcards table will be
created automatically on next backend restart by Base.metadata.create_all.
Frontend page wired into the app shell at `/flashcards` and `/p/:id/flashcards`.


**What changed:**
- New SA model `Flashcard` (table `flashcards`) — additive only.
- New service `app/services/flashcards.py` with the LLM generator
  (uses gpt-4o-mini by default for cost; falls back to Claude). Scoped
  to Section 4 (Statement of Work) requirements only — no bid-mechanics
  cards. Idempotent: skips reqs that already have a card.
- New router `app/routers/flashcards.py`:
  * `GET /api/flashcards?proposal_id=&section_prefix=&due_only=&limit=`
  * `POST /api/flashcards/generate` body
    `{proposal_id, section_prefix='4', max_requirements?}` — runs in
    background via the #12 job runner; returns 202 with job id.
  * `POST /api/flashcards/{id}/review` body
    `{rating}` — Again/Good/Easy → SM-2 ease/interval update.
  * `DELETE /api/flashcards/{id}`
- Wired into app/main.py.
- New ensure_columns block for `flashcards` (runs on next restart).
- 3 new pytest cases.

**Source data touched:** Adds ONE new table (`flashcards`). Does NOT
modify any existing rows. Card generation calls the LLM and writes new
rows; the generator scopes its READS to `rfp_requirements` for the
given proposal where `section_id LIKE '4%'`.

**Pre-step backup:**
`db_backups/pre-prod-hardening-2026-04-28/rfp.db.before_flashcards`.

**Rollback procedure:**
1. Stop backend.
2. `git checkout -- app/models.py app/main.py
   tests/test_smoke.py`
3. `git rm app/services/flashcards.py app/routers/flashcards.py`
4. (Optional) `DROP TABLE flashcards;`
5. Restart.

---

## Template for future entries

```
## Step #<N> — <title> (YYYY-MM-DD) — PLANNED|IN-PROGRESS|DONE|REVERTED

**What changed:** (files, env vars, schema)

**Source data touched:** (which tables / files / column adds; "none" if pure code change)

**Backups taken before:** (path)

**Rollback procedure:**
1. ...
2. ...

**Rollback verification:** (fingerprint diff expected / smoke test)
```
