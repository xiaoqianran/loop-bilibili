"""Offline tests for creator + homepage video sources."""

from __future__ import annotations

import unittest
from typing import Any

from loop_bilibili.sources.creator_opencli import (
    CreatorOpencliSource,
    extract_json,
    parse_creator_item,
)
from loop_bilibili.sources.homepage_rcmd import HomepageRcmdSource, parse_rcmd_item


class CreatorSourceTest(unittest.TestCase):
    def test_extract_json_with_noise(self) -> None:
        text = "warn: slow\n[{\"bvid\":\"BV1xx411c7mD\",\"title\":\"t\"}]\nok\n"
        data = extract_json(text)
        self.assertIsInstance(data, list)
        self.assertEqual(data[0]["bvid"], "BV1xx411c7mD")

    def test_parse_creator_item(self) -> None:
        cand = parse_creator_item(
            {
                "bvid": "BV1xx411c7mD",
                "title": "hello",
                "author": "up",
                "mid": 42,
                "pubdate": "2024-01-01",
            },
            owner_mid="42",
        )
        self.assertIsNotNone(cand)
        assert cand is not None
        self.assertEqual(cand.video.bvid, "BV1xx411c7mD")
        self.assertEqual(cand.source, "creator")
        self.assertEqual(cand.video.owner_mid, "42")

    def test_creator_fetch_via_fake_runner(self) -> None:
        payload = [
            {"bvid": "BV1aa411c7mA", "title": "a", "author": "u", "mid": "9"},
            {"bvid": "BV1bb411c7mB", "title": "b", "author": "u", "mid": "9"},
            {"title": "no-bvid"},
            {"bvid": "BV1aa411c7mA", "title": "dup"},
        ]

        def runner(argv: list[str]) -> Any:
            self.assertIn("user-videos", argv)
            self.assertIn("9", argv)
            return payload

        src = CreatorOpencliSource("9", runner=runner)
        cands = list(src.fetch())
        self.assertEqual([c.video.bvid for c in cands], ["BV1aa411c7mA", "BV1bb411c7mB"])
        self.assertEqual(cands[0].video.title, "a")
        self.assertEqual(cands[0].source, "creator")


class HomepageSourceTest(unittest.TestCase):
    def test_parse_rcmd_item_video_and_ad(self) -> None:
        ok = parse_rcmd_item(
            {
                "goto": "av",
                "bvid": "BV1cc411c7mC",
                "title": "rec",
                "owner": {"mid": 1, "name": "n"},
                "rcmd_reason": {"content": "because"},
            }
        )
        self.assertIsNotNone(ok)
        assert ok is not None
        self.assertEqual(ok.source, "homepage")
        self.assertEqual(ok.reason, "because")
        self.assertEqual(ok.video.owner_name, "n")

        ad = parse_rcmd_item({"goto": "ad", "title": "ad"})
        self.assertIsNone(ad)

    def test_homepage_fetch_fake_http(self) -> None:
        pages: list[dict[str, Any]] = [
            {
                "code": 0,
                "data": {
                    "item": [
                        {
                            "goto": "av",
                            "bvid": "BV1dd411c7mD",
                            "title": "p1",
                            "owner": {"mid": 2, "name": "a"},
                        },
                        {"goto": "live", "title": "live"},
                    ]
                },
            },
            {
                "code": 0,
                "data": {
                    "item": [
                        {
                            "goto": "av",
                            "bvid": "BV1ee411c7mE",
                            "title": "p2",
                            "owner": {"mid": 3, "name": "b"},
                            "rcmd_reason": "hot",
                        },
                        {
                            "goto": "av",
                            "bvid": "BV1dd411c7mD",
                            "title": "dup-across-pages",
                        },
                    ]
                },
            },
        ]
        idx = {"n": 0}

        def http(url: str, cookie: str = "", timeout: float = 30.0) -> Any:
            self.assertIn("feed/rcmd", url)
            i = idx["n"]
            idx["n"] += 1
            return pages[i]

        src = HomepageRcmdSource(
            pages=2,
            ps=10,
            cookie="SESSDATA=x",
            http=http,
            wbi_keys=lambda _c: ("img", "sub"),
        )
        cands = list(src.fetch())
        self.assertEqual(
            [c.video.bvid for c in cands],
            ["BV1dd411c7mD", "BV1ee411c7mE"],
        )
        self.assertEqual(cands[1].reason, "hot")


if __name__ == "__main__":
    unittest.main()
