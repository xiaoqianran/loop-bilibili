"""抓取进度 / 断点续跑落盘。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PROGRESS_NAME = ".progress.json"
PARTIAL_NAME = "all.partial.json"


def load_progress(folder: Path) -> dict[str, Any]:
    path = folder / PROGRESS_NAME
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_progress(folder: Path, data: dict[str, Any]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / PROGRESS_NAME).write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_partial(folder: Path) -> list[dict]:
    path = folder / PARTIAL_NAME
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_partial(folder: Path, videos: list[dict]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / PARTIAL_NAME).write_text(
        json.dumps(videos, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def clear_progress(folder: Path) -> None:
    for name in (PROGRESS_NAME, PARTIAL_NAME):
        p = folder / name
        if p.exists():
            p.unlink()
