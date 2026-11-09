"""etf_constituents：SITCA 月底前十大持股解析、SHA 驗證與可得日。"""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import etf_constituents as ec

SAMPLE = """<table><tr><td class=DTHeader>基金名稱</th><td>名次</th></tr>
<tr><td class='DTeven' align='left' rowspan='2'>甲台灣50ETF基金<br>(配息說明)</td><td>1</td><td>國內上市</td><td>2330</td><td>台積電</td><td>1,000</td><td></td><td></td><td>0</td><td>40.5</td></tr>
<tr><td>2</td><td>國內上櫃</td><td>6488</td><td>環球晶</td><td>500</td><td></td><td></td><td>0</td><td>5.1</td></tr>
<tr><td class='DTodd' align='left' rowspan='1'>乙主動ETF基金</td><td>1</td><td>國外上市</td><td>NVDA US</td><td>輝達</td><td>10</td><td></td><td></td><td>0</td><td>-</td></tr>
</table>"""


def write_month(root: Path, month: str, html_by_class: dict[str, str]) -> None:
    meta = []
    for cls, html in html_by_class.items():
        body = html.encode("utf-8")
        name = f"sitca-{month}-{cls}.html"
        (root / name).write_bytes(body)
        meta.append({"file": name, "sha256": hashlib.sha256(body).hexdigest()})
    (root / f"sitca-{month}.meta.json").write_text(json.dumps(meta), encoding="utf-8")


class ParseTest(unittest.TestCase):
    def test_parses_fund_name_without_notes_and_rows(self) -> None:
        rows = ec.parse_sitca(SAMPLE, "passive", "202609")
        self.assertEqual([r.code for r in rows], ["2330", "6488", "NVDA US"])
        self.assertEqual(rows[0].fund, "甲台灣50ETF基金")
        self.assertEqual(rows[1].fund, "甲台灣50ETF基金")
        self.assertEqual(rows[2].fund, "乙主動ETF基金")
        self.assertEqual(rows[0].weight_pct, 40.5)
        self.assertIsNone(rows[2].weight_pct)

    def test_rejects_page_without_table(self) -> None:
        with self.assertRaises(ValueError):
            ec.parse_sitca("<html>no table</html>", "passive", "202609")


class LoadTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        write_month(self.root, "202609", {"AH11": SAMPLE, "AL11": SAMPLE.replace("甲台灣50", "丙主動")})

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_sha_mismatch_is_rejected(self) -> None:
        (self.root / "sitca-202609-AH11.html").write_text(SAMPLE + "x", encoding="utf-8")
        with self.assertRaises(ValueError):
            ec.load_month("202609", self.root)

    def test_constituents_respect_availability_date(self) -> None:
        self.assertEqual(ec.constituents("2026-10-19", self.root), [])
        rows = ec.constituents("2026-10-20", self.root)
        self.assertTrue(rows)
        self.assertNotIn("NVDA US", {r.code for r in rows})

    def test_kind_filter_and_membership(self) -> None:
        self.assertTrue(ec.is_constituent("2330", "2026-11-09", self.root))
        self.assertFalse(ec.is_constituent("1101", "2026-11-09", self.root))
        active = ec.constituents("2026-11-09", self.root, kinds=("active",))
        self.assertTrue(all(r.kind == "active" for r in active))


CATHAY = {"result": [{"stockCode": "2330", "stockName": "台積電", "volumn": "1,131,000", "weights": "9.16"},
                     {"stockCode": "6488", "stockName": "環球晶", "volumn": "1,000", "weights": "-"}],
          "returnCode": "2000", "success": True}
CAPITAL = {"code": 200, "data": {"pcf": {"date2": "2026-11-06"},
                                 "stocks": [{"stocNo": "2881", "stocName": "富邦金", "weight": 15.4747}]}}
TAISHIN = ("<div>股票 </div><table><thead><tr><th>代號</th><th>名稱</th><th>股數</th><th>持股權重</th></tr></thead>"
           "<tbody><tr><td>5876 TT</td><td>上海商銀</td><td>4,401,290</td><td>6.1281%</td></tr>"
           "<tr><td>2618 TT</td><td>長榮航</td><td>5,087,000</td><td>6.0551%</td></tr></tbody></table>")


def write_pcf(root: Path, issuer: str, name: str, body: bytes) -> None:
    folder = root / "pcf" / issuer
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(body)
    meta_path = folder / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else []
    meta.append({"file": name, "sha256": hashlib.sha256(body).hexdigest()})
    meta_path.write_text(json.dumps(meta), encoding="utf-8")


class PcfFullTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        write_month(self.root, "202609", {"AH11": SAMPLE})
        write_pcf(self.root, "cathay", "00878.json", json.dumps(CATHAY, ensure_ascii=False).encode())
        write_pcf(self.root, "capital", "00919.json", json.dumps(CAPITAL, ensure_ascii=False).encode())
        write_pcf(self.root, "taishin", "00936.html", TAISHIN.encode())

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_parses_each_issuer_format_with_source_label(self) -> None:
        rows = ec.load_pcf(self.root)
        by_etf = {}
        for r in rows:
            by_etf.setdefault(r.etf, []).append(r)
        self.assertEqual([r.code for r in by_etf["00878"]], ["2330", "6488"])
        self.assertIsNone(by_etf["00878"][1].weight_pct)
        self.assertEqual(by_etf["00919"][0].code, "2881")
        self.assertEqual([r.code for r in by_etf["00936"]], ["5876", "2618"])
        self.assertAlmostEqual(by_etf["00936"][0].weight_pct, 6.1281)
        self.assertTrue(all(r.source == "pcf_full" for r in rows))

    def test_pcf_sha_mismatch_is_rejected(self) -> None:
        (self.root / "pcf/cathay/00878.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(ValueError):
            ec.load_pcf(self.root)

    def test_constituent_codes_prefer_full_and_keep_top10_for_uncovered(self) -> None:
        codes = ec.constituent_codes("2026-11-09", self.root)
        self.assertIn("2618", codes)   # 只在完整成分
        self.assertIn("6488", codes)   # 完整與前十大都有
        self.assertEqual(codes["2618"], {"pcf_full"})
        self.assertIn("sitca_top10", codes["2330"])   # 甲台灣50 沒有完整成分 → 仍保留前十大來源


FUBON = """<table><tr><th>股票代碼</th><th>股票名稱</th><th>股數</th><th>金額</th><th>權重(%)</th></tr>
<tr><td>2330</td><td>台積電</td><td>44,911,027</td><td>95,885,042,645</td><td>66.6958</td></tr>
<tr><td>WTEX6F</td><td>電子期</td><td>51</td><td>1</td><td>0.34</td></tr></table>"""
SINOPAC = "<table><tr><td>2330</td><td>台積電</td><td>386,779</td><td>35.02</td></tr><tr><td>總計</td><td></td><td></td><td>100</td></tr></table>"
MEGA = "<table><tr><th>股票代號</th><th>股票名稱</th><th>股數</th><th>持股權重</th></tr><tr><td>1303</td><td>南亞</td><td>1</td><td>3.21%</td></tr></table>"
FSITC = {"d": json.dumps([{"group": "1", "A": "2383", "B": "台光電", "C": "6.63", "D": "51,002"},
                          {"group": "2", "A": "TX", "B": "台指期", "C": "1.0", "D": "3"}], ensure_ascii=False)}
NOMURA = {"Entries": {"Data": {"Table": [{"TableTitle": "股票", "Rows": [["2454", "聯發科技", "1973000", "15.07"]]},
                                         {"TableTitle": "期貨", "Rows": [["TX", "台指", "83", "1.48"]]}]}}}


class MoreIssuersTest(unittest.TestCase):
    """E6：富邦、永豐、兆豐、第一金、野村五家格式；只取股票列，不含期貨、合計。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        write_pcf(self.root, "fubon", "0052.html", FUBON.encode())
        write_pcf(self.root, "sinopac", "00901.html", SINOPAC.encode())
        write_pcf(self.root, "mega", "00921.html", MEGA.encode())
        write_pcf(self.root, "fsitc", "00994A.json", json.dumps(FSITC, ensure_ascii=False).encode())
        write_pcf(self.root, "nomura", "00935.json", json.dumps(NOMURA, ensure_ascii=False).encode())

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_each_new_issuer_parses_only_stock_rows(self) -> None:
        by = {r.etf: r for r in ec.load_pcf(self.root)}
        got = {e: (r.code, r.issuer, r.weight_pct) for e, r in by.items()}
        self.assertEqual(got, {"0052": ("2330", "fubon", 66.6958), "00901": ("2330", "sinopac", 35.02),
                               "00921": ("1303", "mega", 3.21), "00994A": ("2383", "fsitc", 6.63),
                               "00935": ("2454", "nomura", 15.07)})


class RealPcfTest(unittest.TestCase):
    def test_real_counts_match_issuer_pages_for_every_issuer(self) -> None:
        if not (ec.RAW_DIR / "pcf/fubon/meta.json").exists():
            self.skipTest("沒有實際原件")
        count: dict[str, int] = {}
        for r in ec.load_pcf():
            count[r.etf] = count.get(r.etf, 0) + 1
        # 各家官網持股表股票檔數（2026-11-06 前後；E6 以官網頁面逐一核對）
        expected = {"0052": 72, "006208": 50, "00692": 100, "00901": 50, "006204": 277, "00921": 33,
                    "00690": 30, "00994A": 37, "00408A": 47, "00935": 50, "00944": 50}
        self.assertEqual({k: count.get(k) for k in expected}, expected)

    def test_real_0050_and_00878_counts_match_issuer_pages(self) -> None:
        if not (ec.RAW_DIR / "pcf/yuanta/meta.json").exists():
            self.skipTest("沒有實際原件")
        rows = ec.load_pcf()
        count = {}
        for r in rows:
            count[r.etf] = count.get(r.etf, 0) + 1
        self.assertEqual(count["0050"], 50)      # 元大官網 0050 持股表 50 檔（2026-11-06）
        self.assertEqual(count["00878"], 29)     # 國泰官網 00878 股票持股 29 檔（2026-11-06）


class RealDataTest(unittest.TestCase):
    def test_real_202609_has_ten_rows_per_fund(self) -> None:
        if not (ec.RAW_DIR / "sitca-202609.meta.json").exists():
            self.skipTest("沒有實際原件")
        rows = ec.load_month("202609")
        funds: dict[tuple[str, str], int] = {}
        for r in rows:
            funds[(r.kind, r.fund)] = funds.get((r.kind, r.fund), 0) + 1
        self.assertEqual(set(funds.values()), {10})
        self.assertGreaterEqual(len(funds), 80)
        self.assertTrue(ec.is_constituent("2330", "2026-11-09"))


if __name__ == "__main__":
    unittest.main()
