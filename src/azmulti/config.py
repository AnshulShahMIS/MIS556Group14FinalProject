"""Paths, model names and tunable constants in one place."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"

# The team's Excel workbook. Override with AZ_XLSX=... if it lives elsewhere.
XLSX_PATH = Path(os.getenv("AZ_XLSX", RAW_DIR / "Arizona_Places_TripAdvisor_Hiking.xlsx"))

CATALOG_PATH = PROCESSED_DIR / "catalog.parquet"
EMBEDDINGS_PATH = PROCESSED_DIR / "embeddings.npy"
EMBEDDER_INFO_PATH = PROCESSED_DIR / "embedder.json"
CITY_CENTROIDS_PATH = PROCESSED_DIR / "city_centroids.parquet"

# Method 1: dense embeddings. "tfidf" switches to an offline fallback (no model download).
EMBEDDER = os.getenv("AZ_EMBEDDER", "minilm")
EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

# Methods 2 + 4: the LLM. Without an API key every agent falls back to deterministic rules,
# so the whole graph still runs (useful for tests and for demos without a key).
OPENAI_MODEL = os.getenv("AZ_LLM_MODEL", "gpt-4o")
USE_LLM = bool(os.getenv("OPENAI_API_KEY")) and os.getenv("AZ_OFFLINE", "0") != "1"

TIMEZONE = "America/Phoenix"  # Arizona does not observe daylight saving time

# Ranking (from the midterm EDA: Bayesian prior of 38 reviews = the median review count)
BAYES_PRIOR_N = 38
BAYES_PRIOR_MEAN = 4.28
W_SIM, W_TRUST, W_POP, W_DIST, W_TA = 1.0, 0.15, 0.05, 0.10, 0.08
# Places whose newest review is older than this are treated as likely closed (the data runs to late 2021;
# a place with 2+ years of silence before that has almost always shut down, e.g. Camp Social, BLK Live).
QUIET_SINCE = os.getenv("AZ_QUIET_SINCE", "2020-01-01")
W_REV = float(os.getenv("AZ_W_REV", "0.5"))  # Method 3: weight of the best matching real review (0 turns review retrieval off)

# Routing assumptions (no live traffic API): straight-line distance x road factor.
ROAD_FACTOR = 1.3
CITY_SPEED_KMH = 40
HIGHWAY_SPEED_KMH = 95
PARKING_MIN = 5

MAX_REPLANS = 2
