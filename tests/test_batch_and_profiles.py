"""Unit tests for shipped loop_core batch + rate-limit helpers (no network)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages"))

from loop_core.batch import (  # noqa: E402
    extract_bvid,
    load_bvids_from_catalog,
    load_done_set,
    pending_keys,
    save_done_set,
)
from loop_core.rate_limit import (  # noqa: E402
    ITEM_DEFAULT_PROFILE,
    LIST_DEFAULT_PROFILE,
    get_profile,
    item_is_stricter_than_list,
)


class TestBvidExtract(unittest.TestCase):
    def test_plain_bvid(self):
        self.assertEqual(extract_bvid("BV1BVEs6LENZ"), "BV1BVEs6LENZ")

    def test_url(self):
        self.assertEqual(
            extract_bvid("https://www.bilibili.com/video/BV1BVEs6LENZ"),
            "BV1BVEs6LENZ",
        )


class TestPendingResume(unittest.TestCase):
    def test_pending_skips_done_when_resume(self):
        all_keys = ["BV1", "BV2", "BV3"]
        done = {"BV1", "BV3"}
        self.assertEqual(pending_keys(all_keys, done, resume=True), ["BV2"])

    def test_pending_full_when_not_resume(self):
        all_keys = ["BV1", "BV2"]
        done = {"BV1"}
        self.assertEqual(pending_keys(all_keys, done, resume=False), ["BV1", "BV2"])

    def test_done_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            save_done_set(folder, {"BV2", "BV1"})
            loaded = load_done_set(folder)
            self.assertEqual(loaded, {"BV1", "BV2"})


class TestCatalogBvids(unittest.TestCase):
    def test_load_from_all_json(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            (folder / "all.json").write_text(
                json.dumps(
                    [
                        {"bvid": "BV1AAA", "title": "a"},
                        {"url": "https://www.bilibili.com/video/BV1BBB", "title": "b"},
                        {"bvid": "BV1AAA", "title": "dup"},
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            bvids = load_bvids_from_catalog(folder)
            self.assertEqual(bvids, ["BV1AAA", "BV1BBB"])


class TestProfilesAntiRisk(unittest.TestCase):
    def test_defaults_names(self):
        self.assertEqual(LIST_DEFAULT_PROFILE, "conservative")
        self.assertEqual(ITEM_DEFAULT_PROFILE, "conservative")

    def test_item_stricter_than_list_on_defaults(self):
        for name in ("conservative", "balanced", "aggressive"):
            p = get_profile(name)
            self.assertTrue(
                item_is_stricter_than_list(p),
                msg=f"{name}: item_delay={p.item_delay} page_delay={p.page_delay}",
            )
            self.assertGreater(p.item_delay, 1.0)
            self.assertGreaterEqual(p.page_delay, 0.3)

    def test_conservative_item_delay_multi_second(self):
        p = get_profile("conservative")
        self.assertGreaterEqual(p.item_delay, 5.0)
        self.assertGreaterEqual(p.page_delay, 1.0)
        self.assertGreater(p.item_delay, p.page_delay)


if __name__ == "__main__":
    unittest.main()
