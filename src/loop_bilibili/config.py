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


def load_dotenv_files(*extra: str | Path) -> None:
    """
    Load KEY=VAL from gitignored env files into os.environ (no overwrite).

    Paths (first wins for a key):
      1. explicit *extra
      2. ./ .env  (cwd)
      3. ~/.config/loop-bilibili/env
    """
    candidates: list[Path] = [Path(p) for p in extra if p]
    candidates.extend(
        [
            Path.cwd() / ".env",
            Path.home() / ".config" / "loop-bilibili" / "env",
        ]
    )
    seen: set[Path] = set()
    for path in candidates:
        try:
            path = path.resolve()
        except OSError:
            continue
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        try:
            for raw in path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip("'").strip('"')
                if key and key not in os.environ:
                    os.environ[key] = val
        except OSError:
            continue


def _as_str_list(value: Any) -> list[str]:
    """Normalize creators: strings, ints, or [{mid=...}] tables → mid strings."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for x in value:
            if isinstance(x, dict):
                mid = str(x.get("mid") or x.get("slug") or "").strip()
                if mid:
                    out.append(mid)
            else:
                s = str(x).strip()
                if s:
                    out.append(s)
        return out
    text = str(value).strip()
    return [text] if text else []


def load_config(path: str | Path | None = None) -> AppConfig:
    """
    Load AppConfig from TOML path.

    Cookie resolution order (first non-empty wins):
      1. BILI_COOKIE environment variable
      2. gitignored .env / ~/.config/loop-bilibili/env  (auto-loaded)
      3. config.runtime.cookie

    With SESSDATA present, homepage rcmd is personalized for that account.
    """
    cfg_path = Path(path) if path else Path("config.toml")
    # .env next to config.toml + cwd defaults
    env_near = cfg_path.resolve().parent / ".env" if cfg_path else None
    load_dotenv_files(*( [env_near] if env_near else [] ))

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
    prefer = data.get("preference") or {}

    return AppConfig(
        database_path=str(db.get("path") or "data/v2/loop.db"),
        creators=_as_str_list(sources.get("creators")),
        homepage_enabled=bool(sources.get("homepage_enabled", True)),
        homepage_pages=int(sources.get("homepage_pages") or 1),
        homepage_ps=int(sources.get("homepage_ps") or 12),
        homepage_interval_s=float(
            sources.get("homepage_interval_s")
            if sources.get("homepage_interval_s") is not None
            else 600.0
        ),
        poll_interval=float(worker.get("poll_interval") or 2.0),
        subtitle_language=str(worker.get("subtitle_language") or "zh"),
        job_delay=float(worker.get("job_delay") if worker.get("job_delay") is not None else 0.5),
        job_jitter=float(
            worker.get("job_jitter") if worker.get("job_jitter") is not None else 0.15
        ),
        retry_delay=float(
            worker.get("retry_delay") if worker.get("retry_delay") is not None else 60.0
        ),
        risk_base_delay=float(
            worker.get("risk_base_delay")
            if worker.get("risk_base_delay") is not None
            else 15.0
        ),
        risk_max_delay=float(
            worker.get("risk_max_delay")
            if worker.get("risk_max_delay") is not None
            else 300.0
        ),
        jobs_per_cycle=int(
            worker.get("jobs_per_cycle")
            if worker.get("jobs_per_cycle") is not None
            else 50
        ),
        require_cookie=bool(runtime.get("require_cookie", False)),
        cookie=cookie_env or cookie_cfg,
        preference_enabled=bool(prefer.get("enabled", True)),
        preference_path=str(prefer.get("path") or "preferences.toml"),
        raw=data,
    )
