"""帳本檔名含輸入版本（層名一路加長）：Windows 上完整路徑超過 260 字元時照樣能寫、能讀、能判斷已存在。

J 票加了兩層之後，暫存資料夾裡的對帳檔路徑超過 260 字元，寫檔失敗（雲端排程是 Linux，不受影響）。
"""
import tempfile
import unittest
from pathlib import Path

import ledger_core


class LongPathTests(unittest.TestCase):
    def test_a_record_with_a_very_long_path_is_written_read_and_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reconciliations" / ("x" * 230 + ".json")   # 完整路徑一定超過 260 字元
            record = ledger_core.seal({"kind": "test", "value": 1})
            self.assertEqual(ledger_core.append(path, record), "written")
            self.assertTrue(ledger_core.exists(path))
            self.assertEqual(ledger_core.append(path, record), "unchanged")
            self.assertEqual(ledger_core.append(path, ledger_core.seal({"kind": "test", "value": 2})), "conflict")
            self.assertEqual(ledger_core.read_record(path)["value"], 1)
            self.assertEqual([r["value"] for r in ledger_core.read_folders(tmp, ("reconciliations",))["reconciliations"]],
                             [1])
            ledger_core.long_path(path).unlink()   # 暫存資料夾清得掉

    def test_short_paths_are_unchanged(self):
        self.assertEqual(ledger_core.long_path(Path("a.json")), Path("a.json"))


if __name__ == "__main__":
    unittest.main()
