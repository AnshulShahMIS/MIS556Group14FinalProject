"""Guided interview: when a traveler starts from a scenario ("Family day", "Date night", ...) or asks for help,
the Planner asks a few short questions before planning. Each question has quick-reply options, but any typed
answer is understood too. At the end the answers become one request for the planning agents.

State (kept in the conversation): {"active", "scenario", "step", "answers", "asked"}.
"""
from __future__ import annotations

import re

SCENARIOS = {
    "friends": {"title": "Friends' day out", "intro": "Fun day with friends - let's get the details right.",
                "defaults": {"needs": ["Activities", "Lunch", "Dinner", "Night out"], "interests": ["Fun & games"]}},
    "family": {"title": "Family day", "intro": "A family day - I'll keep everything kid-friendly.",
               "defaults": {"needs": ["Activities", "Lunch", "Dinner"], "interests": ["Animals & zoos"], "extras": ["Kid-friendly everything"]}},
    "date": {"title": "Date night", "intro": "A date night - let's make it special.",
             "defaults": {"needs": ["Activities", "Dinner", "Dessert"], "interests": ["Scenic views"], "who": "2 adults",
                          "length": "Evening"}},
    "game": {"title": "Game day", "intro": "Game day - let's plan around the action.",
             "defaults": {"needs": ["Activities", "Lunch", "Dinner", "Night out"], "interests": ["Sports"]}},
    "custom": {"title": "Your trip", "intro": "Happy to help - a few quick questions and I'll build your plan.",
               "defaults": {"needs": ["Activities", "Lunch", "Dinner"]}},
}
STARTERS = {"friends' day out": "friends", "friends day out": "friends", "family day": "family", "date night": "date",
            "game day": "game"}
HELP_RX = re.compile(r"^(help me plan|plan (a|my) trip|ask me (some )?questions|i need (help|ideas)|"
                     r"i don'?t know where to start|guide me)\b", re.I)

CITY_OPTS = ["Phoenix", "Scottsdale", "Tempe", "Tucson", "Sedona", "Flagstaff", "Mesa", "Grand Canyon"]
WHEN_OPTS = ["This Saturday", "This Sunday", "Tomorrow", "Next weekend"]
WHO_OPTS = {
    "family": ["2 adults, 1 kid", "2 adults, 2 kids", "2 adults, 3 kids", "1 adult, 2 kids", "Toddlers", "School-age kids", "Teens"],
    "friends": ["3 friends, all 21+", "4 friends, all 21+", "6 friends, all 21+", "Some of us are under 21"],
    "date": ["2 adults"],
    "game": ["2 people", "4 people", "6 people", "Everyone is 21+"],
    "custom": ["Just me", "2 adults", "4 adults", "2 adults, 2 kids", "Everyone is 21+"],
}
NEED_OPTS = ["Activities", "Breakfast", "Lunch", "Dinner", "Dessert", "Hotel stay", "Night out"]
INTEREST_OPTS = ["Hiking & outdoors", "Museums & history", "Animals & zoos", "Fun & games", "Shopping", "Sports",
                 "Scenic views", "Art", "Tours & adventure", "Breweries & wineries"]
FOOD_OPTS = ["Any food", "Mexican", "Italian", "American", "BBQ", "Pizza", "Sushi", "Steakhouse", "Vegetarian-friendly"]
BUDGET_OPTS = ["Under $50 each", "$50-100 each", "$100-200 each", "No limit"]
EXTRA_OPTS = ["Kid-friendly everything", "Wheelchair accessible", "Vegetarian", "Hidden gems", "Indoor (beat the heat)",
              "Highly rated only", "Nothing else"]

QUESTIONS = [
    ("where", "Where in Arizona? Pick one city, or several in the order you'll visit them.", CITY_OPTS, True),
    ("when", "When are you going?", WHEN_OPTS, False),
    ("who", None, None, True),  # text/options depend on the scenario
    ("needs", "What should I plan for you? Pick all that apply.", NEED_OPTS, True),
    ("interests", "What are you into?", INTEREST_OPTS, True),
    ("food_budget", "Any food you love, and roughly what budget per person?", FOOD_OPTS + BUDGET_OPTS, True),
    ("extras", "Anything else I should know?", EXTRA_OPTS, True),
    ("confirm", None, ["Plan it", "Start over"], False),
]


def starter(message: str) -> str | None:
    t = (message or "").strip().lower().rstrip(".!")
    if t in STARTERS:
        return STARTERS[t]
    if HELP_RX.match(t) and len(t.split()) <= 8:
        return "custom"
    return None


def start(scenario: str, message: str = "") -> tuple[dict, dict]:
    """Start the questions. A city in the message ("help me plan a trip to Sedona", as sent by the Popular places
    cards) is kept, so "Where?" isn't asked again."""
    from .agents.planner import cities_in_order
    sc = SCENARIOS[scenario]
    iv = {"active": True, "scenario": scenario, "step": 0, "answers": dict(sc["defaults"]), "skip": []}
    if "who" in sc["defaults"]:
        iv["skip"].append("who")
    cities = [c for _, c in cities_in_order(message)] if message else []
    intro = sc["intro"]
    if cities:
        iv["answers"]["where"] = " then ".join(cities)
        iv["skip"].append("where")
        if scenario == "custom":
            intro = f"Let's plan your {' and '.join(c.replace(' Village', '') for c in cities)} trip - a few quick questions and I'll build your plan."
    return iv, question(iv, intro=intro)


def start_from_request(message: str) -> tuple[dict, dict] | None:
    """A typed trip request that leaves out WHERE: ask only what's missing (where, plus who / when / interests if
    they're missing too), keep everything the traveler already said, and plan from all of it. None = no gaps."""
    from .agents.planner import cities_in_order, parse_request_rules
    t = (message or "").lower()
    if cities_in_order(message) or "grand canyon" in t or re.search(r"\b8[56]\d{3}\b", t):  # a city or an AZ ZIP
        return None
    r = parse_request_rules(message)
    known_who = bool(re.search(r"\b\d+\s*(?:people|persons|adults|kids|children|of us|friends|travelers|guests)\b|"
                               r"family of|party of|group of|couple|solo|just me|my (?:wife|husband|partner|parents)|"
                               r"\b(?:two|three|four|five|six|seven|eight|nine|ten|twelve)\s+(?:people|adults|kids|friends)", t))
    known_when = bool(r.trip_date or r.weekday is not None or r.month or re.search(r"\bweek|weekend|tomorrow|today|tonight", t))
    scenario = "family" if r.has_kids or "family" in t else "friends" if "friends" in t else "custom"
    skip = ["needs", "food_budget", "extras"] + (["who"] if known_who else []) + (["when"] if known_when else []) \
        + (["interests"] if r.interests else [])
    iv = {"active": True, "scenario": scenario, "step": 0, "skip": skip,
          "answers": {"base_note": message.strip(), "days": r.days}}
    intro = ("Sounds like a great trip! A couple of quick questions so I plan the right places."
             if r.days < 2 else f"Sounds like a great {r.days}-day trip! A couple of quick questions so I plan the right places.")
    return iv, question(iv, intro=intro)


def _q(iv: dict) -> tuple:
    while iv["step"] < len(QUESTIONS) and QUESTIONS[iv["step"]][0] in iv.get("skip", []):
        iv["step"] += 1
    return QUESTIONS[iv["step"]]


def question(iv: dict, intro: str | None = None, note: str | None = None) -> dict:
    key, text, opts, multi = _q(iv)
    sc = iv["scenario"]
    if key == "who":
        text = {"family": "Who's coming? How many adults and kids, and roughly how old are the kids?",
                "friends": "How many of you are going, and is everyone 21 or older?",
                "game": "How many people are going?", "custom": "Who's coming?"}.get(sc, "Who's coming?")
        opts = WHO_OPTS.get(sc, WHO_OPTS["custom"])
    if key == "where" and (iv["answers"].get("days") or 1) >= 3:
        text = (f"Where in Arizona? For {iv['answers']['days']} days you can pick several cities, "
                "in the order you'll visit them.")
    if key == "confirm":
        text = "Here's what I've got:\n\n" + summary(iv) + "\n\nShall I plan it? You can also type anything you'd like to add."
    default = [o for o in (iv["answers"].get(key) or []) if o in (opts or [])] if multi else []
    msg = "\n\n".join(x for x in [intro, note, f"**{text}**" if key != "confirm" else text] if x)
    return {"key": key, "text": msg, "options": opts or [], "multi": multi, "default": default,
            # numbered among the questions actually asked (skipped ones don't count)
            "step": len([q for q in QUESTIONS[:iv["step"] + 1] if q[0] not in iv.get("skip", [])]),
            "total": len([q for q in QUESTIONS if q[0] not in iv.get("skip", [])])}


def _pick(text: str, opts: list[str]) -> list[str]:
    t = text.lower()
    return [o for o in opts if o.lower() in t]


def answer(iv: dict, message: str) -> tuple[dict, dict | None, bool]:
    """Record an answer. Returns (interview, next_question or None, done)."""
    from .agents.planner import CITY_ALIASES, mentioned_az_city, parse_request_rules
    iv = {**iv, "answers": dict(iv["answers"])}
    a = iv["answers"]
    key = _q(iv)[0]
    t = (message or "").strip()
    low = t.lower()
    skip = bool(re.fullmatch(r"(skip|no|none|nothing( else)?|any|anything|not sure|doesn'?t matter|no preference)\.?", low))
    note = None
    if key == "where":
        from .agents.planner import cities_in_order
        cities = [c for _, c in cities_in_order(t)] or ([mentioned_az_city(t)] if mentioned_az_city(t) else [])
        if not cities and not skip:
            return iv, question(iv, note="I didn't recognize that Arizona city - pick one below or type another (e.g. Prescott, Page, Yuma)."), False
        a["where"] = " then ".join(cities) if cities else "Phoenix"  # several cities: a multi-city trip
    elif key == "when":
        a["when"] = {"this saturday": "Saturday", "this sunday": "Sunday", "next weekend": "next weekend"}.get(low, t or "Saturday")
    elif key == "who":
        a["who"] = t
    elif key == "needs":
        picked = _pick(t, NEED_OPTS) or [o for o, rx in (("Hotel stay", r"hotel|stay|overnight|resort|motel|lodg"),
                                                          ("Night out", r"bar|drinks|night ?out|club"),
                                                          ("Activities", r"activit|things to do|sights|fun"))
                                         if re.search(rx, low)]
        if picked:
            a["needs"] = picked
        a["needs_note"] = t if not picked and not skip else ""
    elif key == "interests":
        picked = _pick(t, INTEREST_OPTS)
        if picked or not skip:
            a["interests"] = picked or a.get("interests", [])
        a["interests_note"] = t if not picked and not skip else ""
    elif key == "food_budget":
        a["food"] = [o for o in _pick(t, FOOD_OPTS) if o != "Any food"]
        r = parse_request_rules(t)
        if r.cuisines and not a["food"]:
            a["food"] = r.cuisines
        m = re.search(r"\$?\s?(\d{2,4})\s*(?:-|to)\s*\$?\s?(\d{2,4})", t)
        if "no limit" in low:
            a["budget"] = None
        elif m:
            a["budget"] = int(m.group(2))
        elif re.search(r"under\s*\$?\s?(\d+)", low):
            a["budget"] = int(re.search(r"under\s*\$?\s?(\d+)", low).group(1))
        elif r.budget_per_person:
            a["budget"] = int(r.budget_per_person)
    elif key == "extras":
        a["extras"] = [o for o in _pick(t, EXTRA_OPTS) if o != "Nothing else"]
        a["extras_note"] = t if not a["extras"] and not skip else ""
    elif key == "confirm":
        if re.search(r"start over|restart", low):
            fresh, q = start(iv["scenario"])
            return fresh, q, False
        if not re.match(r"^(plan it|yes|yep|sure|go|ok|okay|do it|sounds good|perfect|great)\b", low):
            a["final_note"] = t  # anything typed here is added to the request
        iv["active"] = False
        return iv, None, True
    iv["step"] += 1
    return iv, question(iv, note=note), False


def summary(iv: dict) -> str:
    a = iv["answers"]
    if a.get("base_note"):
        lines = [f"- **Your request:** {a['base_note']}", f"- **Where:** {a.get('where', 'Phoenix')}"]
        if a.get("when"):
            lines.append(f"- **When:** {a['when']}")
        if a.get("who"):
            lines.append(f"- **Who:** {a['who']}")
        if a.get("interests") or a.get("interests_note"):
            lines.append(f"- **Into:** {', '.join(a.get('interests') or []) or a.get('interests_note')}")
        return "\n".join(lines)
    lines = [f"- **Where:** {a.get('where', 'Phoenix')}",
             f"- **When:** {a.get('when', 'Saturday')}"]
    if a.get("who"):
        lines.append(f"- **Who:** {a['who']}")
    lines.append(f"- **Plan:** {', '.join(a.get('needs') or ['Activities', 'Lunch', 'Dinner'])}")
    if a.get("interests") or a.get("interests_note"):
        lines.append(f"- **Into:** {', '.join(a.get('interests') or []) or a.get('interests_note')}")
    if a.get("food"):
        lines.append(f"- **Food:** {', '.join(a['food'])}")
    lines.append(f"- **Budget:** {'$' + str(a['budget']) + ' per person' if a.get('budget') else 'no limit'}")
    extras = list(a.get("extras") or []) + ([a["extras_note"]] if a.get("extras_note") else [])
    if extras:
        lines.append(f"- **Also:** {', '.join(extras)}")
    return "\n".join(lines)


INTEREST_WORDS = {"Hiking & outdoors": "hiking and outdoors", "Museums & history": "museums and history",
                  "Animals & zoos": "zoo and animals", "Fun & games": "fun activities", "Shopping": "shopping",
                  "Sports": "sports game", "Scenic views": "scenic views", "Art": "art galleries",
                  "Tours & adventure": "tours and adventure", "Breweries & wineries": "breweries and wineries"}


def compose(iv: dict) -> str:
    """Turn the answers into one request sentence the Planner understands."""
    a, sc = iv["answers"], iv["scenario"]
    if a.get("base_note"):
        parts = [a["base_note"].rstrip(". "), f"in {a.get('where', 'Phoenix')}"]
        if a.get("when"):
            parts.append(a["when"])
        if a.get("who"):
            parts.append(a["who"])
        if a.get("interests"):
            parts.append("we like " + ", ".join(INTEREST_WORDS.get(i, i.lower()) for i in a["interests"]))
        if a.get("interests_note"):
            parts.append(a["interests_note"])
        if (a.get("days") or 1) >= 2 and "hotel" not in a["base_note"].lower():
            parts.append("and a hotel to stay overnight")
        if a.get("final_note"):
            parts.append(a["final_note"])
        return ", ".join(p for p in parts if p)
    needs = a.get("needs") or ["Activities", "Lunch", "Dinner"]
    who = (a.get("who") or "").lower()
    parts = []
    when = a.get("when", "Saturday")
    length = a.get("length")
    parts.append(f"{'evening ' if length == 'Evening' else ''}trip in {a.get('where', 'Phoenix')} {when}".strip())
    # group
    m_ad, m_kid = re.search(r"(\d+)\s*adults?", who), re.search(r"(\d+)\s*kids?", who)
    m_n = re.search(r"(\d+)\s*(?:friends|people|of us|adults)", who)
    if m_ad or m_kid:
        adults, kids = int(m_ad.group(1)) if m_ad else 2, int(m_kid.group(1)) if m_kid else 0
        parts.append(f"for {adults + kids} people" + (f" with {kids} kids" if kids else ""))
    elif m_n:
        parts.append(f"for {m_n.group(1)} people")
    elif "just me" in who:
        parts.append("solo, just me")
    elif sc == "family":
        parts.append("for 4 people with 2 kids")
    if "toddler" in who:
        parts.append("with toddlers")
    if "teen" in who:
        parts.append("kids are teens")
    if re.search(r"21\+|over 21|all 21", who):
        parts.append("we're all 21+")
    if "under 21" in who:
        parts.append("some of us are under 21")
    if sc == "date":
        parts.append("romantic date night")
    # what to plan
    want = []
    if "Activities" in needs:
        want.append("activities")
    want += [n.lower() for n in needs if n in ("Breakfast", "Lunch", "Dinner", "Dessert")]
    if "Hotel stay" in needs:
        want.append("a hotel to stay overnight")
    if "Night out" in needs and "under 21" not in who and sc != "family":
        want.append("go out for drinks after")
    parts.append("plan only: " + ", ".join(want))
    if a.get("needs_note"):
        parts.append(a["needs_note"])
    # interests, food, budget, extras
    if a.get("interests"):
        parts.append("we like " + ", ".join(INTEREST_WORDS.get(i, i.lower()) for i in a["interests"]))
    if a.get("interests_note"):
        parts.append(a["interests_note"])
    for f in a.get("food") or []:
        parts.append("vegetarian food" if f == "Vegetarian-friendly" else f"{f.lower()} food")
    if a.get("budget"):
        parts.append(f"${a['budget']} per person")
    ex = {"Kid-friendly everything": "kid friendly", "Wheelchair accessible": "wheelchair accessible",
          "Vegetarian": "vegetarian", "Hidden gems": "hidden gems", "Indoor (beat the heat)": "indoor activities",
          "Highly rated only": "highly rated"}
    parts += [ex[e] for e in a.get("extras") or [] if e in ex]
    if sc == "family" and "kid friendly" not in parts:
        parts.append("kid friendly")
    for k in ("extras_note", "final_note"):
        if a.get(k):
            parts.append(a[k])
    return ", ".join(p for p in parts if p)
