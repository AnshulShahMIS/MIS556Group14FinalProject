"""Pre-trip checklist: items built from the plan and place data, ticks saved with the trip, and email reminders
(scripts/send_reminders.py) sent once, only for due items, only when turned on."""
import email
import importlib.util
import os
import re
import sys
from datetime import date, timedelta
from email import policy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("AZ_OFFLINE", "1")

import pytest  # noqa: E402

from azmulti import accounts as A  # noqa: E402
from azmulti import checklist as CL  # noqa: E402
from azmulti import mailer  # noqa: E402
from azmulti.graph import Planner  # noqa: E402
from azmulti.store import LocalStore, set_store  # noqa: E402

PW = "desert-sun-42"
spec = importlib.util.spec_from_file_location("send_reminders", ROOT / "scripts" / "send_reminders.py")
reminders = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reminders)


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = LocalStore(tmp_path / "store")
    set_store(s)
    monkeypatch.setattr(mailer, "OUTBOX", tmp_path / "outbox")
    monkeypatch.setattr(A, "_mail_live", lambda: True)
    monkeypatch.setattr(A, "RESEND_SECONDS", 0)
    yield s
    set_store(LocalStore())


def account(addr="jordan@example.com", name="Jordan"):
    ok, msg, pending = A.start_signup(addr, PW, PW, name)
    code = re.search(r"\b(\d{6})\b", email.message_from_bytes(mailer.outbox_messages()[-1].read_bytes(),
                                                              policy=policy.default).get_body(("plain",)).get_content()).group(1)
    return A.finish_signup(pending, code)[2]


def planned(acc=None, when="2027-03-12"):
    p = Planner(acc)
    p.ask(f"Trip to Scottsdale on {when} for 4 friends, hiking, then a nice steakhouse dinner and the Heard Museum, "
          "$150 each, and a hotel to stay overnight")
    return p


def test_items_come_from_the_plan(store):
    p = planned()
    items, reminders_on = p.checklist()
    kinds = {i["kind"] for i in items}
    assert {"hotel", "weather"} <= kinds and not reminders_on
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
    items, _ = p.checklist()
    hotel = next(i for i in items if i["kind"] == "hotel")
    p.set_check(hotel["id"], True)
    p.ask("swap dinner for something different")  # the plan changes: the hotel tick stays
    assert next(i for i in p.checklist()[0] if i["id"] == hotel["id"])["done"]
    p.set_reminders(True)
    other = Planner(A.sign_in("jordan@example.com", PW)[2])
    assert other.open_trip(other.trips[0]["id"])
    items, on = other.checklist()
    assert on and next(i for i in items if i["id"] == hotel["id"])["done"]


def test_booked_reservation_ticks_itself(store):
    p = planned()
    table = next((i for i in p.checklist()[0] if i["kind"] == "table"), None)
    if not table:
        pytest.skip("this plan's dinner doesn't take reservations")
    n = next(n for n, s in enumerate([s for s in p.state()["slots"] if s.get("place")], 1)
             if s["place"]["id"] == table["place_id"])
    p.ask(f"book stop {n}")
    p.ask("yes")
    p.ask(f"mark {table['text'].replace('Reserve a table at ', '')} booked")
    assert next(i for i in p.checklist()[0] if i["id"] == table["id"])["done"]


def test_reminders_are_sent_once_when_due(store):
    acc = account()
    p = planned(acc)
    trip_day = date(2027, 3, 12)
    assert reminders.run(today=trip_day - timedelta(days=30)) == []  # reminders not turned on yet
    ok, _ = p.confirm_trip(remind=True)
    assert ok
    n_before = len(mailer.outbox_messages())
    assert reminders.run(today=trip_day - timedelta(days=30)) == []  # nothing due a month out
    sent = reminders.run(today=date(2027, 2, 19))  # the hotel is due 3 weeks before
    assert len(sent) == 1 and any("Book your room" in x for x in sent[0]["items"])
    assert len(mailer.outbox_messages()) == n_before + 1
    msg = email.message_from_bytes(mailer.outbox_messages()[-1].read_bytes(), policy=policy.default)
    assert msg["To"] == "jordan@example.com" and msg["Subject"].startswith("Reminder:")
    assert reminders.run(today=date(2027, 2, 19)) == []  # never the same reminder twice
    p.set_reminders(False)
    assert reminders.run(today=trip_day - timedelta(days=1)) == []  # turned off: nothing more
    p.set_reminders(True)
    later = reminders.run(today=trip_day - timedelta(days=1))
    assert later and not any("Book your room" in x for x in later[0]["items"])
    assert reminders.run(today=trip_day + timedelta(days=1)) == []  # the trip has started: no reminders


def test_email_checklist(store):
    acc = account()
    p = planned(acc)
    ok, _ = p.email_checklist()
    assert ok
    msg = email.message_from_bytes(mailer.outbox_messages()[-1].read_bytes(), policy=policy.default)
    text = msg.get_body(("plain",)).get_content()
    assert msg["Subject"].startswith("Your checklist") and "Book your room" in text
    assert not Planner().email_checklist()[0]  # guests: sign in first
