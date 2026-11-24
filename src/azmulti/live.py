"""Live data providers. Each one switches on only when its key is in .env, caches answers on disk,
and returns None on any error so the agents fall back to the workbook data.

  GOOGLE_PLACES_API_KEY  Google Places API (New): current hours, open/closed status, price level,
                         website, phone, parking options, "takes reservations"
  TICKETMASTER_API_KEY   Ticketmaster Discovery API v2: real concerts, games and shows by date + place
  TAVILY_API_KEY         Tavily web search: menus, discounts, official-site summaries (with sources)
  TOMTOM_API_KEY         TomTom Routing API: drive times with live / predicted traffic

Privacy: only place names, addresses, coordinates, dates and search phrases are sent - never anything
about the traveler. AZ_OFFLINE=1 turns every provider off (tests, demos without internet).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from .config import DATA_DIR
from .security import redact, safe_url

log = logging.getLogger(__name__)
CACHE_DIR = DATA_DIR / "cache"
TTL = {"places": 24 * 3600, "events": 6 * 3600, "search": 24 * 3600, "route": 3600}
AZ_TZ = timezone(timedelta(hours=-7))  # Arizona: UTC-7 all year

# ------------------------------------------------------------------ plumbing


def _key(name: str) -> str | None:
    if os.getenv("AZ_OFFLINE", "0") == "1":
        return None
    v = os.getenv(name, "").strip()
    return v or None


def status() -> dict[str, bool]:
    return {"Google Places": bool(_key("GOOGLE_PLACES_API_KEY")), "Ticketmaster": bool(_key("TICKETMASTER_API_KEY")),
            "Web search (Tavily)": bool(_key("TAVILY_API_KEY")), "Traffic (TomTom)": bool(_key("TOMTOM_API_KEY"))}


def _cache_get(kind: str, ident: str):
    p = CACHE_DIR / kind / (hashlib.sha1(ident.encode()).hexdigest() + ".json")
    if p.exists() and time.time() - p.stat().st_mtime < TTL[kind]:
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def _cache_put(kind: str, ident: str, value) -> None:
    p = CACHE_DIR / kind / (hashlib.sha1(ident.encode()).hexdigest() + ".json")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(value), encoding="utf-8")


def http_json(url: str, *, method: str = "GET", headers: dict | None = None, body: dict | None = None, timeout: float = 8.0):
    """Small JSON HTTP helper (stdlib only). Tests monkeypatch this function."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _safe(kind: str, ident: str, fetch):
    hit = _cache_get(kind, ident)
    if hit is not None:
        return hit
    try:
        val = fetch()
    except urllib.error.HTTPError as e:
        log.warning("%s HTTP %s: %s", kind, e.code, redact(e.read()[:200] if hasattr(e, "read") else e))
        return None
    except Exception as e:  # timeouts, DNS, bad JSON
        log.warning("%s failed: %s", kind, redact(e))
        return None
    if val is not None:
        _cache_put(kind, ident, val)
    return val


def checked_now() -> str:
    return datetime.now(AZ_TZ).strftime("%b %d, %Y %I:%M %p")


# ------------------------------------------------------------------ Google Places (New)
PLACES_FIELDS = ",".join("places." + f for f in (
    "id", "displayName", "formattedAddress", "businessStatus", "regularOpeningHours", "currentOpeningHours",
    "priceLevel", "websiteUri", "nationalPhoneNumber", "parkingOptions", "reservable", "rating", "userRatingCount",
    "googleMapsUri", "location", "editorialSummary", "servesVegetarianFood", "reviews"))
PRICE = {"PRICE_LEVEL_FREE": 0, "PRICE_LEVEL_INEXPENSIVE": 1, "PRICE_LEVEL_MODERATE": 2,
         "PRICE_LEVEL_EXPENSIVE": 3, "PRICE_LEVEL_VERY_EXPENSIVE": 4}
PARKING = {"freeParkingLot": "free lot", "paidParkingLot": "paid lot", "freeStreetParking": "free street",
           "paidStreetParking": "paid street", "valetParking": "valet", "freeGarageParking": "free garage",
           "paidGarageParking": "paid garage"}


def _clean_hours(lines: list[str]) -> str:
    """Google's weekday descriptions -> the workbook's 'Monday: 9AM–5PM; ...' format the agents parse."""
    out = []
    for ln in lines:
        ln = re.sub(r"[   ]", " ", ln)
        ln = re.sub(r":00\s*(AM|PM)", r"\1", ln)
        ln = re.sub(r"\s*(AM|PM)", r"\1", ln)
        ln = re.sub(r"\s*[–—-]\s*", "–", ln.split(": ", 1)[1]) if ": " in ln else ln
        day = lines[len(out)].split(":", 1)[0].strip()
        out.append(f"{day}: {ln}")
    return "; ".join(out)


def place_details(name: str, address: str | None, lat: float, lon: float) -> dict | None:
    """Current facts for one place (matched by name + location)."""
    key = _key("GOOGLE_PLACES_API_KEY")
    if not key:
        return None

    def fetch():
        res = http_json("https://places.googleapis.com/v1/places:searchText", method="POST",
                        headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": PLACES_FIELDS},
                        body={"textQuery": f"{name}, {address or ''}".strip(", "), "maxResultCount": 1,
                              "locationBias": {"circle": {"center": {"latitude": lat, "longitude": lon}, "radius": 800.0}}})
        places = res.get("places") or []
        if not places:
            return {"found": False}
        g = places[0]
        loc = g.get("location") or {}
        # Reject a match that is far from where our record says the place is (wrong business).
        if loc and abs(loc.get("latitude", lat) - lat) + abs(loc.get("longitude", lon) - lon) > 0.03:
            return {"found": False}
        hours = (g.get("regularOpeningHours") or {}).get("weekdayDescriptions")
        return {"found": True, "name": (g.get("displayName") or {}).get("text"), "status": g.get("businessStatus"),
                "hours": _clean_hours(hours) if hours else None,
                "open_now": (g.get("currentOpeningHours") or {}).get("openNow"),
                "price_level": PRICE.get(g.get("priceLevel")), "website": safe_url(g.get("websiteUri")),
                "phone": g.get("nationalPhoneNumber"),
                "parking": [v for k, v in PARKING.items() if (g.get("parkingOptions") or {}).get(k)],
                "reservable": g.get("reservable"), "rating": g.get("rating"), "reviews": g.get("userRatingCount"),
                "maps_url": safe_url(g.get("googleMapsUri")), "summary": (g.get("editorialSummary") or {}).get("text"),
                "reviews": [{"stars": r.get("rating"), "when": r.get("relativePublishTimeDescription"),
                             "text": ((r.get("text") or {}).get("text") or "")[:500],
                             "author": ((r.get("authorAttribution") or {}).get("displayName") or "")[:40]}
                            for r in (g.get("reviews") or [])[:5] if (r.get("text") or {}).get("text")],
                "checked": checked_now()}
    return _safe("places", f"{name}|{lat:.4f}|{lon:.4f}", fetch)


# ------------------------------------------------------------------ Ticketmaster
def events(lat: float, lon: float, start: datetime, end: datetime, radius_mi: int = 30, keyword: str | None = None,
           size: int = 12) -> list[dict] | None:
    key = _key("TICKETMASTER_API_KEY")
    if not key:
        return None
    q = {"apikey": key, "latlong": f"{lat:.4f},{lon:.4f}", "radius": radius_mi, "unit": "miles", "size": size,
         "sort": "date,asc", "startDateTime": start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
         "endDateTime": end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    if keyword:
        q["keyword"] = keyword

    def fetch():
        res = http_json("https://app.ticketmaster.com/discovery/v2/events.json?" + urllib.parse.urlencode(q))
        out = []
        for e in (res.get("_embedded") or {}).get("events", []):
            v = ((e.get("_embedded") or {}).get("venues") or [{}])[0]
            st = (e.get("dates") or {}).get("start") or {}
            pr = (e.get("priceRanges") or [{}])[0]
            cls = (e.get("classifications") or [{}])[0]
            out.append({"name": (e.get("name") or "")[:150], "url": safe_url(e.get("url")), "date": st.get("localDate"), "time": st.get("localTime"),
                        "venue": v.get("name"), "city": (v.get("city") or {}).get("name"),
                        "lat": float((v.get("location") or {}).get("latitude") or 0) or None,
                        "lon": float((v.get("location") or {}).get("longitude") or 0) or None,
                        "price_min": pr.get("min"), "price_max": pr.get("max"),
                        "segment": (cls.get("segment") or {}).get("name"), "genre": (cls.get("genre") or {}).get("name"),
                        "source": "Ticketmaster", "checked": checked_now()})
        return out
    return _safe("events", json.dumps({k: v for k, v in q.items() if k != "apikey"}, sort_keys=True), fetch)


# ------------------------------------------------------------------ Tavily web search
def web_search(query: str, max_results: int = 4) -> dict | None:
    key = _key("TAVILY_API_KEY")
    if not key:
        return None

    def fetch():
        res = http_json("https://api.tavily.com/search", method="POST", headers={"Authorization": f"Bearer {key}"},
                        body={"query": query, "max_results": max_results, "include_answer": True, "search_depth": "basic"})
        return {"answer": res.get("answer"), "checked": checked_now(),
                "results": [{"title": (r.get("title") or "")[:200], "url": safe_url(r.get("url")), "content": (r.get("content") or "")[:600]}
                            for r in res.get("results", [])]}
    return _safe("search", query, fetch)


# ------------------------------------------------------------------ TomTom traffic routing
def traffic_route(lat1: float, lon1: float, lat2: float, lon2: float, depart: datetime | None) -> dict | None:
    """Drive time with traffic. `depart` in Arizona time; past/now -> live traffic, future -> predicted."""
    key = _key("TOMTOM_API_KEY")
    if not key:
        return None
    now = datetime.now(AZ_TZ)
    when = "now" if not depart or depart <= now else depart.strftime("%Y-%m-%dT%H:%M:%S-07:00")
    q = {"key": key, "traffic": "true", "travelMode": "car", "departAt": when, "routeType": "fastest"}
    url = (f"https://api.tomtom.com/routing/1/calculateRoute/{lat1:.5f},{lon1:.5f}:{lat2:.5f},{lon2:.5f}/json?"
           + urllib.parse.urlencode(q))

    def fetch():
        s = (http_json(url).get("routes") or [{}])[0].get("summary") or {}
        if not s:
            return None
        return {"km": round(s.get("lengthInMeters", 0) / 1000, 1), "minutes": round(s.get("travelTimeInSeconds", 0) / 60),
                "delay_min": round(s.get("trafficDelayInSeconds", 0) / 60), "checked": checked_now(), "source": "TomTom"}
    ident = f"{lat1:.4f},{lon1:.4f}>{lat2:.4f},{lon2:.4f}@{when[:13]}"
    return _safe("route", ident, fetch)
