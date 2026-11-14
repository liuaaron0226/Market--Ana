"""個股 5 年歷史日線（本人 2026-11-14：先做 5 年、還原口徑同個股分析、放進資料庫每檔一列每天覆寫）。"""
from __future__ import annotations

import json
import sqlite3
import unittest
from pathlib import Path

from cloud.build_stock_history import HISTORY_BARS, build_history, history_payload, history_sql
from twon_analysis.indicators import Bar

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "cloud/migrations/0008_stock_history.sql"

# 國巨 2327：2025-08-13 收 546.00 後停牌，2025-08-25 一拆四恢復買賣（證交所 TWTB8U：參考價 136.50）。
BARS = [Bar("2025-08-13", 544.0, 548.0, 541.0, 546.0, 2_996_695),
        Bar("2025-08-25", 140.0, 150.0, 139.5, 143.0, 26_586_959)]
SPLIT = {"date": "2025-08-25", "code": "2327", "type": "變更面額", "close_before": 546.0, "reference": 136.5,
         "source": "TWTB8U-202508.json.gz"}


class HistoryPayloadTest(unittest.TestCase):
    def test_bars_are_stored_column_by_column_with_compact_dates(self) -> None:
        self.assertEqual(history_payload("2327", BARS), {
            "schema": "stock-history/v1", "code": "2327", "as_of": "2025-08-25",
            "d": [20250813, 20250825], "o": [544.0, 140.0], "h": [548.0, 150.0], "l": [541.0, 139.5],
            "c": [546.0, 143.0], "v": [2_996_695, 26_586_959]})

    def test_history_uses_the_same_adjusted_prices_as_the_analysis_and_five_years_of_bars(self) -> None:
        asked = []

        def loader(code, as_of, limit):
            asked.append((code, as_of, limit))
            return (BARS, {"market": "上市"}) if code == "2327" else ([], {})

        built = build_history(["2327", "9999"], "2025-08-25", loader=loader, capital_actions={"2327": [SPLIT]})
        self.assertEqual(asked, [("2327", "2025-08-25", HISTORY_BARS), ("9999", "2025-08-25", HISTORY_BARS)])
        self.assertEqual(HISTORY_BARS, 1270)
        self.assertEqual(list(built), ["2327"])                      # 查不到日線的代號不產生空列
        self.assertEqual(built["2327"]["c"], [136.5, 143.0])         # 一拆四已還原，沒有 546 → 143 的斷崖
        self.assertEqual(built["2327"]["v"], [11_986_780, 26_586_959])


class HistorySqlTest(unittest.TestCase):
    def setUp(self) -> None:
        self.db = sqlite3.connect(":memory:")
        self.db.executescript(MIGRATION.read_text(encoding="utf-8"))

    def run_sql(self, statements: list[str]) -> None:
        for statement in statements:
            self.db.executescript(statement)

    def rows(self) -> list[tuple]:
        return self.db.execute("SELECT code, data_date, json_extract(payload, '$.as_of') FROM stock_history ORDER BY code").fetchall()

    def test_each_day_overwrites_the_same_row_and_drops_stocks_that_left_the_universe(self) -> None:
        first = {code: history_payload(code, BARS) for code in ("2327", "2330")}
        statements, skipped = history_sql(first, "2025-08-25")
        self.assertEqual(skipped, [])
        self.run_sql(statements)
        self.assertEqual(self.rows(), [("2327", "2025-08-25", "2025-08-25"), ("2330", "2025-08-25", "2025-08-25")])

        later = [*BARS, Bar("2025-08-26", 143.0, 144.0, 138.0, 138.5, 25_422_011)]
        statements, _ = history_sql({"2327": history_payload("2327", later)}, "2025-08-26")
        self.run_sql(statements)
        self.assertEqual(self.rows(), [("2327", "2025-08-26", "2025-08-26")])   # 2330 今天不在母體：不留舊資料

    def test_a_row_too_large_for_one_statement_is_skipped_and_reported_not_truncated(self) -> None:
        huge = history_payload("2327", BARS)
        huge["v"] = [10 ** 15] * 9000
        statements, skipped = history_sql({"2327": huge, "2330": history_payload("2330", BARS)}, "2025-08-25")
        self.assertEqual(skipped, ["2327"])
        self.run_sql(statements)
        self.assertEqual([row[0] for row in self.rows()], ["2330"])
        self.assertTrue(all(len(statement.encode("utf-8")) <= 90_000 for statement in statements))

    def test_only_a_well_formed_history_payload_can_be_stored(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO stock_history VALUES ('2327', '2025-08-25', ?)", (json.dumps({"schema": "other"}),))


if __name__ == "__main__":
    unittest.main()
