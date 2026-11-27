"""The confirmed trip's itinerary page: the whole trip (day by day, with times, places, links and costs), the budget,
reservations and the route, plus the calendar file (.ics) - both offered as downloads after Confirm trip."""
from __future__ import annotations

from datetime import datetime, timedelta
from html import escape

from .security import safe_url


def _trip_title(st: dict) -> tuple[str, str]:
    req, cond = st.get("request") or {}, st.get("conditions") or {}
    title = " → ".join(lg["city"] for lg in req.get("legs") or []) or req.get("city") or "Arizona"
    first = cond.get("date") or req.get("trip_date")
    when = ""
    if first:
        d0 = datetime.fromisoformat(str(first)[:10])
        days = req.get("days", 1)
        d1 = d0 + timedelta(days=days - 1)
        when = f"{d0:%a, %b} {d0.day}" + (f" – {d1:%a, %b} {d1.day}" if days > 1 else "")
    return title, when


def build(st: dict, name: str = "") -> tuple[str, str, str, list[tuple[str, str, bytes]]]:
    """(title, plain text, html page, attachments) for a confirmed trip."""
    req, b, bk = st.get("request") or {}, st.get("budget") or {}, st.get("booking") or {}
    title, when = _trip_title(st)
    party = req.get("party_size", 2)
    first = (st.get("conditions") or {}).get("date") or req.get("trip_date")
    d0 = datetime.fromisoformat(str(first)[:10]) if first else None
    stops = [s for s in st.get("slots") or [] if s.get("place")]
    res = {r.get("place_id"): r for r in st.get("reservations") or []}

    lines = [f"Hi{' ' + name if name else ''},", "", f"Your Saguaro trip is confirmed: {title}" + (f", {when}" if when else ""),
             f"{party} {'travelers' if party > 1 else 'traveler'}", ""]
    rows, day = [], None
    for n, s in enumerate(stops, 1):
        p = s["place"]
        if s.get("day") != day:
            day = s.get("day")
            label = f"Day {day}" + (f" · {(d0 + timedelta(days=day - 1)):%A, %B} {(d0 + timedelta(days=day - 1)).day}" if d0 else "")
            lines += ["", label.upper()]
            rows.append(f"<tr><td colspan='3' style='padding:18px 0 6px;font-family:Georgia,serif;font-size:18px;"
                        f"font-weight:700'>{escape(label)}</td></tr>")
        cost = f"~${s['cost_pp']:.0f} per person" if s.get("cost_pp") else "free"
        booked = res.get(p.get("id"))
        status = (" · BOOKED" + (f" ({booked['confirmation']})" if booked.get("confirmation") else "")
                  if booked and booked.get("status") == "booked" else " · reservation requested" if booked else "")
        url = safe_url(p.get("url"))
        lines.append(f"{s.get('time', '')}  {p['name']} ({p.get('city', '')}) - {cost}{status}" + (f"\n    {url}" if url else ""))
        name_html = f"<a href='{escape(url)}' style='color:#8A3E1F'>{escape(p['name'])}</a>" if url else escape(p["name"])
        rows.append(f"<tr><td style='padding:6px 12px 6px 0;white-space:nowrap;vertical-align:top;color:#6E6259'>"
                    f"{escape(s.get('time', ''))}</td><td style='padding:6px 12px 6px 0'><b>{name_html}</b><br>"
                    f"<span style='color:#6E6259;font-size:13px'>{escape(s.get('label', ''))} · {escape(p.get('city', ''))}"
                    f"{escape(status)}</span></td><td style='padding:6px 0;white-space:nowrap;vertical-align:top'>"
                    f"{escape(cost)}</td></tr>")
    total = (f"About ${b['total_pp']:.0f} per person (${b.get('group_total', 0):.0f} for the group), plus tax and tip."
             if b.get("total_pp") is not None else "")
    route = safe_url(bk.get("route_url"))
    lines += ["", total] if total else []
    lines += [f"Route in Google Maps: {route}"] if route else []
    lines += ["", "Hours and prices can change - please check before you go.", "Planned with Saguaro (MIS 556, Group 14)."]

    html = ("<div style='font-family:system-ui,-apple-system,Segoe UI,sans-serif;color:#2A2420;max-width:640px;margin:0 auto'>"
            "<div style='background:#B4532A;color:#fff;padding:18px 22px;border-radius:12px 12px 0 0'>"
            "<div style='font-size:13px;opacity:.9'>Trip confirmed</div>"
            f"<div style='font-family:Georgia,serif;font-size:26px;font-weight:700'>{escape(title)}</div>"
            f"<div style='font-size:14px;opacity:.95'>{escape(when)}{' · ' if when else ''}{party} "
            f"{'travelers' if party > 1 else 'traveler'}</div></div>"
            "<div style='border:1px solid #EADFD3;border-top:0;border-radius:0 0 12px 12px;padding:6px 22px 20px;background:#FBF7F2'>"
            f"<p>Hi{' ' + escape(name) if name else ''}, here's your itinerary. The calendar file (download it in Saguaro) adds "
            "every stop to your calendar.</p>"
            f"<table style='width:100%;border-collapse:collapse;font-size:14px'>{''.join(rows)}</table>"
            + (f"<p style='margin-top:16px'><b>{escape(total)}</b></p>" if total else "")
            + (f"<p><a href='{escape(route)}' style='color:#8A3E1F'>Open the route in Google Maps</a></p>" if route else "")
            + "<p style='color:#6E6259;font-size:12px'>Hours and prices can change - please check before you go. "
              "Planned with Saguaro (MIS 556, Group 14).</p></div></div>")
    attachments = [("saguaro-trip.ics", "text/calendar", bk["ics"].encode("utf-8"))] if bk.get("ics") else []
    subject = f"Trip confirmed: {title}" + (f" · {when}" if when else "")
    return subject, "\n".join(lines), html, attachments
