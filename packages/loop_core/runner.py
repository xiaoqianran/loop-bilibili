"""执行 opencli 并解析 JSON；识别 B 站风控/限流信号。"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from typing import Any, Sequence, Union

from .errors import FetchError

logger = logging.getLogger(__name__)

# Do NOT use bare "429" / "412" — subtitle JSON timestamps like "429.10s" false-positive.
RATE_LIMIT_MARKERS = (
    "-799",
    "请求过于频繁",
    "too many requests",
    "rate limit",
    "ratelimit",
)
RISK_412_MARKERS = (
    "-412",
    "precondition failed",
    "请求被拦截",
)
SIGN_352_MARKERS = (
    "-352",
    "风控校验失败",
)


def extract_json(text: str) -> Any:
    """从可能夹杂 warning 的 stdout 中提取首个 JSON 数组/对象。"""
    for start_char, end_char in (("[", "]"), ("{", "}")):
        i = text.find(start_char)
        if i < 0:
            continue
        depth = 0
        in_str = False
        esc = False
        for j in range(i, len(text)):
            c = text[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == start_char:
                depth += 1
            elif c == end_char:
                depth -= 1
                if depth == 0:
                    chunk = text[i : j + 1]
                    try:
                        return json.loads(chunk)
                    except json.JSONDecodeError:
                        break
    raise ValueError(f"No JSON found in opencli output:\n{(text or '')[-500:]}")


def classify_failure(text: str, returncode: int | None = None) -> str | None:
    """Classify risk/rate failures. Avoid matching numbers inside large JSON bodies."""
    low = (text or "").lower()
    raw = text or ""

    # Prefer explicit API / opencli error phrasing over bare digits.
    if re.search(r'"code"\s*:\s*-352\b', raw) or "-352" in raw or "风控校验" in raw:
        return "sign352"

    if (
        re.search(r'"code"\s*:\s*-412\b', raw)
        or "-412" in raw
        or "precondition failed" in low
        or "请求被拦截" in raw
    ):
        return "risk412"
    if re.search(r"\bHTTP[/\s]*412\b", raw, re.I) or (
        re.search(r"\b412\b", raw)
        and ("风控" in raw or "intercept" in low or "precondition" in low)
    ):
        return "risk412"

    if re.search(r'"code"\s*:\s*-799\b', raw) or any(
        m.lower() in low for m in RATE_LIMIT_MARKERS
    ):
        return "rate"
    # HTTP 429 only as status token, not as "429.12s" timestamps
    if re.search(r"(?:status|http)[^\n]{0,20}\b429\b", low) or re.search(
        r"\bHTTP[/\s]*429\b", raw, re.I
    ):
        return "rate"

    if returncode is not None and returncode != 0:
        if "timeout" in low or "timed out" in low:
            return "timeout"
    return None


class OpencliRunner:
    """通用 opencli 运行器。"""

    def __init__(self, timeout_seconds: int = 180) -> None:
        self.timeout_seconds = int(timeout_seconds)

    def run(
        self, args: Sequence[str], *, timeout: int | None = None
    ) -> tuple[str, int]:
        cmd = ["opencli", *args]
        timeout = self.timeout_seconds if timeout is None else timeout
        display = " ".join(cmd)
        logger.info("running: %s", display)
        try:
            proc = subprocess.run(
                list(cmd),
                capture_output=True,
                text=True,
                timeout=timeout,
                env=os.environ.copy(),
            )
        except subprocess.TimeoutExpired as e:
            out = (e.stdout or "") + "\n" + (e.stderr or "")
            raise FetchError(
                f"opencli timeout after {timeout}s: {out[-400:]}", "timeout"
            ) from e

        out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")

        # If we already have parseable JSON success body, do not treat embedded
        # numbers (e.g. subtitle timestamps "429.1s") as rate-limit signals.
        if proc.returncode == 0:
            try:
                extract_json(out)
                return out, proc.returncode
            except ValueError:
                pass

        cat = classify_failure(out, proc.returncode)
        if cat:
            raise FetchError(out[-800:] or f"opencli exit {proc.returncode}", cat)

        if proc.returncode != 0:
            if "[" not in out and "{" not in out:
                raise FetchError(
                    f"opencli failed ({proc.returncode}): {out[-800:]}",
                    "hard",
                )
        return out, proc.returncode

    def run_json(self, args: Sequence[str], *, timeout: int | None = None) -> Any:
        out, rc = self.run(args, timeout=timeout)
        try:
            return extract_json(out)
        except ValueError as e:
            cat = classify_failure(out, rc) or "hard"
            raise FetchError(str(e), cat) from e

    def run_json_list(
        self, args: Sequence[str], *, timeout: int | None = None
    ) -> list[dict[str, Any]]:
        data = self.run_json(args, timeout=timeout)
        if isinstance(data, dict):
            for key in ("items", "data", "results", "rows", "list"):
                if isinstance(data.get(key), list):
                    data = data[key]
                    break
            else:
                raise FetchError(
                    f"unexpected JSON object keys: {list(data.keys())}", "hard"
                )
        if not isinstance(data, list):
            raise FetchError(f"expected list, got {type(data).__name__}", "hard")
        return [x for x in data if isinstance(x, dict)]
