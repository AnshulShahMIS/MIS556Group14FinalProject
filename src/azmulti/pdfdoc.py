"""The trip as a PDF: day by day (times, places with links, why each was picked, costs), the budget, the rental car,
what to book before you go, and the route link - for "Save as PDF" and the downloads after Confirm trip.

Built with fpdf2 (pure Python, no browser needed). Its built-in fonts only cover Latin-1, so other characters
(arrows, stars, dashes, emoji) are swapped for plain ones by _t().
"""
from __future__ import annotations

import unicodedata
from datetime import datetime, timedelta

from fpdf import FPDF

from .security import safe_url

CLAY, INK, MUTED, LINE, CREAM = (180, 83, 42), (42, 36, 32), (110, 98, 89), (234, 223, 211), (251, 247, 242)
SWAP = {"→": "to", "←": "<-", "★": "*", "☆": "*", "–": "-", "—": "-", "‘": "'", "’": "'", "“": '"', "”": '"',
        "…": "...", "•": "-", "·": "-", "≈": "~", "°": " deg", "×": "x", " ": " "}


def _t(s) -> str:
    """Text the built-in PDF fonts can draw."""
    out = []
    for ch in str(s if s is not None else ""):
        ch = SWAP.get(ch, ch)
        if all(ord(c) < 256 for c in ch):
            out.append(ch)
        else:  # accents keep their letter (e.g. ā -> a); anything else is dropped
            out.append(unicodedata.normalize("NFKD", ch).encode("latin-1", "ignore").decode("latin-1"))
    return "".join(out).replace("°", " deg")


def _title(st: dict) -> tuple[str, str, datetime | None]:
    req, cond = st.get("request") or {}, st.get("conditions") or {}
    title = " to ".join(lg["city"] for lg in req.get("legs") or []) or req.get("city") or "Arizona"
    first = cond.get("date") or req.get("trip_date")
    d0 = datetime.fromisoformat(str(first)[:10]) if first else None
    days = max(1, int(req.get("days") or 1))
    when = ""
    if d0:
        d1 = d0 + timedelta(days=days - 1)
        when = f"{d0:%a, %b} {d0.day}" + (f" - {d1:%a, %b} {d1.day}, {d1.year}" if days > 1 else f", {d0.year}")
    return title, when, d0


def _day(iso) -> str:
    try:
        d = datetime.fromisoformat(str(iso)[:10])
        return f"{d:%a, %b} {d.day}"
    except ValueError:
        return str(iso or "")


class _Doc(FPDF):
    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*MUTED)
        self.cell(0, 5, _t(f"Planned with Saguaro (MIS 556, Group 14). Hours and prices can change - check before you go.   "
                           f"Page {self.page_no()}/{{nb}}"), align="C")


def _heading(pdf: FPDF, text: str, size: float = 14):
    if pdf.get_y() > pdf.h - 40:
        pdf.add_page()
    pdf.ln(3)
    pdf.set_font("Times", "B", size)
    pdf.set_text_color(*INK)
    pdf.cell(0, 8, _t(text), new_x="LMARGIN", new_y="NEXT")
    pdf.set_draw_color(*LINE)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(2)


def itinerary_pdf(st: dict, name: str = "", checklist: list[dict] | None = None) -> bytes:
    """The whole trip as PDF bytes."""
    req, b, bk = st.get("request") or {}, st.get("budget") or {}, st.get("booking") or {}
    title, when, d0 = _title(st)
    party = req.get("party_size", 2)
    stops = [s for s in st.get("slots") or [] if s.get("place")]
    res = {r.get("place_id"): r for r in st.get("reservations") or []}

    pdf = _Doc(format="Letter")
    pdf.set_title(_t(f"{title} - Saguaro itinerary"))
    pdf.set_author("Saguaro")
    pdf.set_margins(16, 14, 16)
    pdf.set_auto_page_break(True, margin=18)
    pdf.add_page()
    W = pdf.w - pdf.l_margin - pdf.r_margin
    T = 35  # time column

    # header band
    pdf.set_fill_color(*CLAY)
    pdf.rect(0, 0, pdf.w, 38, style="F")
    pdf.set_xy(pdf.l_margin, 9)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 5, "SAGUARO  -  YOUR ARIZONA TRIP", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Times", "B", 22)
    pdf.cell(0, 10, _t(title), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    who = f"{party} {'travelers' if party > 1 else 'traveler'}" + (f" - for {name}" if name else "")
    pdf.cell(0, 6, _t(" - ".join(x for x in (when, who) if x)), new_x="LMARGIN", new_y="NEXT")
    pdf.set_y(44)

    # day by day
    day = None
    for s in stops:
        p = s["place"]
        if s.get("day") != day:
            day = s.get("day")
            dd = d0 + timedelta(days=(day or 1) - 1) if d0 else None
            _heading(pdf, f"Day {day}" + (f"  -  {dd:%A, %B} {dd.day}" if dd else ""))
        if pdf.get_y() > pdf.h - 36:
            pdf.add_page()
        y = pdf.get_y()
        cost = f"~${s['cost_pp']:.0f} pp" if s.get("cost_pp") else "free"
        pdf.set_font("Helvetica", "B", 8.5)
        pdf.set_text_color(*MUTED)
        pdf.set_xy(pdf.l_margin, y)
        pdf.cell(T, 5.5, _t(s.get("time", "")))
        url = safe_url(p.get("url"))
        pdf.set_text_color(*(CLAY if url else INK))
        pdf.set_font("Helvetica", "B", 10.5)
        pdf.set_xy(pdf.l_margin + T, y)
        pdf.cell(W - T - 24, 5.5, _t(p["name"]), link=url or "")
        pdf.set_text_color(*INK)
        pdf.set_font("Helvetica", "", 9.5)
        pdf.set_xy(pdf.l_margin + W - 24, y)
        pdf.cell(24, 5.5, _t(cost), align="R", new_x="LMARGIN", new_y="NEXT")
        booked = res.get(p.get("id"))
        status = ("  -  booked" + (f" ({booked['confirmation']})" if booked.get("confirmation") else "")
                  if booked and booked.get("status") == "booked" else "  -  reservation requested" if booked else "")
        rating = f"  -  {p['rating']:.1f}/5" if p.get("rating") else ""
        pdf.set_x(pdf.l_margin + T)
        pdf.set_font("Helvetica", "", 8.5)
        pdf.set_text_color(*MUTED)
        pdf.cell(W - T, 4.5, _t(f"{s.get('label', '')}  -  {p.get('city', '')}{rating}{status}"), new_x="LMARGIN", new_y="NEXT")
        why = str(s.get("reason") or "")
        if why:
            why = why if len(why) <= 260 else why[:257].rsplit(" ", 1)[0] + "..."
            pdf.set_x(pdf.l_margin + T)
            pdf.multi_cell(W - T, 4, _t(why), align="L", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2.5)

    # budget
    if b.get("total_pp") is not None:
        _heading(pdf, "Budget (per person, estimates)")
        rows = [("Food & drink", b.get("food")), ("Things to do", b.get("activities")), ("Hotels", b.get("stay")),
                ("Getting around", b.get("transport")), ("Rental car", b.get("rental_pp"))]
        pdf.set_font("Helvetica", "", 10)
        for label, v in rows:
            if v:
                pdf.set_text_color(*INK)
                pdf.cell(W - 30, 6, _t(label))
                pdf.cell(30, 6, f"${v:,.0f}", align="R", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "B", 10.5)
        pdf.cell(W - 30, 7, "Total per person")
        pdf.cell(30, 7, f"${b['total_pp']:,.0f}", align="R", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*MUTED)
        extra = f"${b.get('group_total', 0):,.0f} for the group of {party}, plus tax and tip."
        if b.get("budget_pp"):
            extra += f" Your budget: ${b['budget_pp']:,.0f} each ({b.get('status', '')})."
        pdf.multi_cell(W, 5, _t(extra), align="L", new_x="LMARGIN", new_y="NEXT")

    # rental car
    rc = st.get("rental")
    if rc:
        _heading(pdf, "Rental car")
        where = f"{rc['airport']} airport" if rc.get("airport") else rc.get("pickup_city", "")
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(*INK)
        pdf.multi_cell(W, 5.5, align="L", text=_t(f"{rc['cars']} {rc['size']}{'s' if rc['cars'] > 1 else ''} for {rc['days']} "
                                  f"day{'s' if rc['days'] > 1 else ''}, pick up at {where} on {_day(rc.get('pickup_date'))}, "
                                  f"return {_day(rc.get('return_date'))}. About ${rc['total']:,.0f} before gas "
                                  f"(${rc['per_person']:,.0f} each). Fits {rc.get('fits', '')}."), new_x="LMARGIN", new_y="NEXT")
        if rc.get("link"):
            pdf.set_text_color(*CLAY)
            pdf.cell(0, 6, "Compare rental companies on Kayak", link=rc["link"], new_x="LMARGIN", new_y="NEXT")

    # before you go
    todo = [i for i in checklist or [] if not i.get("done")]
    if todo:
        _heading(pdf, "Before you go")
        for it in sorted(todo, key=lambda i: i.get("due") or "9999"):
            if pdf.get_y() > pdf.h - 30:
                pdf.add_page()
            due = ""
            if it.get("due"):
                dd = datetime.fromisoformat(it["due"])
                due = f"by {dd:%a, %b} {dd.day}"
            pdf.set_font("Helvetica", "", 10)
            pdf.set_text_color(*INK)
            pdf.cell(6, 5.5, "[  ]")
            pdf.set_font("Helvetica", "B", 10)
            link = safe_url(it.get("link"))
            pdf.cell(W - 6 - 34, 5.5, _t(it["text"]), link=link or "")
            pdf.set_font("Helvetica", "", 9)
            pdf.set_text_color(*CLAY)
            pdf.cell(34, 5.5, _t(due), align="R", new_x="LMARGIN", new_y="NEXT")

    route = safe_url(bk.get("route_url"))
    if route:
        pdf.ln(4)
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(*CLAY)
        pdf.cell(0, 6, "Open the route in Google Maps", link=route, new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())
