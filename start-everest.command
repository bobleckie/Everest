#!/usr/bin/env bash
# Everest — one-click Mac launcher.
# Double-click from Finder, or via the Everest.app on the Desktop.

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO"

# On any error, show the message and pause so the user can read it
# before Terminal auto-closes the window.
fail() {
    echo
    echo "ERROR: $*"
    echo
    read -n 1 -s -r -p "Press any key to close this window..."
    exit 1
}
trap 'fail "Launcher failed at line $LINENO. See output above."' ERR
set -E

echo "==> Everest launcher (repo: $REPO)"

# --- 0. Pick a compatible Python ----------------------------------------
# Project pins Python 3.11 in the Dockerfile. Locally we accept 3.11 or 3.12.
# Python 3.13 is rejected: torch (a transitive dep of sentence-transformers
# and chromadb) has no Intel-Mac wheel for 3.13 as of late 2025.
PYBIN=""
for cand in python3.12 python3.11; do
    if command -v "$cand" >/dev/null 2>&1; then
        PYBIN="$(command -v "$cand")"
        break
    fi
done

if [ -z "$PYBIN" ]; then
    echo
    echo "Python 3.11 or 3.12 is required, but neither was found on PATH."
    if command -v brew >/dev/null 2>&1; then
        echo "Install with Homebrew:"
        echo "    brew install python@3.12"
    else
        echo "Install Homebrew first (https://brew.sh), then run:"
        echo "    brew install python@3.12"
    fi
    echo "Then re-run this launcher."
    fail "Missing compatible Python."
fi

echo "==> Using $PYBIN ($($PYBIN --version 2>&1))"

# --- 1. Sanity checks ----------------------------------------------------
command -v node >/dev/null 2>&1 || \
    fail "node not found. Install Node 18 or 20: brew install node@20"

if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        echo "==> No .env found — copying .env.example to .env."
        cp .env.example .env
        echo
        echo "    IMPORTANT: edit .env and set SECRET_KEY + an API key before continuing."
        echo "    Generate a SECRET_KEY with:"
        echo "      $PYBIN -c 'import secrets; print(secrets.token_hex(32))'"
        open -a TextEdit .env
        echo
        read -n 1 -s -r -p "Press any key after saving .env to close this window, then re-launch Everest..."
        exit 0
    else
        fail "no .env or .env.example found in $REPO."
    fi
fi

# --- 2. Backend deps -----------------------------------------------------
# Sentinel file: only consider the venv "ready" if pip install fully succeeded
# on a previous run. A partial install leaves .venv but no sentinel.
SENTINEL=".venv/.deps_installed"

if [ ! -f "$SENTINEL" ]; then
    echo "==> Setting up Python venv (.venv) — this can take several minutes the first time..."
    rm -rf .venv
    "$PYBIN" -m venv .venv
    .venv/bin/pip install --upgrade pip
    if ! .venv/bin/pip install -r requirements.txt; then
        rm -rf .venv
        fail "Backend dependency install failed. See pip output above."
    fi
    touch "$SENTINEL"
fi

# --- 3. Frontend deps ----------------------------------------------------
if [ ! -d frontend/rfp-ui/node_modules ]; then
    echo "==> Installing frontend deps (this also takes a few minutes the first time)..."
    (cd frontend/rfp-ui && npm install) || fail "npm install failed."
fi

# --- 4. Migrations -------------------------------------------------------
echo "==> Running alembic migrations..."
.venv/bin/python -m alembic upgrade head || echo "    (migrations failed — continuing anyway)"

# --- 5. Seed admin (idempotent) ------------------------------------------
if [ -f seed_admin.py ]; then
    echo "==> Ensuring admin user exists..."
    .venv/bin/python seed_admin.py || echo "    (seed_admin failed — continuing anyway)"
fi

# --- 6. Launch in two Terminal tabs --------------------------------------
echo "==> Launching backend (:8000) and frontend (:3000) in new Terminal tabs..."

osascript <<EOF
tell application "Terminal"
    activate
    do script "cd '$REPO' && echo '== Everest backend ==' && .venv/bin/python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000"
    do script "cd '$REPO/frontend/rfp-ui' && echo '== Everest frontend ==' && npm start"
end tell
EOF

# --- 7. Open browser once frontend is responding -------------------------
echo "==> Waiting for frontend to come up..."
for i in {1..60}; do
    if curl -sf http://localhost:3000 >/dev/null 2>&1; then
        open http://localhost:3000
        echo "==> Opened http://localhost:3000 — this window can be closed."
        exit 0
    fi
    sleep 2
done

echo "==> Frontend didn't respond within 2 minutes. Check the Terminal tabs for errors."
read -n 1 -s -r -p "Press any key to close this window..."
