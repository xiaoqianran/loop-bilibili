#!/usr/bin/env python3
"""
loop-bilibili: given a Bilibili UP name / UID / space URL,
fetch ALL videos via opencli and export a complete catalog grouped by series.

Usage:
  python3 scripts/export_up.py 2071007724
  python3 scripts/export_up.py https://space.bilibili.com/2071007724
  python3 scripts/export_up.py 海安雨
  python3 scripts/export_up.py 2071007724 --name 海安雨 --out catalogs
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


SERIES_TAG_RE = re.compile(r"【([^】]+)】")
LEADING_NUM_RE = re.compile(r"^\s*\[?(\d+)\]?[.\s]")
SPACE_URL_RE = re.compile(
    r"(?:https?://)?space\.bilibili\.com/(\d+)", re.IGNORECASE
)
UID_RE = re.compile(r"^\d{3,}$")


def run_opencli(args: list[str], timeout: int = 180) -> str:
    cmd = ["opencli", *args]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    if proc.returncode != 0:
        # still try to parse JSON if present
        if "[" not in out and "{" not in out:
            raise RuntimeError(
                f"opencli failed ({proc.returncode}): {out[-800:]}"
            )
    return out


def extract_json(text: str) -> Any:
    """Extract first JSON array/object from opencli output (may include warnings)."""
    for start_char, end_char in (("[", "]"), ("{", "}")):
        i = text.find(start_char)
        if i < 0:
            continue
        # try progressive parse from first bracket
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
    raise ValueError(f"No JSON found in opencli output:\n{text[-500:]}")


def parse_target(target: str) -> dict[str, str]:
    """Return {uid?, query?} from name / uid / space URL."""
    target = target.strip()
    m = SPACE_URL_RE.search(target)
    if m:
        return {"uid": m.group(1)}
    if UID_RE.match(target):
        return {"uid": target}
    return {"query": target}


def resolve_uid(target: str) -> tuple[str, str]:
    """
    Resolve target to (uid, display_name).
    Uses opencli bilibili search for names; space URL/UID passthrough.
    """
    info = parse_target(target)
    if "uid" in info:
        uid = info["uid"]
        # try whoami-style: user-videos works with uid; name may be provided later
        return uid, uid

    query = info["query"]
    # Prefer user search if available; fall back to video search authors is weak.
    # opencli bilibili search supports users via --type sometimes; try plain search
    # and also feed with username. Best path: search videos and look for matching
    # author is unreliable. Use search with type user if supported.
    out = run_opencli(
        ["bilibili", "search", query, "--limit", "20", "-f", "json"],
        timeout=120,
    )
    data = extract_json(out)
    if not isinstance(data, list):
        data = data.get("results") or data.get("items") or []

    # If search returns users with mid/uid
    for item in data:
        if not isinstance(item, dict):
            continue
        name = str(item.get("author") or item.get("name") or item.get("title") or "")
        mid = item.get("mid") or item.get("uid") or item.get("id")
        # space url in result
        url = str(item.get("url") or item.get("space") or "")
        m = SPACE_URL_RE.search(url)
        if m and (query in name or name == query):
            return m.group(1), name
        if mid and (query in name or name == query) and str(mid).isdigit():
            return str(mid), name

    # Direct attempt: user-videos accepts username
    try:
        out2 = run_opencli(
            ["bilibili", "user-videos", query, "--limit", "1", "-f", "json"],
            timeout=120,
        )
        extract_json(out2)  # validates it works
        # still need uid for folder naming — try feed
        try:
            feed = run_opencli(
                ["bilibili", "feed", query, "--limit", "1", "-f", "json"],
                timeout=120,
            )
            fdata = extract_json(feed)
            if isinstance(fdata, list) and fdata:
                # no uid guaranteed
                pass
        except Exception:
            pass
        return query, query
    except Exception as e:
        raise SystemExit(
            f"无法解析 UP 主「{query}」。请改用 UID 或空间链接，例如:\n"
            f"  https://space.bilibili.com/2071007724\n"
            f"原始错误: {e}"
        )


def fetch_all_videos(uid: str, page_size: int = 50, max_pages: int = 50) -> list[dict]:
    """Paginate opencli bilibili user-videos until exhausted."""
    all_items: list[dict] = []
    seen_urls: set[str] = set()

    for page in range(1, max_pages + 1):
        print(f"  fetching page {page} ...", flush=True)
        out = run_opencli(
            [
                "bilibili",
                "user-videos",
                uid,
                "--limit",
                str(page_size),
                "--page",
                str(page),
                "--order",
                "pubdate",
                "-f",
                "json",
            ],
            timeout=180,
        )
        try:
            items = extract_json(out)
        except ValueError:
            print(f"  page {page}: no JSON, stop", flush=True)
            break
        if not isinstance(items, list) or not items:
            print(f"  page {page}: empty, stop", flush=True)
            break

        new = 0
        for it in items:
            if not isinstance(it, dict):
                continue
            url = str(it.get("url") or it.get("bvid") or "")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            all_items.append(normalize_video(it))
            new += 1

        print(f"  page {page}: +{new} (total {len(all_items)})", flush=True)
        if len(items) < page_size:
            break

    return all_items


def normalize_video(it: dict) -> dict:
    title = str(it.get("title") or "")
    url = str(it.get("url") or "")
    bvid = it.get("bvid") or ""
    if not bvid and "/video/" in url:
        bvid = url.rstrip("/").split("/video/")[-1].split("?")[0]
    return {
        "title": title,
        "url": url,
        "bvid": bvid,
        "plays": it.get("plays") if it.get("plays") is not None else it.get("play"),
        "likes": it.get("likes") if it.get("likes") is not None else it.get("like"),
        "date": it.get("date") or it.get("pubdate") or it.get("created") or "",
        "series": detect_series(title),
        "number": detect_number(title),
    }


def detect_series(title: str) -> str:
    """Extract series name from 【...】 tags; prefer last tag if multiple."""
    tags = SERIES_TAG_RE.findall(title)
    if not tags:
        return "未分类 / 其他"
    # Prefer tags that look like series names (contain 宝藏/系列/合集 etc.)
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


def group_by_series(videos: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for v in videos:
        groups[v["series"]].append(v)

    # sort each series: by number desc if available, else by date desc
    def sort_key(v: dict):
        num = v.get("number")
        date = str(v.get("date") or "")
        # higher number first for numbered series; missing number last
        return (
            0 if num is not None else 1,
            -(num or 0),
            date,
        )

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


def write_series_md(path: Path, series: str, videos: list[dict], up_name: str, uid: str) -> None:
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
        title = v["title"].replace("|", "\\|")
        bvid = v.get("bvid") or ""
        link = v.get("url") or (f"https://www.bilibili.com/video/{bvid}" if bvid else "")
        lines.append(
            f"| {i} | {num_s} | {title} | {fmt_plays(v.get('plays'))} | {v.get('date') or '-'} | [{bvid or 'link'}]({link}) |"
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
) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    # series order: by count desc, 未分类 last
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
    # videos already roughly newest-first from fetch
    latest = sorted(videos, key=lambda v: str(v.get("date") or ""), reverse=True)[:20]
    for v in latest:
        title = v["title"].replace("|", "\\|")
        bvid = v.get("bvid") or ""
        link = v.get("url") or ""
        lines.append(
            f"| {v.get('date') or '-'} | {title} | {v.get('series')} | {fmt_plays(v.get('plays'))} | [{bvid or 'link'}]({link}) |"
        )

    lines += [
        "",
        "## 播放量 Top 20",
        "",
        "| 播放 | 标题 | 系列 | 日期 | 链接 |",
        "|------|------|------|------|------|",
    ]
    top = sorted(
        videos,
        key=lambda v: int(v["plays"]) if str(v.get("plays") or "").isdigit() or isinstance(v.get("plays"), int) else 0,
        reverse=True,
    )[:20]
    for v in top:
        title = v["title"].replace("|", "\\|")
        bvid = v.get("bvid") or ""
        link = v.get("url") or ""
        lines.append(
            f"| {fmt_plays(v.get('plays'))} | {title} | {v.get('series')} | {v.get('date') or '-'} | [{bvid or 'link'}]({link}) |"
        )

    lines += [
        "",
        "## 原始数据",
        "",
        "- 全量 JSON: [all.json](all.json)",
        "- 按系列 JSON: [by_series.json](by_series.json)",
        "",
        "---",
        "",
        "由 [loop-bilibili](../../README.md) 自动生成。",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def export_catalog(
    uid: str,
    up_name: str,
    out_root: Path,
) -> Path:
    space_url = f"https://space.bilibili.com/{uid}" if uid.isdigit() else f"https://space.bilibili.com/{uid}"
    folder = out_root / f"{uid}-{slugify(up_name)}"
    series_dir = folder / "series"
    series_dir.mkdir(parents=True, exist_ok=True)

    print(f"Fetching all videos for {up_name} ({uid}) ...")
    videos = fetch_all_videos(uid)
    if not videos:
        raise SystemExit(f"未获取到任何视频：uid={uid}")

    groups = group_by_series(videos)
    print(f"Got {len(videos)} videos in {len(groups)} series")

    # write series md
    for series, vs in groups.items():
        write_series_md(series_dir / f"{slugify(series)}.md", series, vs, up_name, uid)

    write_index_md(folder / "README.md", up_name, uid, space_url, groups, videos)

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
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "tool": "loop-bilibili",
    }
    (folder / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Exported -> {folder}")
    return folder


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export a Bilibili UP's full video catalog grouped by series"
    )
    parser.add_argument(
        "target",
        help="UP name, UID, or space URL (e.g. 海安雨 / 2071007724 / https://space.bilibili.com/2071007724)",
    )
    parser.add_argument(
        "--name",
        default="",
        help="Display name override (recommended when target is UID/URL)",
    )
    parser.add_argument(
        "--out",
        default="catalogs",
        help="Output root directory (default: catalogs)",
    )
    args = parser.parse_args()

    info = parse_target(args.target)
    if "uid" in info:
        uid = info["uid"]
        up_name = args.name or uid
    else:
        # name search path
        if args.name:
            # still resolve uid
            uid, resolved = resolve_uid(args.target)
            up_name = args.name
        else:
            uid, up_name = resolve_uid(args.target)
            if args.name:
                up_name = args.name

    # If user passed URL/UID with --name
    if args.name and "uid" in info:
        up_name = args.name

    # Prefer digit uid for folder; if username was used and worked, keep it
    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    export_catalog(uid, up_name, out_root)


if __name__ == "__main__":
    main()
