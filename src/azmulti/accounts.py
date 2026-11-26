"""Saguaro accounts: email + password, with the email confirmed by a 6-digit code.

- Sign-up is two steps: start_signup() checks the details and emails a code; finish_signup() creates the account once
  the code is entered (15 minutes, 5 tries). So nobody can open an account with someone else's email address.
- Forgot password: start_reset() emails a code (the reply is the same whether or not the email has an account, so the
  form can't be used to find out who has one); finish_reset() sets the new password.
- Passwords are never stored: only a salted PBKDF2-SHA256 hash (200,000 rounds), checked in constant time. Codes are
  stored only as hashes too, and only in the visitor's own session until they're used.
- The account file's name is a hash of the lower-case email, so addresses aren't visible in file names. The email is
  kept inside the account so trip confirmations can be sent to it.
- Wrong passwords: 5 misses lock that email for 5 minutes (on this server). Error messages never say whether the
  email or the password was wrong.
- "Keep me signed in": a random token stays in the visitor's browser; only its hash is stored, with a 30-day expiry.
- Preferences and trips are stored under the account's random id (profiles.py); deleting the account deletes them.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time
from datetime import datetime, timezone

from . import mailer
from .store import get_store

MIN_PASSWORD = 8
ROUNDS = 200_000
SESSION_DAYS = 30
MAX_MISSES, LOCK_SECONDS = 5, 300
CODE_MINUTES, CODE_TRIES, RESEND_SECONDS = 15, 5, 45
NAME_RX = re.compile(r"^[\w .'-]{0,40}$", re.UNICODE)

_misses: dict[str, list[float]] = {}
_misses_lock = threading.Lock()


def _mail_live() -> bool:
    """Whether emails really go out (Gmail set up). If not, codes are shown in the app instead."""
    return mailer.configured()


def _norm(email: str) -> str:
    return (email or "").strip().lower()


def _user_key(email: str) -> str:
    return "users/" + hashlib.sha256(_norm(email).encode()).hexdigest()[:32] + ".json"


def _hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), ROUNDS).hex()


def _code_hash(code: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}|{code}".encode()).hexdigest()


def public(rec: dict) -> dict:
    """What the app keeps in memory about a signed-in account (no hash, no salt)."""
    email = rec["email"]
    return {"id": rec["id"], "email": email, "name": rec.get("name") or "",
            "username": rec.get("name") or email.split("@")[0]}  # shown in the header


def check_new(email: str, password: str, confirm: str | None = None, name: str = "") -> str | None:
    """Why these details can't be used for a new account, or None if they're fine."""
    if not mailer.valid_email(email):
        return "Please enter a valid email address."
    if not NAME_RX.match(name or ""):
        return "Names can have letters, spaces, dots, dashes and apostrophes (up to 40)."
    if len(password or "") < MIN_PASSWORD:
        return f"Passwords need at least {MIN_PASSWORD} characters."
    if _norm(password) in (_norm(email), _norm(email).split("@")[0]):
        return "Pick a password that isn't your email."
    if confirm is not None and confirm != password:
        return "The two passwords don't match."
    return None


def _send_code(email: str, code: str, purpose: str) -> tuple[bool, str]:
    what = "finish creating your Saguaro account" if purpose == "signup" else "reset your Saguaro password"
    text = (f"Your Saguaro code is {code}\n\nEnter it in the app to {what}. It expires in {CODE_MINUTES} minutes.\n\n"
            "If you didn't ask for this, you can ignore this email.")
    html = (f"<div style='font-family:system-ui,sans-serif;color:#2A2420;max-width:480px'>"
            f"<h2 style='font-family:Georgia,serif'>Your Saguaro code</h2>"
            f"<p style='font-size:32px;letter-spacing:6px;font-weight:700;margin:18px 0'>{code}</p>"
            f"<p>Enter it in the app to {what}. It expires in {CODE_MINUTES} minutes.</p>"
            "<p style='color:#6E6259;font-size:13px'>If you didn't ask for this, you can ignore this email.</p></div>")
    return mailer.send(email, f"Your Saguaro code: {code}", text, html)


def _new_code(pending: dict, purpose: str) -> tuple[bool, str]:
    if time.time() - pending.get("sent_at", 0) < RESEND_SECONDS:
        return False, f"A code was just sent - wait {RESEND_SECONDS} seconds before asking for another."
    code = f"{secrets.randbelow(10**6):06d}"
    pending.update(code_salt=secrets.token_hex(8), tries=0, sent_at=time.time(), expires=time.time() + CODE_MINUTES * 60)
    pending["code_hash"] = _code_hash(code, pending["code_salt"])
    if purpose == "reset" and not pending.get("exists"):
        return True, "If there's an account for that email, we've sent it a 6-digit code."
    ok, msg = _send_code(pending["email"], code, purpose)
    if not ok:
        return False, msg
    if not _mail_live():  # nothing is emailed on this copy of the app, so show the code instead
        return True, f"Email isn't set up on this copy of the app, so nothing was sent - your code is {code}."
    return True, ("We sent a 6-digit code to " + pending["email"] + "." if purpose == "signup" else
                  "If there's an account for that email, we've sent it a 6-digit code.")


def _check_code(pending: dict, code: str) -> str | None:
    if time.time() > pending.get("expires", 0):
        return "That code has expired - ask for a new one."
    if pending.get("tries", 0) >= CODE_TRIES:
        return "Too many wrong codes - ask for a new one."
    pending["tries"] = pending.get("tries", 0) + 1
    if not pending.get("code_hash") or not hmac.compare_digest(
            _code_hash((code or "").strip(), pending.get("code_salt", "")), pending["code_hash"]):
        return "That code isn't right - check the email and try again."
    return None


# ---------------------------------------------------------------- sign up
def start_signup(email: str, password: str, confirm: str | None = None, name: str = "") -> tuple[bool, str, dict | None]:
    """Check the details and email a 6-digit code. Returns the pending sign-up (kept in the visitor's session)."""
    email, name = _norm(email), (name or "").strip()
    problem = check_new(email, password, confirm, name)
    if problem:
        return False, problem, None
    if get_store().get(_user_key(email)):
        return False, "There's already an account for that email - sign in instead (or reset your password).", None
    salt = secrets.token_hex(16)
    pending = {"email": email, "name": name, "salt": salt, "hash": _hash(password, salt)}  # never the password itself
    ok, msg = _new_code(pending, "signup")
    return ok, msg, pending if ok else None


def resend_code(pending: dict, purpose: str = "signup") -> tuple[bool, str]:
    return _new_code(pending, purpose)


def finish_signup(pending: dict, code: str) -> tuple[bool, str, dict | None]:
    problem = _check_code(pending, code)
    if problem:
        return False, problem, None
    store = get_store()
    key = _user_key(pending["email"])
    if store.get(key):
        return False, "There's already an account for that email - sign in instead.", None
    rec = {"id": secrets.token_hex(12), "email": pending["email"], "name": pending.get("name") or "",
           "salt": pending["salt"], "hash": pending["hash"], "rounds": ROUNDS, "email_confirmed": True,
           "created": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    store.put(key, rec)
    pending.clear()
    return True, f"Welcome{', ' + rec['name'] if rec['name'] else ''}! Your account is ready.", public(rec)


# ---------------------------------------------------------------- sign in
def locked_for(email: str) -> int:
    """Seconds until this email may try again (0 = not locked)."""
    u = _norm(email)
    with _misses_lock:
        recent = [t for t in _misses.get(u, []) if time.time() - t < LOCK_SECONDS]
        _misses[u] = recent
        return int(LOCK_SECONDS - (time.time() - recent[0])) + 1 if len(recent) >= MAX_MISSES else 0


def sign_in(email: str, password: str) -> tuple[bool, str, dict | None]:
    email = _norm(email)
    wait = locked_for(email)
    if wait:
        return False, f"Too many tries for this email - wait {max(1, wait // 60)} min, or reset your password.", None
    rec = get_store().get(_user_key(email)) if mailer.valid_email(email) else None
    # always do the (slow) hash, so a missing account takes as long as a wrong password
    salt = (rec or {}).get("salt") or "00" * 16
    ok = hmac.compare_digest(_hash(password or "", salt), (rec or {}).get("hash") or "x")
    if not (rec and ok):
        with _misses_lock:
            _misses.setdefault(email, []).append(time.time())
        return False, "That email and password don't match.", None
    with _misses_lock:
        _misses.pop(email, None)
    return True, f"Welcome back{', ' + rec['name'] if rec.get('name') else ''}!", public(rec)


# ---------------------------------------------------------------- passwords
def _set_password(email: str, new: str) -> None:
    store = get_store()
    key = _user_key(email)
    rec = dict(store.get(key) or {})
    rec["salt"] = secrets.token_hex(16)
    rec["hash"] = _hash(new, rec["salt"])
    store.put(key, rec)


def change_password(account: dict, old: str, new: str, confirm: str) -> tuple[bool, str]:
    ok, _, _ = sign_in(account["email"], old)
    if not ok:
        return False, "Your current password isn't right."
    problem = check_new(account["email"], new, confirm)
    if problem:
        return False, problem
    _set_password(account["email"], new)
    return True, "Password changed."


def start_reset(email: str) -> tuple[bool, str, dict | None]:
    """Email a code to reset the password. Same answer whether or not the email has an account."""
    email = _norm(email)
    if not mailer.valid_email(email):
        return False, "Please enter a valid email address.", None
    pending = {"email": email, "exists": bool(get_store().get(_user_key(email)))}
    ok, msg = _new_code(pending, "reset")
    return ok, msg, pending if ok else None


def finish_reset(pending: dict, code: str, new: str, confirm: str) -> tuple[bool, str]:
    problem = _check_code(pending, code) if pending.get("exists") else "That code isn't right - check the email and try again."
    if problem:
        return False, problem
    problem = check_new(pending["email"], new, confirm)
    if problem:
        pending["tries"] = max(0, pending.get("tries", 1) - 1)  # the code was right; let them fix the password
        return False, problem
    _set_password(pending["email"], new)
    with _misses_lock:
        _misses.pop(pending["email"], None)
    pending.clear()
    return True, "Password changed - you can sign in with it now."


# ---------------------------------------------------------------- keep me signed in
def _session_key(token: str) -> str:
    return "sessions/" + hashlib.sha256(token.encode()).hexdigest()[:40] + ".json"


def new_session(account: dict) -> str:
    token = secrets.token_urlsafe(32)
    get_store().put(_session_key(token), {"id": account["id"], "email": account["email"],
                                          "expires": time.time() + SESSION_DAYS * 86400})
    return token


def resume_session(token: str | None) -> dict | None:
    """The account a 'keep me signed in' token belongs to, if it's valid and not expired."""
    if not token or not re.fullmatch(r"[A-Za-z0-9_-]{20,100}", token):
        return None
    rec = get_store().get(_session_key(token))
    if not rec or rec.get("expires", 0) < time.time() or not rec.get("email"):
        return None
    user = get_store().get(_user_key(rec["email"]))  # the account may have been deleted since
    return public(user) if user and user.get("id") == rec.get("id") else None


def end_session(token: str | None) -> None:
    if token and re.fullmatch(r"[A-Za-z0-9_-]{20,100}", token):
        get_store().delete(_session_key(token))


def delete_account(account: dict, token: str | None = None) -> None:
    """Delete the account and everything saved under it (preferences, trips, this device's sign-in)."""
    from .profiles import delete_all
    end_session(token)
    delete_all(account["id"])
    get_store().delete(_user_key(account["email"]))
