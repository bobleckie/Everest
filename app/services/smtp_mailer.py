"""Optional SMTP mailer for invitation emails.

If `SMTP_HOST` is not set this module is a no-op (`is_configured()`
returns False, `send_invite()` returns False with a logged note).
The router falls back to handing the redeem URL back in the API
response so the admin can paste it into Teams/email.

Env vars:
  SMTP_HOST            (required to enable)
  SMTP_PORT            default 587
  SMTP_USERNAME        optional
  SMTP_PASSWORD        optional
  SMTP_FROM            default = SMTP_USERNAME
  SMTP_USE_TLS         default "true" (use STARTTLS)
"""
from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage
from typing import Optional

logger = logging.getLogger(__name__)


def is_configured() -> bool:
    return bool(os.getenv("SMTP_HOST", "").strip())


def _bool(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes")


def send_invite(
    *,
    to_email: str,
    redeem_url: str,
    inviter_name: Optional[str] = None,
    expires_in_days: int = 7,
) -> bool:
    """Send the invite email. Returns True on send, False on any failure
    (including SMTP not configured). Never raises.
    """
    if not is_configured():
        return False
    host = os.getenv("SMTP_HOST", "").strip()
    try:
        port = int(os.getenv("SMTP_PORT", "587"))
    except ValueError:
        port = 587
    user = os.getenv("SMTP_USERNAME", "").strip() or None
    pw = os.getenv("SMTP_PASSWORD", "").strip() or None
    from_addr = os.getenv("SMTP_FROM", "").strip() or user or "noreply@everest.local"
    use_tls = _bool("SMTP_USE_TLS", "true")

    body = render_invite_body(
        redeem_url=redeem_url,
        inviter_name=inviter_name,
        expires_in_days=expires_in_days,
    )

    msg = EmailMessage()
    msg["Subject"] = "You're invited to Everest (Parsons RFP Platform)"
    msg["From"] = from_addr
    msg["To"] = to_email
    msg.set_content(body)

    try:
        with smtplib.SMTP(host, port, timeout=15) as s:
            if use_tls:
                s.starttls()
            if user and pw:
                s.login(user, pw)
            s.send_message(msg)
        logger.info("[smtp] invite email sent to %s", to_email)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("[smtp] failed to send invite to %s: %s", to_email, e)
        return False


def render_invite_body(
    *,
    redeem_url: str,
    inviter_name: Optional[str],
    expires_in_days: int,
) -> str:
    """Return the plaintext body used both for emailing AND for the
    admin-visible 'paste-this-into-Teams' fallback in the API response.
    """
    inviter = inviter_name or "Your Everest administrator"
    return (
        f"{inviter} has invited you to Everest, the Parsons RFP Response "
        f"Platform.\n\n"
        f"To set up your account, click the link below within "
        f"{expires_in_days} days:\n\n"
        f"  {redeem_url}\n\n"
        f"You'll be asked to set your own password. After that, you can "
        f"sign in at any time at the URL above.\n\n"
        f"If you weren't expecting this invitation, you can safely ignore "
        f"this email — no account changes happen until you click the link.\n\n"
        f"— The Everest team\n"
    )
