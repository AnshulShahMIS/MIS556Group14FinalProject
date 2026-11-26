"""Accounts: email sign-up with a 6-digit code, sign-in, password reset, 'keep me signed in', memory and trips saved
per account, and the emailed trip confirmation. Emails go to a throwaway outbox folder (nothing is sent)."""
import email
import os
import re
import sys
from email import policy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("AZ_OFFLINE", "1")

import pytest  # noqa: E402

from azmulti import accounts as A  # noqa: E402
from azmulti import mailer  # noqa: E402
from azmulti.graph import Planner  # noqa: E402
from azmulti.store import LocalStore, set_store  # noqa: E402

PW = "desert-sun-42"


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = LocalStore(tmp_path / "store")
    set_store(s)
    monkeypatch.setattr(mailer, "OUTBOX", tmp_path / "outbox")
    monkeypatch.setattr(A, "RESEND_SECONDS", 0)
    monkeypatch.setattr(A, "_mail_live", lambda: True)  # behave as if Gmail is set up (messages land in the outbox)
    A._misses.clear()
    yield s
    set_store(LocalStore())  # back to the test-wide throwaway folder (conftest)


def files(root):
    return sorted(str(p.relative_to(root)).replace("\\", "/") for p in Path(root).rglob("*.json"))


def last_email():
    msg = email.message_from_bytes(mailer.outbox_messages()[-1].read_bytes(), policy=policy.default)
    return msg, msg.get_body(("plain",)).get_content()


def code_from_email():
    return re.search(r"\b(\d{6})\b", last_email()[1]).group(1)


def signup(addr="jordan@example.com", name="Jordan"):
    ok, msg, pending = A.start_signup(addr, PW, PW, name)
    assert ok, msg
    ok, msg, acc = A.finish_signup(pending, code_from_email())
    assert ok, msg
    return acc


def test_sign_up_needs_the_emailed_code(store, tmp_path):
    ok, msg, pending = A.start_signup("Jordan@Example.com", PW, PW, "Jordan")
    assert ok and "jordan@example.com" in msg
    m, text = last_email()
    assert m["To"] == "jordan@example.com" and "Your Saguaro code" in m["Subject"]
    assert files(tmp_path / "store") == []  # nothing saved until the code is entered
    assert PW not in str(pending) and code_from_email() not in str(pending)  # only hashes kept, even in the session
    ok, msg, _ = A.finish_signup(pending, "000000" if code_from_email() != "000000" else "111111")
    assert not ok and "isn't right" in msg
    ok, msg, acc = A.finish_signup(pending, code_from_email())
    assert ok and acc["email"] == "jordan@example.com" and acc["username"] == "Jordan"
    saved = (tmp_path / "store" / files(tmp_path / "store")[0]).read_text()
    assert PW not in saved and "jordan@example.com" not in files(tmp_path / "store")[0]  # no password; email not in file names


def test_wrong_codes_run_out(store):
    _, _, pending = A.start_signup("sam@example.com", PW, PW)
    for _ in range(A.CODE_TRIES):
        A.finish_signup(pending, "999999" if code_from_email() != "999999" else "888888")
    ok, msg, _ = A.finish_signup(pending, code_from_email())  # even the right code is refused now
    assert not ok and "Too many" in msg
    assert A.resend_code(pending)[0]
    assert A.finish_signup(pending, code_from_email())[0]


def test_sign_up_rules(store):
    assert not A.start_signup("not-an-email", PW, PW)[0]
    assert not A.start_signup("a@b.com", "short", "short")[0]
    assert not A.start_signup("jordan@example.com", "jordan@example.com", "jordan@example.com")[0]
    assert not A.start_signup("a@b.com", PW, "different-123")[0]
    signup()
    ok, msg, _ = A.start_signup("JORDAN@example.com", PW, PW)
    assert not ok and "already an account" in msg


def test_sign_in(store):
    acc = signup()
    assert A.sign_in("JORDAN@example.com ", PW)[2]["id"] == acc["id"]  # emails aren't case-sensitive
    ok, msg, _ = A.sign_in("jordan@example.com", "wrong-password")
    assert not ok and msg == A.sign_in("nobody@example.com", "whatever1")[1]  # doesn't reveal which was wrong


def test_too_many_wrong_passwords_locks_the_email(store):
    signup("sam@example.com")
    for _ in range(A.MAX_MISSES):
        assert not A.sign_in("sam@example.com", "nope-nope")[0]
    ok, msg, _ = A.sign_in("sam@example.com", PW)
    assert not ok and "Too many tries" in msg


def test_forgot_password(store):
    signup()
    n = len(mailer.outbox_messages())
    ok, msg_known, pending = A.start_reset("jordan@example.com")
    assert ok and len(mailer.outbox_messages()) == n + 1
    ok, msg_unknown, fake = A.start_reset("nobody@example.com")
    assert ok and msg_unknown == msg_known and len(mailer.outbox_messages()) == n + 1  # same reply, no email sent
    assert not A.finish_reset(fake, "123456", "new-pass-123", "new-pass-123")[0]
    assert A.finish_reset(pending, code_from_email(), "new-pass-123", "new-pass-123")[0]
    assert A.sign_in("jordan@example.com", "new-pass-123")[0] and not A.sign_in("jordan@example.com", PW)[0]


def test_change_password(store):
    acc = signup()
    assert not A.change_password(acc, "wrong-old-1", "new-pass-123", "new-pass-123")[0]
    assert A.change_password(acc, PW, "new-pass-123", "new-pass-123")[0]
    assert A.sign_in("jordan@example.com", "new-pass-123")[0]


def test_keep_me_signed_in_token(store, tmp_path):
    acc = signup()
    token = A.new_session(acc)
    assert A.resume_session(token)["id"] == acc["id"]
    assert not any(token in (tmp_path / "store" / f).read_text() for f in files(tmp_path / "store"))  # only a hash
    assert A.resume_session("not-a-real-token-1234567890") is None
    A.end_session(token)
    assert A.resume_session(token) is None


def test_guest_writes_nothing(store, tmp_path):
    p = Planner()
    p.ask("Saturday in Sedona for 2, hiking")
    p.ask("save stop 1")
    p.ask("remember I'm vegetarian")
    assert files(tmp_path / "store") == []
    ok, msg = p.confirm_trip()
    assert not ok and "Sign in" in msg


def test_preferences_and_trips_follow_the_account(store):
    p = Planner()
    p.ask("Saturday in Sedona for 2, hiking")  # planned as a guest...
    acc = signup()
    p.sign_in(acc)  # ...then signed in: the trip moves to the account
    p.ask("remember I'm vegetarian")
    assert p.profile["memory_on"] and "vegetarian" in p.profile["dietary"]

    other_device = Planner(A.sign_in("jordan@example.com", PW)[2])
    assert "vegetarian" in other_device.profile["dietary"]
    assert [t["city"] for t in other_device.trips] == ["Sedona"]
    assert "state" not in other_device.trips[0]  # the full plan loads only when opened
    assert other_device.open_trip(other_device.trips[0]["id"])
    assert other_device.state()["request"]["city"] == "Sedona"
    out = other_device.ask("Saturday in Tempe for 2, lunch and a museum")
    assert "vegetarian" in out["request"]["dietary"]

    acc2 = signup("someone@example.com", "")
    assert Planner(acc2).trips == [] and not Planner(acc2).profile.get("dietary")  # accounts don't see each other

    p.sign_out()
    assert p.account is None and p.trips == [] and not p.profile.get("dietary")


def test_confirm_trip_emails_the_itinerary(store):
    acc = signup()
    p = Planner(acc)
    p.ask("Saturday in Sedona for 2, hiking and Italian food")
    assert p.trip_confirmation() is None
    ok, msg = p.confirm_trip()
    assert ok, msg
    m, text = last_email()
    stops = [s["place"]["name"] for s in p.state()["slots"] if s.get("place")]
    assert m["To"] == "jordan@example.com" and m["Subject"].startswith("Trip confirmed: Sedona")
    assert all(name in text for name in stops) and "Hi Jordan" in text
    assert any(part.get_filename() == "saguaro-trip.ics" for part in m.iter_attachments())
    assert p.trip_confirmation() == {"at": p.trip_confirmation()["at"], "changed": False}
    p.ask("swap dinner for something different")
    assert p.trip_confirmation()["changed"]  # the plan changed since it was confirmed
    again = Planner(acc)
    assert again.trips[0].get("confirmed_at")  # the Confirmed mark is saved with the account


def test_forget_and_delete_account(store, tmp_path):
    acc = signup()
    p = Planner(acc)
    p.ask("Saturday in Sedona for 2, hiking")
    p.ask("remember I'm vegetarian")
    p.forget()  # wipes what was learned and the trips; the account stays
    again = Planner(acc)
    assert again.trips == [] and not again.profile.get("dietary") and A.sign_in("jordan@example.com", PW)[0]
    p.ask("Saturday in Tucson for 2, zoo")
    token = A.new_session(acc)
    A.delete_account(acc, token)
    assert files(tmp_path / "store") == [] and not A.sign_in("jordan@example.com", PW)[0]
    assert A.resume_session(token) is None


def test_without_email_set_up_the_code_is_shown(store, monkeypatch):
    monkeypatch.setattr(A, "_mail_live", lambda: False)
    ok, msg, pending = A.start_signup("kai@example.com", PW, PW)
    code = re.search(r"\b(\d{6})\b", msg).group(1)
    assert ok and "isn't set up" in msg and A.finish_signup(pending, code)[0]


def test_storage_keys_cannot_escape(store):
    for bad in ("../x.json", "users/../../x.json", "users/x.txt"):
        with pytest.raises(ValueError):
            store.put(bad, {})
