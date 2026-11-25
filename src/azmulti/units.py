"""Distances for display: miles first (US audience), km alongside."""


def snip(text: str | None, limit: int = 180) -> str:
    """Shorten text at a word boundary (never mid-word), adding an ellipsis when cut."""
    t = " ".join((text or "").split())
    if len(t) <= limit:
        return t
    cut = t[:limit].rsplit(" ", 1)[0].rstrip(",;:-–—")
    return cut + "…"


def city_stays(legs: list | None) -> dict[int, tuple[str, int]]:
    """Multi-city trips: {first day of a city's stay: (city, number of days)}."""
    out, day = {}, 1
    for lg in legs or []:
        out[day] = (lg["city"], int(lg["days"]))
        day += int(lg["days"])
    return out


def dist(km) -> str:
    km = float(km or 0)
    return f"{km * 0.621371:.1f} mi ({km:.1f} km)"
