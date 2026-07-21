"""限速 profile、抖动 sleep、退避参数。"""

from __future__ import annotations

import random
import time
from dataclasses import asdict, dataclass


@dataclass
class Profile:
    name: str
    page_size: int
    page_delay: float
    page_jitter: float
    search_delay: float
    max_retries: int
    backoff_base: float
    backoff_factor: float
    backoff_cap: float
    cooldown_412: float
    max_pages: int


PROFILES: dict[str, Profile] = {
    "conservative": Profile(
        name="conservative",
        page_size=30,
        page_delay=1.5,
        page_jitter=0.5,
        search_delay=4.0,
        max_retries=5,
        backoff_base=2.0,
        backoff_factor=2.0,
        backoff_cap=120.0,
        cooldown_412=300.0,
        max_pages=200,
    ),
    "balanced": Profile(
        name="balanced",
        page_size=50,
        page_delay=1.0,
        page_jitter=0.3,
        search_delay=3.0,
        max_retries=4,
        backoff_base=1.5,
        backoff_factor=2.0,
        backoff_cap=90.0,
        cooldown_412=180.0,
        max_pages=200,
    ),
    "aggressive": Profile(
        name="aggressive",
        page_size=50,
        page_delay=0.3,
        page_jitter=0.1,
        search_delay=1.5,
        max_retries=3,
        backoff_base=1.0,
        backoff_factor=2.0,
        backoff_cap=60.0,
        cooldown_412=120.0,
        max_pages=200,
    ),
}


def get_profile(name: str) -> Profile:
    if name not in PROFILES:
        raise KeyError(f"unknown profile: {name}; choose from {list(PROFILES)}")
    return Profile(**asdict(PROFILES[name]))


def sleep_with_jitter(
    base: float,
    jitter: float,
    label: str = "",
    log=print,
) -> float:
    delay = max(0.0, base + random.uniform(-jitter, jitter))
    if delay <= 0:
        return 0.0
    msg = f"  sleep {delay:.2f}s"
    if label:
        msg += f" ({label})"
    log(msg)
    time.sleep(delay)
    return delay


def backoff_seconds(profile: Profile, attempt: int) -> float:
    """attempt 从 1 起。"""
    wait = min(
        profile.backoff_cap,
        profile.backoff_base * (profile.backoff_factor ** max(0, attempt - 1)),
    )
    wait += random.uniform(0, 0.5)
    return wait
