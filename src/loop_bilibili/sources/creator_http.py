"""Creator upload list via Bilibili space arc/search (WBI HTTP, no opencli)."""

from __future__ import annotations

import time
from typing import Any, Callable, Sequence

from loop_bilibili.models import Candidate, Video

from ._http import get_cookie, http_json
from ._util import extract_bvid
from ._wbi import enc_wbi
from .subtitle_bilibili import GetWbiKeysFn, get_wbi_keys

HttpJsonFn = Callable[..., Any]

ARC_SEARCH = "https://api.bilibili.com/x/space/wbi/arc/search"


def parse_arc_item(item: dict[str, Any], *, owner_mid: str, owner_name: str) -> Candidate | None:
    if not isinstance(item, dict):
        return None
    bvid = extract_bvid(str(item.get("bvid") or item.get("short_link_v2") or ""))
    if not bvid:
        return None
    pub = item.get("created") or item.get("pubdate") or ""
    if isinstance(pub, (int, float)) and pub > 0:
        # unix seconds → ISO-ish local-agnostic string
        pub = time.strftime("%Y-%m-%d", time.gmtime(int(pub)))
    else:
        pub = str(pub or "")
    author = str(item.get("author") or owner_name or "")
    mid = str(item.get("mid") or owner_mid or "")
    return Candidate(
        video=Video(
            bvid=bvid,
            title=str(item.get("title") or ""),
            owner_mid=mid,
            owner_name=author,
            published_at=pub,
        ),
        reason="creator",
        source="creator",
    )


class CreatorHttpSource:
    """
    Paginate mid's uploads via space WBI arc/search.

    Suitable for CI / servers without opencli browser.
    """

    name = "creator"

    def __init__(
        self,
        mid: str,
        *,
        owner_name: str = "",
        page_size: int = 30,
        max_pages: int = 50,
        cookie: str | None = None,
        http: HttpJsonFn | None = None,
        wbi_keys: GetWbiKeysFn | None = None,
        page_delay: float = 0.4,
    ):
        self.mid = str(mid).strip()
        self.owner_name = owner_name
        self.page_size = max(1, min(int(page_size), 50))
        self.max_pages = max(1, int(max_pages))
        self.cookie = get_cookie(cookie)
        self.http = http or http_json
        self._wbi_keys = wbi_keys
        self.page_delay = max(0.0, float(page_delay))

    def _keys(self) -> tuple[str, str]:
        if self._wbi_keys is not None:
            return self._wbi_keys(self.cookie)
        return get_wbi_keys(self.cookie, http=self.http)

    def fetch(self) -> Sequence[Candidate]:
        if not self.mid:
            return []
        out: list[Candidate] = []
        seen: set[str] = set()
        img, sub = self._keys()
        for pn in range(1, self.max_pages + 1):
            params = {
                "mid": self.mid,
                "ps": self.page_size,
                "pn": pn,
                "order": "pubdate",
            }
            q = enc_wbi(params, img, sub)
            payload = self.http(f"{ARC_SEARCH}?{q}", cookie=self.cookie)
            if not isinstance(payload, dict):
                break
            code = payload.get("code")
            if code not in (0, None):
                raise RuntimeError(
                    f"space arc/search code={code} {payload.get('message')}"
                )
            data = payload.get("data") or {}
            lst = data.get("list") or {}
            vlist = lst.get("vlist") if isinstance(lst, dict) else None
            if not isinstance(vlist, list) or not vlist:
                break
            for raw in vlist:
                cand = parse_arc_item(
                    raw, owner_mid=self.mid, owner_name=self.owner_name
                )
                if cand is None or cand.video.bvid in seen:
                    continue
                if self.owner_name and not cand.video.owner_name:
                    cand.video.owner_name = self.owner_name
                if not cand.video.owner_mid:
                    cand.video.owner_mid = self.mid
                seen.add(cand.video.bvid)
                out.append(cand)
            # last page if fewer than ps
            if len(vlist) < self.page_size:
                break
            page = data.get("page") or {}
            count = int(page.get("count") or 0) if isinstance(page, dict) else 0
            if count and len(out) >= count:
                break
            if self.page_delay > 0 and pn < self.max_pages:
                time.sleep(self.page_delay)
        return out
