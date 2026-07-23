"""
Pack local batch outputs into a GitHub-friendly subtitle dataset.

Why pack?
  Raw batch dirs keep full cue arrays in items/ + results.json (5–10× bloat).
  For GitHub we publish only:
    - srt/     canonical timed subtitles
    - txt/     plain text (optional, good for RAG)
    - index.jsonl  slim per-video metadata (no cue bodies)
    - meta.json    UP-level stats

Layout (data repo root)::

    README.md
    NOTICE
    dataset.json
    ups/{mid}-{name}/
      meta.json
      index.jsonl
      srt/{bvid}.srt
      txt/{bvid}.txt
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .srt import cues_to_srt

SLUG_RE = re.compile(r"^(\d+)-(.+)$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_slug(name: str) -> tuple[str, str]:
    """'280780745-张小珺商业访谈录' → (mid, name)."""
    m = SLUG_RE.match(name.strip())
    if m:
        return m.group(1), m.group(2)
    return "", name.strip()


def cues_to_txt(cues: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for c in cues:
        t = str(c.get("content") or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if t:
            lines.append(t)
    return "\n".join(lines) + ("\n" if lines else "")


def slim_row(row: dict[str, Any], *, has_srt: bool, has_txt: bool) -> dict[str, Any]:
    bvid = str(row.get("bvid") or "")
    out: dict[str, Any] = {
        "bvid": bvid,
        "status": row.get("status") or "",
        "title": row.get("title") or "",
        "cue_count": int(row.get("cue_count") or 0),
        "lan": row.get("lan") or "",
        "source": row.get("source") or "",
        "author": row.get("author") or "",
        "aid": row.get("aid"),
        "cid": row.get("cid"),
    }
    if row.get("error"):
        out["error"] = row["error"]
    if row.get("reason"):
        out["reason"] = row["reason"]
    if has_srt and bvid:
        out["srt"] = f"srt/{bvid}.srt"
    if has_txt and bvid:
        out["txt"] = f"txt/{bvid}.txt"
    return out


def load_rows_from_batch_dir(src: Path) -> list[dict[str, Any]]:
    """Prefer results.json; fall back to items/*.json (deduped by bvid)."""
    src = Path(src)
    results_path = src / "results.json"
    if results_path.exists():
        raw = json.loads(results_path.read_text(encoding="utf-8"))
        if isinstance(raw, list) and raw:
            return [r for r in raw if isinstance(r, dict)]

    items_dir = src / "items"
    if not items_dir.is_dir():
        return []
    by_bvid: dict[str, dict[str, Any]] = {}
    for p in sorted(items_dir.glob("*.json")):
        try:
            row = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(row, dict):
            continue
        bvid = str(row.get("bvid") or p.stem)
        if bvid:
            row.setdefault("bvid", bvid)
            by_bvid[bvid] = row
    return list(by_bvid.values())


def load_catalog_meta(catalogs: Path | None, slug: str) -> dict[str, Any]:
    if not catalogs:
        return {}
    meta_path = Path(catalogs) / slug / "meta.json"
    if not meta_path.exists():
        return {}
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def pack_up(
    src: Path,
    dest_up: Path,
    *,
    write_txt: bool = True,
    catalogs: Path | None = None,
    skip_empty_up: bool = False,
) -> dict[str, Any] | None:
    """
    Pack one batch output directory into ups/{slug}/.

    Returns UP meta dict, or None if skipped (no ok rows and skip_empty_up).
    """
    src = Path(src)
    dest_up = Path(dest_up)
    slug = src.name
    mid, name = parse_slug(slug)
    rows = load_rows_from_batch_dir(src)
    if not rows:
        return None

    ok_rows = [
        r
        for r in rows
        if r.get("status") == "ok" and (r.get("data") or (src / "srt" / f"{r.get('bvid')}.srt").exists())
    ]
    if skip_empty_up and not ok_rows:
        return None

    if dest_up.exists():
        shutil.rmtree(dest_up)
    srt_dir = dest_up / "srt"
    txt_dir = dest_up / "txt"
    srt_dir.mkdir(parents=True, exist_ok=True)
    if write_txt:
        txt_dir.mkdir(parents=True, exist_ok=True)

    index_lines: list[str] = []
    ok_count = 0
    empty_count = 0
    error_count = 0
    total_cues = 0
    srt_bytes = 0
    txt_bytes = 0

    for row in rows:
        status = row.get("status") or ""
        bvid = str(row.get("bvid") or "")
        if status == "ok":
            ok_count += 1
        elif status == "empty":
            empty_count += 1
        elif status == "error":
            error_count += 1

        has_srt = False
        has_txt = False
        cues = row.get("data") if isinstance(row.get("data"), list) else None
        existing_srt = src / "srt" / f"{bvid}.srt" if bvid else None

        if status == "ok" and bvid:
            srt_text = ""
            if existing_srt and existing_srt.exists():
                srt_text = existing_srt.read_text(encoding="utf-8")
            elif cues:
                srt_text = cues_to_srt(cues)
            if srt_text.strip():
                sp = srt_dir / f"{bvid}.srt"
                sp.write_text(srt_text if srt_text.endswith("\n") else srt_text + "\n", encoding="utf-8")
                srt_bytes += sp.stat().st_size
                has_srt = True
                if not row.get("cue_count") and cues:
                    row = {**row, "cue_count": len(cues)}
                total_cues += int(row.get("cue_count") or 0)

            if write_txt:
                if cues:
                    txt = cues_to_txt(cues)
                elif has_srt:
                    # strip timestamps from srt as fallback
                    txt = _srt_to_plain(srt_text)
                else:
                    txt = ""
                if txt.strip():
                    tp = txt_dir / f"{bvid}.txt"
                    tp.write_text(txt if txt.endswith("\n") else txt + "\n", encoding="utf-8")
                    txt_bytes += tp.stat().st_size
                    has_txt = True

        slim = slim_row(row, has_srt=has_srt, has_txt=has_txt)
        index_lines.append(json.dumps(slim, ensure_ascii=False))

    (dest_up / "index.jsonl").write_text(
        "\n".join(index_lines) + ("\n" if index_lines else ""), encoding="utf-8"
    )

    cat = load_catalog_meta(catalogs, slug)
    meta: dict[str, Any] = {
        "slug": slug,
        "mid": mid or cat.get("uid") or "",
        "name": name or cat.get("name") or slug,
        "space_url": cat.get("space_url")
        or (f"https://space.bilibili.com/{mid}" if mid else ""),
        "video_count": len(rows),
        "ok_count": ok_count,
        "empty_count": empty_count,
        "error_count": error_count,
        "total_cues": total_cues,
        "srt_bytes": srt_bytes,
        "txt_bytes": txt_bytes,
        "has_txt": write_txt,
        "packed_at": utc_now(),
        "source_tool": "loop-bilibili pack-subtitles",
    }
    if cat.get("total") is not None:
        meta["catalog_total"] = cat.get("total")
    if cat.get("series_count") is not None:
        meta["series_count"] = cat.get("series_count")

    (dest_up / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return meta


def _srt_to_plain(srt: str) -> str:
    lines: list[str] = []
    for line in srt.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.isdigit():
            continue
        if "-->" in s:
            continue
        lines.append(s)
    return "\n".join(lines) + ("\n" if lines else "")


def discover_batch_dirs(src_root: Path) -> list[Path]:
    src_root = Path(src_root)
    if not src_root.is_dir():
        return []
    out: list[Path] = []
    for p in sorted(src_root.iterdir()):
        if not p.is_dir():
            continue
        if (p / "results.json").exists() or (p / "items").is_dir():
            out.append(p)
    return out


def write_dataset_readme(out_root: Path, ups: list[dict[str, Any]]) -> None:
    lines = [
        "# data/subtitles — packed subtitle archive",
        "",
        "B 站 UP 字幕瘦归档，由 **loop-bilibili** `main.py pack-subtitles` 生成（SubBatch HTTP）。",
        "",
        "> **非官方**。字幕多为平台 AI/CC 轨；版权归原 UP / B 站。仅供个人学习、检索与研究。",
        "",
        "完整方案见仓库 `docs/DATASET.md`。原始抓取工作区在 `data/subtitle/`（可 gitignore 大 JSON）。",
        "",
        "## 布局",
        "",
        "```text",
        "ups/{mid}-{name}/",
        "  meta.json       # UP 统计",
        "  index.jsonl     # 每行一个视频的瘦元数据（无 cue 正文）",
        "  srt/{bvid}.srt  # 带时间轴字幕",
        "  txt/{bvid}.txt  # 纯文本（便于 RAG / 全文检索）",
        "dataset.json      # 全局清单",
        "```",
        "",
        "## 已收录",
        "",
        "| slug | ok | empty | cues | srt |",
        "|------|----|-------|------|-----|",
    ]
    for u in ups:
        srt_mb = (u.get("srt_bytes") or 0) / 1024 / 1024
        lines.append(
            f"| `{u.get('slug')}` | {u.get('ok_count', 0)} | {u.get('empty_count', 0)} | "
            f"{u.get('total_cues', 0)} | {srt_mb:.1f} MB |"
        )
    lines += [
        "",
        f"共 **{len(ups)}** 个 UP，打包时间见 `dataset.json`。",
        "",
        "## 重新打包",
        "",
        "```bash",
        "python3 main.py pack-subtitles \\",
        "  --src-root data/subtitle \\",
        "  --catalogs catalogs \\",
        "  -o data/subtitles",
        "```",
        "",
    ]
    (out_root / "README.md").write_text("\n".join(lines), encoding="utf-8")


NOTICE = """# NOTICE

This directory contains **subtitle text** extracted from public Bilibili videos
via unofficial web APIs (AI / CC tracks when available).

- Original video and subtitle rights belong to the respective uploaders and
  Bilibili. This archive is for **personal study / research / search** only.
- Do **not** use for commercial redistribution or as a substitute for the
  official player when the uploader requires a paid course (cheese / 充电).
- Tool: https://github.com/xiaoqianran/loop-bilibili

If you are a rights holder and want content removed, open an issue or contact
the repository owner.
"""


def pack_dataset(
    src_root: Path | Iterable[Path],
    out_root: Path,
    *,
    write_txt: bool = True,
    catalogs: Path | None = None,
    skip_empty_up: bool = False,
    clean: bool = True,
) -> dict[str, Any]:
    """
    Pack many UP batch dirs into a publishable data-repo tree.
    """
    if isinstance(src_root, (str, Path)):
        dirs = discover_batch_dirs(Path(src_root))
    else:
        dirs = [Path(p) for p in src_root]

    out_root = Path(out_root)
    if clean and out_root.exists():
        # only remove ups/ + manifests we own; keep .git if present
        ups_dir = out_root / "ups"
        if ups_dir.exists():
            shutil.rmtree(ups_dir)
        for name in ("dataset.json", "README.md", "NOTICE"):
            p = out_root / name
            if p.exists() and p.is_file():
                p.unlink()
    out_root.mkdir(parents=True, exist_ok=True)
    ups_dir = out_root / "ups"
    ups_dir.mkdir(exist_ok=True)

    packed: list[dict[str, Any]] = []
    for src in dirs:
        meta = pack_up(
            src,
            ups_dir / src.name,
            write_txt=write_txt,
            catalogs=catalogs,
            skip_empty_up=skip_empty_up,
        )
        if meta:
            packed.append(meta)
            print(
                f"packed {meta['slug']}: ok={meta['ok_count']} "
                f"empty={meta['empty_count']} cues={meta['total_cues']} "
                f"srt={meta['srt_bytes']/1024:.0f}KB",
                flush=True,
            )

    dataset = {
        "name": "loop-bilibili/data/subtitles",
        "description": "Packed Bilibili UP subtitles (SRT + slim index)",
        "packed_at": utc_now(),
        "up_count": len(packed),
        "ok_total": sum(u.get("ok_count", 0) for u in packed),
        "empty_total": sum(u.get("empty_count", 0) for u in packed),
        "error_total": sum(u.get("error_count", 0) for u in packed),
        "cues_total": sum(u.get("total_cues", 0) for u in packed),
        "srt_bytes_total": sum(u.get("srt_bytes", 0) for u in packed),
        "txt_bytes_total": sum(u.get("txt_bytes", 0) for u in packed),
        "layout": {
            "ups": "ups/{mid}-{name}/",
            "index": "index.jsonl",
            "srt": "srt/{bvid}.srt",
            "txt": "txt/{bvid}.txt",
        },
        "ups": [
            {
                "slug": u["slug"],
                "mid": u.get("mid"),
                "name": u.get("name"),
                "ok_count": u.get("ok_count"),
                "empty_count": u.get("empty_count"),
                "total_cues": u.get("total_cues"),
                "path": f"ups/{u['slug']}",
            }
            for u in packed
        ],
        "tool": "loop-bilibili",
        "plugin": "bili_subbatch",
        "tool_url": "https://github.com/xiaoqianran/loop-bilibili",
    }
    (out_root / "dataset.json").write_text(
        json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out_root / "NOTICE").write_text(NOTICE, encoding="utf-8")
    write_dataset_readme(out_root, packed)
    print(
        f"dataset -> {out_root} ups={len(packed)} "
        f"ok={dataset['ok_total']} cues={dataset['cues_total']} "
        f"srt={dataset['srt_bytes_total']/1024/1024:.1f}MB",
        flush=True,
    )
    return dataset
