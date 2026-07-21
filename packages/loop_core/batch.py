"""批量逐条任务：bvid 列表、完成集合、进度落盘（可单测，无网络）。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from .timeutil import utc_now_iso

BVID_RE = re.compile(r"(BV[\w]+)", re.IGNORECASE)

DONE_NAME = "done.json"
PROGRESS_NAME = ".progress.json"
RESULTS_NAME = "results.json"


def extract_bvid(text: str) -> str:
    if not text:
        return ""
    text = str(text).strip()
    if text.upper().startswith("BV") and "/" not in text and "?" not in text:
        return text if text.startswith("BV") else "BV" + text[2:]
    m = BVID_RE.search(text)
    return m.group(1) if m else ""


def load_bvids_from_catalog(catalog_path: Path) -> list[str]:
    """从 catalog 目录或 all.json 读取 bvid 列表（保持文件顺序）。"""
    path = Path(catalog_path)
    if path.is_dir():
        path = path / "all.json"
    if not path.is_file():
        raise FileNotFoundError(f"catalog all.json not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("all.json must be a list")
    out: list[str] = []
    seen: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        bvid = extract_bvid(str(item.get("bvid") or item.get("url") or ""))
        if bvid and bvid not in seen:
            seen.add(bvid)
            out.append(bvid)
    return out


def load_bvids_from_args(
    *,
    bvid: str | None = None,
    bvids: Iterable[str] | None = None,
    catalog: str | Path | None = None,
    limit: int | None = None,
) -> list[str]:
    """合并 CLI 来源的 bvid 列表。"""
    out: list[str] = []
    seen: set[str] = set()

    def add(x: str) -> None:
        b = extract_bvid(x)
        if b and b not in seen:
            seen.add(b)
            out.append(b)

    if bvid:
        add(bvid)
    if bvids:
        for x in bvids:
            add(str(x))
    if catalog:
        for x in load_bvids_from_catalog(Path(catalog)):
            add(x)
    if limit is not None and limit >= 0:
        out = out[: int(limit)]
    return out


def load_done_set(folder: Path) -> set[str]:
    path = folder / DONE_NAME
    if not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return set()
    if isinstance(data, list):
        return {str(x) for x in data}
    if isinstance(data, dict) and isinstance(data.get("done"), list):
        return {str(x) for x in data["done"]}
    return set()


def save_done_set(folder: Path, done: set[str] | list[str]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    ordered = sorted(set(done))
    (folder / DONE_NAME).write_text(
        json.dumps({"done": ordered, "updated_at": utc_now_iso()}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )


def pending_keys(all_keys: list[str], done: set[str], *, resume: bool) -> list[str]:
    """返回仍需处理的 key；resume=False 时忽略 done（全量重跑）。"""
    if not resume:
        return list(all_keys)
    return [k for k in all_keys if k not in done]


def load_results(folder: Path) -> list[dict[str, Any]]:
    path = folder / RESULTS_NAME
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_results(folder: Path, results: list[dict[str, Any]]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / RESULTS_NAME).write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def upsert_result(results: list[dict[str, Any]], key_field: str, row: dict[str, Any]) -> list[dict[str, Any]]:
    key = row.get(key_field)
    out = [r for r in results if r.get(key_field) != key]
    out.append(row)
    return out


def save_job_progress(folder: Path, data: dict[str, Any]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    data = {**data, "updated_at": utc_now_iso()}
    (folder / PROGRESS_NAME).write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
