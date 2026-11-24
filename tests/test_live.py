"""Live-data agents tested with mocked API responses (shaped like the real Google Places,
Ticketmaster, Tavily and TomTom JSON) - no keys or internet needed."""
import os
import sys
from pathlib import Path

os.environ["AZ_OFFLINE"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

from azmulti import live, shared  # noqa: E402
from azmulti.graph import Planner  # noqa: E402

BRIEF = ("Plan a Saturday in Scottsdale for four friends. We're all 24, have a $125 budget per person, want a fun "
         "outdoor activity, Mexican food for dinner, and somewhere to go out afterward.")
CLOSED_NAME = {"name": None}


def fake_http(url, *, method="GET", headers=None, body=None, timeout=8.0):
    if "places.googleapis.com" in url:
        q = body["textQuery"]
        name = q.split(",")[0]
        c = body["locationBias"]["circle"]["center"]
        status = "CLOSED_PERMANENTLY" if CLOSED_NAME["name"] and name == CLOSED_NAME["name"] else "OPERATIONAL"
        return {"places": [{"displayName": {"text": name}, "businessStatus": status,
                            "location": {"latitude": c["latitude"], "longitude": c["longitude"]},
                            "regularOpeningHours": {"weekdayDescriptions": [
                                f"{d}: 6:00 AM – 11:30 PM" for d in
                                ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")]},
                            "priceLevel": "PRICE_LEVEL_MODERATE", "websiteUri": f"https://example.com/{name[:5]}",
                            "nationalPhoneNumber": "(480) 555-0100", "reservable": True,
                            "parkingOptions": {"freeParkingLot": True, "valetParking": True}}]}
    if "ticketmaster.com" in url:
        return {"_embedded": {"events": [{
            "name": "Phoenix Suns vs. Denver Nuggets", "url": "https://www.ticketmaster.com/event/X",
            "dates": {"start": {"localDate": "2026-11-28", "localTime": "19:00:00"}},
            "priceRanges": [{"min": 35.0, "max": 250.0}], "classifications": [{"segment": {"name": "Sports"}, "genre": {"name": "Basketball"}}],
            "_embedded": {"venues": [{"name": "Footprint Center", "city": {"name": "Phoenix"},
                                      "location": {"latitude": "33.4457", "longitude": "-112.0712"}}]}}]}}
    if "api.tavily.com" in url:
        return {"answer": "They serve street tacos, burritos and a weekend brunch menu.",
                "results": [{"title": "Menu - Example", "url": "https://example.com/menu", "content": "Street tacos $4, burritos $11..."}]}
    if "api.tomtom.com" in url:
        return {"routes": [{"summary": {"lengthInMeters": 12000, "travelTimeInSeconds": 1500, "trafficDelayInSeconds": 360}}]}
    raise AssertionError(f"unexpected URL {url}")


@pytest.fixture()
def live_on(monkeypatch, tmp_path):
    monkeypatch.setenv("AZ_OFFLINE", "0")
    monkeypatch.setenv("AZ_WEATHER", "off")
    for k in ("GOOGLE_PLACES_API_KEY", "TICKETMASTER_API_KEY", "TAVILY_API_KEY", "TOMTOM_API_KEY"):
        monkeypatch.setenv(k, "test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setattr(live, "http_json", fake_http)
    monkeypatch.setattr(live, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(shared, "SHARED_DIR", tmp_path / "shared")
    import azmulti.config as C
    monkeypatch.setattr(C, "USE_LLM", False)
    CLOSED_NAME["name"] = None
    yield


def stops(out):
    return [s for s in out["slots"] if s.get("place")]


def test_offline_has_no_live_calls():
    assert not any(live.status().values())
    out = Planner().ask(BRIEF)
    assert not any(t["agent"] == "Live Info" for t in out["trace"])


def test_places_live_hours_website_parking(live_on):
    out = Planner().ask(BRIEF)
    assert any(t["agent"] == "Live Info" for t in out["trace"])
    p = stops(out)[0]["place"]
    assert p["live"]["found"] and p["hours"].startswith("Monday: 6AM–11:30PM")
    assert "free lot" in p["live"]["parking"]
    assert "Official website" in stops(out)[0]["booking"]["links"]
    assert "Live check (Google" in out["answer"]


def test_closed_place_is_replaced(live_on):
    first = Planner().ask(BRIEF)
    victim = stops(first)[1]["place"]["name"]
    CLOSED_NAME["name"] = victim
    import shutil
    shutil.rmtree(live.CACHE_DIR, ignore_errors=True)  # the first run cached "open" for 24h
    out = Planner().ask(BRIEF)
    assert victim not in [s["place"]["name"] for s in stops(out)]
    assert any("permanently closed" in n for n in out["notes"])


def test_traffic_used_for_drive_times(live_on):
    out = Planner().ask(BRIEF)
    legs = [s for s in stops(out) if s.get("traffic")]
    assert legs and legs[0]["traffic"]["delay_min"] == 6 and legs[0]["drive_min"] == 25 + 5


def test_live_events_and_add_event(live_on):
    p = Planner()
    out = p.ask(BRIEF)
    assert any(e.get("source") == "Ticketmaster" for e in out["events"])
    ev = p.ask("what events are on that day?")
    assert "Suns" in ev["answer"] and "ticketmaster.com" in ev["answer"]
    added = p.ask("add the Suns game to my plan")
    assert any(s["label"].startswith("Event: Phoenix Suns") for s in stops(added))


def test_web_lookup(live_on):
    p = Planner()
    p.ask(BRIEF)
    out = p.ask("what's on the menu at dinner?")
    assert out["intent"] == "tools" and "street tacos" in out["answer"] and "example.com/menu" in out["answer"]


def test_lookup_offline_explains_key():
    p = Planner()
    p.ask(BRIEF)
    out = p.ask("what's on the menu at dinner?")
    assert "TAVILY_API_KEY" in out["answer"]


def test_shared_plan_collaboration(live_on):
    alice, bob = Planner(), Planner()
    alice.display_name = "Alice"
    alice.ask(BRIEF)
    code = alice.share("Alice")
    assert bob.join(code, "Bob")
    assert [s["place"]["id"] for s in stops(bob.state())] == [s["place"]["id"] for s in stops(alice.state())]
    bob.ask("swap dinner for something cheaper")          # Bob edits...
    assert alice.sync()                                   # ...Alice sees it
    assert stops(alice.state())[3]["place"]["id"] == stops(bob.state())[3]["place"]["id"]
    s = stops(alice.state())[1]
    alt = s["alternatives"][0]["id"]
    shared.vote(code, s["id"], alt, "Alice")
    shared.vote(code, s["id"], alt, "Bob")
    assert len(shared.load(code)["votes"][s["id"]][alt]) == 2
