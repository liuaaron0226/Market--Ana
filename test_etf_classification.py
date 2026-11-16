"""股票型 ETF 的判斷與名單（11 票）：字尾＋官方名錄類別，不一致的先不納入；不影響普通股名單與籌碼排行。"""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import etf_classification as ec
import liquid_universe as lu
from test_liquid_universe import DAYS, FakeEtfs, Fixture, etf, flat, make_db


class ClassifyTest(unittest.TestCase):
    def status(self, code: str, cfi: str | None) -> str:
        return ec.classify(code, cfi).status

    def test_stock_type_passive_active_and_overseas_are_included(self) -> None:
        # 0056 台股、00981A 主動式、00662 海外（NASDAQ）、00888 上櫃台股；名錄資產類別都是股票（第 5 碼 E）
        for code, cfi in (("0056", "CEOGEU"), ("00981A", "CEOIEU"), ("00662", "CEOGEU"), ("00888", "CEOIEU")):
            result = ec.classify(code, cfi)
            self.assertEqual(result.status, ec.STOCK, code)
            self.assertEqual(result.official_label, "股票")
        self.assertEqual(ec.classify("00981A", "CEOIEU").suffix_label, "主動式股票型")

    def test_structure_suffixes_are_excluded_even_when_catalog_says_stock(self) -> None:
        # 槓桿、反向、外幣計價是商品結構，CFI 沒有這個欄位（00685L 的 CFI 第 5 碼就是 E），不算不一致
        for code, cfi, reason in (("00631L", "CEOGDU", "槓桿型"), ("00685L", "CEOGEU", "槓桿型"),
                                  ("00632R", "CEOGDU", "反向型"), ("00625K", "CEOGEU", "外幣計價")):
            result = ec.classify(code, cfi)
            self.assertEqual((result.status, result.reason), (ec.EXCLUDED, reason), code)

    def test_bond_futures_and_balanced_are_excluded(self) -> None:
        for code, cfi, reason in (("00679B", "CEOIBU", "債券型"), ("00982D", "CEOIBU", "主動式債券型"),
                                  ("00635U", "CEOGDU", "期貨商品型"), ("00693U", "CEOGCU", "期貨商品型"),
                                  ("00981T", "CEOJLU", "平衡型（股債混合）")):
            result = ec.classify(code, cfi)
            self.assertEqual((result.status, result.reason), (ec.EXCLUDED, reason), code)

    def test_disagreement_is_not_included_and_says_why(self) -> None:
        reit = ec.classify("00714", "CEOIRU")          # 字尾看是股票型，名錄說不動產
        self.assertEqual(reit.status, ec.MISMATCH)
        self.assertIn("不動產", reit.reason)
        self.assertEqual(self.status("00401A", "CEOILU"), ec.MISMATCH)   # 主動式，名錄說混合
        self.assertEqual(self.status("00999B", "CEOIEU"), ec.MISMATCH)   # 字尾債券，名錄說股票

    def test_stock_suffix_missing_from_catalog_or_unknown_suffix_is_listed_not_guessed(self) -> None:
        self.assertEqual(self.status("00999", None), ec.MISMATCH)
        self.assertIn("查不到", ec.classify("00999", None).reason)
        self.assertEqual(self.status("00999Z", "CEOGEU"), ec.MISMATCH)
        # 字尾本來就不是股票型的，名錄查不到也只是排除
        self.assertEqual(self.status("00999L", None), ec.EXCLUDED)


def write_catalog(folder: Path, name: str, rows: list[list[str]]) -> tuple[Path, Path]:
    cells = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    raw = f"<html><table>{cells}</table></html>".encode("cp950")
    html = folder / f"{name}.html"
    html.write_bytes(raw)
    meta = folder / f"{name}.meta.json"
    meta.write_text(json.dumps({"sha256": hashlib.sha256(raw).hexdigest()}), encoding="utf-8")
    return html, meta


LISTED_ROWS = [
    ["ETF"],
    ["0050　元大台灣50", "TW0000050004", "2003/06/30", "上市", "", "CEOGEU", ""],
    ["0056　元大高股息", "TW0000056001", "2007/12/26", "上市", "", "CEOGEU", ""],
    ["00631L　元大台灣50正2", "TW00000631L0", "2014/10/31", "上市", "", "CEOGDU", ""],
    ["00714　群益道瓊美國地產", "TW0000071408", "2017/06/22", "上市", "", "CEOIRU", ""],
    ["2330　台積電", "TW0002330008", "1994/09/05", "上市", "半導體業", "ESVUFR", ""],
]
OTC_ROWS = [
    ["00679B　元大美債20年", "TW00000679B0", "2017/01/17", "上櫃", "", "CEOIBU", ""],
    ["00888　永豐台灣ESG", "TW0000088806", "2021/03/31", "上櫃", "", "CEOIEU", ""],
]


class CatalogTest(unittest.TestCase):
    def test_reads_only_etfs_from_both_official_catalogs_and_picks_stock_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            catalog = ec.load_etf_catalog(write_catalog(folder, "listed", LISTED_ROWS),
                                          write_catalog(folder, "otc", OTC_ROWS))
        self.assertEqual(sorted(catalog), ["0050", "0056", "00631L", "00679B", "00714", "00888"])
        self.assertEqual((catalog["00888"].market, catalog["00888"].name, catalog["00888"].cfi), ("上櫃", "永豐台灣ESG", "CEOIEU"))
        self.assertEqual(ec.stock_etf_codes(catalog), {"0050", "0056", "00888"})
        self.assertEqual(ec.stock_etf_codes(catalog, "上櫃"), {"00888"})

    def test_tampered_catalog_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            html, meta = write_catalog(Path(tmp), "listed", LISTED_ROWS)
            html.write_bytes(html.read_bytes() + b" ")
            with self.assertRaises(ValueError):
                ec.load_etf_catalog((html, meta))


CATALOG = {code: ec.EtfEntry(code, code, market, cfi) for code, market, cfi in (
    ("0050", "上市", "CEOGEU"), ("00878", "上市", "CEOGEU"), ("00631L", "上市", "CEOGDU"),
    ("00679B", "上櫃", "CEOIBU"), ("00714", "上市", "CEOIRU"))}


class StockEtfListTest(Fixture):
    """股票型 ETF 名單＝現成的 ETF 名單再過分類這一層；普通股名單與籌碼排行用的 ETF 範圍不變。"""

    def setUp(self) -> None:
        super().setUp()
        make_db(self.listed, flat("A", 900) + flat("B", 800) + flat("C", 700))
        make_db(self.otc, [])
        self.etfs = {"0050": etf("上市", 950), "00878": etf("上市", 850), "00631L": etf("上市", 880),
                     "00679B": etf("上櫃", 860), "00714": etf("上市", 870), "00999": etf("上市", 890)}

    def build(self, catalog):
        sources = lu.Sources(self.listed, self.otc, frozenset({"A", "B", "C"}), frozenset(), FakeEtfs(self.etfs),
                             etf_catalog=catalog)
        return lu.build_universe("2026-10-25", sources, enter_rank=3, exit_rank=4)

    def test_only_stock_type_etfs_are_in_the_stock_etf_list(self) -> None:
        result = self.build(CATALOG)
        self.assertEqual([m["code"] for m in result["stock_etf_members"]], ["0050", "00878"])
        self.assertTrue(lu.is_stock_etf_member(result, "00878"))
        self.assertFalse(lu.is_stock_etf_member(result, "00631L"))
        reasons = {item["code"]: (item["status"], item["reason"]) for item in result["etf_classification"]}
        self.assertEqual(reasons["00631L"], (ec.EXCLUDED, "槓桿型"))
        self.assertEqual(reasons["00679B"], (ec.EXCLUDED, "債券型"))
        self.assertEqual(reasons["00714"][0], ec.MISMATCH)
        self.assertEqual(reasons["00999"][0], ec.MISMATCH)   # 名錄裡沒有：先不納入
        self.assertNotIn("0050", reasons)

    def test_layer_does_not_change_common_list_or_chip_ranking_etfs(self) -> None:
        without = self.build(None)
        with_layer = self.build(CATALOG)
        self.assertEqual(with_layer["members"], without["members"])
        self.assertEqual(with_layer["etf_members"], without["etf_members"])
        self.assertEqual(len(with_layer["etf_members"]), 6)   # 籌碼排行仍含槓桿、債券
        self.assertNotIn("stock_etf_members", without)        # 沒給名錄時快照和原本一模一樣

    def test_0050_stays_visible_even_if_it_falls_out_of_the_etf_list(self) -> None:
        self.etfs["0050"] = etf("上市", 1)
        result = self.build(CATALOG)
        self.assertFalse(lu.is_stock_etf_member(result, "0050"))
        self.assertTrue(lu.is_member(result, "0050"))

    def test_hysteresis_carries_over_through_the_stock_layer(self) -> None:
        self.etfs["00878"] = etf("上市", 650)   # 同口徑第 4 名：只有前一天在名單才留下
        sources = lu.Sources(self.listed, self.otc, frozenset({"A", "B", "C"}), frozenset(), FakeEtfs(self.etfs),
                             etf_catalog=CATALOG)
        kept = lu.build_universe("2026-10-25", sources, enter_rank=3, exit_rank=10, previous={"00878"})
        self.assertIn("00878", [m["code"] for m in kept["stock_etf_members"]])
        fresh = lu.build_universe("2026-10-25", sources, enter_rank=3, exit_rank=10)
        self.assertNotIn("00878", [m["code"] for m in fresh["stock_etf_members"]])


if __name__ == "__main__":
    unittest.main()
