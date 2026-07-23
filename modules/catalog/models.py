"""视频与系列模型。"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

SERIES_TAG_RE = re.compile(r"【([^】]+)】")
LEADING_NUM_RE = re.compile(r"^\s*\[?(\d+)\]?[.\s]")
SPACE_URL_RE = re.compile(
    r"(?:https?://)?space\.bilibili\.com/(\d+)", re.IGNORECASE
)
UID_RE = re.compile(r"^\d{3,}$")
BVID_RE = re.compile(r"BV[\w]+", re.IGNORECASE)


def parse_target(target: str) -> dict[str, str]:
    target = target.strip()
    m = SPACE_URL_RE.search(target)
    if m:
        return {"uid": m.group(1)}
    if UID_RE.match(target):
        return {"uid": target}
    return {"query": target}


def detect_series(title: str) -> str:
    tags = SERIES_TAG_RE.findall(title)
    if not tags:
        return "未分类 / 其他"
    priority = ("宝藏", "系列", "合集", "每周", "每天", "连载")
    for t in reversed(tags):
        if any(k in t for k in priority):
            return t.strip()
    return tags[-1].strip()


def detect_number(title: str) -> int | None:
    m = LEADING_NUM_RE.match(title)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


def normalize_video(it: dict) -> dict:
    title = str(it.get("title") or "")
    url = str(it.get("url") or "")
    bvid = str(it.get("bvid") or "")
    if not bvid and "/video/" in url:
        bvid = url.rstrip("/").split("/video/")[-1].split("?")[0]
    if not bvid:
        m = BVID_RE.search(url) or BVID_RE.search(title)
        if m:
            bvid = m.group(0)
    # Prefer already-attached official collection; never invent unless asked.
    series_source = str(it.get("series_source") or "")
    if series_source in ("official", "none") and it.get("series"):
        series = str(it.get("series"))
    elif it.get("series") and series_source == "official":
        series = str(it.get("series"))
    elif it.get("series") and str(it.get("series")) not in ("", "未分类 / 其他"):
        # keep explicit series from collections enrich / prior export
        series = str(it.get("series"))
        if not series_source:
            series_source = str(it.get("series_source") or "legacy")
    else:
        # legacy title heuristic only if no official pass yet
        series = str(it.get("series") or detect_series(title))
        series_source = series_source or "title_heuristic"
    out = {
        "title": title,
        "url": url or (f"https://www.bilibili.com/video/{bvid}" if bvid else ""),
        "bvid": bvid,
        "plays": it.get("plays") if it.get("plays") is not None else it.get("play"),
        "likes": it.get("likes") if it.get("likes") is not None else it.get("like"),
        "date": it.get("date") or it.get("pubdate") or it.get("created") or "",
        "series": series,
        "series_source": series_source,
        "number": it.get("number")
        if it.get("number") is not None
        else detect_number(title),
    }
    if it.get("collection_kind"):
        out["collection_kind"] = it.get("collection_kind")
    if it.get("collection_id") is not None:
        out["collection_id"] = it.get("collection_id")
    if it.get("collections"):
        out["collections"] = it.get("collections")
    return out


def group_by_series(videos: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for v in videos:
        src = str(v.get("series_source") or "")
        if src in ("official", "none") and v.get("series"):
            series = str(v.get("series"))
        elif v.get("series"):
            series = str(v.get("series"))
        else:
            series = detect_series(v.get("title") or "")
            v["series_source"] = "title_heuristic"
        v["series"] = series
        if v.get("number") is None:
            v["number"] = detect_number(v.get("title") or "")
        groups[series].append(v)

    def sort_key(v: dict):
        num = v.get("number")
        date = str(v.get("date") or "")
        return (0 if num is not None else 1, -(num or 0), date)

    for k in groups:
        groups[k].sort(key=sort_key)
    return dict(groups)


def slugify(name: str) -> str:
    s = re.sub(r"[\\/:*?\"<>|\s]+", "-", name.strip())
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:80] or "unknown"


def fmt_plays(n: Any) -> str:
    if n is None or n == "":
        return "-"
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return str(n)


def plays_int(v: dict) -> int:
    try:
        return int(v.get("plays"))
    except (TypeError, ValueError):
        return 0
