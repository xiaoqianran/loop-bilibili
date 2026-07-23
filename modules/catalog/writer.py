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


def _subtitle_paths(catalog_folder: Path, bvid: str) -> tuple[str, str, bool, bool]:
    """
    Relative links from catalogs/{slug}/ to packed archive, plus existence flags.

    Returns (txt_rel_from_catalog_root, srt_rel_from_catalog_root, has_txt, has_srt).
    """
    slug = catalog_folder.name
    # catalogs/slug -> ../../data/subtitles/ups/slug/txt/BVxx.txt
    txt_rel = f"../../data/subtitles/ups/{slug}/txt/{bvid}.txt"
    srt_rel = f"../../data/subtitles/ups/{slug}/srt/{bvid}.srt"
    repo = catalog_folder.parent.parent  # .../loop-bilibili
    has_txt = (repo / "data" / "subtitles" / "ups" / slug / "txt" / f"{bvid}.txt").is_file()
    has_srt = (repo / "data" / "subtitles" / "ups" / slug / "srt" / f"{bvid}.srt").is_file()
    return txt_rel, srt_rel, has_txt, has_srt


def _subtitle_cell(catalog_folder: Path, bvid: str, *, from_series_subdir: bool = False) -> str:
    """Markdown cell: [txt] · [srt] or 无字幕."""
    if not bvid:
        return "—"
    txt_rel, srt_rel, has_txt, has_srt = _subtitle_paths(catalog_folder, bvid)
    if from_series_subdir:
        # series/*.md is one level deeper → need ../../../
        txt_rel = "../" + txt_rel
        srt_rel = "../" + srt_rel
    parts: list[str] = []
    if has_txt:
        parts.append(f"[txt]({txt_rel})")
    if has_srt:
        parts.append(f"[srt]({srt_rel})")
    if parts:
        return " · ".join(parts)
    # still offer intended path when missing (pack not done yet)
    return f"_无_（pack 后见 `{txt_rel}`）"


def write_series_md(
    path: Path,
    series: str,
    videos: list[dict],
    up_name: str,
    uid: str,
    catalog_folder: Path | None = None,
) -> None:
    catalog_folder = catalog_folder or path.parent.parent
    slug = catalog_folder.name
    hub_rel = f"../../../data/subtitles/ups/{slug}/README.md"
    lines = [
        f"# {series}",
        "",
        f"> UP：{up_name}（UID `{uid}`）· 本系列 **{len(videos)}** 条  ",
        f"> 字幕总导航：[{up_name} · 字幕 README]({hub_rel})",
        "",
        "| # | 标题 | 播放 | 日期 | 字幕 txt/srt |",
        "|---|------|------|------|--------------|",
    ]
    for i, v in enumerate(videos, 1):
        title = (v.get("title") or "").replace("|", "\\|")
        bvid = v.get("bvid") or ""
        link = v.get("url") or (
            f"https://www.bilibili.com/video/{bvid}" if bvid else ""
        )
        play = f"[{bvid or 'link'}]({link})" if link else (bvid or "—")
        sub = _subtitle_cell(catalog_folder, bvid, from_series_subdir=True)
        lines.append(
            f"| {i} | {title} | {play} | {v.get('date') or '-'} | {sub} |"
        )
    lines += [
        "",
        f"全量导航（含预览）：[{hub_rel}]({hub_rel})",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def _append_series_video_table(
    lines: list[str],
    videos: list[dict],
    catalog_folder: Path,
) -> None:
    lines += [
        "",
        "| # | 标题 | 播放 | 日期 | 字幕 txt/srt |",
        "|---|------|------|------|--------------|",
    ]
    for i, v in enumerate(videos, 1):
        title = (v.get("title") or "").replace("|", "\\|")
        bvid = v.get("bvid") or ""
        link = v.get("url") or (
            f"https://www.bilibili.com/video/{bvid}" if bvid else ""
        )
        play = f"[{bvid or 'link'}]({link})" if link else (bvid or "—")
        sub = _subtitle_cell(catalog_folder, bvid, from_series_subdir=False)
        lines.append(
            f"| {i} | {title} | {play} | {v.get('date') or '-'} | {sub} |"
        )
    lines.append("")


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
    catalog_folder = path.parent
    slug = catalog_folder.name
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
        "",
        "---",
        "",
        "## 📚 系列一览（含每条视频的 txt / srt）",
        "",
        f"> 下列按系列展开**全部**视频；**字幕 txt/srt** 链到 "
        f"`data/subtitles/ups/{slug}/`。"
        f"总索引仍见上方 [字幕导航]({hub_rel})。",
        "",
        "### 系列速查",
        "",
        "| 系列 | 条数 | 本页锚点 | 系列文件 |",
        "|------|------|----------|----------|",
    ]

    for series, vs in ordered:
        fname = f"series/{slugify(series)}.md"
        anchor = slugify(series)
        lines.append(
            f"| {series} | {len(vs)} | [↓ 展开](#{anchor}) | [{fname}]({fname}) |"
        )

    lines.append("")

    for series, vs in ordered:
        anchor = slugify(series)
        # GitHub-style heading anchors: we use explicit HTML id for reliability with CJK
        lines.append(f'<a id="{anchor}"></a>')
        lines.append("")
        lines.append(f"### {series} · {len(vs)} 条")
        lines.append("")
        lines.append(
            f"系列页（同表）：[series/{slugify(series)}.md](series/{slugify(series)}.md)"
            f" · [全部字幕导航]({hub_rel})"
        )
        _append_series_video_table(lines, vs, catalog_folder)

    lines += [
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
        "## 全站最新 20 条（仅摘要，非全文）",
        "",
        "| 日期 | 标题 | 系列 | 播放 | 链接 | 字幕 |",
        "|------|------|------|------|------|------|",
    ]
    latest = sorted(videos, key=lambda v: str(v.get("date") or ""), reverse=True)[:20]
    for v in latest:
        title = (v.get("title") or "").replace("|", "\\|")
        bvid = v.get("bvid") or ""
        sub = _subtitle_cell(catalog_folder, bvid)
        lines.append(
            f"| {v.get('date') or '-'} | {title} | {v.get('series')} | "
            f"{fmt_plays(v.get('plays'))} | [{bvid or 'link'}]({v.get('url') or ''}) | {sub} |"
        )

    lines += [
        "",
        "## 播放量 Top 20（仅摘要，非全文）",
        "",
        "| 播放 | 标题 | 系列 | 日期 | 链接 | 字幕 |",
        "|------|------|------|------|------|------|",
    ]
    for v in sorted(videos, key=plays_int, reverse=True)[:20]:
        title = (v.get("title") or "").replace("|", "\\|")
        bvid = v.get("bvid") or ""
        sub = _subtitle_cell(catalog_folder, bvid)
        lines.append(
            f"| {fmt_plays(v.get('plays'))} | {title} | {v.get('series')} | "
            f"{v.get('date') or '-'} | [{bvid or 'link'}]({v.get('url') or ''}) | {sub} |"
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
            series_dir / f"{slugify(series)}.md",
            series,
            vs,
            up_name,
            uid,
            catalog_folder=folder,
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
