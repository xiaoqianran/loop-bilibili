"""CLI tests driving the real loop_bilibili.cli.main entrypoint."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from loop_bilibili.cli import main
from loop_bilibili.database import Database
from loop_bilibili.models import Video


class CliTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db_path = self.root / "loop.db"
        self.cfg_path = self.root / "config.toml"
        self.cfg_path.write_text(
            """
[database]
path = "unused.db"

[sources]
creators = []
homepage_enabled = false

[worker]
poll_interval = 0.01
subtitle_language = "zh"

[runtime]
cookie = ""
""",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _argv(self, *parts: str) -> list[str]:
        return ["--config", str(self.cfg_path), "--db", str(self.db_path), *parts]

    def test_init_and_status(self) -> None:
        rc = main(self._argv("init"))
        self.assertEqual(rc, 0)
        self.assertTrue(self.db_path.is_file())

        rc2 = main(self._argv("status", "--json"))
        self.assertEqual(rc2, 0)

    def test_status_missing_db(self) -> None:
        rc = main(self._argv("status"))
        self.assertEqual(rc, 1)

    def test_once_skip_refresh_offline(self) -> None:
        self.assertEqual(main(self._argv("init")), 0)
        # seed a job and process with fake subtitle source via monkeypatch
        db = Database(self.db_path)
        db.init_schema()
        db.upsert_video(Video(bvid="BV1cli00001", title="t"))
        db.enqueue_once("fetch_subtitle", "BV1cli00001")
        db.close()

        from loop_bilibili.sources.subtitle_bilibili import SubtitleFetchResult

        class Fake:
            def fetch(self, bvid: str) -> SubtitleFetchResult:
                return SubtitleFetchResult(
                    bvid=bvid, status="ok", text="hi", cues=[{"content": "hi"}]
                )

        with mock.patch(
            "loop_bilibili.cli.BilibiliSubtitleSource",
            return_value=Fake(),
        ):
            rc = main(self._argv("once", "--skip-refresh", "--max-jobs", "5"))
        self.assertEqual(rc, 0)

        db = Database(self.db_path)
        sub = db.get_subtitle("BV1cli00001", "zh")
        self.assertIsNotNone(sub)
        assert sub is not None
        self.assertEqual(sub["status"], "ok")
        analyze = db.get_job("analyze", "BV1cli00001")
        self.assertIsNotNone(analyze)
        db.close()

    def test_worker_max_idle(self) -> None:
        self.assertEqual(main(self._argv("init")), 0)
        rc = main(self._argv("worker", "--max-idle", "1", "--max-jobs", "0"))
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
