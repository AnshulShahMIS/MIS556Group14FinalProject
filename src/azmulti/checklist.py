"""Pre-trip checklist: what to book or check before the trip, with a "do it by" date and a link, built from the plan
and the place data (reservations accepted / recommended, ticket prices, opening hours, the forecast).

Item: {"id", "kind", "text", "why", "due" (YYYY-MM-DD or None when the trip has no date), "link", "link_label"}.
Items are rebuilt whenever the plan changes; which ones are ticked off is kept with the saved trip by id, so a tick survives changes elsewhere in the plan.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta, timezone

from .security import safe_url

TICKET_MIN = 10  # $ per person: below this (parking, day passes) no "buy tickets" item


def _id(*parts) -> str:
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:10]


def _first_link(links: dict, names: tuple[str, ...]) -> tuple[str | None, str | None]:
    for n in names:
        if safe_url(links.get(n)):
            return safe_url(links[n]), n
    return None, None


def build(st: dict) -> list[dict]:
    req, cond = st.get("request") or {}, st.get("conditions") or {}
    start = cond.get("date") or req.get("trip_date")
    d0 = date.fromisoformat(str(start)[:10]) if start else None
    booked = {r.get("place_id") for r in st.get("reservations") or [] if r.get("status") == "booked"}
    requested = {r.get("place_id"): r for r in st.get("reservations") or []}

    def due(day: int, days_before: int) -> str | None:
        if not d0:
            return None
        return (d0 + timedelta(days=day - 1) - timedelta(days=days_before)).isoformat()

    items, seen_hotels = [], set()
    stops = [s for s in st.get("slots") or [] if s.get("place")]
    for s in stops:
        p, day = s["place"], s.get("day", 1)
        links = (s.get("booking") or {}).get("links") or {}
        when = (f"{d0 + timedelta(days=day - 1):%a, %b} {(d0 + timedelta(days=day - 1)).day}" if d0 else f"day {day}")
        if s["kind"] == "lodging":
            if p["id"] in seen_hotels:  # night 2, 3... of the same hotel: one booking
                continue
            seen_hotels.add(p["id"])
            nights = sum(1 for x in stops if x["kind"] == "lodging" and x["place"]["id"] == p["id"])
            link, label = _first_link(links, ("Booking.com", "Expedia", "Google Hotels", "Official website"))
            items.append({"id": _id("hotel", p["id"]), "kind": "hotel",
                          "text": f"Book your room at {p['name']}",
                          "why": f"Check in {when}, {nights} night{'s' if nights > 1 else ''} - rooms go fast, and the best rates are early.",
                          "due": due(day, 21), "link": link, "link_label": label, "place_id": p["id"]})
        elif s["kind"] in ("restaurant", "nightlife") and (p.get("reservations_recommended") or p.get("accepts_reservations")):
            strong = bool(p.get("reservations_recommended"))
            link, label = _first_link(links, ("OpenTable", "Resy", "Google (Reserve a table)", "Official website"))
            items.append({"id": _id("table", p["id"], day), "kind": "table",
                          "text": f"Reserve a table at {p['name']}",
                          "why": (f"{s.get('label', 'Meal')} on {when} at {(s.get('time') or '').split(' - ')[0]}. "
                                  + ("Reservations are recommended here." if strong else "They take reservations.")),
                          "due": due(day, 7 if strong else 2), "link": link, "link_label": label, "place_id": p["id"]})
        elif s["kind"] == "attraction" and (s.get("cost_pp") or 0) >= TICKET_MIN:
            link, label = _first_link(links, ("Find official tickets", "Official website", "GetYourGuide", "Viator", "TripAdvisor"))
            items.append({"id": _id("tickets", p["id"], day), "kind": "tickets",
                          "text": f"Get tickets for {p['name']}",
                          "why": f"About ${s['cost_pp']:.0f} per person, {when}. Buying ahead skips the line and sell-outs.",
                          "due": due(day, 3), "link": link, "link_label": label, "place_id": p["id"]})
        if s.get("open_ok") is False:
            link, label = _first_link(links, ("Official website", "Google Maps"))
            items.append({"id": _id("hours", p["id"], day), "kind": "hours",
                          "text": f"Check opening hours for {p['name']}",
                          "why": f"Our data isn't sure it's open {when} at {(s.get('time') or '').split(' - ')[0]}.",
                          "due": due(day, 1), "link": link, "link_label": label, "place_id": p["id"]})
    for it in items:  # already booked in the app: done
        it["auto_done"] = it.get("place_id") in booked or (it["kind"] in ("hotel", "table", "tickets")
                                                             and (requested.get(it.get("place_id")) or {}).get("status") == "booked")
    # the whole trip: weather (and Arizona heat), one item each
    highs = [v.get("high_f") for v in (cond.get("by_day") or {}).values() if v.get("high_f")] or (
        [cond["high_f"]] if cond.get("high_f") else [])
    cities = " and ".join(dict.fromkeys(lg["city"] for lg in req.get("legs") or [])) or req.get("city") or "your trip"
    items.append({"id": _id("weather", start), "kind": "weather", "text": f"Check the forecast for {cities}",
                  "why": "Plans here were made with " + ("a live forecast" if "forecast" in str(cond.get("source", "")).lower()
                                                         else "typical weather for the month") + " - check again close to the trip.",
                  "due": due(1, 2), "link": None, "link_label": None, "auto_done": False})
    hikes = any(s["kind"] == "attraction" and any(w in (s["place"].get("primary_category") or "").lower()
                                                   for w in ("hik", "trail", "park", "canyon", "mountain"))
                for s in stops)
    if hikes and highs and max(highs) >= 95:
        items.append({"id": _id("heat", start), "kind": "heat", "text": "Plan for the heat on your hikes",
                      "why": f"Highs up to {max(highs)}°F: start hikes at sunrise, carry about a gallon of water per person, "
                             "and turn back by mid-morning.",
                      "due": due(1, 1), "link": None, "link_label": None, "auto_done": False})
    return items


def with_status(items: list[dict], done: list[str] | set[str]) -> list[dict]:
    done = set(done or [])
    return [{**it, "done": bool(it.get("auto_done") or it["id"] in done)} for it in items]


def fmt_due(due: str | None, today: date | None = None) -> str:
    if not due:
        return ""
    d, today = date.fromisoformat(due), today or date.today()
    n = (d - today).days
    return ("overdue - do it now" if n < 0 else "do it today" if n == 0 else "do it by tomorrow" if n == 1
            else f"do it by {d:%a, %b} {d.day}")


def today_in_arizona() -> date:
    """Arizona doesn't use daylight saving: always UTC-7."""
    return (datetime.now(timezone.utc) - timedelta(hours=7)).date()
