"""What changed between two versions of an itinerary (for the Itinerary page's "Updated" badges
and change log). Works for your own edits and for edits synced from your group."""
from __future__ import annotations

import time


def _stops(slots):
    return {s["id"]: s for s in slots or [] if s.get("place")}


def diff_plans(old: dict | None, new: dict | None) -> dict:
    """Returns {"summary": [...lines], "changed": set(slot ids), "new_trip": bool}."""
    old, new = old or {}, new or {}
    a, b = _stops(old.get("slots")), _stops(new.get("slots"))
    if not b:
        return {"summary": [], "changed": set(), "new_trip": False}
    ra, rb = old.get("request") or {}, new.get("request") or {}
    if not a:
        return {"summary": [f"New itinerary for {rb.get('city', 'your trip')}"], "changed": set(b), "new_trip": True}
    summary, changed = [], set()
    if ra.get("city") != rb.get("city"):
        return {"summary": [f"New itinerary for {rb.get('city')}"], "changed": set(b), "new_trip": True}
    for key, label in (("weekday", "day"), ("trip_date", "date"), ("party_size", "group size"),
                       ("budget_per_person", "budget"), ("transport", "getting around")):
        if ra.get(key) != rb.get(key) and rb.get(key) is not None:
            summary.append(f"Changed the {label}")
    for sid, s in b.items():
        name = s["place"]["name"]
        if sid not in a:
            summary.append(f"Added {s['label'].lower()}: {name}")
            changed.add(sid)
        elif a[sid]["place"]["id"] != s["place"]["id"]:
            summary.append(f"{s['label']}: {a[sid]['place']['name']} → {name}")
            changed.add(sid)
        elif a[sid].get("time") != s.get("time") and s.get("time"):
            summary.append(f"{s['label']} now {s['time']}")
            changed.add(sid)
    for sid, s in a.items():
        if sid not in b:
            summary.append(f"Removed {s['label'].lower()} ({s['place']['name']})")
    swapped = sum(1 for x in summary if "→" in x)
    if swapped >= max(3, int(0.7 * len(b))) and not any(x.startswith("Changed") for x in summary):
        return {"summary": ["Rebuilt the whole day"], "changed": changed, "new_trip": False}
    return {"summary": summary, "changed": changed, "new_trip": False}


def record(history: list, diff: dict, who: str = "You") -> list:
    """Append a change-log entry (newest last, keeps 25)."""
    if not diff["summary"]:
        return history
    return (history + [{"t": time.time(), "who": who, "what": diff["summary"]}])[-25:]
