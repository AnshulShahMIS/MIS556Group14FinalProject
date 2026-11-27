"""Car Rental agent: when the traveler needs a rental car, it decides where and when to pick it up, how many cars and
what size, for how many days, roughly what it costs, and where to book - for the Budget agent, the Checklist and the
Booking links. Runs after the Route agent (it needs the cities, days and distances) and before the Budget agent.

Rules, not live prices: daily rates are typical Arizona estimates by car size and are labelled as estimates; booking
happens on the rental sites (Kayak compares the companies), pre-filled with the airport and dates. Nothing is booked.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

from ..checklist import AIRPORTS, rental_link
from ..state import PlannerState

# car size: (seats it suits, typical daily rate in Arizona before gas, what fits)
SIZES = {"economy": (2, 45.0, "2 people and a couple of bags"),
         "midsize": (4, 55.0, "4 people with luggage"),
         "SUV": (5, 70.0, "5 people, or 4 with lots of gear - better on dirt roads to trailheads"),
         "minivan": (7, 85.0, "6-7 people with luggage")}
LONG_TRIP_KM = 400  # past this much driving, upgrade economy to midsize for comfort


def plan_rental(req: dict, route: dict | None, start: str | None, catalog=None) -> dict | None:
    """The rental decision for a trip (None when no rental car is needed)."""
    if not req.get("rental_car") or req.get("transport", "car") != "car":
        return None
    party = max(1, int(req.get("party_size") or 2))
    days = max(1, int(req.get("days") or 1))
    km = float((route or {}).get("km") or 0)
    outdoorsy = any(i in ("hiking_outdoors", "scenic_views", "tours_adventure") for i in req.get("interests") or [])
    # how many cars, and what size: up to 7 per minivan; otherwise one car sized to the group
    cars = 1 if party <= 7 else math.ceil(party / 7)
    per_car = math.ceil(party / cars)
    size = ("economy" if per_car <= 2 else "midsize" if per_car <= 4 else "SUV" if per_car <= 5 else "minivan")
    if size == "economy" and km > LONG_TRIP_KM:
        size = "midsize"  # long drives: more room and comfort for little more money
    if size == "midsize" and outdoorsy and (req.get("kids") or km > LONG_TRIP_KM):
        size = "SUV"  # trailheads, gear and kids
    seats, rate, fits = SIZES[size]
    city = (req.get("legs") or [{}])[0].get("city") or req.get("city") or "Phoenix"
    airport = AIRPORTS.get(city)
    last = (req.get("legs") or [{}])[-1].get("city") if req.get("legs") else None
    d0 = date.fromisoformat(str(start)[:10]) if start else None
    total = round(days * rate * cars, 2)
    office = None
    if catalog is not None and not airport:  # no airport nearby: a rental office from the place data, if one is close
        try:
            df = catalog.df
            offices = df[df["primary_category"].astype(str).str.contains("rental agency", case=False)
                         & df["primary_category"].astype(str).str.contains("car", case=False)]
            center = catalog.center_for(req)
            if len(offices) and center:
                d2 = (offices.lat - center[0]) ** 2 + (offices.lon - center[1]) ** 2
                best = offices.loc[d2.idxmin()]
                if float(d2.min()) ** 0.5 < 0.35:  # roughly within 35 km
                    office = {"name": best["name"], "city": best.get("city"), "url": best.get("url")}
        except Exception:
            office = None
    notes = []
    if last and last != city and AIRPORTS.get(last) != airport:
        notes.append(f"Return it where you picked it up - dropping off in {last} instead usually costs extra.")
    notes.append("Most companies want a credit card and a driver 25 or older (younger drivers pay a daily fee).")
    if req.get("kids"):
        notes.append("Ask for car seats when booking, or bring your own.")
    return {"needed": True, "cars": cars, "size": size, "fits": fits, "seats": seats, "days": days,
            "pickup_city": city, "airport": airport, "pickup_date": d0.isoformat() if d0 else None,
            "return_date": (d0 + timedelta(days=days - 1)).isoformat() if d0 else None,
            "rate_per_day": rate, "total": total, "per_person": round(total / party, 2),
            "link": rental_link(city, d0, days), "office": office, "notes": notes,
            "summary": (f"{cars} {size}{'s' if cars > 1 else ''} for {days} day{'s' if days > 1 else ''}, pick up "
                        + (f"at {airport} airport" if airport else f"in {city}")
                        + f" - about ${total:.0f} (${round(total / party):.0f} each), before gas")}


def rental_node(state: PlannerState) -> dict:
    req = state.get("request") or {}
    cond = state.get("conditions") or {}
    try:
        from ..catalog import get_catalog
        cat = get_catalog()
    except Exception:
        cat = None
    rental = plan_rental(req, state.get("route"), cond.get("date") or req.get("trip_date"), cat)
    if not rental:
        return {"rental": None}
    return {"rental": rental,
            "trace": state.get("trace", []) + [{"agent": "Car Rental", "via": "rules", "did": rental["summary"]}]}
