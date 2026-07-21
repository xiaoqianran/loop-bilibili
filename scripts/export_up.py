#!/usr/bin/env python3
"""
loop-bilibili: export a Bilibili UP's full video catalog, grouped by series.

Features:
  - Paginated fetch via opencli bilibili user-videos
  - Rate-limit profiles (conservative / balanced / aggressive)
  - Delay + jitter, exponential backoff, hard-risk cooldown
  - Resume from .progress.json / all.partial.json
  - Series markdown + index + JSON + CSV
  - Rebuild catalogs from existing all.json (no network)

Usage:
  python3 scripts/export_up.py 2071007724 --name 海安雨
  python3 scripts/export_up.py 2071007724 --name 海安雨 --profile conservative --resume
  python3 scripts/export_up.py --rebuild catalogs/2071007724-海安雨
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SERIES_TAG_RE = re.compile(r"【([^】]+)】")
LEADING_NUM_RE = re.compile(r"^\s*\[?(\d+)\]?[.\s]")
SPACE_URL_RE = re.compile(
    r"(?:https?://)?space\.bilibili\.com/(\d+)", re.IGNORECASE
)
UID_RE = re.compile(r"^\d{3,}$")
BVID_RE = re.compile(r"BV[\w]+", re.IGNORECASE)

# opencli / bilibili risk signals in text output
RATE_LIMIT_MARKERS = (
    "-799",
    "请求过于频繁",
    "too many requests",
    "rate limit",
    "429",
)
RISK_412_MARKERS = (
    "-412",
    "412",
    "precondition failed",
    "请求被拦截",
    "风控",
)
SIGN_352_MARKERS = (
    "-352",
    "风控校验失败",
    "wbi",
)


@dataclass
class Profile:
    name: str
    page_size: int
    page_delay: float
    page_jitter: float
    search_delay: float
    max_retries: int
    backoff_base: float
    backoff_factor: float
    backoff_cap: float
    cooldown_412: float
    max_pages: int


PROFILES: dict[str, Profile] = {
    "conservative": Profile(
        name="conservative",
        page_size=30,
        page_delay=1.5,
        page_jitter=0.5,
        search_delay=4.0,
        max_retries=5,
        backoff_base=2.0,
        backoff_factor=2.0,
        backoff_cap=120.0,
        cooldown_412=300.0,
        max_pages=200,
    ),
    "balanced": Profile(
        name="balanced",
        page_size=50,
        page_delay=1.0,
        page_jitter=0.3,
        search_delay=3.0,
        max_retries=4,
        backoff_base=1.5,
        backoff_factor=2.0,
        backoff_cap=90.0,
        cooldown_412=180.0,
        max_pages=200,
    ),
    "aggressive": Profile(
        name="aggressive",
        page_size=50,
        page_delay=0.3,
        page_jitter=0.1,
        search_delay=1.5,
        max_retries=3,
        backoff_base=1.0,
        backoff_factor=2.0,
        backoff_cap=60.0,
        cooldown_412=120.0,
        max_pages=200,
    ),
}


class FetchError(Exception):
    """Base fetch error with a category for retry policy."""

    def __init__(self, message: str, category: str = "unknown"):
        super().__init__(message)
        self.category = category  # rate | risk412 | sign352 | empty_soft | timeout | hard


def log(msg: str) -> None:
    print(msg, flush=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sleep_with_jitter(base: float, jitter: float, label: str = "") -> None:
    delay = max(0.0, base + random.uniform(-jitter, jitter))
    if delay <= 0:
        return
    if label:
        log(f"  sleep {delay:.2f}s ({label})")
    else:
        log(f"  sleep {delay:.2f}s")
    time.sleep(delay)


def classify_failure(text: str, returncode: int | None = None) -> str | None:
    """Return error category if output looks like a rate/risk failure."""
    low = (text or "").lower()
    raw = text or ""

    if any(m.lower() in low or m in raw for m in SIGN_352_MARKERS):
        # avoid false positive: bare "wbi" in unrelated text is rare in opencli errors
        if "-352" in raw or "风控校验" in raw or "wbi" in low:
            return "sign352"

    if any(m.lower() in low or m in raw for m in RISK_412_MARKERS):
        # require stronger signal than bare "412" in huge dumps
        if "-412" in raw or "precondition failed" in low or "请求被拦截" in raw:
            return "risk412"
        if re.search(r"\b412\b", raw) and ("风控" in raw or "intercept" in low or "fail" in low):
            return "risk412"

    if any(m.lower() in low or m in raw for m in RATE_LIMIT_MARKERS):
        return "rate"

    if returncode is not None and returncode != 0:
        if "timeout" in low or "timed out" in low:
            return "timeout"
    return None


def run_opencli(args: list[str], timeout: int = 180) -> tuple[str, int]:
    cmd = ["opencli", *args]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or "") + "\n" + (e.stderr or "")
        raise FetchError(f"opencli timeout after {timeout}s: {out[-400:]}", "timeout") from e

    out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    cat = classify_failure(out, proc.returncode)
    if cat:
        raise FetchError(out[-800:] or f"opencli exit {proc.returncode}", cat)

    if proc.returncode != 0:
        # still try parse later; only hard-fail if no JSON
        if "[" not in out and "{" not in out:
            raise FetchError(
                f"opencli failed ({proc.returncode}): {out[-800:]}",
                "hard",
            )
    return out, proc.returncode


def extract_json(text: str) -> Any:
    for start_char, end_char in (("[", "]"), ("{", "}")):
        i = text.find(start_char)
        if i < 0:
            continue
        depth = 0
        in_str = False
        esc = False
        for j in range(i, len(text)):
            c = text[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == start_char:
                depth += 1
            elif c == end_char:
                depth -= 1
                if depth == 0:
                    chunk = text[i : j + 1]
                    try:
                        return json.loads(chunk)
                    except json.JSONDecodeError:
                        break
    raise ValueError(f"No JSON found in opencli output:\n{(text or '')[-500:]}")


def parse_target(target: str) -> dict[str, str]:
    target = target.strip()
    m = SPACE_URL_RE.search(target)
    if m:
        return {"uid": m.group(1)}
    if UID_RE.match(target):
        return {"uid": target}
    return {"query": target}


def resolve_uid(target: str, profile: Profile) -> tuple[str, str]:
    info = parse_target(target)
    if "uid" in info:
        return info["uid"], info["uid"]

    query = info["query"]
    log(f"Resolving UP name via search: {query!r} (search delay {profile.search_delay}s)")
    sleep_with_jitter(profile.search_delay, min(0.5, profile.search_delay * 0.2), "search")

    try:
        out, _ = run_opencli(
            ["bilibili", "search", query, "--limit", "20", "-f", "json"],
            timeout=120,
        )
        data = extract_json(out)
    except Exception as e:
        data = []
        log(f"  search failed: {e}")

    if not isinstance(data, list):
        data = (data or {}).get("results") or (data or {}).get("items") or []

    for item in data:
        if not isinstance(item, dict):
            continue
        name = str(item.get("author") or item.get("name") or item.get("title") or "")
        mid = item.get("mid") or item.get("uid") or item.get("id")
        url = str(item.get("url") or item.get("space") or "")
        m = SPACE_URL_RE.search(url)
        if m and (query in name or name == query):
            return m.group(1), name or query
        if mid and str(mid).isdigit() and (query in name or name == query):
            return str(mid), name or query

    # fallback: user-videos accepts username on some adapters
    sleep_with_jitter(profile.search_delay, 0.3, "search-fallback")
    try:
        out2, _ = run_opencli(
            ["bilibili", "user-videos", query, "--limit", "1", "-f", "json"],
            timeout=120,
        )
        extract_json(out2)
        return query, query
    except Exception as e:
        raise SystemExit(
            f"无法解析 UP 主「{query}」。请改用 UID 或空间链接，例如:\n"
            f"  https://space.bilibili.com/2071007724\n"
            f"原始错误: {e}"
        )


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
    return {
        "title": title,
        "url": url or (f"https://www.bilibili.com/video/{bvid}" if bvid else ""),
        "bvid": bvid,
        "plays": it.get("plays") if it.get("plays") is not None else it.get("play"),
        "likes": it.get("likes") if it.get("likes") is not None else it.get("like"),
        "date": it.get("date") or it.get("pubdate") or it.get("created") or "",
        "series": it.get("series") or detect_series(title),
        "number": it.get("number") if it.get("number") is not None else detect_number(title),
    }


def group_by_series(videos: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for v in videos:
        series = v.get("series") or detect_series(v.get("title") or "")
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
    p = v.get("plays")
    try:
        return int(p)
    except (TypeError, ValueError):
        return 0


# ---------- progress / resume ----------

def load_progress(folder: Path) -> dict:
    path = folder / ".progress.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_progress(folder: Path, data: dict) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / ".progress.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_partial(folder: Path) -> list[dict]:
    path = folder / "all.partial.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_partial(folder: Path, videos: list[dict]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "all.partial.json").write_text(
        json.dumps(videos, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def clear_progress_files(folder: Path) -> None:
    for name in (".progress.json", "all.partial.json"):
        p = folder / name
        if p.exists():
            p.unlink()


# ---------- fetch with retry ----------

def fetch_page(
    uid: str, page: int, page_size: int, order: str
) -> tuple[list[dict], float]:
    t0 = time.monotonic()
    out, _ = run_opencli(
        [
            "bilibili",
            "user-videos",
            uid,
            "--limit",
            str(page_size),
            "--page",
            str(page),
            "--order",
            order,
            "-f",
            "json",
        ],
        timeout=180,
    )
    elapsed = time.monotonic() - t0
    try:
        items = extract_json(out)
    except ValueError as e:
        raise FetchError(str(e), "hard") from e
    if not isinstance(items, list):
        raise FetchError(f"expected list, got {type(items)}", "hard")
    return items, elapsed


def fetch_page_with_retry(
    uid: str,
    page: int,
    profile: Profile,
    order: str,
) -> tuple[list[dict], float]:
    attempt = 0
    while True:
        attempt += 1
        try:
            items, elapsed = fetch_page(uid, page, profile.page_size, order)
            return items, elapsed
        except FetchError as e:
            cat = e.category
            log(f"  page {page} attempt {attempt}/{profile.max_retries}: {cat}: {str(e)[:200]}")

            if cat == "sign352":
                raise SystemExit(
                    "检测到可能的 -352 风控校验失败（UA/签名/会话）。\n"
                    "请检查 opencli bilibili 登录/会话，或更新 opencli；"
                    "单靠加长 sleep 通常无效。\n"
                    f"详情: {e}"
                ) from e

            if attempt >= profile.max_retries:
                raise

            if cat == "risk412":
                log(f"  hard risk cooldown {profile.cooldown_412:.0f}s ...")
                time.sleep(profile.cooldown_412)
            elif cat in ("rate", "timeout", "empty_soft"):
                wait = min(
                    profile.backoff_cap,
                    profile.backoff_base * (profile.backoff_factor ** (attempt - 1)),
                )
                wait += random.uniform(0, 0.5)
                log(f"  backoff {wait:.1f}s ...")
                time.sleep(wait)
            else:
                wait = min(30.0, profile.backoff_base * attempt)
                time.sleep(wait)


def fetch_all_videos(
    uid: str,
    folder: Path,
    profile: Profile,
    order: str = "pubdate",
    resume: bool = False,
    max_pages: int | None = None,
) -> list[dict]:
    max_pages = max_pages if max_pages is not None else profile.max_pages
    all_items: list[dict] = []
    seen: set[str] = set()
    start_page = 1
    consecutive_empty = 0

    if resume:
        partial = load_partial(folder)
        prog = load_progress(folder)
        if partial:
            for v in partial:
                nv = normalize_video(v)
                key = nv.get("url") or nv.get("bvid") or ""
                if key and key not in seen:
                    seen.add(key)
                    all_items.append(nv)
            start_page = int(prog.get("next_page") or prog.get("page") or 1)
            log(f"Resume: {len(all_items)} videos, continue from page {start_page}")
        else:
            log("Resume requested but no partial data; starting fresh")

    log(
        f"Fetch profile={profile.name} page_size={profile.page_size} "
        f"delay={profile.page_delay}±{profile.page_jitter}s max_pages={max_pages}"
    )

    page = start_page
    while page <= max_pages:
        log(f"  fetching page {page} ...")
        try:
            items, elapsed = fetch_page_with_retry(uid, page, profile, order)
        except FetchError as e:
            save_partial(folder, all_items)
            save_progress(
                folder,
                {
                    "uid": uid,
                    "next_page": page,
                    "fetched_count": len(all_items),
                    "status": "failed",
                    "error": str(e)[:500],
                    "category": e.category,
                    "updated_at": now_iso(),
                    "profile": profile.name,
                },
            )
            raise SystemExit(
                f"抓取失败并已保存进度（{len(all_items)} 条）。\n"
                f"使用 --resume 从 page {page} 继续。\n"
                f"错误[{e.category}]: {e}"
            ) from e

        if elapsed > 30:
            log(f"  page {page} slow ({elapsed:.1f}s); next delay x1.5")
            slow_factor = 1.5
        else:
            slow_factor = 1.0

        if not items:
            consecutive_empty += 1
            log(f"  page {page}: empty ({consecutive_empty})")
            if consecutive_empty >= 2 and all_items:
                # soft rate-limit suspicion: retry this page once more after backoff
                log("  soft-limit suspicion (2 empty pages); backoff and retry once")
                time.sleep(min(profile.backoff_cap, profile.backoff_base * 4))
                try:
                    items2, _ = fetch_page_with_retry(uid, page, profile, order)
                except FetchError:
                    items2 = []
                if not items2:
                    log("  still empty, treat as end of list")
                    break
                items = items2
                consecutive_empty = 0
            elif not all_items and consecutive_empty >= 2:
                break
            else:
                # first empty often means end
                if all_items:
                    break
                page += 1
                sleep_with_jitter(
                    profile.page_delay * slow_factor,
                    profile.page_jitter,
                    "page",
                )
                continue
        else:
            consecutive_empty = 0

        new = 0
        for it in items:
            if not isinstance(it, dict):
                continue
            nv = normalize_video(it)
            key = nv.get("url") or nv.get("bvid") or ""
            if not key or key in seen:
                continue
            seen.add(key)
            all_items.append(nv)
            new += 1

        log(f"  page {page}: +{new} (total {len(all_items)}) in {elapsed:.1f}s")

        save_partial(folder, all_items)
        save_progress(
            folder,
            {
                "uid": uid,
                "next_page": page + 1,
                "page": page,
                "fetched_count": len(all_items),
                "status": "in_progress",
                "updated_at": now_iso(),
                "profile": profile.name,
                "last_bvid": all_items[-1].get("bvid") if all_items else "",
            },
        )

        if len(items) < profile.page_size:
            log("  last page (short page)")
            break

        page += 1
        sleep_with_jitter(
            profile.page_delay * slow_factor,
            profile.page_jitter,
            "page",
        )

    return all_items


# ---------- writers ----------

def write_series_md(
    path: Path, series: str, videos: list[dict], up_name: str, uid: str
) -> None:
    lines = [
        f"# {series}",
        "",
        f"> UP：{up_name}（UID `{uid}`）  ",
        f"> 本系列共 **{len(videos)}** 条",
        "",
        "| # | 编号 | 标题 | 播放 | 日期 | 链接 |",
        "|---|------|------|------|------|------|",
    ]
    for i, v in enumerate(videos, 1):
        num = v.get("number")
        num_s = str(num) if num is not None else "-"
        title = (v.get("title") or "").replace("|", "\\|")
        bvid = v.get("bvid") or ""
        link = v.get("url") or (
            f"https://www.bilibili.com/video/{bvid}" if bvid else ""
        )
        lines.append(
            f"| {i} | {num_s} | {title} | {fmt_plays(v.get('plays'))} | "
            f"{v.get('date') or '-'} | [{bvid or 'link'}]({link}) |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_index_md(
    path: Path,
    up_name: str,
    uid: str,
    space_url: str,
    groups: dict[str, list[dict]],
    videos: list[dict],
    profile_name: str = "",
) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    def series_order(item: tuple[str, list]):
        name, vs = item
        if name.startswith("未分类"):
            return (1, 0, name)
        return (0, -len(vs), name)

    ordered = sorted(groups.items(), key=series_order)

    lines = [
        f"# {up_name} · 完整视频目录",
        "",
        f"- **UID**: `{uid}`",
        f"- **空间**: {space_url}",
        f"- **投稿总数**: **{len(videos)}**",
        f"- **系列数**: **{len(groups)}**",
        f"- **导出时间**: {now}",
        f"- **数据源**: `opencli bilibili user-videos`",
    ]
    if profile_name:
        lines.append(f"- **限速 profile**: `{profile_name}`")
    lines += [
        "",
        "## 系列一览",
        "",
        "| 系列 | 数量 | 文件 |",
        "|------|------|------|",
    ]
    for series, vs in ordered:
        fname = f"series/{slugify(series)}.md"
        lines.append(f"| {series} | {len(vs)} | [{fname}]({fname}) |")

    lines += [
        "",
        "## 全站最新 20 条",
        "",
        "| 日期 | 标题 | 系列 | 播放 | 链接 |",
        "|------|------|------|------|------|",
    ]
    latest = sorted(videos, key=lambda v: str(v.get("date") or ""), reverse=True)[:20]
    for v in latest:
        title = (v.get("title") or "").replace("|", "\\|")
        bvid = v.get("bvid") or ""
        link = v.get("url") or ""
        lines.append(
            f"| {v.get('date') or '-'} | {title} | {v.get('series')} | "
            f"{fmt_plays(v.get('plays'))} | [{bvid or 'link'}]({link}) |"
        )

    lines += [
        "",
        "## 播放量 Top 20",
        "",
        "| 播放 | 标题 | 系列 | 日期 | 链接 |",
        "|------|------|------|------|------|",
    ]
    top = sorted(videos, key=plays_int, reverse=True)[:20]
    for v in top:
        title = (v.get("title") or "").replace("|", "\\|")
        bvid = v.get("bvid") or ""
        link = v.get("url") or ""
        lines.append(
            f"| {fmt_plays(v.get('plays'))} | {title} | {v.get('series')} | "
            f"{v.get('date') or '-'} | [{bvid or 'link'}]({link}) |"
        )

    lines += [
        "",
        "## 原始数据",
        "",
        "- 全量 JSON: [all.json](all.json)",
        "- 全量 CSV: [all.csv](all.csv)",
        "- 按系列 JSON: [by_series.json](by_series.json)",
        "- 元信息: [meta.json](meta.json)",
        "",
        "---",
        "",
        "由 [loop-bilibili](../../README.md) 自动生成。",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def write_csv(path: Path, videos: list[dict]) -> None:
    fields = ["bvid", "title", "series", "number", "plays", "likes", "date", "url"]
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for v in videos:
            row = {k: v.get(k, "") for k in fields}
            w.writerow(row)


def write_catalog_files(
    folder: Path,
    uid: str,
    up_name: str,
    videos: list[dict],
    profile_name: str = "",
) -> None:
    space_url = f"https://space.bilibili.com/{uid}"
    series_dir = folder / "series"
    # clean old series md to avoid stale files after regroup
    if series_dir.exists():
        for p in series_dir.glob("*.md"):
            p.unlink()
    series_dir.mkdir(parents=True, exist_ok=True)

    # re-normalize series fields
    videos = [normalize_video(v) for v in videos]
    groups = group_by_series(videos)

    for series, vs in groups.items():
        write_series_md(
            series_dir / f"{slugify(series)}.md", series, vs, up_name, uid
        )

    write_index_md(
        folder / "README.md",
        up_name,
        uid,
        space_url,
        groups,
        videos,
        profile_name=profile_name,
    )
    write_csv(folder / "all.csv", videos)

    (folder / "all.json").write_text(
        json.dumps(videos, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (folder / "by_series.json").write_text(
        json.dumps(groups, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    meta = {
        "uid": uid,
        "name": up_name,
        "space_url": space_url,
        "total": len(videos),
        "series_count": len(groups),
        "series": {k: len(v) for k, v in groups.items()},
        "exported_at": now_iso(),
        "tool": "loop-bilibili",
        "profile": profile_name or None,
    }
    (folder / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    log(f"Wrote catalog -> {folder} ({len(videos)} videos, {len(groups)} series)")


def export_catalog(
    uid: str,
    up_name: str,
    out_root: Path,
    profile: Profile,
    order: str = "pubdate",
    resume: bool = False,
    max_pages: int | None = None,
) -> Path:
    folder = out_root / f"{uid}-{slugify(up_name)}"
    folder.mkdir(parents=True, exist_ok=True)

    log(f"Fetching all videos for {up_name} ({uid}) ...")
    videos = fetch_all_videos(
        uid,
        folder,
        profile,
        order=order,
        resume=resume,
        max_pages=max_pages,
    )
    if not videos:
        raise SystemExit(f"未获取到任何视频：uid={uid}")

    write_catalog_files(folder, uid, up_name, videos, profile_name=profile.name)
    clear_progress_files(folder)
    log("Progress files cleared (export complete).")
    return folder


def rebuild_from_folder(folder: Path, name_override: str = "") -> Path:
    folder = folder.resolve()
    all_json = folder / "all.json"
    partial = folder / "all.partial.json"
    meta_path = folder / "meta.json"

    if all_json.exists():
        videos = json.loads(all_json.read_text(encoding="utf-8"))
    elif partial.exists():
        videos = json.loads(partial.read_text(encoding="utf-8"))
        log("Rebuilding from all.partial.json (incomplete fetch?)")
    else:
        raise SystemExit(f"No all.json or all.partial.json in {folder}")

    meta = {}
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

    uid = str(meta.get("uid") or "")
    up_name = name_override or str(meta.get("name") or "")
    if not uid:
        # folder name: {uid}-{name}
        m = re.match(r"^(\d+)-(.+)$", folder.name)
        if m:
            uid, up_name = m.group(1), up_name or m.group(2)
    if not uid:
        raise SystemExit("Cannot determine uid; pass folder named {uid}-{name} or meta.json")
    if not up_name:
        up_name = uid

    write_catalog_files(folder, uid, up_name, videos, profile_name="rebuild")
    return folder


def build_profile(args: argparse.Namespace) -> Profile:
    base = PROFILES[args.profile]
    # copy with overrides
    p = Profile(**asdict(base))
    if args.page_size is not None:
        p.page_size = args.page_size
    if args.delay is not None:
        p.page_delay = args.delay
    if args.jitter is not None:
        p.page_jitter = args.jitter
    if args.retries is not None:
        p.max_retries = args.retries
    if args.max_pages is not None:
        p.max_pages = args.max_pages
    if args.cooldown_412 is not None:
        p.cooldown_412 = args.cooldown_412
    return p


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export a Bilibili UP's full video catalog grouped by series",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "target",
        nargs="?",
        default="",
        help="UP name, UID, or space URL",
    )
    parser.add_argument("--name", default="", help="Display name override")
    parser.add_argument("--out", default="catalogs", help="Output root directory")
    parser.add_argument(
        "--profile",
        choices=list(PROFILES.keys()),
        default="conservative",
        help="Rate-limit profile",
    )
    parser.add_argument("--page-size", type=int, default=None, help="Override page size")
    parser.add_argument("--delay", type=float, default=None, help="Override page delay (seconds)")
    parser.add_argument("--jitter", type=float, default=None, help="Override delay jitter")
    parser.add_argument("--retries", type=int, default=None, help="Override max retries per page")
    parser.add_argument("--max-pages", type=int, default=None, help="Override max pages")
    parser.add_argument(
        "--cooldown-412",
        type=float,
        default=None,
        help="Seconds to sleep after -412 risk control",
    )
    parser.add_argument(
        "--order",
        choices=["pubdate", "click", "stow"],
        default="pubdate",
        help="Sort order for user-videos",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from .progress.json / all.partial.json",
    )
    parser.add_argument(
        "--rebuild",
        metavar="DIR",
        default="",
        help="Rebuild markdown/csv from existing all.json (no network)",
    )
    args = parser.parse_args()

    if args.rebuild:
        rebuild_from_folder(Path(args.rebuild), name_override=args.name)
        return

    if not args.target:
        parser.error("target is required unless --rebuild is used")

    if args.profile == "aggressive":
        log(
            "WARNING: profile=aggressive uses short delays; "
            "higher risk of -799/-412. Prefer conservative for bulk export."
        )

    profile = build_profile(args)
    info = parse_target(args.target)

    if "uid" in info:
        uid = info["uid"]
        up_name = args.name or uid
    else:
        uid, resolved = resolve_uid(args.target, profile)
        up_name = args.name or resolved

    if args.name:
        up_name = args.name

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    export_catalog(
        uid,
        up_name,
        out_root,
        profile,
        order=args.order,
        resume=args.resume,
        max_pages=args.max_pages,
    )


if __name__ == "__main__":
    main()
