"""Places travelers reported as closed for good.

The workbook is a 2021 snapshot, so a place that shut down after that still looks open (with a Google
Places key the Live Info agent checks every stop's current status instead). When a traveler says a stop is
permanently closed - in chat or with the "Closed? Replace it" button - the place is written here and never
recommended again by any agent, for anyone using this app. Only the place id, name, city and date are stored.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from datetime import date

from .config import PROCESSED_DIR

PATH = Path(os.getenv("AZ_CLOSED_REPORTS") or PROCESSED_DIR.parent / "closed_reports.json")
_LOCK = threading.Lock()


def load() -> dict[str, dict]:
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
        return {k: v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}
    except Exception:
        return {}


def report(place: dict) -> bool:
    """Remember a place as closed for good and drop it from the live catalog right away."""
    pid = str(place.get("id") or "")[:120]
    if not pid:
        return False
    with _LOCK:
        data = load()
        data[pid] = {"name": str(place.get("name") or "")[:120], "city": str(place.get("city") or "")[:60],
                     "reported": date.today().isoformat()}
        PATH.parent.mkdir(parents=True, exist_ok=True)
        PATH.write_text(json.dumps(data, indent=1), encoding="utf-8")
    from .catalog import get_catalog
    get_catalog().mark_closed(pid, f"a traveler reported it permanently closed on {date.today():%b %d, %Y}")
    return True
