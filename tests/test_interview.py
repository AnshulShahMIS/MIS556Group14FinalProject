"""Guided questions from the four starters, hotels, and reviews (offline)."""
import os
import sys
from pathlib import Path

os.environ["AZ_OFFLINE"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

from azmulti.graph import Planner  # noqa: E402

FAMILY = ["Family day", "Scottsdale", "This Saturday", "2 adults, 2 kids, School-age kids",
          "Activities, Lunch, Dinner, Hotel stay", "Animals & zoos, Fun & games", "Mexican, $50-100 each",
          "Kid-friendly everything", "Plan it"]


def stops(out):
    return [s for s in out["slots"] if s.get("place")]


@pytest.mark.parametrize("starter", ["Friends' day out", "Family day", "Date night", "Game day", "help me plan"])
def test_starters_ask_questions_instead_of_planning(starter):
    out = Planner().ask(starter)
    assert out["intent"] == "chat" and not out.get("slots")
    q = out["interview"]["q"]
    assert q["key"] == "where" and "Phoenix" in q["options"]


def test_family_interview_builds_matching_plan():
    p = Planner()
    for msg in FAMILY[:-1]:
        out = p.ask(msg)
        assert out["intent"] == "chat" and out["interview"]["active"]
    assert "Here's what I've got" in out["answer"] and "Hotel stay" in out["answer"]
    out = p.ask("Plan it")
    r = out["request"]
    assert out["intent"] == "new_plan" and not out.get("interview")
    assert (r["city"], r["weekday"], r["party_size"], r["has_kids"], r["need_hotel"]) == ("Scottsdale", 5, 4, True, True)
    kinds = [s["kind"] for s in stops(out)]
    assert "lodging" in kinds and kinds.count("restaurant") == 2  # lunch + dinner only (no unrequested dessert)
    assert not any(s["place"].get("adults_only") for s in stops(out))
    dinner = next(s for s in stops(out) if s.get("meal") == "dinner")
    assert "mexican" in dinner["place"]["cuisines"]
    assert out["budget"]["stay"] > 0 and "per night" in out["answer"]


def test_typed_answers_work_and_bad_city_is_reasked():
    p = Planner()
    p.ask("Date night")
    out = p.ask("somewhere nice")  # not a city
    assert out["interview"]["q"]["key"] == "where" and "didn't recognize" in out["answer"]
    out = p.ask("sedona please")
    assert out["interview"]["q"]["key"] == "when"


def test_stop_questions_and_just_plan():
    p = Planner()
    p.ask("Friends' day out")
    p.ask("Tempe")
    out = p.ask("just plan it")
    assert out["intent"] == "new_plan" and out["request"]["city"] == "Tempe" and stops(out)
    p2 = Planner()
    p2.ask("Game day")
    out = p2.ask("cancel")
    assert out["intent"] == "chat" and not out.get("interview")


def test_hotel_request_in_free_text():
    out = Planner().ask("Weekend in Sedona for 2, hiking and a hotel, $250 per person")
    hotels = [s for s in stops(out) if s["kind"] == "lodging"]
    assert len(hotels) == 1 and hotels[0]["place"]["kind"] == "lodging"
    assert "Google Hotels" in hotels[0]["booking"]["links"]


def test_reviews_are_real_quotes():
    p = Planner()
    p.ask("Saturday in Phoenix for 2, zoo and Mexican food")
    out = p.ask("what do people say about stop 1?")
    assert out["intent"] == "tools" and "Google reviews" in out["answer"] and "> " in out["answer"]
    out = p.ask("reviews of Phoenix Zoo")
    assert "Phoenix Zoo" in out["answer"] and "Star breakdown" in out["answer"]


def test_reserve_restaurant_and_hotel_with_prefilled_links():
    p = Planner()
    p.ask("Weekend in Sedona for 2, hiking, dinner and a hotel, $250 per person")
    out = p.ask("book dinner")
    assert out["pending"]["kind"] == "restaurant" and "please check and confirm" in out["answer"]
    out = p.ask("yes at 7:30pm for 3")
    r = out["reservations"][-1]
    assert (r["time_min"], r["party"]) == (19 * 60 + 30, 3)
    assert "covers=3" in r["links"]["OpenTable"] and "T19:30" in r["links"]["OpenTable"] and "seats=3" in r["links"]["Resy"]
    out = p.ask("reserve the hotel")
    assert out["pending"]["kind"] == "lodging"
    out = p.ask("yes for 2 guests, 2 rooms, 2 nights")
    h = out["reservations"][-1]
    assert (h["rooms"], h["nights"]) == (2, 2) and "no_rooms=2" in h["links"]["Booking.com"]
    d = h["date"]
    assert f"checkin={d}" in h["links"]["Booking.com"] and "checkout=" in h["links"]["Booking.com"]
    out = p.ask("I booked the hotel, confirmation number HX42K9")
    h = next(x for x in out["reservations"] if x["kind"] == "lodging")
    assert h["status"] == "booked" and h["confirmation"] == "HX42K9"
    assert "booked" in p.ask("show my reservations")["answer"]


def test_distances_show_miles_and_km():
    from azmulti.units import dist
    assert dist(24.9) == "15.5 mi (24.9 km)" and dist(None) == "0.0 mi (0.0 km)"


def test_experience_booking_tickets_and_free_stops():
    p = Planner()
    out = p.ask("Plan a Saturday in Scottsdale for four friends. We're all 24, have a $125 budget per person, want a fun "
                "outdoor activity, Mexican food for dinner, and somewhere to go out afterward.")
    st = stops(out)
    free = next((i for i, s in enumerate(st, 1) if s["kind"] == "attraction" and not s.get("cost_pp")), None)
    paid = next(i for i, s in enumerate(st, 1) if s["kind"] == "attraction" and s.get("cost_pp"))
    if free:
        out = p.ask(f"get tickets for stop {free}")
        assert "free" in out["answer"] and not out.get("pending")
    out = p.ask(f"get tickets for stop {paid}")
    assert out["pending"]["kind"] == "attraction"
    r = p.ask("yes for 4")["reservations"][-1]
    assert r["party"] == 4 and "GetYourGuide" in r["links"] and "Viator" in r["links"] and "Find official tickets" in r["links"]


def test_top5_attractions_differ_by_traveler_and_cite_reviews():
    hike = Planner().ask("Top 5 attractions in Sedona for hiking with big views")
    art = Planner().ask("Top 5 attractions in Sedona for art galleries and local artists")
    assert hike["intent"] == "tools" and hike["answer"].count("\n1. ") + hike["answer"].count("\n5. ") == 2
    names = lambda a: {l.split("**[")[1].split("]")[0] for l in a["answer"].splitlines() if l[:3] in {f"{i}. " for i in range(1, 6)}}  # noqa: E731
    assert len(names(hike)) == 5 and not names(hike) & names(art)  # different travelers -> different picks
    assert "A reviewer said" in hike["answer"] and "Match to your interests" in hike["answer"]
    kids = Planner().ask("top 5 tourist attractions in Phoenix for a family with young kids who love animals")
    assert "Zoo" in kids["answer"].split("\n2. ")[0]


def test_review_rag_grounds_plan_reasons():
    out = Planner().ask("Plan a Saturday in Scottsdale for 2, hiking and Mexican food")
    st = stops(out)
    assert any((s["place"].get("review_evidence") or {}).get("text") for s in st)
    assert "A reviewer said" in out["answer"]


def test_website_and_menu_links():
    from azmulti.agents.concierge import web_links
    out = Planner().ask("Plan a Saturday in Scottsdale for 2, hiking and Mexican food")
    st = stops(out)
    food = next(s for s in st if s["kind"] == "restaurant")
    act = next(s for s in st if s["kind"] == "attraction")
    w = web_links(food["place"], "restaurant")
    assert "menu" in w["Menu"] and "Find website" in w and "Menu" not in web_links(act["place"], "attraction")
    assert "Menu" in food["booking"]["links"]
    live = dict(food["place"], live={"website": "https://example-restaurant.com"})
    assert web_links(live, "restaurant")["Website"] == "https://example-restaurant.com"  # real site when Places is live
    assert "Website" not in web_links(dict(live, live={"website": "javascript:alert(1)"}), "restaurant")  # unsafe URL dropped


def test_any_date_and_date_ranges():
    from datetime import date, timedelta
    from azmulti.agents.planner import parse_request_rules
    far = date.today() + timedelta(days=200)
    end = far + timedelta(days=3)
    r = parse_request_rules(f"trip in Sedona from {far.isoformat()} to {end.isoformat()} for 2")
    assert (r.trip_date, r.end_date, r.days) == (far.isoformat(), end.isoformat(), 4)
    r = parse_request_rules("Sedona March 3-6 for 2")
    assert r.days == 4 and r.trip_date.endswith("-03-03") and r.end_date.endswith("-03-06")
    r = parse_request_rules("Tucson Mar 30 to Apr 2")
    assert r.days == 4 and r.end_date.endswith("-04-02")
    p = Planner()
    out = p.ask(f"Trip in Sedona from {far.isoformat()} to {end.isoformat()} for 2, hiking, dinner and a hotel")
    assert out["conditions"]["date"] == far.isoformat() and sorted({s["day"] for s in stops(out)}) == [1, 2, 3, 4]
    assert sum(s["kind"] == "lodging" for s in stops(out)) == 3 and "(4 days)" in out["answer"]
    out = p.ask(f"change the dates to {(far + timedelta(days=30)).isoformat()} to {(far + timedelta(days=31)).isoformat()}")
    assert out["request"]["days"] == 2 and out["conditions"]["date"] == (far + timedelta(days=30)).isoformat()


def test_long_range_is_capped_with_a_note():
    from datetime import date, timedelta
    s = date.today() + timedelta(days=60)
    out = Planner().ask(f"Phoenix from {s.isoformat()} to {(s + timedelta(days=19)).isoformat()} for 2, museums")
    assert out["request"]["days"] == 14 and "up to 14 days" in out["answer"]


def test_interview_accepts_calendar_dates():
    from datetime import date, timedelta
    s = date.today() + timedelta(days=90)
    p = Planner()
    p.ask("Date night")
    p.ask("Sedona")
    out = p.ask(f"from {s.isoformat()} to {(s + timedelta(days=1)).isoformat()}")
    assert out["interview"]["q"]["key"] not in ("where", "when")  # dates accepted, moved on
    while out["interview"]["q"]["key"] != "confirm":
        out = p.ask("skip")
    out = p.ask("Plan it")
    assert out["request"]["trip_date"] == s.isoformat() and out["request"]["days"] == 2


def test_closed_places_are_never_recommended():
    from azmulti.catalog import get_catalog
    cat = get_catalog()
    df = cat.df
    assert df.loc[df.name.eq("Camp Social"), "likely_closed"].all()  # no reviews since 2019: closed since
    assert df.loc[df.status_2021.fillna("").eq("Temporarily closed"), "likely_closed"].all()
    ctr = cat.locate("Phoenix")
    for q, kind in [("brunch cocktails", "restaurant"), ("live music bar", "nightlife"), ("museum", "attraction")]:
        assert not any(h["likely_closed"] for h in cat.search(q, kind=kind, center=ctr, radius_km=40, k=30))
    out = Planner().ask("reviews of Camp Social in Phoenix")
    assert "probably closed" in out["answer"]


def test_reporting_a_closed_stop_replaces_it_for_good():
    from azmulti.catalog import get_catalog
    p = Planner()
    out = p.ask("Saturday in Tempe for 2, bowling and Indian food")
    st = stops(out)
    old = st[1]["place"]
    out = p.ask("stop 2 is permanently closed")
    assert stops(out)[1]["place"]["id"] != old["id"]
    assert get_catalog().by_id(old["id"])["likely_closed"]
    again = Planner().ask("Saturday in Tempe for 2, bowling and Indian food")
    assert old["id"] not in {s["place"]["id"] for s in stops(again)}


def test_new_trip_keeps_old_trips():
    p = Planner()
    p.ask("Saturday in Sedona for 2, hiking and Italian dinner")
    p.ask("make it cheaper")
    assert [t["city"] for t in p.trips] == ["Sedona"]  # edits update the same trip
    p.ask("Plan a Sunday in Tucson for 4, museums and tacos")
    assert sorted(t["city"] for t in p.trips) == ["Sedona", "Tucson"]  # a new city keeps the old trip
    p.reset()
    assert not p.state().get("slots") and len(p.trips) == 2  # New trip: nothing thrown away
    sedona = next(t for t in p.trips if t["city"] == "Sedona")
    assert sedona["stops"] >= 3 and sedona["when"]
    assert p.open_trip(sedona["id"]) and p.state()["request"]["city"] == "Sedona"
    out = p.ask("swap dinner for something different")
    assert out["request"]["city"] == "Sedona" and len(p.trips) == 2
    p.delete_trip(sedona["id"])
    assert [t["city"] for t in p.trips] == ["Tucson"]


def test_saved_trips_persist_only_with_memory_on(tmp_path, monkeypatch):
    import azmulti.profiles as P
    monkeypatch.setattr(P, "PROFILE_DIR", tmp_path)
    p = Planner()
    p.ask("Saturday in Sedona for 2, hiking")
    assert not list(tmp_path.glob("*.trips.json"))  # memory off: nothing written
    p.set_memory(True, "Jordan", "4321")
    assert len(list(tmp_path.glob("*.trips.json"))) == 1
    again = Planner("Jordan", "4321")
    assert [t["city"] for t in again.trips] == ["Sedona"]
    assert Planner("Jordan", "0000").trips == []  # wrong PIN: no trips
    again.forget()
    assert not list(tmp_path.glob("*.trips.json"))


def test_multi_city_trip():
    from azmulti.agents.planner import parse_request_rules
    r = parse_request_rules("3 days in Phoenix, then 2 days in Sedona and a day at the Grand Canyon")
    assert [(lg["city"], lg["days"]) for lg in r.legs] == [("Phoenix", 3), ("Sedona", 2), ("Grand Canyon Village", 1)] and r.days == 6
    r = parse_request_rules("10-day trip: Phoenix, Sedona, Page")
    assert [lg["days"] for lg in r.legs] == [4, 3, 3]
    assert parse_request_rules("day trip to Sedona from Phoenix").city == "Sedona" and not parse_request_rules("Sunday in Tempe").legs
    out = Planner().ask("Trip for 2: 2 days in Phoenix, then 2 days in Sedona, hiking and Mexican food")
    st = stops(out)
    by_day = {d: {s.get("city") for s in st if s["day"] == d} for d in (1, 2, 3, 4)}
    assert by_day == {1: {"Phoenix"}, 2: {"Phoenix"}, 3: {"Sedona"}, 4: {"Sedona"}}
    sedona_stops = [s for s in st if s["day"] >= 3 and s["kind"] != "lodging"]
    assert all(s["place"]["city"] in ("Sedona", "Village of Oak Creek", "Cornville", "Cottonwood") or (s["place"].get("distance_km") or 0) < 40 for s in sedona_stops)
    hotels = [s for s in st if s["kind"] == "lodging"]
    assert len({h["place"]["id"] for h in hotels if h["day"] <= 2}) == 1 and len({h["place"]["id"] for h in hotels if h["day"] == 3}) == 1
    assert {h["place"]["id"] for h in hotels if h["day"] <= 2}.isdisjoint({h["place"]["id"] for h in hotels if h["day"] == 3})
    tr = next(s["transfer"] for s in st if s.get("transfer"))
    assert tr["from"] == "Phoenix" and tr["to"] == "Sedona" and 90 <= tr["min"] <= 150  # ~2 h drive
    assert "Phoenix (2 days) → Sedona (2 days)" in out["answer"] and "Travel day" in out["answer"]
    assert out["quality"]["total"] >= 70


def test_add_a_city_to_a_trip():
    p = Planner()
    p.ask("Weekend in Phoenix for 2, museums and tacos")
    out = p.ask("add 2 days in Sedona")
    assert [(lg["city"], lg["days"]) for lg in out["request"]["legs"]] == [("Phoenix", 2), ("Sedona", 2)]
    assert len(p.trips) == 1  # same trip, extended


def test_each_city_gets_its_own_weather():
    out = Planner().ask("Trip for 2 from 2027-07-10: 2 days in Phoenix, then 2 days in Flagstaff, hiking and Mexican food")
    bd = out["conditions"]["by_day"]
    assert [bd[d]["city"] for d in (1, 2, 3, 4)] == ["Phoenix", "Phoenix", "Flagstaff", "Flagstaff"]
    assert bd[1]["high_f"] >= 100 and bd[3]["high_f"] <= 90  # Phoenix July heat vs. cool Flagstaff
    acts = {s["day"]: s for s in stops(out) if s["kind"] == "attraction" and s["label"].startswith(("Sunrise", "Morning"))}
    assert acts[1]["label"] == "Sunrise activity" and acts[1]["start"] < 420  # hot Phoenix: hike at dawn
    assert acts[3]["label"] == "Morning activity"  # Flagstaff: normal morning
    pm = {s["day"]: s for s in stops(out) if s["label"] == "Afternoon activity"}
    assert pm[1].get("indoor_only") and not pm[3].get("indoor_only")  # afternoons indoors only where it's hot
    assert "*Phoenix: high" in out["answer"] and "*Flagstaff: high" in out["answer"] and "Weather in Flagstaff" in out["answer"]


def test_hot_day_breakfast_comes_after_the_sunrise_hike():
    out = Planner().ask("Weekend in Phoenix in July for 2, hiking and museums")
    day1 = [s for s in stops(out) if s["day"] == 1]
    assert day1[0]["label"] == "Sunrise activity" and day1[1]["label"] == "Breakfast"
