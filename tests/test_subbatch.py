"""Offline tests for packages/bili_subbatch + subtitle module wiring."""

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

from bili_subbatch.client import SubtitleResult  # noqa: E402
from bili_subbatch.pack import pack_up, parse_slug  # noqa: E402
from bili_subbatch.wbi import enc_wbi  # noqa: E402
from subtitle.export import _PROFILE_PACE, export_subtitles  # noqa: E402


class TestSubbatchCore(unittest.TestCase):
    def test_enc_wbi_deterministic(self):
        q1 = enc_wbi({"bvid": "BV1"}, "img", "sub", wts=100)
        q2 = enc_wbi({"bvid": "BV1"}, "img", "sub", wts=100)
        self.assertEqual(q1, q2)
        self.assertIn("w_rid=", q1)

    def test_parse_slug(self):
        mid, name = parse_slug("280780745-张小珺商业访谈录")
        self.assertEqual(mid, "280780745")
        self.assertIn("张小珺", name)

    def test_pack_up(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "1-demo"
            src.mkdir()
            (src / "results.json").write_text(
                json.dumps(
                    [
                        {
                            "bvid": "BV1",
                            "status": "ok",
                            "title": "t",
                            "cue_count": 1,
                            "data": [
                                {
                                    "from_sec": 0,
                                    "to_sec": 1,
                                    "content": "hi",
                                    "index": 1,
                                }
                            ],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            dest = Path(td) / "out"
            meta = pack_up(src, dest, write_txt=True)
            self.assertEqual(meta["ok_count"], 1)
            self.assertTrue((dest / "srt" / "BV1.srt").exists())

    def test_profile_pace_for_subtitle(self):
        self.assertEqual(_PROFILE_PACE["conservative"][0], 0.5)

    def test_export_injectable(self):
        calls: list[str] = []

        def fake(bvid: str, cookie=None):
            calls.append(bvid)
            return SubtitleResult(bvid=bvid, status="empty")

        with tempfile.TemporaryDirectory() as td:
            from bili_subbatch import batch as batch_mod

            # use run_batch inject via export's run_batch
            out = Path(td)
            # call run_batch directly to verify resume path used by export
            batch_mod.run_batch(
                ["BV1", "BV2"],
                out,
                delay=0,
                jitter=0,
                resume=False,
                fetch_fn=fake,
            )
            self.assertEqual(calls, ["BV1", "BV2"])


if __name__ == "__main__":
    unittest.main()
