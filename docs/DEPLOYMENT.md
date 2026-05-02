# Everest — Deployment Runbook

This is the **single source of truth** for running Everest in any
environment. If the docs and the code disagree, the code wins —
update this file.

> Last updated: 2026-04-28 (after Phase-1 + Phase-2 hardening).
> See [PROD_HARDENING_ROLLBACK.md](PROD_HARDENING_ROLLBACK.md) for the
> per-step change log and how to revert anything.

---

## 1. Environments

| Env | Where it runs | Who can reach it | Notes |
|---|---|---|---|
| **dev** | Your laptop (`127.0.0.1:8000` + `:3000`) | You only | Default. Hot-reload OK on the frontend; backend should be restarted manually. |
| **pilot** | Internal Parsons VM / Docker host | 5–20 trusted users on VPN | Postgres recommended (todo #3). HTTPS required (#6). |
| **prod** | Managed host (Azure App Service, Parsons-hosted Linux, etc.) | All Parsons | SSO (#18), centralized logs, monitored uptime. |

This runbook describes **dev** in detail and **pilot** at the
"happy-path" level. **Prod** is sketched at the end — finalize it when
you pick the host.

---

## 2. Prerequisites

| Tool | Version | Why |
|---|---|---|
| Python | 3.13.x (matches `py` launcher on Windows) | Backend runtime. **Do NOT use the `python` 3.12 binary on this machine** — uvicorn/fastapi live in `py`. |
| Node.js | 18.x or 20.x LTS | Frontend build (CRA 5.0.1). |
| Git | any recent | The portable copy at `mingit/cmd` is sufficient on Windows. |
| Docker Desktop (pilot+) | latest stable | Postgres + reverse proxy + container builds. |
| OneDrive or a network share | n/a | Backup destination. |

Optional but recommended:
- **VS Code** with the Python + Pylance extensions.
- **DBeaver** or **SQLite Browser** for spot-checking the DB.

---

## 3. Required environment variables

Source of truth: `.env.example`. Copy to `.env` and fill in.

### Always required

| Var | Example | Notes |
|---|---|---|
| `SECRET_KEY` | (64 hex chars) | Generate: `py -c "import secrets; print(secrets.token_hex(32))"`. Backend refuses to start if missing or set to the placeholder (see [`app/config.py`](../app/config.py)). |
| `DATABASE_URL` | `sqlite:///./rfp.db` | Postgres in pilot+: `postgresql+psycopg://rfp_user:<pw>@db:5432/rfp_db`. |
| `CORS_ORIGINS` | `http://localhost:3000,http://localhost:3001` | Comma-separated. **Never `*`** in shared environments — startup will warn. |
| `DEBUG` | `false` | When `true`, enables a `dev-bypass-token` that auto-logs in as the first admin. **Never enable in pilot or prod.** |

### LLM providers (at least one)

| Var | Example | Notes |
|---|---|---|
| `OPENAI_API_KEY` | `sk-...` | |
| `ANTHROPIC_API_KEY` | `sk-ant-...` | |
| `CLAUDE_REFINER_MODEL` | `claude-opus-4-7` | Default in [`orchestrator_agent.py`](../app/services/orchestrator_agent.py). |
| `OPENAI_REFINER_MODEL` | `gpt-4o` | |

### Auth / sessions

| Var | Default | Notes |
|---|---|---|
| `ALGORITHM` | `HS256` | JWT signing alg. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `480` (8h) | Refresh-token rotation pending in todo #8. |

### Data directories

| Var | Default | Notes |
|---|---|---|
| `EVEREST_DATA_DIR` | unset | When set, `uploads/`, `chroma_db/`, `logs/` all default to `<EVEREST_DATA_DIR>/...`. Use this in containers / VMs. |
| `EVEREST_UPLOADS_DIR` | `<workspace>/uploads` | Individual override. |
| `EVEREST_CHROMA_DIR` | `<workspace>/chroma_db` | Re-embedding the corpus is expensive — **back this up** (see [BACKUPS.md](BACKUPS.md)). |
| `EVEREST_LOGS_DIR` | `<workspace>/logs` | |

### Backups

| Var | Default | Notes |
|---|---|---|
| `EVEREST_BACKUP_DEST` | OneDrive path | Where `backup_db.ps1` writes daily snapshots. |

### Rate limits (per hour, per user)

| Var | Default | Notes |
|---|---|---|
| `EVEREST_RATE_LIMIT_LLM_PER_HOUR` | `30` | proposal_manager bucket. |
| `EVEREST_RATE_LIMIT_LLM_VENDOR_PER_HOUR` | `10` | vendor + evaluator. |
| `EVEREST_RATE_LIMIT_LLM_EVALUATOR_PER_HOUR` | `10` | |
| (admins) | unlimited | Hard-coded — no env. |

### Error reporting (optional)

| Var | Notes |
|---|---|
| `SENTRY_DSN` | If set, Anthropic / OpenAI / SQL exceptions are sent to Sentry. No-op when unset. |
| `SENTRY_ENVIRONMENT` | `development` / `pilot` / `production`. |
| `SENTRY_TRACES_SAMPLE_RATE` | `0.0` (off by default — performance tracing). |

### Corp network / SSL (only when needed)

| Var | Notes |
|---|---|
| `EVEREST_SSL_VERIFY` | `0` to disable verify on outbound LLM calls. **Last resort.** Prefer `REQUESTS_CA_BUNDLE`. |
| `REQUESTS_CA_BUNDLE` / `SSL_CERT_FILE` | Path to corp root CA bundle. |

### Env matrix

| Variable | dev | pilot | prod |
|---|---|---|---|
| `DEBUG` | `false` | `false` | `false` |
| `DATABASE_URL` | sqlite | postgres | postgres (managed) |
| `EVEREST_DATA_DIR` | unset | `/data/everest` | mounted volume / blob |
| `CORS_ORIGINS` | `http://localhost:3000` | `https://everest.<domain>` | `https://everest.<domain>` |
| `SENTRY_DSN` | unset | set | set |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `480` | `480` (or shorter once #8 ships) | `60` after #8 ships |

---

## 4. First-time setup (dev)

```powershell
# 1. Clone (or pull) the repo
cd c:\Users\<you>
git clone https://github.com/BobLeckie1974/Everest
cd Everest

# 2. Create .env from template
Copy-Item .env.example .env
# (then edit .env, add SECRET_KEY + LLM keys)

# 3. Install Python deps
py -m pip install -r requirements.txt

# 4. Install frontend deps
cd frontend\rfp-ui
npm install
cd ..\..

# 5. Initialize DB schema + seed admin
py seed_admin.py

# 6. (Optional) Install daily backup task as Administrator
powershell -ExecutionPolicy Bypass -File install_backup_task.ps1 `
  -BackupDest "C:\Users\<you>\OneDrive - Parsons Corp\Everest Backup"
```

## 5. Daily start (dev)

The repo ships VS Code tasks for both servers. Use them.

| Task | Equivalent shell command |
|---|---|
| **Start Backend (FastAPI)** | `py -m uvicorn app.main:app --port 8000 --host 127.0.0.1` |
| **Start Frontend (React)** | `cd frontend\rfp-ui; $env:BROWSER="none"; npm start` |
| **Start Everest (Backend + Frontend)** | Both in parallel — the default build task. |

> **Don't use `--reload`** on Windows. The reloader spawns a worker
> that imports torch and can hang silently for minutes (see
> [/memories/repo/everest-overview.md](.) for context).

> **First boot is slow** (~90 s) because `langchain_openai → transformers → torch`
> import takes time on a cold FS cache. Subsequent boots are ~6 s.

After both are up:
- Frontend: http://localhost:3000
- Backend: http://localhost:8000
- Default admin login: see `seed_admin.py` env vars (defaults to `Robert.Leckie@parsons.com`).

## 6. Smoke test after every change

Every backend change that's *not* purely a frontend tweak should be
followed by:

```powershell
# 1. Run the test suite
py -m pytest -q
# expected: 21 passed (or current count)

# 2. Confirm health probes
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/healthz
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/readyz

# 3. Check fingerprint vs baseline (data-touching changes only)
py _fingerprint.py --out _fp.json
# diff against db_backups/pre-prod-hardening-2026-04-28/fingerprint.json

# 4. Check the request-id flow
$r = Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/healthz
$r.Headers["X-Request-Id"]
# Then: tail -F logs\rfp_platform.log and confirm the same id appears
```

For data-touching changes, also follow the rules in
[/memories/repo/data-safety-policy.md](.).

## 7. Where things live (file map)

| Path | Purpose |
|---|---|
| `app/main.py` | FastAPI app, middleware stack, router registry |
| `app/config.py` | Startup env validation + model<->DB drift guard |
| `app/auth.py` | JWT, password hashing, role dependencies |
| `app/database.py` | SQLAlchemy engine + `get_db` |
| `app/models.py` | All SA models (one file by convention) |
| `app/paths.py` | `uploads_dir()`, `chroma_dir()`, `logs_dir()` resolvers |
| `app/observability.py` | Sentry init |
| `app/logging_config.py` | JSON logs + request-id filter |
| `app/error_handlers.py` | Centralized exception handlers |
| `app/routers/*.py` | One per HTTP feature area (see registration in `main.py`) |
| `app/services/*.py` | Business logic: `audit.py`, `jobs.py`, `llm_usage.py`, `rate_limit.py`, `orchestrator_agent.py`, `competitor_analyst.py`, `parsons_coverage.py`, ... |
| `frontend/rfp-ui/src/auth/AuthContext.js` | Front-end auth state, axios interceptor, post-login redirect |
| `frontend/rfp-ui/src/App.js` | Routes + session-expired toast |
| `frontend/rfp-ui/src/pages/Settings.js` | Admin Users CRUD |
| `tests/conftest.py` | Test fixtures (ephemeral SQLite, `client`, `admin_headers`) |
| `tests/test_smoke.py` | All current smoke tests |
| `_fingerprint.py` | Read-only DB fingerprint for verification gates |
| `_run_restore_drill.py` | Read-only backup-validation drill |
| `backup_db.ps1` / `restore_db.ps1` / `install_backup_task.ps1` | Backup tooling |
| `seed_admin.py` | Idempotent admin user creation |
| `docs/BACKUPS.md` | Backup + restore runbook |
| `docs/PROD_HARDENING_ROLLBACK.md` | Per-step change log with rollback procedures |
| `docs/DEPLOYMENT.md` | This file |
| `db_backups/pre-prod-hardening-2026-04-28/` | Baseline snapshot + fingerprint |

## 8. Diagnostics cheat-sheet

| Symptom | First thing to check | Reference |
|---|---|---|
| Login returns 500 | Backend not running. `Invoke-WebRequest http://127.0.0.1:8000/healthz`. Check `logs/rfp_platform.log` for the latest stack trace. | [Schedule load-failure entry](../app/models.py) — `IngestedDocument.created_at` is the canonical example. The drift guard added in #2b will warn at startup if it recurs. |
| "Failed to load schedule" / dashboard blank | One API call 500'd. `Get-Content logs/rfp_platform.log -Tail 80 \| Select-String 500`. The frontend uses `Promise.allSettled` so other tiles still render. | |
| User booted out mid-task | Token expired (was 30 min, now 8 h). Snackbar should say "Your session expired". | Step #1 |
| Mysterious 401 with valid token | Probably a 307 redirect that lost `Authorization`. Hit the same path with curl + token to confirm. The interceptor in `AuthContext.js` only logs out on 401s from `/api/auth/me` or `/api/auth/token`. | Step #1 |
| LLM call returns "[MOCK RESPONSE — no AI provider available]" | Either the SSL cert chain failed (corp proxy → set `EVEREST_SSL_VERIFY=0`) or the API key is wrong. Check log lines from `orchestrator_agent`. | [/memories/repo/everest-overview.md](.) — corp network section |
| "Extracted 0 requirements" | Used to mean reconciler max_tokens too low. Fixed; if it recurs, check the `_extract_from_window` log. | [/memories/repo/everest-overview.md](.) |
| 429 Too Many Requests | Rate limit (#10). The response body has `retry_after_seconds`. Bump `EVEREST_RATE_LIMIT_LLM_PER_HOUR` if needed. | Step #10 |
| Background job stuck "running" | Daemon thread lost. Re-submit. (In-memory queue is lost on backend restart — pending #12 follow-up to use the DB.) | Step #12 |
| Audit log empty | Logging is best-effort and never raises. Check `logs/rfp_platform.log` for `[audit] failed to write` lines. | Step #9 |

## 9. Path to pilot (Phase 2)

These steps are **not yet done** but are the block of work needed to
move from "your laptop" to "shared VM with 5–20 users". Each is its
own todo (see `docs/PROD_HARDENING_ROLLBACK.md`).

1. **#3** Migrate SQLite → Postgres. Stand up Postgres in the existing
   `docker-compose.yml`. Copy data with a one-shot script (sqlite_to_pg).
   Verify row counts. Switch `DATABASE_URL`. SQLite stays untouched as
   the rollback path.
2. **#5 / #21** Production frontend build. `npm run build`, serve with
   nginx (in a second container), reverse-proxy `/api/*` to FastAPI.
3. **#6** TLS termination. Caddy (auto-cert) or nginx + corp cert.
4. **#8** Refresh tokens — short access tokens (15 min) + long refresh
   tokens (7 d) with rotation. Lets us tighten session security
   without UX regression.
5. **#17** Email-invite flow. SMTP via Parsons relay. Replaces the
   manual temp-password handoff.
6. **#22** Pick a host (Azure App Service / Parsons VM / etc.) and
   write the deploy procedure for it as a follow-up to this doc.

## 10. Path to prod (Phase 3)

- **#18** SSO via Entra ID. OIDC. Provision users from groups.
- Centralized logs (we already emit JSON; just route to the corp
  log sink — Splunk / ELK / Azure Log Analytics).
- Off-site backup with versioning (Azure Blob).
- Postgres point-in-time recovery (WAL archiving).
- 24×7 monitoring on `/healthz` + `/readyz` (the load balancer should
  drop the instance from rotation if `/readyz` returns 503).
- Per-user quota dashboards (already wired — `/api/llm-usage/summary`).

## 11. Cross-references

- [BACKUPS.md](BACKUPS.md) — backup + restore runbook
- [PROD_HARDENING_ROLLBACK.md](PROD_HARDENING_ROLLBACK.md) — per-step
  change log + how to revert any step
- [ENTERPRISE_DESIGN.md](ENTERPRISE_DESIGN.md) — original architecture vision
- [.env.example](../.env.example) — every env var, with comments
- [/memories/repo/everest-overview.md](.) — accumulated tribal knowledge
- [/memories/repo/data-safety-policy.md](.) — non-negotiable rules for
  data-touching changes
