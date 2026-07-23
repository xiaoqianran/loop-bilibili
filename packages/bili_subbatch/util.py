"""Pure helpers (no network)."""

from __future__ import annotations

import re
from typing import Any, Iterable

BVID_RE = re.compile(r"(BV[\w]+)", re.IGNORECASE)


def _normalize_bvid(raw: str) -> str:
    """Force BV prefix uppercase; keep the rest as matched."""
    if len(raw) < 3:
        return ""
    return "BV" + raw[2:]


def extract_bvid(text: str | None) -> str:
    if not text:
        return ""
    text = str(text).strip()
    if not text:
        return ""
    # plain BV…
    if re.fullmatch(r"BV[\w]+", text, flags=re.I):
        return _normalize_bvid(text)
    m = BVID_RE.search(text)
    if not m:
        return ""
    return _normalize_bvid(m.group(1))


def load_bvids_from_items(items: Iterable[Any]) -> list[str]:
    """Dedupe bvids from catalog-like list of dicts (order preserved)."""
    out: list[str] = []
    seen: set[str] = set()
    for it in items:
        if not isinstance(it, dict):
            continue
        b = extract_bvid(str(it.get("bvid") or ""))
        if not b:
            b = extract_bvid(str(it.get("url") or ""))
        if b and b not in seen:
            seen.add(b)
            out.append(b)
    return out


def pending_keys(all_keys: list[str], done: set[str], *, resume: bool) -> list[str]:
    if not resume:
        return list(all_keys)
    return [k for k in all_keys if k not in done]


def resolve_cid(view: dict[str, Any], page: int = 1) -> int | None:
    """Pick cid from View payload (page is 1-based)."""
    pages = view.get("pages") or []
    if isinstance(pages, list) and pages and 1 <= page <= len(pages):
        cid = pages[page - 1].get("cid")
        return int(cid) if cid is not None else None
    cid = view.get("cid")
    if cid is None:
        return None
    try:
        return int(cid)
    except (TypeError, ValueError):
        return None


def pick_track(subs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Prefer Chinese / AI Chinese tracks, else first."""
    if not subs:
        return None
    for s in subs:
        lan = str(s.get("lan") or "")
        if lan in ("zh-CN", "ai-zh") or lan.startswith("zh") or lan.startswith("ai"):
            return s
    return subs[0]


def is_charge_exclusive_blocked(view: dict[str, Any]) -> bool:
    """Match SubBatch: exclusive and not playable."""
    return bool(view.get("is_upower_exclusive") and not view.get("is_upower_play"))


def to_cues(body: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, c in enumerate(body, 1):
        try:
            fr = float(c.get("from") or 0)
        except (TypeError, ValueError):
            fr = 0.0
        try:
            to = float(c.get("to") or 0)
        except (TypeError, ValueError):
            to = 0.0
        sid = c.get("sid")
        try:
            index = int(sid) if sid is not None else i
        except (TypeError, ValueError):
            index = i
        out.append(
            {
                "index": index,
                "from": f"{fr:.2f}s",
                "to": f"{to:.2f}s",
                "from_sec": fr,
                "to_sec": to,
                "content": str(c.get("content") or ""),
            }
        )
    return out
