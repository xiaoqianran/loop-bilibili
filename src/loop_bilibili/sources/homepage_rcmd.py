"""Homepage personalized rcmd source (Web feed/rcmd + WBI, no opencli)."""

from __future__ import annotations

from typing import Any, Callable, Sequence

from loop_bilibili.models import Candidate, Video

from ._http import get_cookie, http_json
from ._util import extract_bvid
from ._wbi import enc_wbi
from .subtitle_bilibili import GetWbiKeysFn, get_wbi_keys

HttpJsonFn = Callable[..., Any]

RCMD_URL = "https://api.bilibili.com/x/web-interface/wbi/index/top/feed/rcmd"
DEFAULT_GOTO_ALLOW = frozenset({"av"})


def parse_rcmd_item(item: dict[str, Any]) -> Candidate | None:
    """Normalize one rcmd card into Candidate; drop ads/live without bvid."""
    if not isinstance(item, dict):
        return None
    goto = str(item.get("goto") or "")
    if goto and goto not in DEFAULT_GOTO_ALLOW and not item.get("bvid"):
        return None
    bvid = extract_bvid(str(item.get("bvid") or ""))
    if not bvid:
        args = item.get("args") if isinstance(item.get("args"), dict) else {}
        bvid = extract_bvid(str(args.get("bvid") or ""))
    if not bvid:
        return None
    owner = item.get("owner") if isinstance(item.get("owner"), dict) else {}
    author = str(owner.get("name") or item.get("name") or "")
    mid = str(owner.get("mid") or item.get("mid") or "")
    reason_raw = item.get("rcmd_reason")
    if isinstance(reason_raw, dict):
        reason = str(reason_raw.get("content") or reason_raw.get("reason_type") or "")
    else:
        reason = str(reason_raw or "homepage")
    return Candidate(
        video=Video(
            bvid=bvid,
            title=str(item.get("title") or ""),
            owner_mid=mid,
            owner_name=author,
            published_at=str(item.get("pubdate") or item.get("ctime") or ""),
        ),
        reason=reason or "homepage",
        source="homepage",
    )


class HomepageRcmdSource:
    """Fetch personalized homepage feed pages."""

    name = "homepage"

    def __init__(
        self,
        *,
        pages: int = 1,
        ps: int = 12,
        cookie: str | None = None,
        http: HttpJsonFn | None = None,
        wbi_keys: GetWbiKeysFn | None = None,
        fresh_type: int = 4,
    ):
        self.pages = max(1, int(pages))
        self.ps = max(1, min(int(ps), 30))
        self.cookie = get_cookie(cookie)
        self.http = http or http_json
        self._wbi_keys = wbi_keys
        self.fresh_type = fresh_type

    def _keys(self) -> tuple[str, str]:
        if self._wbi_keys is not None:
            return self._wbi_keys(self.cookie)
        return get_wbi_keys(self.cookie, http=self.http)

    def fetch(self) -> Sequence[Candidate]:
        out: list[Candidate] = []
        seen: set[str] = set()
        img, sub = self._keys()
        for page_i in range(self.pages):
            fresh_idx = page_i + 1
            brush = page_i
            params = {
                "fresh_type": self.fresh_type,
                "fresh_idx": fresh_idx,
                "brush": brush,
                "ps": self.ps,
            }
            q = enc_wbi(params, img, sub)
            payload = self.http(f"{RCMD_URL}?{q}", cookie=self.cookie)
            if not isinstance(payload, dict):
                continue
            code = payload.get("code")
            if code not in (0, None):
                raise RuntimeError(
                    f"homepage rcmd code={code} {payload.get('message')}"
                )
            items = (payload.get("data") or {}).get("item") or []
            if not isinstance(items, list):
                continue
            for raw in items:
                cand = parse_rcmd_item(raw) if isinstance(raw, dict) else None
                if cand is None:
                    continue
                if cand.video.bvid in seen:
                    continue
                seen.add(cand.video.bvid)
                out.append(cand)
        return out
