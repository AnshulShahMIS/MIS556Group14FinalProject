"""Method 1 - dense vector embeddings.

all-MiniLM-L6-v2 (Sentence-Transformers, CPU) turns every place's text and every traveler
request into a 384-d unit vector; cosine similarity = dot product. A TF-IDF + SVD fallback
(AZ_EMBEDDER=tfidf) keeps everything runnable offline and serves as an evaluation baseline.
"""
from __future__ import annotations

import json
import pickle

import numpy as np

from .config import EMBED_MODEL, EMBEDDER, EMBEDDER_INFO_PATH, EMBEDDINGS_PATH, PROCESSED_DIR


class MiniLMEmbedder:
    name = "minilm"

    def __init__(self):
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer(EMBED_MODEL, device="cpu")

    def fit(self, docs: list[str]) -> np.ndarray:
        return self.encode(docs, batch_size=128, show_progress_bar=True)

    def encode(self, texts: list[str], **kw) -> np.ndarray:
        v = self.model.encode(texts, normalize_embeddings=True, convert_to_numpy=True, **kw)
        return v.astype(np.float32)


class TfidfEmbedder:
    name = "tfidf"
    path = PROCESSED_DIR / "tfidf.pkl"

    def __init__(self):
        self.vec = self.svd = None
        if self.path.exists():
            self.vec, self.svd = pickle.loads(self.path.read_bytes())

    def fit(self, docs: list[str]) -> np.ndarray:
        from sklearn.decomposition import TruncatedSVD
        from sklearn.feature_extraction.text import TfidfVectorizer
        self.vec = TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True, stop_words="english")
        x = self.vec.fit_transform(docs)
        self.svd = TruncatedSVD(256, random_state=0).fit(x)
        self.path.write_bytes(pickle.dumps((self.vec, self.svd)))
        return self._norm(self.svd.transform(x))

    def encode(self, texts: list[str], **_) -> np.ndarray:
        return self._norm(self.svd.transform(self.vec.transform(texts)))

    @staticmethod
    def _norm(v: np.ndarray) -> np.ndarray:
        v = v / (np.linalg.norm(v, axis=1, keepdims=True) + 1e-9)
        return v.astype(np.float32)


def get_embedder(name: str | None = None):
    name = name or (json.loads(EMBEDDER_INFO_PATH.read_text())["name"] if EMBEDDER_INFO_PATH.exists() else EMBEDDER)
    return MiniLMEmbedder() if name == "minilm" else TfidfEmbedder()


def build_embeddings(docs: list[str], name: str = EMBEDDER) -> np.ndarray:
    emb = MiniLMEmbedder() if name == "minilm" else TfidfEmbedder()
    vecs = emb.fit(docs)
    np.save(EMBEDDINGS_PATH, vecs)
    EMBEDDER_INFO_PATH.write_text(json.dumps({"name": emb.name, "dim": int(vecs.shape[1]), "n": len(docs)}))
    return vecs
