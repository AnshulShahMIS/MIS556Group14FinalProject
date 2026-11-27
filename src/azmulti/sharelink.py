"""View-only share links: anyone with the link can see a trip, without an account; nobody can change it.

- The link carries a long random token (unguessable); the trip's snapshot is stored as shared-trips/<token>.json in the
  same store as accounts (store.py). The owner's later changes to the trip update the snapshot (graph.save_trip), and
  "Stop sharing" deletes it, so the link stops working.
- What's shared: the plan itself (days, times, places, costs, route, weather, events, budget) and the owner's
  username. Not shared: the chat, preferences, favorites, checklist ticks, or reservation confirmation numbers.
"""
from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone

from .store import get_store

TOKEN_RX = re.compile(r"^[A-Za-z0-9_-]{20,64}$")
VIEW_KEYS = ("request", "slots", "conditions", "budget", "route", "booking", "events", "reservations", "rental")
REQUEST_KEYS = ("city", "legs", "days", "party_size", "transport", "trip_date", "weekday")


def _key(token: str) -> str:
    if not TOKEN_RX.match(token or ""):
        raise ValueError("bad share token")
    return f"shared-trips/{token}.json"


def new_token() -> str:
    return secrets.token_urlsafe(18)  # 24 characters


def snapshot(state: dict, trip: dict, owner: str) -> dict:
    view = {k: state.get(k) for k in VIEW_KEYS if state.get(k) is not None}
    view["reservations"] = [{k: v for k, v in r.items() if k not in ("confirmation", "links")}
                            for r in view.get("reservations") or []]
    # only the trip's basics from the request (not diet, accessibility or other preferences)
    view["request"] = {k: v for k, v in (view.get("request") or {}).items() if k in REQUEST_KEYS}
    return {"title": trip.get("city") or (state.get("request") or {}).get("city") or "Arizona trip",
            "when": trip.get("when") or "", "days": trip.get("days", 1), "party": trip.get("party", 2),
            "owner": owner, "updated": datetime.now(timezone.utc).isoformat(timespec="minutes"), "state": view}


def publish(token: str, state: dict, trip: dict, owner: str) -> None:
    get_store().put(_key(token), snapshot(state, trip, owner))


def load(token: str | None) -> dict | None:
    if not token or not TOKEN_RX.match(token):
        return None
    data = get_store().get(_key(token))
    return data if isinstance(data, dict) and data.get("state") else None


def revoke(token: str | None) -> None:
    if token and TOKEN_RX.match(token):
        get_store().delete(_key(token))
