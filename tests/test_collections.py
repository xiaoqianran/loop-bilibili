"""Offline tests for official seasons/series client + enrich."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages"))
sys.path.insert(0, str(ROOT / "modules"))
sys.path.insert(0, str(ROOT))

from catalog.collections import (  # noqa: E402
    CollectionItem,
    enrich_videos_with_collections,
    fetch_all_collections,
)


class TestCollections(unittest.TestCase):
    def test_enrich_prefers_season_over_series(self):
        colls = [
            CollectionItem(
                kind="series",
                id=2,
                name="系列A",
                archives=[{"bvid": "BV1", "title": "t"}],
            ),
            CollectionItem(
                kind="season",
                id=1,
                name="合集B",
                archives=[{"bvid": "BV1", "title": "t"}],
            ),
        ]
        # fix archives to full shape
        colls[0].archives = [
            {
                "bvid": "BV1",
                "title": "t",
                "series": "系列A",
                "series_source": "official",
            }
        ]
        colls[1].archives = [
            {
                "bvid": "BV1",
                "title": "t",
                "series": "合集B",
                "series_source": "official",
            }
        ]
        videos = [{"bvid": "BV1", "title": "t", "url": ""}]
        out = enrich_videos_with_collections(videos, colls)
        self.assertEqual(out[0]["series"], "合集B")
        self.assertEqual(out[0]["series_source"], "official")
        self.assertEqual(len(out[0]["collections"]), 2)

    def test_enrich_uncollected(self):
        out = enrich_videos_with_collections(
            [{"bvid": "BV9", "title": "x"}],
            [],
        )
        self.assertEqual(out[0]["series"], "未入合集")
        self.assertEqual(out[0]["series_source"], "none")

    def test_fetch_all_collections_mocked(self):
        def fake_http(url: str):
            if "seasons_series_list" in url:
                return {
                    "code": 0,
                    "data": {
                        "items_lists": {
                            "page": {"page_num": 1, "page_size": 20, "total": 1},
                            "seasons_list": [
                                {
                                    "meta": {
                                        "season_id": 10,
                                        "name": "合集·测试",
                                        "total": 2,
                                        "description": "",
                                        "cover": "",
                                    },
                                    "archives": [],
                                }
                            ],
                            "series_list": [],
                        }
                    },
                }
            if "seasons_archives_list" in url:
                return {
                    "code": 0,
                    "data": {
                        "page": {"page_num": 1, "page_size": 30, "total": 2},
                        "meta": {"total": 2, "name": "合集·测试"},
                        "archives": [
                            {
                                "bvid": "BV1AAA",
                                "title": "a",
                                "pubdate": 1700000000,
                                "stat": {"view": 1},
                            },
                            {
                                "bvid": "BV1BBB",
                                "title": "b",
                                "pubdate": 1700001000,
                                "stat": {"view": 2},
                            },
                        ],
                    },
                }
            raise AssertionError(url)

        items = fetch_all_collections("123", http=fake_http, delay=0)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].kind, "season")
        self.assertEqual(items[0].id, 10)
        self.assertEqual(len(items[0].archives), 2)
        self.assertEqual(items[0].archives[0]["bvid"], "BV1AAA")
        self.assertEqual(items[0].archives[0]["series_source"], "official")


if __name__ == "__main__":
    unittest.main()
