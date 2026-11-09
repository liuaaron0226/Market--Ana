"""票06 資料契約：上櫃代號由上櫃日 K 庫供應，上市代號仍只讀原行情庫。"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from twon_analysis.data import load_bars

SCHEMA = """CREATE TABLE daily (
    date TEXT NOT NULL, code TEXT NOT NULL, name TEXT NOT NULL, kind TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL,
    change_sign TEXT NOT NULL, change REAL,
    volume_shares INTEGER, trades INTEGER, turnover INTEGER,
    suspended INTEGER NOT NULL,
    PRIMARY KEY (date, code))"""


def make_db(path: Path, rows: list[tuple]) -> Path:
    connection = sqlite3.connect(path)
    connection.execute(SCHEMA)
    connection.executemany("INSERT INTO daily VALUES (?, ?, ?, 'common', ?, ?, ?, ?, '+', 1.0, 1000, 10, 100000, 0)", rows)
    connection.commit()
    connection.close()
    return path


class OtcRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        # 6423 在原行情庫留有轉市場前的舊列；現行官方市場是上櫃，必須只取上櫃庫。
        self.listed = make_db(root / "listed.sqlite", [
            ("2026-01-20", "1102", "亞泥", 40.0, 41.0, 39.0, 40.5),
            ("2026-01-21", "6423", "舊列", 10.0, 10.0, 10.0, 10.0),
        ])
        self.otc = make_db(root / "otc.sqlite", [
            ("2026-11-05", "6423", "億而得", 50.0, 52.0, 49.0, 51.0),
            ("2026-11-06", "6423", "億而得", 51.0, 53.0, 50.0, 52.0),
        ])

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_code_present_in_otc_db_reads_only_otc_rows(self) -> None:
        bars, info = load_bars("6423", "9999-12-31", db_path=self.listed, otc_db_path=self.otc)
        self.assertEqual([bar.date for bar in bars], ["2026-11-05", "2026-11-06"])
        self.assertEqual(info["name"], "億而得")
        self.assertEqual(info["date"], "2026-11-06")

    def test_listed_code_keeps_reading_listed_db(self) -> None:
        bars, info = load_bars("1102", "9999-12-31", db_path=self.listed, otc_db_path=self.otc)
        self.assertEqual([bar.date for bar in bars], ["2026-01-20"])
        self.assertEqual(info["name"], "亞泥")

    def test_missing_otc_db_falls_back_to_listed_only(self) -> None:
        bars, _ = load_bars("1102", "9999-12-31", db_path=self.listed, otc_db_path=Path(self.tmp.name) / "none.sqlite")
        self.assertEqual(len(bars), 1)

    def test_otc_db_is_opened_read_only(self) -> None:
        load_bars("6423", "9999-12-31", db_path=self.listed, otc_db_path=self.otc)
        connection = sqlite3.connect(self.otc)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM daily").fetchone()[0], 2)
        connection.close()


if __name__ == "__main__":
    unittest.main()
