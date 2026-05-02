"""Pytest fixtures for Everest backend smoke tests.

Hard rule (see /memories/repo/data-safety-policy.md): tests MUST NOT
touch the production `rfp.db` file. We force `DATABASE_URL` to a fresh
sqlite file in a temp directory BEFORE any `app.*` module is imported,
then override the FastAPI dependency `get_db` to use that engine.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# ── 1. Inject env BEFORE app modules are imported ────────────────────
_TMP = Path(tempfile.mkdtemp(prefix="everest_pytest_"))
_TEST_DB = _TMP / "test_rfp.db"
_TEST_DB_URL = f"sqlite:///{_TEST_DB.as_posix()}"

os.environ["DATABASE_URL"] = _TEST_DB_URL
os.environ["SECRET_KEY"] = "pytest-secret-key-not-for-prod-not-for-prod-32"
os.environ["DEBUG"] = "false"
os.environ["EVEREST_RATE_LIMIT_LLM_PER_HOUR"] = "2"
os.environ["EVEREST_RATE_LIMIT_LLM_VENDOR_PER_HOUR"] = "1"
os.environ["LOG_LEVEL"] = "WARNING"
# Don't let tests touch the real chroma_db / uploads / logs dirs.
os.environ["EVEREST_DATA_DIR"] = str(_TMP)

# ── 2. Now safe to import app + build a test engine bound to the temp DB
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app import models  # noqa: E402, F401  (loads all model classes)
from app.database import Base, get_db  # noqa: E402

_test_engine = create_engine(
    _TEST_DB_URL,
    connect_args={"check_same_thread": False},
)
_TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_test_engine)
Base.metadata.create_all(bind=_test_engine)


def _override_get_db():
    db = _TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(scope="session")
def app_instance():
    """The FastAPI app, with `get_db` overridden to use the test DB."""
    from app.main import app
    app.dependency_overrides[get_db] = _override_get_db
    yield app
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture()
def client(app_instance):
    from fastapi.testclient import TestClient
    with TestClient(app_instance) as c:
        yield c


@pytest.fixture()
def reset_rate_limits():
    """Tests that exercise the rate limiter call this to start clean."""
    from app.services import rate_limit
    rate_limit._reset_all()
    yield
    rate_limit._reset_all()


def _create_test_admin() -> dict:
    """Idempotent: creates a test admin user via the same code path
    `seed_admin.py` uses, but in the test DB.
    """
    from app.auth import get_password_hash
    db = _TestSessionLocal()
    try:
        existing = db.query(models.User).filter(models.User.username == "test_admin").first()
        if existing:
            return {"username": "test_admin", "password": "testpass1234!"}
        u = models.User(
            username="test_admin",
            email="test_admin@example.com",
            first_name="Test",
            last_name="Admin",
            hashed_password=get_password_hash("testpass1234!"),
            role="admin",
            is_active=True,
            must_change_password=False,
        )
        db.add(u)
        db.commit()
        return {"username": "test_admin", "password": "testpass1234!"}
    finally:
        db.close()


@pytest.fixture(scope="session")
def admin_credentials():
    return _create_test_admin()


@pytest.fixture()
def admin_headers(client, admin_credentials):
    """Logs in as the test admin and returns Authorization header."""
    r = client.post(
        "/api/auth/token",
        data=admin_credentials,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
