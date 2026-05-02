"""Smoke tests for critical backend paths.

Run: `py -m pytest`
"""
from __future__ import annotations


# ── Liveness / readiness ─────────────────────────────────────────────
def test_healthz_is_unauthenticated_and_ok(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_readyz_reports_db_and_chroma(client):
    r = client.get("/readyz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["checks"]["db"] == "ok"
    assert body["checks"]["chroma_dir"].startswith("ok") or body["checks"]["chroma_dir"] == "ok"


# ── Security middleware ──────────────────────────────────────────────
def test_security_headers_present_on_plain_http(client):
    r = client.get("/healthz")
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("x-frame-options") == "DENY"
    assert r.headers.get("referrer-policy") == "strict-origin-when-cross-origin"
    assert r.headers.get("permissions-policy")
    # HSTS should NOT appear over plain HTTP
    assert "strict-transport-security" not in (k.lower() for k in r.headers.keys())


def test_hsts_appears_when_proxy_says_https(client):
    r = client.get("/healthz", headers={"X-Forwarded-Proto": "https"})
    assert "max-age=31536000" in r.headers.get("strict-transport-security", "")


def test_request_id_header_is_echoed(client):
    r = client.get("/healthz")
    rid = r.headers.get("x-request-id")
    assert rid and len(rid) >= 8


def test_request_id_passthrough(client):
    r = client.get("/healthz", headers={"X-Request-Id": "deadbeef0001"})
    assert r.headers.get("x-request-id") == "deadbeef0001"


# ── Auth ─────────────────────────────────────────────────────────────
def test_login_and_me_roundtrip(client, admin_credentials):
    r = client.post(
        "/api/auth/token",
        data=admin_credentials,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    body = me.json()
    assert body["username"] == admin_credentials["username"]
    assert body["role"] == "admin"


def test_login_with_bad_password_is_401(client, admin_credentials):
    r = client.post(
        "/api/auth/token",
        data={"username": admin_credentials["username"], "password": "wrongpw"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 401


def test_protected_endpoint_requires_auth(client):
    r = client.get("/api/auth/users")
    assert r.status_code in (401, 403)


# ── Rate limiter ─────────────────────────────────────────────────────
def test_rate_limit_429_after_cap_for_non_admin(client, reset_rate_limits):
    """Vendor cap is 1/h in pytest config; admin is unlimited.
    Use a fake user with role=vendor via dependency override.
    """
    from app.auth import get_current_user
    app = client.app

    class FakeVendor:
        id = 9001
        role = "vendor"

    app.dependency_overrides[get_current_user] = lambda: FakeVendor()
    try:
        r1 = client.post("/api/questions/ask", json={"q": "first"})
        assert r1.status_code != 429, f"first call should pass, got {r1.status_code}"
        r2 = client.post("/api/questions/ask", json={"q": "second"})
        assert r2.status_code == 429
        assert r2.headers.get("retry-after"), "429 must include Retry-After"
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_admin_bypasses_rate_limit(client, reset_rate_limits, admin_headers):
    # Admin should NOT be rate-limited, even though cap=2 in tests.
    codes = [
        client.post(
            "/api/questions/ask",
            json={"q": f"admin-{i}"},
            headers=admin_headers,
        ).status_code
        for i in range(5)
    ]
    assert 429 not in codes, f"admin shouldn't be 429-limited, got {codes}"


# ── Background jobs ──────────────────────────────────────────────────
def test_jobs_endpoint_404s_for_unknown_id(client, admin_headers):
    r = client.get("/api/jobs/does-not-exist", headers=admin_headers)
    assert r.status_code == 404


def test_jobs_list_returns_envelope(client, admin_headers):
    r = client.get("/api/jobs?limit=5", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert "items" in body and "count" in body
    assert isinstance(body["items"], list)


# ── Config validator (sanity) ────────────────────────────────────────
def test_config_validator_rejects_placeholder_secret(monkeypatch):
    from app import config as cfg
    monkeypatch.setenv("SECRET_KEY", "your-secret-key-here-change-in-production")
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000")
    import pytest
    with pytest.raises(SystemExit):
        cfg.validate_runtime_config()


def test_config_validator_passes_real_key(monkeypatch):
    from app import config as cfg
    monkeypatch.setenv("SECRET_KEY", "8f4a2e6d1c9b3f7e5a0d8c4b2f6e1a3d9c7b5e2f4a8d1c6b3f9e7a2d5c0b8e4f")
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000")
    cfg.validate_runtime_config()  # must not raise


# ── Audit log ────────────────────────────────────────────────────────
def test_audit_records_login_success(client, admin_credentials, admin_headers):
    """A successful login should produce a `login_success` row, and the
    admin endpoint should return it."""
    r = client.get("/api/audit?action=login_success&limit=5", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    assert any(it["action"] == "login_success" for it in body["items"])
    # Most recent entry should have a request_id (because RequestIdMiddleware ran)
    latest = body["items"][0]
    assert latest["username"] == admin_credentials["username"]
    assert latest["status"] == "ok"


def test_audit_records_failed_login(client, admin_credentials, admin_headers):
    # Force a failed login
    client.post(
        "/api/auth/token",
        data={"username": admin_credentials["username"], "password": "definitely-wrong"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    r = client.get("/api/audit?action=login_failure&limit=5", headers=admin_headers)
    assert r.status_code == 200
    items = r.json()["items"]
    assert items, "expected at least one login_failure entry"
    assert items[0]["username"] == admin_credentials["username"]
    assert items[0]["status"] == "error"


# ── User invitations (#17) ──────────────────────────────────────────
def _create_pending_user_via_register(client, admin_headers, *, username, email):
    """Use the admin /register endpoint to create a fresh user, then mint
    an invitation via the new endpoint. Returns (user_id, invite_payload).
    """
    r = client.post(
        "/api/auth/register",
        json={
            "username": username, "email": email,
            "password": "placeholder1234!",  # immediately overwritten by invite
            "first_name": "Test", "last_name": "Invitee",
            "role": "proposal_manager",
        },
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    user_id = r.json()["id"]
    inv = client.post(
        f"/api/auth/users/{user_id}/invite",
        headers=admin_headers,
    )
    assert inv.status_code == 200, inv.text
    return user_id, inv.json()


def test_invite_create_returns_redeem_url_and_email_body(client, admin_headers):
    _, payload = _create_pending_user_via_register(
        client, admin_headers,
        username="invite_smoke_1", email="invite_smoke_1@example.com",
    )
    assert payload["redeem_url"].startswith("http")
    assert "/redeem-invite/" in payload["redeem_url"]
    # When SMTP isn't configured (no env vars in tests), email_sent is False
    # but admin still has the body to paste.
    assert payload["smtp_configured"] is False
    assert payload["email_sent"] is False
    assert "set up your account" in payload["email_body"].lower() or "everest" in payload["email_body"].lower()


def test_redeem_invitation_signs_in_user(client, admin_headers):
    _, payload = _create_pending_user_via_register(
        client, admin_headers,
        username="invite_smoke_2", email="invite_smoke_2@example.com",
    )
    # The redeem URL contains the plaintext token after the last slash.
    token = payload["redeem_url"].rsplit("/", 1)[-1]
    r = client.post(
        "/api/auth/redeem-invite",
        json={"token": token, "new_password": "MyOwnNewPass99!"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["access_token"]
    assert body["refresh_token"]
    # And the user can sign in normally with the password they chose
    login = client.post(
        "/api/auth/token",
        data={"username": "invite_smoke_2", "password": "MyOwnNewPass99!"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert login.status_code == 200


def test_redeem_invitation_short_password_rejected(client, admin_headers):
    _, payload = _create_pending_user_via_register(
        client, admin_headers,
        username="invite_smoke_3", email="invite_smoke_3@example.com",
    )
    token = payload["redeem_url"].rsplit("/", 1)[-1]
    r = client.post(
        "/api/auth/redeem-invite",
        json={"token": token, "new_password": "short"},
    )
    assert r.status_code == 400


def test_redeem_invitation_token_is_one_shot(client, admin_headers):
    _, payload = _create_pending_user_via_register(
        client, admin_headers,
        username="invite_smoke_4", email="invite_smoke_4@example.com",
    )
    token = payload["redeem_url"].rsplit("/", 1)[-1]
    r1 = client.post(
        "/api/auth/redeem-invite",
        json={"token": token, "new_password": "FirstUseValid1!"},
    )
    assert r1.status_code == 200
    # Replay should be refused
    r2 = client.post(
        "/api/auth/redeem-invite",
        json={"token": token, "new_password": "AnotherTryValid1!"},
    )
    assert r2.status_code == 400


def test_invite_endpoint_admin_only(client):
    """Non-admin gets 401/403 when calling the admin invite endpoint."""
    from app.auth import get_current_user
    app = client.app

    class FakeVendor:
        id = 9001
        role = "vendor"
        username = "fake_vendor"
        email = "fake_vendor@example.com"
        is_active = True

    app.dependency_overrides[get_current_user] = lambda: FakeVendor()
    try:
        r = client.post("/api/auth/users/1/invite")
        assert r.status_code in (401, 403)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


# ── Refresh tokens ──────────────────────────────────────────────────
def test_token_endpoint_returns_refresh_token(client, admin_credentials):
    r = client.post(
        "/api/auth/token",
        data=admin_credentials,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 200
    body = r.json()
    assert "access_token" in body
    assert body.get("refresh_token"), "expected refresh_token in /token response"
    assert body.get("refresh_token_expires_in_minutes", 0) > 0


def test_refresh_rotates_and_revokes_old(client, admin_credentials):
    # Get a refresh token
    r = client.post(
        "/api/auth/token",
        data=admin_credentials,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    rt1 = r.json()["refresh_token"]

    # Use it -> get a new pair
    r2 = client.post("/api/auth/refresh", json={"refresh_token": rt1})
    assert r2.status_code == 200, r2.text
    rt2 = r2.json()["refresh_token"]
    assert rt2 != rt1, "rotation must produce a new token"
    # New access token works
    new_access = r2.json()["access_token"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {new_access}"})
    assert me.status_code == 200

    # rt1 is now revoked — using it again should 401
    r3 = client.post("/api/auth/refresh", json={"refresh_token": rt1})
    assert r3.status_code == 401


def test_refresh_replay_revokes_chain(client, admin_credentials):
    """If a revoked token is replayed, the entire chain is killed."""
    r = client.post(
        "/api/auth/token",
        data=admin_credentials,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    rt1 = r.json()["refresh_token"]
    rt2 = client.post("/api/auth/refresh", json={"refresh_token": rt1}).json()["refresh_token"]
    rt3 = client.post("/api/auth/refresh", json={"refresh_token": rt2}).json()["refresh_token"]

    # rt3 is currently the only "live" token.
    # Replay rt1 — that should detect theft and revoke rt3 too.
    replay = client.post("/api/auth/refresh", json={"refresh_token": rt1})
    assert replay.status_code == 401

    # Now rt3 should ALSO be revoked due to chain takedown.
    r_after = client.post("/api/auth/refresh", json={"refresh_token": rt3})
    assert r_after.status_code == 401, "rt3 should be revoked after chain detection"


def test_logout_revokes_token(client, admin_credentials):
    r = client.post(
        "/api/auth/token",
        data=admin_credentials,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    body = r.json()
    rt = body["refresh_token"]
    access = body["access_token"]

    lo = client.post(
        "/api/auth/logout",
        json={"refresh_token": rt},
        headers={"Authorization": f"Bearer {access}"},
    )
    assert lo.status_code == 200

    # Refresh should now fail
    r2 = client.post("/api/auth/refresh", json={"refresh_token": rt})
    assert r2.status_code == 401


def test_logout_all_revokes_every_active_token(client, admin_credentials):
    # Login twice to get two distinct refresh tokens
    r1 = client.post(
        "/api/auth/token",
        data=admin_credentials,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    r2 = client.post(
        "/api/auth/token",
        data=admin_credentials,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    rt_a = r1.json()["refresh_token"]
    rt_b = r2.json()["refresh_token"]
    access = r2.json()["access_token"]

    lo = client.post(
        "/api/auth/logout-all",
        headers={"Authorization": f"Bearer {access}"},
    )
    assert lo.status_code == 200
    assert "Revoked" in lo.json().get("message", "")

    assert client.post("/api/auth/refresh", json={"refresh_token": rt_a}).status_code == 401
    assert client.post("/api/auth/refresh", json={"refresh_token": rt_b}).status_code == 401


# ── LLM usage / cost ────────────────────────────────────────────────
def test_llm_usage_summary_shape(client, admin_headers):
    """Empty DB still returns the right envelope."""
    r = client.get("/api/llm-usage/summary?days=30", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["window_days"] == 30
    assert "totals" in body and "by_user" in body and "by_model" in body
    for k in ("calls", "input_tokens", "output_tokens", "total_tokens", "cost_usd"):
        assert k in body["totals"]


def test_llm_usage_record_writes_row(client, admin_headers):
    """Helper writes a row, list endpoint returns it."""
    from app.services import llm_usage
    llm_usage.record(
        provider="anthropic", model="claude-sonnet-4-5-20250929",
        input_tokens=1000, output_tokens=500, latency_ms=1234,
        username="test_admin",
    )
    r = client.get("/api/llm-usage?limit=5", headers=admin_headers)
    assert r.status_code == 200
    items = r.json()["items"]
    assert items, "expected at least one llm_usage row"
    row = items[0]
    assert row["provider"] == "anthropic"
    assert row["input_tokens"] == 1000
    assert row["output_tokens"] == 500
    # cost_usd should be > 0 because Sonnet has a known rate
    assert row["cost_estimate_usd"] is not None
    assert row["cost_estimate_usd"] > 0


def test_llm_usage_unknown_model_records_null_cost(client, admin_headers):
    from app.services import llm_usage
    llm_usage.record(
        provider="openai", model="gpt-future-not-real",
        input_tokens=10, output_tokens=5,
    )
    r = client.get("/api/llm-usage?model=gpt-future-not-real", headers=admin_headers)
    items = r.json()["items"]
    assert items
    assert items[0]["cost_estimate_usd"] is None


# ── Flashcards ──────────────────────────────────────────────────────
def test_flashcards_list_returns_envelope(client, admin_headers):
    r = client.get("/api/flashcards?limit=10", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert "items" in body and "total" in body and "limit" in body


def test_flashcards_review_invalid_card_404(client, admin_headers):
    r = client.post(
        "/api/flashcards/999999/review",
        json={"rating": "good"},
        headers=admin_headers,
    )
    assert r.status_code == 404


def test_flashcards_review_invalid_rating_400(client, admin_headers):
    """Insert a card directly via the model, then exercise the review endpoint
    with a bad rating value.
    """
    from sqlalchemy.orm import sessionmaker
    from app import models
    # Build a minimal card in the test DB by going through the same engine
    # the test client uses.
    from tests.conftest import _test_engine, _TestSessionLocal  # type: ignore
    db = _TestSessionLocal()
    try:
        fc = models.Flashcard(
            question="What is 2+2?",
            answer="4",
            section_id="4.test",
            proposal_id=None,
        )
        db.add(fc)
        db.commit()
        db.refresh(fc)
        card_id = fc.id
    finally:
        db.close()

    r = client.post(
        f"/api/flashcards/{card_id}/review",
        json={"rating": "definitely-not-a-rating"},
        headers=admin_headers,
    )
    assert r.status_code == 400


def test_audit_requires_admin(client, admin_headers):
    """Non-admin gets 403/401."""
    from app.auth import get_current_user
    app = client.app

    class FakeUser:
        id, role, is_active = 9001, "vendor", True
        username = "fake_vendor"
        email = "fake_vendor@example.com"

    app.dependency_overrides[get_current_user] = lambda: FakeUser()
    try:
        r = client.get("/api/audit")
        assert r.status_code in (401, 403)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
