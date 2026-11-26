"""GitHubStore against a pretend GitHub contents API (no network): save, read, list, delete, version conflicts, and
seeing another writer's changes (the daily reminder job) once the cache is stale."""
import base64
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("AZ_OFFLINE", "1")

from azmulti.store import GitHubStore  # noqa: E402


class FakeGitHub:
    def __init__(self):
        self.files: dict[str, tuple[str, str]] = {}  # path -> (sha, base64 content)
        self.n = 0

    def call(self, method, path, body=None):
        if method == "GET":
            if path in self.files:
                sha, content = self.files[path]
                return 200, {"sha": sha, "content": content}
            kids = [{"type": "file", "name": p.split("/")[-1]} for p in self.files if p.rsplit("/", 1)[0] == path]
            return (200, kids) if kids else (404, {})
        if method == "PUT":
            if path in self.files and body.get("sha") != self.files[path][0]:
                return 409, {}
            self.n += 1
            self.files[path] = (f"sha{self.n}", body["content"])
            return 201, {"content": {"sha": f"sha{self.n}"}}
        if method == "DELETE":
            if path not in self.files or body.get("sha") != self.files[path][0]:
                return 409, {}
            del self.files[path]
            return 200, {}

    def write_directly(self, path, value):  # someone else (e.g. the reminder job) changes a file
        self.n += 1
        self.files[path] = (f"sha{self.n}", base64.b64encode(json.dumps(value).encode()).decode())


def make():
    gh = FakeGitHub()
    s = GitHubStore("me/saguaro-data", "token-not-real")
    s._call = gh.call
    return s, gh


def test_put_get_list_delete():
    s, gh = make()
    s.put("users/a.json", {"x": 1})
    s.put("users/b.json", {"x": 2})
    s.flush()
    assert s.get("users/a.json") == {"x": 1} and s.list("users") == ["users/a.json", "users/b.json"]
    s.put("users/a.json", {"x": 3})  # an update needs the current version (sha): handled
    s.delete("users/b.json")
    s.flush()
    fresh = GitHubStore("me/saguaro-data", "t")
    fresh._call = gh.call
    assert fresh.get("users/a.json") == {"x": 3} and fresh.get("users/b.json") is None
    assert fresh.list("users") == ["users/a.json"] and fresh.list("nothing") == []


def test_unchanged_writes_are_skipped():
    s, gh = make()
    s.put("trips/a.json", [1, 2])
    s.flush()
    n = gh.n
    s.put("trips/a.json", [1, 2])
    s.flush()
    assert gh.n == n


def test_sees_other_writers_after_the_cache_expires():
    s, gh = make()
    s.put("trips/a.json", [{"id": "t1"}])
    s.flush()
    gh.write_directly("trips/a.json", [{"id": "t1", "reminded": ["x"]}])
    assert s.get("trips/a.json") == [{"id": "t1"}]  # still the cached copy
    s._read_at["trips/a.json"] = time.time() - s.CACHE_SECONDS - 1
    assert s.get("trips/a.json") == [{"id": "t1", "reminded": ["x"]}]
    s.put("trips/a.json", [{"id": "t1", "reminded": ["x"], "reminders": True}])  # and can write on top of it
    s.flush()
    assert json.loads(base64.b64decode(gh.files["trips/a.json"][1]))[0]["reminders"] is True


def test_a_queued_write_wins_over_a_stale_read():
    s, gh = make()
    s._pending["users/a.json"] = 1  # a write is still queued...
    s._cache["users/a.json"] = {"new": True}
    s._read_at["users/a.json"] = 0
    gh.write_directly("users/a.json", {"old": True})
    assert s.get("users/a.json") == {"new": True}  # ...so the app keeps its newer copy
