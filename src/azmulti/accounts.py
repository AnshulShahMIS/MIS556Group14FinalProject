"""Saguaro accounts: username + password.

- Passwords are never stored: only a salted PBKDF2-SHA256 hash (200,000 rounds), checked in constant time.
- The account file's name is a hash of the lower-case username, so usernames aren't visible in file names.
- Wrong passwords: after 5 misses for a username, that username is locked for 5 minutes (on this server).
  Error messages never say whether the username or the password was wrong.
- "Keep me signed in": a random token is kept in the visitor's browser; only its hash is stored, with an expiry
  (30 days). Signing out deletes it.
- No email: there's no password reset, so the sign-up form tells people to keep their password somewhere safe.
- Each account's preferences and trips are stored under its random id (see profiles.py). Deleting the account
  deletes all of it.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time
from datetime import datetime, timezone

from .store import get_store

USERNAME_RX = re.compile(r"^[A-Za-z0-9._-]{3,30}$")
MIN_PASSWORD = 8
ROUNDS = 200_000
SESSION_DAYS = 30
MAX_MISSES, LOCK_SECONDS = 5, 300

_misses: dict[str, list[float]] = {}
_misses_lock = threading.Lock()


def _user_key(username: str) -> str:
    return "users/" + hashlib.sha256(username.strip().lower().encode()).hexdigest()[:32] + ".json"


def _hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), ROUNDS).hex()


def public(rec: dict) -> dict:
    """What the app keeps in memory about a signed-in account (no hash, no salt)."""
    return {"id": rec["id"], "username": rec["username"]}


def check_new(username: str, password: str, confirm: str | None = None) -> str | None:
    """Why this username/password can't be used, or None if they're fine."""
    if not USERNAME_RX.match(username or ""):
        return "Usernames are 3-30 letters, numbers, dots, dashes or underscores."
    if len(password or "") < MIN_PASSWORD:
        return f"Passwords need at least {MIN_PASSWORD} characters."
    if password.strip().lower() == username.strip().lower():
        return "Pick a password that isn't your username."
    if confirm is not None and confirm != password:
        return "The two passwords don't match."
    return None


def create_account(username: str, password: str, confirm: str | None = None) -> tuple[bool, str, dict | None]:
    username = (username or "").strip()
    problem = check_new(username, password, confirm)
    if problem:
        return False, problem, None
    store = get_store()
    key = _user_key(username)
    if store.get(key):
        return False, "That username is taken - try another, or sign in.", None
    salt = secrets.token_hex(16)
    rec = {"id": secrets.token_hex(12), "username": username, "salt": salt, "hash": _hash(password, salt),
           "rounds": ROUNDS, "created": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    store.put(key, rec)
    return True, f"Welcome, {username}! Your account is ready.", public(rec)


def locked_for(username: str) -> int:
    """Seconds until this username may try again (0 = not locked)."""
    u = (username or "").strip().lower()
    with _misses_lock:
        recent = [t for t in _misses.get(u, []) if time.time() - t < LOCK_SECONDS]
        _misses[u] = recent
        return int(LOCK_SECONDS - (time.time() - recent[0])) + 1 if len(recent) >= MAX_MISSES else 0


def sign_in(username: str, password: str) -> tuple[bool, str, dict | None]:
    username = (username or "").strip()
    wait = locked_for(username)
    if wait:
        return False, f"Too many tries for this username - wait {max(1, wait // 60)} min and try again.", None
    rec = get_store().get(_user_key(username)) if USERNAME_RX.match(username) else None
    # always do the (slow) hash, so a missing username takes as long as a wrong password
    salt = (rec or {}).get("salt") or "00" * 16
    ok = hmac.compare_digest(_hash(password or "", salt), (rec or {}).get("hash") or "x")
    if not (rec and ok):
        with _misses_lock:
            _misses.setdefault(username.lower(), []).append(time.time())
        return False, "That username and password don't match.", None
    with _misses_lock:
        _misses.pop(username.lower(), None)
    return True, f"Welcome back, {rec['username']}!", public(rec)


def change_password(account: dict, old: str, new: str, confirm: str) -> tuple[bool, str]:
    ok, msg, _ = sign_in(account["username"], old)
    if not ok:
        return False, "Your current password isn't right."
    problem = check_new(account["username"], new, confirm)
    if problem:
        return False, problem
    store = get_store()
    key = _user_key(account["username"])
    rec = dict(store.get(key) or {})
    rec["salt"] = secrets.token_hex(16)
    rec["hash"] = _hash(new, rec["salt"])
    store.put(key, rec)
    return True, "Password changed."


# ---------------------------------------------------------------- keep me signed in
def _session_key(token: str) -> str:
    return "sessions/" + hashlib.sha256(token.encode()).hexdigest()[:40] + ".json"


def new_session(account: dict) -> str:
    token = secrets.token_urlsafe(32)
    get_store().put(_session_key(token), {"id": account["id"], "username": account["username"],
                                          "expires": time.time() + SESSION_DAYS * 86400})
    return token


def resume_session(token: str | None) -> dict | None:
    """The account a 'keep me signed in' token belongs to, if it's valid and not expired."""
    if not token or not re.fullmatch(r"[A-Za-z0-9_-]{20,100}", token):
        return None
    rec = get_store().get(_session_key(token))
    if not rec or rec.get("expires", 0) < time.time():
        return None
    user = get_store().get(_user_key(rec["username"]))  # the account may have been deleted since
    return public(user) if user and user.get("id") == rec.get("id") else None


def end_session(token: str | None) -> None:
    if token and re.fullmatch(r"[A-Za-z0-9_-]{20,100}", token):
        get_store().delete(_session_key(token))


def delete_account(account: dict, token: str | None = None) -> None:
    """Delete the account and everything saved under it (preferences, trips, this device's sign-in)."""
    from .profiles import delete_all
    end_session(token)
    delete_all(account["id"])
    get_store().delete(_user_key(account["username"]))
