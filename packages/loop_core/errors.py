"""错误类型与错误日志。"""

from __future__ import annotations

import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class FetchError(Exception):
    """可分类的抓取失败，供重试策略使用。"""

    def __init__(self, message: str, category: str = "unknown"):
        super().__init__(message)
        # rate | risk412 | sign352 | empty_soft | timeout | hard | unknown
        self.category = category


def log_error(
    error_log: str | Path,
    message: str,
    *,
    exc: Optional[BaseException] = None,
    context: Optional[str] = None,
) -> None:
    path = Path(error_log)
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    lines = [f"[{ts}] {message}"]
    if context:
        lines.append(f"  context: {context}")
    if exc is not None:
        lines.append(f"  exception: {type(exc).__name__}: {exc}")
        lines.append("  traceback:")
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        for line in tb.rstrip().splitlines():
            lines.append(f"    {line}")
    lines.append("")
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
