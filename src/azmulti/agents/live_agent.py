"""LIVE INFO agent: checks every chosen stop against Google Places right now.

- permanently / temporarily closed  -> the stop is excluded and sent back to the specialist
- current opening hours             -> replace the 2021 hours (the Route agent uses them)
- price level, website, phone, parking options, "takes reservations" -> added to the stop
Without GOOGLE_PLACES_API_KEY it does nothing (the plan uses the workbook data, as before).
"""
from __future__ import annotations

from .. import live
from ..categories import PRICE_LEVEL_COST
from ..state import PlannerState


def live_node(state: PlannerState) -> dict:
    if not live.status()["Google Places"]:
        return {}
    slots = [dict(s) for s in state["slots"]]
    checked = closed = updated = 0
    for s in slots:
        p = s.get("place")
        if not p or (p.get("live") or {}).get("found") is not None:
            continue  # empty slot, or already checked this turn
        info = live.place_details(p["name"], p.get("address"), p["lat"], p["lon"])
        if info is None:
            continue
        checked += 1
        p = dict(p, live=info)
        if info.get("found"):
            if info.get("status") in ("CLOSED_PERMANENTLY", "CLOSED_TEMPORARILY"):
                # Not open any more: drop it and let the specialist agent pick again.
                closed += 1
                s["exclude"] = list(set(s.get("exclude", [])) | {p["id"]})
                s["closed_note"] = f"{p['name']} is {'permanently' if info['status'] == 'CLOSED_PERMANENTLY' else 'temporarily'} closed (Google, {info['checked']})"
                s.update(place=None, locked=False, reason="", alternatives=s.get("alternatives", []))
                continue
            if info.get("hours"):
                p["hours"] = info["hours"]
                p["hours_source"] = f"Google, checked {info['checked']}"
                updated += 1
            if info.get("price_level") and s["kind"] != "attraction" and not p.get("cost_known"):
                p["price_level"] = info["price_level"]
                p["cost_pp"] = PRICE_LEVEL_COST.get(int(info["price_level"]), p.get("cost_pp"))
                p["cost_known"] = True
            if info.get("reservable") is not None:
                p["accepts_reservations"] = bool(info["reservable"])
        s["place"] = p
    out = {"slots": slots}
    notes = [s.pop("closed_note") for s in slots if s.get("closed_note")]
    if notes:
        out["notes"] = list(state.get("notes", [])) + [n + " - replaced it." for n in notes]
    did = f"checked {checked} stops live: {updated} hours updated, {closed} closed"
    out["trace"] = state.get("trace", []) + [{"agent": "Live Info", "via": "api", "did": did}]
    if closed:  # refill the closed stops right away
        from .specialists import attraction_node, dining_node
        st = dict(state, **out)
        st.update(attraction_node(st))
        st.update(dining_node(st))
        out = {"slots": st["slots"], "trace": st["trace"], **({"notes": out["notes"]} if "notes" in out else {})}
        # check the replacements too (one more pass, cached afterwards)
        st2 = dict(state, **out)
        nxt = live_node(st2) if any(s.get("place") and "live" not in s["place"] for s in st2["slots"]) else {}
        out.update({k: v for k, v in nxt.items() if k in ("slots", "trace")})
    return out
