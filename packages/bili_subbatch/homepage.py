"""B 站 Web 推荐首页（个性化 feed/rcmd）— 纯 HTTP + WBI，不依赖 opencli。

接口: GET /x/web-interface/wbi/index/top/feed/rcmd
「刷新」: 递增 fresh_idx / brush 再请求一轮。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from .client import GetWbiKeysFn, get_wbi_keys
from .http import get_cookie, http_json
from .wbi import enc_wbi

logger = logging.getLogger(__name__)

HttpJsonFn = Callable[..., Any]

RCMD_PATH = "https://api.bilibili.com/x/web-interface/wbi/index/top/feed/rcmd"

# 仅保留可进字幕管线的视频卡；广告 / 直播等默认丢掉
DEFAULT_GOTO_ALLOW = frozenset({"av"})


@dataclass
class HomepageCard:
    """归一化后的首页卡片。"""

    bvid: str = ""
    title: str = ""
    author: str = ""
    mid: int | str = ""
    goto: str = ""
    uri: str = ""
    cover: str = ""
    play: int | None = None
    danmaku: int | None = None
    duration: int | None = None
    rcmd_reason: str = ""
    fresh_idx: int = 0
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_row(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("raw", None)
        if self.bvid:
            d["url"] = f"https://www.bilibili.com/video/{self.bvid}"
        return d


def _coerce_int(v: Any) -> int | None:
    if v is None or isinstance(v, dict):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _stat_num(stat: Any, *keys: str) -> int | None:
    if not isinstance(stat, dict):
        return None
    for k in keys:
        n = _coerce_int(stat.get(k))
        if n is not None:
            return n
    return None


def parse_rcmd_item(item: dict[str, Any], *, fresh_idx: int = 0) -> HomepageCard | None:
    """把 rcmd 原始 item 归一成 HomepageCard；无法识别则返回 None。"""
    if not isinstance(item, dict):
        return None
    goto = str(item.get("goto") or "")
    bvid = str(item.get("bvid") or "").strip()
    if not bvid:
        args = item.get("args") if isinstance(item.get("args"), dict) else {}
        bvid = str(args.get("bvid") or "").strip()
    owner = item.get("owner") if isinstance(item.get("owner"), dict) else {}
    author = str(owner.get("name") or item.get("name") or "")
    mid = owner.get("mid") or item.get("id") or ""
    stat = item.get("stat") if isinstance(item.get("stat"), dict) else {}
    reason = item.get("rcmd_reason")
    if isinstance(reason, dict):
        rcmd_reason = str(reason.get("content") or reason.get("reason_type") or "")
    else:
        rcmd_reason = str(reason or "")
    uri = str(item.get("uri") or item.get("url") or "")
    if bvid and not uri:
        uri = f"https://www.bilibili.com/video/{bvid}"
    return HomepageCard(
        bvid=bvid,
        title=str(item.get("title") or ""),
        author=author,
        mid=mid if mid is not None else "",
        goto=goto,
        uri=uri,
        cover=str(item.get("pic") or item.get("cover") or ""),
        play=_stat_num(stat, "view", "play"),
        danmaku=_stat_num(stat, "danmaku"),
        duration=_coerce_int(item.get("duration")),
        rcmd_reason=rcmd_reason,
        fresh_idx=fresh_idx,
        raw=item,
    )


def fetch_rcmd_page(
    *,
    ps: int = 12,
    fresh_idx: int = 1,
    fresh_type: int = 4,
    brush: int | None = None,
    cookie: str = "",
    http: HttpJsonFn | None = None,
    wbi_keys: GetWbiKeysFn | None = None,
    extra_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """请求一页推荐首页；返回 API 原始 JSON。"""
    http = http or http_json
    keys = wbi_keys or get_wbi_keys
    cookie = cookie or get_cookie()
    img, sub = keys(cookie)
    brush_v = int(brush) if brush is not None else int(fresh_idx)
    params: dict[str, Any] = {
        "fresh_type": int(fresh_type),
        "fresh_idx": int(fresh_idx),
        "fresh_idx_1h": int(fresh_idx),
        "feed_version": "V8",
        "homepage_ver": 1,
        "ps": max(1, min(int(ps), 30)),
        "brush": brush_v,
        "web_location": 1430650,
        "y_num": 4,
    }
    if extra_params:
        params.update(extra_params)
    qs = enc_wbi(params, img, sub)
    url = f"{RCMD_PATH}?{qs}"
    data = http(url, cookie=cookie)
    if not isinstance(data, dict):
        raise RuntimeError(f"rcmd: unexpected response type {type(data)}")
    code = data.get("code")
    if code not in (0, "0", None):
        raise RuntimeError(
            f"rcmd API code={code} message={data.get('message')!r}"
        )
    return data


def fetch_homepage(
    *,
    limit: int = 20,
    pages: int = 1,
    page_size: int = 12,
    fresh_idx_start: int = 1,
    page_delay: float = 0.8,
    page_jitter: float = 0.2,
    videos_only: bool = True,
    goto_allow: Sequence[str] | None = None,
    cookie: str | None = None,
    http: HttpJsonFn | None = None,
    wbi_keys: GetWbiKeysFn | None = None,
) -> list[HomepageCard]:
    """
    拉取多页推荐首页（每页递增 fresh_idx，实现「刷新」）。

    - videos_only=True：只保留 goto=av 且带 bvid 的卡片
    - 跨页按 bvid 去重，保持首次出现顺序
    """
    import random

    cookie_s = get_cookie(cookie)
    allow = frozenset(goto_allow) if goto_allow is not None else DEFAULT_GOTO_ALLOW
    pages = max(1, int(pages))
    limit = max(0, int(limit))
    out: list[HomepageCard] = []
    seen: set[str] = set()

    for i in range(pages):
        fresh = int(fresh_idx_start) + i
        logger.info("homepage rcmd page=%s fresh_idx=%s ps=%s", i + 1, fresh, page_size)
        raw = fetch_rcmd_page(
            ps=page_size,
            fresh_idx=fresh,
            brush=fresh,
            cookie=cookie_s,
            http=http,
            wbi_keys=wbi_keys,
        )
        items = (raw.get("data") or {}).get("item") or []
        if not isinstance(items, list):
            items = []
        for it in items:
            if not isinstance(it, dict):
                continue
            card = parse_rcmd_item(it, fresh_idx=fresh)
            if card is None:
                continue
            if videos_only:
                if allow and card.goto and card.goto not in allow:
                    continue
                if not card.bvid:
                    continue
            key = card.bvid or f"{card.goto}:{card.uri}:{card.title}"
            if key in seen:
                continue
            seen.add(key)
            out.append(card)
            if limit and len(out) >= limit:
                return out
        if i + 1 < pages and page_delay > 0:
            time.sleep(page_delay + random.uniform(0, max(0.0, page_jitter)))
    if limit:
        return out[:limit]
    return out


def write_homepage_export(
    cards: Sequence[HomepageCard],
    out_dir: Path,
    *,
    meta: dict[str, Any] | None = None,
) -> Path:
    """写出 homepage.json / homepage.md / bvids.txt / meta.json。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = [c.to_row() for c in cards]
    bvids = [c.bvid for c in cards if c.bvid]

    json_path = out_dir / "homepage.json"
    md_path = out_dir / "homepage.md"
    bvids_path = out_dir / "bvids.txt"
    meta_path = out_dir / "meta.json"

    json_path.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    bvids_path.write_text("\n".join(bvids) + ("\n" if bvids else ""), encoding="utf-8")

    meta_out = {
        "kind": "homepage",
        "backend": "bili_subbatch/rcmd",
        "opencli": False,
        "api": RCMD_PATH,
        "count": len(rows),
        "bvid_count": len(bvids),
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if meta:
        meta_out.update(meta)
    meta_path.write_text(
        json.dumps(meta_out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    lines = [
        "# homepage (Web 推荐首页 rcmd)",
        "",
        f"- count: **{len(rows)}** (bvid={len(bvids)})",
        f"- backend: `bili_subbatch`（非 opencli）",
        f"- api: `{RCMD_PATH}`",
        f"- exported_at: {meta_out.get('exported_at')}",
        "",
        "| # | bvid | author | title | fresh_idx |",
        "|---|------|--------|-------|-----------|",
    ]
    for i, r in enumerate(rows, 1):
        title = str(r.get("title") or "").replace("|", "\\|")[:60]
        author = str(r.get("author") or "-").replace("|", "\\|")[:24]
        bvid = str(r.get("bvid") or "")
        fi = r.get("fresh_idx", "")
        lines.append(f"| {i} | {bvid} | {author} | {title} | {fi} |")
    lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Wrote %s (%s cards, %s bvids)", json_path, len(rows), len(bvids))
    return out_dir
