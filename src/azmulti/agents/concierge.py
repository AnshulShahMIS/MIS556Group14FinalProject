"""BOOKING agent, CONCIERGE (writer) agent and Q&A agent.

- Booking never books anything: it prepares official links, flags where a reservation or
  ticket is advisable and builds a calendar (.ics) file. Purchases need the traveler's approval.
- Concierge writes the final answer from the itinerary + retrieved evidence (Method 3: RAG,
  grounded in the workbook's descriptions, amenities and TripAdvisor review summaries).
- Q&A answers follow-up questions with retrieval over the plan and the catalog.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

from .. import prompts
from ..catalog import get_catalog
from ..config import TIMEZONE
from ..hours import DAYS, fmt_time
from ..llm import text
from ..security import safe_url
from ..state import PlannerState
from ..units import dist, snip
from .logistics import rooms_needed


def _trip_date(weekday: int | None, cond: dict | None = None) -> datetime:
    if cond and cond.get("date"):
        return datetime.fromisoformat(cond["date"])
    now = datetime.now(ZoneInfo(TIMEZONE)).replace(tzinfo=None)
    if weekday is None:
        return now
    return now + timedelta(days=(weekday - now.weekday()) % 7 or 7)


def _when(s: dict, base: datetime) -> tuple[datetime, datetime]:
    day0 = base.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=s["day"] - 1)
    return day0 + timedelta(minutes=s["start"]), day0 + timedelta(minutes=s["end"])


def gcal_link(s: dict, base: datetime) -> str:
    """'Add to Google Calendar' link for one stop (times as Arizona local, which is UTC-7 all year)."""
    st, en = _when(s, base)
    st_u, en_u = st + timedelta(hours=7), en + timedelta(hours=7)
    p = s["place"]
    return ("https://calendar.google.com/calendar/render?action=TEMPLATE"
            f"&text={quote_plus(s['label'] + ': ' + p['name'])}&dates={st_u:%Y%m%dT%H%M%SZ}/{en_u:%Y%m%dT%H%M%SZ}"
            f"&location={quote_plus(p.get('address') or p['name'])}&details={quote_plus('Planned with Saguaro. ' + (p.get('url') or ''))}")


def _ics(text: str) -> str:
    """Escape a value for iCalendar (RFC 5545): no line breaks that could inject extra fields."""
    s = re.sub(r"[\r\n]+", " ", str(text or ""))
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")[:300]


def build_ics(slots: list[dict], weekday: int | None, cond: dict | None = None) -> str:
    base = _trip_date(weekday, cond)
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Saguaro AZ Planner//EN"]
    for s in slots:
        p = s.get("place")
        if not p or "start" not in s:
            continue
        st, en = _when(s, base)
        lines += ["BEGIN:VEVENT", f"UID:{s['id']}-{p['id'][-8:]}@saguaro", f"DTSTART;TZID=America/Phoenix:{st:%Y%m%dT%H%M%S}",
                  f"DTEND;TZID=America/Phoenix:{en:%Y%m%dT%H%M%S}", f"SUMMARY:{_ics(s['label'] + ': ' + p['name'])}",
                  f"LOCATION:{_ics(p.get('address'))}", f"URL:{safe_url(p.get('url')) or ''}", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines)


def web_links(p: dict, kind: str) -> dict:
    """Website (and menu for restaurants / bars) for one place.

    The workbook has no official-website or menu URLs, so: with a Google Places key the Live Info agent's
    `websiteUri` is the real official site; otherwise the link searches for the official site. The menu link
    searches for that restaurant's menu (Google shows the menu card / menu pages first)."""
    q = quote_plus(f"{p['name']} {p.get('city', '')} AZ")
    site = safe_url((p.get("live") or {}).get("website"))
    out = {"Website": site} if site else {"Find website": f"https://www.google.com/search?q={q}+official+website"}
    if kind in ("restaurant", "nightlife"):
        out["Menu"] = f"https://www.google.com/search?q={q}+menu"
    return out


RESERVABLE = ("restaurant", "nightlife", "lodging", "attraction")


def reservation_links(kind: str, name: str, city: str, day: datetime, time_min: int | None, party: int,
                      nights: int = 1, rooms: int = 1) -> dict:
    """Booking pages pre-filled with the date, time, party size (and nights / rooms for hotels).

    Real-time availability and in-app confirmation need partner access (OpenTable / Resy / hotel APIs), which this
    project doesn't have, so the agent opens each platform's own search with everything filled in and the user picks
    from the live times there. Nothing is ever booked or paid for by the agent."""
    q = quote_plus(f"{name} {city} AZ")
    party = max(1, int(party or 1))
    if kind == "attraction":  # tickets, tours and experiences
        nq = quote_plus(f"{name} {city}")
        return {"Find official tickets": f"https://www.google.com/search?q={q}+official+tickets",
                "GetYourGuide": f"https://www.getyourguide.com/s/?q={nq}&date_from={day:%Y-%m-%d}&date_to={day:%Y-%m-%d}",
                "Viator": f"https://www.viator.com/searchResults/all?text={nq}"}
    if kind == "lodging":
        cin, cout = day.date(), (day + timedelta(days=max(1, nights))).date()
        return {"Booking.com": f"https://www.booking.com/searchresults.html?ss={q}&checkin={cin}&checkout={cout}"
                               f"&group_adults={party}&no_rooms={max(1, rooms)}",
                "Expedia": f"https://www.expedia.com/Hotel-Search?destination={q}&startDate={cin}&endDate={cout}"
                           f"&adults={party}&rooms={max(1, rooms)}",
                "Google Hotels": f"https://www.google.com/travel/search?q={q}&checkin={cin}&checkout={cout}&adults={party}"}
    when = day.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(minutes=time_min or 19 * 60)
    return {"OpenTable": f"https://www.opentable.com/s?term={q}&covers={party}&dateTime={when:%Y-%m-%dT%H:%M}",
            "Resy": f"https://resy.com/cities/phoenix-az?query={quote_plus(name)}&date={when:%Y-%m-%d}&seats={party}",
            "Google (Reserve a table)": f"https://www.google.com/maps/search/?api=1&query={q}"}


def booking_node(state: PlannerState) -> dict:
    req = state["request"]
    slots = [dict(s) for s in state["slots"]]
    weekend = req.get("weekday") in (4, 5)
    party = req.get("party_size", 2)
    base = _trip_date(req.get("weekday"), state.get("conditions"))
    n_flag = 0
    for s in slots:
        p = s.get("place")
        if not p:
            continue
        q = quote_plus(p["name"] + " " + p["city"] + " AZ")
        links = {"Google Maps": p.get("url"),
                 "Directions": f"https://www.google.com/maps/dir/?api=1&destination={p['lat']},{p['lon']}"}
        if "start" in s:
            links["Add to Google Calendar"] = gcal_link(s, base)
        advice = []
        sit_down = (p.get("cost_pp") or 0) >= 25 and "fast food" not in (p.get("categories") or "").lower() \
            and "counter" not in (p.get("description") or "").lower()
        if s["kind"] in ("restaurant", "nightlife") and s.get("meal") != "dessert":
            if p.get("reservations_recommended") or (sit_down and (party >= 5 or (p.get("price_level") or 0) >= 3
                                                                   or (s.get("meal") == "dinner" and (weekend or req.get("occasion"))))):
                advice.append("Reservation recommended" + (" (Google lists it as 'reservations recommended')" if p.get("reservations_recommended") else "") + ".")
            elif p.get("accepts_reservations"):
                advice.append("Takes reservations.")
            if p.get("usually_wait"):
                advice.append("Google notes there is usually a wait.")
            if p.get("accepts_reservations") or advice:
                day = base + timedelta(days=s["day"] - 1)
                links.update({k: v for k, v in reservation_links(s["kind"], p["name"], p["city"], day, s.get("start", s.get("target")),
                                                                  party).items() if k != "Google (Reserve a table)"})
            if party >= 8:
                advice.append(f"Party of {party}: call ahead - many places cap online bookings at 6-8 guests.")
        elif s["kind"] == "lodging":
            advice.append(f"About ${p.get('cost_pp') or 0:.0f} a night per room (estimate) - compare rates before booking. "
                          "Check-in is usually from 3-4 PM.")
            nights = sum(1 for x in slots if x.get("place") and x["place"]["id"] == p["id"])
            links.update(reservation_links("lodging", p["name"], p["city"], base + timedelta(days=s["day"] - 1), None, party,
                                           nights=nights, rooms=rooms_needed(req)))
        elif s["kind"] == "attraction" and (s.get("cost_pp") or 0) >= 15:
            advice.append("Ticketed - buy from the official site (compare with authorized resellers) and verify the current price.")
            links.update(reservation_links("attraction", p["name"], p["city"], base + timedelta(days=s["day"] - 1),
                                           s.get("start"), party))
            if party >= 10:
                advice.append(f"Group of {party}: ask about group rates and capacity limits.")
        if p.get("ta_rank") or p.get("ta_rating"):
            links["TripAdvisor"] = f"https://www.tripadvisor.com/Search?q={q}"
        lv = p.get("live") or {}
        if lv.get("website"):
            links = {"Official website": lv["website"], **links}
        links.update({k: v for k, v in web_links(p, s["kind"]).items() if k != "Website"})
        if lv.get("phone"):
            advice.append(f"Phone: {lv['phone']}.")
        if p.get("military_discount") and "military" in req.get("discounts", []):
            advice.append("Google lists active-military discounts here.")
        n_flag += bool(advice)
        s["booking"] = {"links": links, "advice": " ".join(advice) or None, "requires_approval": True}
    ics = build_ics(slots, req.get("weekday"), state.get("conditions"))
    trace = {"agent": "Booking", "via": "rules", "did": f"links for {sum(1 for s in slots if s.get('place'))} stops, {n_flag} reservation/ticket flag(s), calendar file ready (nothing booked)"}
    return {"slots": slots, "booking": {"ics": ics, "route_url": (state.get("route") or {}).get("maps_url")},
            "trace": state.get("trace", []) + [trace]}


# ---------------------------------------------------------------- writer

def _evidence(p: dict) -> dict:
    ev = {k: p.get(k) for k in ("description", "ev_love", "ev_complaints", "ev_tip", "hike_length", "hike_difficulty",
                                "amenities", "ta_rank") if p.get(k)}
    if "amenities" in ev:
        ev["amenities"] = ev["amenities"][:300]
    return ev


def _quotes(p: dict) -> list[dict]:
    """Real review quotes for the writer (never the file's AI-generated sample reviews)."""
    try:
        return [q for q in json.loads(p.get("review_quotes") or "[]") if q.get("text")]
    except Exception:
        return []


def itinerary_payload(state: PlannerState) -> dict:
    out = []
    for n, s in enumerate([s for s in state["slots"] if s.get("place")], 1):
        p = s["place"]
        out.append({"n": n, "day": s["day"], "time": s.get("time"), "stop": s["label"], "name": p["name"], "city": p["city"],
                    "category": p["primary_category"], "rating": round(p["rating"] or 0, 1), "reviews": int(p["n_reviews"]),
                    "review_source": p.get("review_source"), "est_cost_pp": s.get("cost_pp"),
                    "cost_basis": "price level" if p.get("cost_known") else "estimate", "drive_min": s.get("drive_min"),
                    "hours_that_day": s.get("hours_that_day"), "why_picked": s.get("reason"), "start_min": s.get("start"),
                    "reservation": (s.get("booking") or {}).get("advice"), "evidence": _evidence(p),
                    "constraints_relaxed": s.get("relaxed") or [], "kind": s["kind"],
                    "day_city": s.get("city"), "transfer": s.get("transfer"),
                    "review_quote": (p.get("review_evidence") or {}).get("text"),  # retrieved for this request (Method 3)
                    "review_quotes": _quotes(p)[:2],
                    "menu_highlights": p.get("menu_highlights") if s["kind"] != "attraction" else None,
                    "atmosphere": p.get("atmosphere"), "url": p.get("url"),
                    "live": {k: v for k, v in (p.get("live") or {}).items() if k in ("status", "website", "parking", "checked", "summary", "rating", "reviews")} or None,
                    "traffic": s.get("traffic"),
                    "sources": ["Google Maps listing (2021 snapshot)"] + (["TripAdvisor (gathered Nov 2026)"] if p.get("review_source") == "TripAdvisor" or p.get("ev_love") else [])})
    return {"request": {k: v for k, v in state["request"].items() if v not in (None, [], "", False)},
            "stops": out, "budget": {k: v for k, v in state.get("budget", {}).items() if k != "lines"},
            "quality": state.get("quality"), "issues": [i["msg"] for i in state.get("issues", [])],
            "notes": state.get("notes", []), "route_url": (state.get("route") or {}).get("maps_url"),
            "transport": state["request"].get("transport", "car")}


def day_weather(cond: dict | None, day: int) -> dict | None:
    """One day's weather on a multi-city trip (keys may be strings once a plan has been saved)."""
    by_day = (cond or {}).get("by_day") or {}
    return by_day.get(day) or by_day.get(str(day))


def template_answer(state: PlannerState) -> str:
    pay = itinerary_payload(state)
    r = state["request"]
    who = f"{r['party_size']} {'people' if r['party_size'] > 1 else 'person'}"
    day = DAYS[r["weekday"]] if r.get("weekday") is not None else "your day"
    cond = state.get("conditions") or {}
    date_txt = f" {datetime.fromisoformat(cond['date']):%b %d}" if cond.get("date") else ""
    if cond.get("date") and r.get("days", 1) > 1:  # a multi-day trip: show the date range
        last = datetime.fromisoformat(cond["date"]) + timedelta(days=r["days"] - 1)
        date_txt += f" - {DAYS[last.weekday()]} {last:%b %d} ({r['days']} days)"
    base = datetime.fromisoformat(cond["date"]) if cond.get("date") else None
    where = " → ".join(f"{lg['city']} ({lg['days']} day{'s' if lg['days'] > 1 else ''})" for lg in r.get("legs") or []) or r["city"]
    lines = [f"### Your {where} plan - {day}{date_txt}, {who}" + (f", {r['occasion']}" if r.get("occasion") else ""), ""]
    cur_day = None
    verb = {"car": "drive", "rideshare": "ride", "walking": "walk", "transit": "by transit"}[pay["transport"]]
    if not pay["stops"]:
        return ("I couldn't find places that match all of that in the Arizona data. Try a nearby bigger city "
                "(Phoenix, Tucson, Flagstaff), a wider time window, or fewer must-haves.")
    for s in pay["stops"]:
        if r.get("days", 1) > 1 and s["day"] != cur_day:
            cur_day = s["day"]
            lines.append(f"**Day {cur_day}" + (f" · {base + timedelta(days=cur_day - 1):%A, %b %d}" if base else "")
                         + (f" · {s['day_city']}" if r.get("legs") and s.get("day_city") else "") + "**")
            wx = day_weather(cond, cur_day)
            if wx and r.get("legs"):  # multi-city: that day's weather where you'll be
                lines.append(f"*{wx['city']}: high {wx['high_f']}°F"
                             + (f", {wx['rain_pct']}% chance of rain" if wx.get("rain_pct") else "")
                             + f", sunset {fmt_time(wx['sunset'])}"
                             + (" - forecast" if "forecast" in (wx.get("source") or "").lower() else " - typical for the month") + "*")
            if s.get("transfer"):
                tr = s["transfer"]
                lines.append(f"*Travel day: about {tr['min'] // 60} h {tr['min'] % 60:02d} min ({dist(tr['km'])}) from "
                             f"{tr['from']} to {tr['to']} - leave around {fmt_time(max(420, (s.get('start_min') or 540) - tr['min']))}.*")
        cost = (f"~${s['est_cost_pp']:.0f}/person per night" if s.get("kind") == "lodging" and s["est_cost_pp"]
                else f"~${s['est_cost_pp']:.0f}/person" if s["est_cost_pp"] else "free")
        drive = f" · {s['drive_min']} min {verb}" if s.get("drive_min") else ""
        lines.append(f"{s['n']}. **{s['time']} - [{s['name']}]({s['url']})** ({s['city']}) · {s['rating']}★ ({s['reviews']:,} reviews) · {cost}{drive}")
        lines.append(f"   - *Why:* {s['why_picked']}")
        lv = s.get("live") or {}
        if lv.get("checked"):
            bits = ["open" if lv.get("status") == "OPERATIONAL" else (lv.get("status") or "").replace("_", " ").lower()]
            if s.get("hours_that_day"):
                bits.append(f"hours that day {s['hours_that_day']}")
            if lv.get("parking"):
                bits.append("parking: " + ", ".join(lv["parking"]))
            lines.append(f"   - *Live check (Google, {lv['checked']}):* " + "; ".join(b for b in bits if b))
        if (s.get("traffic") or {}).get("delay_min"):
            lines.append(f"   - *Traffic (TomTom):* about {s['traffic']['delay_min']} min of delay on the way here.")
        if s.get("menu_highlights"):
            lines.append(f"   - *Menu highlights (Google tags):* {s['menu_highlights'][:140]}"
                         + (f" · *Atmosphere:* {s['atmosphere']}" if s.get("atmosphere") else ""))
        ev = s["evidence"]
        if ev.get("ev_tip"):
            lines.append(f"   - *Tip (TripAdvisor summary):* {ev['ev_tip']}")
        if ev.get("ev_complaints"):
            lines.append(f"   - *Heads-up:* {ev['ev_complaints']}")
        if s.get("reservation"):
            lines.append(f"   - {s['reservation']}")
        if s["constraints_relaxed"]:
            gave_up = [c for c in s["constraints_relaxed"] if c not in ("meal tag", "per-stop budget share", "amenity tags")]
            if any("same tier" in c for c in gave_up):
                lines.append("   - Nothing cheaper matched nearby, so this is a different pick in the same price tier.")
            elif gave_up:
                lines.append(f"   - No exact match, so I relaxed: {', '.join(gave_up)}.")
    b = pay["budget"]
    if b:
        tl = {"car": "driving", "rideshare": "rideshare", "walking": "walking", "transit": "transit"}[pay["transport"]]
        lines += ["", f"**Estimated cost:** ${b['total_pp']:.0f}/person (food ${b['food']:.0f}, activities ${b['activities']:.0f}, "
                      f"{tl} ${b['transport']:.0f}) - {b['status']}" + (f" vs. ${b['budget_pp']:.0f}" if b.get("budget_pp") else "")
                  + f". Group of {b.get('party_size', r['party_size'])}: about ${b['group_total']:.0f} total. Tax and tip add roughly ${b['extras_pp']:.0f}/person."]
        if b.get("status") == "over budget":
            lines.append("Say *make it cheaper* or *swap the most expensive stop* and I'll trim it.")
    more = []
    for st_ in state["slots"]:
        if st_.get("place") and st_.get("alternatives") and st_["kind"] in ("lodging", "restaurant", "attraction"):
            if st_["kind"] == "lodging" and any(m.startswith("**Hotel") for m in more):
                continue
            alts = ", ".join(f"{a['name']} ({(a.get('rating') or 0):.1f}★)" for a in st_["alternatives"][:2])
            more.append(f"**{st_['label']}:** {alts}")
    if more:
        lines += ["", "**More options you might like**"] + [f"- {m}" for m in more[:5]]
    if pay.get("route_url"):
        lines.append(f"\n[Open the whole route in Google Maps]({pay['route_url']}) - shareable with your group.")
    notes = pay["notes"] + pay["issues"]
    live_srcs = []
    checked = [s["live"]["checked"] for s in pay["stops"] if (s.get("live") or {}).get("checked")]
    if checked:
        live_srcs.append(f"live Google Places check ({checked[0]})")
    if any(s.get("traffic") for s in pay["stops"]):
        live_srcs.append("TomTom traffic")
    if any(e.get("source") == "Ticketmaster" for e in state.get("events") or []):
        live_srcs.append("Ticketmaster events")
    lines += ["", "**Good to know**"] + [f"- {n}" for n in notes] + [
        "- Sources: Google Maps listings (2021 snapshot) and TripAdvisor rankings/review summaries gathered Nov 2026"
        + (f"; {', '.join(live_srcs)}" if live_srcs else "") + ". "
        + ("Hours shown come from the live check where available. " if checked else "Hours, prices and availability can change - verify before you go. ")
        + "Costs marked as estimates are category averages.",
        "", "Want me to swap a stop, *show budget/balanced/premium options*, *compare* stops, find something *near stop 2*, or *book dinner*?"]
    return "\n".join(lines)


def concierge_node(state: PlannerState) -> dict:
    payload = json.dumps(itinerary_payload(state), default=str)[:14000]
    answer, how = text(prompts.CONCIERGE, f"Itinerary data (JSON):\n{payload}", lambda: template_answer(state))
    trace = {"agent": "Concierge", "via": how, "did": "wrote the grounded itinerary answer"}
    hist = (state.get("history") or []) + [{"role": "user", "content": state["user_message"]}, {"role": "assistant", "content": answer}]
    return {"answer": answer, "history": hist[-12:], "trace": state.get("trace", []) + [trace]}


# ---------------------------------------------------------------- Q&A (RAG)

def qa_node(state: PlannerState) -> dict:
    q = state.get("question") or state["user_message"]
    cat = get_catalog()
    filled = [s for s in state.get("slots") or [] if s.get("place")]
    plan = [s["place"] for s in filled]
    ql = q.lower()
    focus = [p for p in plan if any(w in ql for w in p["name"].lower().split() if len(w) > 3)]
    ordinals = {"first": 0, "1st": 0, "second": 1, "2nd": 1, "third": 2, "3rd": 2, "fourth": 3, "4th": 3,
                "fifth": 4, "5th": 4, "last": -1}
    for w, i in ordinals.items():
        if re.search(rf"\b{w}\b", ql) and plan:
            focus.append(plan[i] if -len(plan) <= i < len(plan) else plan[-1])
    for n in re.findall(r"(?:stop|#)\s*(\d)", ql):
        if 0 < int(n) <= len(plan):
            focus.append(plan[int(n) - 1])
    for s in filled:
        if s["label"].lower().split()[0] in ql or s["label"].lower() in ql:
            focus.append(s["place"])
    focus = list({p["id"]: p for p in focus}.values())
    req = state.get("request") or {}
    from .planner import mentioned_az_city
    asked_city = mentioned_az_city(q)
    elsewhere = bool(asked_city and asked_city != req.get("city")
                     and asked_city not in [lg["city"] for lg in req.get("legs") or []])
    center = cat.locate(asked_city) if asked_city else (cat.locate(req.get("city")) if req else None)
    hits = cat.search(q, center=center, radius_km=40, k=4) if center else cat.search(q, k=4)
    if elsewhere:  # "best hikes near Sedona?" while planning Phoenix: answer about Sedona, not the plan
        ctx = hits
    else:
        ctx = focus if focus else plan[:6] + [h for h in hits if h["id"] not in {p["id"] for p in plan}][:4]
    slot_of = {s["place"]["id"]: s for s in filled}
    from ..reviews_index import get_index
    rix = get_index(cat)  # Method 3: top-k real review snippets most similar to the question, per context place
    docs = []
    for p in ctx:
        s = slot_of.get(p["id"])
        d = {"name": p["name"], "city": p["city"], "category": p["categories"], "rating": p["rating"], "reviews": p["n_reviews"],
             "hours": p.get("hours"), "price_level": p.get("price_level"), "url": p.get("url"), **_evidence(p)}
        if p.get("likely_closed"):
            d["closure_warning"] = f"Probably closed: {p['closed_reason']}. Say so and do not recommend it."
        if rix is not None and p.get("_idx") is not None:
            d["retrieved_reviews"] = [f"{r['stars']}★ ({r.get('year')}): {snip(r['text'], 280)}"
                                      for r in rix.top_k(q, [int(p["_idx"])], k=2) if r["similarity"] >= 0.2]
        if s:  # it's in the traveler's plan: add the schedule facts
            d.update(in_plan_as=s["label"], scheduled_time=s.get("time"), est_cost_pp=s.get("cost_pp"),
                     drive_min=s.get("drive_min"), good_for_kids=bool(p.get("kids")), good_for_groups=bool(p.get("groups")),
                     wheelchair_entrance=bool(p.get("wheelchair")))
        docs.append(d)

    def fallback() -> str:
        """Answer in plain, friendly sentences about what was asked (used when GPT-4o isn't available)."""
        if not docs:
            return ("I couldn't find a good match for that in Arizona. Try naming a city or a place, "
                    "or ask me about a stop in your plan.")
        ql_ = ql
        topic = next((k for k, rx in (("kids", r"\bkids?\b|child|family|toddler|stroller"),
                                       ("access", r"wheelchair|accessib|mobility|walker"),
                                       ("groups", r"\bgroup|big party|large party"),
                                       ("cost", r"\bcost|price|expensive|cheap|how much|\$"),
                                       ("hours", r"\bopen\b|hours|close|closing|when does"))
                      if re.search(rx, ql_)), None)
        flag = {"kids": "good_for_kids", "access": "wheelchair_entrance", "groups": "good_for_groups"}.get(topic)
        in_plan = [d for d in docs if d.get("in_plan_as")]
        pool = (in_plan or docs)[:4]
        if flag and in_plan:
            yes = [d for d in in_plan if d.get(flag)]
            what = {"kids": "kid-friendly", "access": "wheelchair accessible", "groups": "good for groups"}[topic]
            if len(in_plan) == 1:
                lead = (f"Yes, {in_plan[0]['name']} is listed as {what}." if yes else
                        f"{in_plan[0]['name']} isn't listed as {what}. Want me to swap it for a place that is?")
            elif len(yes) == len(in_plan):
                lead = f"Good news: every stop in your plan is listed as {what}."
            elif yes:
                lead = (f"{len(yes)} of your {len(in_plan)} stops are listed as {what}. "
                        "Want me to swap the others for ones that are?")
            else:
                lead = f"None of these stops are listed as {what}. I can swap them for ones that are, just say the word."
        elif in_plan and len(in_plan) > 1:
            lead = "Here's a quick look at your stops:"
        elif in_plan:
            lead = f"Here's what I know about {in_plan[0]['name']}:"
        else:
            lead = "Here are a few places that fit:"
        out = []
        for d in pool:
            when = (d.get("scheduled_time") or "").split(" - ")[0]
            head = f"**{d['name']}**" + (f" ({d['in_plan_as'].lower()}, {when})" if d.get("in_plan_as") and when else f" in {d['city']}")
            bits = [f"rated {d['rating']}★ by {int(d['reviews']):,} people"]
            if d.get("est_cost_pp"):
                bits.append(f"about ${d['est_cost_pp']:.0f} per person")
            elif d.get("in_plan_as"):
                bits.append("free")
            sent = head + ": " + ", ".join(bits) + "."
            nice = [lbl for k, lbl in (("good_for_kids", "great for kids"), ("wheelchair_entrance", "wheelchair accessible"),
                                       ("good_for_groups", "good for groups")) if d.get(k)]
            if nice:
                sent += f" It's {', '.join(nice[:-1]) + ' and ' + nice[-1] if len(nice) > 1 else nice[0]}."
            desc = d.get("description") or ""
            if desc and desc.lower() != (d.get("category") or "").lower() and len(desc) > 25:
                sent += f" {snip(desc, 140).rstrip('.')}."
            if d.get("retrieved_reviews"):
                m = re.match(r"(\d)★ \((\d{4}|None)\): (.*)", d["retrieved_reviews"][0], re.S)
                if m:
                    yr = f", {m.group(2)}" if m.group(2) != "None" else ""
                    sent += f" One visitor ({m.group(1)}★{yr}) wrote: \"{snip(m.group(3), 170)}\""
            elif d.get("ev_love"):
                sent += f" Visitors love {snip(d['ev_love'], 120).rstrip('.')}."
            if topic == "hours" and d.get("hours"):
                sent += f" Listed hours: {snip(d['hours'], 120)} (check before you go)."
            out.append(sent)
        tail = ("\n\nWant me to swap any of these, find something nearby, or book a table?" if in_plan
                else "\n\nWant me to add one of these to your plan?")
        return lead + "\n\n" + "\n\n".join(out) + tail

    answer, how = text(prompts.QA, f"Question: {q}\n\nRetrieved records:\n{json.dumps(docs, default=str)[:10000]}", fallback)
    hist = (state.get("history") or []) + [{"role": "user", "content": state["user_message"]}, {"role": "assistant", "content": answer}]
    trace = [{"agent": "Q&A", "via": how, "did": f"answered from {len(docs)} retrieved records and "
                                                            f"{sum(len(d.get('retrieved_reviews') or []) for d in docs)} retrieved reviews"}]
    return {"answer": answer, "history": hist[-12:], "trace": state.get("trace", []) + trace}
