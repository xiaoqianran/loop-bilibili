"""WBI signing — same algorithm/table as SubBatch background.js."""

from __future__ import annotations

import hashlib
import time
import urllib.parse
from typing import Any, Mapping

# SubBatch background.js mixinKeyEncTab (must stay byte-identical)
MIXIN_KEY_ENC_TAB: tuple[int, ...] = (
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40, 61,
    26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36,
    20, 34, 44, 52,
)

_FORBIDDEN = frozenset("!'()*")


def key_from_url(url: str) -> str:
    """img_url/sub_url → key filename without extension (SubBatch style)."""
    name = url.rsplit("/", 1)[-1]
    if "." in name:
        name = name.rsplit(".", 1)[0]
    return name


def mixin_key(img_key: str, sub_key: str) -> str:
    raw = img_key + sub_key
    if max(MIXIN_KEY_ENC_TAB) >= len(raw):
        # defensive: keys shorter than expected table span
        raw = raw.ljust(max(MIXIN_KEY_ENC_TAB) + 1, "0")
    return "".join(raw[i] for i in MIXIN_KEY_ENC_TAB)[:32]


def enc_wbi(
    params: Mapping[str, Any],
    img_key: str,
    sub_key: str,
    *,
    wts: int | None = None,
) -> str:
    """
    Build signed query string: sorted params + wts + w_rid=md5(...).

    `wts` is injectable for deterministic tests; production passes None → now.
    """
    data = {str(k): v for k, v in params.items()}
    data["wts"] = int(time.time()) if wts is None else int(wts)
    parts: list[str] = []
    for key in sorted(data.keys()):
        val = "".join(c for c in str(data[key]) if c not in _FORBIDDEN)
        parts.append(
            f"{urllib.parse.quote(key, safe='')}="
            f"{urllib.parse.quote(val, safe='')}"
        )
    query = "&".join(parts)
    w_rid = hashlib.md5((query + mixin_key(img_key, sub_key)).encode()).hexdigest()
    return f"{query}&w_rid={w_rid}"
