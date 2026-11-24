"""Weather & seasonal intelligence.

- Climate normals (average daily high, °F, by month) for Arizona's main regions, approximated
  from NOAA 1991-2020 normals. Used for any date.
- Sunrise / sunset from the NOAA solar-position equations (no internet needed).
- Optional live forecast from Open-Meteo (free, no key) when the trip is within 14 days.
  Only the city's coordinates are sent. Disabled when AZ_OFFLINE=1 or AZ_WEATHER=off.
"""
from __future__ import annotations

import json
import math
import os
import urllib.request
from datetime import date, datetime, timedelta
from functools import lru_cache

# (lat, lon, monthly avg highs Jan..Dec in °F)
REGIONS: dict[str, tuple[float, float, list[int]]] = {
    "Phoenix metro": (33.45, -112.07, [67, 71, 77, 85, 95, 104, 106, 105, 100, 89, 76, 66]),
    "Tucson": (32.22, -110.97, [66, 69, 75, 83, 92, 101, 100, 98, 95, 86, 74, 65]),
    "Flagstaff": (35.20, -111.65, [43, 45, 51, 58, 67, 79, 82, 79, 74, 63, 51, 43]),
    "Sedona": (34.87, -111.76, [57, 60, 66, 73, 83, 93, 96, 93, 88, 77, 64, 56]),
    "Prescott": (34.54, -112.47, [51, 54, 60, 67, 76, 87, 89, 87, 82, 71, 59, 50]),
    "Grand Canyon South Rim": (36.05, -112.14, [44, 46, 52, 60, 70, 81, 84, 81, 75, 64, 52, 44]),
    "Page": (36.91, -111.46, [46, 52, 61, 70, 80, 92, 97, 94, 86, 71, 56, 46]),
    "Yuma": (32.69, -114.62, [70, 74, 80, 87, 95, 104, 107, 106, 102, 91, 78, 68]),
    "Lake Havasu": (34.48, -114.32, [67, 71, 78, 87, 96, 106, 110, 109, 103, 90, 75, 65]),
}
COOL_ESCAPES = "Flagstaff, Sedona's higher trails at dawn, Prescott or the Grand Canyon's South Rim"


def region_for(lat: float, lon: float) -> str:
    return min(REGIONS, key=lambda r: (REGIONS[r][0] - lat) ** 2 + (REGIONS[r][1] - lon) ** 2)


def normal_high(lat: float, lon: float, month: int) -> int:
    return REGIONS[region_for(lat, lon)][2][month - 1]


def sun_times(lat: float, lon: float, d: date, utc_offset: float = -7.0) -> tuple[int, int]:
    """(sunrise, sunset) in minutes after local midnight. NOAA general solar position equations."""
    n = d.timetuple().tm_yday
    g = 2 * math.pi / 365 * (n - 1)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                       - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    lat_r = math.radians(lat)
    cos_ha = (math.cos(math.radians(90.833)) / (math.cos(lat_r) * math.cos(decl)) - math.tan(lat_r) * math.tan(decl))
    ha = math.degrees(math.acos(max(-1.0, min(1.0, cos_ha))))
    sunrise_utc = 720 - 4 * (lon + ha) - eqtime
    sunset_utc = 720 - 4 * (lon - ha) - eqtime
    return int(round(sunrise_utc + utc_offset * 60)), int(round(sunset_utc + utc_offset * 60))


def _live_enabled() -> bool:
    return os.getenv("AZ_OFFLINE", "0") != "1" and os.getenv("AZ_WEATHER", "on") != "off"


@lru_cache(maxsize=64)
def live_forecast(lat: float, lon: float, day: str) -> dict | None:
    if not _live_enabled():
        return None
    url = ("https://api.open-meteo.com/v1/forecast?"
           f"latitude={lat:.3f}&longitude={lon:.3f}&daily=temperature_2m_max,temperature_2m_min,"
           "precipitation_probability_max,weathercode&temperature_unit=fahrenheit&timezone=America%2FPhoenix"
           f"&start_date={day}&end_date={day}")
    try:
        with urllib.request.urlopen(url, timeout=4) as r:
            d = json.loads(r.read().decode())["daily"]
        return {"high": round(d["temperature_2m_max"][0]), "low": round(d["temperature_2m_min"][0]),
                "rain_pct": d["precipitation_probability_max"][0] or 0, "code": d["weathercode"][0]}
    except Exception:
        return None


def trip_date(weekday: int | None, month: int | None, today: date | None = None) -> date:
    """Next occurrence of the weekday (in the requested month if one is given)."""
    today = today or datetime.now().date()
    if month and month != today.month:
        year = today.year if month > today.month else today.year + 1
        d = date(year, month, 1)
    else:
        d = today
    if weekday is not None:
        d += timedelta(days=(weekday - d.weekday()) % 7)
    return d


def conditions(lat: float, lon: float, weekday: int | None, month: int | None, days: int = 1,
               exact: str | None = None) -> dict:
    d = date.fromisoformat(exact) if exact else trip_date(weekday, month)
    region = region_for(lat, lon)
    sunrise, sunset = sun_times(lat, lon, d)
    fc = live_forecast(round(lat, 2), round(lon, 2), d.isoformat()) if 0 <= (d - datetime.now().date()).days <= 14 else None
    high = fc["high"] if fc else normal_high(lat, lon, d.month)
    rain = fc["rain_pct"] if fc else 0
    out = {"date": d.isoformat(), "region": region, "high_f": high, "low_f": fc["low"] if fc else None, "rain_pct": rain,
           "source": "Open-Meteo forecast" if fc else f"typical {d:%B} high for {region} (climate normal)",
           "sunrise": sunrise, "sunset": sunset, "hot": high >= 98, "extreme_heat": high >= 108,
           "cold": high < 50, "rainy": rain >= 50, "alerts": [], "month": d.month}
    if out["extreme_heat"]:
        out["alerts"].append(f"Extreme heat ({high}°F): outdoor stops only around sunrise; carry 1 gallon of water per person on trails.")
    elif out["hot"]:
        out["alerts"].append(f"Hot day ({high}°F): outdoor stops moved early, afternoon kept indoors. Cooler escapes: {COOL_ESCAPES}.")
    if out["cold"]:
        out["alerts"].append(f"Cold ({high}°F high): dress in layers; trails at elevation may be icy or snowy.")
    if out["rainy"]:
        out["alerts"].append(f"{rain}% chance of rain: outdoor stops swapped for indoor options; watch for flash floods in washes and slot canyons.")
    return out
