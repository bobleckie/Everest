"""
TLS / SSL helpers for outbound LLM API calls.

Corporate networks frequently use TLS-inspecting proxies that present a
self-signed root CA to clients. Python's `ssl` module then fails with
`CERTIFICATE_VERIFY_FAILED: self-signed certificate in certificate chain`,
which bubbles up from the Anthropic / OpenAI SDK as a generic exception and
causes our pipeline to silently fall back to the "[MOCK RESPONSE]" placeholder.

Options this module supports (highest precedence first):
  1. App-setting `ssl_verify` (bool, stored in app_settings table)
     - `true`  → verify TLS normally
     - `false` → disable verification (insecure, use only on trusted corp nets)
  2. Env var `EVEREST_SSL_VERIFY=0` → disable verification
  3. Env var `REQUESTS_CA_BUNDLE` / `SSL_CERT_FILE` pointing at the corporate
     root CA bundle → verify against that bundle
  4. Default: verify using the system default trust store
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


def _load_ssl_verify_setting() -> Optional[bool]:
    """Return the DB-stored ssl_verify flag, or None if unset."""
    try:
        from ..database import SessionLocal
        from ..models import AppSetting

        s = SessionLocal()
        try:
            row = s.query(AppSetting).filter(AppSetting.key == "ssl_verify").first()
            if not row or not row.value_json:
                return None
            try:
                val = json.loads(row.value_json)
            except (json.JSONDecodeError, TypeError):
                val = row.value_json
            if isinstance(val, bool):
                return val
            if isinstance(val, str):
                return val.strip().lower() not in ("false", "0", "no", "off", "")
        finally:
            s.close()
    except Exception as exc:
        logger.debug(f"Could not read ssl_verify from app_settings: {exc}")
    return None


def resolve_verify() -> bool | str:
    """Return a value suitable for httpx `verify=` / `OpenAI(http_client=...)`.

    Returns:
      - `False`          → verification disabled
      - path to CA bundle (str) → verify against that bundle
      - `True`           → verify using system defaults
    """
    db_flag = _load_ssl_verify_setting()
    if db_flag is False:
        return False
    if db_flag is True:
        # explicitly opted in — still allow a custom CA bundle below
        pass

    if os.getenv("EVEREST_SSL_VERIFY", "").strip() in ("0", "false", "no", "off"):
        return False

    ca_bundle = (
        os.getenv("REQUESTS_CA_BUNDLE")
        or os.getenv("SSL_CERT_FILE")
        or os.getenv("CURL_CA_BUNDLE")
    )
    if ca_bundle and os.path.exists(ca_bundle):
        return ca_bundle

    return True


def make_sync_httpx_client(timeout: float = 120.0) -> httpx.Client:
    """Build an httpx.Client configured for the current SSL policy."""
    verify = resolve_verify()
    if verify is False:
        # Silence the single-line warning httpx emits per request
        import urllib3
        try:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        except Exception:
            pass
    return httpx.Client(verify=verify, timeout=timeout)


def make_async_httpx_client(timeout: float = 120.0) -> httpx.AsyncClient:
    """Build an httpx.AsyncClient configured for the current SSL policy."""
    verify = resolve_verify()
    if verify is False:
        import urllib3
        try:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        except Exception:
            pass
    return httpx.AsyncClient(verify=verify, timeout=timeout)
