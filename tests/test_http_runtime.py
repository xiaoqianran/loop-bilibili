"""Tests for SubBatch-aligned HTTP runtime helpers + risk backoff."""

from __future__ import annotations

import unittest

from loop_bilibili.sources._http import (
    cookie_ready_for_batch,
    cookie_summary,
    is_risk_text,
)
from loop_bilibili.worker import risk_retry_delay


class CookieAndRiskTest(unittest.TestCase):
    def test_cookie_summary_and_ready(self) -> None:
        empty = cookie_summary("")
        self.assertEqual(empty["length"], 0)
        self.assertFalse(cookie_ready_for_batch(""))

        full = (
            "SESSDATA=abc; bili_jct=def; DedeUserID=1; buvid3=x; buvid4=y"
        )
        s = cookie_summary(full)
        self.assertTrue(s["has_SESSDATA"])
        self.assertTrue(s["has_bili_jct"])
        self.assertTrue(s["has_buvid3"])
        self.assertTrue(cookie_ready_for_batch(full))

    def test_is_risk_text(self) -> None:
        self.assertTrue(is_risk_text("view/detail code=-352 风控校验失败"))
        self.assertTrue(is_risk_text('{"code": -412, "message": "x"}'))
        self.assertFalse(is_risk_text("view/detail code=-404 not found"))

    def test_risk_retry_delay_grows(self) -> None:
        d1 = risk_retry_delay(1, base=15.0, maximum=300.0)
        d2 = risk_retry_delay(2, base=15.0, maximum=300.0)
        d3 = risk_retry_delay(3, base=15.0, maximum=300.0)
        d_big = risk_retry_delay(20, base=15.0, maximum=300.0)
        # allow jitter: floor bounds
        self.assertGreaterEqual(d1, 15.0)
        self.assertLess(d1, 20.0)
        self.assertGreaterEqual(d2, 30.0)
        self.assertGreaterEqual(d3, 60.0)
        self.assertLessEqual(d_big, 303.0)  # 300 + up to 3 jitter


if __name__ == "__main__":
    unittest.main()
