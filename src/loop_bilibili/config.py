"""Load config.toml (stdlib tomllib / tomli fallback)."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from .models import AppConfig

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    try:
        import tomli as tomllib  # type: ignore
    except ImportError:
        tomllib = None  # type: ignore


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(x).strip() for x in value if str(x).strip()]
    text = str(value).strip()
    return [text] if text else []


def load_config(path: str | Path | None = None) -> AppConfig:
    """
    Load AppConfig from TOML path.

    Cookie resolution order: BILI_COOKIE env > config.runtime.cookie.
    """
    cfg_path = Path(path) if path else Path("config.toml")
    data: dict[str, Any] = {}
    if cfg_path.is_file():
        if tomllib is None:
            raise RuntimeError(
                "tomllib/tomli unavailable; use Python 3.11+ or install tomli"
            )
        with cfg_path.open("rb") as fh:
            data = tomllib.load(fh) or {}

    db = data.get("database") or {}
    sources = data.get("sources") or {}
    worker = data.get("worker") or {}
    runtime = data.get("runtime") or {}

    cookie_env = (os.environ.get("BILI_COOKIE") or "").strip()
    cookie_cfg = str(runtime.get("cookie") or "").strip()

    return AppConfig(
        database_path=str(db.get("path") or "data/v2/loop.db"),
        creators=_as_str_list(sources.get("creators")),
        homepage_enabled=bool(sources.get("homepage_enabled", True)),
        homepage_pages=int(sources.get("homepage_pages") or 1),
        homepage_ps=int(sources.get("homepage_ps") or 12),
        poll_interval=float(worker.get("poll_interval") or 2.0),
        subtitle_language=str(worker.get("subtitle_language") or "zh"),
        cookie=cookie_env or cookie_cfg,
        raw=data,
    )
