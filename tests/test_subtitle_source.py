"""Offline tests for BilibiliSubtitleSource (fake HTTP)."""

from __future__ import annotations

import unittest
from typing import Any
from urllib.parse import parse_qs, urlparse

from loop_bilibili.sources._http import RetryableError
from loop_bilibili.sources._wbi import enc_wbi, mixin_key
from loop_bilibili.sources.subtitle_bilibili import BilibiliSubtitleSource, clear_wbi_cache


class FakeHttp:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.view: dict[str, Any] = {
            "code": 0,
            "data": {
                "View": {
                    "aid": 100,
                    "cid": 200,
                    "title": "demo",
                    "owner": {"name": "up"},
                    "pages": [{"cid": 200}],
                }
            },
        }
        self.player: dict[str, Any] = {
            "code": 0,
            "data": {
                "subtitle": {
                    "subtitles": [
                        {
                            "lan": "zh-CN",
                            "subtitle_url": "//subtitle.example/a.json",
                        }
                    ]
                }
            },
        }
        self.body: dict[str, Any] = {
            "body": [
                {"from": 0.0, "to": 1.0, "content": "你好", "sid": 1},
                {"from": 1.0, "to": 2.0, "content": "世界", "sid": 2},
            ]
        }
        self.raise_on: dict[str, Exception] = {}

    def __call__(self, url: str, cookie: str = "", timeout: float = 30.0) -> Any:
        self.calls.append(url)
        for key, exc in self.raise_on.items():
            if key in url:
                raise exc
        if "view/detail" in url:
            return self.view
        if "player/wbi/v2" in url:
            return self.player
        if "dm/view" in url:
            return {"code": 0, "data": {"subtitle": {"subtitles": []}}}
        if "subtitle.example" in url:
            return self.body
        if "nav" in url:
            return {
                "data": {
                    "wbi_img": {
                        "img_url": "https://i0.hdslb.com/bfs/wbi/imgkey.png",
                        "sub_url": "https://i0.hdslb.com/bfs/wbi/subkey.png",
                    }
                }
            }
        raise AssertionError(f"unexpected url {url}")


class SubtitleSourceTest(unittest.TestCase):
    def setUp(self) -> None:
        clear_wbi_cache()
        self.http = FakeHttp()
        self.src = BilibiliSubtitleSource(
            cookie="SESSDATA=x",
            http=self.http,
            wbi_keys=lambda _c: ("imgkey", "subkey"),
        )

    def test_wbi_enc_deterministic(self) -> None:
        q = enc_wbi({"bvid": "BV1", "need_elec": 0}, "imgkey", "subkey", wts=1700000000)
        parsed = parse_qs(q)
        self.assertIn("w_rid", parsed)
        self.assertEqual(parsed["wts"], ["1700000000"])
        # mixin key is stable
        self.assertEqual(len(mixin_key("imgkey", "subkey")), 32)

    def test_fetch_ok(self) -> None:
        result = self.src.fetch("BV1okokokok")
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.bvid, "BV1okokokok")
        self.assertIn("你好", result.text)
        self.assertIn("世界", result.text)
        self.assertEqual(len(result.cues or []), 2)
        payload = result.to_payload()
        self.assertEqual(payload.status, "ok")
        self.assertIn("你好", payload.cues_json)
        # signed view request used
        self.assertTrue(any("view/detail" in u for u in self.http.calls))

    def test_fetch_empty_no_tracks(self) -> None:
        self.http.player = {"code": 0, "data": {"subtitle": {"subtitles": []}}}
        result = self.src.fetch("BV1empty0000")
        self.assertEqual(result.status, "empty")

    def test_fetch_retryable(self) -> None:
        self.http.raise_on["view/detail"] = RetryableError("HTTP 429")
        result = self.src.fetch("BV1retry0000")
        self.assertEqual(result.status, "retry")
        self.assertIn("429", result.error)

    def test_fetch_failed_permanent(self) -> None:
        self.http.view = {"code": -404, "message": "not found"}
        result = self.src.fetch("BV1fail00000")
        self.assertEqual(result.status, "failed")
        self.assertIn("-404", result.error)

    def test_charge_exclusive_empty(self) -> None:
        self.http.view["data"]["View"]["is_upower_exclusive"] = True
        self.http.view["data"]["View"]["is_upower_play"] = False
        result = self.src.fetch("BV1charge000")
        self.assertEqual(result.status, "empty")
        self.assertEqual(result.error, "charge_exclusive_blocked")


if __name__ == "__main__":
    unittest.main()
