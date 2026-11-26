"""AI memory with privacy controls, tied to Saguaro accounts.

- Guests (not signed in): nothing is ever written. Preferences and trips last only for the visit.
- Signed in: preferences and trips are saved to the account (see accounts.py / store.py), under the account's
  random id, so they come back on any device after signing in. Saying "turn off memory" stops learning new
  preferences; "forget me" deletes what was learned and the saved trips (the account stays).
What is kept: cuisine/interest counts from past requests, diet and accessibility needs, liked/disliked place
categories (feedback), favorites, and IDs of places already shown (so repeat plans feel fresh).
"""
from __future__ import annotations

import re

from .store import get_store


def _key(account_id: str, what: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{16,64}", account_id or ""):
        raise ValueError("bad account id")
    return f"{what}/{account_id}.json"


def load_profile(account_id: str | None) -> dict:
    """The saved preferences of a signed-in account (memory on unless they turned it off); guests: empty."""
    if not account_id:
        return {"memory_on": False}
    prof = get_store().get(_key(account_id, "profiles"))
    return prof if isinstance(prof, dict) else {"memory_on": True}


def save_profile(account_id: str | None, profile: dict) -> None:
    if not account_id:
        profile.pop("_forget", None)
        return  # guests: never written
    if profile.get("_forget"):  # "forget me": wipe what was learned, keep the account (memory stays on)
        profile.clear()
        profile["memory_on"] = True
    get_store().put(_key(account_id, "profiles"), {k: v for k, v in profile.items() if not k.startswith("_")})


def load_trips(account_id: str | None) -> list[dict]:
    """The account's saved trips: the summary list only (each trip's full plan loads when it's opened)."""
    if not account_id:
        return []
    data = get_store().get(_key(account_id, "trips"))
    return [dict(t) for t in data if isinstance(t, dict) and t.get("id")] if isinstance(data, list) else []


def _trip_key(account_id: str, trip_id: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{6,32}", trip_id or ""):
        raise ValueError("bad trip id")
    return f"trip-plans/{_key(account_id, 'x')[2:-5]}-{trip_id}.json"


def load_trip_plan(account_id: str | None, trip_id: str) -> dict | None:
    if not account_id:
        return None
    data = get_store().get(_trip_key(account_id, trip_id))
    return data if isinstance(data, dict) else None


def save_trips(account_id: str | None, trips: list[dict]) -> None:
    """Save the trip list (small) and each trip's full plan in its own file (a plan is ~250 KB; one file per trip
    keeps every file well under the storage's 1 MB read limit). Plans of trips that were removed are deleted."""
    if not account_id:
        return
    store = get_store()
    saved = [t for t in (store.get(_key(account_id, "trips")) or []) if isinstance(t, dict)]
    before = {t.get("id") for t in saved}
    # reminders already emailed are recorded by the daily reminder job (scripts/send_reminders.py): keep its record
    sent = {t.get("id"): set(t.get("reminded") or []) for t in saved}
    for t in trips:
        if sent.get(t["id"]):
            t["reminded"] = sorted(set(t.get("reminded") or []) | sent[t["id"]])
    for t in trips:
        if t.get("state") is not None:
            store.put(_trip_key(account_id, t["id"]), t["state"])
    store.put(_key(account_id, "trips"), [{k: v for k, v in t.items() if k != "state"} for t in trips])
    for gone in before - {t["id"] for t in trips}:
        if gone:
            store.delete(_trip_key(account_id, gone))


def delete_all(account_id: str) -> None:
    store = get_store()
    for t in load_trips(account_id):
        store.delete(_trip_key(account_id, t["id"]))
    store.delete(_key(account_id, "profiles"))
    store.delete(_key(account_id, "trips"))


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
