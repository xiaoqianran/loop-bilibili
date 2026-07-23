"""SRT export."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def parse_seconds(val: Any) -> float:
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().rstrip("sS")
    try:
        return float(s)
    except ValueError:
        return 0.0


def format_srt_timestamp(sec: float) -> str:
    if sec < 0:
        sec = 0.0
    # SRT uses comma millis
    total_ms = int(round(sec * 1000))
    h, rem = divmod(total_ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def cues_to_srt(cues: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    n = 0
    for c in cues:
        text = str(c.get("content") or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not text:
            continue
        n += 1
        fr = c.get("from_sec", c.get("from"))
        to = c.get("to_sec", c.get("to"))
        lines.append(str(n))
        lines.append(
            f"{format_srt_timestamp(parse_seconds(fr))} --> "
            f"{format_srt_timestamp(parse_seconds(to))}"
        )
        lines.append(text)
        lines.append("")
    return "\n".join(lines)


def write_srt(path: Path, cues: list[dict[str, Any]]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cues_to_srt(cues), encoding="utf-8")
