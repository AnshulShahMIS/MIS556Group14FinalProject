"""Offline evaluation (no API key needed):  python eval/evaluate.py

Part A - Retrieval Precision@5 / Recall@5 / NDCG@5 on intent-style queries ("somewhere to see paintings")
         whose wording avoids the category name. Relevance = the place's Google category matches
         the intent (a label independent of the query wording). Recall@5 is pooled: the relevant set
         for a query is every relevant place found in the top 20 of any method (standard TREC pooling).
         Compared: MiniLM + review RAG (ours), MiniLM only, TF-IDF, and a popularity baseline.
Part C - Top 5 attractions for different traveler personas in the same city: precision against each
         persona's interests, and how different the lists are (personalization).
Part D - Grounding: share of recommended stops backed by a retrieved real review that matches the request.
Part B - Itinerary constraint satisfaction on benchmark trip requests: the full multi-agent
         graph vs. a popularity baseline that fills the same stops with the most-reviewed
         place of the right kind (no constraints, no agents).
Results are written to eval/results.md.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("AZ_OFFLINE", "1")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from azmulti import hours as H  # noqa: E402
from azmulti.agents.critic import score  # noqa: E402
from azmulti.agents.logistics import budget_node, route_node  # noqa: E402
from azmulti.agents.planner import build_slots, parse_request_rules  # noqa: E402
from azmulti.catalog import Catalog, get_catalog, haversine_km  # noqa: E402
from azmulti.embeddings import TfidfEmbedder  # noqa: E402
from azmulti.graph import Planner  # noqa: E402

# (query, city, kind, relevant-category regex)
QUERIES = [
    ("somewhere to look at paintings and sculpture", "Phoenix", "attraction", r"Art museum|Art gallery|Art center|Sculpture"),
    ("see paintings and local artists", "Scottsdale", "attraction", r"Art museum|Art gallery|Art center"),
    ("see cactus and desert plants", "Phoenix", "attraction", r"Botanical garden|Garden"),
    ("walk among flowers and plants", "Tucson", "attraction", r"Botanical garden|Garden"),
    ("look at stars through a telescope", "Flagstaff", "attraction", r"Observatory|Planetarium"),
    ("stargazing night sky", "Tucson", "attraction", r"Observatory|Planetarium"),
    ("see lions giraffes and other animals", "Phoenix", "attraction", r"Zoo|Aquarium|Wildlife|Animal"),
    ("see animals with the kids", "Tucson", "attraction", r"Zoo|Aquarium|Wildlife|Animal"),
    ("learn about the old west and pioneers", "Tucson", "attraction", r"museum|Historical|Mission|Ghost town|Heritage"),
    ("ancient ruins and native history", "Phoenix", "attraction", r"museum|Archaeological|Historical|Ruin|Monument"),
    ("a strenuous climb with city views", "Phoenix", "attraction", r"Hiking area|Mountain peak|Trail|Park"),
    ("red rock trail walk", "Sedona", "attraction", r"Hiking area|Trail|Scenic|Park|Mountain"),
    ("thrill rides and roller coasters", "Mesa", "attraction", r"Amusement|Theme park|Water park"),
    ("solve puzzles to get out of a locked room", "Phoenix", "attraction", r"Escape room"),
    ("knock down some pins", "Tempe", "attraction", r"Bowling"),
    ("watch a play or musical", "Phoenix", "attraction", r"theater|Theater|Concert|Performing"),
    ("go wine tasting", "Scottsdale", "attraction", r"Winery|Vineyard|Wine"),
    ("float down a river or paddle", "Phoenix", "attraction", r"Raft|Canoe|kayak|Boat|Lake|River|Marina"),
    ("tacos and burritos", "Tucson", "restaurant", r"Mexican|Taco|Burrito"),
    ("street tacos", "Phoenix", "restaurant", r"Mexican|Taco"),
    ("raw fish rolls and nigiri", "Scottsdale", "restaurant", r"Sushi|Japanese"),
    ("noodle soup with broth", "Mesa", "restaurant", r"Ramen|Pho|Vietnamese|Noodle|Japanese"),
    ("wood fired pies", "Phoenix", "restaurant", r"Pizza|Italian"),
    ("slow smoked brisket and ribs", "Phoenix", "restaurant", r"Barbecue"),
    ("pancakes and eggs in the morning", "Tempe", "restaurant", r"Breakfast|Brunch|Diner|Pancake"),
    ("curry and naan", "Chandler", "restaurant", r"Indian"),
    ("plant based meals with no meat", "Phoenix", "restaurant", r"Vegan|Vegetarian"),
    ("a fancy cut of beef", "Scottsdale", "restaurant", r"Steak"),
    ("gyros and hummus", "Tempe", "restaurant", r"Mediterranean|Greek|Middle Eastern|Lebanese"),
    ("a cold scoop on a hot day", "Phoenix", "restaurant", r"Ice cream|Frozen yogurt|Dessert|Gelato"),
    ("craft cocktails in a dim lounge", "Scottsdale", "nightlife", r"Cocktail bar|Lounge"),
    ("watch the game with beers", "Tempe", "nightlife", r"Sports bar|Pub|Bar"),
    ("dance until late", "Scottsdale", "nightlife", r"Night club|Dance"),
]

TRIPS = [
    "Plan a Saturday in Scottsdale for four friends. We're all 24, have a $125 budget per person, want a fun outdoor activity, Mexican food for dinner, and somewhere to go out afterward.",
    "Family day in Phoenix in July with two kids, we like animals and museums, $60 per person",
    "Romantic date night in Sedona on Friday, scenic sunset then Italian dinner, $150 per person",
    "Weekend in Tucson for 2 people, history and hiking, cheap",
    "Sunday in Tempe with my parents, wheelchair accessible, museums and lunch, $80 per person",
    "Half day morning hike in Flagstaff for 3 friends then lunch, $40 each",
    "Birthday dinner and drinks in downtown Phoenix for 6 people on Friday, sushi, $90 per person",
    "Fun day in Mesa with toddlers, $50 per person, indoor activities",
    "Vegetarian food and art galleries in Scottsdale on Thursday for 2, $100 per person",
    "Cheap day in Tucson for a student, outdoors and tacos, $30",
    "Sedona day trip for 4 adults, hiking, scenic views, steakhouse dinner, $200 per person",
    "Thursday in Chandler, Indian food, bowling and a sports bar, 5 friends aged 30, $70 per person",
]


def dcg(rels):
    return sum(r / np.log2(i + 2) for i, r in enumerate(rels))


def ranked(cat: Catalog, q: str, city: str, kind: str, k: int, popularity: bool = False, reviews: bool = True) -> list[dict]:
    ctr = cat.locate(city)
    if popularity:
        df = cat.df
        d = haversine_km(ctr[0], ctr[1], df.lat.to_numpy(), df.lon.to_numpy())
        sub = df[(df.kind == kind).to_numpy() & (d <= 25)].nlargest(k, "n_reviews")
        return [{"id": r.id, "categories": r.categories} for r in sub.itertuples()]
    return cat.search(q, kind=kind, center=ctr, radius_km=25, k=k, reviews=reviews)


def is_rel(categories, rx) -> int:
    return int(bool(pd.Series([categories]).str.contains(rx, case=False, regex=True)[0]))


def eval_retrieval(runs: dict[str, list[list[dict]]]) -> pd.DataFrame:
    """runs: method -> per-query ranked lists (top 20). Pooled Recall@5 over the union of relevant hits."""
    pools = []
    for qi, (_, _, _, rx) in enumerate(QUERIES):
        pools.append({r["id"] for lists in runs.values() for r in lists[qi] if is_rel(r["categories"], rx)})
    rows = []
    for label, lists in runs.items():
        p5, r5, nd = [], [], []
        for qi, (_, _, _, rx) in enumerate(QUERIES):
            top = lists[qi][:5]
            rel = [is_rel(r["categories"], rx) for r in top] + [0] * (5 - len(top))
            p5.append(sum(rel) / 5)
            r5.append(sum(rel) / len(pools[qi]) if pools[qi] else 0.0)
            nd.append(dcg(rel) / dcg(sorted(rel, reverse=True)) if sum(rel) else 0.0)
        rows.append({"method": label, "Precision@5": np.mean(p5), "Recall@5": np.mean(r5), "NDCG@5": np.mean(nd),
                     "queries": len(QUERIES)})
    return pd.DataFrame(rows)


# Part C: different travelers, same city -> different top 5 (expected result: "recommend different attractions
# based on each traveler's interests"). Relevance = category matches the persona's interests.
PERSONAS = [
    ("Hiker", "Sedona", "top 5 attractions in Sedona for challenging hikes with big views", r"Hiking|Trail|Mountain|Scenic|Park|Vista|Canyon"),
    ("Art lover", "Sedona", "top 5 attractions in Sedona for art galleries and local artists", r"Art|Gallery|Museum|Craft"),
    ("Spiritual traveler", "Sedona", "top 5 attractions in Sedona for vortexes, meditation and peaceful spiritual places",
     r"Vortex|Church|Chapel|Spiritual|Meditation|Monastery|Temple|Scenic|Garden|Stupa|Shrine|Religious|Hiking"),
    ("Family with kids", "Phoenix", "top 5 attractions in Phoenix for kids who love animals", r"Zoo|Aquarium|Wildlife|Animal|Farm|Petting"),
    ("History buff", "Phoenix", "top 5 attractions in Phoenix for history and native culture", r"museum|Histor|Heritage|Archaeolog|Monument|Ruin"),
    ("Thrill seeker", "Phoenix", "top 5 attractions in Phoenix for adrenaline, go karts and thrill rides",
     r"Amusement|Go-kart|Kart|Theme park|Water park|Zipline|Skydiv|Trampoline|Racing|Adventure|Paintball|Climb"),
    ("Stargazer", "Flagstaff", "top 5 attractions in Flagstaff for stargazing and astronomy", r"Observatory|Planetarium|Astronom|Science"),
    ("Foodie explorer", "Tucson", "top 5 attractions in Tucson for food markets and culinary tours", r"Market|Food|Farm|Tour|Winery|Brewery|Distillery"),
]


def eval_personas() -> tuple[pd.DataFrame, float]:
    from azmulti.agents.tools import top_attractions
    rows, lists = [], {}
    for name, city, ask, rx in PERSONAS:
        hits = top_attractions(ask, None, 5)["hits"]
        rel = [is_rel(f"{h['name']} | {h['categories']}", rx) for h in hits]  # label: category or the place's own name
        lists[name] = (city, {h["id"] for h in hits})
        grounded = np.mean([bool(h.get("review_evidence")) and h["review_evidence"]["similarity"] >= 0.3 for h in hits]) if hits else 0
        rows.append({"traveler": name, "city": city, "Precision@5": sum(rel) / 5, "grounded_in_review": grounded,
                     "top 5": "; ".join(h["name"] for h in hits)})
    over = []  # Jaccard overlap between travelers in the same city (lower = more personalized)
    names = list(lists)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            (c1, a), (c2, b) = lists[names[i]], lists[names[j]]
            if c1 == c2 and (a | b):
                over.append(len(a & b) / len(a | b))
    return pd.DataFrame(rows), float(np.mean(over)) if over else 0.0


def check_plan(req: dict, slots: list[dict], cat: Catalog) -> dict:
    stops = [s for s in slots if s.get("place")]
    ctr = cat.locate(req["city"])
    in_area = np.mean([haversine_km(ctr[0], ctr[1], s["place"]["lat"], s["place"]["lon"]) <= 30 for s in stops]) if stops else 0
    opened = [H.open_for(cat.schedule(s["place"]["_idx"]), (req["weekday"] + s["day"] - 1) % 7, s.get("start", s["target"]), s["duration"]) for s in stops]
    open_rate = np.mean([o is not False for o in opened]) if stops else 0
    kid_ok = (not req.get("has_kids")) or all(not s["place"].get("adults_only") for s in stops)
    under21_ok = (req.get("min_age") or 99) >= 21 or all(s["kind"] != "nightlife" for s in stops)
    wants = [s for s in stops if s.get("cuisine")]
    cuisine_ok = np.mean([s["cuisine"] in (s["place"].get("cuisines") or "") for s in wants]) if wants else 1.0
    wc = [s for s in stops if req.get("wheelchair")]
    wheelchair_ok = np.mean([bool(s["place"].get("wheelchair")) for s in wc]) if wc else 1.0
    return {"in_area": in_area, "open_during_visit": open_rate, "age_rules_ok": float(kid_ok and under21_ok),
            "cuisine_match": cuisine_ok, "wheelchair_match": wheelchair_ok, "filled": len(stops) / max(1, len(slots))}


def popularity_plan(text: str, cat: Catalog) -> dict:
    """Baseline: same stop skeleton, each filled with the most-reviewed place of that kind
    within 15 km of the city centre. No semantic matching, no constraints, no agents."""
    r = parse_request_rules(text)
    r.city = cat.canonical_city(r.city) or "Phoenix"
    r.weekday = 5 if r.weekday is None else r.weekday
    slots, _ = build_slots(r)
    ctr = cat.locate(r.city)
    df = cat.df
    d = haversine_km(ctr[0], ctr[1], df.lat.to_numpy(), df.lon.to_numpy())
    used = set()
    for s in slots:
        sub = df[(df.kind == s["kind"]).to_numpy() & (d <= 15) & ~df.id.isin(used).to_numpy()]
        if len(sub):
            i = sub.n_reviews.idxmax()
            s["place"] = cat.record(i) | {"similarity": float(cat.vecs[i] @ cat.embed(s["query"]))}
            used.add(s["place"]["id"])
    state = {"request": r.model_dump(), "slots": slots, "trace": []}
    state |= route_node(state)
    state |= budget_node(state)
    return state


def summarize(rows: list[dict]) -> dict:
    df = pd.DataFrame(rows)
    out = df.drop(columns=["trip"]).mean(numeric_only=True).to_dict()
    return out


def main():
    cat = get_catalog()
    print("Part A - retrieval ...")
    tfidf = TfidfEmbedder()
    tf_vecs = tfidf.fit(cat.df["doc"].tolist())
    tf_cat = Catalog(cat.df, tf_vecs, tfidf)
    tf_cat.centroids = cat.centroids
    runs = {
        "MiniLM embeddings + review RAG (ours)": [ranked(cat, q, c, k, 20) for q, c, k, _ in QUERIES],
        "MiniLM embeddings only": [ranked(cat, q, c, k, 20, reviews=False) for q, c, k, _ in QUERIES],
        "TF-IDF + ranking": [ranked(tf_cat, q, c, k, 20, reviews=False) for q, c, k, _ in QUERIES],
        "Popularity baseline (most reviews nearby)": [ranked(cat, q, c, k, 20, popularity=True) for q, c, k, _ in QUERIES],
    }
    ret = eval_retrieval(runs)
    print(ret.round(3).to_string(index=False))

    print("\nPart C - top 5 per traveler ...")
    pers, overlap = eval_personas()
    print(pers.drop(columns=["top 5"]).round(3).to_string(index=False), f"\nmean same-city overlap: {overlap:.3f}")

    print("\nPart B - itineraries ...")
    ours, base = [], []
    for t in TRIPS:
        out = Planner().ask(t)
        m = check_plan(out["request"], out["slots"], cat)
        st_ = [s for s in out["slots"] if s.get("place")]
        m.update(quality=out["quality"]["total"], within_budget=float(out["budget"]["status"] != "over budget"),
                 drive_min=out["route"]["minutes"], replans=out.get("replans", 0), trip=t[:50],
                 grounded_in_review=np.mean([(s["place"].get("review_evidence") or {}).get("similarity", 0) >= 0.3 for s in st_]) if st_ else 0)
        ours.append(m)
        b = popularity_plan(t, cat)
        q = score(b | {"issues": b.get("issues", [])})
        mb = check_plan(b["request"], b["slots"], cat)
        mb.update(quality=q["total"], within_budget=float(b["budget"]["status"] != "over budget"),
                  drive_min=b["route"]["minutes"], replans=0, trip=t[:50], grounded_in_review=0.0)
        base.append(mb)
        print(f"  ours {m['quality']:5.1f} | baseline {mb['quality']:5.1f} | {t[:70]}")
    comp = pd.DataFrame([summarize(ours) | {"system": "Multi-agent (ours)"},
                         summarize(base) | {"system": "Popularity baseline"}]).set_index("system").T
    print(comp.round(3).to_string())

    md = ["# Evaluation results", "", "Run: `python eval/evaluate.py` (offline mode: rule-based planner/chooser, MiniLM embeddings).", "",
          "How the four GenAI methods show up here: **Method 1** (MiniLM embeddings) and **Method 3** (top-k retrieval over "
          "57k real review quotes) drive Parts A, C and D; **Method 4** (LangGraph agents) produces the Part B itineraries; "
          "**Method 2** (GPT-4o prompts) runs only with an OpenAI key, so these offline numbers use the rule-based fallbacks.", "",
          "## A. Retrieval quality on intent-style queries (Precision@5, Recall@5)", "",
          f"{len(QUERIES)} queries worded without the category name (e.g. *\"somewhere to look at paintings and sculpture\"*), "
          "each searched within 25 km of a city. A result is relevant if its Google category matches the intent. "
          "Recall@5 is pooled: a query's relevant set is every relevant place that any method returned in its top 20.", "",
          ret.round(3).to_markdown(index=False), "",
          "## C. Top 5 attractions for different travelers (personalized recommendations)", "",
          f"{len(PERSONAS)} traveler personas; several share a city, so the lists show whether the agent recommends "
          "different attractions for different interests. `grounded_in_review` = share of the 5 picks backed by a "
          "retrieved real review that matches the request (similarity >= 0.3). A pick is relevant if its Google category or "
          "its name matches the persona's interest (many Sedona vortex sites are only categorized as 'Tourist attraction'). "
          "Flagstaff has a single stargazing attraction with 20+ reviews in the data (Lowell Observatory), which caps that row.", "",
          pers.round(2).to_markdown(index=False), "",
          f"Mean top-5 overlap (Jaccard) between travelers in the same city: **{overlap:.2f}** "
          "(0 = completely different lists, 1 = identical).", "",
          "## B. Itinerary quality on benchmark trip requests", "",
          f"{len(TRIPS)} requests (including the example from the project brief). The baseline uses the same stop skeleton "
          "but fills each stop with the most-reviewed place of that kind near the city centre.", "",
          comp.round(3).to_markdown(), "",
          "Metric notes: `in_area` = share of stops within 30 km of the requested city; `open_during_visit` = share not "
          "closed at the scheduled time (2021 hours); `age_rules_ok` = no bars for under-21s / adults-only places with kids; "
          "`cuisine_match`, `wheelchair_match` = requested constraint satisfied; `quality` = critic score out of 100.", "",
          "Caveats: relevance labels come from Google categories, which also appear in the embedded text, so Part A "
          "measures intent matching, not category look-up; the queries deliberately avoid category words. The midterm's "
          "leave-one-out Recall@5 needs per-user review histories (the separate 18M-review file), which this workbook does not contain, "
          "so Recall@5 here is pooled over the methods' results instead.",
          "", "`grounded_in_review` in Part B = share of plan stops whose 'why' cites a retrieved real review matching the request."]
    (ROOT / "eval" / "results.md").write_text("\n".join(md), encoding="utf-8")
    print("\nwrote eval/results.md")


if __name__ == "__main__":
    main()
