"""带分类退避的 opencli 调用。"""

from __future__ import annotations

import time
from typing import Any, Callable, Sequence

from .errors import FetchError
from .rate_limit import Profile, backoff_seconds
from .runner import OpencliRunner


def run_with_retry(
    runner: OpencliRunner,
    args: Sequence[str],
    profile: Profile,
    *,
    timeout: int | None = None,
    log: Callable[[str], None] = print,
) -> Any:
    """运行 opencli 并解析 JSON；对 rate/timeout/412 重试。"""
    attempt = 0
    while True:
        attempt += 1
        try:
            return runner.run_json(list(args), timeout=timeout)
        except FetchError as e:
            cat = e.category
            log(f"  attempt {attempt}/{profile.max_retries} [{cat}]: {str(e)[:180]}")
            if cat == "sign352":
                raise
            if attempt >= profile.max_retries:
                raise
            if cat == "risk412":
                log(f"  cooldown {profile.cooldown_412:.0f}s (412)")
                time.sleep(profile.cooldown_412)
            else:
                wait = backoff_seconds(profile, attempt)
                log(f"  backoff {wait:.1f}s")
                time.sleep(wait)
