"""PLANNER / supervisor agent.

1. Understands the message (GPT-4o structured output, or rules offline) -> intent + TripRequest.
2. For a new plan, lays out the day skeleton: which stops, in what order, at what target time,
   with a search query per stop. Arizona heat awareness moves outdoor stops to early morning
   from May to September and keeps the afternoon stop indoors.
3. For a revision, clears only the stops the traveler wants changed (others stay locked).
"""
from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from .. import prompts
from ..catalog import get_catalog
from ..categories import CUISINES
from ..config import TIMEZONE
from ..llm import structured
from ..state import Intake, PlannerState, Revision, TripRequest

NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
       "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "a couple": 2, "couple": 2, "pair": 2}
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september",
          "october", "november", "december"]
INTEREST_WORDS = {
    "outdoors": ["hike", "hiking", "trail", "outdoor", "nature", "scenic", "views", "canyon", "lake", "park", "desert", "sunset", "sunrise", "red rock"],
    "gardens": ["garden", "botanical", "flowers", "cactus"],
    "history_culture": ["museum", "history", "historic", "culture", "mission", "ruins", "native"],
    "arts": ["art", "gallery", "theater", "theatre", "show", "concert", "music"],
    "science_sky": ["science", "stars", "observatory", "planetarium", "astronomy", "space"],
    "animals": ["zoo", "aquarium", "animals", "wildlife"],
    "family_fun": ["fun", "arcade", "bowling", "mini golf", "go-kart", "go kart", "escape room", "amusement", "water park", "entertainment"],
    "tours_adventure": ["tour", "jeep", "kayak", "raft", "balloon", "horseback", "atv", "adventure", "helicopter"],
    "food_drink": ["winery", "wineries", "wine tasting", "brewery", "breweries", "distillery", "farmers market", "vineyard"],
    "sports_nightlife": ["casino", "game", "spring training", "stadium", "golf", "sports", "baseball", "ballpark", "basketball", "football", "hockey"],
    "shopping": ["shopping", "shop", "mall", "outlet", "market"],
}
CUISINE_ALIASES = {"sushi": "japanese", "ramen": "japanese", "tacos": "mexican", "taco": "mexican",
                   "pizza": "italian", "pasta": "italian", "bbq": "bbq", "barbecue": "bbq", "steak": "steakhouse",
                   "burgers": "american", "burger": "american", "pho": "vietnamese", "curry": "indian",
                   "brunch": "breakfast", "vegan": "vegan", "greek": "mediterranean", "ice cream": "dessert"}
CITY_ALIASES = {"grand canyon": "Grand Canyon Village", "phx": "Phoenix", "old town scottsdale": "Scottsdale",
                "downtown phoenix": "Phoenix", "asu": "Tempe", "u of a": "Tucson", "uofa": "Tucson"}

# ----------------------------------------------------------------------------------
# Rule-based understanding (used offline and as a safety net)
# ----------------------------------------------------------------------------------


def _num(tok: str) -> int | None:
    tok = tok.lower().strip()
    return int(tok) if tok.isdigit() else NUM.get(tok)


RENTAL_RX = re.compile(r"\b(rental car|rent(?:ing)? a (?:car|vehicle|suv|minivan)|car rental|hire a car|"
                       r"(?:need|get|want) (?:a )?(?:rental|car to drive)|pick up a car)\b")
MAX_DAYS = 14
_MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_TO = r"\s*(?:-|–|—|to|through|thru|until|till)\s*"


def _range_end(t: str, start):
    """The end of a date range that starts at `start` ("... to 2026-03-06", "March 3-6", "Mar 3 to Apr 2")."""
    from datetime import date as _date
    pats = [(rf"\b20\d\d-\d\d-\d\d{_TO}(20\d\d)-(\d\d)-(\d\d)\b", "iso"),
            (rf"\b{_MON}\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s*20\d\d)?{_TO}{_MON}\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", "mon"),
            (rf"\b{_MON}\s+\d{{1,2}}(?:st|nd|rd|th)?{_TO}(\d{{1,2}})(?:st|nd|rd|th)?\b(?!\s*(?:people|friends|adults|kids|am|pm|:))", "day")]
    for rx, kind in pats:
        m = re.search(rx, t)
        if not m:
            continue
        try:
            if kind == "iso":
                end = _date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            elif kind == "mon":
                mon = [x[:3] for x in MONTHS].index(m.group(2)[:3]) + 1
                end = _date(start.year, mon, int(m.group(3)))
            else:
                end = _date(start.year, start.month, int(m.group(2)))
            if end < start:
                end = end.replace(year=end.year + 1)
            return end if end >= start else None
        except ValueError:
            return None
    return None


def parse_request_rules(text: str) -> TripRequest:
    t = text.lower()
    cat = get_catalog()
    r = TripRequest()

    for alias, city in CITY_ALIASES.items():
        if alias in t:
            r.city = city
            break
    else:
        for c in cat.cities:
            if re.search(rf"\b{re.escape(c.lower())}\b", t):
                r.city = c
                break

    for i, d in enumerate(DAYS):
        if re.search(rf"\b{d}s?\b", t):
            r.weekday = i
            break
    if "weekend" in t:
        r.weekday, r.days = 5, 2
    now = datetime.now(ZoneInfo(TIMEZONE))
    if "tomorrow" in t:
        r.weekday = (now.weekday() + 1) % 7
    elif "today" in t or "tonight" in t:
        r.weekday = now.weekday()
    for i, m in enumerate(MONTHS):
        if re.search(rf"\b{m}\b", t):
            r.month = i + 1
    for word, mo in {"summer": 7, "winter": 1, "spring": 3, "fall": 10, "autumn": 10}.items():
        if word in t and r.month is None:
            r.month = mo
    m_wk = re.search(r"\b(?:a|one|1)[- ]?week(?:[- ]long)?\b|\bweek[- ]long\b|\bfor (?:a|one) week\b|\b(two|2)[- ]weeks?\b", t)
    if m_wk:
        r.days = MAX_DAYS if m_wk.group(1) else 7
    m = re.search(r"\b(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)[- ]days?\b", t)
    if m and not re.search(r"\b(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)[- ]days?\s+(in|at)\b", t):
        r.days = max(1, min(MAX_DAYS, _num(m.group(1)) or 1))
    # Exact dates: "2026-12-12", "December 12", "Dec 12th"
    from datetime import date as _date
    exact = None
    m = re.search(r"\b(20\d\d)-(\d\d)-(\d\d)\b", t)
    if m:
        try:
            exact = _date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            exact = None
    else:
        m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b", t)
        if m:
            mon = [x[:3] for x in MONTHS].index(m.group(1)[:3]) + 1
            today = now.date()
            try:
                exact = _date(today.year, mon, int(m.group(2)))
                if exact < today:
                    exact = _date(today.year + 1, mon, int(m.group(2)))
            except ValueError:
                exact = None
    if exact:
        r.trip_date, r.weekday, r.month = exact.isoformat(), exact.weekday(), exact.month
        end = _range_end(t, exact)
        if end:  # "2026-03-03 to 2026-03-06", "March 3-6", "Mar 3 through Mar 6"
            r.end_date = end.isoformat()
            r.days = max(1, min(MAX_DAYS, (end - exact).days + 1))

    who = r"(?:of us|people|friends|adults|guys|girls|ladies|women|men|persons|travelers|coworkers|colleagues|students|seniors)"
    m = (re.search(rf"\b(?:for|with|of)\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+{who}", t)
         or re.search(rf"\b(\d+|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+{who}\b", t)
         or re.search(r"\b(?:family|party|group|reunion|team) of (\d+|three|four|five|six|seven|eight|nine|ten|eleven|twelve)", t))
    if not m:  # "for 4", "for four" (not "for 4 hours", "for $40")
        m = re.search(r"\bfor (\d{1,2}|two|three|four|five|six|seven|eight|nine|ten)\b(?!\s*(?:am|pm|hours?|hrs?|days?|nights?|miles?|mi|km|stars?|dollars|bucks|minutes?|mins?|%|:))", t)
    if m:
        r.party_size = _num(m.group(1)) or 2
    elif any(w in t for w in ("date night", "my wife", "my husband", "my partner", "girlfriend", "boyfriend", "anniversary", "couple")):
        r.party_size = 2
    elif re.search(r"\b(solo|by myself|just me|alone)\b", t):
        r.party_size = 1
    elif re.search(r"my (grand)?parents", t):
        r.party_size = 3

    if re.search(r"\b(kids?|children|child|toddlers?|baby|babies|family)\b", t):
        r.has_kids = True
        r.min_age = 3 if re.search(r"toddler|baby|babies", t) else 8
        k = re.search(r"\b(\d|one|two|three|four|five)\s+(?:little\s+|young\s+|small\s+)?(?:kids?|children|child|toddlers?|boys?|girls?|sons?|daughters?)", t)
        if not m:  # "with two kids" -> two adults + kids, unless a group size was given
            r.party_size = 2 + ((_num(k.group(1)) or 1) if k else (2 if re.search(r"kids|children|toddlers|babies", t) else 1))
    m = re.search(r"(?:all|we're|we are|everyone is|aged?)\s+(\d{2})\b", t)
    if m:
        r.min_age = int(m.group(1))

    m = re.search(r"\$\s?(\d[\d,]*)", t) or re.search(r"(\d[\d,]*)\s?(?:dollars|bucks|usd)", t)
    if m:
        amt = float(m.group(1).replace(",", ""))
        if re.search(r"per person|each|a head|pp\b|/person|per head", t):
            r.budget_per_person = amt
        elif re.search(r"total|for (all|the|us|both)|together|group", t):
            r.budget_per_person = round(amt / max(r.party_size, 1), 2)
        else:
            r.budget_per_person = amt if r.party_size <= 2 and "date" not in t else amt / max(r.party_size, 1)
    if re.search(r"cheap|on a budget|low budget|tight budget|budget[- ]friendly|free (things|stuff|activities|attractions)|for free|student|inexpensive|broke|frugal", t):
        r.tier = "budget"
    elif re.search(r"luxury|splurge|upscale|fancy|fine dining|premium|anniversary|special", t):
        r.tier = "premium"

    r.interests = [k for k, words in INTEREST_WORDS.items() if any(re.search(rf"\b{re.escape(w)}", t) for w in words)]
    cuis = [c for c in CUISINES if re.search(rf"\b{re.escape(c)}\b", t)]
    cuis += [v for k, v in CUISINE_ALIASES.items() if re.search(rf"\b{re.escape(k)}\b", t)]
    r.cuisines = [c for c in dict.fromkeys(cuis) if c not in ("breakfast",)] or []
    r.meals = [m for m in ("breakfast", "lunch", "dinner") if m in t or (m == "breakfast" and "brunch" in t)]
    r.nightlife = bool(re.search(r"go(ing)? out|nightlife|bars?\b|club|drinks|cocktails|pub crawl|dancing", t))
    r.dietary = [d for d in ("vegetarian", "vegan", "gluten") if d in t]
    r.wheelchair = bool(re.search(r"wheelchair|accessible|mobility|walker", t))
    r.hidden_gems = bool(re.search(r"hidden gem|off the beaten|local favorite|locals|underrated|not touristy", t))
    r.avoid_crowds = bool(re.search(r"not crowded|nothing crowded|avoid crowds|quiet|no crowds", t))

    if "date night" in t or "romantic" in t:
        r.occasion = "date night"
    for occ in ("birthday", "anniversary", "bachelorette", "bachelor", "graduation", "family outing", "team outing"):
        if occ in t:
            r.occasion = occ
    if re.search(r"half[- ]day|morning", t) and not re.search(r"evening|night|dinner", t):
        r.start_time, r.end_time = 480, 840
    elif re.search(r"afternoon", t) and not re.search(r"morning|dinner|night", t):
        r.start_time, r.end_time = 720, 1080
    elif re.search(r"evening|tonight|date night|night out", t) and not re.search(r"morning|all day|full day|lunch", t):
        r.start_time, r.end_time = 1020, 1380
    # Group scheduling: "we're free 2-8pm", "from 10am to 6pm", "until 9pm", "right now"
    def to_min(h: str, ap: str | None, default_ap: str) -> int:
        return (int(h) % 12 + (12 if (ap or default_ap) == "pm" else 0)) * 60
    m = re.search(r"(?:from|free|between)?\s*\b(\d{1,2})\s*(am|pm)?\s*(?:-|–|to|until|and)\s*(\d{1,2})\s*(am|pm)\b", t)
    if m and int(m.group(1)) <= 12 and int(m.group(3)) <= 12:
        r.end_time = to_min(m.group(3), m.group(4), "pm")
        r.start_time = to_min(m.group(1), m.group(2), m.group(4) if int(m.group(1)) < int(m.group(3)) else "am")
    else:
        m = re.search(r"(?:from|start(?:ing)? at|leave at|begin at)\s+(\d{1,2})\s*(am|pm)", t)
        if m:
            r.start_time = to_min(m.group(1), m.group(2), "am")
        m = re.search(r"(?:until|till|til|back by|done by|end by)\s+(\d{1,2})\s*(am|pm)", t)
        if m:
            r.end_time = to_min(m.group(1), m.group(2), "pm")
    if re.search(r"\b(right now|open now|open right now|go now|leaving now)\b", t):
        r.weekday = now.weekday()
        r.start_time = max(r.start_time, (now.hour * 60 + now.minute + 29) // 30 * 30)
    if r.end_time <= r.start_time:
        r.end_time = min(1439, r.start_time + 360)

    # Location filters: ZIP code and radius
    z = re.search(r"\b(8[56]\d{3})\b", t)
    if z:
        r.zip_code = z.group(1)
    m = re.search(r"within\s+(\d+(?:\.\d+)?)\s*(miles?|mi|km|kilometers?)", t)
    if m:
        r.radius_km = float(m.group(1)) * (1.609 if m.group(2).startswith("mi") else 1.0)
    # Transportation preference
    if re.search(r"\b(walk|walking|on foot|walkable)\b", t):
        r.transport = "walking"
    elif re.search(r"\b(uber|lyft|rideshare|ride share|taxi|waymo)\b", t):
        r.transport = "rideshare"
    elif re.search(r"\b(light rail|bus|transit|public transport|metro|streetcar)\b", t):
        r.transport = "transit"
    if RENTAL_RX.search(t):  # "we need a rental car": drive, and add the rental to the budget and checklist
        r.rental_car, r.transport = True, "car"
    # Price, quality, atmosphere, indoor/outdoor, discounts, options
    r.free_only = bool(re.search(r"\bfree (things|stuff|activities|activity|attractions|day)\b|\bonly free\b|\bfor free\b|\bno cost\b", t))
    m = re.search(r"\b(\d(?:\.\d)?)\s*(?:\+\s*stars?|stars?|★)", t)
    if m and 3 <= float(m.group(1)) <= 5:
        r.min_rating = float(m.group(1))
    elif re.search(r"highly rated|top rated|best rated|best reviewed|top-rated", t):
        r.min_rating = 4.5
    for word, atm in (("romantic", "Romantic"), ("upscale", "Upscale"), ("fancy", "Upscale"), ("cozy", "Cozy"),
                      ("casual", "Casual"), ("historic", "Historic"), ("trendy", "Trendy"), ("hip", "Trendy")):
        if re.search(rf"\b{word}\b", t):
            r.atmosphere = atm
            break
    r.indoor_only = bool(re.search(r"\bindoors?\b|inside activities|air[- ]condition", t))
    r.outdoor_pref = bool(re.search(r"\boutdoors?\b|outside", t)) and not r.indoor_only
    r.discounts = [d for d, rx in (("student", r"\bstudents?\b|college"), ("military", r"military|veteran|\bvets?\b"),
                                   ("senior", r"\bseniors?\b|retired|grandparents?|\bparents\b"))
                   if re.search(rx, t)]
    r.want_options = bool(re.search(r"\boptions\b|budget,? balanced|three plans|3 plans|different price", t))
    r.halal = "halal" in t
    r.need_hotel = bool(re.search(r"\b(hotel|motel|resort|place to stay|where to stay|stay overnight|overnight stay|lodging|"
                                  r"b&b|bed and breakfast|staycation|somewhere to sleep)\b", t))
    if re.search(r"\bplan only:|\bonly plan\b|\bjust (plan|need)\b", t):
        r.needs_specified = True
        tail = t.split("plan only:", 1)[-1] if "plan only:" in t else t
        tail = re.split(r",\s*(?:we like|\$|vegetarian|kid friendly)", tail)[0]
        r.want_activities = bool(re.search(r"activit|things to do|sights", tail))
        r.meals = [m for m in ("breakfast", "lunch", "dinner") if m in tail]
        r.want_dessert = "dessert" in tail
    elif "dessert" in t:
        r.want_dessert = True

    if r.min_age is not None and r.min_age < 21:
        r.nightlife = False
    if r.has_kids:
        r.nightlife = False
    # Search phrase for activities: the activity words the traveler used (the LLM path writes
    # a cleaner phrase itself). Food and nightlife words are left to the dining agent.
    words = [w for ws in INTEREST_WORDS.values() for w in ws if re.search(rf"\b{re.escape(w)}", t)]
    adjectives = re.findall(r"\b(fun|easy|relaxing|romantic|scenic|adventurous|active|quiet|unique|instagrammable|educational)\b", t)
    r.activity_text = " ".join(dict.fromkeys(adjectives + words)) or re.sub(r"\$\s?\d[\d,]*|\b\d+\b", " ", text)[:200]
    legs = parse_legs(text, r)
    if legs:
        r.legs, r.city, r.days = legs, legs[0]["city"], sum(lg["days"] for lg in legs)
        r.need_hotel = True  # moving between cities means nights away
    else:  # "day trip to Sedona from Phoenix": plan where they're going, not where they start
        # one city: "3 days in Tucson" counts as the trip length (the skip above was for multi-city legs)
        m_in = re.search(r"\b(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)[- ]days?\s+(?:in|at)\b", t)
        if m_in and r.days == 1:
            r.days = max(1, min(MAX_DAYS, _num(m_in.group(1)) or 1))
        named = cities_in_order(text)
        dest = [c for pos, c in named if not re.search(r"\b(from|leaving|starting in|based in)\s+(the\s+)?$", t[max(0, pos - 20):pos])]
        if len(named) >= 2 and dest:
            r.city = dest[0]
    return r


def cities_in_order(text: str) -> list[tuple[int, str]]:
    """Every Arizona city named in the text, in the order mentioned: [(position, city)]."""
    t = text.lower()
    cat = get_catalog()
    found, taken = [], []
    for alias, city in CITY_ALIASES.items():
        for m in re.finditer(rf"\b{re.escape(alias)}\b", t):
            found.append((m.start(), m.end(), city))
            taken.append((m.start(), m.end()))
    for c in cat.cities:  # longest names first, so "South Tucson" wins over "Tucson"
        for m in re.finditer(rf"\b{re.escape(c.lower())}\b", t):
            if not any(a < m.end() and m.start() < b for a, b in taken):
                found.append((m.start(), m.end(), c))
                taken.append((m.start(), m.end()))
    out, seen = [], set()
    for start, _, city in sorted(found):
        if city not in seen:
            out.append((start, city))
            seen.add(city)
    return out


def parse_legs(text: str, r: TripRequest | None = None) -> list[dict]:
    """Multi-city trips: "3 days in Phoenix, then 2 in Sedona and a day at the Grand Canyon",
    "Phoenix, Sedona and Flagstaff from March 3 to March 9". Days not given are shared out evenly."""
    t = text.lower()
    found = cities_in_order(text)
    if len(found) < 2 or re.search(r"(?<![\w-])day trip\b|\bfrom \w+ to (the )?\w+ and back\b", t):
        return []
    # "from Phoenix" at the start of a request is where people leave from, not a stop - unless days are given for it
    num = r"(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)"
    legs = []
    for k, (pos, city) in enumerate(found):
        end = found[k + 1][0] if k + 1 < len(found) else len(t)
        start = found[k - 1][0] if k else 0
        after, before = t[pos:end], t[start:pos]
        m = (re.search(rf"^[^,;]*?\b(?:for|-|–)?\s*{num}\s*(?:days?|nights?)\b(?!\s+(?:in|at)\b)", after[:40])
             or re.search(rf"\b{num}\s*(?:more\s+)?(?:days?|nights?)\s+(?:in|at)\s+(?:the\s+)?$", before[-30:]))
        n = _num(m.group(1)) if m else None
        legs.append({"city": city, "days": n})
    total = None
    if r is not None and r.days > 1:  # a date range, "5-day trip" or "weekend" sets the total
        total = r.days
    given = sum(lg["days"] or 0 for lg in legs)
    unknown = [lg for lg in legs if not lg["days"]]
    if unknown:
        spare = max(len(unknown), total - given) if total else len(unknown) * (2 if not given and len(legs) <= 3 else 1)
        each, extra = divmod(spare, len(unknown))
        for i, lg in enumerate(unknown):
            lg["days"] = max(1, each + (1 if i < extra else 0))
    legs = [lg for lg in legs if lg["days"]]
    while sum(lg["days"] for lg in legs) > MAX_DAYS:  # trim the longest stays first
        max(legs, key=lambda lg: lg["days"])["days"] -= 1
    return [lg for lg in legs if lg["days"] > 0]


def day_cities(r) -> dict[int, str]:
    """Day number -> city for the whole trip."""
    legs = r.get("legs") if isinstance(r, dict) else r.legs
    city = r.get("city") if isinstance(r, dict) else r.city
    days = r.get("days", 1) if isinstance(r, dict) else r.days
    out, d = {}, 1
    for lg in legs or []:
        for _ in range(lg["days"]):
            out[d] = lg["city"]
            d += 1
    return {i: out.get(i, city) for i in range(1, days + 1)}


PERM_CLOSED_RX = re.compile(r"permanently closed|closed (for good|forever|permanently|down)|out of business|shut down|"
                            r"no longer (open|exists|there)|went out of business")


def parse_revision_rules(text: str) -> Revision | None:
    t = text.lower()
    if not re.search(r"swap|replace|change|instead|different|cheaper|another|switch|rather|don't like|not a fan|skip|kid[- ]friendly|"
                     r"fully booked|booked up|sold out|is closed|was closed|closed (today|now)|unavailable|no availability|no tables|can'?t get (in|a table)|"
                     r"permanently closed|closed (for good|forever|permanently|down)|out of business|shut down|no longer (open|exists|there)", t):
        return None
    # "swap the museum for a hike", "replace dinner with sushi", "a hike instead of the museum"
    old_part, new_part = t, None
    m = re.search(r"(?:swap|replace|change|switch)\s+(?:out\s+)?(?:the\s+|my\s+|our\s+)?(.+?)\s+(?:for|with|to|into)\s+(?:a\s+|an\s+|some\s+|the\s+)?(.+)", t)
    if m:
        old_part, new_part = m.group(1), m.group(2)
    else:
        m = re.search(r"(?:do\s+|get\s+|have\s+)?(?:a\s+|an\s+|some\s+)?(.+?)\s+instead of\s+(?:the\s+|a\s+)?(.+)", t)
        if m:
            new_part, old_part = m.group(1), m.group(2)
    targets = [w for w in ("breakfast", "lunch", "dinner", "dessert", "nightlife", "bar", "activity", "hike", "museum",
                           "morning", "afternoon", "sunset") if re.search(rf"\b{w}", old_part)]
    targets += [f"stop {n}" for n in re.findall(r"(?:stop|#)\s*(\d)", old_part)]
    ords = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5}
    targets += [f"stop {n}" for w, n in ords.items() if re.search(rf"\b{w}\b", old_part)]
    if re.search(r"most expensive|priciest", t):
        targets.append("most expensive")
    probe = new_part or t
    cuis = next((c for c in CUISINES if re.search(rf"\b{c}\b", probe)), None) or next((v for k, v in CUISINE_ALIASES.items() if re.search(rf"\b{k}\b", probe)), None)
    kid = bool(re.search(r"kid[- ]friendly|for kids|with kids", t))
    if not targets and new_part:  # "swap it for something outdoors" with nothing named -> activities
        targets = ["activity"]
    return Revision(targets=targets or (["all"] if kid else ["dinner" if "food" in t or "eat" in t else "all"]),
                    cheaper=bool(re.search(r"cheaper|less expensive|budget|cheap|save money|lower cost", t)),
                    new_query=new_part or text, cuisine=cuis, make_kid_friendly=kid)


NON_AZ_PLACES = ["las vegas", "vegas", "los angeles", "san diego", "san francisco", "new york", "chicago", "denver",
                 "albuquerque", "santa fe", "salt lake", "seattle", "miami", "dallas", "austin", "houston", "nevada",
                 "california", "utah", "new mexico", "texas", "colorado", "mexico city", "hawaii", "florida", "zion",
                 "yosemite", "moab", "palm springs", "orlando", "boston", "portland"]
TRIP_WORDS = (r"\b(plan|trip|itinerary|day out|visit|things to do|what to do|recommend|suggest|where (should|can|to)|"
              r"date night|weekend|vacation|getaway|explore|tour|eat|food|restaurant|dinner|lunch|breakfast|brunch|"
              r"drinks|bar|go out|activities|activity|hike|hiking|museum|outing|itinerary|spend the day|day in|night in)\b")


def mentioned_az_city(text: str) -> str | None:
    t = text.lower()
    for alias, city in CITY_ALIASES.items():
        if alias in t:
            return city
    for c in get_catalog().cities:
        if len(c) > 3 and re.search(rf"\b{re.escape(c.lower())}\b", t):
            return c
    return None


def has_trip_signal(text: str) -> bool:
    t = text.lower()
    if mentioned_az_city(text) or re.search(TRIP_WORDS, t) or re.search(r"\$\s?\d", t):
        return True
    if any(re.search(rf"\b{d}s?\b", t) for d in DAYS) or any(re.search(rf"\b{re.escape(w)}", t) for ws in INTEREST_WORDS.values() for w in ws):
        return True
    return any(re.search(rf"\b{re.escape(c)}\b", t) for c in list(CUISINES) + list(CUISINE_ALIASES))


def non_az_place(text: str) -> str | None:
    t = text.lower()
    if mentioned_az_city(text):
        return None
    return next((p for p in NON_AZ_PLACES if re.search(rf"\b{re.escape(p)}\b", t)), None)


def chat_reply(text: str, has_plan: bool) -> str:
    t = text.lower().strip()
    place = non_az_place(text)
    if place:
        return (f"I only plan trips inside **Arizona**, because my data is Arizona's 108,000 Google Maps places, so I can't "
                f"plan {place.title()}. Want an Arizona day instead? Try Phoenix, Scottsdale, Sedona, Tucson, Flagstaff "
                "or the Grand Canyon.")
    if re.search(r"\b(thanks|thank you|thx|ty|appreciate|perfect|great|awesome|looks good|sounds good|cool|ok|okay|nice)\b", t):
        return ("You're welcome! Enjoy Arizona. Remember to double-check hours before you go, since my data is a 2021 "
                "snapshot. " + ("I can still swap any stop, find a cheaper dinner, or plan another day." if has_plan else
                                "Tell me a city and what you're into whenever you want a plan."))
    if re.search(r"\b(bye|goodbye|see ya|later)\b", t):
        return "Bye! Have a great trip."
    intro = ("Hi! I'm **Saguaro**, a team of AI agents that plans days out in Arizona: attractions, restaurants, "
             "nightlife, driving times and budget, all from real Google Maps and TripAdvisor data.")
    if re.search(r"\b(hi|hello|hey|hiya|howdy|yo|good (morning|afternoon|evening)|sup)\b", t) and len(t.split()) <= 6:
        lead = intro
    elif re.search(r"help|what can (you|u|ya) do|how does (this|it) work|who (are|r) (you|u)|what are you|who (made|built|created)", t):
        lead = (intro + "\n\nHere's how it works: a Planner agent reads your request, a Weather & Events agent checks the forecast, "
                "sunrise/sunset and local events, Attraction and Dining agents search 21,762 places, a Route agent schedules them, "
                "a Budget agent prices them, a Critic scores and fixes the plan, and a Booking agent prepares links (nothing is booked "
                "without your OK). Built by MIS 556 Group 14.\n\nAfter a plan you can ask for budget/balanced/premium options, compare "
                "places, find things nearby, add or remove stops, book with approval, vote as a group, or save favorites.")
    else:
        lead = "I'm not sure that's a trip request. I can only help with planning outings in Arizona."
    if has_plan:
        return lead + "\n\nYou already have a plan. You can say things like *swap dinner for something cheaper*, *replace stop 2*, *make it kid friendly*, or ask about a stop."
    return (lead + "\n\nTell me a bit about your day and I'll build an itinerary. Helpful details:\n"
            "- **Where:** an Arizona city (Phoenix, Scottsdale, Sedona, Tucson, ...)\n"
            "- **When:** a day, or morning / evening / weekend\n"
            "- **Who:** how many people, any kids, ages if going out\n"
            "- **Budget:** per person\n"
            "- **What you like:** hiking, museums, food type, nightlife, ...\n\n"
            "*For example: \"Saturday in Scottsdale for 4 friends, $125 each, a hike, Mexican dinner, then drinks.\"*")


SMALLTALK = (r"^\W*(hi|hello|hey|hiya|howdy|yo|sup|thanks|thank you|thx|ty|ok|okay|cool|nice|great|perfect|awesome|bye|"
             r"goodbye|good (morning|afternoon|evening))\b|what can (you|u|ya) do|how does (this|it) work|who (are|r) (you|u)|"
             r"what are you|who (made|built|created) (you|this)|\bhelp\b")


def is_question(text: str) -> bool:
    t = text.lower().strip()
    # a question mark, or a sentence that starts like a question ("tell me about the dinner spot")
    asks = "?" in t or re.match(r"(tell me (about|more)|what('s| is| are| about)|how (is|are|much|long|far|good)|"
                                r"is (the|it|there|this|that)|are (there|they|the)|does |do they|can (we|i|kids|you)|"
                                r"which |where (is|are|can)|when (does|do|is))\b", t)
    return bool(asks and len(re.findall(r"[a-z]{2,}", t)) >= 2 and not re.search(SMALLTALK, t)
                and not re.search(r"\b(plan|itinerary|schedule)\b", t))


# ---------------------------------------------------------------- request modifiers ("make it Sunday")
MODIFIER_RX = re.compile(
    r"\b(make it|change (it |the day |the date |the dates )?to|move (it |the trip )?to|switch (it )?to|instead|actually|on|for|we('re| are) now|"
    r"now|update|budget is|our budget|start(ing)? at|until|till|end by|add|include|we want|let'?s)\b")


def request_changes(text: str, current: dict) -> dict:
    """Fields a short follow-up changes in the current request ("actually make it Sunday", "for 6 people",
    "$50 each instead", "start at 10am", "add nightlife"). Empty dict = not a modifier."""
    t = text.lower()
    new = parse_request_rules(text).model_dump()
    ch: dict = {}
    if any(re.search(rf"\b{d}s?\b", t) for d in DAYS) or re.search(r"\b(tomorrow|today|tonight|weekend)\b", t):
        ch["weekday"] = new["weekday"]
        if "weekend" in t:
            ch["days"] = 2
    if re.search(r"\b\d[- ]days?\b|\b(one|two|three|four|five|six|seven)[- ]days?\b", t):
        ch["days"] = new["days"]
    if any(re.search(rf"\b{m}\b", t) for m in MONTHS):
        ch["month"] = new["month"]
    if new.get("trip_date"):
        ch.update(trip_date=new["trip_date"], weekday=new["weekday"], month=new["month"], end_date=new.get("end_date"))
        if new.get("end_date"):
            ch["days"] = new["days"]
        elif "days" not in ch and current.get("end_date"):
            ch["days"] = 1  # a single new date replaces an earlier range
    elif "weekday" in ch or "month" in ch:
        ch["trip_date"] = None  # a new day/month replaces an earlier exact date
    if re.search(r"\$\s?\d|\d+\s?(dollars|bucks)", t):
        ch["budget_per_person"] = parse_request_rules(text.replace("instead", "") + (" each" if not re.search(r"total|for (all|us|both)", t) else "")).budget_per_person
    if new["party_size"] != 2 or re.search(r"\b(two|2) (of us|people|adults)\b|\bcouple\b", t):
        if re.search(r"\b(people|friends|adults|guys|girls|of us|family of|party of|group of|kids|children|toddlers|for \d+)\b", t):
            ch["party_size"] = new["party_size"]
    if re.search(r"(?:from|start(?:ing)? at|until|till|end by|\d\s*(am|pm))", t) and (new["start_time"], new["end_time"]) != (540, 1320):
        ch["start_time"], ch["end_time"] = new["start_time"], new["end_time"]
    if re.search(r"half[- ]day|morning only|evening only|afternoon only", t):
        ch["start_time"], ch["end_time"] = new["start_time"], new["end_time"]
    for k in ("cuisines", "interests", "dietary"):
        if new[k] and new[k] != current.get(k):
            ch[k] = sorted(set(current.get(k) or []) | set(new[k])) if re.search(r"\b(add|also|and|include|too)\b", t) else new[k]
    if new["nightlife"] and not current.get("nightlife"):
        ch["nightlife"] = True
    for k in ("wheelchair", "halal", "free_only", "indoor_only", "hidden_gems"):
        if new[k] and not current.get(k):
            ch[k] = True
    if new["tier"] != "balanced" and new["tier"] != current.get("tier") and not re.search(r"cheaper|less expensive", t):
        ch["tier"] = new["tier"]
    if new["min_rating"]:
        ch["min_rating"] = new["min_rating"]
    if new["atmosphere"]:
        ch["atmosphere"] = new["atmosphere"]
    return ch


NO_PLAN_YET = ("There's no plan to change yet. Tell me where and what kind of day you want first, for example "
               "*\"Saturday in Tempe for 3 friends, $80 each, a museum and pizza\"*, and then I can swap or adjust any stop.")


def intake_rules(text: str, has_plan: bool, current_city: str | None = None, current_request: dict | None = None) -> Intake:
    t = text.lower().strip()
    refers_to_plan = re.search(r"\bstop \d|\bthe (first|second|third|last) (option|stop|one|alternative)|\b(this|the|my|our) (plan|itinerary)\b|"
                               r"\buse the\b|\boption \d|\balternative\b", t)
    if not has_plan and (parse_revision_rules(text) or refers_to_plan) and not mentioned_az_city(text) \
            and not re.search(r"\bplan (a|an|my|our|me)\b|\$\s?\d", t):
        return Intake(intent="chat", reply=NO_PLAN_YET)
    edits = request_changes(text, current_request or {}) if has_plan else {}
    plan_edit = has_plan and (parse_revision_rules(text) or edits)
    short_smalltalk = re.search(SMALLTALK, t) and len(t.split()) <= 6 and not plan_edit
    if non_az_place(text) or short_smalltalk or (not has_trip_signal(text) and not is_question(text) and not plan_edit):
        return Intake(intent="chat", reply=chat_reply(text, has_plan))
    if is_question(text) and not (has_plan and parse_revision_rules(text)):
        return Intake(intent="question", question=text)
    if has_plan:
        city = mentioned_az_city(text)
        cur = current_request or {}
        if (city and city != cur.get("city") and city not in [lg["city"] for lg in cur.get("legs") or []]
                and re.search(r"\b(add|also (visit|go to|see)|extend|then (go to|head to|visit)|plus|and then)\b", t)):
            # "add 2 days in Flagstaff": extend the current trip with another city instead of starting over
            m = re.search(r"\b(\d{1,2}|one|two|three|four|five|six|seven)\s*(?:more\s+)?(?:days?|nights?)\b", t)
            legs = (cur.get("legs") or [{"city": cur.get("city"), "days": cur.get("days", 1)}]) + [{"city": city, "days": _num(m.group(1)) if m else 1}]
            while sum(lg["days"] for lg in legs) > MAX_DAYS:
                max(legs, key=lambda lg: lg["days"])["days"] -= 1
            return Intake(intent="new_plan", changed=["legs", "days"],
                          request=TripRequest(**{**cur, "legs": legs, "days": sum(lg["days"] for lg in legs),
                                                 "need_hotel": True, "end_date": None}))
        new_trip = (city and city != current_city) or re.search(r"\b(new|another|different) (plan|trip|day|itinerary)\b|\bplan (a|my|our)\b", text.lower())
        if not new_trip:
            rev = parse_revision_rules(text)
            swap = rev and re.search(r"swap|replace|instead of|switch|cheaper|kid[- ]friendly|booked|closed|unavailable|sold out|don't like|not a fan|"
                                     r"out of business|shut down|no longer", t)
            if swap:
                return Intake(intent="revise", revision=rev)
            if "?" in text:
                return Intake(intent="question", question=text)
            ch = request_changes(text, current_request or {})
            if ch:
                return Intake(intent="new_plan", request=TripRequest(**{**(current_request or {}), **ch}), changed=sorted(ch))
            if rev:
                return Intake(intent="revise", revision=rev)
    return Intake(intent="new_plan", request=parse_request_rules(text))


# ----------------------------------------------------------------------------------
# Skeleton builder
# ----------------------------------------------------------------------------------
HOT_MONTHS = {5, 6, 7, 8, 9}


INDOOR_THEMES = ["history_culture", "arts", "science_sky", "family_fun", "shopping", "animals"]


def build_slots(r: TripRequest, cond: dict | None = None, by_day: dict | None = None) -> tuple[list[dict], list[str]]:
    """Day skeleton. `cond` comes from the Weather agent (heat, rain, sunrise, sunset); on multi-city trips
    `by_day` holds each day's own city weather, so every day is planned for the weather where you'll be."""
    notes: list[str] = []
    if cond is None:
        month = r.month or datetime.now(ZoneInfo(TIMEZONE)).month
        cond = {"hot": month in HOT_MONTHS, "rainy": False, "sunrise": 360, "sunset": 1110}
    hot, rainy = cond["hot"], cond["rainy"]
    sunrise, sunset = cond["sunrise"], cond["sunset"]
    adults_only_ok = r.nightlife and not r.has_kids and (r.min_age is None or r.min_age >= 21)
    if r.nightlife and not adults_only_ok:
        notes.append("Nightlife skipped: someone in the group is under 21.")
    interests = list(r.interests) or (["outdoors", "history_culture"] if not r.has_kids else ["animals", "family_fun"])
    if r.outdoor_pref and "outdoors" not in interests:
        interests.insert(0, "outdoors")
    base_interests = list(interests)
    if by_day:  # multi-city: rain is handled day by day below
        rainy = False
    if r.indoor_only or rainy:
        interests = [i for i in interests if i not in ("outdoors", "gardens")] or ["history_culture", "family_fun"]
        if rainy and not r.indoor_only:
            notes.append("Rain in the forecast, so I picked indoor activities instead of outdoor ones.")
    occasion = f" {r.occasion}" if r.occasion else ""
    vibe = "family friendly kids" if r.has_kids else ("romantic" if r.occasion == "date night" else
                                                      "lively fun for friends" if r.party_size >= 3 else "")
    slots: list[dict] = []
    S, E = r.start_time, r.end_time
    if adults_only_ok:
        E = max(E, 1380)  # going out runs to ~11PM
    attr_extra = {"max_cost": 0.0} if r.free_only else {}
    if not r.interests:
        attr_extra["soft_theme"] = True
    if r.indoor_only or rainy:
        attr_extra["indoor_only"] = True

    def add(day, kind, label, start, dur, query, early_ok=False, **kw):
        if (start < S and not early_ok) or start + dur > E + 30:
            return
        if kind == "attraction":
            kw = {**attr_extra, **kw}
        slots.append(dict(id=f"d{day}-{len(slots)}", day=day, kind=kind, label=label, target=start, duration=dur,
                          query=query, place=None, alternatives=[], reason="", locked=False, **kw))

    for day in range(1, r.days + 1):
        dc = (by_day or {}).get(day)
        if dc:  # this day's city weather
            hot, rainy, sunrise, sunset = dc["hot"], dc["rainy"], dc["sunrise"], dc["sunset"]
            dry = [i for i in base_interests if i not in ("outdoors", "gardens")] or ["history_culture", "family_fun"]
            interests = dry if (r.indoor_only or rainy) else list(base_interests)
            if r.indoor_only or rainy:
                attr_extra["indoor_only"] = True
            else:
                attr_extra.pop("indoor_only", None)
            if rainy and not r.indoor_only:
                notes.append(f"Day {day} ({dc.get('city', '')}): rain in the forecast, so that day's activities are indoors.")
        a_i = (day - 1) * 2
        def interest(i):  # noqa: E306
            return interests[i % len(interests)]
        want = lambda m: m in r.meals  # noqa: E731
        t = S
        bfast = want("breakfast") or (r.days > 1 and S <= 540 and not r.meals and not r.needs_specified)
        first = interest(a_i)
        # Heat: an outdoor morning starts ~15 min after sunrise, and breakfast moves to after it (brunch),
        # so the hike isn't pushed into the late-morning heat.
        early = hot and first in ("outdoors", "gardens") and S <= 600 and r.want_activities and S < 960
        if bfast and not early:
            add(day, "restaurant", "Breakfast", t, 60, f"breakfast brunch cafe {vibe}", meal="breakfast")
            t += 75
        a1_start = max(330, (sunrise + 15) // 15 * 15) if early else max(t, S)
        evening_plan = S >= 960
        a1_dur = 120 if hot and first == "outdoors" else 150
        if r.want_activities and not evening_plan and (E - a1_start >= 180 or (E - S) < 300):
            label = "Sunrise activity" if early else ("Morning activity" if a1_start < 720 else "Activity")
            add(day, "attraction", label, a1_start, a1_dur,
                f"{r.activity_text} {first.replace('_', ' ')}{occasion} {vibe}", early_ok=early, theme=first,
                avoid_strenuous=r.has_kids, primary=bool(r.interests) and day == 1)
        if bfast and early and a1_start + a1_dur + 20 + 60 <= 735:
            add(day, "restaurant", "Breakfast", (a1_start + a1_dur + 20 + 14) // 15 * 15, 60, f"breakfast brunch cafe {vibe}",
                meal="breakfast", early_ok=True)
        if S <= 750 <= E - 60 and (want("lunch") or (E - S >= 360 and not r.needs_specified)):
            add(day, "restaurant", "Lunch", 750, 75, f"lunch {vibe} {' '.join(r.cuisines[1:2])}", meal="lunch")
        second = interest(a_i + 1)
        if hot and second in ("outdoors", "gardens"):
            second = next((i for i in interests if i in INDOOR_THEMES), "family_fun" if r.has_kids else "history_culture")
        dinner_at = 1110
        if not r.want_activities:
            pass
        elif S <= 870 and E >= 1020:
            one_interest = len(set(interests)) == 1
            add(day, "attraction", "Afternoon activity", 870, 120,
                f"{r.activity_text if not one_interest else ''} {second.replace('_', ' ')}{occasion} {vibe}", theme=second,
                indoor_only=hot or attr_extra.get("indoor_only", False), avoid_strenuous=True,
                **({"soft_theme": True} if one_interest else {}))
        elif evening_plan and E - S >= 300 and (r.interests or r.activity_text.strip()):
            # Sunset planning: arrive ~60 min before sunset
            sun_start = max(S, (sunset - 60) // 15 * 15) if not rainy else S
            add(day, "attraction", "Sunset activity" if not rainy else "Evening activity", sun_start, 75,
                f"{r.activity_text} scenic sunset viewpoint {first}{occasion}", theme=first, avoid_strenuous=True)
            dinner_at = max(1110, (sun_start + 90 + 14) // 15 * 15)
        if E >= 1170 and (want("dinner") or not r.needs_specified):
            dinner_cuisine = r.cuisines[0] if r.cuisines else None
            add(day, "restaurant", "Dinner", dinner_at, 90,
                f"dinner {dinner_cuisine or ''} {'romantic upscale' if r.occasion else ''} {r.atmosphere or ''} {vibe}",
                meal="dinner", cuisine=dinner_cuisine)
        if E >= 1290:
            if adults_only_ok:
                add(day, "nightlife", "Night out", 1245, 120, f"bar nightlife cocktails live music {occasion} {vibe}")
            elif ((r.occasion or r.has_kids) and not r.needs_specified) or r.want_dessert:
                add(day, "restaurant", "Dessert", 1230, 45, "dessert ice cream", meal="dessert", cuisine="dessert")
        elif r.want_dessert and E >= 1200:
            add(day, "restaurant", "Dessert", min(E - 45, 1230), 45, "dessert ice cream", meal="dessert", cuisine="dessert")
        # Where to sleep: a hotel at the end of each night of the trip
        nights = max(1, r.days - 1) if r.need_hotel else 0
        if day <= nights:
            stay = ("family friendly hotel with pool" if r.has_kids else "romantic boutique hotel resort" if r.occasion
                    else "hotel")
            slots.append(dict(id=f"d{day}-hotel", day=day, kind="lodging", label="Hotel" if nights == 1 else f"Hotel, night {day}",
                              target=max(E - 30, 1260), duration=30, query=f"{stay} {r.atmosphere or ''} near {day_cities(r)[day]}",
                              place=None, alternatives=[], reason="", locked=False))
    if r.legs:  # multi-city: every stop is searched around that day's city
        where = day_cities(r)
        for s in slots:
            s["city"] = where[s["day"]]
    return slots, notes


# ----------------------------------------------------------------------------------
# Graph node
# ----------------------------------------------------------------------------------


PLAN_BOUND_CMDS = {"feedback", "transport", "add_stop", "nearby", "realloc", "budget", "favorite",
                   "alternatives", "use_alt", "book", "share", "vote", "remove", "distance"}


def route_command(msg: str, state: PlannerState) -> dict | None:
    """Concierge-tools commands are matched first, with rules, so they work the same with or without GPT-4o."""
    from .tools import detect_command
    cmd = detect_command(msg, state)
    if not cmd:
        return None
    t = msg.lower()
    has_plan = bool([s for s in state.get("slots") or [] if s.get("place")])
    current = (state.get("request") or {}).get("city")
    city = mentioned_az_city(msg)
    new_trip = bool(re.search(r"\bplan (a|my|our|me|an?)\b|\bnew plan\b|itinerary for|\bspend (a|the) day\b", t)) or (city and city != current and has_plan)
    # A full trip request ("Spring training game day in Mesa for 5 friends, $90 each") is a plan, not an events/weather question
    strong_trip = bool(city) and bool(re.search(r"\$\s?\d|\b(for|with) \w+ (people|friends|adults|kids)|\bfor \d+\b|\b(day|weekend|trip|night|morning|afternoon|evening)\b|"
                                                + "|".join(DAYS), t)) and not re.search(r"^(what|when|is|are|how|which)\b.*\?$", t)
    if cmd["cmd"] in PLAN_BOUND_CMDS and (new_trip or (not has_plan and (city or strong_trip))):
        return None  # it's really a new trip request that happens to contain a command word
    if cmd["cmd"] in ("events", "weather", "feedback") and (new_trip or strong_trip):
        return None
    if cmd["cmd"] == "options" and (new_trip or not has_plan):
        return {**cmd, "new_request": True}
    return cmd


def _interview_turn(state: PlannerState, msg: str) -> tuple[dict | None, str]:
    """Guided questions. Returns (reply-to-send or None, message-to-plan-with)."""
    from .. import interview as IV
    iv = state.get("interview")
    has_plan = bool(state.get("slots"))
    trace = lambda did: [{"agent": "Planner", "via": "rules", "did": did}]  # noqa: E731
    reply = lambda text, iv_, did: {"intent": "chat", "answer": text, "interview": iv_, "issues": state.get("issues", []),  # noqa: E731
                                    "pending": None, "command": None, "trace": trace(did),
                                    "history": ((state.get("history") or []) + [{"role": "user", "content": msg},
                                                                                {"role": "assistant", "content": text}])[-12:]}
    if iv and iv.get("active"):
        if re.match(r"^\s*(cancel|stop|never ?mind|forget it|skip the questions|just plan( it)?)\b", msg.lower()):
            if re.match(r"^\s*just plan", msg.lower()):
                return None, IV.compose({**iv, "active": False})
            return reply("No problem - I stopped the questions. Tell me what you'd like, or tap a suggestion to start again.",
                         None, "interview cancelled"), msg
        iv2, q, done = IV.answer(iv, msg)
        if not done:
            return reply(q["text"], {**iv2, "q": q}, f"interview question {q['step']} of {q['total']}"), msg
        return None, IV.compose(iv2)  # all answers in: plan with them
    scen = IV.starter(msg)
    if scen and not (has_plan and scen == "custom"):
        iv, q = IV.start(scen, msg)
        return reply(q["text"], {**iv, "q": q}, f"started guided questions ({IV.SCENARIOS[scen]['title']})"), msg
    # a typed trip request with no city: ask the missing details (tap-to-answer) instead of guessing Phoenix
    cmd = route_command(msg, state) if not has_plan else None
    if (not has_plan and has_trip_signal(msg) and not is_question(msg) and not non_az_place(msg)
            and not parse_revision_rules(msg)
            and (not cmd or cmd["cmd"] in PLAN_BOUND_CMDS)):  # "book a family trip" is a trip, not a reservation
        started = IV.start_from_request(msg)
        if started:
            iv, q = started
            return reply(q["text"], {**iv, "q": q}, "asked for the missing trip details"), msg
    return None, msg


def planner_node(state: PlannerState) -> dict:
    iv_reply, msg = _interview_turn(state, state["user_message"])
    if iv_reply:
        return iv_reply
    if msg == state["user_message"]:
        return _planner_core(state)
    # the interview finished: plan from the composed request, as a brand-new trip
    out = _planner_core({**state, "user_message": msg, "interview": None, "slots": [], "request": None})
    out["interview"] = None
    out.setdefault("trace", [])
    out["trace"] = [{"agent": "Planner", "via": "rules", "did": "guided questions done - planning with your answers"}] + out["trace"]
    return out


def _planner_core(state: PlannerState) -> dict:
    msg = state["user_message"]
    has_plan = bool(state.get("slots"))
    cmd = route_command(msg, state)
    if cmd:
        out = {"intent": "tools", "command": cmd, "issues": state.get("issues", []), "replans": 0,
               "trace": [{"agent": "Planner", "via": "rules", "did": f"intent = tools ({cmd['cmd']})"}]}
        if cmd.get("new_request"):
            if not has_trip_signal(msg) or non_az_place(msg):
                return {**out, "intent": "chat", "answer": chat_reply(msg, has_plan) if non_az_place(msg) else
                        "Happy to make budget, balanced and premium versions - tell me the city, day, group and what you like first."}
            r = parse_request_rules(msg)
            r.city = get_catalog().canonical_city(r.city) or "Phoenix"
            notes = []
            if r.weekday is None:
                r.weekday = 5
                notes.append("No day given, so the plan assumes a Saturday for opening hours.")
            out.update(request=r.model_dump(), slots=[], needs_skeleton=True, notes=notes)
        return out
    history = "\n".join(f"{h['role']}: {h['content'][:400]}" for h in (state.get("history") or [])[-6:])
    plan_txt = "\n".join(f"- {s['label']}: {s['place']['name'] if s.get('place') else '?'}" for s in state.get("slots") or [])
    user = f"Conversation so far:\n{history or '(none)'}\n\nCurrent itinerary:\n{plan_txt or '(none)'}\n\nLatest message: {msg}"
    current_city = (state.get("request") or {}).get("city")
    intake, how = structured(prompts.PLANNER, user, Intake, lambda: intake_rules(msg, has_plan, current_city, state.get("request")))
    # Guardrails that hold whatever the LLM said: never invent a plan from small talk, never
    # plan outside Arizona, never "revise" a plan that does not exist.
    if non_az_place(msg) or (intake.intent == "new_plan" and not has_trip_signal(msg) and not intake.changed
                             and not (has_plan and request_changes(msg, state.get("request") or {}))):
        intake = Intake(intent="chat", reply=chat_reply(msg, has_plan))
    if intake.intent == "revise" and (not has_plan or intake.revision is None):
        intake = intake_rules(msg, False)
    if intake.intent == "new_plan" and intake.request is None:
        intake.request = parse_request_rules(msg)

    trace = [{"agent": "Planner", "via": how, "did": f"intent = {intake.intent}"}]
    out: dict = {"intent": intake.intent, "trace": trace, "issues": [], "replans": 0, "pending": None, "command": None, "repaired": []}

    if intake.intent == "chat":
        reply = intake.reply or chat_reply(msg, has_plan)
        hist = (state.get("history") or []) + [{"role": "user", "content": msg}, {"role": "assistant", "content": reply}]
        out.update(answer=reply, history=hist[-12:])
        return out

    if intake.intent == "question":
        out["question"] = intake.question or msg
        return out

    if intake.intent == "revise":
        rev = intake.revision
        req = dict(state["request"])
        if rev.make_kid_friendly:
            req["has_kids"], req["nightlife"] = True, False
        slots = [dict(s) for s in state["slots"]]
        keys = [t.lower() for t in rev.targets]
        changed = []
        priciest = max((s for s in slots if s.get("place")), key=lambda s: s.get("cost_pp") or 0, default=None)
        filled_ = [s for s in slots if s.get("place")]
        def cat_match(word):  # noqa: E306  attractions whose Google category matches "museum" / "hike"
            stem = {"hike": "hik", "museum": "museum"}[word]
            return {id(s) for s in filled_ if s["kind"] == "attraction" and stem in (s["place"].get("categories") or "").lower()}
        named = set()
        for w in ("hike", "museum"):
            if w in keys:
                named |= cat_match(w) or {id(s) for s in filled_ if s["kind"] == "attraction"}
        new_theme = next((k for k, ws in INTEREST_WORDS.items() if rev.new_query and any(re.search(rf"\b{re.escape(x)}", rev.new_query.lower()) for x in ws)), None)
        stop_no = {id(s): n for n, s in enumerate(filled_, 1)}
        for i, s in enumerate(slots, 1):
            lbl = s["label"].lower()
            hit = ("all" in keys or f"stop {stop_no.get(id(s), -1)}" in keys or ("most expensive" in keys and s is priciest)
                   or any(k in lbl for k in keys if k not in ("hike", "museum", "bar"))
                   or id(s) in named
                   or ("activity" in keys and s["kind"] == "attraction")
                   or (any(k in ("bar", "nightlife") for k in keys) and s["kind"] == "nightlife"))
            if rev.make_kid_friendly and s["kind"] == "nightlife":
                s.update(kind="restaurant", label="Dessert", meal="dessert", cuisine="dessert", query="dessert ice cream")
                hit = True
            if hit and rev.cheaper and s.get("place") and not (s["place"].get("cost_pp") or 0) and "all" in keys:
                hit = False  # already free: nothing to save here
            if hit:
                old = s.get("place") or {}
                s["exclude"] = list(set(s.get("exclude", [])) | ({old["id"]} if old.get("id") else set()))
                if old.get("id") and PERM_CLOSED_RX.search(state.get("user_message", "").lower()):
                    from ..closed_reports import report
                    report(old)  # closed for good: never recommend it again, for anyone
                    changed.append(f"{old.get('name')} is now marked permanently closed")
                if rev.cheaper and old.get("cost_pp"):
                    # strictly cheaper; if nothing is, the specialist keeps the same price tier
                    s["max_cost"] = float(old["cost_pp"]) - 0.5
                    s["cost_ceiling_fallback"] = float(old["cost_pp"])
                if rev.cuisine:
                    s["cuisine"] = rev.cuisine
                if rev.new_query:
                    s["query"] = f"{rev.new_query} {s['query']}"
                    if s["kind"] == "attraction" and new_theme:  # "swap the museum for a hike" -> outdoors
                        s.update(theme=new_theme, soft_theme=False, indoor_only=False if new_theme in ("outdoors", "gardens") else s.get("indoor_only", False))
                        s["query"] = f"{rev.new_query} {new_theme.replace('_', ' ')}"
                s.update(place=None, locked=False, reason="", alternatives=[])
                changed.append(s["label"])
            else:
                s["locked"] = True
        trace.append({"agent": "Planner", "via": how, "did": f"revising: {', '.join(changed) or 'nothing matched'}"})
        out.update(request=req, slots=slots, revision=rev.model_dump())
        return out

    req = intake.request
    cat = get_catalog()
    req.city = cat.canonical_city(req.city) or "Phoenix"
    assumed = []
    if intake.changed:  # a follow-up edited the existing trip: keep everything else
        nice = {"weekday": "day", "budget_per_person": "budget", "party_size": "group size", "start_time": "times",
                "end_time": None, "cuisines": "food", "interests": "interests", "days": "number of days"}
        what = [nice.get(c, c.replace("_", " ")) for c in intake.changed if nice.get(c, c) is not None]
        assumed.append(f"Updated your plan: {', '.join(dict.fromkeys(what))}.")
        trace.append({"agent": "Planner", "via": how, "did": f"edit trip: {', '.join(intake.changed)}"})
    elif req.zip_code and not mentioned_az_city(msg) and cat.city_for_zip(req.zip_code):
        req.city = cat.city_for_zip(req.zip_code)
    elif how == "rules" and not mentioned_az_city(msg):
        assumed.append(f"No city given, so I planned around {req.city}. Tell me another Arizona city to switch.")
    if req.weekday is None:
        req.weekday = 5  # Saturday: the most common trip day, so hours checks still run
        assumed.append("No day given, so the plan assumes a Saturday for opening hours.")
    if req.trip_date and req.end_date:
        from datetime import date as _d, timedelta as _td
        start, end = _d.fromisoformat(req.trip_date), _d.fromisoformat(req.end_date)
        if (end - start).days + 1 > req.days:
            last = start + _td(days=req.days - 1)
            assumed.append(f"Plans cover up to {MAX_DAYS} days, so this one covers {start:%b} {start.day} - {last:%b} {last.day}. "
                           f"Ask for the rest of your dates as a second trip.")
            req.end_date = last.isoformat()
    if req.has_kids or (req.min_age is not None and req.min_age < 21):
        req.nightlife = False
    if req.free_only:
        req.tier = "budget"
    if req.zip_code and not cat.locate_zip(req.zip_code):
        assumed.append(f"ZIP {req.zip_code} isn't in the Arizona data, so I used the {req.city} centre.")
        req.zip_code = None
    if not req.activity_text:
        req.activity_text = msg[:300]
    req = apply_profile(req, state.get("profile") or {}, assumed)
    trace.append({"agent": "Planner", "via": "rules", "did": f"{req.city}, {req.days} day(s), party of {req.party_size}"
                  + (f", ${req.budget_per_person:.0f}/person" if req.budget_per_person else "")
                  + (f", by {req.transport}" if req.transport != "car" else "")})
    out.update(request=req.model_dump(), slots=[], notes=assumed, needs_skeleton=True, options=None)
    return out


def apply_profile(req: TripRequest, profile: dict, notes: list[str]) -> TripRequest:
    """AI memory: fill gaps from saved preferences (only when the traveler turned memory on)."""
    if not profile.get("memory_on"):
        return req
    used = []
    if not req.cuisines and profile.get("cuisines"):
        req.cuisines = sorted(profile["cuisines"], key=profile["cuisines"].get, reverse=True)[:1]
        used.append(f"cuisine: {req.cuisines[0]}")
    if not req.interests and profile.get("interests"):
        req.interests = sorted(profile["interests"], key=profile["interests"].get, reverse=True)[:2]
        used.append(f"interests: {', '.join(req.interests)}")
    if not req.dietary and profile.get("dietary"):
        req.dietary = list(profile["dietary"])
        used.append(f"diet: {', '.join(req.dietary)}")
    if not req.wheelchair and profile.get("wheelchair"):
        req.wheelchair = True
        used.append("wheelchair access")
    if used:
        notes.append("Used your saved preferences (" + "; ".join(used) + "). Say *forget my preferences* to clear them.")
    return req
