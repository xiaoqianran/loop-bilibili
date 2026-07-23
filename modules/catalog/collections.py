"""B 站空间「合集和系列」官方数据（非标题启发式）。

对应空间页 Tab: space.bilibili.com/{mid}/lists

API:
  - list:  /x/polymer/web-space/seasons_series_list
  - 合集:  /x/polymer/web-space/seasons_archives_list
  - 系列:  /x/series/archives
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

logger = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

HttpJson = Callable[[str], Any]


def _default_http_json(url: str, *, cookie: str = "", timeout: float = 30.0) -> Any:
    headers = {
        "User-Agent": UA,
        "Referer": "https://space.bilibili.com/",
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
        raise RuntimeError(f"HTTP {e.code} {url}: {body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"URL error {url}: {e.reason}") from e
    return json.loads(raw)


def _cookie() -> str:
    return (os.environ.get("BILI_COOKIE") or "").strip()


def _ts(ts: int | float | None) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return ""


@dataclass
class CollectionItem:
    """One official 合集 (season) or 系列 (series)."""

    kind: str  # season | series
    id: int
    name: str
    total: int = 0
    description: str = ""
    cover: str = ""
    archives: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _archive_to_video(a: dict[str, Any], *, collection_name: str, kind: str, cid: int) -> dict[str, Any]:
    bvid = str(a.get("bvid") or "")
    title = str(a.get("title") or "")
    pub = a.get("pubdate") or a.get("ctime")
    stat = a.get("stat") if isinstance(a.get("stat"), dict) else {}
    plays = stat.get("view") if stat else a.get("play") or a.get("plays")
    return {
        "title": title,
        "bvid": bvid,
        "url": f"https://www.bilibili.com/video/{bvid}" if bvid else "",
        "plays": plays,
        "likes": stat.get("like") if stat else a.get("likes"),
        "date": _ts(pub),
        "series": collection_name,
        "series_source": "official",
        "collection_kind": kind,
        "collection_id": cid,
        "number": None,
    }


def fetch_seasons_series_page(
    mid: str,
    page_num: int = 1,
    page_size: int = 20,
    *,
    http: HttpJson | None = None,
    cookie: str | None = None,
) -> dict[str, Any]:
    http = http or (lambda u: _default_http_json(u, cookie=cookie if cookie is not None else _cookie()))
    url = (
        "https://api.bilibili.com/x/polymer/web-space/seasons_series_list"
        f"?mid={mid}&page_num={page_num}&page_size={page_size}"
    )
    data = http(url)
    if data.get("code") != 0:
        raise RuntimeError(f"seasons_series_list code={data.get('code')} {data.get('message')}")
    return (data.get("data") or {}).get("items_lists") or {}


def fetch_season_archives(
    mid: str,
    season_id: int,
    *,
    http: HttpJson | None = None,
    cookie: str | None = None,
    page_size: int = 30,
    delay: float = 0.35,
) -> list[dict[str, Any]]:
    """All archives in a 合集 (season)."""
    http = http or (lambda u: _default_http_json(u, cookie=cookie if cookie is not None else _cookie()))
    out: list[dict[str, Any]] = []
    page = 1
    total = None
    while True:
        url = (
            "https://api.bilibili.com/x/polymer/web-space/seasons_archives_list"
            f"?mid={mid}&season_id={season_id}&page_num={page}&page_size={page_size}"
        )
        data = http(url)
        if data.get("code") != 0:
            raise RuntimeError(
                f"seasons_archives_list season={season_id} code={data.get('code')} {data.get('message')}"
            )
        body = data.get("data") or {}
        archives = body.get("archives") or []
        out.extend(archives)
        page_info = body.get("page") or {}
        if total is None:
            total = int(page_info.get("total") or 0)
        if not archives or len(out) >= total or len(archives) < page_size:
            break
        page += 1
        if delay > 0:
            time.sleep(delay)
    return out


def fetch_series_archives(
    mid: str,
    series_id: int,
    *,
    http: HttpJson | None = None,
    cookie: str | None = None,
    page_size: int = 30,
    delay: float = 0.35,
) -> list[dict[str, Any]]:
    """All archives in a 系列 (series)."""
    http = http or (lambda u: _default_http_json(u, cookie=cookie if cookie is not None else _cookie()))
    out: list[dict[str, Any]] = []
    page = 1
    total = None
    while True:
        url = (
            "https://api.bilibili.com/x/series/archives"
            f"?mid={mid}&series_id={series_id}&only_normal=true&sort=desc"
            f"&pn={page}&ps={page_size}"
        )
        data = http(url)
        if data.get("code") != 0:
            raise RuntimeError(
                f"series/archives series={series_id} code={data.get('code')} {data.get('message')}"
            )
        body = data.get("data") or {}
        archives = body.get("archives") or []
        out.extend(archives)
        page_info = body.get("page") or {}
        if total is None:
            total = int(page_info.get("total") or 0)
        if not archives or len(out) >= total or len(archives) < page_size:
            break
        page += 1
        if delay > 0:
            time.sleep(delay)
    return out


def fetch_all_collections(
    mid: str,
    *,
    http: HttpJson | None = None,
    cookie: str | None = None,
    delay: float = 0.35,
    log: Callable[[str], None] | None = None,
) -> list[CollectionItem]:
    """
    Full official 合集 + 系列 for a mid, with complete archive lists.
    """
    _log = log or (lambda m: None)
    cookie = cookie if cookie is not None else _cookie()
    http = http or (lambda u: _default_http_json(u, cookie=cookie))

    seasons_raw: list[dict] = []
    series_raw: list[dict] = []
    page = 1
    total_items = None
    while True:
        il = fetch_seasons_series_page(mid, page_num=page, page_size=20, http=http, cookie=cookie)
        seasons_raw.extend(il.get("seasons_list") or [])
        series_raw.extend(il.get("series_list") or [])
        page_info = il.get("page") or {}
        if total_items is None:
            total_items = int(page_info.get("total") or 0)
        got = len(seasons_raw) + len(series_raw)
        _log(f"  collections list page {page}: seasons+series so far {got}/{total_items or '?'}")
        if not total_items or got >= total_items or (
            not (il.get("seasons_list") or il.get("series_list"))
        ):
            break
        page += 1
        if delay > 0:
            time.sleep(delay)

    collections: list[CollectionItem] = []

    for s in seasons_raw:
        meta = s.get("meta") or {}
        sid = int(meta.get("season_id") or 0)
        name = str(meta.get("name") or meta.get("title") or f"合集{sid}")
        # strip leading 合集· for cleaner display but keep if unique
        display = name
        total = int(meta.get("total") or 0)
        _log(f"  fetching 合集 archives: {display!r} id={sid} total≈{total}")
        try:
            archives = fetch_season_archives(
                mid, sid, http=http, cookie=cookie, delay=delay
            )
        except Exception as e:
            _log(f"  ! season {sid} archives failed: {e}; using list preview")
            archives = list(s.get("archives") or [])
        collections.append(
            CollectionItem(
                kind="season",
                id=sid,
                name=display,
                total=len(archives) or total,
                description=str(meta.get("description") or ""),
                cover=str(meta.get("cover") or ""),
                archives=[
                    _archive_to_video(a, collection_name=display, kind="season", cid=sid)
                    for a in archives
                    if a.get("bvid")
                ],
            )
        )
        if delay > 0:
            time.sleep(delay)

    for s in series_raw:
        meta = s.get("meta") or {}
        sid = int(meta.get("series_id") or 0)
        name = str(meta.get("name") or f"系列{sid}")
        total = int(meta.get("total") or 0)
        _log(f"  fetching 系列 archives: {name!r} id={sid} total≈{total}")
        try:
            archives = fetch_series_archives(
                mid, sid, http=http, cookie=cookie, delay=delay
            )
        except Exception as e:
            _log(f"  ! series {sid} archives failed: {e}; using list preview")
            archives = list(s.get("archives") or [])
        collections.append(
            CollectionItem(
                kind="series",
                id=sid,
                name=name,
                total=len(archives) or total,
                description=str(meta.get("description") or ""),
                cover=str(meta.get("cover") or ""),
                archives=[
                    _archive_to_video(a, collection_name=name, kind="series", cid=sid)
                    for a in archives
                    if a.get("bvid")
                ],
            )
        )
        if delay > 0:
            time.sleep(delay)

    return collections


def enrich_videos_with_collections(
    videos: list[dict[str, Any]],
    collections: list[CollectionItem],
    *,
    merge_collection_only: bool = True,
) -> list[dict[str, Any]]:
    """
    Attach official collection membership to user-videos rows.

    series field: primary official collection name (season preferred over series),
    else 「未入合集」.

    If merge_collection_only, append archives that appear only in 合集/系列
    (not in opencli user-videos list) so catalog groups match space lists totals.
    """
    # bvid -> list of (priority, name, kind, id)
    membership: dict[str, list[tuple[int, str, str, int]]] = {}
    archive_by_bvid: dict[str, dict[str, Any]] = {}
    for c in collections:
        # season first (合集), then series
        prio = 0 if c.kind == "season" else 1
        for a in c.archives:
            bvid = str(a.get("bvid") or "")
            if not bvid:
                continue
            membership.setdefault(bvid, []).append((prio, c.name, c.kind, c.id))
            # keep first seen archive row as template
            archive_by_bvid.setdefault(bvid, a)

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for v in videos:
        row = dict(v)
        bvid = str(row.get("bvid") or "")
        if bvid:
            seen.add(bvid)
        mem = sorted(membership.get(bvid) or [], key=lambda x: x[0])
        if mem:
            _, name, kind, cid = mem[0]
            row["series"] = name
            row["series_source"] = "official"
            row["collection_kind"] = kind
            row["collection_id"] = cid
            row["collections"] = [
                {"kind": k, "id": i, "name": n} for _, n, k, i in mem
            ]
        else:
            row["series"] = "未入合集"
            row["series_source"] = "none"
            row["collections"] = []
        out.append(row)

    if merge_collection_only:
        for bvid, a in archive_by_bvid.items():
            if bvid in seen:
                continue
            mem = sorted(membership.get(bvid) or [], key=lambda x: x[0])
            row = dict(a)
            if mem:
                _, name, kind, cid = mem[0]
                row["series"] = name
                row["series_source"] = "official"
                row["collection_kind"] = kind
                row["collection_id"] = cid
                row["collections"] = [
                    {"kind": k, "id": i, "name": n} for _, n, k, i in mem
                ]
            row.setdefault("url", f"https://www.bilibili.com/video/{bvid}")
            out.append(row)

    return out
