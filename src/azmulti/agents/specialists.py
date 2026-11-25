"""ATTRACTION agent and DINING & NIGHTLIFE agent.

Each one owns its stops: it builds a filtered semantic search (Method 1 + Method 3 retrieval),
relaxes constraints step by step if nothing matches, and then GPT-4o picks one of the top
candidates with a one-sentence, data-grounded reason (Method 2). Offline, the top-ranked
candidate wins and the reason is generated from its data.
"""
from __future__ import annotations

import json
import re

from .. import prompts
from ..catalog import get_catalog
from ..llm import structured
from ..state import Choice, PlannerState
from ..units import dist, snip

TIER_CAP = {"budget": {"restaurant": 20, "attraction": 25, "nightlife": 25, "lodging": 120},
            "balanced": {"restaurant": 60, "attraction": 90, "nightlife": 40, "lodging": 260},
            "premium": {"restaurant": None, "attraction": None, "nightlife": None, "lodging": None}}
BUDGET_SHARE = {"breakfast": 0.12, "lunch": 0.17, "dinner": 0.30, "dessert": 0.06, "attraction": 0.22, "nightlife": 0.2}


def _anchor(slots: list[dict], i: int, city_center) -> tuple[tuple[float, float], bool]:
    """Search near the neighbouring chosen stops (same day); fall back to the city centre."""
    day = slots[i]["day"]
    if slots[i].get("anchor_id"):  # an added stop is searched right next to the stop it follows
        a = next((s["place"] for s in slots if s.get("place") and s["place"]["id"] == slots[i]["anchor_id"]), None)
        if a:
            return (a["lat"], a["lon"]), True
    same_day =[(abs(j - i), s["place"]) for j, s in enumerate(slots) if s["day"] == day and s.get("place") and j != i]
    if same_day:  # average of the closest already-chosen stops (both neighbours when they exist)
        d0 = min(d for d, _ in same_day)
        near = [p for d, p in same_day if d == d0]
    else:
        near = []
    if near:
        lat = sum(p["lat"] for p in near) / len(near)
        lon = sum(p["lon"] for p in near) / len(near)
        return (lat, lon), True
    return city_center, False


_GENERIC = {"the", "trail", "trailhead", "park", "hiking", "restaurant", "cafe", "bar", "grill", "and", "museum",
            "of", "at", "mexican", "food", "kitchen", "co", "company", "az", "arizona"}


def _name_tokens(name: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9']+", name.lower()) if w not in _GENERIC and len(w) > 2}


def _same_place(a: set[str], b: set[str]) -> bool:
    return bool(a and b) and len(a & b) / min(len(a), len(b)) >= 0.99


def _max_cost(slot: dict, req: dict) -> float | None:
    if slot.get("max_cost") is not None:
        return slot["max_cost"]
    kind = slot["kind"]
    cap = TIER_CAP[req.get("tier", "balanced")][kind]
    if kind == "lodging":  # hotels are priced per room-night; the Budget agent converts to per person
        return cap
    if req.get("budget_per_person"):
        share = BUDGET_SHARE.get(slot.get("meal") or kind, 0.2) * req["budget_per_person"] * 1.3
        cap = share if cap is None else min(cap, share)
        cap = max(cap, 12.0 if kind == "restaurant" else 25.0 if kind == "nightlife" else 0.0)
    return cap


def _reason_rules(slot: dict, p: dict, req: dict, chained: bool) -> str:
    src = "TripAdvisor" if p.get("review_source") == "TripAdvisor" else "Google"
    bits = [f"rated {p['rating']:.1f}★ by {int(p['n_reviews']):,} {src} reviewers"]
    if p.get("ev_love"):
        bits.append(f"travelers love: {p['ev_love'].split(';')[0].strip().rstrip('.')[:120]}")
    elif p.get("description"):
        bits.append(p["description"].rstrip(".")[:120])
    if req.get("has_kids") and p.get("kids"):
        bits.append("tagged good for kids")
    if p.get("ta_rank"):
        bits.append(f"TripAdvisor {p['ta_rank']}")
    if p.get("distance_km") is not None:
        bits.append(f"{dist(p['distance_km'])} from the {'neighbouring stops' if chained else req['city'] + ' centre'}")
    out = f"{p['primary_category']}, " + "; ".join(bits) + "."
    ev = p.get("review_evidence")
    if ev and ev.get("similarity", 0) >= 0.3:  # Method 3: the retrieved review that best matches the request
        out += f" A reviewer said ({ev['stars']}★, {ev.get('year') or ''}): \"{snip(ev['text'], 160)}\""
    return out


def _compact(p: dict) -> dict:
    keep = ["id", "name", "city", "primary_category", "bayes_rating", "rating", "n_reviews", "cost_pp", "price_level",
            "distance_km", "similarity", "kids", "wheelchair", "vegetarian", "groups", "atmosphere", "description",
            "ev_love", "ev_complaints", "ta_rank", "open_ok", "accepts_reservations", "menu_highlights", "categories",
            "review_match"]
    out = {k: p.get(k) for k in keep if p.get(k) not in (None, "", False)}
    if (p.get("review_evidence") or {}).get("text"):  # retrieved real review text for GPT-4o to ground its reason
        ev = p["review_evidence"]
        out["review_evidence"] = f"{ev['stars']}★ ({ev.get('year')}): {ev['text'][:300]}"
    return out


def _personalize(cands: list[dict], req: dict, profile: dict) -> list[dict]:
    """Re-rank: atmosphere match, learned likes/dislikes (feedback), and past-experience tracking
    (places already shown in earlier plans drop a little so repeat plans feel fresh)."""
    likes, dislikes = profile.get("liked_categories", {}), profile.get("disliked_categories", {})
    seen = set(profile.get("history", [])) if profile.get("memory_on") else set()
    favs = {f["id"] for f in profile.get("favorites", [])}
    atm = (req.get("atmosphere") or "").lower()
    for c in cands:
        adj = 0.0
        if atm and atm in (c.get("atmosphere") or "").lower():
            adj += 0.08
        cats = (c.get("categories") or "").split(", ")
        adj += 0.04 * sum(min(3, likes.get(x, 0)) for x in cats[:2])
        adj -= 0.08 * sum(min(3, dislikes.get(x, 0)) for x in cats[:2])
        if c["id"] in seen:
            adj -= 0.05
        if c["id"] in favs:
            adj += 0.03
        c["score"] = round(c["score"] + adj, 3)
    return sorted(cands, key=lambda c: -c["score"])


def fill_slot(slot: dict, slots: list[dict], i: int, req: dict, used: set[str], agent: str,
              profile: dict | None = None) -> tuple[dict | None, list[str], str]:
    cat = get_catalog()
    center = (cat.locate(slot["city"]) if slot.get("city") else None) or cat.center_for(req)  # multi-city: that day's city
    anchor, chained = _anchor(slots, i, center)
    kind = slot["kind"]
    require, avoid = [], []
    if kind != "attraction" and req.get("halal"):
        require.append("halal")
    if req.get("has_kids"):
        avoid.append("adults_only")
        if kind == "restaurant":
            require.append("kids")
        if slot.get("avoid_strenuous"):
            avoid.append("strenuous")
    else:
        avoid.append("kids_only")  # no children's play centres for adult groups
    if kind == "attraction":
        avoid.append("not_a_destination")  # tourism offices, chambers of commerce
    if req.get("wheelchair"):
        require.append("wheelchair")
    if kind == "restaurant" and any(d in ("vegetarian", "vegan") for d in req.get("dietary", [])):
        require.append("vegetarian")
    if kind == "restaurant" and req.get("party_size", 2) >= 6:
        require.append("groups")
    if slot.get("indoor_only"):
        avoid.append("outdoor")
    # A theme the traveler asked for is a filter; a default theme is only a hint in the query,
    # so we stay local instead of driving across the metro to match it.
    themes = [slot["theme"]] if slot.get("theme") and kind == "attraction" and not slot.get("soft_theme") else None
    cuisines = [slot["cuisine"]] if slot.get("cuisine") else None
    meal_flag = {"breakfast": "serves_breakfast", "lunch": "serves_lunch", "dinner": "serves_dinner"}.get(slot.get("meal"))
    open_at = (req["weekday"], slot["target"], slot["duration"]) if req.get("weekday") is not None and kind != "lodging" else None
    exclude = used | set(slot.get("exclude", []))
    # Stay close: 10 km around neighbouring stops, 15 km around the city centre for the first
    # stop of the day; the ladder below widens only if nothing matches.
    hop = {"walking": 2.0, "transit": 6.0}.get(req.get("transport"), 10.0)
    radius = hop if chained else min(15.0 if req.get("transport") != "walking" else 5.0, req.get("radius_km", 30.0))
    base = dict(kind=kind, k=12, open_at=open_at, exclude=exclude,
                hidden_gems=req.get("hidden_gems") or req.get("avoid_crowds"),
                min_reviews=20 if kind != "attraction" else 10, min_cost=slot.get("min_cost"))
    min_rating = req.get("min_rating")

    # Relaxation ladder: strictest first. Each step records what was given up. A cost ceiling
    # the traveler asked for ("cheaper") is never dropped - at worst we stay in the same tier.
    cost = _max_cost(slot, req)
    hard_cost = slot.get("max_cost")
    near = dict(center=anchor, radius_km=radius)
    keep = [r for r in require if r in ("wheelchair", "halal")]
    # The per-stop share of the budget is a soft guide; the tier cap (or an explicit "cheaper") is firmer.
    tier_cost = hard_cost if hard_cost is not None else TIER_CAP[req.get("tier", "balanced")][kind]
    ladder = [
        ("", dict(themes=themes, cuisines=cuisines, require=require + ([meal_flag] if meal_flag else []), avoid=avoid, max_cost=cost, min_rating=min_rating, **near)),
        ("meal tag", dict(themes=themes, cuisines=cuisines, require=require, avoid=avoid, max_cost=cost, min_rating=min_rating, **near)),
        ("amenity tags", dict(themes=themes, cuisines=cuisines, require=keep, avoid=avoid, max_cost=cost, min_rating=min_rating, **near)),
        ("per-stop budget share", dict(themes=themes, cuisines=cuisines, require=keep, avoid=avoid, max_cost=tier_cost, min_rating=min_rating, **near)),
        ("rating", dict(themes=themes, cuisines=cuisines, require=keep, avoid=avoid, max_cost=tier_cost, **near)),
        ("wider area", dict(themes=themes, cuisines=cuisines, require=keep, avoid=avoid, max_cost=cost, center=center, radius_km=max(radius, 45))),
        ("theme", dict(themes=None, cuisines=cuisines, require=[], avoid=avoid, max_cost=cost, center=center, radius_km=60)),
    ]
    if hard_cost is not None and slot.get("cost_ceiling_fallback"):
        ladder.append(("cheaper price (same tier instead)", dict(themes=themes, cuisines=cuisines, require=[], avoid=avoid,
                                                                max_cost=slot["cost_ceiling_fallback"], **near)))
    else:
        ladder.append(("cost", dict(themes=None, cuisines=cuisines, require=[], avoid=avoid, max_cost=hard_cost, center=center, radius_km=80)))
    ladder.append(("everything", dict(themes=None, cuisines=None, require=[], avoid=[], max_cost=hard_cost if not slot.get("cost_ceiling_fallback") else slot["cost_ceiling_fallback"], center=center, radius_km=120)))
    if slot.get("meal") == "dessert":
        ladder = ladder[:5]  # an optional treat is not worth a long drive
    relaxed: list[str] = []
    cands: list[dict] = []
    used_names = [_name_tokens(s["place"]["name"]) for s in slots if s.get("place")]
    for attempt in (0, 1):
        for what, kw in ladder:
            cands = cat.search(slot["query"], **base, **kw)
            # the same landmark can have several Google listings ("Tumamoc Hill", "Tumamoc Hill Trail")
            cands = [c for c in cands if not any(_same_place(_name_tokens(c["name"]), u) for u in used_names)]
            if cands:
                break
            if what and what not in relaxed:
                relaxed.append(what)
        if cands or not base.get("min_cost"):
            break
        base["min_cost"] = None  # a "spend more" floor is a preference, not a hard rule
        relaxed.append("minimum spend")
    if not cands:
        return None, relaxed, "rules"
    cands = _personalize(cands, req, profile or {})
    # Variety: don't repeat the same kind of attraction on one day (two stadiums, two escape rooms)
    if kind == "attraction":
        day_cats = {s["place"]["primary_category"] for s in slots if s.get("place") and s["day"] == slot["day"] and s["kind"] == "attraction"}
        varied = [c for c in cands if c["primary_category"] not in day_cats]
        cands = varied or cands
    relaxed = [f"{min_rating}+ star rating" if r == "rating" else r for r in relaxed if r != "rating" or min_rating]

    prev =[s["place"]["primary_category"] for s in slots if s.get("place")]
    user = (f"Traveler request: {json.dumps({k: req[k] for k in ('city', 'party_size', 'has_kids', 'budget_per_person', 'tier', 'occasion', 'activity_text', 'cuisines') if req.get(k)})}\n"
            f"Stop to fill: {slot['label']} at {slot['target'] // 60}:{slot['target'] % 60:02d}, query: {slot['query']}\n"
            f"Already in plan: {prev}\nCandidates:\n" + "\n".join(json.dumps(_compact(c), default=str) for c in cands[:6]))
    fallback = lambda: Choice(choice_id=cands[0]["id"], reason=_reason_rules(slot, cands[0], req, chained))  # noqa: E731
    choice, how = structured(prompts.CHOOSER.format(agent=agent), user, Choice, fallback)
    pick = next((c for c in cands if c["id"] == choice.choice_id), None)
    if pick is None:
        choice, how = fallback(), "rules"
        pick = cands[0]
    slot["reason"] = choice.reason
    pick_toks = _name_tokens(pick["name"])
    slot["alternatives"] = [_compact(c) | {"lat": c["lat"], "lon": c["lon"], "url": c["url"]} for c in cands
                            if c["id"] != pick["id"] and not _same_place(_name_tokens(c["name"]), pick_toks)][:3]
    slot["relaxed"] = relaxed
    return pick, relaxed, how


def _run(state: PlannerState, kinds: set[str], agent: str) -> dict:
    req = state["request"]
    slots = [dict(s) for s in state["slots"]]
    used = {s["place"]["id"] for s in slots if s.get("place")}
    trace = []
    for i, s in enumerate(slots):
        if s["kind"] not in kinds or s.get("place"):
            continue
        pick, relaxed, how = fill_slot(s, slots, i, req, used, agent, state.get("profile"))
        if pick:
            s["place"] = pick
            s.pop("fallback_place", None)
            used.add(pick["id"])
            for k in ("min_cost", "max_cost", "cost_ceiling_fallback"):  # one-shot constraints
                if k in s and not (k == "max_cost" and s.get("kind") == "attraction" and s.get(k) == 0.0 and req.get("free_only")):
                    s.pop(k, None)
            msg = f"{s['label']}: {pick['name']} ({pick['city']}, {pick['bayes_rating']:.1f}*)"
            if relaxed:
                msg += f" - relaxed: {', '.join(relaxed)}"
        elif s.get("fallback_place"):  # nothing cheaper exists: keep what we had rather than lose the stop
            s["place"] = s.pop("fallback_place")
            s.pop("max_cost", None)
            used.add(s["place"]["id"])
            s["reason"] = s.get("reason") or "Kept: no cheaper option matched nearby."
            msg = f"{s['label']}: nothing cheaper nearby, kept {s['place']['name']}"
        elif s.get("meal") == "dessert":
            s["drop"] = True
            msg = f"{s['label']}: nothing close by, stop dropped"
        else:
            msg = f"{s['label']}: no match found"
        trace.append({"agent": agent, "via": how, "did": msg})
    return {"slots": [s for s in slots if not s.get("drop")], "trace": state.get("trace", []) + trace}


def attraction_node(state: PlannerState) -> dict:
    return _run(state, {"attraction"}, "Attraction")


def dining_node(state: PlannerState) -> dict:
    return _run(state, {"restaurant", "nightlife"}, "Dining & Nightlife")


def lodging_node(state: PlannerState) -> dict:
    """LODGING agent: picks one hotel near the evening's stops and keeps it for every night of the trip."""
    if not any(s["kind"] == "lodging" for s in state.get("slots") or []):
        return {}
    slots = [dict(s) for s in state["slots"]]
    home = (state.get("request") or {}).get("city")
    city_of = lambda s: s.get("city") or home  # noqa: E731  (multi-city trips: one hotel per city)
    first: dict = {}
    for s in slots:
        if s["kind"] == "lodging" and s.get("place"):
            first.setdefault(city_of(s), s)
    out = {}
    if any(s["kind"] == "lodging" and not s.get("place") and city_of(s) not in first for s in slots):
        # leave only the first night in each new city for the agent to choose; later nights there reuse it
        todo, seen = [], set(first)
        for s in slots:
            if s["kind"] == "lodging" and not s.get("place") and city_of(s) not in seen:
                todo.append(s["id"])
                seen.add(city_of(s))
        hold = [dict(s, kind="_hold") if s["kind"] == "lodging" and not s.get("place") and s["id"] not in todo else s for s in slots]
        out = _run({**state, "slots": hold}, {"lodging"}, "Lodging")
        slots = [dict(s, kind="lodging") if s["kind"] == "_hold" else s for s in out["slots"]]
        for s in slots:
            if s["kind"] == "lodging" and s.get("place"):
                first.setdefault(city_of(s), s)
    for s in slots:
        f = first.get(city_of(s))
        if s["kind"] == "lodging" and f is not None and s is not f and not s.get("place"):
            s.update(place=f["place"], reason="Same hotel as the night before.", alternatives=f.get("alternatives", []))
    return {**out, "slots": slots}
