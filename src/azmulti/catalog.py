"""The retrieval layer every specialist agent calls: semantic search + hard filters + ranking.

score = W_SIM*cosine + W_TRUST*trust + W_POP*popularity - W_DIST*(distance/radius) + W_TA*on_TripAdvisor
(hidden-gem mode flips the popularity term so less-reviewed, well-rated places rise.)
"""
from __future__ import annotations

import difflib
import math
from functools import lru_cache

import numpy as np
import pandas as pd

from . import hours as H
from .config import (QUIET_SINCE, CATALOG_PATH, CITY_CENTROIDS_PATH, EMBEDDINGS_PATH, W_DIST, W_POP, W_REV, W_SIM, W_TA,
                     W_TRUST)
from .embeddings import get_embedder


def haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * np.arcsin(np.sqrt(a))


class Catalog:
    def __init__(self, df: pd.DataFrame | None = None, vecs: np.ndarray | None = None, embedder=None):
        self.df = df if df is not None else pd.read_parquet(CATALOG_PATH)
        self.vecs = vecs if vecs is not None else np.load(EMBEDDINGS_PATH)
        assert len(self.df) == len(self.vecs), "catalog and embeddings out of sync - rerun azmulti.build"
        self._embedder = embedder
        self.centroids = pd.read_parquet(CITY_CENTROIDS_PATH) if CITY_CENTROIDS_PATH.exists() else \
            self.df.groupby("city").agg(lat=("lat", "median"), lon=("lon", "median")).reset_index()
        self._sched = {}
        if "not_a_destination" not in self.df:
            self.df["not_a_destination"] = (
                self.df.name.str.contains(r"tourism office|visitors? bureau|chamber of commerce|convention|welcome center|information center",
                                          case=False, regex=True)
                | self.df.primary_category.isin(["Tourist information center", "Visitor center"]) & (self.df.n_reviews < 200))
        if "kids_only" not in self.df:
            self.df["kids_only"] = False
        # Stadiums / arenas / event venues open for events, so their posted hours don't apply
        self.df["event_venue"] = self.df.categories.fillna("").str.contains(
            r"Stadium|Arena|Amphitheater|Baseball field|Event venue|Concert hall|Performing arts theater|Rodeo", regex=True)
        # Never recommend places that are closed for good: permanently / temporarily closed in Google's 2021 data,
        # or no reviews for 2+ years before the data was collected (closed since). Live Google status (with a key)
        # is checked again for every stop by the Live Info agent.
        status = self.df["status_2021"].fillna("") if "status_2021" in self.df else pd.Series("", index=self.df.index)
        last = pd.to_datetime(self.df["last_review"], errors="coerce") if "last_review" in self.df else pd.Series(pd.NaT, index=self.df.index)
        quiet = (last < pd.Timestamp(QUIET_SINCE)).fillna(False)
        reason = pd.Series("", index=self.df.index, dtype=object)
        reason[quiet] = "no reviews since " + last[quiet].dt.strftime("%B %Y") + ", so it has most likely closed"
        reason[status.eq("Temporarily closed")] = "it was marked temporarily closed on Google (2021 data)"
        reason[status.eq("Permanently closed")] = "Google lists it as permanently closed"
        try:  # places travelers reported closed since the snapshot
            from .closed_reports import load as _reports
            for pid, rep in _reports().items():
                reason[self.df["id"].eq(pid)] = f"a traveler reported it permanently closed on {rep.get('reported', '')}"
        except Exception:
            pass
        self.df["closed_reason"] = reason
        self.df["likely_closed"] = reason.ne("")
        self.cities = sorted(self.centroids.city.dropna().unique(), key=len, reverse=True)

    # ---------- helpers ----------
    @property
    def embedder(self):
        if self._embedder is None:
            self._embedder = get_embedder()
        return self._embedder

    def embed(self, text: str) -> np.ndarray:
        return _cached_query(self.embedder, text)

    def locate(self, city: str | None) -> tuple[float, float] | None:
        if not city:
            return None
        names = self.centroids.city.tolist()
        hit = difflib.get_close_matches(city.strip().title(), names, n=1, cutoff=0.8)
        if not hit:
            return None
        r = self.centroids[self.centroids.city == hit[0]].iloc[0]
        return float(r.lat), float(r.lon)

    def locate_zip(self, zip_code: str | None) -> tuple[float, float] | None:
        if not zip_code:
            return None
        sub = self.df[self.df["zip"] == str(zip_code)]
        if len(sub) < 3:
            return None
        return float(sub.lat.median()), float(sub.lon.median())

    def city_for_zip(self, zip_code: str | None) -> str | None:
        sub = self.df[self.df["zip"] == str(zip_code)] if zip_code else self.df.iloc[:0]
        return None if sub.empty or sub.city.eq("").all() else sub.city[sub.city != ""].mode().iloc[0]

    def center_for(self, req: dict) -> tuple[float, float]:
        return self.locate_zip(req.get("zip_code")) or self.locate(req.get("city")) or self.locate("Phoenix")

    def canonical_city(self, city: str | None) -> str | None:
        if not city:
            return None
        hit = difflib.get_close_matches(city.strip().title(), self.centroids.city.tolist(), n=1, cutoff=0.8)
        return hit[0] if hit else None

    def schedule(self, idx: int):
        if idx not in self._sched:
            self._sched[idx] = H.parse_hours(self.df.at[idx, "hours"])
        return self._sched[idx]

    def find_by_name(self, name: str, center: tuple[float, float] | None = None) -> dict | None:
        """A place the user named ("reviews of Camp Social"): exact name first, then a name starting with it."""
        import re as _re
        full = " ".join((name or "").lower().strip(" ?.!").split())
        names = self.df["name"].fillna("").str.lower()
        for n in dict.fromkeys([full, _re.sub(r"\s+(in|near|at|around)\s+[a-z .]+$", "", full)]):
            if len(n) < 3:
                continue
            for mask in (names.eq(n), names.str.startswith(n + " ") | names.str.startswith(n + ",")):
                sub = self.df[mask]
                if len(sub):
                    if center is not None:
                        d = haversine_km(center[0], center[1], sub.lat.to_numpy(), sub.lon.to_numpy())
                        sub = sub[d <= 80] if (d <= 80).any() else sub
                    return self.record(sub.n_reviews.idxmax())
        return None

    def mark_closed(self, place_id: str, reason: str) -> None:
        hit = self.df["id"].eq(place_id)
        self.df.loc[hit, "closed_reason"] = reason
        self.df.loc[hit, "likely_closed"] = True

    def by_id(self, place_id: str | None) -> dict | None:
        if not place_id:
            return None
        if not hasattr(self, "_id_index"):
            self._id_index = {v: i for i, v in zip(self.df.index, self.df.id)}
        i = self._id_index.get(place_id)
        return None if i is None else self.record(i)

    def record(self, idx: int) -> dict:
        r = self.df.loc[idx].to_dict()
        r["_idx"] = int(idx)
        return {k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in r.items()}

    # ---------- the search tool ----------
    def search(self, query: str, *, kind: str | list[str] | None = None, center: tuple[float, float] | None = None,
               radius_km: float = 30.0, k: int = 8, themes: list[str] | None = None,
               cuisines: list[str] | None = None, require: list[str] | None = None,
               avoid: list[str] | None = None, max_cost: float | None = None,
               open_at: tuple[int, int, int] | None = None, exclude: set[str] | None = None,
               hidden_gems: bool = False, min_reviews: int = 0, min_rating: float | None = None,
               min_cost: float | None = None, reviews: bool = True, include_closed: bool = False) -> list[dict]:
        df = self.df
        m = np.ones(len(df), dtype=bool) if include_closed else ~df["likely_closed"].to_numpy(dtype=bool)
        if kind:
            m &= df.kind.isin([kind] if isinstance(kind, str) else kind).to_numpy()
        if themes:
            m &= df.themes.fillna("").map(lambda t: any(x in t for x in themes)).to_numpy()
        if cuisines:
            m &= df.cuisines.fillna("").map(lambda t: any(x in t for x in cuisines)).to_numpy()
        for col in require or []:
            m &= df[col].fillna(False).to_numpy(dtype=bool)
        for col in avoid or []:
            m &= ~df[col].fillna(False).to_numpy(dtype=bool)
        if max_cost is not None:
            m &= (df.cost_pp <= max_cost).to_numpy()
        if min_cost is not None:
            m &= (df.cost_pp >= min_cost).to_numpy()
        if min_rating:
            m &= (df.rating.fillna(0) >= min_rating).to_numpy()
        if min_reviews:
            m &= (df.n_reviews >= min_reviews).to_numpy()
        if exclude:
            m &= ~df.id.isin(exclude).to_numpy()
        dist = np.zeros(len(df))
        if center:
            dist = haversine_km(center[0], center[1], df.lat.to_numpy(), df.lon.to_numpy())
            m &= dist <= radius_km
        idx = np.flatnonzero(m)
        if len(idx) == 0:
            return []

        qv = self.embed(query)
        sim = self.vecs[idx] @ qv
        rix = None
        if reviews and W_REV:
            from .reviews_index import get_index  # Method 3: top-k retrieval over real review text
            rix = get_index(self)
        rev = rix.place_scores(qv, idx) if rix is not None else np.zeros(len(idx), dtype=np.float32)
        pop = df.popularity.to_numpy()[idx]
        score = (W_SIM * sim + W_TRUST * df.trust.to_numpy()[idx]
                 + (W_POP * (0.5 - pop) * 2 if hidden_gems else W_POP * pop)
                 + W_TA * df.ta_bonus.to_numpy()[idx]
                 + W_REV * rev)
        if center:
            score -= W_DIST * dist[idx] / max(radius_km, 1)

        order = idx[np.argsort(-score)]
        sims = dict(zip(idx, sim))
        revs = dict(zip(idx, rev))
        scores = dict(zip(idx, score))
        out = []
        venue = df.event_venue.to_numpy()
        for i in order:
            open_ok = None
            if open_at and not venue[i]:
                open_ok = H.open_for(self.schedule(i), *open_at)
                if open_ok is False:
                    continue  # closed during the planned visit
            rec = self.record(i)
            rec.update(similarity=round(float(sims[i]), 3), score=round(float(scores[i]) - (0.03 if open_ok is None and open_at else 0), 3),
                       distance_km=round(float(dist[i]), 1) if center else None, open_ok=open_ok,
                       review_match=round(float(revs[i]), 3))
            if rix is not None:  # ground the pick in the real review that best matches the request
                ev = rix.top_k(query, [int(i)], k=1, min_stars=4) or rix.top_k(query, [int(i)], k=1)
                rec["review_evidence"] = ev[0] if ev else None
            out.append(rec)
            if len(out) >= k:
                break
        out.sort(key=lambda r: -r["score"])
        return out


@lru_cache(maxsize=512)
def _cached_query_inner(embedder_id: int, text: str):
    return _EMB[embedder_id].encode([text])[0]


_EMB: dict[int, object] = {}


def _cached_query(embedder, text: str) -> np.ndarray:
    _EMB[id(embedder)] = embedder
    return _cached_query_inner(id(embedder), text)


_CATALOG: Catalog | None = None


def get_catalog() -> Catalog:
    global _CATALOG
    if _CATALOG is None:
        _CATALOG = Catalog()
    return _CATALOG
