"""HTTP helpers aligned with SubBatch browser request style."""

from __future__ import annotations

import http.cookiejar
import json
import os
import re
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

# SubBatch-like desktop Chrome UA (background.js uses similar Chrome UA)
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Match SubBatch fetchWithHeaders extras
DEFAULT_HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Origin": "https://www.bilibili.com",
    "Referer": "https://www.bilibili.com/",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Connection": "keep-alive",
    "X-Wbi-UA": "Win32.Chrome.120.0.0.0",
}


class HttpError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


class RetryableError(RuntimeError):
    """Network / rate-limit / temporary API failures."""


_RISK_MARKERS = (
    "-352",
    "-412",
    "-799",
    "风控",
    "请求被拦截",
    "too many requests",
    "rate limit",
    "precondition failed",
)


def is_risk_text(text: str) -> bool:
    low = (text or "").lower()
    raw = text or ""
    if re.search(r'"code"\s*:\s*-352\b', raw) or "-352" in raw:
        return True
    if re.search(r'"code"\s*:\s*-412\b', raw) or "-412" in raw:
        return True
    if re.search(r'"code"\s*:\s*-799\b', raw):
        return True
    return any(m.lower() in low or m in raw for m in _RISK_MARKERS)


def cookie_summary(cookie: str) -> dict[str, Any]:
    """Non-secret diagnostic of cookie completeness (SubBatch-style)."""
    value = (cookie or "").strip()
    return {
        "length": len(value),
        "has_SESSDATA": "SESSDATA=" in value,
        "has_bili_jct": "bili_jct=" in value,
        "has_DedeUserID": "DedeUserID=" in value,
        "has_buvid3": "buvid3=" in value.lower() or "buvid3=" in value,
        "has_buvid4": "buvid4=" in value.lower() or "buvid4=" in value,
        "item_count": len([p for p in value.split(";") if p.strip()]) if value else 0,
    }


def cookie_ready_for_batch(cookie: str) -> bool:
    """True when cookie looks like a logged-in SubBatch session."""
    s = cookie_summary(cookie)
    return bool(s["has_SESSDATA"])


def get_cookie(explicit: str | None = None) -> str:
    if explicit is not None and str(explicit).strip():
        return str(explicit).strip()
    return (os.environ.get("BILI_COOKIE") or "").strip()


def _build_headers(cookie: str = "", *, extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = dict(DEFAULT_HEADERS)
    if cookie:
        headers["Cookie"] = cookie
    if extra:
        headers.update(extra)
    return headers


class HttpClient:
    """
    urllib client with keep-alive via shared opener + optional cookie jar.

    Closer to SubBatch continuous browser session than one-off urlopen.
    """

    def __init__(self, cookie: str = "", *, timeout: float = 30.0):
        self.cookie = (cookie or "").strip()
        self.timeout = timeout
        self.jar = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )
        # Seed jar from cookie string if present (best-effort)
        if self.cookie:
            self._seed_cookie_string(self.cookie)

    def _seed_cookie_string(self, cookie: str) -> None:
        # urllib CookieJar doesn't parse Cookie header easily; we still send
        # the full Cookie header manually for SESSDATA etc.
        self.cookie = cookie.strip()

    def get_json(self, url: str, cookie: str = "", timeout: float | None = None) -> Any:
        ck = (cookie if cookie is not None and str(cookie).strip() else self.cookie) or ""
        headers = _build_headers(ck)
        # Subtitle CDN may not need all API headers but keep Referer
        host = urlparse(url).netloc or ""
        if "aisubtitle" in host or "hdslb.com" in host:
            headers = {
                "User-Agent": UA,
                "Referer": "https://www.bilibili.com/",
                "Origin": "https://www.bilibili.com",
                "Accept": "*/*",
            }
            if ck:
                headers["Cookie"] = ck
        req = urllib.request.Request(url, headers=headers)
        to = self.timeout if timeout is None else timeout
        try:
            with self._opener.open(req, timeout=to) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:300]
            err = HttpError(f"HTTP {e.code} for {url}: {body}", status=e.code)
            if e.code in (412, 429, 502, 503, 504) or is_risk_text(body):
                raise RetryableError(str(err)) from e
            raise err from e
        except urllib.error.URLError as e:
            raise RetryableError(f"URL error for {url}: {e.reason}") from e
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise HttpError(f"invalid JSON from {url}: {raw[:200]}") from e
        # API-level risk codes in body (leave other codes to callers)
        if isinstance(data, dict) and data.get("code") in (-352, -412, -799, -509):
            raise RetryableError(
                f"api code={data.get('code')} {data.get('message')} "
                f"for {urlparse(url).path}"
            )
        return data


# Module-level default client (lazy cookie from env)
_default_client: HttpClient | None = None


def _default() -> HttpClient:
    global _default_client
    if _default_client is None:
        _default_client = HttpClient(get_cookie())
    return _default_client


def http_json(url: str, cookie: str = "", timeout: float = 30.0) -> Any:
    """Backward-compatible function entry (tests inject fakes instead)."""
    client = _default()
    if cookie and cookie != client.cookie:
        # one-off with different cookie without rebuilding global
        return HttpClient(cookie, timeout=timeout).get_json(url, cookie=cookie, timeout=timeout)
    return client.get_json(url, cookie=cookie or client.cookie, timeout=timeout)


def format_subtitle_url(u: str | None) -> str:
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
