"""Subtitle fetch via Bilibili HTTP/WBI (ported SubBatch request chain)."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable

from loop_bilibili.database import dumps_cues
from loop_bilibili.models import SubtitlePayload, SubtitleStatus

from ._http import RetryableError, format_subtitle_url, get_cookie, http_json
from ._util import (
    cues_to_text,
    is_charge_exclusive_blocked,
    pick_track,
    resolve_cid,
    to_cues,
)
from ._wbi import enc_wbi, key_from_url

HttpJsonFn = Callable[..., Any]
GetWbiKeysFn = Callable[[str], tuple[str, str]]

_wbi: dict[str, Any] = {"img": None, "sub": None, "at": 0.0}
_WBI_TTL = 600.0

# Bilibili API codes often treated as temporary
_RETRYABLE_CODES = frozenset({-352, -412, -799, -509, 412, 429})


def clear_wbi_cache() -> None:
    _wbi.update(img=None, sub=None, at=0.0)


def get_wbi_keys(cookie: str = "", *, http: HttpJsonFn | None = None) -> tuple[str, str]:
    http = http or http_json
    now = time.time()
    if _wbi["img"] and now - _wbi["at"] < _WBI_TTL:
        return _wbi["img"], _wbi["sub"]
    nav = http("https://api.bilibili.com/x/web-interface/nav", cookie=cookie)
    wbi = (nav.get("data") or {}).get("wbi_img") or {}
    img_url = wbi.get("img_url") or ""
    sub_url = wbi.get("sub_url") or ""
    if not img_url or not sub_url:
        raise RuntimeError("failed to get wbi keys from /nav")
    img = key_from_url(img_url)
    sub = key_from_url(sub_url)
    _wbi.update(img=img, sub=sub, at=now)
    return img, sub


def _check_api_code(payload: dict[str, Any], *, where: str) -> None:
    code = payload.get("code")
    if code in (0, None):
        return
    msg = f"{where} code={code} {payload.get('message')}"
    if code in _RETRYABLE_CODES:
        raise RetryableError(msg)
    raise RuntimeError(msg)


@dataclass
class SubtitleFetchResult:
    """Internal fetch outcome before DB mapping."""

    bvid: str
    status: SubtitleStatus
    language: str = "zh"
    text: str = ""
    cues: list[dict[str, Any]] | None = None
    error: str = ""
    title: str = ""
    author: str = ""
    source: str = ""

    def to_payload(self, language: str | None = None) -> SubtitlePayload:
        lang = language or self.language or "zh"
        return SubtitlePayload(
            bvid=self.bvid,
            language=lang,
            status=self.status,
            text=self.text,
            cues_json=dumps_cues(self.cues or []),
            error=self.error,
        )


class BilibiliSubtitleSource:
    """Fetch subtitles for one bvid using injectable HTTP (tests use fakes)."""

    def __init__(
        self,
        cookie: str | None = None,
        *,
        http: HttpJsonFn | None = None,
        wbi_keys: GetWbiKeysFn | None = None,
        default_language: str = "zh",
    ):
        self.cookie = get_cookie(cookie)
        self.http = http or http_json
        self._wbi_keys = wbi_keys
        self.default_language = default_language

    def _keys(self, cookie: str) -> tuple[str, str]:
        if self._wbi_keys is not None:
            return self._wbi_keys(cookie)
        return get_wbi_keys(cookie, http=self.http)

    def fetch(self, bvid: str, *, page: int = 1) -> SubtitleFetchResult:
        bvid = (bvid or "").strip()
        if not bvid:
            return SubtitleFetchResult(
                bvid="", status="failed", error="empty bvid", language=self.default_language
            )
        try:
            return self._fetch_inner(bvid, page=page)
        except RetryableError as exc:
            return SubtitleFetchResult(
                bvid=bvid,
                status="retry",
                error=str(exc),
                language=self.default_language,
            )
        except Exception as exc:
            return SubtitleFetchResult(
                bvid=bvid,
                status="failed",
                error=str(exc),
                language=self.default_language,
            )

    def _fetch_inner(self, bvid: str, *, page: int) -> SubtitleFetchResult:
        cookie = self.cookie
        img, sub = self._keys(cookie)
        q = enc_wbi({"bvid": bvid, "need_elec": 0}, img, sub)
        detail = self.http(
            f"https://api.bilibili.com/x/web-interface/wbi/view/detail?{q}",
            cookie=cookie,
        )
        _check_api_code(detail, where="view/detail")

        view = (detail.get("data") or {}).get("View") or {}
        if is_charge_exclusive_blocked(view):
            return SubtitleFetchResult(
                bvid=bvid,
                status="empty",
                error="charge_exclusive_blocked",
                title=str(view.get("title") or ""),
                language=self.default_language,
            )

        aid_raw = view.get("aid") or 0
        try:
            aid = int(aid_raw) or None
        except (TypeError, ValueError):
            aid = None
        cid = resolve_cid(view, page)
        title = str(view.get("title") or "")
        author = str((view.get("owner") or {}).get("name") or "")
        if cid is None:
            return SubtitleFetchResult(
                bvid=bvid,
                status="failed",
                error="no cid",
                title=title,
                author=author,
                language=self.default_language,
            )

        subs, source = self._collect_tracks(aid=aid, cid=cid, bvid=bvid)
        if not subs:
            return SubtitleFetchResult(
                bvid=bvid,
                status="empty",
                title=title,
                author=author,
                language=self.default_language,
            )

        track = pick_track(subs)
        if track is None:
            return SubtitleFetchResult(
                bvid=bvid,
                status="empty",
                title=title,
                author=author,
                language=self.default_language,
            )

        lan = str(track.get("lan") or self.default_language)
        url, source = self._resolve_url(
            track, aid=aid, cid=cid, source=source
        )
        if not url:
            return SubtitleFetchResult(
                bvid=bvid,
                status="empty",
                language=lan or self.default_language,
                title=title,
                author=author,
            )

        body_json = self.http(url, cookie=cookie)
        body = body_json.get("body") if isinstance(body_json, dict) else None
        if not isinstance(body, list) or not body:
            return SubtitleFetchResult(
                bvid=bvid,
                status="empty",
                language=lan or self.default_language,
                title=title,
                author=author,
            )

        cues = to_cues(body)
        return SubtitleFetchResult(
            bvid=bvid,
            status="ok",
            language=lan or self.default_language,
            text=cues_to_text(cues),
            cues=cues,
            title=title,
            author=author,
            source=source,
        )

    def _collect_tracks(
        self, *, aid: int | None, cid: int, bvid: str
    ) -> tuple[list[dict], str]:
        cookie = self.cookie
        img, sub = self._keys(cookie)
        # 1) player/wbi/v2
        try:
            if aid:
                q = enc_wbi({"aid": aid, "cid": cid}, img, sub)
            else:
                q = enc_wbi({"bvid": bvid, "cid": cid}, img, sub)
            player = self.http(
                f"https://api.bilibili.com/x/player/wbi/v2?{q}", cookie=cookie
            )
            if player.get("code") == 0:
                subs = list(
                    ((player.get("data") or {}).get("subtitle") or {}).get("subtitles")
                    or []
                )
                if subs:
                    return subs, "player_wbi"
        except RetryableError:
            raise
        except Exception:
            pass

        # 2) dm/view
        try:
            dm = self.http(
                f"https://api.bilibili.com/x/v2/dm/view?oid={cid}&type=1&bvid={bvid}",
                cookie=cookie,
            )
            if dm.get("code") != 0:
                return [], ""
            return (
                list(
                    ((dm.get("data") or {}).get("subtitle") or {}).get("subtitles") or []
                ),
                "dm_view",
            )
        except RetryableError:
            raise
        except Exception:
            return [], ""

    def _resolve_url(
        self,
        track: dict[str, Any],
        *,
        aid: int | None,
        cid: int,
        source: str,
    ) -> tuple[str, str]:
        lan = str(track.get("lan") or "")
        url = format_subtitle_url(track.get("subtitle_url") or "")
        if not url and lan.startswith("ai-") and aid:
            try:
                data = self.http(
                    f"https://api.bilibili.com/x/player/v2/ai/subtitle/search/stat"
                    f"?aid={aid}&cid={cid}",
                    cookie=self.cookie,
                )
                if data.get("code") == 0 and (data.get("data") or {}).get("subtitle_url"):
                    return format_subtitle_url(data["data"]["subtitle_url"]), "ai_stat"
            except RetryableError:
                raise
            except Exception:
                pass
        return url, source
