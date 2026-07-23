"""Bilibili client — SubBatch-compatible request graph."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .http import format_subtitle_url, get_cookie, http_json
from .util import is_charge_exclusive_blocked, pick_track, resolve_cid, to_cues
from .wbi import enc_wbi, key_from_url

_wbi: dict[str, Any] = {"img": None, "sub": None, "at": 0.0}
_WBI_TTL = 600.0

# Optional injectables (tests). Production leaves these as module defaults.
HttpJsonFn = Callable[..., Any]
GetWbiKeysFn = Callable[[str], tuple[str, str]]


@dataclass
class SubtitleResult:
    bvid: str
    status: str  # ok | empty | error
    cue_count: int = 0
    lan: str = ""
    aid: int | None = None
    cid: int | None = None
    title: str = ""
    author: str = ""
    data: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    source: str = ""  # player_wbi | dm_view | ai_stat

    def to_row(self, *, elapsed_s: float | None = None) -> dict[str, Any]:
        row: dict[str, Any] = {
            "bvid": self.bvid,
            "status": self.status,
            "cue_count": self.cue_count,
            "lan": self.lan,
            "aid": self.aid,
            "cid": self.cid,
            "title": self.title,
            "author": self.author,
            "source": self.source,
            "data": self.data,
            "plugin": "bili_subbatch",
        }
        if elapsed_s is not None:
            row["elapsed_s"] = round(elapsed_s, 3)
        if self.status == "empty":
            row["reason"] = self.error or "no_subtitle"
        if self.error and self.status == "error":
            row["error"] = self.error
        return row


def clear_wbi_cache() -> None:
    """Test helper / force key refresh."""
    _wbi.update(img=None, sub=None, at=0.0)


def get_wbi_keys(cookie: str = "") -> tuple[str, str]:
    now = time.time()
    if _wbi["img"] and now - _wbi["at"] < _WBI_TTL:
        return _wbi["img"], _wbi["sub"]
    nav = http_json("https://api.bilibili.com/x/web-interface/nav", cookie=cookie)
    wbi = (nav.get("data") or {}).get("wbi_img") or {}
    img_url = wbi.get("img_url") or ""
    sub_url = wbi.get("sub_url") or ""
    if not img_url or not sub_url:
        raise RuntimeError("failed to get wbi keys from /nav")
    img = key_from_url(img_url)
    sub = key_from_url(sub_url)
    _wbi.update(img=img, sub=sub, at=now)
    return img, sub


def view_detail(
    bvid: str,
    cookie: str = "",
    *,
    http: HttpJsonFn | None = None,
    wbi_keys: GetWbiKeysFn | None = None,
) -> dict[str, Any]:
    http = http or http_json
    keys = wbi_keys or get_wbi_keys
    img, sub = keys(cookie)
    q = enc_wbi({"bvid": bvid, "need_elec": 0}, img, sub)
    return http(
        f"https://api.bilibili.com/x/web-interface/wbi/view/detail?{q}",
        cookie=cookie,
    )


def player_wbi_v2(
    aid: int | None,
    cid: int,
    bvid: str,
    cookie: str = "",
    *,
    http: HttpJsonFn | None = None,
    wbi_keys: GetWbiKeysFn | None = None,
) -> dict[str, Any]:
    http = http or http_json
    keys = wbi_keys or get_wbi_keys
    img, sub = keys(cookie)
    if aid:
        q = enc_wbi({"aid": aid, "cid": cid}, img, sub)
    else:
        q = enc_wbi({"bvid": bvid, "cid": cid}, img, sub)
    return http(f"https://api.bilibili.com/x/player/wbi/v2?{q}", cookie=cookie)


def dm_view_subs(
    cid: int,
    bvid: str,
    cookie: str = "",
    *,
    http: HttpJsonFn | None = None,
) -> list[dict]:
    http = http or http_json
    dm = http(
        f"https://api.bilibili.com/x/v2/dm/view?oid={cid}&type=1&bvid={bvid}",
        cookie=cookie,
    )
    if dm.get("code") != 0:
        return []
    return list(((dm.get("data") or {}).get("subtitle") or {}).get("subtitles") or [])


def ai_subtitle_stat(
    aid: int,
    cid: int,
    cookie: str = "",
    *,
    http: HttpJsonFn | None = None,
) -> str:
    http = http or http_json
    data = http(
        f"https://api.bilibili.com/x/player/v2/ai/subtitle/search/stat?aid={aid}&cid={cid}",
        cookie=cookie,
    )
    if data.get("code") == 0 and (data.get("data") or {}).get("subtitle_url"):
        return format_subtitle_url(data["data"]["subtitle_url"])
    return ""


def _meta_from_view(view: dict[str, Any], page: int) -> tuple[int | None, int | None, str, str]:
    aid_raw = view.get("aid") or 0
    try:
        aid = int(aid_raw) or None
    except (TypeError, ValueError):
        aid = None
    cid = resolve_cid(view, page)
    title = str(view.get("title") or "")
    author = str((view.get("owner") or {}).get("name") or "")
    return aid, cid, title, author


def _collect_tracks(
    *,
    aid: int | None,
    cid: int,
    bvid: str,
    cookie: str,
    http: HttpJsonFn | None = None,
    wbi_keys: GetWbiKeysFn | None = None,
) -> tuple[list[dict], str]:
    """Return (subtitles, source) using SubBatch order + guest fallback."""
    # 1) player/wbi/v2
    try:
        player = player_wbi_v2(
            aid, cid, bvid, cookie, http=http, wbi_keys=wbi_keys
        )
        if player.get("code") == 0:
            subs = list(
                ((player.get("data") or {}).get("subtitle") or {}).get("subtitles")
                or []
            )
            if subs:
                return subs, "player_wbi"
    except Exception:
        pass

    # 2) dm/view
    try:
        subs = dm_view_subs(cid, bvid, cookie, http=http)
        if subs:
            return subs, "dm_view"
    except Exception:
        pass

    return [], ""


def _resolve_url(
    track: dict[str, Any],
    *,
    aid: int | None,
    cid: int,
    cookie: str,
    source: str,
    http: HttpJsonFn | None = None,
) -> tuple[str, str]:
    """Return (url, source) possibly upgrading via ai_stat."""
    lan = str(track.get("lan") or "")
    url = format_subtitle_url(track.get("subtitle_url") or "")
    if not url and lan.startswith("ai-") and aid:
        try:
            url = ai_subtitle_stat(aid, cid, cookie, http=http)
            if url:
                return url, "ai_stat"
        except Exception:
            pass
    return url, source


def fetch_subtitle(
    bvid: str,
    *,
    cookie: str | None = None,
    page: int = 1,
    http: HttpJsonFn | None = None,
    wbi_keys: GetWbiKeysFn | None = None,
) -> SubtitleResult:
    """
    Full SubBatch-compatible extract for one bvid.

    `http` / `wbi_keys` are injectable for offline unit tests.
    """
    bvid = (bvid or "").strip()
    if not bvid:
        return SubtitleResult(bvid="", status="error", error="empty bvid")
    cookie = get_cookie(cookie)
    http = http or http_json
    keys = wbi_keys or get_wbi_keys

    try:
        detail = view_detail(bvid, cookie, http=http, wbi_keys=keys)
    except Exception as e:
        return SubtitleResult(bvid=bvid, status="error", error=f"view/detail: {e}")

    if detail.get("code") != 0:
        return SubtitleResult(
            bvid=bvid,
            status="error",
            error=f"view/detail code={detail.get('code')} {detail.get('message')}",
        )

    view = (detail.get("data") or {}).get("View") or {}
    if is_charge_exclusive_blocked(view):
        return SubtitleResult(
            bvid=bvid, status="empty", error="charge_exclusive_blocked"
        )

    aid, cid, title, author = _meta_from_view(view, page)
    if cid is None:
        return SubtitleResult(bvid=bvid, status="error", error="no cid", title=title)

    base = dict(bvid=bvid, aid=aid, cid=cid, title=title, author=author)

    subs, source = _collect_tracks(
        aid=aid, cid=cid, bvid=bvid, cookie=cookie, http=http, wbi_keys=keys
    )
    if not subs:
        return SubtitleResult(status="empty", **base)

    track = pick_track(subs)
    if track is None:
        return SubtitleResult(status="empty", **base)

    lan = str(track.get("lan") or "")
    url, source = _resolve_url(
        track, aid=aid, cid=cid, cookie=cookie, source=source, http=http
    )
    if not url:
        return SubtitleResult(status="empty", lan=lan, **base)

    try:
        body_json = http(url, cookie=cookie)
    except Exception as e:
        return SubtitleResult(
            status="error",
            lan=lan,
            error=f"subtitle body: {e}",
            **base,
        )

    body = body_json.get("body") if isinstance(body_json, dict) else None
    if not isinstance(body, list) or not body:
        return SubtitleResult(status="empty", lan=lan, **base)

    cues = to_cues(body)
    return SubtitleResult(
        status="ok",
        cue_count=len(cues),
        lan=lan,
        data=cues,
        source=source,
        **base,
    )
