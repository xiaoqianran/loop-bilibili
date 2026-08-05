"""Creator upload source via opencli (injectable runner for tests)."""

from __future__ import annotations

import json
import subprocess
from typing import Any, Callable, Sequence

from loop_bilibili.models import Candidate, Video

from ._util import extract_bvid

RunnerFn = Callable[[list[str]], Any]


def extract_json(text: str) -> Any:
    """Extract first JSON array/object from opencli stdout (may include noise)."""
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


def default_opencli_runner(argv: list[str], *, timeout: float = 120.0) -> Any:
    cmd = ["opencli", *argv]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    if proc.returncode != 0:
        raise RuntimeError(
            f"opencli failed rc={proc.returncode}: {out[-500:]}"
        )
    return extract_json(proc.stdout or "")


def parse_creator_item(item: dict[str, Any], *, owner_mid: str = "") -> Candidate | None:
    if not isinstance(item, dict):
        return None
    bvid = extract_bvid(str(item.get("bvid") or item.get("url") or ""))
    if not bvid:
        return None
    owner = item.get("owner") if isinstance(item.get("owner"), dict) else {}
    mid = str(
        item.get("mid")
        or item.get("uid")
        or owner.get("mid")
        or owner_mid
        or ""
    )
    name = str(
        item.get("author")
        or item.get("name")
        or owner.get("name")
        or ""
    )
    title = str(item.get("title") or "")
    published = str(
        item.get("published_at")
        or item.get("pubdate")
        or item.get("created")
        or ""
    )
    return Candidate(
        video=Video(
            bvid=bvid,
            title=title,
            owner_mid=str(mid),
            owner_name=name,
            published_at=str(published),
        ),
        reason="creator",
        source="creator",
    )


class CreatorOpencliSource:
    """List recent videos for one creator mid via opencli user-videos."""

    name = "creator"

    def __init__(
        self,
        mid: str,
        *,
        limit: int = 30,
        page: int = 1,
        order: str = "pubdate",
        runner: RunnerFn | None = None,
        owner_name: str = "",
    ):
        self.mid = str(mid).strip()
        self.limit = max(1, int(limit))
        self.page = max(1, int(page))
        self.order = order
        self.runner = runner or default_opencli_runner
        self.owner_name = owner_name

    def fetch(self) -> Sequence[Candidate]:
        if not self.mid:
            return []
        data = self.runner(
            [
                "bilibili",
                "user-videos",
                self.mid,
                "--limit",
                str(self.limit),
                "--page",
                str(self.page),
                "--order",
                self.order,
                "-f",
                "json",
            ]
        )
        if isinstance(data, dict):
            items = data.get("list") or data.get("items") or data.get("vlist") or []
        elif isinstance(data, list):
            items = data
        else:
            items = []

        out: list[Candidate] = []
        seen: set[str] = set()
        for raw in items:
            if not isinstance(raw, dict):
                continue
            cand = parse_creator_item(raw, owner_mid=self.mid)
            if cand is None:
                continue
            if self.owner_name and not cand.video.owner_name:
                cand.video.owner_name = self.owner_name
            if not cand.video.owner_mid:
                cand.video.owner_mid = self.mid
            if cand.video.bvid in seen:
                continue
            seen.add(cand.video.bvid)
            out.append(cand)
        return out
