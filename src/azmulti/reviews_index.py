"""Method 3 - retrieval-augmented generation over REAL review text.

Every real Google review quote in the data (about 57k, 2018-2021) is embedded with the same
all-MiniLM-L6-v2 model as the places (Method 1). At planning and question time the agents run a
top-k cosine-similarity search over those review vectors to:

* re-rank candidate places by how well what reviewers actually wrote matches the traveler's request, and
* ground each recommendation and answer in the retrieved review text (shown as a quote with its stars and year).

The file's AI-generated sample reviews are never indexed.
"""
from __future__ import annotations

import json
from functools import lru_cache

import numpy as np
import pandas as pd

from .config import PROCESSED_DIR

VEC_PATH = PROCESSED_DIR / "review_vecs.npy"
META_PATH = PROCESSED_DIR / "review_meta.parquet"


def _snippets(cat: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for i, raw in enumerate(cat["review_quotes"].fillna("[]")):
        try:
            quotes = json.loads(raw)
        except Exception:
            continue
        for q in quotes:
            t = " ".join(str(q.get("text") or "").split())
            if len(t) >= 15:
                rows.append({"place_idx": i, "stars": int(q.get("stars") or 0), "year": q.get("year"), "text": t[:400]})
    return pd.DataFrame(rows)


def build(cat: pd.DataFrame, embedder) -> int:
    """Embed every real review quote (run by `python -m azmulti.build`, or lazily on first use)."""
    meta = _snippets(cat)
    vecs = embedder.encode(meta["text"].tolist(), batch_size=256, show_progress_bar=True) if len(meta) else np.zeros((0, 384))
    np.save(VEC_PATH, vecs.astype(np.float16))  # half precision: ~42 MB on disk (fits GitHub), same ranking
    meta.to_parquet(META_PATH, index=False)
    return len(meta)


class ReviewIndex:
    def __init__(self, cat_df: pd.DataFrame, embed):
        self.embed = embed
        self.meta = pd.read_parquet(META_PATH)
        self.vecs = np.load(VEC_PATH).astype(np.float32)
        self.place_idx = self.meta["place_idx"].to_numpy()
        self.n_places = len(cat_df)
        order = np.argsort(self.place_idx, kind="stable")  # snippets grouped by place for fast per-place max
        self.meta, self.vecs, self.place_idx = self.meta.iloc[order].reset_index(drop=True), self.vecs[order], self.place_idx[order]
        self.starts = np.searchsorted(self.place_idx, np.arange(self.n_places))
        self.ends = np.searchsorted(self.place_idx, np.arange(self.n_places), side="right")

    def place_scores(self, qv: np.ndarray, place_rows: np.ndarray) -> np.ndarray:
        """Best review-to-query cosine similarity for each place (0 when a place has no reviews)."""
        best = np.zeros(self.n_places, dtype=np.float32)
        m = np.isin(self.place_idx, place_rows)
        if m.any():
            np.maximum.at(best, self.place_idx[m], self.vecs[m] @ qv)
        return best[place_rows]

    def top_k(self, query: str, place_rows: list[int] | None = None, k: int = 3, min_stars: int = 0) -> list[dict]:
        """Top-k review snippets most similar to the query, optionally only for some places."""
        qv = self.embed(query)
        if place_rows is not None:
            sel = np.concatenate([np.arange(self.starts[r], self.ends[r]) for r in place_rows] or [np.array([], int)]).astype(int)
        else:
            sel = np.arange(len(self.meta))
        if min_stars:
            sel = sel[self.meta["stars"].to_numpy()[sel] >= min_stars]
        if not len(sel):
            return []
        sims = self.vecs[sel] @ qv
        best = sel[np.argsort(-sims)[:k]]
        return [dict(self.meta.iloc[i].to_dict(), similarity=round(float(self.vecs[i] @ qv), 3)) for i in best]


@lru_cache(maxsize=1)
def get_index(cat) -> ReviewIndex | None:
    """The review index for this catalog, building it on first use if needed (about 2 minutes on CPU)."""
    try:
        if not (VEC_PATH.exists() and META_PATH.exists()):
            build(cat.df, cat.embedder)
        idx = ReviewIndex(cat.df, cat.embed)
        if idx.vecs.shape[1] != cat.vecs.shape[1]:  # a different embedder (e.g. the TF-IDF baseline): no review retrieval
            return None
        return idx
    except Exception:
        return None
