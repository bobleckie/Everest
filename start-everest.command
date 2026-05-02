#!/usr/bin/env bash
# Everest — one-click Mac launcher.
# Double-click from Finder, or drag to the Dock for one-click access.
#
# What it does:
#   1. cd's to the repo (resolves its own path)
#   2. Creates .venv and installs backend + frontend deps if missing
#   3. Runs alembic migrations
#   4. Seeds the first admin user (idempotent)
#   5. Opens two Terminal tabs: one for uvicorn, one for `npm start`
#   6. Opens http://localhost:3000 once the frontend is up

set -e

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO"

echo "==> Everest launcher (repo: $REPO)"

# --- 0. Sanity checks ----------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 not found. Install Python 3.11+ (e.g. via Homebrew: brew install python@3.11)."
    read -n 1 -s -r -p "Press any key to close..."
    exit 1
fi

if ! command -v node >/dev/null 2>&1; then
    echo "ERROR: node not found. Install Node 18 or 20 (e.g. via Homebrew: brew install node@20)."
    read -n 1 -s -r -p "Press any key to close..."
    exit 1
fi

if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        echo "==> No .env found — copying .env.example to .env."
        cp .env.example .env
        echo
        echo "    IMPORTANT: edit .env and set SECRET_KEY + an API key before continuing."
        echo "    Generate SECRET_KEY with:"
        echo "      python3 -c 'import secrets; print(secrets.token_hex(32))'"
        echo
        read -n 1 -s -r -p "Press any key to open .env in TextEdit, then save and re-run this launcher..."
        open -a TextEdit .env
        exit 0
    else
        echo "ERROR: no .env or .env.example found."
        read -n 1 -s -r -p "Press any key to close..."
        exit 1
    fi
fi

# --- 1. Backend deps -----------------------------------------------------
if [ ! -d .venv ]; then
    echo "==> Creating Python venv (.venv) and installing backend deps..."
    python3 -m venv .venv
    .venv/bin/pip install --upgrade pip
    .venv/bin/pip install -r requirements.txt
fi

# --- 2. Frontend deps ----------------------------------------------------
if [ ! -d frontend/rfp-ui/node_modules ]; then
    echo "==> Installing frontend deps..."
    (cd frontend/rfp-ui && npm install)
fi

# --- 3. Migrations -------------------------------------------------------
echo "==> Running alembic migrations..."
.venv/bin/python -m alembic upgrade head || echo "    (migrations failed — continuing anyway)"

# --- 4. Seed admin (idempotent) ------------------------------------------
if [ -f seed_admin.py ]; then
    echo "==> Ensuring admin user exists..."
    .venv/bin/python seed_admin.py || echo "    (seed_admin failed — continuing anyway)"
fi

# --- 5. Launch in two Terminal tabs --------------------------------------
echo "==> Launching backend (:8000) and frontend (:3000) in new Terminal tabs..."

osascript <<EOF
tell application "Terminal"
    activate
    do script "cd '$REPO' && echo '== Everest backend ==' && .venv/bin/python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000"
    do script "cd '$REPO/frontend/rfp-ui' && echo '== Everest frontend ==' && npm start"
end tell
EOF

# --- 6. Open browser once frontend is responding -------------------------
echo "==> Waiting for frontend to come up..."
for i in {1..60}; do
    if curl -sf http://localhost:3000 >/dev/null 2>&1; then
        open http://localhost:3000
        echo "==> Opened http://localhost:3000"
        exit 0
    fi
    sleep 2
done

echo "==> Frontend didn't respond within 2 minutes. Check the Terminal tabs for errors."
read -n 1 -s -r -p "Press any key to close this window..."
