"""Sending email: sign-up / password-reset codes and trip confirmations.

- Gmail SMTP when AZ_SMTP_USER (the Gmail address) and AZ_SMTP_PASSWORD (a Gmail *app password*, not the account
  password) are set, e.g. in Streamlit secrets. Gmail allows roughly 500 messages a day.
- Otherwise (running locally, tests): each message is written to data/outbox/ as an .eml file instead of being sent,
  so the whole flow can be tried without an email account. The app says so.
The password is read from the environment only and is never logged, shown or stored.
"""
from __future__ import annotations

import logging
import os
import re
import smtplib
import ssl
import time
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path

from .config import DATA_DIR

log = logging.getLogger("mailer")
EMAIL_RX = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$")
OUTBOX = Path(os.getenv("AZ_OUTBOX", DATA_DIR / "outbox"))


def configured() -> bool:
    return bool(os.getenv("AZ_SMTP_USER") and os.getenv("AZ_SMTP_PASSWORD")) and os.getenv("AZ_OFFLINE", "0") != "1"


def valid_email(addr: str) -> bool:
    return bool(EMAIL_RX.match((addr or "").strip())) and ".." not in addr


def send(to: str, subject: str, text: str, html: str | None = None,
         attachments: list[tuple[str, str, bytes]] | None = None) -> tuple[bool, str]:
    """Send one email. attachments: (filename, mime type, bytes). Returns (ok, message for the visitor)."""
    to = (to or "").strip()
    if not valid_email(to):
        return False, "That email address doesn't look right."
    sender = os.getenv("AZ_SMTP_USER", "saguaro@localhost")
    msg = EmailMessage()
    msg["Subject"] = subject[:150]
    msg["From"] = formataddr((os.getenv("AZ_MAIL_FROM_NAME", "Saguaro trip planner"), sender))
    msg["To"] = to
    msg["Message-ID"] = make_msgid(domain="saguaro.app")
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    for name, mime, data in attachments or []:
        maintype, _, subtype = mime.partition("/")
        msg.add_attachment(data, maintype=maintype, subtype=subtype or "octet-stream", filename=name)
    if not configured():
        OUTBOX.mkdir(parents=True, exist_ok=True)
        (OUTBOX / f"{int(time.time() * 1000)}.eml").write_bytes(bytes(msg))
        return True, "Email saved to the outbox (sending isn't set up on this copy of the app)."
    try:
        with smtplib.SMTP_SSL(os.getenv("AZ_SMTP_HOST", "smtp.gmail.com"), int(os.getenv("AZ_SMTP_PORT", "465")),
                              context=ssl.create_default_context(), timeout=20) as s:
            s.login(sender, os.environ["AZ_SMTP_PASSWORD"])
            s.send_message(msg)
        return True, f"Sent to {to}."
    except smtplib.SMTPAuthenticationError:
        log.error("email: sign-in to the mail server failed (check AZ_SMTP_USER / AZ_SMTP_PASSWORD)")
        return False, "Sorry - email isn't working right now. Please try again later."
    except Exception as e:  # never include the password or full error text in what the visitor sees
        log.error("email to %s failed: %s", to.split("@")[-1], type(e).__name__)
        return False, "Sorry - the email couldn't be sent. Please try again in a minute."


def outbox_messages() -> list[Path]:
    """Messages saved locally instead of sent (newest last) - for testing."""
    return sorted(OUTBOX.glob("*.eml")) if OUTBOX.exists() else []
