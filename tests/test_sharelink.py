"""View-only share links: anyone with the link sees the latest version of the trip without signing in; the owner
can stop sharing; private details aren't included."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("AZ_OFFLINE", "1")

import pytest  # noqa: E402

from azmulti import accounts as A  # noqa: E402
from azmulti import sharelink  # noqa: E402
from azmulti.graph import Planner  # noqa: E402
from azmulti.store import LocalStore, set_store  # noqa: E402


@pytest.fixture
def store(tmp_path):
    s = LocalStore(tmp_path / "store")
    set_store(s)
    A._misses.clear()
    yield s
    set_store(LocalStore())


def planner():
    p = Planner(A.create_account("jordan", "desert-sun-42", "desert-sun-42")[2])
    p.ask("Saturday in Sedona for 2, hiking and Italian food")
    return p


def test_link_shows_the_trip_and_follows_changes(store):
    p = planner()
    assert p.shared_token() is None
    tok = p.share_link()
    assert len(tok) >= 20 and p.share_link() == tok  # one link per trip
    snap = sharelink.load(tok)
    names = [s["place"]["name"] for s in snap["state"]["slots"] if s.get("place")]
    assert snap["owner"] == "jordan" and snap["title"] == "Sedona" and names
    p.ask("swap dinner for something different")  # the owner changes the trip...
    new = [s["place"]["name"] for s in sharelink.load(tok)["state"]["slots"] if s.get("place")]
    assert new == [s["place"]["name"] for s in p.state()["slots"] if s.get("place")]  # ...the link shows it


def test_nothing_private_is_shared(store):
    p = planner()
    n = next(n for n, s in enumerate([s for s in p.state()["slots"] if s.get("place")], 1) if s["kind"] == "restaurant")
    p.ask(f"book stop {n}")
    p.ask("yes")
    name = next(r["name"] for r in p.state()["reservations"])
    p.ask(f"mark {name} booked confirmation ABC123")
    p.ask("remember I'm vegetarian")
    tok = p.share_link()
    raw = (store.root / f"shared-trips/{tok}.json").read_text()
    i = raw.find("ABC123")
    assert i < 0, raw[max(0, i - 300):i + 40]  # the confirmation number stays private
    snap = sharelink.load(tok)
    assert "profile" not in snap and "trace" not in snap["state"] and "answer" not in snap["state"]
    assert all("confirmation" not in r for r in snap["state"].get("reservations") or [])
    assert "dietary" not in snap["state"]["request"]


def test_stop_sharing_and_deleting(store):
    p = planner()
    tok = p.share_link()
    p.stop_sharing()
    assert sharelink.load(tok) is None and p.shared_token() is None
    tok2 = p.share_link()
    assert tok2 != tok and sharelink.load(tok2)
    p.delete_trip(p.trip_id)
    assert sharelink.load(tok2) is None  # deleting the trip turns its link off


def test_deleting_the_account_turns_links_off(store):
    p = planner()
    tok = p.share_link()
    A.delete_account(p.account)
    assert sharelink.load(tok) is None


def test_bad_tokens(store):
    assert sharelink.load(None) is None and sharelink.load("../../users/x") is None and sharelink.load("short") is None
