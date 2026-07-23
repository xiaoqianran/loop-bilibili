"""Offline tests for per-UP hub README generation (shipped path)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages"))
sys.path.insert(0, str(ROOT))

from bili_subbatch.hub import (  # noqa: E402
    build_hub_videos,
    render_hub_readme,
    truncate_preview,
    write_up_hub,
)


class TestHubBuilder(unittest.TestCase):
    def test_truncate_preview(self):
        self.assertEqual(truncate_preview("hi", 10), "hi")
        long = "a" * 50
        out = truncate_preview(long, 20)
        self.assertTrue(out.endswith("…"))
        self.assertLessEqual(len(out), 20)

    def test_full_order_not_capped_at_20(self):
        """Catalog order lists ALL videos, including empty (no silent drop)."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cat = root / "catalogs" / "1-demo"
            up = root / "ups" / "1-demo"
            cat.mkdir(parents=True)
            (up / "srt").mkdir(parents=True)
            (up / "txt").mkdir(parents=True)

            # 25 catalog videos — hub must list all 25, not 20
            items = []
            for i in range(1, 26):
                bvid = f"BV{i:010d}"  # fake but unique
                # fix: BV need alnum - use BV1XXXX style
                bvid = f"BV1TEST{i:06d}"
                items.append(
                    {
                        "bvid": bvid,
                        "title": f"Video {i}",
                        "url": f"https://www.bilibili.com/video/{bvid}",
                        "date": f"2024-01-{i:02d}" if i <= 28 else "2024-02-01",
                        "series": "s",
                    }
                )
            (cat / "all.json").write_text(
                json.dumps(items, ensure_ascii=False), encoding="utf-8"
            )

            # only first 3 have subtitles
            index_lines = []
            for i, it in enumerate(items, 1):
                bvid = it["bvid"]
                if i <= 3:
                    (up / "srt" / f"{bvid}.srt").write_text(
                        "1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8"
                    )
                    body = f"Preview body for video {i}. " * 20
                    (up / "txt" / f"{bvid}.txt").write_text(body, encoding="utf-8")
                    index_lines.append(
                        json.dumps(
                            {
                                "bvid": bvid,
                                "status": "ok",
                                "title": it["title"],
                                "cue_count": 1,
                                "srt": f"srt/{bvid}.srt",
                                "txt": f"txt/{bvid}.txt",
                            },
                            ensure_ascii=False,
                        )
                    )
                else:
                    index_lines.append(
                        json.dumps(
                            {
                                "bvid": bvid,
                                "status": "empty",
                                "title": it["title"],
                                "cue_count": 0,
                                "reason": "no_subtitle",
                            },
                            ensure_ascii=False,
                        )
                    )
            (up / "index.jsonl").write_text(
                "\n".join(index_lines) + "\n", encoding="utf-8"
            )
            (up / "meta.json").write_text(
                json.dumps(
                    {
                        "slug": "1-demo",
                        "mid": "1",
                        "name": "demo",
                        "space_url": "https://space.bilibili.com/1",
                    }
                ),
                encoding="utf-8",
            )

            videos, order_src = build_hub_videos(up, catalog_dir=cat, preview_chars=40)
            self.assertEqual(len(videos), 25, "must list all catalog videos, not top 20")
            self.assertIn("catalog", order_src)
            # first three have files + preview
            self.assertIsNotNone(videos[0].srt)
            self.assertIsNotNone(videos[0].txt)
            self.assertTrue(videos[0].preview)
            self.assertLessEqual(len(videos[0].preview), 41)  # 40 + ellipsis
            # empty still listed
            self.assertIsNone(videos[10].srt)
            self.assertEqual(videos[10].status, "empty")

            md = write_up_hub(up, catalogs=root / "catalogs", preview_chars=40)
            text = md.read_text(encoding="utf-8")
            self.assertIn("### 1.", text)
            self.assertIn("### 25.", text)
            self.assertNotIn("### 26.", text)
            # relative links
            self.assertIn("txt/BV1TEST000001.txt", text)
            self.assertIn("srt/BV1TEST000001.srt", text)
            self.assertIn("https://www.bilibili.com/video/BV1TEST000001", text)
            # preview is prefix of real file
            real = (up / "txt" / "BV1TEST000001.txt").read_text(encoding="utf-8")
            # strip blockquote marker from first preview occurrence
            self.assertTrue(any(line.startswith("> ") for line in text.splitlines()))
            # ensure Video 25 appears (full list)
            self.assertIn("Video 25", text)
            # empty label
            self.assertIn("无字幕", text)

    def test_render_includes_missing_without_files(self):
        from bili_subbatch.hub import HubVideo

        vids = [
            HubVideo(
                bvid="BV1AAAA",
                title="Has",
                url="https://www.bilibili.com/video/BV1AAAA",
                status="ok",
                cue_count=3,
                srt="srt/BV1AAAA.srt",
                txt="txt/BV1AAAA.txt",
                preview="hello world",
            ),
            HubVideo(
                bvid="BV1BBBB",
                title="None",
                url="https://www.bilibili.com/video/BV1BBBB",
                status="empty",
                cue_count=0,
            ),
        ]
        md = render_hub_readme(
            slug="9-x",
            mid="9",
            name="x",
            space_url="https://space.bilibili.com/9",
            videos=vids,
            order_source="test",
        )
        self.assertIn("BV1AAAA", md)
        self.assertIn("BV1BBBB", md)
        self.assertIn("[txt](txt/BV1AAAA.txt)", md)
        self.assertIn("无字幕", md)


if __name__ == "__main__":
    unittest.main()
