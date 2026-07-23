"""目录文件写入：README / series md / JSON / CSV。"""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path

from loop_core.timeutil import utc_now_display, utc_now_iso

from .models import (
    fmt_plays,
    group_by_series,
    normalize_video,
    plays_int,
    slugify,
)

logger = logging.getLogger(__name__)


def log(msg: str) -> None:
    print(msg, flush=True)
    logger.info(msg)


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
    def series_order(item: tuple[str, list]):
        name, vs = item
        if name.startswith("未分类"):
            return (1, 0, name)
        return (0, -len(vs), name)

    ordered = sorted(groups.items(), key=series_order)
    # Point humans to the subtitle hub FIRST — most readers want txt, not Top20 tables.
    # catalogs/ only carries metadata; full ordered transcripts live under data/subtitles.
    slug = path.parent.name  # catalogs/{uid}-{name}/README.md
    hub_rel = f"../../data/subtitles/ups/{slug}/README.md"

    lines = [
        f"# {up_name} · 完整视频目录",
        "",
        "---",
        "",
        "## ⭐ 字幕与全文导航（优先读这里）",
        "",
        "> **看字幕 / txt 全文？点这里，不要往下翻 Top 20。**",
        ">",
        f"> ### 👉 [{up_name} · 全部视频 · 字幕预览 · txt/srt]({hub_rel})",
        ">",
        "> 按投稿顺序列出**全部**视频：标题、播放页、有无字幕、**txt 正文预览**、相对路径 `txt/` · `srt/`。",
        "> 本 catalog 页只是投稿元数据摘要；真正给人读的字幕导航在上面的链接。",
        "",
        "---",
        "",
        f"- **UID**: `{uid}`",
        f"- **空间**: {space_url}",
        f"- **投稿总数**: **{len(videos)}**",
        f"- **系列数**: **{len(groups)}**",
        f"- **导出时间**: {utc_now_display()}",
        f"- **数据源**: `opencli bilibili user-videos`",
    ]
    if profile_name:
        lines.append(f"- **限速 profile**: `{profile_name}`")
    lines += [
        f"- **字幕导航**: [{hub_rel}]({hub_rel})",
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
        "## 全站最新 20 条（仅摘要，非全文）",
        "",
        "| 日期 | 标题 | 系列 | 播放 | 链接 |",
        "|------|------|------|------|------|",
    ]
    latest = sorted(videos, key=lambda v: str(v.get("date") or ""), reverse=True)[:20]
    for v in latest:
        title = (v.get("title") or "").replace("|", "\\|")
        bvid = v.get("bvid") or ""
        lines.append(
            f"| {v.get('date') or '-'} | {title} | {v.get('series')} | "
            f"{fmt_plays(v.get('plays'))} | [{bvid or 'link'}]({v.get('url') or ''}) |"
        )

    lines += [
        "",
        "## 播放量 Top 20（仅摘要，非全文）",
        "",
        "| 播放 | 标题 | 系列 | 日期 | 链接 |",
        "|------|------|------|------|------|",
    ]
    for v in sorted(videos, key=plays_int, reverse=True)[:20]:
        title = (v.get("title") or "").replace("|", "\\|")
        bvid = v.get("bvid") or ""
        lines.append(
            f"| {fmt_plays(v.get('plays'))} | {title} | {v.get('series')} | "
            f"{v.get('date') or '-'} | [{bvid or 'link'}]({v.get('url') or ''}) |"
        )

    lines += [
        "",
        "## 原始数据",
        "",
        "- 全量 JSON: [all.json](all.json)",
        "- 全量 CSV: [all.csv](all.csv)",
        "- 按系列 JSON: [by_series.json](by_series.json)",
        "- 元信息: [meta.json](meta.json)",
        f"- **字幕/全文导航（推荐）**: [{hub_rel}]({hub_rel})",
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
            w.writerow({k: v.get(k, "") for k in fields})


def write_catalog_files(
    folder: Path,
    uid: str,
    up_name: str,
    videos: list[dict],
    profile_name: str = "",
) -> None:
    space_url = f"https://space.bilibili.com/{uid}"
    series_dir = folder / "series"
    if series_dir.exists():
        for p in series_dir.glob("*.md"):
            p.unlink()
    series_dir.mkdir(parents=True, exist_ok=True)

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
        json.dumps(videos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (folder / "by_series.json").write_text(
        json.dumps(groups, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    meta = {
        "uid": uid,
        "name": up_name,
        "space_url": space_url,
        "total": len(videos),
        "series_count": len(groups),
        "series": {k: len(v) for k, v in groups.items()},
        "exported_at": utc_now_iso(),
        "tool": "loop-bilibili",
        "module": "catalog",
        "profile": profile_name or None,
    }
    (folder / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    log(f"Wrote catalog -> {folder} ({len(videos)} videos, {len(groups)} series)")
