"""HTTP helpers."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class HttpError(RuntimeError):
    """HTTP or JSON failure with optional status code."""

    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


def get_cookie(explicit: str | None = None) -> str:
    if explicit is not None and str(explicit).strip():
        return str(explicit).strip()
    return (os.environ.get("BILI_COOKIE") or "").strip()


def http_json(url: str, cookie: str = "", timeout: float = 30.0) -> Any:
    headers = {
        "User-Agent": UA,
        "Referer": "https://www.bilibili.com/",
        "Origin": "https://www.bilibili.com",
        "Accept": "application/json, text/plain, */*",
    }
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:300]
        raise HttpError(f"HTTP {e.code} for {url}: {body}", status=e.code) from e
    except urllib.error.URLError as e:
        raise HttpError(f"URL error for {url}: {e.reason}") from e
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise HttpError(f"invalid JSON from {url}: {raw[:200]}") from e


def format_subtitle_url(u: str | None) -> str:
    """Normalize relative / protocol-relative subtitle URLs to https."""
    if not u:
        return ""
    u = str(u).strip()
    if not u:
        return ""
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("http://"):
        return "https://" + u[len("http://") :]
    if not u.startswith("http"):
        return "https://" + u.lstrip("/")
    return u
