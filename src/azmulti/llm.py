"""Thin wrapper around GPT-4o (LangChain). Every caller passes a deterministic fallback, so the graph
runs end-to-end with no API key (AZ_OFFLINE=1 forces that, e.g. in tests).

Bring-your-own-key: each browser session can supply its own OpenAI key. The key lives only in a
context variable for the duration of that session's request (set by Planner.ask) - it is never written
to the graph state, checkpoints, shared plans, profiles, logs or files. Errors are redacted.
Set AZ_REQUIRE_USER_KEY=1 on a public deployment so the server's own key is never used for visitors.
"""
from __future__ import annotations

import contextvars
import hashlib
import logging
import os
from typing import Callable, TypeVar

from pydantic import BaseModel

from . import config
from .security import redact

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

_session_key: contextvars.ContextVar[str | None] = contextvars.ContextVar("openai_session_key", default=None)
_errors: contextvars.ContextVar[list | None] = contextvars.ContextVar("llm_errors", default=None)
_CLIENTS: dict[str, object] = {}  # keyed by a hash of the key, never by the key itself


def set_session_key(key: str | None):
    """Returns a token for reset_session_key()."""
    return _session_key.set(key or None)


def reset_session_key(token) -> None:
    _session_key.reset(token)


def start_error_capture():
    return _errors.set([])


def captured_errors() -> list[str]:
    return list(_errors.get() or [])


def _active_key() -> str | None:
    if os.getenv("AZ_OFFLINE", "0") == "1":
        return None
    k = _session_key.get()
    if k:
        return k
    if os.getenv("AZ_REQUIRE_USER_KEY", "0") == "1":
        return None
    return os.getenv("OPENAI_API_KEY") or None if config.USE_LLM else None


def llm_enabled() -> bool:
    errs = _errors.get() or []
    if any("rejected" in e or "quota" in e for e in errs):
        return False  # don't keep retrying a bad key within the same request
    return _active_key() is not None


def using_session_key() -> bool:
    return bool(_session_key.get())


def _make_client(key: str, temperature: float):
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(model=config.OPENAI_MODEL, temperature=temperature, timeout=60, max_retries=1, api_key=key)


def get_llm(temperature: float = 0.2):
    key = _active_key()
    ident = hashlib.sha256(f"{key}|{temperature}".encode()).hexdigest()
    if ident not in _CLIENTS:
        if len(_CLIENTS) > 64:
            _CLIENTS.clear()
        _CLIENTS[ident] = _make_client(key, temperature)
    return _CLIENTS[ident]


def _record(e: Exception) -> None:
    msg = redact(e)
    low = msg.lower()
    kind = ("Your OpenAI key was rejected (invalid or revoked)." if "401" in msg or "incorrect api key" in low or "invalid_api_key" in low
            else "Your OpenAI key has no remaining quota or billing is not set up." if "429" in msg and "quota" in low
            else "OpenAI is rate-limiting requests right now." if "429" in msg
            else "Couldn't reach OpenAI.")
    log.warning("LLM call failed, using rules: %s", msg[:300])
    errs = _errors.get()
    if errs is not None and kind not in errs:
        errs.append(kind)


def structured(system: str, user: str, schema: type[T], fallback: Callable[[], T]) -> tuple[T, str]:
    """Ask the LLM to fill `schema`. Returns (result, "llm"|"rules")."""
    if llm_enabled():
        try:
            out = get_llm().with_structured_output(schema).invoke([("system", system), ("human", user)])
            if out is not None:
                return out, "llm"
        except Exception as e:  # network, auth, quota, schema errors -> degrade gracefully
            _record(e)
    return fallback(), "rules"


def text(system: str, user: str, fallback: Callable[[], str]) -> tuple[str, str]:
    if llm_enabled():
        try:
            return get_llm(0.5).invoke([("system", system), ("human", user)]).content, "llm"
        except Exception as e:
            _record(e)
    return fallback(), "rules"


def verify_key(key: str) -> tuple[bool | None, str]:
    """Cheap check (lists models, no token cost). Returns (ok, message); ok=None if OpenAI is unreachable."""
    import json
    import urllib.error
    import urllib.request
    req = urllib.request.Request("https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            ids = {m.get("id") for m in json.loads(r.read().decode()).get("data", [])}
        if ids and config.OPENAI_MODEL not in ids:
            return True, f"Key works, but it can't use {config.OPENAI_MODEL}; requests may fail."
        return True, "Key verified with OpenAI."
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return False, "OpenAI rejected this key (invalid or revoked)."
        if e.code == 429:
            return True, "Key accepted, but OpenAI reports rate limits or no quota right now."
        return None, f"OpenAI returned HTTP {e.code}; the key will be checked on your first AI request."
    except Exception:
        return None, "Couldn't reach OpenAI to verify; the key will be checked on your first AI request."
