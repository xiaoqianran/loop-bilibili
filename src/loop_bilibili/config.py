"""Load config.toml (stdlib tomllib / tomli fallback)."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from .models import DEFAULT_AI_MODELS, AiConfig, AppConfig

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


def _parse_models(ai: dict[str, Any]) -> list[str]:
    """
    Resolve ordered model list.

    Priority for the *list*:
      1. AI_MODELS env (comma / semicolon / newline separated)
      2. [ai].models array in config.toml
      3. DEFAULT_AI_MODELS dual stack

    Preferred / default (first position) override:
      AI_DEFAULT_MODEL | [ai].default_model
      (AI_MODEL / LLM_MODEL only used when no multi-list is configured)
    """
    env_multi = (
        (os.environ.get("AI_MODELS") or "").strip()
        or (os.environ.get("LLM_MODELS") or "").strip()
    )
    models: list[str] = []
    list_from_env = bool(env_multi)
    list_from_cfg = isinstance(ai.get("models"), (list, tuple)) and bool(ai.get("models"))

    if env_multi:
        for part in re_split_models(env_multi):
            if part:
                models.append(part)
    elif list_from_cfg:
        for x in ai["models"]:
            s = str(x).strip()
            if s:
                models.append(s)
    else:
        models = list(DEFAULT_AI_MODELS)

    # Single-model env only when user did not specify a multi-list
    env_one = (
        (os.environ.get("AI_MODEL") or "").strip()
        or (os.environ.get("LLM_MODEL") or "").strip()
    )
    default_override = (
        (os.environ.get("AI_DEFAULT_MODEL") or "").strip()
        or str(ai.get("default_model") or "").strip()
    )

    if not list_from_env and not list_from_cfg:
        # legacy single AI_MODEL → put first (or sole custom)
        preferred = default_override or env_one
        if preferred:
            if preferred in models:
                models = [preferred] + [m for m in models if m != preferred]
            else:
                models = [preferred] + [m for m in DEFAULT_AI_MODELS if m != preferred]
    else:
        # multi-list present: only AI_DEFAULT_MODEL / [ai].default_model reorders
        if default_override:
            if default_override in models:
                models = [default_override] + [m for m in models if m != default_override]
            else:
                models = [default_override] + models

    seen: set[str] = set()
    out: list[str] = []
    for m in models:
        if m not in seen:
            out.append(m)
            seen.add(m)
    return out or list(DEFAULT_AI_MODELS)


def re_split_models(text: str) -> list[str]:
    parts: list[str] = []
    for chunk in text.replace(";", ",").replace("\n", ",").split(","):
        s = chunk.strip()
        if s:
            parts.append(s)
    return parts


def _load_ai_config(data: dict[str, Any]) -> AiConfig:
    """
    AI settings from [ai] + env.

    API key resolution (first non-empty):
      AI_API_KEY | LLM_API_KEY | OPENAI_API_KEY | NVIDIA_API_KEY
    Base URL override: AI_BASE_URL | LLM_BASE_URL
    Models: see _parse_models
    Enable override: AI_ENABLED=true|false
    """
    ai = data.get("ai") or {}
    key = (
        (os.environ.get("AI_API_KEY") or "").strip()
        or (os.environ.get("LLM_API_KEY") or "").strip()
        or (os.environ.get("OPENAI_API_KEY") or "").strip()
        or (os.environ.get("NVIDIA_API_KEY") or "").strip()
        or str(ai.get("api_key") or "").strip()
    )
    base = (
        (os.environ.get("AI_BASE_URL") or "").strip()
        or (os.environ.get("LLM_BASE_URL") or "").strip()
        or str(ai.get("base_url") or "").strip()
    )
    models = _parse_models(ai if isinstance(ai, dict) else {})

    env_flag = (os.environ.get("AI_ENABLED") or "").strip().lower()
    if env_flag in ("0", "false", "no", "off"):
        enabled = False
    elif env_flag in ("1", "true", "yes", "on"):
        enabled = True
    elif "enabled" in ai:
        enabled = bool(ai.get("enabled"))
    else:
        enabled = False

    return AiConfig(
        enabled=enabled,
        base_url=base.rstrip("/"),
        api_key=key,
        models=models,
        temperature=float(
            ai.get("temperature") if ai.get("temperature") is not None else 0.4
        ),
        max_tokens=int(ai.get("max_tokens") or 8192),
        max_subtitle_chars=int(ai.get("max_subtitle_chars") or 80_000),
        timeout_s=float(ai.get("timeout_s") or 180.0),
        mode=str(ai.get("mode") or "mermaid"),
        custom_instruction=str(ai.get("custom_instruction") or ""),
        request_retries=int(
            ai.get("request_retries")
            if ai.get("request_retries") is not None
            else 2
        ),
        request_retry_backoff_s=float(
            ai.get("request_retry_backoff_s")
            if ai.get("request_retry_backoff_s") is not None
            else 2.0
        ),
        max_attempts=int(ai.get("max_attempts") if ai.get("max_attempts") is not None else 3),
        job_retry_delay_s=float(
            ai.get("job_retry_delay_s")
            if ai.get("job_retry_delay_s") is not None
            else 60.0
        ),
    )


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
        ai=_load_ai_config(data),
        raw=data,
    )
