# Everest — Mac/Linux dev runner.
# Quick reference:
#   make setup     install backend + frontend deps (one-time)
#   make migrate   apply alembic migrations
#   make seed      create the first admin user
#   make backend   run uvicorn on :8000
#   make frontend  run CRA dev server on :3000
#   make dev       run backend + frontend together (two terminals via osascript on macOS)
#   make test      run pytest

PY ?= python3
VENV := .venv
VENV_PY := $(VENV)/bin/python
VENV_PIP := $(VENV)/bin/pip
UI_DIR := frontend/rfp-ui

.PHONY: help setup venv backend-deps frontend-deps migrate seed backend frontend dev test clean

help:
	@grep -E '^[a-zA-Z_-]+:.*' Makefile | grep -v '^\.PHONY' | sed 's/:.*//' | sort

$(VENV)/bin/activate:
	$(PY) -m venv $(VENV)
	$(VENV_PIP) install --upgrade pip

venv: $(VENV)/bin/activate

backend-deps: venv
	$(VENV_PIP) install -r requirements.txt

frontend-deps:
	cd $(UI_DIR) && npm install

setup: backend-deps frontend-deps
	@echo "Setup complete. Copy .env.example to .env and fill in SECRET_KEY + at least one API key."

migrate: venv
	$(VENV_PY) -m alembic upgrade head

seed: venv
	$(VENV_PY) seed_admin.py

backend: venv
	$(VENV_PY) -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

frontend:
	cd $(UI_DIR) && npm start

# `make dev` opens two Terminal tabs on macOS (backend + frontend).
# On Linux, just run `make backend` and `make frontend` in two terminals.
dev:
	@osascript -e 'tell application "Terminal" to do script "cd $(CURDIR) && make backend"' \
	           -e 'tell application "Terminal" to do script "cd $(CURDIR) && make frontend"'

test: venv
	$(VENV_PY) -m pytest

clean:
	rm -rf $(VENV)
	rm -rf $(UI_DIR)/node_modules
	find . -type d -name __pycache__ -exec rm -rf {} +
