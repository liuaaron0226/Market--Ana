"""票 06 資料前置：官方上櫃普通股名錄的公開契約。"""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from otc_catalog import load_otc_catalog


ROOT = Path(__file__).resolve().parent
RAW = ROOT / "data/otc-catalog/raw/tpex-isin-otc-20261108.html"
META = ROOT / "data/otc-catalog/raw/tpex-isin-otc-20261108.meta.json"
HISTORY_DB = ROOT / "data/stock-history/stock-history.sqlite"


class OtcCatalogContractTest(unittest.TestCase):
    def test_official_otc_common_stock_can_be_looked_up(self) -> None:
        catalog = load_otc_catalog(RAW, META, HISTORY_DB)

        security = catalog.lookup("6488")

        self.assertIsNotNone(security)
        self.assertEqual(security.code, "6488")
        self.assertEqual(security.name, "環球晶")
        self.assertEqual(security.market, "上櫃")
        self.assertEqual(security.security_type, "普通股")
        self.assertEqual(security.source_url, "https://isin.twse.com.tw/isin/C_public.jsp?strMode=4")
        self.assertEqual(security.source_date, "2026-11-08")
        self.assertEqual(security.available_bars, 0)
        self.assertIsNone(security.latest_bar_date)
        self.assertFalse(security.analysis_eligible)

    def test_official_cfi_and_market_exclude_non_common_stock(self) -> None:
        catalog = load_otc_catalog(RAW, META, HISTORY_DB)

        self.assertEqual(len(catalog), 897)
        self.assertIsNone(catalog.lookup("00679B"))  # 上櫃 ETF
        self.assertIsNone(catalog.lookup("700019"))  # 上櫃權證
        self.assertIsNone(catalog.lookup("8349A"))  # 上櫃特別股
        self.assertIsNone(catalog.lookup("2330"))  # 上市普通股

    def test_market_filter_rejects_emerging_stock_even_with_common_stock_cfi(self) -> None:
        raw = RAW.read_bytes()
        injected_row = (
            "<table><tr><td>9999　測試興櫃</td><td>TW0009999000</td>"
            "<td>2026/11/08</td><td>興櫃</td><td>測試業</td>"
            "<td>ESVUFR</td><td></td></tr></table>"
        ).encode("cp950")
        modified_raw = raw + injected_row
        metadata = json.loads(META.read_text(encoding="utf-8"))
        metadata["sha256"] = hashlib.sha256(modified_raw).hexdigest()

        with tempfile.TemporaryDirectory() as directory:
            fixture_dir = Path(directory)
            raw_path = fixture_dir / "catalog.html"
            meta_path = fixture_dir / "catalog.meta.json"
            raw_path.write_bytes(modified_raw)
            meta_path.write_text(json.dumps(metadata), encoding="utf-8")

            catalog = load_otc_catalog(raw_path, meta_path, HISTORY_DB)

        self.assertIsNone(catalog.lookup("9999"))

    def test_existing_sqlite_history_is_reported_without_backfill(self) -> None:
        catalog = load_otc_catalog(RAW, META, HISTORY_DB)

        security = catalog.lookup("6423")

        self.assertIsNotNone(security)
        self.assertEqual(security.available_bars, 413)
        self.assertEqual(security.latest_bar_date, "2026-01-21")
        self.assertTrue(security.analysis_eligible)

    def test_rejects_raw_catalog_that_does_not_match_preserved_source_hash(self) -> None:
        metadata = json.loads(META.read_text(encoding="utf-8"))
        metadata["sha256"] = "0" * 64
        with tempfile.TemporaryDirectory() as directory:
            mismatched_meta = Path(directory) / "meta.json"
            mismatched_meta.write_text(json.dumps(metadata), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "SHA-256"):
                load_otc_catalog(RAW, mismatched_meta, HISTORY_DB)


if __name__ == "__main__":
    unittest.main()
