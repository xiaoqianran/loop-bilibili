"""Load PreferenceProfile from TOML (stdlib tomllib / tomli)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from .models import Interest, PreferenceProfile

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    try:
        import tomli as tomllib  # type: ignore
    except ImportError:
        tomllib = None  # type: ignore


def _str_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    if isinstance(value, (list, tuple)):
        return tuple(str(x).strip() for x in value if str(x).strip())
    return (str(value).strip(),) if str(value).strip() else ()


def profile_from_mapping(data: dict[str, Any]) -> PreferenceProfile:
    """Build profile from a decoded TOML/dict structure."""
    root = data.get("preference") if "preference" in data else data
    if not isinstance(root, dict):
        raise ValueError("preference root must be a table")

    raw_interests = root.get("interests") or data.get("interests") or []
    if not isinstance(raw_interests, list) or not raw_interests:
        raise ValueError("preference.interests must be a non-empty array")

    interests: list[Interest] = []
    for i, item in enumerate(raw_interests):
        if not isinstance(item, dict):
            raise ValueError(f"interests[{i}] must be a table")
        interests.append(
            Interest(
                id=str(item.get("id") or f"interest_{i}"),
                weight=float(item.get("weight") if item.get("weight") is not None else 1.0),
                keywords=_str_tuple(item.get("keywords")),
                related=_str_tuple(item.get("related")),
            )
        )

    return PreferenceProfile(
        interests=tuple(interests),
        must_not=_str_tuple(root.get("must_not")),
        threshold=float(
            root.get("threshold") if root.get("threshold") is not None else 0.35
        ),
        related_weight=float(
            root.get("related_weight")
            if root.get("related_weight") is not None
            else 0.65
        ),
        soft_weight=float(
            root.get("soft_weight") if root.get("soft_weight") is not None else 0.40
        ),
        min_term_len=int(
            root.get("min_term_len") if root.get("min_term_len") is not None else 2
        ),
    )


def load_preference_profile(path: str | Path) -> PreferenceProfile:
    """
    Load preferences from a TOML file.

    Supports either:
      [preference]
      [[preference.interests]]
    or top-level [[interests]] with [preference] thresholds.
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"preference file not found: {p}")
    if tomllib is None:
        raise RuntimeError("tomllib/tomli unavailable; use Python 3.11+")
    with p.open("rb") as fh:
        data = tomllib.load(fh) or {}
    return profile_from_mapping(data)
