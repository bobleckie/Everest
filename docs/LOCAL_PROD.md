# Local-prod stack

A docker compose stack that runs a **production-equivalent** Everest
on your laptop. Same shape as the eventual server: Postgres + backend
+ static React + nginx TLS termination. No dev servers, no hot
reload, no dev-mode CORS shortcuts.

## Why this exists

You can't deploy to a server yet. Running a local-prod equivalent
buys you:

1. **Confidence** the production deploy actually works — bugs surface
   here, not in front of users.
2. A migration target — you can use [POSTGRES_MIGRATION.md](POSTGRES_MIGRATION.md)
   to copy your live SQLite into this Postgres and verify everything.
3. Realistic perf — torch import time, container startup, real
   nginx + TLS — exactly what the user will see on the server.

> The local-prod stack is **completely isolated** from your dev
> SQLite (`rfp.db`) and dev data dirs. It uses its own Docker
> volumes. Nothing in `c:\Users\p002298J\Everest\` is touched.

## Prerequisites

- Docker Desktop running (Windows + WSL2 backend or Linux native).
- ~6 GB of free disk for the backend image (torch is ~2 GB).
- About 3–4 minutes for the first build.

## First-time bring-up

```powershell
# 1. Copy the env template and fill in REQUIRED values.
Copy-Item .env.local-prod.example .env.local-prod
# Then edit .env.local-prod:
#   * SECRET_KEY        — py -c "import secrets; print(secrets.token_hex(32))"
#   * POSTGRES_PASSWORD — pick a strong one
#   * ANTHROPIC_API_KEY and/or OPENAI_API_KEY

# 2. Build + start everything.
docker compose -f docker-compose.local-prod.yml --env-file .env.local-prod up -d --build

# 3. Watch it come up. The backend takes ~90s on first boot (torch import).
docker compose -f docker-compose.local-prod.yml ps
docker compose -f docker-compose.local-prod.yml logs -f backend
```

When all services show `(healthy)`, you're up.

## Smoke test

Open https://localhost:8443/

Your browser will warn about the self-signed cert — that's expected.
Click through. (In the eventual server deploy this gets replaced with
a real cert and the warning goes away.)

```powershell
# Health probes — TLS path, with -SkipCertificateCheck since it's self-signed.
Invoke-WebRequest -SkipCertificateCheck https://localhost:8443/healthz
Invoke-WebRequest -SkipCertificateCheck https://localhost:8443/readyz

# HTTP -> HTTPS redirect
Invoke-WebRequest -SkipCertificateCheck http://localhost:8080/healthz
# (should 301 to https)
```

The first time you sign in, the Postgres DB is empty — there's no
admin user yet. Two options:

```powershell
# Option A: bake-in admin from env vars (simplest)
docker compose -f docker-compose.local-prod.yml exec backend py seed_admin.py

# Option B: copy your live SQLite data into the compose Postgres
# (follow docs/POSTGRES_MIGRATION.md, but point --target at the
# compose Postgres on localhost:5432 — note this requires you to
# expose Postgres or run the migrator from inside the backend
# container).
```

## Day-to-day

```powershell
# Stop the stack, KEEP the data:
docker compose -f docker-compose.local-prod.yml down

# Restart it:
docker compose -f docker-compose.local-prod.yml --env-file .env.local-prod up -d

# Tail logs:
docker compose -f docker-compose.local-prod.yml logs -f backend

# Shell into the backend (run a one-off py script):
docker compose -f docker-compose.local-prod.yml exec backend bash

# Watch all services in one pane:
docker compose -f docker-compose.local-prod.yml logs -f --tail=20
```

## Switching between dev and local-prod

These two stacks **don't conflict** — they use different volumes and
different ports.

| | Dev | Local-prod |
|---|---|---|
| Frontend | `npm start` (3000) | nginx in container (8443/HTTPS via outer proxy) |
| Backend | `py -m uvicorn ... 127.0.0.1:8000` | container (port not exposed; reached via proxy) |
| DB | SQLite `rfp.db` | Postgres in container |
| Data dirs | `<workspace>/uploads/`, `<workspace>/chroma_db/` | `/data/` in `everest_app_data_localprod` volume |

You can run both at once during testing as long as your dev backend
keeps `--port 8000` and the local-prod stack uses different host
ports (8443/8080 by default).

## Tearing down + clean slate

```powershell
# Wipes the local-prod Postgres + uploads + chroma. Dev SQLite untouched.
docker compose -f docker-compose.local-prod.yml down -v
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `the option -d --build is ambiguous` | You forgot `--env-file .env.local-prod`. Compose can't read variables that have a `:?` requirement without it. |
| `POSTGRES_PASSWORD is required` | The compose file declares it as required (`:?`). Set it in `.env.local-prod`. |
| Backend healthcheck flaps | First boot is slow — torch import takes ~90s. Healthcheck `start_period` is 90s; if your machine is slow it may need longer. Check `docker compose logs backend`. |
| Browser cert warning | Expected — self-signed. Click through. To suppress permanently, import `everest_tls_localprod`'s `server.crt` into your trust store. |
| `502 Bad Gateway` from the proxy | Backend or frontend container not yet healthy. `docker compose ps` will show. |
| Login fails: "no such user" | Postgres is empty. Run `docker compose exec backend py seed_admin.py` or do the SQLite migration. |
| Want to expose Postgres on the host (e.g. for the migrator) | Add `ports: ["5432:5432"]` to the postgres service — left out by default so we don't fight the dev `docker-compose.postgres.yml`. |

## What this is NOT

- **Not** the production deploy. Replace the self-signed cert with a
  real one. Replace `everest-postgres` with managed Postgres + WAL
  archiving + backups. Add Sentry DSN. Tune ACCESS_TOKEN_EXPIRE_MINUTES
  down to 15 once everyone's using refresh tokens.
- **Not** a place to put real proposal data unless you're OK losing it
  on `down -v`. Move good data INTO it via the migration script,
  validate, then keep going.
