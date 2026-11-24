"""AI memory with privacy controls.

- Off by default. Nothing is written to disk unless the traveler turns memory on.
- Stored locally only, under data/profiles/. The file name is a salted hash of (name, PIN): the name
  alone can't open someone's profile, and names are not visible on disk.
- "Forget me" deletes the file.
What is kept: cuisine/interest counts from past requests, diet and accessibility needs,
liked/disliked place categories (feedback), favorites, and IDs of places already shown
(past-experience tracking, so repeat plans feel fresh).
"""
from __future__ import annotations

import hashlib
import json

from .config import DATA_DIR

PROFILE_DIR = DATA_DIR / "profiles"


def _path(name: str | None, pin: str | None = ""):
    ident = hashlib.pbkdf2_hmac("sha256", f"{(name or 'default').strip().lower()}|{pin or ''}".encode(),
                                b"saguaro-profile-v1", 50_000).hex()[:40]
    return PROFILE_DIR / f"{ident}.json"


def load_profile(name: str | None, pin: str | None = "") -> dict:
    p = _path(name, pin)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {"memory_on": False}
    return {"memory_on": False}


def save_profile(name: str | None, profile: dict, pin: str | None = "") -> None:
    p = _path(name, pin)
    if profile.get("_forget"):
        if p.exists():
            p.unlink()
        profile.pop("_forget", None)
        return
    if not profile.get("memory_on"):
        return  # privacy: never write unless the traveler opted in
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(profile, indent=1), encoding="utf-8")


def _trips_path(name: str | None, pin: str | None = ""):
    return _path(name, pin).with_suffix(".trips.json")


def load_trips(name: str | None, pin: str | None = "") -> list[dict]:
    """Past trips (only stored when memory is on)."""
    p = _trips_path(name, pin)
    try:
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_trips(name: str | None, trips: list[dict], profile: dict, pin: str | None = "") -> None:
    p = _trips_path(name, pin)
    if profile.get("_forget") or not profile.get("memory_on"):
        if profile.get("_forget") and p.exists():
            p.unlink()
        return  # privacy: never write unless the traveler opted in
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(trips, default=str), encoding="utf-8")


def update_from_turn(profile: dict, out: dict) -> dict:
    """Learn from a planning turn (only when memory is on)."""
    profile = dict(profile)
    if profile.get("_forget"):
        return {"memory_on": False, "_forget": True}  # save_profile() deletes the file
    if not profile.get("memory_on") or out.get("intent") not in ("new_plan", "revise", "tools"):
        return profile
    req = out.get("request") or {}
    if out.get("intent") == "new_plan":
        for c in req.get("cuisines", []):
            profile.setdefault("cuisines", {})[c] = profile.get("cuisines", {}).get(c, 0) + 1
        for i in req.get("interests", []):
            profile.setdefault("interests", {})[i] = profile.get("interests", {}).get(i, 0) + 1
        if req.get("dietary"):
            profile["dietary"] = sorted(set(profile.get("dietary", [])) | set(req["dietary"]))
        if req.get("wheelchair"):
            profile["wheelchair"] = True
    shown = [s["place"]["id"] for s in out.get("slots") or [] if s.get("place")]
    profile["history"] = list(dict.fromkeys(profile.get("history", []) + shown))[-300:]
    return profile
