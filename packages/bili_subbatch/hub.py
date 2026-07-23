"""
Per-UP human hub README — ordered full list + txt preview + srt/txt links.

Problem: catalogs/ only show top-20 tables; data/subtitles is bvid→file
with no human join. Hub lives next to srt/txt so relative links work.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

DEFAULT_PREVIEW_CHARS = 180
BVID_RE = re.compile(r"^BV[\w]+$", re.I)


@dataclass
class HubVideo:
    """One row in the UP hub index."""

    bvid: str
    title: str = ""
    url: str = ""
    status: str = "missing"  # ok | empty | error | missing
    cue_count: int = 0
    date: str = ""
    series: str = ""
    srt: str | None = None  # relative path under up dir
    txt: str | None = None
    preview: str = ""
    reason: str = ""


def play_url(bvid: str) -> str:
    bvid = (bvid or "").strip()
    if not bvid:
        return ""
    return f"https://www.bilibili.com/video/{bvid}"


def truncate_preview(text: str, max_chars: int = DEFAULT_PREVIEW_CHARS) -> str:
    """Single-line-ish preview: collapse whitespace, hard cap, ellipsis."""
    if not text:
        return ""
    t = " ".join(str(text).replace("\r", "\n").split())
    if len(t) <= max_chars:
        return t
    # break on word boundary when possible
    cut = t[: max_chars - 1].rstrip()
    return cut + "…"


def read_txt_preview(path: Path, max_chars: int = DEFAULT_PREVIEW_CHARS) -> str:
    if not path.is_file():
        return ""
    try:
        # only read a small prefix for large files
        with path.open("r", encoding="utf-8", errors="replace") as f:
            chunk = f.read(max_chars * 4)
    except OSError:
        return ""
    return truncate_preview(chunk, max_chars=max_chars)


def load_index_map(up_dir: Path) -> dict[str, dict[str, Any]]:
    """bvid → index.jsonl row."""
    path = Path(up_dir) / "index.jsonl"
    out: dict[str, dict[str, Any]] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        bvid = str(row.get("bvid") or "").strip()
        if bvid:
            out[bvid] = row
    return out


def load_catalog_order(catalog_dir: Path | None) -> list[dict[str, Any]]:
    """Ordered list of {bvid, title, url, date, series, plays} from all.json."""
    if not catalog_dir:
        return []
    path = Path(catalog_dir) / "all.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, list):
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for it in data:
        if not isinstance(it, dict):
            continue
        bvid = str(it.get("bvid") or "").strip()
        if not bvid or bvid in seen:
            continue
        seen.add(bvid)
        url = str(it.get("url") or "") or play_url(bvid)
        out.append(
            {
                "bvid": bvid,
                "title": str(it.get("title") or ""),
                "url": url,
                "date": str(it.get("date") or ""),
                "series": str(it.get("series") or ""),
                "plays": it.get("plays"),
            }
        )
    return out


def merge_order(
    catalog_items: list[dict[str, Any]],
    index_map: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], str]:
    """
    Prefer catalog all.json order; append index-only bvids.
    Returns (base items, order_source label).
    """
    if catalog_items:
        ordered = list(catalog_items)
        seen = {str(x["bvid"]) for x in ordered}
        extra = 0
        for bvid, row in index_map.items():
            if bvid in seen:
                continue
            ordered.append(
                {
                    "bvid": bvid,
                    "title": str(row.get("title") or ""),
                    "url": play_url(bvid),
                    "date": "",
                    "series": "",
                }
            )
            extra += 1
        src = "catalog/all.json"
        if extra:
            src += f" + {extra} index-only"
        return ordered, src

    # index.jsonl file order
    ordered = []
    for bvid, row in index_map.items():
        ordered.append(
            {
                "bvid": bvid,
                "title": str(row.get("title") or ""),
                "url": play_url(bvid),
                "date": "",
                "series": "",
            }
        )
    # also discover loose srt/txt files not in index
    up_dir_placeholder = None  # filled by caller via scan
    return ordered, "index.jsonl"


def scan_loose_bvids(up_dir: Path) -> set[str]:
    found: set[str] = set()
    for sub in ("srt", "txt"):
        d = Path(up_dir) / sub
        if not d.is_dir():
            continue
        for p in d.iterdir():
            if p.suffix.lower() in (".srt", ".txt") and BVID_RE.match(p.stem):
                # normalize BV prefix case
                stem = p.stem
                found.add("BV" + stem[2:] if len(stem) > 2 else stem)
    return found


def build_hub_videos(
    up_dir: Path,
    *,
    catalog_dir: Path | None = None,
    preview_chars: int = DEFAULT_PREVIEW_CHARS,
) -> tuple[list[HubVideo], str]:
    """
    Build full ordered hub list from catalog (preferred) + index + filesystem.
    """
    up_dir = Path(up_dir)
    index_map = load_index_map(up_dir)
    catalog_items = load_catalog_order(catalog_dir)
    base, order_source = merge_order(catalog_items, index_map)

    # if no catalog and empty index, invent from loose files
    if not base:
        loose = sorted(scan_loose_bvids(up_dir))
        base = [
            {"bvid": b, "title": "", "url": play_url(b), "date": "", "series": ""}
            for b in loose
        ]
        order_source = "filesystem srt/txt"

    seen = {str(x["bvid"]) for x in base}
    for bvid in sorted(scan_loose_bvids(up_dir)):
        if bvid not in seen:
            base.append(
                {
                    "bvid": bvid,
                    "title": index_map.get(bvid, {}).get("title") or "",
                    "url": play_url(bvid),
                    "date": "",
                    "series": "",
                }
            )
            seen.add(bvid)
            if "filesystem" not in order_source:
                order_source += " + loose files"

    videos: list[HubVideo] = []
    for it in base:
        bvid = str(it["bvid"])
        idx = index_map.get(bvid) or {}
        srt_rel = f"srt/{bvid}.srt"
        txt_rel = f"txt/{bvid}.txt"
        has_srt = (up_dir / srt_rel).is_file()
        has_txt = (up_dir / txt_rel).is_file()
        status = str(idx.get("status") or "")
        if not status:
            if has_srt or has_txt:
                status = "ok"
            else:
                status = "missing"
        title = str(it.get("title") or idx.get("title") or bvid)
        preview = read_txt_preview(up_dir / txt_rel, max_chars=preview_chars) if has_txt else ""
        cue = int(idx.get("cue_count") or 0)
        reason = str(idx.get("reason") or idx.get("error") or "")
        videos.append(
            HubVideo(
                bvid=bvid,
                title=title,
                url=str(it.get("url") or play_url(bvid)),
                status=status,
                cue_count=cue,
                date=str(it.get("date") or ""),
                series=str(it.get("series") or ""),
                srt=srt_rel if has_srt else None,
                txt=txt_rel if has_txt else None,
                preview=preview,
                reason=reason,
            )
        )
    return videos, order_source


def _md_escape_cell(s: str) -> str:
    return (s or "").replace("|", "\\|").replace("\n", " ")


def render_hub_readme(
    *,
    slug: str,
    mid: str,
    name: str,
    space_url: str,
    videos: list[HubVideo],
    order_source: str,
    meta: dict[str, Any] | None = None,
    preview_chars: int = DEFAULT_PREVIEW_CHARS,
) -> str:
    """Pure markdown renderer (testable)."""
    meta = meta or {}
    with_sub = sum(1 for v in videos if v.srt or v.txt)
    no_sub = len(videos) - with_sub
    cues = sum(v.cue_count for v in videos)

    lines: list[str] = [
        f"# {name or slug} · 字幕导航",
        "",
        f"- **slug**: `{slug}`",
        f"- **UID / mid**: `{mid or '—'}`",
        f"- **空间**: {space_url or (f'https://space.bilibili.com/{mid}' if mid else '—')}",
        f"- **视频总数**: **{len(videos)}**（字幕可用 **{with_sub}** · 无字幕 **{no_sub}**）",
        f"- **cues（有字幕条目合计）**: {cues}",
        f"- **排序来源**: `{order_source}`",
        f"- **正文预览**: 每条截取约 {preview_chars} 字（全文见 `txt/`）",
        "",
        "> 本页是给人读的索引：标题 + 播放页 + 字幕预览 + 相对路径。",
        "> 完整时间轴字幕在 `srt/`，纯文本在 `txt/`，机器元数据见 `index.jsonl` / `meta.json`。",
        "",
        "## 全部视频（按序）",
        "",
    ]

    for i, v in enumerate(videos, 1):
        title = _md_escape_cell(v.title) or v.bvid
        play = v.url or play_url(v.bvid)
        if v.srt or v.txt:
            status_label = f"有字幕 · {v.cue_count} cues" if v.cue_count else "有字幕"
        elif v.status == "empty":
            status_label = "无字幕（平台未提供）"
        elif v.status == "error":
            status_label = f"抓取失败{(' · ' + v.reason) if v.reason else ''}"
        else:
            status_label = "无字幕文件"

        links: list[str] = [f"[播放]({play})"]
        if v.txt:
            links.append(f"[txt]({v.txt})")
        if v.srt:
            links.append(f"[srt]({v.srt})")
        link_s = " · ".join(links)

        meta_bits = [f"`{v.bvid}`", status_label]
        if v.date:
            meta_bits.append(v.date)
        if v.series and not str(v.series).startswith("未分类"):
            meta_bits.append(v.series)

        lines.append(f"### {i}. {title}")
        lines.append("")
        lines.append(f"{' · '.join(meta_bits)}")
        lines.append("")
        lines.append(link_s)
        lines.append("")
        if v.preview:
            # blockquote preview
            lines.append(f"> {v.preview}")
            lines.append("")
        elif not (v.srt or v.txt):
            lines.append("> _（无字幕正文）_")
            lines.append("")

    lines += [
        "---",
        "",
        "## 文件",
        "",
        "- [index.jsonl](index.jsonl) — 机器可读清单",
        "- [meta.json](meta.json) — UP 统计",
        "- [srt/](srt/) · [txt/](txt/)",
        "",
        "由 `loop-bilibili`（`pack-subtitles` / `rebuild-hubs`）自动生成。",
        "",
    ]
    return "\n".join(lines)


def write_up_hub(
    up_dir: Path,
    *,
    catalogs: Path | None = None,
    preview_chars: int = DEFAULT_PREVIEW_CHARS,
) -> Path:
    """Write ups/{slug}/README.md hub. Returns path written."""
    up_dir = Path(up_dir)
    slug = up_dir.name
    mid, name = "", slug
    m = re.match(r"^(\d+)-(.+)$", slug)
    if m:
        mid, name = m.group(1), m.group(2)

    cat_dir = None
    if catalogs:
        cand = Path(catalogs) / slug
        if cand.is_dir():
            cat_dir = cand

    meta: dict[str, Any] = {}
    meta_path = up_dir / "meta.json"
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            meta = {}
    if meta.get("mid"):
        mid = str(meta["mid"])
    if meta.get("name"):
        name = str(meta["name"])
    space = str(meta.get("space_url") or "") or (
        f"https://space.bilibili.com/{mid}" if mid else ""
    )

    videos, order_source = build_hub_videos(
        up_dir, catalog_dir=cat_dir, preview_chars=preview_chars
    )
    text = render_hub_readme(
        slug=slug,
        mid=mid,
        name=name,
        space_url=space,
        videos=videos,
        order_source=order_source,
        meta=meta,
        preview_chars=preview_chars,
    )
    out = up_dir / "README.md"
    out.write_text(text, encoding="utf-8")
    return out


def rebuild_all_hubs(
    archive_root: Path,
    *,
    catalogs: Path | None = None,
    preview_chars: int = DEFAULT_PREVIEW_CHARS,
) -> list[Path]:
    """Regenerate README.md for every ups/* directory."""
    archive_root = Path(archive_root)
    ups = archive_root / "ups"
    if not ups.is_dir():
        return []
    written: list[Path] = []
    for d in sorted(p for p in ups.iterdir() if p.is_dir()):
        written.append(
            write_up_hub(d, catalogs=catalogs, preview_chars=preview_chars)
        )
    return written
