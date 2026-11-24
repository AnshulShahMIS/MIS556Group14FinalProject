"""ROUTE & SCHEDULE agent and BUDGET agent (deterministic tools - numbers should not be
hallucinated, so no LLM here)."""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta

from .. import live

from .. import hours as H
from ..catalog import get_catalog, haversine_km
from ..config import CITY_SPEED_KMH, HIGHWAY_SPEED_KMH, PARKING_MIN, ROAD_FACTOR
from ..state import PlannerState
from ..units import dist

COST_PER_KM = 0.40      # gas + wear, per car
PEOPLE_PER_CAR = 5
MEAL_FACTOR = {"breakfast": 0.7, "lunch": 0.85, "dinner": 1.0, "dessert": 0.4}


RIDESHARE_BASE, RIDESHARE_PER_KM = 4.0, 1.25    # rough Phoenix-area UberX/Lyft averages
TRANSIT_FARE = 2.0                                # Valley Metro / Sun Tran single ride, approx.
WALK_KMH, TRANSIT_KMH, TRANSIT_WAIT = 4.8, 20, 10


def drive(a: dict | None, b: dict | None) -> tuple[float, int]:
    """(road km, minutes) between two places; 0 if either is missing."""
    if not a or not b:
        return 0.0, 0
    crow = float(haversine_km(a["lat"], a["lon"], b["lat"], b["lon"]))
    km = crow * ROAD_FACTOR
    if km < 0.4:
        return round(km, 1), 3  # walkable
    speed = CITY_SPEED_KMH if km < 25 else HIGHWAY_SPEED_KMH
    if km > 80:  # city-to-city: interstates are straighter and faster (Phoenix-Sedona ~ 115 mi, ~2 h)
        km, speed = crow * 1.2, 105
    return round(km, 1), int(round(km / speed * 60 + PARKING_MIN))


def leg_times(km: float, drive_min: int) -> dict:
    """Driving, walking and transit estimates for one leg (no live data - rough averages)."""
    return {"drive": drive_min, "walk": int(round(km / WALK_KMH * 60)),
            "transit": int(round(km / TRANSIT_KMH * 60 + TRANSIT_WAIT)) if km > 0.8 else int(round(km / WALK_KMH * 60))}


def travel(a: dict | None, b: dict | None, mode: str, weekday: int | None, depart_min: int,
           trip_day: date | None = None, info: dict | None = None) -> tuple[float, int]:
    km, mins = drive(a, b)
    if not a or not b:
        return km, 0
    if mode == "walking":
        return km, leg_times(km, mins)["walk"]
    if mode == "transit":
        return km, leg_times(km, mins)["transit"]
    # Live / predicted traffic (TomTom) when a key is configured; otherwise the rush-hour heuristic below.
    if km >= 0.4 and live.status()["Traffic (TomTom)"]:
        depart = datetime.combine(trip_day, datetime.min.time(), tzinfo=live.AZ_TZ) + timedelta(minutes=depart_min) if trip_day else None
        r = live.traffic_route(a["lat"], a["lon"], b["lat"], b["lon"], depart)
        if r:
            if info is not None:
                info.update(r)
            extra = PARKING_MIN if mode == "car" else 4
            return r["km"], int(r["minutes"]) + extra
    # Traffic-aware (heuristic): weekday rush hours are ~35% slower. Rideshare skips parking.
    rush = weekday is not None and weekday < 5 and (420 <= depart_min <= 540 or 960 <= depart_min <= 1110)
    if rush and km >= 0.4:  # only the in-town part (about the first half hour) is slowed by rush hour
        mins = int(round(mins + min(mins - PARKING_MIN, 30) * 0.35))
    if mode == "rideshare" and km >= 0.4:
        mins = max(3, mins - PARKING_MIN + 4)  # wait for pickup instead of parking
    return km, mins


def _day_drive(day_slots: list[dict]) -> int:
    return sum(drive(a.get("place"), b.get("place"))[1] for a, b in zip(day_slots, day_slots[1:]))


def route_node(state: PlannerState) -> dict:
    req = state["request"]
    slots = [dict(s) for s in state["slots"]]
    cat = get_catalog()
    trace, issues = [], []
    total_km = total_min = live_legs = 0
    last_place, last_city = None, None  # multi-city trips: where the previous day ended

    for day in sorted({s["day"] for s in slots}):
        idx = [i for i, s in enumerate(slots) if s["day"] == day]
        day_city = next((slots[i].get("city") for i in idx if slots[i].get("city")), None)
        for i in idx:
            slots[i].pop("transfer", None)  # recomputed below (route runs again after repairs)
        # Route optimisation: try swapping two attraction stops when it cuts driving (and the
        # swap does not break heat/indoor rules or opening hours).
        attr = [i for i in idx if slots[i]["kind"] == "attraction" and slots[i].get("place")
                and not slots[i].get("indoor_only") and not slots[i].get("locked")]
        for a in range(len(attr)):
            for b in range(a + 1, len(attr)):
                i, j = attr[a], attr[b]
                before = _day_drive([slots[k] for k in idx])
                slots[i]["place"], slots[j]["place"] = slots[j]["place"], slots[i]["place"]
                after = _day_drive([slots[k] for k in idx])
                if after + 10 > before:
                    slots[i]["place"], slots[j]["place"] = slots[j]["place"], slots[i]["place"]
                else:
                    for k in ("reason", "primary", "theme", "alternatives", "relaxed"):  # these follow the place
                        slots[i][k], slots[j][k] = slots[j].get(k), slots[i].get(k)
                    trace.append({"agent": "Route", "via": "rules", "did": f"swapped day-{day} activities, saves {before - after} min driving"})

        t_prev_end, prev = None, None
        transfer = bool(day_city and last_city and day_city != last_city and last_place)
        if transfer:  # moving to the next city: the day starts with the drive from last night's stop
            prev = last_place
            first = next((slots[i] for i in idx if slots[i].get("place")), None)
            # leave early enough to arrive about when the day's first stop starts (not before 7 AM)
            t_prev_end = max(420, first["target"] - drive(last_place, first["place"])[1]) if first else None
        mode = req.get("transport", "car")
        wd_day = (req["weekday"] + day - 1) % 7 if req.get("weekday") is not None else None
        for i in idx:
            s = slots[i]
            p = s.get("place")
            if not p:
                continue
            cond_date = (state.get("conditions") or {}).get("date")
            trip_day = (date.fromisoformat(cond_date) + timedelta(days=day - 1)) if cond_date else None
            tinfo: dict = {}
            km, mins = travel(prev, p, mode, wd_day, t_prev_end or s["target"], trip_day, tinfo)
            s["drive_km"], s["drive_min"] = km, mins
            s["traffic"] = tinfo or None
            if tinfo:
                live_legs += 1
            s["legs"] = leg_times(km, drive(prev, p)[1]) if prev else None
            if transfer and prev is last_place and not any(slots[k].get("transfer") for k in idx):  # the city-to-city drive
                s["transfer"] = {"from": last_city, "to": day_city, "km": km, "min": mins}
                trace.append({"agent": "Route", "via": "api" if tinfo else "rules",
                              "did": f"day {day}: drive {last_city} to {day_city}, {dist(km)} / {mins} min"})
            if prev and mode == "walking" and km > 3:
                issues.append({"type": "long_drive", "slot": s["id"], "msg": f"{dist(km)} walk to {p['name']} - consider a rideshare for this leg"})
            total_km += km
            total_min += mins
            start = s["target"] if t_prev_end is None else max(s["target"], t_prev_end + mins)
            s["start"], s["end"] = start, start + s["duration"]
            s["time"] = f"{H.fmt_time(s['start'])} - {H.fmt_time(s['end'])}"
            if req.get("weekday") is not None:
                wd = (req["weekday"] + day - 1) % 7
                sched = H.parse_hours(p["hours"]) if p.get("hours_source") else cat.schedule(p["_idx"])  # live hours win
                ok = None if p.get("event_venue") else H.open_for(sched, wd, start, s["duration"])
                s["open_ok"] = ok
                s["hours_that_day"] = H.hours_on(p.get("hours"), wd)
                if ok is False:
                    issues.append({"type": "closed", "slot": s["id"], "msg": f"{p['name']} is closed at {H.fmt_time(start)} ({s['hours_that_day']})"})
            if start - s["target"] > 45 and not s.get("transfer"):
                issues.append({"type": "late", "slot": s["id"], "msg": f"{s['label']} slips {start - s['target']} min because of driving"})
            day_end = max(req["end_time"], 1380 if any(x["kind"] == "nightlife" for x in slots) else 0)  # nights out run to ~11PM
            if s["end"] > day_end + 45 and s["kind"] in ("nightlife", "restaurant") and s.get("meal") in (None, "dessert"):
                issues.append({"type": "overrun", "slot": s["id"], "msg": f"{s['label']} runs past the planned end time"})
            if mins > 60 and mode != "walking" and not s.get("transfer"):
                issues.append({"type": "long_drive", "slot": s["id"], "msg": f"{mins} min {'drive' if mode == 'car' else 'trip'} to {p['name']}"})
            t_prev_end, prev = s["end"], p
            last_place = p
        last_city = day_city or last_city

    # Booking conflict detection: approved reservations must still match the schedule.
    by_place = {s["place"]["id"]: s for s in slots if s.get("place")}
    for r in state.get("reservations") or []:
        s = by_place.get(r["place_id"])
        if not s:
            issues.append({"type": "booking_conflict", "slot": None, "msg": f"Your reservation request at {r['name']} is no longer in the plan - cancel it."})
        elif r.get("kind") != "lodging" and abs(s.get("start", 0) - r["time_min"]) > 20:
            issues.append({"type": "booking_conflict", "slot": s["id"],
                           "msg": f"Reservation request at {r['name']} is for {H.fmt_time(r['time_min'])} but the plan now arrives {H.fmt_time(s['start'])}."})
    verb = {"car": "driving", "rideshare": "by rideshare", "walking": "walking", "transit": "by transit"}[req.get("transport", "car")]
    trace.append({"agent": "Route", "via": "api" if live_legs else "rules", "did": f"scheduled {sum(1 for s in slots if s.get('place'))} stops, {dist(total_km)} / {total_min} min {verb}" + (f" ({live_legs} legs with live traffic)" if live_legs else "")})
    transfer_min = sum(s["transfer"]["min"] for s in slots if s.get("transfer"))
    return {"slots": slots, "issues": issues, "route": {"km": round(total_km, 1), "minutes": total_min, "mode": req.get("transport", "car"),
                                                        "transfer_min": transfer_min,
                                                        "maps_url": maps_route_url([s["place"] for s in slots if s.get("place")], req.get("transport", "car"))},
            "trace": state.get("trace", []) + trace}


def maps_route_url(places: list[dict], mode: str = "car") -> str | None:
    """Shareable Google Maps directions link through every stop (Google allows up to 9 waypoints)."""
    if not places:
        return None
    pts = [f"{p['lat']:.5f},{p['lon']:.5f}" for p in places][:11]
    travelmode = {"car": "driving", "rideshare": "driving", "walking": "walking", "transit": "transit"}[mode]
    url = f"https://www.google.com/maps/dir/?api=1&origin={pts[0]}&destination={pts[-1]}&travelmode={travelmode}"
    if len(pts) > 2:
        url += "&waypoints=" + "%7C".join(pts[1:-1])
    return url


def rooms_needed(req: dict) -> int:
    party = max(1, req.get("party_size", 2))
    return max(1, math.ceil(party / (4 if req.get("has_kids") else 2)))


def slot_cost(s: dict, req: dict | None = None) -> float:
    p = s.get("place")
    if not p:
        return 0.0
    c = float(p.get("cost_pp") or 0)
    if s["kind"] == "lodging":  # nightly room rate -> per person
        req = req or {}
        return round(c * rooms_needed(req) / max(1, req.get("party_size", 2)), 2)
    if s["kind"] == "restaurant":
        c *= MEAL_FACTOR.get(s.get("meal"), 1.0)
        if s.get("meal") == "dessert":
            c = min(c, 12.0)
    return round(c, 2)


def budget_node(state: PlannerState) -> dict:
    req = state["request"]
    slots = [dict(s) for s in state["slots"]]
    party = max(1, req.get("party_size", 2))
    lines = []
    for s in slots:
        s["cost_pp"] = slot_cost(s, req)
        if s.get("place"):
            known = bool(s["place"].get("cost_known"))
            lines.append({"stop": s["label"], "place": s["place"]["name"],
                          "category": {"restaurant": "food", "lodging": "stay"}.get(s["kind"], s["kind"]),
                          "per_person": s["cost_pp"], "basis": "price level" if known else "category estimate"})
    km = state.get("route", {}).get("km", 0)
    mode = req.get("transport", "car")
    cars = math.ceil(party / PEOPLE_PER_CAR)
    legs = [s.get("drive_km") or 0 for s in slots if s.get("place") and s.get("drive_km")]
    if mode == "rideshare":
        rides = math.ceil(party / 4)
        transport = round(sum(RIDESHARE_BASE + RIDESHARE_PER_KM * k for k in legs if k >= 0.4) * rides / party, 2)
    elif mode == "transit":
        transport = round(TRANSIT_FARE * sum(1 for k in legs if k > 0.8), 2)
    elif mode == "walking":
        transport = 0.0
    else:
        transport = round(km * COST_PER_KM * cars / party, 2)
    # Cheaper alternatives: for each paid stop, the cheapest alternative the specialist also liked
    for s in slots:
        alts = [a for a in s.get("alternatives") or [] if (a.get("cost_pp") or 0) < (s.get("cost_pp") or 0) - 1]
        if alts and s.get("place"):
            best = min(alts, key=lambda a: a.get("cost_pp") or 0)
            for line in lines:
                if line["place"] == s["place"]["name"]:
                    line["cheaper_option"] = f"{best['name']} (~${best.get('cost_pp') or 0:.0f})"
    food = sum(l["per_person"] for l in lines if l["category"] == "food")
    stay = sum(l["per_person"] for l in lines if l["category"] == "stay")
    activities = sum(l["per_person"] for l in lines if l["category"] not in ("food", "stay"))
    total = round(food + activities + stay + transport, 2)
    extras = round(food * 0.25, 2)  # tax + tip on meals
    budget = req.get("budget_per_person")
    tolerance = max(5.0, 0.05 * budget) if budget else 0
    status = ("no budget given" if not budget else "within budget" if total <= budget
              else "slightly over budget" if total - budget <= tolerance else "over budget")
    b = {"lines": lines, "food": round(food, 2), "activities": round(activities, 2), "stay": round(stay, 2), "transport": transport,
         "transport_mode": mode, "total_pp": total, "extras_pp": extras, "group_total": round(total * party, 2),
         "party_size": party, "budget_pp": budget, "status": status,
         "remaining_pp": round(budget - total, 2) if budget else None,
         "free_stops": [l["place"] for l in lines if l["per_person"] == 0]}
    issues = list(state.get("issues", []))
    if budget and total - budget > tolerance:
        over = total - budget
        flexible = [s for s in slots if s.get("place") and not s.get("locked") and s["cost_pp"] > 0]
        # protect what the traveler explicitly asked for (main activity, requested cuisine, the night out)
        protected = lambda s: s.get("primary") or (s.get("cuisine") and s.get("meal") == "dinner") or (s["kind"] == "nightlife" and req.get("nightlife"))  # noqa: E731
        tried = {i.get("slot") for i in state.get("repaired", [])}
        flexible = [s for s in flexible if not protected(s) and s["id"] not in tried] or [s for s in flexible if s["id"] not in tried] or flexible
        if flexible:
            # biggest cost first; on ties, trim the later stop (the first activity is usually the main ask)
            worst = max(flexible, key=lambda s: (s["cost_pp"], slots.index(s)))
            issues.append({"type": "over_budget", "slot": worst["id"], "over": round(over, 2),
                           "msg": f"${over:.0f}/person over budget; {worst['label']} (${worst['cost_pp']:.0f}) is the biggest cost"})
    trace = {"agent": "Budget", "via": "rules", "did": f"${total:.0f}/person (food ${food:.0f}, activities ${activities:.0f}, transport ${transport:.0f}) - {status}"}
    return {"slots": slots, "budget": b, "issues": issues, "trace": state.get("trace", []) + [trace]}
