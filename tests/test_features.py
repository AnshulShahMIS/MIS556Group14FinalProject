"""One test per feature group from Ideas_for_Final_Project_AI_Agent_Capabilities_Usage.pdf (offline)."""
import os
import sys
from pathlib import Path

os.environ["AZ_OFFLINE"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

from azmulti import weather as W  # noqa: E402
from azmulti.agents.planner import parse_request_rules  # noqa: E402
from azmulti.graph import Planner  # noqa: E402
from azmulti.profiles import _path  # noqa: E402

BRIEF = ("Plan a Saturday in Scottsdale for four friends. We're all 24, have a $125 budget per person, want a fun "
         "outdoor activity, Mexican food for dinner, and somewhere to go out afterward.")


def stops(out):
    return [s for s in out["slots"] if s.get("place")]


@pytest.fixture()
def planned():
    p = Planner()
    return p, p.ask(BRIEF)


# 1. Personalized itinerary planner ---------------------------------------------------------
def test_multiple_itinerary_options(planned):
    p, _ = planned
    out = p.ask("show me budget, balanced and premium options")
    assert out["intent"] == "tools" and set(out["options"]) == {"budget", "balanced", "premium"}
    costs = [out["options"][t]["budget"]["total_pp"] for t in ("budget", "balanced", "premium")]
    assert costs[0] <= costs[1] <= costs[2]
    picked = p.ask("go with the budget option")
    assert picked["budget"]["total_pp"] == pytest.approx(costs[0])


def test_options_as_first_message():
    out = Planner().ask("Give me budget, balanced and premium options for a Saturday in Tucson for 2, hiking and tacos")
    assert out["intent"] == "tools" and out["options"] and out["request"]["city"] == "Tucson"


def test_time_window_and_party_parsing():
    r = parse_request_rules("We're free from 2 to 8pm on Thursday in Tempe, 3 friends")
    assert (r.start_time, r.end_time, r.weekday, r.party_size) == (840, 1200, 3, 3)
    assert parse_request_rules("Weekend in Flagstaff for 4, hiking").party_size == 4
    assert parse_request_rules("Mesa with toddlers").party_size == 4


# 2. Attraction discovery & filtering -------------------------------------------------------
def test_free_only_and_indoor():
    out = Planner().ask("Free things to do in Tucson on Sunday for a student")
    assert all(s["cost_pp"] == 0 for s in stops(out) if s["kind"] == "attraction")
    out = Planner().ask("Indoor activities in Mesa with toddlers on Wednesday, $50 per person")
    assert all(not s["place"]["outdoor"] for s in stops(out) if s["kind"] == "attraction")
    assert all(not s["place"]["adults_only"] for s in stops(out))


def test_zip_radius_and_walking():
    out = Planner().ask("Saturday near 85281 within 3 miles, walking only, coffee and a museum")
    assert out["request"]["city"] == "Tempe" and out["request"]["transport"] == "walking"
    assert all((s.get("drive_km") or 0) < 6 for s in stops(out))


def test_rating_and_atmosphere_filters():
    out = Planner().ask("Romantic dinner in Scottsdale, 4.5 stars or better, upscale, Friday evening, $150 per person")
    dinner = next(s for s in stops(out) if s.get("meal") == "dinner")
    assert out["request"]["min_rating"] == 4.5 and out["request"]["atmosphere"] == "Romantic"
    assert dinner["place"]["rating"] >= 4.5


# 3./4. Restaurants, booking, reservations --------------------------------------------------
def test_booking_needs_approval_and_is_tracked(planned):
    p, _ = planned
    ask = p.ask("book dinner")
    assert ask["pending"] and "confirm" in ask["answer"].lower() and not ask.get("reservations")
    ok = p.ask("yes")
    assert ok["reservations"][0]["name"] == ask["pending"]["name"] and not ok["pending"]
    assert "reservation" in p.ask("show my reservations")["answer"].lower()


def test_booking_cancel():
    p = Planner()
    p.ask(BRIEF)
    p.ask("book dinner")
    out = p.ask("no")
    assert not out["pending"] and not out.get("reservations")


def test_alternatives_and_swap(planned):
    p, first = planned
    alts = p.ask("other options for dinner")
    assert "Stop" in alts["answer"]
    lunch_before = next(s for s in stops(first) if s.get("meal") == "lunch")["place"]["id"]
    swapped = p.ask("use the first option for lunch")
    assert next(s for s in stops(swapped) if s.get("meal") == "lunch")["place"]["id"] != lunch_before


def test_unavailable_dinner_is_replanned(planned):
    p, first = planned
    before = next(s for s in stops(first) if s.get("meal") == "dinner")["place"]["id"]
    out = p.ask("dinner is fully booked")
    assert next(s for s in stops(out) if s.get("meal") == "dinner")["place"]["id"] != before


def test_booking_links(planned):
    _, out = planned
    links = {k for s in stops(out) for k in (s.get("booking") or {}).get("links", {})}
    assert {"Google Maps", "Directions", "Add to Google Calendar"} <= links


# 5. RAG & real-time info --------------------------------------------------------------------
def test_answers_cite_sources(planned):
    _, out = planned
    assert "Sources:" in out["answer"] and "2021" in out["answer"]


def test_compare(planned):
    p, _ = planned
    out = p.ask("compare dinner options")
    assert out["intent"] == "tools" and "Trust score" in out["answer"] and out["answer"].count("|") > 20


# 6. Maps & transportation ---------------------------------------------------------------------
def test_route_link_nearby_and_add_stop(planned):
    p, out = planned
    assert out["route"]["maps_url"].startswith("https://www.google.com/maps/dir/")
    near = p.ask("what's near stop 1?")
    assert "Near" in near["answer"]
    added = p.ask("add a coffee stop after stop 1")
    assert len(stops(added)) == len(stops(out)) + 1


def test_transport_change(planned):
    p, _ = planned
    out = p.ask("we'll take uber instead")
    assert out["request"]["transport"] == "rideshare" and out["budget"]["transport_mode"] == "rideshare"


# 7. Weather & seasonal --------------------------------------------------------------------------
def test_sun_times_reasonable():
    rise, sset = W.sun_times(33.45, -112.07, __import__("datetime").date(2026, 6, 21))
    assert 5 * 60 + 10 <= rise <= 5 * 60 + 30 and 19 * 60 + 25 <= sset <= 19 * 60 + 50  # Phoenix solstice ~5:19 / ~19:42


def test_heat_plan_and_events():
    out = Planner().ask("Hiking and museums in Phoenix in July for 2 adults")
    first = stops(out)[0]
    assert first["start"] < 420 and any("Hot day" in n for n in out["notes"])
    ev = Planner().ask("what events are happening in March in Mesa?")
    assert "spring training" in ev["answer"].lower()


def test_weather_question():
    out = Planner().ask("what's the weather in Flagstaff?")
    assert out["intent"] == "tools" and "Sunrise" in out["answer"]


# 8. Personalization & memory --------------------------------------------------------------------
def test_memory_is_opt_in_and_forgettable(tmp_path, monkeypatch):
    import azmulti.profiles as P
    monkeypatch.setattr(P, "PROFILE_DIR", tmp_path)
    p = Planner(profile_name="pytest-user", pin="4321")
    p.ask("remember I'm vegetarian")
    assert p.profile["memory_on"] and "vegetarian" in p.profile["dietary"]
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1 and "pytest" not in files[0].name  # name not visible on disk
    assert not Planner(profile_name="pytest-user", pin="0000").profile.get("dietary")  # wrong PIN sees nothing
    out = p.ask("Saturday in Tempe for 2, lunch and a museum")
    assert "vegetarian" in out["request"]["dietary"]
    p.ask("forget me")
    assert not list(tmp_path.glob("*.json")) and not p.profile.get("memory_on")


def test_memory_off_writes_nothing(tmp_path, monkeypatch):
    import azmulti.profiles as P
    monkeypatch.setattr(P, "PROFILE_DIR", tmp_path)
    p = Planner(profile_name="nobody")
    p.ask(BRIEF)
    p.ask("save stop 1")
    assert not list(tmp_path.iterdir())


def test_feedback_learns_and_replaces(planned):
    p, first = planned
    attr_cat = next(s for s in stops(first) if s["kind"] == "attraction" and s["label"].startswith("Afternoon"))["place"]["primary_category"]
    out = p.ask(f"we don't like {attr_cat.lower()}s")
    assert out["intent"] == "tools" and attr_cat in out["profile"]["disliked_categories"]
    assert all(s["place"]["primary_category"] != attr_cat for s in stops(out))


def test_favorites(planned):
    p, _ = planned
    p.ask("save stop 2")
    assert "Your favorites" in p.ask("show my favorites")["answer"]


# 9./10. Group & budget --------------------------------------------------------------------------
def test_budget_split_and_realloc(planned):
    p, first = planned
    b = p.ask("how much will this cost? split the bill")
    assert "Group of 4" in b["answer"]
    before = next(s for s in stops(first) if s.get("meal") == "dinner")["cost_pp"]
    out = p.ask("spend more on dinner and less on activities")
    assert next(s for s in stops(out) if s.get("meal") == "dinner")["cost_pp"] >= before


def test_group_vote_applies_winner(planned):
    p, first = planned
    s = next(x for x in stops(first) if x.get("meal") == "lunch")
    winner = s["alternatives"][0]["id"]
    new = p.apply_votes({s["id"]: winner})
    assert next(x for x in new["slots"] if x["id"] == s["id"])["place"]["id"] == winner


def test_share(planned):
    p, _ = planned
    assert "google.com/maps/dir" in p.ask("share this plan")["answer"]


# Robustness -------------------------------------------------------------------------------------
@pytest.mark.parametrize("msg", ["compare dinner options", "book dinner", "what's near stop 1?", "share this plan",
                                 "how much will this cost?", "use the second option for lunch", "let's vote"])
def test_plan_commands_without_a_plan_are_handled(msg):
    out = Planner().ask(msg)
    assert out.get("answer") and not out.get("slots")


def test_very_long_and_weird_input():
    out = Planner().ask("!!!" * 1000)
    assert out["intent"] == "chat"
    out = Planner().ask("Phoenix " * 400)
    assert out["intent"] in ("new_plan", "chat")


def test_followup_edits_keep_the_trip():
    p = Planner()
    p.ask("Saturday in Tempe for 3 friends, $80 each, museum and pizza")
    out = p.ask("actually make it sunday")
    assert out["request"]["city"] == "Tempe" and out["request"]["weekday"] == 6 and out["request"]["party_size"] == 3
    out = p.ask("for 6 people instead")
    assert out["request"]["party_size"] == 6 and out["request"]["city"] == "Tempe"


def test_swap_only_touches_the_named_stop():
    p = Planner()
    first = p.ask("Saturday in Tempe for 3 friends, $80 each, museum and pizza")
    museums = [s for s in stops(first) if "museum" in (s["place"].get("categories") or "").lower()]
    others = {s["id"]: s["place"]["id"] for s in stops(first) if s not in museums}
    out = p.ask("swap the museum for a hike")
    after = {s["id"]: s["place"]["id"] for s in stops(out)}
    assert all(after[k] == v for k, v in others.items() if k in after)


def test_remove_distance_and_cheapest():
    p = Planner()
    first = p.ask(BRIEF)
    d = p.ask("how far is lunch from the first stop?")
    assert d["intent"] == "tools" and "km" in d["answer"]
    c = p.ask("what's the cheapest option for dinner?")
    assert c["intent"] == "tools" and "Dinner" in c["answer"]
    p.ask("add dessert after dinner")
    out = p.ask("remove the dessert")
    assert len(stops(out)) == len(stops(first))


@pytest.mark.parametrize("msg,intent", [("what can u do", "chat"), ("?", "chat"), ("Is it hot in July?", "tools"),
                                        ("compare Phoenix Zoo vs Desert Botanical Garden", "tools"),
                                        ("8 girls in Scottsdale saturday night, $200 each", "new_plan")])
def test_misc_routing(msg, intent):
    assert Planner().ask(msg)["intent"] == intent


def test_interrupted_run_rolls_back(monkeypatch):
    import azmulti.graph as G
    p = Planner()
    p.ask("Saturday in Tempe for 3 friends, $80 each, museum and pizza")
    saver = p.graph.checkpointer

    def boom(state):
        raise KeyboardInterrupt("simulated interruption")
    monkeypatch.setattr(G, "attraction_node", boom)
    p.graph = G.build_graph(saver)
    with pytest.raises(KeyboardInterrupt):
        p.ask("actually make it sunday")
    monkeypatch.undo()
    p.graph = G.build_graph(saver)
    st = p.state()
    assert st["request"]["city"] == "Tempe" and len([s for s in st["slots"] if s.get("place")]) >= 4
    out = p.ask("actually make it sunday")
    assert out["request"]["city"] == "Tempe" and out["request"]["weekday"] == 6
