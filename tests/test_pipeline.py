"""Offline tests for SubtitlePipeline + processors."""

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

from bili_subbatch.models import BatchConfig, SubtitleResult  # noqa: E402
from bili_subbatch.pipeline import SubtitlePipeline  # noqa: E402
from bili_subbatch.processors import (  # noqa: E402
    BaseProcessor,
    NormalizeCuesProcessor,
    default_processors,
)


class TestPipeline(unittest.TestCase):
    def test_pipeline_with_injectable_fetch(self):
        calls: list[str] = []

        def fake(bvid: str, cookie=None):
            calls.append(bvid)
            if bvid == "BV1":
                return SubtitleResult(
                    bvid=bvid,
                    status="ok",
                    cue_count=2,
                    data=[
                        {"from_sec": 0, "to_sec": 1, "content": "  hello  ", "index": 1},
                        {"from_sec": 1, "to_sec": 2, "content": "", "index": 2},
                        {"from_sec": 2, "to_sec": 3, "content": "world", "index": 3},
                    ],
                    title="t",
                )
            return SubtitleResult(bvid=bvid, status="empty")

        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            cfg = BatchConfig(delay=0, jitter=0, resume=False, write_srt=True, write_txt=True)
            stats = SubtitlePipeline(cfg, fetch_fn=fake).run(["BV1", "BV2"], out)
            self.assertEqual(calls, ["BV1", "BV2"])
            self.assertEqual(stats.ok, 1)
            self.assertEqual(stats.empty, 1)
            # normalize dropped empty cue → 2 cues
            item = json.loads((out / "items" / "BV1.json").read_text())
            self.assertEqual(item["cue_count"], 2)
            self.assertTrue((out / "srt" / "BV1.srt").exists())
            self.assertTrue((out / "txt" / "BV1.txt").exists())
            txt = (out / "txt" / "BV1.txt").read_text()
            self.assertIn("hello", txt)
            self.assertIn("world", txt)
            # index.jsonl has 2 lines
            lines = (out / "index.jsonl").read_text().strip().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertTrue((out / "meta.json").exists())

    def test_resume_skips_done(self):
        n = {"c": 0}

        def fake(bvid: str, cookie=None):
            n["c"] += 1
            return SubtitleResult(bvid=bvid, status="empty")

        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            (out / "done.json").write_text(
                json.dumps({"done": ["BV1"]}), encoding="utf-8"
            )
            (out / "results.json").write_text(
                json.dumps([{"bvid": "BV1", "status": "empty"}]), encoding="utf-8"
            )
            cfg = BatchConfig(delay=0, jitter=0, resume=True)
            SubtitlePipeline(cfg, fetch_fn=fake).run(["BV1", "BV2"], out)
            self.assertEqual(n["c"], 1)  # only BV2

    def test_custom_processor(self):
        class TagProc(BaseProcessor):
            name = "tag"

            def on_result(self, result, row, ctx):
                row["extras"] = {"tagged": True}
                return row

        def fake(bvid: str, cookie=None):
            return SubtitleResult(
                bvid=bvid,
                status="ok",
                data=[{"from_sec": 0, "to_sec": 1, "content": "x", "index": 1}],
                cue_count=1,
            )

        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            cfg = BatchConfig(delay=0, jitter=0, write_srt=False, write_txt=False, write_index=False)
            procs = [NormalizeCuesProcessor(), TagProc()]
            SubtitlePipeline(cfg, fetch_fn=fake, processors=procs).run(["BV9"], out)
            item = json.loads((out / "items" / "BV9.json").read_text())
            self.assertTrue(item.get("extras", {}).get("tagged"))

    def test_default_processors_names(self):
        names = [p.name for p in default_processors(BatchConfig())]
        self.assertIn("normalize_cues", names)
        self.assertIn("write_srt", names)


if __name__ == "__main__":
    unittest.main()
