"""限速 profile、抖动 sleep、退避参数。

Anti-risk defaults (B 站社区实践归纳，非官方 SLA):
  - list/discovery: serial pages, ~1.5s ± jitter (conservative)
  - per-item (summary/subtitle/comments): multi-second gaps, always serial
  - never concurrent opencli against one session by default
"""

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
    # per-video ops (summary / subtitle / comments) — must stay > page_delay
    item_delay: float
    item_jitter: float


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
        item_delay=5.0,
        item_jitter=1.5,
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
        item_delay=3.5,
        item_jitter=1.0,
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
        item_delay=1.2,
        item_jitter=0.3,
    ),
}

# Explicit aliases for documentation / selection
LIST_DEFAULT_PROFILE = "conservative"
ITEM_DEFAULT_PROFILE = "conservative"


def get_profile(name: str) -> Profile:
    if name not in PROFILES:
        raise KeyError(f"unknown profile: {name}; choose from {list(PROFILES)}")
    return Profile(**asdict(PROFILES[name]))


def item_is_stricter_than_list(profile: Profile) -> bool:
    """True when per-item delay is strictly greater than list page delay."""
    return float(profile.item_delay) > float(profile.page_delay)


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


def sleep_item(profile: Profile, label: str = "item", log=print) -> float:
    return sleep_with_jitter(profile.item_delay, profile.item_jitter, label, log=log)


def sleep_page(profile: Profile, label: str = "page", log=print) -> float:
    return sleep_with_jitter(profile.page_delay, profile.page_jitter, label, log=log)


def backoff_seconds(profile: Profile, attempt: int) -> float:
    """attempt 从 1 起。"""
    wait = min(
        profile.backoff_cap,
        profile.backoff_base * (profile.backoff_factor ** max(0, attempt - 1)),
    )
    wait += random.uniform(0, 0.5)
    return wait
