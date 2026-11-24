"""Shared itineraries + collaborative editing + group voting across devices.

A plan is shared under a short code (e.g. "SAG-7K3Q"). Everyone who joins with that code works on
the same plan: each person's chat edits are saved back, others press "Sync" (or the app polls) to see
the latest version, and group votes are pooled in the same record. Stored as JSON under
data/shared/ - on the deployed app (Streamlit Community Cloud) all users share that folder.
Only the itinerary is shared, never anyone's memory profile.
"""
from __future__ import annotations

import json
import os
import secrets
import re
import string
import time

from .config import DATA_DIR
from .security import clean_name

SHARED_DIR = DATA_DIR / "shared"
SHARE_KEYS = ("request", "slots", "budget", "quality", "route", "issues", "booking", "notes", "conditions", "events",
              "options", "reservations", "answer", "history")


def _path(code: str):
    return SHARED_DIR / f"{re.sub(r'[^A-Z0-9-]', '', code.upper())}.json"


def new_code() -> str:
    alphabet = string.ascii_uppercase.replace("O", "").replace("I", "") + "23456789"
    while True:
        # 8 random chars from 32 symbols = 2^40 codes (cryptographic RNG) - not guessable by trying codes
        code = "SAG-" + "".join(secrets.choice(alphabet) for _ in range(8))
        if not _path(code).exists():
            return code


MAX_BYTES = 2_000_000  # a shared plan is a few KB; refuse anything absurd (disk abuse)


def valid_code(code: str | None) -> bool:
    return bool(code) and re.fullmatch(r"SAG-[A-Z0-9]{8}", code.strip().upper()) is not None


def save(code: str, state: dict, editor: str | None = None, votes: dict | None = None) -> dict:
    if not valid_code(code):
        raise ValueError("bad share code")
    editor = clean_name(editor)
    SHARED_DIR.mkdir(parents=True, exist_ok=True)
    old = load(code) or {}
    rec = {"code": code, "version": old.get("version", 0) + 1, "updated": time.time(), "editor": editor or "someone",
           "state": {k: state.get(k) for k in SHARE_KEYS if k in state},
           "votes": votes if votes is not None else old.get("votes", {}),
           "log": (old.get("log", []) + [{"t": time.time(), "who": editor or "someone",
                                          "what": (state.get("history") or [{}])[-2].get("content", "")[:80] if state.get("history") else "shared"}])[-30:]}
    blob = json.dumps(rec, default=str)
    if len(blob) > MAX_BYTES:
        raise ValueError("shared plan too large")
    tmp = _path(code).with_suffix(".tmp")
    tmp.write_text(blob, encoding="utf-8")
    os.replace(tmp, _path(code))  # atomic: readers never see half a file
    return rec


def load(code: str) -> dict | None:
    if not valid_code(code):
        return None
    p = _path(code)
    if not p.exists() or p.stat().st_size > MAX_BYTES:
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def vote(code: str, slot_id: str, place_id: str, voter: str) -> dict | None:
    """One vote per person per stop (re-voting moves your vote)."""
    rec = load(code)
    if not rec:
        return None
    voter = clean_name(voter)
    votes = rec.setdefault("votes", {})
    if len(votes) > 50 or len(str(slot_id)) > 40 or len(str(place_id)) > 80:
        return rec
    stop = votes.setdefault(slot_id, {})
    for pid, voters in stop.items():
        if voter in voters:
            voters.remove(voter)
    stop.setdefault(place_id, []).append(voter)
    rec["updated"] = time.time()
    tmp = _path(code).with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, default=str), encoding="utf-8")
    os.replace(tmp, _path(code))
    return rec
