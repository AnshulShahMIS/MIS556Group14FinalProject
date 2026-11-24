"""python -m azmulti.build  -> catalog.parquet + embeddings.npy (run once)."""
from __future__ import annotations

import sys
import time

from .config import EMBEDDER
from .embeddings import build_embeddings
from .pipeline import build_catalog


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else EMBEDDER
    t = time.time()
    cat = build_catalog()
    print(f"catalog: {len(cat):,} places  ({cat.kind.value_counts().to_dict()})")
    vecs = build_embeddings(cat["doc"].tolist(), name)
    print(f"embeddings: {vecs.shape} with {name}  in {time.time() - t:.0f}s")
    from .embeddings import get_embedder
    from .reviews_index import build as build_reviews
    n = build_reviews(cat, get_embedder(name))
    print(f"review index: {n:,} real review quotes embedded  in {time.time() - t:.0f}s")


if __name__ == "__main__":
    main()
