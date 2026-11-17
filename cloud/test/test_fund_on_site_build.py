"""基金當成一般個股 03 票資料端：每日建置把基金（貝萊德世界科技 A10）寫成基金名錄列、個股分析列（kind fund）與淨值長日線，D1 匯入跟著寫。

規格 .scratch/fund-as-stock/spec.md「名錄與代碼」「分析契約」：分析契約 v2 加 kind fund，這張票只交 quote（淨值、幣別、淨值日、漲跌）
與只有收盤的 bars；strategy／levels 先回資料不足（04 票補）。淨值超過 6 個平日沒更新時分析狀態是過期。
預期值取自回補原件 seeds/funds/BLKTENAU/nav-backfill.csv 的最後兩筆（2026-11-13 17.73、2026-11-16 17.14），不用程式重算。
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from functools import partial
from pathlib import Path

import fund_compare
import fund_stock
from cloud import build_stock_pack as build
from cloud import import_d1_seed as importer

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = [ROOT / "cloud/migrations/0004_listed_stock_data.sql", ROOT / "cloud/migrations/0018_etf_securities.sql",
              ROOT / "cloud/migrations/0026_fund_securities.sql"]
DATA_DATE = "2026-11-18"
EMPTY_RAW = Path(tempfile.mkdtemp(prefix="fund-raw-empty-"))
# 只讀回補原件（不讀本機或 R2 的每日抽取檔），預期值才固定。
REAL = partial(fund_compare.load_history, raw_dir=EMPTY_RAW)


class FundRowsTests(unittest.TestCase):
    def test_real_navs_become_one_directory_row_and_a_fund_analysis(self) -> None:
        rows, analyses, failures = fund_stock.fund_rows(DATA_DATE, loader=REAL)
        self.assertEqual(failures, [])
        self.assertEqual(rows, [{
            "code": "BLKTENAU", "name": "貝萊德世界科技 A10", "full_name": "貝萊德世界科技基金 A10美元(總報酬穩定配息)",
            "currency": "USD", "isin": "LU2533723974", "latest_bar_date": "2026-11-16",
            "available_bars": rows[0]["available_bars"], "analysis_status": "available"}])
        self.assertGreater(rows[0]["available_bars"], 900)
        [row] = analyses
        payload = row["payload"]
        self.assertEqual((row["code"], row["as_of"]), ("BLKTENAU", "2026-11-16"))
        self.assertEqual((payload["schema"], payload["kind"], payload["market"], payload["currency"], payload["name"]),
                         ("stock-analysis/v2", "fund", "境外基金", "USD", "貝萊德世界科技 A10"))
        self.assertEqual(payload["quote"], {"close": 17.14, "previous_close": 17.73, "change": -0.59, "chg_pct": -3.33,
                                            "change_sign": "-", "nav_date": "2026-11-16", "currency": "USD"})
        # 只有收盤的 bars（最近 120 筆），最後一筆就是最新淨值；沒有開高低量
        self.assertEqual(len(payload["bars"]), 120)
        self.assertEqual(payload["bars"][-2:], [{"date": "2026-11-13", "close": 17.73}, {"date": "2026-11-16", "close": 17.14}])
        # 策略與價位：04 票才算，這張回資料不足（不填假數字）
        self.assertIsNone(payload["strategy"])
        self.assertEqual((payload["levels"]["status"], payload["levels"]["resistance"], payload["levels"]["support"]),
                         ("insufficient", [], []))
        self.assertEqual(payload["institutional"], [])
        self.assertEqual(payload["notes"]["nav_lag"], "淨值每天一個，約晚 3 個營業日公布")
        build._check_payload("BLKTENAU", payload)

    def test_nav_more_than_six_weekdays_old_is_stale_but_still_analysed(self) -> None:
        rows, analyses, _ = fund_stock.fund_rows("2026-11-27", loader=REAL)
        self.assertEqual(rows[0]["analysis_status"], "stale")
        self.assertEqual(analyses[0]["payload"]["analysis_status"], "stale")
        rows, _, _ = fund_stock.fund_rows("2026-11-24", loader=REAL)
        self.assertEqual(rows[0]["analysis_status"], "available", "6 個平日內不算過期")

    def test_navs_after_the_data_date_are_not_used(self) -> None:
        rows, analyses, _ = fund_stock.fund_rows("2026-11-14", loader=REAL)
        self.assertEqual(rows[0]["latest_bar_date"], "2026-11-13")
        self.assertEqual(analyses[0]["payload"]["quote"]["close"], 17.73)

    def test_too_few_navs_is_insufficient_without_analysis(self) -> None:
        def few(code: str, *, as_of: str) -> dict:
            return {"navs": [("2026-11-10", 18.0), ("2026-11-11", 18.1)], "distributions": [], "fx": [], "issues": []}

        rows, analyses, failures = fund_stock.fund_rows(DATA_DATE, loader=few)
        self.assertEqual((rows[0]["analysis_status"], rows[0]["available_bars"], analyses, failures), ("insufficient", 2, [], []))

    def test_unreadable_data_is_unavailable_and_reported(self) -> None:
        def broken(code: str, *, as_of: str) -> dict:
            raise ValueError("BLKTENAU 回補原件 nav-backfill.csv 的 SHA-256 和清單不符，停止")

        rows, analyses, failures = fund_stock.fund_rows(DATA_DATE, loader=broken)
        self.assertEqual((rows[0]["analysis_status"], rows[0]["latest_bar_date"], analyses), ("unavailable", None, []))
        self.assertEqual([(f["code"], f["scope"]) for f in failures], [("BLKTENAU", "fund")])

    def test_history_payload_is_the_whole_nav_series_close_only(self) -> None:
        payloads = fund_stock.history_payloads(DATA_DATE, loader=REAL)
        history = payloads["BLKTENAU"]
        self.assertEqual((history["schema"], history["code"], history["as_of"]), ("stock-history/v1", "BLKTENAU", "2026-11-16"))
        self.assertEqual((history["d"][0], history["c"][0]), (20221201, 10.52))
        self.assertEqual((history["d"][-1], history["c"][-1]), (20261116, 17.14))
        self.assertEqual(len(history["d"]), len(history["c"]))
        self.assertFalse({"o", "h", "l", "v"} & history.keys())


def staging_db() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys = ON")
    for migration in MIGRATIONS:
        connection.executescript(migration.read_text(encoding="utf-8"))
    return connection


def seed_parts() -> tuple[dict, list[dict], list[dict], list[dict]]:
    rows, analyses, _ = fund_stock.fund_rows(DATA_DATE, loader=REAL)
    payload = {**analyses[0]["payload"], "code": "0050", "kind": "etf", "market": "上市", "as_of": DATA_DATE}
    analyses = analyses + [{"code": "0050", "as_of": DATA_DATE, "payload": payload}]
    manifest = {"version": "liquid-20261118-test-v2", "source_date": "2026-11-08", "source_url": "https://isin.twse.com.tw",
                "source_sha256": "5" * 64, "built_at": "2026-11-18T12:00:00Z", "catalog_count": 0,
                "analysis_count": len(analyses), "compatibility_codes": ["0050"], "data_date": DATA_DATE,
                "fund_count": len(rows)}
    return manifest, [], analyses, rows


class FundSeedTests(unittest.TestCase):
    def test_staging_install_writes_fund_directory_and_analysis(self) -> None:
        manifest, securities, analyses, rows = seed_parts()
        connection = staging_db()
        build.install_staging_version(connection, manifest, securities, analyses, funds=rows)
        self.assertEqual(connection.execute("SELECT code, name, currency, latest_bar_date, analysis_status FROM fund_securities").fetchall(),
                         [("BLKTENAU", "貝萊德世界科技 A10", "USD", "2026-11-16", "available")])
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM listed_securities").fetchone()[0], 0, "基金不混進普通股名錄")
        stored = json.loads(connection.execute("SELECT payload FROM stock_analysis WHERE code = 'BLKTENAU'").fetchone()[0])
        self.assertEqual(stored["kind"], "fund")

    def test_seed_check_rejects_mismatched_fund_rows(self) -> None:
        manifest, securities, analyses, rows = seed_parts()
        with self.assertRaises(ValueError):  # 基金分析列沒有名錄列
            build._validate_seed({**manifest, "fund_count": 0}, securities, analyses, funds=[])
        with self.assertRaises(ValueError):  # 可分析的基金缺分析列
            build._validate_seed({**manifest, "analysis_count": 1}, securities, analyses[1:], funds=rows)
        with self.assertRaises(ValueError):  # 數量和清單不符
            build._validate_seed({**manifest, "fund_count": 2}, securities, analyses, funds=rows)
        wrong = [{**analyses[0], "payload": {**analyses[0]["payload"], "kind": "common"}}, analyses[1]]
        with self.assertRaises(ValueError):  # 分析列不是基金
            build._validate_seed(manifest, securities, wrong, funds=rows)

    def test_seed_file_round_trips_through_d1_import_into_fund_table(self) -> None:
        manifest, securities, analyses, rows = seed_parts()
        with tempfile.TemporaryDirectory() as tmp:
            build._write_seed_jsonl(Path(tmp) / "seed.jsonl", manifest, securities, analyses, funds=rows)
            seed = importer.load_seed(Path(tmp))
        self.assertEqual([row["code"] for row in seed.funds], ["BLKTENAU"])
        self.assertIn("fund_securities", importer.STAGING_TABLES)
        self.assertIn("fund_securities", importer.OPTIONAL_TABLES, "0026 還沒套到資料庫時，匯入略過這張表，其他照常")
        self.assertIn("fund_securities", importer.ALWAYS_SENT_TABLES)
        connection = staging_db()
        for batch in importer.plan_batches(seed):
            connection.executescript(batch.sql)
        self.assertEqual(connection.execute("SELECT code, full_name, isin, available_bars FROM fund_securities").fetchone(),
                         ("BLKTENAU", "貝萊德世界科技基金 A10美元(總報酬穩定配息)", "LU2533723974", rows[0]["available_bars"]))

    def test_import_refuses_a_seed_whose_fund_rows_do_not_match_the_manifest(self) -> None:
        manifest, securities, analyses, rows = seed_parts()
        with tempfile.TemporaryDirectory() as tmp:
            build._write_seed_jsonl(Path(tmp) / "seed.jsonl", {**manifest, "fund_count": 2}, securities, analyses, funds=rows)
            seed = importer.load_seed(Path(tmp))
        with self.assertRaises(ValueError):
            importer.plan_batches(seed)


if __name__ == "__main__":
    unittest.main()
