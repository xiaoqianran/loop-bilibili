"""Offline tests for homepage rcmd (no network)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages"))
sys.path.insert(0, str(ROOT / "modules"))
sys.path.insert(0, str(ROOT))

from bili_subbatch.homepage import (  # noqa: E402
    fetch_homepage,
    parse_rcmd_item,
    write_homepage_export,
)


def _fake_rcmd_payload(bvids: list[str], *, with_ad: bool = True) -> dict:
    items = []
    if with_ad:
        items.append({"goto": "ad", "title": "ad-card"})
    for b in bvids:
        items.append(
            {
                "goto": "av",
                "bvid": b,
                "title": f"title-{b}",
                "owner": {"name": "up", "mid": 1},
                "stat": {"view": 10, "danmaku": 1},
                "uri": f"https://www.bilibili.com/video/{b}",
            }
        )
    return {"code": 0, "message": "0", "data": {"item": items}}


class TestHomepage(unittest.TestCase):
    def test_parse_av_and_skip_empty(self):
        card = parse_rcmd_item(
            {
                "goto": "av",
                "bvid": "BV1xx411c7m9",
                "title": "t",
                "owner": {"name": "a", "mid": 2},
            },
            fresh_idx=3,
        )
        assert card is not None
        self.assertEqual(card.bvid, "BV1xx411c7m9")
        self.assertEqual(card.fresh_idx, 3)
        self.assertEqual(card.author, "a")

    def test_fetch_homepage_injectable_and_dedupe(self):
        calls: list[int] = []

        def http(url: str, cookie: str = "", timeout: float = 30.0):
            # two pages with overlapping BV
            if "fresh_idx=1" in url or "fresh_idx%3D1" in url or "fresh_idx=1&" in url:
                # enc_wbi puts fresh_idx in query; match loosely
                pass
            # count by call order
            calls.append(1)
            if len(calls) == 1:
                return _fake_rcmd_payload(["BV1aaa", "BV1bbb"])
            return _fake_rcmd_payload(["BV1bbb", "BV1ccc"])

        def wbi_keys(cookie: str = "") -> tuple[str, str]:
            return "imgkeyxx", "subkeyyy"

        cards = fetch_homepage(
            limit=10,
            pages=2,
            page_size=12,
            page_delay=0,
            page_jitter=0,
            videos_only=True,
            cookie="SESSDATA=x",
            http=http,
            wbi_keys=wbi_keys,
        )
        bvids = [c.bvid for c in cards]
        self.assertEqual(bvids, ["BV1aaa", "BV1bbb", "BV1ccc"])
        self.assertEqual(len(calls), 2)

    def test_write_export(self):
        cards = fetch_homepage(
            limit=5,
            pages=1,
            page_delay=0,
            page_jitter=0,
            http=lambda *a, **k: _fake_rcmd_payload(["BV1x", "BV1y"]),
            wbi_keys=lambda c="": ("i", "s"),
            cookie="x",
        )
        with tempfile.TemporaryDirectory() as td:
            folder = write_homepage_export(cards, Path(td) / "homepage")
            self.assertTrue((folder / "homepage.json").is_file())
            self.assertTrue((folder / "bvids.txt").is_file())
            text = (folder / "bvids.txt").read_text(encoding="utf-8")
            self.assertIn("BV1x", text)
            rows = json.loads((folder / "homepage.json").read_text(encoding="utf-8"))
            self.assertEqual(len(rows), 2)


if __name__ == "__main__":
    unittest.main()
