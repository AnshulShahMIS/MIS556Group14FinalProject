"""Parse the workbook's Hours column ("Monday: 9AM–5PM; Tuesday: Closed; ...") and answer
"is this place open for the whole visit?".

Range formats (from the team's first prototype): "8AM–6PM", "Open 24 hours", "Closed",
"11AM–2PM,5–9PM", "6:30AM–12PM", "5–10PM" (start inherits PM), "10PM–2AM" (past midnight).
"""
from __future__ import annotations

import re

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_TIME = re.compile(r"^\s*(\d{1,2})(?::(\d{2}))?\s*([AP]M)?\s*$", re.I)
_DASH = re.compile(r"\s*[–—-]\s*")

Schedule = dict[str, list[tuple[int, int]]]


def _to_minutes(text: str, default_ampm: str | None) -> tuple[int, str | None]:
    m = _TIME.match(text)
    if not m:
        raise ValueError(text)
    h, mins, ampm = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or default_ampm)
    if ampm:
        ampm = ampm.upper()
        if h == 12:
            h = 0
        if ampm == "PM":
            h += 12
    return h * 60 + mins, ampm


def parse_ranges(spec: str) -> list[tuple[int, int]] | None:
    s = (spec or "").strip()
    if not s:
        return None
    low = s.lower()
    if low.startswith("closed"):
        return []
    if "24 hours" in low:
        return [(0, 1440)]
    out = []
    for part in s.split(","):
        bits = _DASH.split(part.strip())
        if len(bits) != 2:
            return None
        try:
            end, end_ampm = _to_minutes(bits[1], None)
            start, _ = _to_minutes(bits[0], end_ampm)
            if start > end and _TIME.match(bits[0]).group(3) is None and start >= 720:
                start -= 720  # "11–2PM" means 11AM
        except ValueError:
            return None
        if end <= start:
            end += 1440
        out.append((start, end))
    return out


def parse_hours(text: str | None) -> Schedule | None:
    if not text or not isinstance(text, str):
        return None
    sched: Schedule = {}
    for chunk in text.split(";"):
        day, _, spec = chunk.strip().partition(":")
        day = day.strip()
        if day in DAYS:
            r = parse_ranges(spec)
            if r is not None:
                sched[day] = r
    return sched or None


def open_for(sched: Schedule | None, weekday: int, start_min: int, duration_min: int) -> bool | None:
    """True if open from start to start+duration on that weekday (0=Mon). None = unknown."""
    if not sched:
        return None
    day = DAYS[weekday]
    if day not in sched:
        return None
    end = start_min + duration_min
    if any(s <= start_min and end <= e for s, e in sched[day]):
        return True
    prev = sched.get(DAYS[(weekday - 1) % 7], [])
    if any(e > 1440 and end <= e - 1440 for _, e in prev):
        return True
    return False


def hours_on(text: str | None, weekday: int) -> str | None:
    if not text:
        return None
    day = DAYS[weekday]
    for chunk in text.split(";"):
        d, _, spec = chunk.strip().partition(":")
        if d.strip() == day:
            return spec.strip()
    return None


def fmt_time(minutes: int) -> str:
    minutes %= 1440
    h, m = divmod(minutes, 60)
    ampm = "AM" if h < 12 else "PM"
    return f"{(h % 12) or 12}:{m:02d} {ampm}"
