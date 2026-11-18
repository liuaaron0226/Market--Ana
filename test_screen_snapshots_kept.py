"""選股快照與資金輪動要留進工作集（2026-11-19 查到：每日更新只寫在 runner 的暫存目錄，工作集的
data/market-screen-snapshots 一直停在舊的那批，大盤預測的產業成交比重與選股快照兩層因此天天是缺）。"""
import tempfile
import unittest
from pathlib import Path

import daily_update as du
import state_sync


class KeepSnapshotsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.source, self.target = self.tmp / "work", self.tmp / "data"
        for kind in ("rotation", "screen-foreign_net_rank-1d-buy", "universe"):
            (self.source / kind).mkdir(parents=True)
            (self.source / kind / "2026-11-18.json").write_text(f'{{"kind": "{kind}"}}', encoding="utf-8")
            (self.source / kind / "2026-11-17.json").write_text('{"old": true}', encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_that_days_batch_is_copied_with_the_same_layout(self):
        result = du.keep_screen_snapshots(self.source, self.target, "2026-11-18")
        self.assertEqual(result, {"copied": 3, "unchanged": 0, "conflicts": []})
        self.assertEqual((self.target / "rotation" / "2026-11-18.json").read_text(encoding="utf-8"), '{"kind": "rotation"}')
        self.assertFalse((self.target / "rotation" / "2026-11-17.json").exists())   # 只放那一天

    def test_running_again_changes_nothing_and_a_different_file_is_never_overwritten(self):
        du.keep_screen_snapshots(self.source, self.target, "2026-11-18")
        self.assertEqual(du.keep_screen_snapshots(self.source, self.target, "2026-11-18"),
                         {"copied": 0, "unchanged": 3, "conflicts": []})
        (self.target / "universe" / "2026-11-18.json").write_text("different", encoding="utf-8")
        result = du.keep_screen_snapshots(self.source, self.target, "2026-11-18")
        self.assertEqual(result["conflicts"], ["universe/2026-11-18.json"])
        self.assertEqual((self.target / "universe" / "2026-11-18.json").read_text(encoding="utf-8"), "different")

    def test_the_working_set_keeps_the_last_thirty_days_of_each_kind(self):
        [item] = [item for item in state_sync.WORKING_SET if item.path == "data/market-screen-snapshots"]
        self.assertEqual(item.recent_files, 30)


if __name__ == "__main__":
    unittest.main()
