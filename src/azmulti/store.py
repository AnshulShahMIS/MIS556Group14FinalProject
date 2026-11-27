"""Where accounts, saved preferences and trips are kept.

Two backends, same small key/value API (`get`, `put`, `delete`; keys look like "users/ab12.json"):

- GitHubStore: JSON files in a PRIVATE GitHub repository, written through GitHub's contents API. Used when
  AZ_STORE_REPO ("owner/name") and AZ_STORE_TOKEN (a fine-grained token with read/write access to that one repo's
  contents) are set, e.g. in Streamlit secrets. Survives app restarts and redeploys, which Streamlit Cloud's own disk
  does not. Writes go through one background thread, in order, so planning never waits on GitHub.
- LocalStore: files under data/accounts/ (or AZ_STORE_DIR). Used everywhere else: running locally and in tests.

Nothing secret is stored in plain text: passwords only as salted hashes (see accounts.py), session tokens only as
hashes. The token itself is read from the environment and never written, logged or shown.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import queue
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from .config import DATA_DIR

log = logging.getLogger("store")


def _copy(value):
    return json.loads(json.dumps(value, default=str)) if value is not None else None


def _clean_key(key: str) -> str:
    key = key.strip("/")
    if ".." in key or key.startswith(("/", "\\")) or not key.endswith(".json"):
        raise ValueError("bad storage key")
    return key


class LocalStore:
    kind = "local"

    def __init__(self, root: Path | str | None = None):
        self.root = Path(root or os.getenv("AZ_STORE_DIR") or DATA_DIR / "accounts")

    def _path(self, key: str) -> Path:
        return self.root / _clean_key(key)

    def get(self, key: str):
        p = self._path(key)
        try:
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
        except Exception:
            return None

    def put(self, key: str, value) -> None:
        p = self._path(key)
        text = json.dumps(value, default=str)
        if p.exists() and p.read_text(encoding="utf-8") == text:
            return  # unchanged
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(p)

    def delete(self, key: str) -> None:
        p = self._path(key)
        if p.exists():
            p.unlink()

    def list(self, folder: str) -> list[str]:
        """Keys of the .json files directly in a folder, e.g. list("trips") -> ["trips/ab12.json", ...]."""
        d = self.root / folder.strip("/")
        return sorted(f"{folder.strip('/')}/{f.name}" for f in d.glob("*.json")) if d.is_dir() else []

    def flush(self, timeout: float = 0) -> None:  # same API as GitHubStore
        return None


class GitHubStore:
    """JSON files in a private GitHub repo. Reads are cached for a couple of minutes (other writers, like the daily
    reminder job, may change a file); writes are queued and sent in order."""
    kind = "github"
    API = "https://api.github.com"
    CACHE_SECONDS = 120

    def __init__(self, repo: str, token: str, branch: str = "main"):
        self.repo, self._token, self.branch = repo, token, branch
        self._cache: dict[str, object] = {}
        self._read_at: dict[str, float] = {}
        self._pending: dict[str, int] = {}  # queued writes per file: the cache is newer than GitHub until they land
        self._sha: dict[str, str | None] = {}
        self._lock = threading.Lock()
        self._q: queue.Queue = queue.Queue()
        threading.Thread(target=self._writer, daemon=True, name="github-store").start()

    # ---- HTTP
    def _call(self, method: str, path: str, body: dict | None = None):
        req = urllib.request.Request(
            f"{self.API}/repos/{self.repo}/contents/{path}" + (f"?ref={self.branch}" if method == "GET" else ""),
            data=json.dumps(body).encode() if body is not None else None, method=method,
            headers={"Authorization": f"Bearer {self._token}", "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "saguaro-app",
                     **({"Content-Type": "application/json"} if body is not None else {})})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, {}
        except Exception as e:  # network trouble: report without details (never the token)
            log.warning("github store %s %s failed: %s", method, path, type(e).__name__)
            return 0, {}

    def _fetch(self, key: str):
        status, data = self._call("GET", key)
        if status == 200 and data.get("content") is not None:
            self._sha[key] = data.get("sha")
            try:
                return json.loads(base64.b64decode(data["content"]).decode("utf-8"))
            except Exception:
                return None
        if status == 404:
            self._sha[key] = None
        return None

    # ---- API
    def get(self, key: str):
        key = _clean_key(key)
        with self._lock:
            fresh = time.time() - self._read_at.get(key, 0) < self.CACHE_SECONDS
            if key in self._cache and (fresh or self._pending.get(key)):
                return _copy(self._cache[key])  # a copy: callers may change what they get
        value = self._fetch(key)
        with self._lock:
            if not self._pending.get(key):  # a write queued meanwhile is newer than what was just read
                self._cache[key] = value
                self._read_at[key] = time.time()
            return _copy(self._cache.get(key))

    def put(self, key: str, value) -> None:
        key = _clean_key(key)
        text = json.dumps(value, default=str, indent=1)
        with self._lock:
            if key in self._cache and json.dumps(self._cache[key], default=str, indent=1) == text:
                return  # unchanged: no commit
            self._cache[key] = json.loads(text)
            self._pending[key] = self._pending.get(key, 0) + 1
        self._q.put(("put", key, text))

    def delete(self, key: str) -> None:
        key = _clean_key(key)
        with self._lock:
            self._cache[key] = None
            self._pending[key] = self._pending.get(key, 0) + 1
        self._q.put(("delete", key, None))

    def list(self, folder: str) -> list[str]:
        """Keys of the .json files directly in a folder of the repo."""
        folder = folder.strip("/")
        if ".." in folder:
            raise ValueError("bad folder")
        status, data = self._call("GET", folder)
        if status != 200 or not isinstance(data, list):
            return []
        return sorted(f"{folder}/{x['name']}" for x in data if x.get("type") == "file" and x.get("name", "").endswith(".json"))

    def flush(self, timeout: float = 10) -> None:
        """Wait (up to `timeout` s) until queued writes are sent. Used before sign-out and in tests."""
        end = time.time() + timeout
        while self._q.unfinished_tasks and time.time() < end:
            time.sleep(0.05)

    def _writer(self) -> None:
        while True:
            op, key, text = self._q.get()
            try:
                for _ in range(3):  # a stale file version (someone else wrote it): fetch the new one and retry
                    if key not in self._sha:
                        self._fetch(key)
                    sha = self._sha.get(key)
                    if op == "delete":
                        if not sha:
                            break
                        status, _ = self._call("DELETE", key, {"message": "delete " + key.split("/")[0],
                                                               "sha": sha, "branch": self.branch})
                        if status in (200, 404):
                            self._sha[key] = None
                            break
                    else:
                        body = {"message": "save " + key.split("/")[0], "branch": self.branch,
                                "content": base64.b64encode(text.encode("utf-8")).decode()}
                        if sha:
                            body["sha"] = sha
                        status, data = self._call("PUT", key, body)
                        if status in (200, 201):
                            self._sha[key] = (data.get("content") or {}).get("sha")
                            break
                    if status in (409, 422):
                        self._sha.pop(key, None)
                        continue
                    log.warning("github store %s %s: HTTP %s", op, key.split("/")[0], status)
                    break
            finally:
                with self._lock:
                    self._pending[key] = max(0, self._pending.get(key, 1) - 1)
                    self._read_at[key] = time.time()
                self._q.task_done()


_STORE = None
_STORE_LOCK = threading.Lock()


def get_store():
    """The store for this server: the private GitHub repo when configured, else local files."""
    global _STORE
    with _STORE_LOCK:
        repo, token = os.getenv("AZ_STORE_REPO", "").strip(), os.getenv("AZ_STORE_TOKEN", "").strip()
        wanted = bool(repo and token and os.getenv("AZ_OFFLINE", "0") != "1")
        # the secrets may be added (or changed) while the app is running: switch over then, without a restart
        if _STORE is None or (wanted and (not isinstance(_STORE, GitHubStore) or (_STORE.repo, _STORE._token) != (repo, token))):
            _STORE = GitHubStore(repo, token, os.getenv("AZ_STORE_BRANCH", "main")) if wanted else LocalStore()
        return _STORE


def set_store(store) -> None:
    """Swap the store (tests)."""
    global _STORE
    with _STORE_LOCK:
        _STORE = store
