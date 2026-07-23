"""Batch entrypoints — thin façade over SubtitlePipeline (compat)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .pipeline import (
    FetchFn,
    load_done,
    load_results,
    run_batch,
    upsert_result,
    utc_now,
    write_json,
)
from .util import load_bvids_from_items

__all__ = [
    "run_batch",
    "load_bvids_from_catalog",
    "load_done",
    "load_results",
    "upsert_result",
    "write_json",
    "utc_now",
    "FetchFn",
]


def load_bvids_from_catalog(path: Path) -> list[str]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("catalog JSON must be a list")
    return load_bvids_from_items(data)
