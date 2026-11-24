"""Security helpers used across the app.

- redact():           remove API keys / bearer tokens from any text before it is logged or shown
- sanitize_markdown(): make LLM / web / data text safe to render (no raw HTML, no images that could
                       leak data to a third-party server, only http(s) links)
- safe_url():          allow only http(s) URLs in links and downloads
- clean_name():        short, plain display names (shared plans)
- RateLimiter:         per-session request limits (cost and abuse control)
"""
from __future__ import annotations

import html
import re
import time
from collections import deque

_SECRET = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|tvly-[A-Za-z0-9_\-]{8,}|Bearer\s+[A-Za-z0-9._\-]{8,}|"
                     r"(?:api[_-]?key|apikey|key)=([A-Za-z0-9._\-]{8,}))", re.I)
KEY_RE = re.compile(r"^sk-[A-Za-z0-9_\-]{20,200}$")


def redact(text: object) -> str:
    s = str(text)
    return _SECRET.sub(lambda m: "[redacted]" if not m.group(2) else m.group(0).replace(m.group(2), "[redacted]"), s)


def valid_key_format(key: str) -> bool:
    return bool(KEY_RE.match((key or "").strip()))


def mask_key(key: str | None) -> str:
    """Never reveal any characters of a key, not even the last few (screenshots / screen sharing)."""
    return "key added (hidden)" if key else ""


def safe_url(url: str | None) -> str | None:
    if not url or not isinstance(url, str):
        return None
    u = url.strip()
    return u if re.match(r"^https?://[^\s<>\"']+$", u, re.I) else None


_MD_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)]*)\)")
_MD_LINK = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)\s]*)(?:\s+\"[^\"]*\")?\)")
_HTML_TAG = re.compile(r"</?[A-Za-z][^>]{0,500}>")


def sanitize_markdown(text: str | None) -> str:
    """Render-safe Markdown: images become plain text (a hidden image URL can exfiltrate chat data),
    HTML tags are removed, and links keep only http(s) targets."""
    if not text:
        return ""
    s = str(text)
    s = _MD_IMAGE.sub(lambda m: m.group(1) or "", s)
    s = _HTML_TAG.sub("", s)
    s = _MD_LINK.sub(lambda m: f"[{m.group(1)}]({m.group(2)})" if safe_url(m.group(2)) else m.group(1), s)
    s = re.sub(r"(?i)\b(javascript|data|vbscript):", "", s)
    return redact(s)


def clean_name(name: str | None, limit: int = 30) -> str:
    s = re.sub(r"[^\w .'-]", "", (name or "").strip())[:limit].strip()
    return s or "someone"


def esc(s: object) -> str:
    return html.escape(str(s or ""), quote=True)


class RateLimiter:
    """At most `limit` events per `window` seconds (per browser session)."""

    def __init__(self, limit: int, window: float):
        self.limit, self.window, self.events = limit, window, deque()

    def allow(self) -> bool:
        now = time.time()
        while self.events and now - self.events[0] > self.window:
            self.events.popleft()
        if len(self.events) >= self.limit:
            return False
        self.events.append(now)
        return True

    def retry_in(self) -> int:
        return int(self.window - (time.time() - self.events[0])) + 1 if self.events else 0
