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

    def test_a_relative_path_with_dots_still_works(self):
        import os
        with tempfile.TemporaryDirectory() as tmp:
            inner = Path(tmp) / "a"
            inner.mkdir()
            here = os.getcwd()
            os.chdir(inner)
            try:
                path = Path("..") / "ledger" / ("y" * 230 + ".json")
                self.assertEqual(ledger_core.append(path, ledger_core.seal({"kind": "test"})), "written")
                self.assertTrue(ledger_core.exists(path))
                ledger_core.long_path(path).unlink()
            finally:
                os.chdir(here)

    def test_short_paths_are_unchanged(self):
        self.assertEqual(ledger_core.long_path(Path("a.json")), Path("a.json"))


if __name__ == "__main__":
    unittest.main()


class ForecastFileNameTests(unittest.TestCase):
    """J 票再加委買委賣後，輸入版本長到 148 字：檔名改用短的寫法（只有超過 130 字的版本才換，已上線的 I 票版本
    123 字照舊，舊紀錄與對帳檔的路徑一個都不變）。版本全文照樣寫在紀錄裡。"""

    def test_long_versions_get_a_short_file_tag_and_existing_ones_keep_theirs(self):
        import forecast_ledger as fl
        live = ("base+overseas+foreign_flow+wyckoff_lpsy+market_margin+otc_index+sector_share+screens+ofr_stress"
                "+similar+lessons-v1-74371474")
        self.assertEqual(fl._stem("2026-11-18", "evening", "0.4", live), f"2026-11-18__evening__m0.4__{live}")
        long = ("base+overseas+foreign_flow+wyckoff_lpsy+market_margin+otc_index+sector_share+screens+ofr_stress"
                "+intraday+news+order_flow+similar+lessons-v1-8eb80757")
        self.assertEqual(fl._stem("2026-11-18", "evening", "0.4", long), "2026-11-18__evening__m0.4__14layers-v1-8eb80757")
