"""Car Rental agent: car size and count from the group, pickup airport and dates, estimated cost in the budget,
"Book a rental car" on the checklist, and switching it on / off in chat."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("AZ_OFFLINE", "1")

from azmulti.agents.rental_agent import SIZES, plan_rental  # noqa: E402
from azmulti.graph import Planner  # noqa: E402


def req(**kw):
    return {"rental_car": True, "transport": "car", "party_size": 2, "days": 3, "city": "Phoenix", **kw}


def test_car_size_follows_the_group():
    assert plan_rental(req(party_size=2), {"km": 100}, "2027-03-12")["size"] == "economy"
    assert plan_rental(req(party_size=4), {"km": 100}, "2027-03-12")["size"] == "midsize"
    assert plan_rental(req(party_size=5), {"km": 100}, "2027-03-12")["size"] == "SUV"
    assert plan_rental(req(party_size=6), {"km": 100}, "2027-03-12")["size"] == "minivan"
    big = plan_rental(req(party_size=12), {"km": 100}, "2027-03-12")
    assert big["cars"] == 2 and big["size"] == "minivan"
    assert plan_rental(req(party_size=2), {"km": 600}, "2027-03-12")["size"] == "midsize"  # long drives: more room
    kids_hiking = plan_rental(req(party_size=4, kids=2, interests=["hiking_outdoors"]), {"km": 100}, "2027-03-12")
    assert kids_hiking["size"] == "SUV" and any("car seats" in n for n in kids_hiking["notes"])


def test_pickup_dates_cost_and_link():
    r = plan_rental(req(party_size=4, days=3, city="Tucson"), {"km": 200}, "2027-03-12")
    assert r["airport"] == "TUS" and r["pickup_date"] == "2027-03-12" and r["return_date"] == "2027-03-14"
    assert r["total"] == 3 * SIZES["midsize"][1] and r["per_person"] == round(r["total"] / 4, 2)
    assert r["link"] == "https://www.kayak.com/cars/TUS/2027-03-12/2027-03-14"
    assert plan_rental(req(transport="rideshare"), {}, None) is None  # not driving: no rental
    assert plan_rental({"transport": "car", "party_size": 2}, {}, None) is None  # own car


def test_rental_runs_as_an_agent_in_the_plan():
    p = Planner()
    p.ask("3 days in Tucson starting 2027-03-12 for 4 people, museums and hiking, $200 each, we need a rental car")
    st = p.state()
    rc = st["rental"]
    assert rc and rc["airport"] == "TUS" and rc["cars"] == 1
    assert st["request"]["days"] == 3 and rc["days"] == 3 and rc["return_date"] == "2027-03-14"  # "3 days in Tucson"
    assert any(t["agent"] == "Car Rental" for t in st["trace"])
    line = next(l for l in st["budget"]["lines"] if l["category"] == "rental car")
    assert line["per_person"] == rc["per_person"] and st["budget"]["rental_pp"] == rc["per_person"]
    item = next(i for i in p.checklist() if i["kind"] == "car")
    assert item["link"] == rc["link"] and item["due"] == "2027-02-26"  # two weeks ahead


def test_switch_rental_on_and_off_in_chat():
    p = Planner()
    p.ask("Saturday in Phoenix for 2, museums")
    assert not p.state().get("rental")
    out = p.ask("we need a rental car")
    assert "rental car" in out["answer"].lower() and p.state()["rental"]["size"] == "economy"
    assert any(i["kind"] == "car" for i in p.checklist())
    p.ask("actually we have our own car")
    assert not p.state().get("rental") and p.state()["budget"]["rental_pp"] == 0
    assert not any(i["kind"] == "car" for i in p.checklist())


def test_single_city_day_count():
    from azmulti.agents.planner import parse_request_rules
    assert parse_request_rules("3 days in Tucson for 4 people").days == 3
    assert parse_request_rules("2 days at the Grand Canyon").days == 2
    assert parse_request_rules("3 days in Phoenix, then 2 days in Sedona").days == 5  # multi-city unchanged
    assert parse_request_rules("Saturday in Sedona for 2").days == 1
