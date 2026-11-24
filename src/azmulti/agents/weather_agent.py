"""WEATHER & EVENTS agent: forecast or climate normal, heat/rain/cold alerts, sunrise and sunset,
recurring local events, then the day skeleton (which depends on all of these)."""
from __future__ import annotations

from .. import events as EV
from .. import weather as W
from ..catalog import get_catalog
from ..hours import fmt_time
from ..state import PlannerState, TripRequest
from .planner import build_slots


def per_day_conditions(req: dict, first: dict) -> dict[int, dict]:
    """Multi-city trips: the forecast (or climate normal) for each day in the city you'll be in that day."""
    from datetime import date, timedelta
    from .planner import day_cities
    cat = get_catalog()
    start = date.fromisoformat(first["date"])
    out = {}
    for day, city in day_cities(req).items():
        lat, lon = cat.locate(city) or cat.center_for(req)
        d = (start + timedelta(days=day - 1)).isoformat()
        out[day] = {**W.conditions(lat, lon, None, None, 1, exact=d), "city": city}
    return out


def _stays(by_day: dict) -> list[tuple[str, list[int]]]:
    """[(city, [days])] in visiting order."""
    out: list[tuple[str, list[int]]] = []
    for d in sorted(by_day):
        if out and out[-1][0] == by_day[d]["city"]:
            out[-1][1].append(d)
        else:
            out.append((by_day[d]["city"], [d]))
    return out


def weather_node(state: PlannerState) -> dict:
    req = state["request"]
    cat = get_catalog()
    lat, lon = cat.center_for(req)
    cond = W.conditions(lat, lon, req.get("weekday"), req.get("month"), req.get("days", 1), exact=req.get("trip_date"))
    by_day = per_day_conditions(req, cond) if req.get("legs") else None
    if by_day:
        cond = {**cond, "by_day": by_day}
    out: dict = {"conditions": cond}
    trace = [{"agent": "Weather & Events", "via": "rules" if "normal" in cond["source"] else "api",
              "did": f"{cond['date']}: high {cond['high_f']}°F ({cond['source']}), sunrise {fmt_time(cond['sunrise'])}, "
                     f"sunset {fmt_time(cond['sunset'])}" + (f", {cond['rain_pct']}% rain" if cond["rain_pct"] else "")}]
    if by_day:
        trace.append({"agent": "Weather & Events", "via": "rules" if all("normal" in d["source"] for d in by_day.values()) else "api",
                      "did": "per-city weather: " + "; ".join(f"day {d} {c['city']} {c['high_f']}°F" for d, c in by_day.items())})
    if not state.get("needs_skeleton"):
        out["trace"] = state.get("trace", []) + trace
        return out

    notes = list(state.get("notes", []))
    r = TripRequest(**req)
    slots, sk_notes = build_slots(r, cond, by_day)
    if by_day:  # one line per city stay, with that city's own alerts
        for city, ds in _stays(by_day):
            c0 = by_day[ds[0]]
            highs = sorted({by_day[d]["high_f"] for d in ds})
            hi = f"{highs[0]}°F" if len(highs) == 1 else f"{highs[0]}-{highs[-1]}°F"
            notes.append(f"Weather in {city} (day {ds[0]}{'-' + str(ds[-1]) if len(ds) > 1 else ''}): high {hi} ({c0['source']}). "
                         f"Sunrise {fmt_time(c0['sunrise'])}, sunset {fmt_time(c0['sunset'])}.")
            notes += [f"{city}: {a}" for a in dict.fromkeys(a for d in ds for a in by_day[d]["alerts"])]
        notes += sk_notes
    else:
        notes += [f"Weather: {cond['high_f']}°F high on {cond['date']} ({cond['source']}). Sunrise {fmt_time(cond['sunrise'])}, sunset {fmt_time(cond['sunset'])}."]
        notes += cond["alerts"] + sk_notes
    if by_day:  # events in every city you visit, during your days there
        evs, live_evs = [], []
        for city, ds in _stays(by_day):
            clat, clon = cat.locate(city) or (lat, lon)
            evs += [dict(e, city=city) for e in EV.events_for(by_day[ds[0]]["month"], city, req.get("interests"))]
            live_evs += [dict(e, city=city) for e in EV.live_events(clat, clon, by_day[ds[0]]["date"], len(ds)) or []]
        seen = set()
        evs = [e for e in evs if not (e["name"] in seen or seen.add(e["name"]))]
    else:
        evs = EV.events_for(cond["month"], req.get("city"), req.get("interests"))
        live_evs = EV.live_events(lat, lon, cond["date"], req.get("days", 1))
    if live_evs:
        notes.append("Live events on your date(s) (Ticketmaster): " + "; ".join(
            f"[{e['name']}]({e['url']}) - {e['note']}" for e in live_evs[:4]) + ". Say *add the <event> to my plan* to fit one in.")
        trace.append({"agent": "Weather & Events", "via": "api", "did": f"{len(live_evs)} live events from Ticketmaster"})
        evs = live_evs + evs
    elif evs:
        top = evs[:3]
        notes.append("Happening around then (usual timing, check the official site for dates): "
                     + "; ".join(f"**{e['name']}** - {e['note']}" for e in top))
    if req.get("discounts"):
        notes.append(f"Discounts: ask museums, parks and attractions about {', '.join(req['discounts'])} rates. I can't verify offers, so check before you buy.")
    trace.append({"agent": "Weather & Events", "via": "rules", "did": f"built {len(slots)} stops; {len(cond['alerts'])} alert(s); {len(evs)} event(s) that month"})
    out.update(slots=slots, notes=notes, events=evs, needs_skeleton=False, trace=state.get("trace", []) + trace)
    return out
