"""Accounts: sign-up, sign-in, password safety, 'keep me signed in', and memory/trips saved per account."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("AZ_OFFLINE", "1")

import pytest  # noqa: E402

from azmulti import accounts as A  # noqa: E402
from azmulti.graph import Planner  # noqa: E402
from azmulti.store import LocalStore, set_store  # noqa: E402


@pytest.fixture
def store(tmp_path):
    s = LocalStore(tmp_path)
    set_store(s)
    A._misses.clear()
    yield s
    set_store(LocalStore())  # back to the test-wide throwaway folder (conftest)


def files(root):
    return sorted(str(p.relative_to(root)).replace("\\", "/") for p in Path(root).rglob("*.json"))


def test_create_and_sign_in(store, tmp_path):
    ok, msg, acc = A.create_account("Jordan_T", "desert-sun-42", "desert-sun-42")
    assert ok and acc["username"] == "Jordan_T" and set(acc) == {"id", "username"}
    raw = (tmp_path / files(tmp_path)[0]).read_text()
    assert "desert-sun-42" not in raw and "Jordan" not in files(tmp_path)[0]  # no plain password; name not in file names
    assert A.sign_in("jordan_t", "desert-sun-42")[2]["id"] == acc["id"]  # usernames aren't case-sensitive
    ok, msg, _ = A.sign_in("Jordan_T", "wrong-password")
    assert not ok and msg == A.sign_in("nobody-here", "whatever1")[1]  # same message: doesn't reveal which was wrong


def test_sign_up_rules(store):
    assert not A.create_account("jo", "longenough1")[0]  # username too short
    assert not A.create_account("jordan", "short")[0]  # password too short
    assert not A.create_account("jordan", "jordan", "jordan")[0]
    assert not A.create_account("jordan", "desert-sun-42", "different-1")[0]  # confirmation mismatch
    assert A.create_account("jordan", "desert-sun-42")[0]
    ok, msg, _ = A.create_account("JORDAN", "another-pass-1")
    assert not ok and "taken" in msg


def test_too_many_wrong_passwords_locks_the_username(store):
    A.create_account("sam", "desert-sun-42")
    for _ in range(A.MAX_MISSES):
        assert not A.sign_in("sam", "nope-nope")[0]
    ok, msg, _ = A.sign_in("sam", "desert-sun-42")  # even the right password waits now
    assert not ok and "Too many tries" in msg


def test_keep_me_signed_in_token(store, tmp_path):
    _, _, acc = A.create_account("priya", "desert-sun-42")
    token = A.new_session(acc)
    assert A.resume_session(token)["id"] == acc["id"]
    assert not any(token in (tmp_path / f).read_text() for f in files(tmp_path))  # only a hash of the token is stored
    assert A.resume_session("not-a-real-token-1234567890") is None
    A.end_session(token)
    assert A.resume_session(token) is None


def test_guest_writes_nothing(store, tmp_path):
    p = Planner()
    p.ask("Saturday in Sedona for 2, hiking")
    p.ask("save stop 1")
    p.ask("remember I'm vegetarian")
    assert files(tmp_path) == []


def test_preferences_and_trips_follow_the_account(store, tmp_path):
    p = Planner()
    p.ask("Saturday in Sedona for 2, hiking")  # planned as a guest...
    _, _, acc = A.create_account("jordan", "desert-sun-42")
    p.sign_in(acc)  # ...then signed in: the trip moves to the account
    p.ask("remember I'm vegetarian")
    assert p.profile["memory_on"] and "vegetarian" in p.profile["dietary"]

    other_device = Planner(A.sign_in("jordan", "desert-sun-42")[2])
    assert "vegetarian" in other_device.profile["dietary"]
    assert [t["city"] for t in other_device.trips] == ["Sedona"]
    assert "state" not in other_device.trips[0]  # the full plan loads only when opened
    assert other_device.open_trip(other_device.trips[0]["id"])
    assert other_device.state()["request"]["city"] == "Sedona"
    out = other_device.ask("Saturday in Tempe for 2, lunch and a museum")
    assert "vegetarian" in out["request"]["dietary"]

    _, _, acc2 = A.create_account("someone-else", "desert-sun-42")
    assert Planner(acc2).trips == [] and not Planner(acc2).profile.get("dietary")  # accounts don't see each other

    p.sign_out()
    assert p.account is None and p.trips == [] and not p.profile.get("dietary")


def test_forget_and_delete_account(store, tmp_path):
    _, _, acc = A.create_account("jordan", "desert-sun-42")
    p = Planner(acc)
    p.ask("Saturday in Sedona for 2, hiking")
    p.ask("remember I'm vegetarian")
    p.forget()  # wipes what was learned and the trips; the account stays
    again = Planner(acc)
    assert again.trips == [] and not again.profile.get("dietary") and A.sign_in("jordan", "desert-sun-42")[0]
    p.ask("Saturday in Tucson for 2, zoo")
    token = A.new_session(acc)
    A.delete_account(acc, token)
    assert files(tmp_path) == [] and not A.sign_in("jordan", "desert-sun-42")[0]
    assert A.resume_session(token) is None


def test_change_password(store):
    _, _, acc = A.create_account("jordan", "desert-sun-42")
    assert not A.change_password(acc, "wrong-old-1", "new-pass-123", "new-pass-123")[0]
    assert A.change_password(acc, "desert-sun-42", "new-pass-123", "new-pass-123")[0]
    assert A.sign_in("jordan", "new-pass-123")[0] and not A.sign_in("jordan", "desert-sun-42")[0]


def test_storage_keys_cannot_escape(store):
    for bad in ("../x.json", "users/../../x.json", "users/x.txt"):
        with pytest.raises(ValueError):
            store.put(bad, {})
