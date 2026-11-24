"""Local event intelligence: recurring Arizona events by month.

This is a curated list of *recurring* events with their usual timing. Exact dates change every
year, so the agent always says "usual timing - check the official site" and never claims a date.
"""
from __future__ import annotations

# name, months (1-12), metro/city, interest tags, note
EVENTS = [
    ("Cactus League spring training (MLB)", [2, 3], "Phoenix metro", ["sports"], "15 MLB teams play at 10 Phoenix-area ballparks; lawn seats are a budget favorite."),
    ("WM Phoenix Open (PGA golf)", [2], "Scottsdale", ["sports", "nightlife"], "Famous 16th-hole 'stadium' crowd at TPC Scottsdale."),
    ("Tucson Gem, Mineral & Fossil Showcase", [1, 2], "Tucson", ["shopping", "science"], "Dozens of shows across Tucson, one of the biggest of its kind."),
    ("Scottsdale Arabian Horse Show", [2], "Scottsdale", ["animals", "family"], "Large Arabian horse show at WestWorld."),
    ("Sedona International Film Festival", [2, 3], "Sedona", ["arts"], "Week of screenings and filmmaker talks."),
    ("Tucson Festival of Books", [3], "Tucson", ["arts", "family", "free"], "Free book festival on the University of Arizona campus."),
    ("Arizona Diamondbacks home games (MLB)", [4, 5, 6, 7, 8, 9], "Phoenix", ["sports"], "Air-conditioned Chase Field in downtown Phoenix - a good hot-weather pick."),
    ("Phoenix Suns home games (NBA)", [10, 11, 12, 1, 2, 3, 4], "Phoenix", ["sports", "nightlife"], "Footprint Center, downtown Phoenix."),
    ("Phoenix Mercury home games (WNBA)", [5, 6, 7, 8, 9], "Phoenix", ["sports", "family"], "Footprint Center, downtown Phoenix."),
    ("Arizona Cardinals home games (NFL)", [9, 10, 11, 12, 1], "Glendale", ["sports"], "State Farm Stadium, Glendale."),
    ("ASU Sun Devils football", [9, 10, 11], "Tempe", ["sports", "college"], "Mountain America Stadium in Tempe."),
    ("Arizona Wildcats football", [9, 10, 11], "Tucson", ["sports", "college"], "Arizona Stadium on the UA campus."),
    ("Arizona State Fair", [9, 10], "Phoenix", ["family", "food"], "Rides, fair food and concerts at the state fairgrounds."),
    ("Phoenix Pride", [10], "Phoenix", ["nightlife", "arts", "free"], "Festival and parade, usually in October."),
    ("Las Noches de las Luminarias", [11, 12], "Phoenix", ["gardens", "family"], "Thousands of luminarias at the Desert Botanical Garden (ticketed)."),
    ("Glendale Glitters holiday lights", [11, 12, 1], "Glendale", ["family", "free"], "Holiday lights in historic downtown Glendale."),
    ("Fiesta Bowl & Fiesta Bowl Parade", [12, 1], "Phoenix metro", ["sports", "family"], "College bowl game around New Year plus a downtown Phoenix parade."),
    ("Tucson Meet Yourself folklife festival", [10], "Tucson", ["food", "arts", "free"], "Free festival of Tucson's food and cultures."),
    ("All Souls Procession", [11], "Tucson", ["arts", "free"], "Large community procession in downtown Tucson, early November."),
    ("Flagstaff summer & monsoon season", [7, 8], "Flagstaff", ["outdoors"], "Cooler pines (about 80°F) when the desert is 105°F+; afternoon storms are common."),
    ("Wildflower season in the Sonoran Desert", [3, 4], "Phoenix metro", ["outdoors", "free"], "Peak poppies and blooms after wet winters - Picacho Peak, Lost Dutchman."),
    ("Grand Canyon North Rim season", [5, 6, 7, 8, 9, 10], "Grand Canyon Village", ["outdoors"], "The North Rim is only open mid-May to mid-October."),
]

METRO = {"Phoenix metro": {"Phoenix", "Scottsdale", "Tempe", "Mesa", "Chandler", "Gilbert", "Glendale", "Peoria",
                           "Surprise", "Goodyear", "Avondale", "Paradise Valley", "Fountain Hills", "Cave Creek",
                           "Queen Creek", "Buckeye", "Apache Junction", "Litchfield Park", "Tolleson", "Sun City"}}


def _matches_place(ev_place: str, city: str | None) -> bool:
    if not city:
        return True
    if ev_place == city:
        return True
    for metro, cities in METRO.items():
        if ev_place == metro and city in cities:
            return True
        if ev_place in cities and city in cities:  # a Scottsdale event is relevant to a Tempe trip
            return True
    return False


def live_events(lat: float, lon: float, start_date: str, days: int = 1, keyword: str | None = None,
                radius_mi: int = 30) -> list[dict] | None:
    """Real events from Ticketmaster for the trip date(s); None when no key / API error."""
    from datetime import datetime, timedelta

    from . import live
    start = datetime.fromisoformat(start_date).replace(tzinfo=live.AZ_TZ)
    evs = live.events(lat, lon, start, start + timedelta(days=days), radius_mi=radius_mi, keyword=keyword)
    if evs is None:
        return None
    for e in evs:
        price = f"from ${e['price_min']:.0f}" if e.get("price_min") else "price on site"
        when = e.get("time")[:5] if e.get("time") else "time TBA"
        e.update(where=e.get("venue") or "", note=f"{e.get('date')} {when} at {e.get('venue')} ({e.get('city')}) - {price}", relevant=True)
    return evs


def events_for(month: int, city: str | None = None, interests: list[str] | None = None) -> list[dict]:
    out = []
    for name, months, place, tags, note in EVENTS:
        if month in months and _matches_place(place, city):
            out.append({"name": name, "where": place, "tags": tags, "note": note,
                        "relevant": bool(interests and set(tags) & {i.split("_")[0] for i in interests})})
    out.sort(key=lambda e: not e["relevant"])
    return out
