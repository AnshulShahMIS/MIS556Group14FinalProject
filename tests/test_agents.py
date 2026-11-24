"""Offline tests (no API key): pytest -q"""
import os
import sys
from pathlib import Path

os.environ["AZ_OFFLINE"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

from azmulti import hours as H  # noqa: E402
from azmulti.agents.planner import parse_request_rules  # noqa: E402
from azmulti.graph import Planner  # noqa: E402

BRIEF = ("Plan a Saturday in Scottsdale for four friends. We're all 24, have a $125 budget per person, want a fun "
         "outdoor activity, Mexican food for dinner, and somewhere to go out afterward.")


def test_hours_parsing():
    s = H.parse_hours("Monday: 9AM–5PM; Tuesday: Closed; Friday: 5–10PM; Saturday: 11AM–2AM")
    assert H.open_for(s, 0, 600, 60) is True
    assert H.open_for(s, 0, 990, 60) is False      # runs past 5PM
    assert H.open_for(s, 1, 600, 30) is False      # closed Tuesday
    assert H.open_for(s, 4, 1080, 90) is True      # "5–10PM" inherits PM
    assert H.open_for(s, 5, 1320, 180) is True     # past midnight
    assert H.open_for(s, 3, 600, 60) is None       # Thursday unknown


def test_rule_parser_on_brief_example():
    r = parse_request_rules(BRIEF)
    assert r.city == "Scottsdale" and r.weekday == 5 and r.party_size == 4
    assert r.budget_per_person == 125 and r.tier == "balanced"   # "$125 budget" is not "on a budget"
    assert "mexican" in r.cuisines and r.nightlife and "outdoors" in r.interests


def test_parser_kids_block_nightlife():
    r = parse_request_rules("Saturday in Phoenix with two kids, then drinks at a bar")
    assert r.has_kids and not r.nightlife and r.party_size == 4


@pytest.fixture(scope="module")
def brief_run():
    p = Planner()
    return p, p.ask(BRIEF)


def test_full_plan(brief_run):
    _, out = brief_run
    stops = [s for s in out["slots"] if s.get("place")]
    kinds = [s["kind"] for s in stops]
    assert len(stops) == 5 and "nightlife" in kinds and kinds.count("attraction") == 2
    dinner = next(s for s in stops if s.get("meal") == "dinner")
    assert "mexican" in dinner["place"]["cuisines"]
    assert out["budget"]["total_pp"] <= 125
    assert all(s.get("open_ok") is not False for s in stops)
    assert len({s["place"]["id"] for s in stops}) == len(stops)
    assert out["quality"]["total"] >= 70
    assert "BEGIN:VCALENDAR" in out["booking"]["ics"]
    agents = {t["agent"] for t in out["trace"]}
    assert {"Planner", "Attraction", "Dining & Nightlife", "Route", "Budget", "Critic", "Booking", "Concierge"} <= agents


def test_revision_keeps_other_stops_and_is_not_pricier(brief_run):
    p, first = brief_run
    before = {s["label"]: s for s in first["slots"]}
    out = p.ask("swap dinner for something cheaper")
    assert out["intent"] == "revise"
    after = {s["label"]: s for s in out["slots"]}
    assert after["Dinner"]["place"]["id"] != before["Dinner"]["place"]["id"]
    assert after["Dinner"]["cost_pp"] <= before["Dinner"]["cost_pp"]
    assert after["Lunch"]["place"]["id"] == before["Lunch"]["place"]["id"]


def test_question_routes_to_rag(brief_run):
    p, _ = brief_run
    out = p.ask("Is the first stop good for beginners?")
    assert out["intent"] == "question" and out["trace"][-1]["agent"] == "Q&A"


def test_summer_heat_plan():
    out = Planner().ask("Hiking and museums in Phoenix in July for 2 adults")
    first = next(s for s in out["slots"] if s["kind"] == "attraction")
    assert first["target"] <= 420 or first["theme"] not in ("outdoors", "gardens")
    afternoon = [s for s in out["slots"] if s["label"] == "Afternoon activity" and s.get("place")]
    assert all(not s["place"]["outdoor"] for s in afternoon)


# ---- conversation handling: never invent a plan from small talk -------------------------------
@pytest.mark.parametrize("msg", ["hello", "hey there!", "hi", "what can you do?", "asdfgh", "thanks", "", "tell me a joke"])
def test_small_talk_gets_a_reply_not_a_plan(msg):
    out = Planner().ask(msg)
    assert out["intent"] == "chat" and not out.get("slots")
    assert "Arizona" in out["answer"] or "welcome" in out["answer"].lower()


@pytest.mark.parametrize("msg", ["Plan a day in Las Vegas for 2", "weekend in San Diego, beaches"])
def test_outside_arizona_is_declined(msg):
    out = Planner().ask(msg)
    assert out["intent"] == "chat" and not out.get("slots") and "only plan trips inside" in out["answer"]


def test_revision_without_a_plan_asks_for_one():
    out = Planner().ask("swap dinner for something cheaper")
    assert out["intent"] == "chat" and not out.get("slots") and "no plan to change yet" in out["answer"]


def test_question_without_a_plan_uses_rag():
    out = Planner().ask("Where should we eat tacos in Tucson?")
    assert out["intent"] == "question" and "Tucson" in out["answer"]


def test_full_conversation_flow():
    p = Planner()
    assert p.ask("hi")["intent"] == "chat"
    plan = p.ask("Saturday in Tempe for 3 friends, $80 each, museum and pizza")
    assert plan["intent"] == "new_plan" and plan["request"]["city"] == "Tempe"
    thanks = p.ask("thanks!")
    assert thanks["intent"] == "chat" and len(thanks["slots"]) == len(plan["slots"])   # plan kept
    assert p.ask("replace stop 2")["intent"] == "revise"
    q = p.ask("what time is dinner?")
    assert q["intent"] == "question" and "In your plan, **Dinner**" in q["answer"]
    new = p.ask("now plan a different day in Sedona with hiking")
    assert new["intent"] == "new_plan" and new["request"]["city"] == "Sedona"


def test_missing_city_is_disclosed():
    out = Planner().ask("something fun to do this weekend")
    assert out["intent"] == "new_plan" and "No city given" in out["answer"]
