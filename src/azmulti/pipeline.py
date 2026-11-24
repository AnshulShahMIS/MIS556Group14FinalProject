"""Turn the team workbook (Arizona_Places_TripAdvisor_Hiking.xlsx) into the agents' catalog.

Steps
1. Read the five sheets (cached as parquet in data/raw the first time; Excel is slow).
2. Drop permanently closed places and rows outside Arizona's bounding box.
3. Label each place as an attraction, restaurant or nightlife spot (others are dropped).
4. Parse amenity tags into boolean filters (kids, wheelchair, vegetarian, groups, ...).
5. Join the TripAdvisor review summaries and hiking notes, which become RAG evidence.
6. Add ranking signals (Bayesian rating, popularity) and per-person cost estimates.
7. Build one text per place for embedding.
"""
from __future__ import annotations

import math
import re

import numpy as np
import pandas as pd

from . import categories as C
from .config import BAYES_PRIOR_MEAN, BAYES_PRIOR_N, CATALOG_PATH, CITY_CENTROIDS_PATH, PROCESSED_DIR, RAW_DIR, XLSX_PATH

SHEETS = {
    "AZ Places": "az_places", "TripAdvisor Reviews": "tripadvisor_reviews",
    "Hiking Places": "hiking_places", "TripAdvisor Coverage": "tripadvisor_coverage",
}
AZ_BOX = (31.3, 37.1, -114.9, -109.0)  # lat min/max, lon min/max


def load_sheets() -> dict[str, pd.DataFrame]:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    paths = {k: RAW_DIR / f"{v}.parquet" for k, v in SHEETS.items()}
    if not all(p.exists() for p in paths.values()):
        print(f"Reading {XLSX_PATH} (about a minute) ...")
        book = pd.read_excel(XLSX_PATH, sheet_name=list(SHEETS), dtype=str, engine="openpyxl")
        for k, df in book.items():
            df.to_parquet(paths[k], index=False)
    return {k: pd.read_parquet(p) for k, p in paths.items()}


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.astype(str).str.replace(",", "").str.extract(r"(-?[\d.]+)")[0], errors="coerce")


def _price_level(p) -> float:
    # The workbook has some prices in ₩ (a scraping artifact); the count of symbols is the level.
    if not isinstance(p, str) or not p.strip():
        return np.nan
    return float(min(4, len(re.findall(r"[$₩]", p)))) or np.nan


def _tags(amen: str | None) -> set[str]:
    out = set()
    if not isinstance(amen, str):
        return out
    for sec in amen.split(" | "):
        _, _, vals = sec.partition(": ")
        out.update(v.strip() for v in vals.split(", ") if v.strip())
    return out


def _tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if len(w) > 2}


def _join_review_sheet(cat: pd.DataFrame, sheet: pd.DataFrame, name_col: str, cols: dict[str, str]) -> pd.DataFrame:
    """Attach sheet columns to catalog rows by (Google rating, Google review count, name overlap)."""
    for new in cols.values():
        if new not in cat:
            cat[new] = None
    for _, r in sheet.iterrows():
        g_r, g_n = _num(pd.Series([r["Google Rating"]]))[0], _num(pd.Series([r["Google # Reviews"]]))[0]
        cand = cat[(cat.google_rating == g_r) & (cat.google_reviews == g_n)]
        toks = _tokens(r[name_col])
        best, best_overlap = None, 0
        for i, c in cand.iterrows():
            ov = len(toks & _tokens(c["name"]))
            if ov > best_overlap:
                best, best_overlap = i, ov
        if best is None:  # fall back to name-only match
            m = cat[cat["name"].str.lower() == r[name_col].lower()]
            best = m.index[0] if len(m) else None
        if best is not None:
            for src, new in cols.items():
                cat.at[best, new] = r.get(src)
    return cat


def _admission(cats: list[str], themes: list[str]) -> float:
    for c in cats:
        if c in C.ADMISSION_BY_CATEGORY:
            return C.ADMISSION_BY_CATEGORY[c]
    return 10.0 if themes else 0.0


def _meal_cost(level: float, cats: list[str]) -> float:
    if not math.isnan(level):
        return C.PRICE_LEVEL_COST[int(level)]
    t = " ".join(cats).lower()
    if "fast food" in t or "coffee" in t or "dessert" in t or "ice cream" in t or "bakery" in t:
        return 12.0
    if "steak" in t or "fine dining" in t or "seafood" in t:
        return 55.0
    return 25.0


EXTENDED_CSV = RAW_DIR / "arizona_places_extended.csv"
REVIEW_COLS = {"real_review_quotes": "review_quotes", "rating_distribution": "rating_dist", "praised_aspects": "praised",
               "complaint_aspects": "complaints", "rating_trend": "rating_trend", "last_review_date": "last_review",
               "real_reviews_in_dump": "reviews_in_dump", "share_1_2_star": "share_low_stars"}


def add_reviews(cat: pd.DataFrame) -> pd.DataFrame:
    """Attach REAL reviews from the team's extended file (Google Local review dump): up to 3 quotes per place,
    star breakdown, aspects reviewers praise / complain about, rating trend. The file's AI-generated sample
    reviews (`ai_reviews`) are deliberately NOT used - they must never be shown as real reviews."""
    for new in REVIEW_COLS.values():
        cat[new] = None
    if not EXTENDED_CSV.exists():
        print("(no arizona_places_extended.csv - reviews skipped)")
        return cat
    ext = pd.read_csv(EXTENDED_CSV, usecols=["place_id", *REVIEW_COLS], dtype=str).drop_duplicates("place_id")
    ext = ext.rename(columns=REVIEW_COLS).set_index("place_id")
    for col in REVIEW_COLS.values():
        cat[col] = cat["id"].map(ext[col])
    print(f"reviews attached: {cat.review_quotes.notna().sum():,} places")
    return cat


def build_catalog() -> pd.DataFrame:
    sheets = load_sheets()
    d = sheets["AZ Places"].copy()
    d = d.drop_duplicates("Google Map ID")
    d["lat"], d["lon"] = _num(d["Latitude"]), _num(d["Longitude"])
    la0, la1, lo0, lo1 = AZ_BOX
    d = d[d.lat.between(la0, la1) & d.lon.between(lo0, lo1)].copy()

    # City centroids from ALL businesses so "near Sedona" works for any city in the data.
    cent = d.groupby("City").agg(lat=("lat", "median"), lon=("lon", "median"), n=("lat", "size")).reset_index()
    cent = cent[cent.n >= 5].rename(columns={"City": "city"})

    d = d[d["Business Status"].fillna("") != "Permanently closed"].copy()
    d["cats"] = d["Categories"].fillna("").map(lambda s: [c.strip() for c in s.split(",") if c.strip()])
    d["google_rating"], d["google_reviews"] = _num(d["Google Avg Rating"]), _num(d["Google # Reviews"])
    d["rating"], d["n_reviews"] = _num(d["Review Rating (best source)"]), _num(d["# Reviews (best source)"])
    d["ta_rating"], d["ta_reviews"] = _num(d["TripAdvisor Rating"]), _num(d["TripAdvisor # Reviews"])

    def kind(row) -> str | None:
        cats, n = row.cats, row.google_reviews or 0
        if C.is_nightlife(cats) and n >= 10:
            return "nightlife"
        if C.is_attraction(cats, n) and n >= 5:
            return "attraction"
        if C.is_food(cats) and n >= 10:
            return "restaurant"
        if C.is_lodging(cats) and n >= 10:
            return "lodging"
        return None

    d["kind"] = d.apply(kind, axis=1)
    d = d[d.kind.notna()].copy()

    cat = pd.DataFrame({
        "id": d["Google Map ID"], "name": d["Name"], "address": d["Address"], "city": d["City"].fillna(""),
        "zip": d["ZIP"], "lat": d.lat, "lon": d.lon, "kind": d.kind,
        "categories": d.cats.map(lambda c: ", ".join(c)), "primary_category": d.cats.map(lambda c: c[0]),
        "rating": d.rating, "n_reviews": d.n_reviews.fillna(0), "google_rating": d.google_rating,
        "google_reviews": d.google_reviews.fillna(0), "ta_rating": d.ta_rating, "ta_reviews": d.ta_reviews,
        "ta_rank": d["TripAdvisor Rank"], "review_source": d["Review Source"],
        "price_level": d["Price"].map(_price_level), "hours": d["Hours"], "status_2021": d["Business Status"],
        "description": d["Description"], "amenities": d["Amenities / Other Info"], "url": d["Google Maps URL"],
    }).reset_index(drop=True)

    cat_lists = cat.categories.str.split(", ")
    cat["themes"] = cat_lists.map(lambda c: ", ".join(C.themes_for(c)))
    cat["cuisines"] = cat_lists.map(lambda c: ", ".join(C.cuisines_for(c)))
    tags = cat.amenities.map(_tags)
    has = lambda *names: tags.map(lambda t: any(n in t for n in names))  # noqa: E731
    cat["kids"] = has("Good for kids", "Family-friendly", "Kids' menu", "High chairs")
    cat["wheelchair"] = has("Wheelchair accessible entrance")
    cat["vegetarian"] = has("Vegetarian options", "Vegan options")
    cat["groups"] = has("Groups", "Good for groups")
    cat["alcohol"] = has("Alcohol", "Beer", "Wine", "Cocktails", "Full bar")
    cat["outdoor_seating"] = has("Outdoor seating")
    cat["serves_breakfast"] = has("Breakfast", "Brunch")
    cat["serves_lunch"] = has("Lunch")
    cat["serves_dinner"] = has("Dinner")
    cat["adults_only"] = cat.primary_category.isin({"Night club", "Bar", "Cocktail bar", "Casino", "Dive bar",
                                                     "Lounge", "Hookah bar", "Pub", "Wine bar"})
    # Extra amenity facts for the PDF feature list (atmosphere filter, reservations, menu info, discounts).
    def section(amen, key):
        if not isinstance(amen, str):
            return ""
        for sec in amen.split(" | "):
            k, _, v = sec.partition(": ")
            if k == key:
                return v
        return ""
    cat["atmosphere"] = cat.amenities.map(lambda a: section(a, "Atmosphere").replace("Cosy", "Cozy").replace("Trending", "Trendy"))
    cat["menu_highlights"] = cat.amenities.map(lambda a: ", ".join(x for x in [section(a, "Offerings"), section(a, "Highlights"), section(a, "Dining options")] if x))
    cat["accepts_reservations"] = has("Accepts reservations", "Dinner reservations recommended", "Lunch reservations recommended")
    cat["reservations_recommended"] = has("Dinner reservations recommended", "Lunch reservations recommended")
    cat["usually_wait"] = has("Usually a wait")
    cat["military_discount"] = has("Active military discounts")
    cat["live_music"] = has("Live music", "Live performances")
    cat["happy_hour"] = has("Happy hour food", "Happy hour drinks", "Happy-hour food", "Happy-hour drinks")
    cat["halal"] = has("Halal food")
    cat["wheelchair_parking"] = has("Wheelchair accessible parking lot", "Wheelchair-accessible car park")
    cat["wheelchair_restroom"] = has("Wheelchair accessible restroom", "Wheelchair-accessible toilet")
    cat["kids_only"] =cat.primary_category.str.contains(r"Children|Indoor playground|Playground", regex=True)
    # Some places (trailheads, peaks) have no city in the address: use the nearest city centroid.
    no_city = cat.city.eq("")
    if no_city.any():
        big = cent[cent.n >= 50]
        for i in cat.index[no_city]:
            d2 = (big.lat - cat.at[i, "lat"]) ** 2 + (big.lon - cat.at[i, "lon"]) ** 2
            cat.at[i, "city"] = big.city.iloc[int(d2.to_numpy().argmin())]
    cat["outdoor"] = cat.themes.map(lambda t: bool(set(t.split(", ")) & C.OUTDOOR_THEMES))
    cat["strenuous"] = cat_lists.map(lambda c: bool(set(c) & C.STRENUOUS_CATS))

    # Costs (USD per person). Restaurants: price level; attractions: category admission table.
    is_food = cat.kind == "restaurant"
    cat["cost_pp"] = np.where(
        is_food, [_meal_cost(l, c) for l, c in zip(cat.price_level, cat_lists)],
        np.where(cat.kind == "nightlife", 25.0, [_admission(c, t.split(", ") if t else []) for c, t in zip(cat_lists, cat.themes)]),
    )
    # Hotels: cost_pp holds the nightly ROOM rate; the Budget agent converts it to per person.
    lod = cat.kind == "lodging"
    cat.loc[lod, "cost_pp"] = [C.LODGING.get(c[0], 150.0) for c in cat_lists[lod]]
    cat["cost_known"] = cat.price_level.notna() & is_food

    # RAG evidence from the TripAdvisor review summaries and the hiking sheet.
    cat = _join_review_sheet(cat, sheets["TripAdvisor Reviews"], "Attraction", {
        "What Travelers Love": "ev_love", "Common Complaints": "ev_complaints", "Visitor Tip": "ev_tip"})
    hk = sheets["Hiking Places"].rename(columns={"What Hikers Love": "What Travelers Love",
                                                 "Watch Out For": "Common Complaints", "Hiker Tip": "Visitor Tip"})
    cat = _join_review_sheet(cat, hk, "Trail / Hiking Area", {
        "What Travelers Love": "ev_love", "Common Complaints": "ev_complaints", "Visitor Tip": "ev_tip",
        "Approx. Length": "hike_length", "Difficulty": "hike_difficulty"})

    cat = add_reviews(cat)

    # Ranking signals
    n, r = cat.n_reviews.astype(float), cat.rating.fillna(BAYES_PRIOR_MEAN)
    cat["bayes_rating"] = (n * r + BAYES_PRIOR_N * BAYES_PRIOR_MEAN) / (n + BAYES_PRIOR_N)
    cat["trust"] = ((cat.bayes_rating - 4.0) / 1.0).clip(0, 1)
    cat["popularity"] = (np.log1p(n) / np.log1p(10000)).clip(0, 1)
    cat["ta_bonus"] = cat.ta_rating.notna().astype(float)

    def doc(row) -> str:
        parts = [row["name"], ("hotel, place to stay overnight" if row.kind == "lodging" else row.kind) + f" in {row.city}", row.categories]
        for col in ("themes", "cuisines", "description", "ev_love", "hike_difficulty"):
            if isinstance(row.get(col), str) and row[col]:
                parts.append(row[col])
        flags = [lbl for col, lbl in [("kids", "good for kids family friendly"), ("outdoor", "outdoors"),
                                      ("vegetarian", "vegetarian options"), ("groups", "good for groups"),
                                      ("alcohol", "drinks"), ("outdoor_seating", "patio outdoor seating")] if row[col]]
        parts += flags
        return ". ".join(parts)

    cat["doc"] = cat.apply(doc, axis=1)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    cat.to_parquet(CATALOG_PATH, index=False)
    cent.to_parquet(CITY_CENTROIDS_PATH, index=False)
    return cat


if __name__ == "__main__":
    c = build_catalog()
    print(c.kind.value_counts().to_string())
    print("with RAG evidence:", c.ev_love.notna().sum())
