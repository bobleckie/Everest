"""Resolve filesystem paths for app data directories.

Single source of truth for where the app reads/writes:
  * uploaded RFP source documents (`uploads/`)
  * the Chroma vector store (`chroma_db/`)
  * application logs (`logs/`)
  * the question-drafter scratch dir (`logs/.qdraft/`)

Each is overridable by an env var, but the defaults are byte-for-byte
identical to the historical hardcoded paths so this module is a pure
no-op refactor at default settings.

Env vars:
  EVEREST_UPLOADS_DIR  default: <workspace>/uploads
  EVEREST_CHROMA_DIR   default: <workspace>/chroma_db
  EVEREST_LOGS_DIR     default: <workspace>/logs
  EVEREST_DATA_DIR     optional: when set, ALL of the above default to
                       paths under it (uploads, chroma_db, logs) unless
                       individually overridden. Useful for prod deploys
                       where everything lives on a single mounted volume.

All resolvers create the directory if it doesn't exist (mkdir -p).
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


def _workspace_root() -> Path:
    """Workspace root = parent of `app/` package."""
    return Path(__file__).resolve().parent.parent


def _data_root() -> Path:
    """Optional umbrella dir for prod deploys."""
    val = os.getenv("EVEREST_DATA_DIR", "").strip()
    if val:
        return Path(val).resolve()
    return _workspace_root()


def _resolve(env_var: str, default_subdir: str) -> Path:
    """Return env-overridden path, else `<data_root>/<default_subdir>`."""
    val = os.getenv(env_var, "").strip()
    if val:
        p = Path(val).resolve()
    else:
        p = (_data_root() / default_subdir).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


@lru_cache(maxsize=1)
def workspace_root() -> Path:
    return _workspace_root()


@lru_cache(maxsize=1)
def uploads_dir() -> Path:
    return _resolve("EVEREST_UPLOADS_DIR", "uploads")


@lru_cache(maxsize=1)
def chroma_dir() -> Path:
    return _resolve("EVEREST_CHROMA_DIR", "chroma_db")


@lru_cache(maxsize=1)
def logs_dir() -> Path:
    return _resolve("EVEREST_LOGS_DIR", "logs")


@lru_cache(maxsize=1)
def qdraft_dir() -> Path:
    """Scratch dir for question-drafter intermediate JSON."""
    p = (logs_dir() / ".qdraft").resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p
