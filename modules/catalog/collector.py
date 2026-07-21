"""通过 opencli 拉取 UP 投稿列表（限速 + 重试 + 续跑）。"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from loop_core.errors import FetchError
from loop_core.progress import load_partial, load_progress, save_partial, save_progress
from loop_core.rate_limit import Profile, backoff_seconds, sleep_with_jitter
from loop_core.runner import OpencliRunner
from loop_core.timeutil import utc_now_iso

from .models import normalize_video, parse_target

logger = logging.getLogger(__name__)


def log(msg: str) -> None:
    print(msg, flush=True)
    logger.info(msg)


class CatalogCollector:
    """UP 投稿列表采集器。"""

    def __init__(
        self,
        profile: Profile,
        *,
        order: str = "pubdate",
        runner: OpencliRunner | None = None,
    ) -> None:
        self.profile = profile
        self.order = order
        self.runner = runner or OpencliRunner(timeout_seconds=180)

    def resolve_uid(self, target: str) -> tuple[str, str]:
        info = parse_target(target)
        if "uid" in info:
            return info["uid"], info["uid"]

        query = info["query"]
        log(f"Resolving UP name via search: {query!r}")
        sleep_with_jitter(
            self.profile.search_delay,
            min(0.5, self.profile.search_delay * 0.2),
            "search",
            log=log,
        )
        try:
            data = self.runner.run_json_list(
                ["bilibili", "search", query, "--limit", "20", "-f", "json"],
                timeout=120,
            )
        except Exception as e:
            data = []
            log(f"  search failed: {e}")

        from .models import SPACE_URL_RE

        for item in data:
            name = str(
                item.get("author") or item.get("name") or item.get("title") or ""
            )
            mid = item.get("mid") or item.get("uid") or item.get("id")
            url = str(item.get("url") or item.get("space") or "")
            m = SPACE_URL_RE.search(url)
            if m and (query in name or name == query):
                return m.group(1), name or query
            if mid and str(mid).isdigit() and (query in name or name == query):
                return str(mid), name or query

        sleep_with_jitter(self.profile.search_delay, 0.3, "search-fallback", log=log)
        try:
            self.runner.run_json_list(
                ["bilibili", "user-videos", query, "--limit", "1", "-f", "json"],
                timeout=120,
            )
            return query, query
        except Exception as e:
            raise SystemExit(
                f"无法解析 UP 主「{query}」。请改用 UID 或空间链接。\n原始错误: {e}"
            ) from e

    def fetch_page(self, uid: str, page: int) -> tuple[list[dict], float]:
        t0 = time.monotonic()
        items = self.runner.run_json_list(
            [
                "bilibili",
                "user-videos",
                uid,
                "--limit",
                str(self.profile.page_size),
                "--page",
                str(page),
                "--order",
                self.order,
                "-f",
                "json",
            ],
            timeout=180,
        )
        return items, time.monotonic() - t0

    def fetch_page_with_retry(self, uid: str, page: int) -> tuple[list[dict], float]:
        attempt = 0
        while True:
            attempt += 1
            try:
                return self.fetch_page(uid, page)
            except FetchError as e:
                cat = e.category
                log(
                    f"  page {page} attempt {attempt}/{self.profile.max_retries}: "
                    f"{cat}: {str(e)[:200]}"
                )
                if cat == "sign352":
                    raise SystemExit(
                        "检测到可能的 -352 风控校验失败。请检查 opencli 会话；"
                        f"单靠 sleep 通常无效。\n详情: {e}"
                    ) from e
                if attempt >= self.profile.max_retries:
                    raise
                if cat == "risk412":
                    log(f"  hard risk cooldown {self.profile.cooldown_412:.0f}s ...")
                    time.sleep(self.profile.cooldown_412)
                elif cat in ("rate", "timeout", "empty_soft"):
                    wait = backoff_seconds(self.profile, attempt)
                    log(f"  backoff {wait:.1f}s ...")
                    time.sleep(wait)
                else:
                    time.sleep(min(30.0, self.profile.backoff_base * attempt))

    def fetch_all(
        self,
        uid: str,
        folder: Path,
        *,
        resume: bool = False,
        max_pages: int | None = None,
    ) -> list[dict]:
        profile = self.profile
        max_pages = max_pages if max_pages is not None else profile.max_pages
        all_items: list[dict] = []
        seen: set[str] = set()
        start_page = 1
        consecutive_empty = 0

        if resume:
            partial = load_partial(folder)
            prog = load_progress(folder)
            if partial:
                for v in partial:
                    nv = normalize_video(v)
                    key = nv.get("url") or nv.get("bvid") or ""
                    if key and key not in seen:
                        seen.add(key)
                        all_items.append(nv)
                start_page = int(prog.get("next_page") or prog.get("page") or 1)
                log(f"Resume: {len(all_items)} videos, continue from page {start_page}")
            else:
                log("Resume requested but no partial data; starting fresh")

        log(
            f"Fetch profile={profile.name} page_size={profile.page_size} "
            f"delay={profile.page_delay}±{profile.page_jitter}s max_pages={max_pages}"
        )

        page = start_page
        while page <= max_pages:
            log(f"  fetching page {page} ...")
            try:
                items, elapsed = self.fetch_page_with_retry(uid, page)
            except FetchError as e:
                save_partial(folder, all_items)
                save_progress(
                    folder,
                    {
                        "uid": uid,
                        "next_page": page,
                        "fetched_count": len(all_items),
                        "status": "failed",
                        "error": str(e)[:500],
                        "category": e.category,
                        "updated_at": utc_now_iso(),
                        "profile": profile.name,
                    },
                )
                raise SystemExit(
                    f"抓取失败并已保存进度（{len(all_items)} 条）。\n"
                    f"使用 --resume 从 page {page} 继续。\n"
                    f"错误[{e.category}]: {e}"
                ) from e

            slow_factor = 1.5 if elapsed > 30 else 1.0
            if elapsed > 30:
                log(f"  page {page} slow ({elapsed:.1f}s); next delay x1.5")

            if not items:
                consecutive_empty += 1
                log(f"  page {page}: empty ({consecutive_empty})")
                if consecutive_empty >= 2 and all_items:
                    log("  soft-limit suspicion; backoff and retry once")
                    time.sleep(min(profile.backoff_cap, profile.backoff_base * 4))
                    try:
                        items2, _ = self.fetch_page_with_retry(uid, page)
                    except FetchError:
                        items2 = []
                    if not items2:
                        log("  still empty, treat as end of list")
                        break
                    items = items2
                    consecutive_empty = 0
                elif not all_items and consecutive_empty >= 2:
                    break
                else:
                    if all_items:
                        break
                    page += 1
                    sleep_with_jitter(
                        profile.page_delay * slow_factor,
                        profile.page_jitter,
                        "page",
                        log=log,
                    )
                    continue
            else:
                consecutive_empty = 0

            new = 0
            for it in items:
                nv = normalize_video(it)
                key = nv.get("url") or nv.get("bvid") or ""
                if not key or key in seen:
                    continue
                seen.add(key)
                all_items.append(nv)
                new += 1

            log(f"  page {page}: +{new} (total {len(all_items)}) in {elapsed:.1f}s")
            save_partial(folder, all_items)
            save_progress(
                folder,
                {
                    "uid": uid,
                    "next_page": page + 1,
                    "page": page,
                    "fetched_count": len(all_items),
                    "status": "in_progress",
                    "updated_at": utc_now_iso(),
                    "profile": profile.name,
                    "last_bvid": all_items[-1].get("bvid") if all_items else "",
                },
            )

            if len(items) < profile.page_size:
                log("  last page (short page)")
                break

            page += 1
            sleep_with_jitter(
                profile.page_delay * slow_factor,
                profile.page_jitter,
                "page",
                log=log,
            )

        return all_items
