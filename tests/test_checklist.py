"""Pre-trip checklist: items built from the plan and place data, and ticks saved with the trip."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("AZ_OFFLINE", "1")

import pytest  # noqa: E402

from azmulti import accounts as A  # noqa: E402
from azmulti import checklist as CL  # noqa: E402
from azmulti.graph import Planner  # noqa: E402
from azmulti.store import LocalStore, set_store  # noqa: E402

PW = "desert-sun-42"


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = LocalStore(tmp_path / "store")
    set_store(s)
    A._misses.clear()
    yield s
    set_store(LocalStore())


def account(name="jordan"):
    return A.create_account(name, PW, PW)[2]


def planned(acc=None, when="2027-03-12"):
    p = Planner(acc)
    p.ask(f"Trip to Scottsdale on {when} for 4 friends, hiking, then a nice steakhouse dinner and the Heard Museum, "
          "$150 each, and a hotel to stay overnight")
    return p


def test_items_come_from_the_plan(store):
    p = planned()
    items = p.checklist()
    kinds = {i["kind"] for i in items}
    assert {"hotel", "weather"} <= kinds
    hotel = next(i for i in items if i["kind"] == "hotel")
    assert hotel["due"] == "2027-02-19" and hotel["link"]  # 3 weeks before, with a booking link
    for i in items:  # every item has a date before (or on) the day it's for, and text
        assert i["text"] and i["why"] and i["due"] <= "2027-03-12"
    stops = [s for s in p.state()["slots"] if s.get("place")]
    paid = [s for s in stops if s["kind"] == "attraction" and (s.get("cost_pp") or 0) >= CL.TICKET_MIN]
    assert len([i for i in items if i["kind"] == "tickets"]) == len(paid)


def test_ticks_survive_plan_changes_and_sync(store):
    acc = account()
    p = planned(acc)
    items = p.checklist()
    hotel = next(i for i in items if i["kind"] == "hotel")
    p.set_check(hotel["id"], True)
    p.ask("swap dinner for something different")  # the plan changes: the hotel tick stays
    assert next(i for i in p.checklist() if i["id"] == hotel["id"])["done"]
    other = Planner(A.sign_in("jordan", PW)[2])  # another device: the tick is saved with the trip
    assert other.open_trip(other.trips[0]["id"])
    assert next(i for i in other.checklist() if i["id"] == hotel["id"])["done"]


def test_booked_reservation_ticks_itself(store):
    p = planned()
    table = next((i for i in p.checklist() if i["kind"] == "table"), None)
    if not table:
        pytest.skip("this plan's dinner doesn't take reservations")
    n = next(n for n, s in enumerate([s for s in p.state()["slots"] if s.get("place")], 1)
             if s["place"]["id"] == table["place_id"])
    p.ask(f"book stop {n}")
    p.ask("yes")
    p.ask(f"mark {table['text'].replace('Reserve a table at ', '')} booked")
    assert next(i for i in p.checklist() if i["id"] == table["id"])["done"]
