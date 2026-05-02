# RFP Response Generator (Everest)

A FastAPI + React web application for generating RFP responses using AI agents.

## Features
- Orchestrator for human interaction
- Competitive Research Agent
- Parsons SME (Subject Matter Expert)
- Conflict Detector
- Document Composer
- Document and image ingestion
- Presentation generation in PDF, HTML, Word, PPT

## Quick start (macOS / Linux)

Prereqs: Python 3.11+, Node 18 or 20, optionally Docker.

```bash
# 1. Clone and configure
cp .env.example .env
# Then edit .env — at minimum set SECRET_KEY (64 hex chars) and one of
#   OPENAI_API_KEY / ANTHROPIC_API_KEY.
# Generate a SECRET_KEY with: python3 -c "import secrets; print(secrets.token_hex(32))"

# 2. Install deps (creates .venv and installs frontend node_modules)
make setup

# 3. Initialize the database
make migrate
make seed       # creates the first admin user

# 4. Run the app
make dev        # opens backend (:8000) and frontend (:3000) in two Terminal tabs
# or, in two terminals: `make backend` + `make frontend`
```

App is at http://localhost:3000 — log in with the admin you just seeded.

### Mac shortcut

Double-click [`start-everest.command`](start-everest.command) from Finder to launch
both servers and open the browser. You can drag it to the Dock for one-click access.

## Quick start (Windows / Docker)

See **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)** — the single source of truth for
all environments (dev, pilot, prod). Also relevant:

- [docs/LOCAL_PROD.md](docs/LOCAL_PROD.md) — running prod-style on a single host
- [docs/POSTGRES_MIGRATION.md](docs/POSTGRES_MIGRATION.md) — moving off SQLite
- [docs/BACKUPS.md](docs/BACKUPS.md) — backup / restore procedure

## Repo layout

```
app/             FastAPI backend (routers, services, models, auth)
alembic/         DB migrations
frontend/rfp-ui/ React (CRA + MUI) frontend
tests/           pytest suite
docs/            Deployment and design docs
infra/proxy/     Nginx reverse-proxy (for prod-style runs)
seed_*.py        Database seed scripts
```

## Tests

```bash
make test
```
