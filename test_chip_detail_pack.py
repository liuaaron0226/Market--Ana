"""首頁六張籌碼卡點開的名單：每日抓取寫出的原件 → logs/build_stock_detail.py → detail-data.js 要接得起來。

本人 11-17 回報：籌碼卡點開明細，最近幾天都「這一天沒有資料」。根因是每日流程只寫 t86／margin，
沒寫每日收盤行情（stock-detail/quotes），建置算不出金額，外資、投信、成交金額的名單整批變空。
這裡從 daily_ingest.listed_chip_detail_day 的真實輸出建一次明細，斷言每張卡最新一天的名單都有人。

用法：PYTHONPATH=. python -m unittest test_chip_detail_pack
"""
from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from pathlib import Path

import daily_ingest as di
import stock_history as sh
from cloud import market_pack_daily as mpd
from test_listed_institutional import t86_json
from test_stock_history import FIELDS as QUOTE_FIELDS

REPO = Path(__file__).resolve().parent
EMPTY = {"stat": "很抱歉，沒有符合條件的資料!", "total": 0}


def quote_row(code: str, name: str, amount: int, close: str) -> list[str]:
    return [code, name, "1,000", "1", f"{amount:,}", close, close, close, close, "<p style= color:red>+</p>", "1.00",
            close, "1", close, "1", "0.00"]


def mi_index(day: str, rows: list[list[str]]) -> dict:
    return {"tables": [{"title": "價格指數(臺灣證券交易所)", "fields": ["指數"], "data": [["加權"]]},
                       {"title": f"{day} 每日收盤行情(全部(不含權證、牛熊證、可展延牛熊證))",
                        "fields": QUOTE_FIELDS, "data": rows}],
            "type": "ALLBUT0999", "stat": "OK", "date": day}


def t86_row(code: str, name: str, foreign: int, trust: int) -> list[str]:
    # 欄位順序同 test_listed_institutional.FIELDS：外資買賣超在第 5 欄、投信在第 11 欄
    return [code, name, "0", "0", f"{foreign:,}", "0", "0", "0", "0", "0", f"{trust:,}",
            "0", "0", "0", "0", "0", "0", "0", "0"]


def margin_payload(day: str, rows: list[list[str]]) -> dict:
    return {"stat": "OK", "date": day, "tables": [
        {"title": "信用交易統計", "fields": ["項目"], "data": [["融資金額(仟元)", "1", "2", "3", "4", "5"]]},
        {"title": "融資融券彙總", "fields": ["代號"], "data": rows}]}


def margin_row(code: str, name: str, before: int, after: int, short_before: int, short_after: int) -> list[str]:
    return [code, name, "0", "0", "0", str(before), str(after), "0", "0", "0", "0",
            str(short_before), str(short_after), "0", "0", ""]


class Network:
    """證交所：MI_MARGN（個股與大盤）、TWT93U。沒列到的日子一律回「查無資料」（官方還沒公布）。"""

    def __init__(self, margin: dict[str, dict]) -> None:
        self.margin = margin
        self.calls: list[tuple[str, str]] = []

    def __call__(self, url, params):
        day = str(params.get("date"))
        self.calls.append((url.rsplit("/", 1)[-1], day))
        body = self.margin.get(day) if url.endswith("MI_MARGN") and params.get("selectType") == "ALL" else None
        return sh.Response(200, "application/json", json.dumps(body or EMPTY, ensure_ascii=False).encode(), url)


def ingest(root: Path, day: str, *, t86: list, quotes: list, network: Network) -> dict:
    """模擬每日流程跑完那一天：行情、上市法人原件已由前面的步驟寫好，再跑籌碼明細那一步。"""
    compact = day.replace("-", "")
    raw = sh.raw_file(root / "data/stock-history", compact)
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(gzip.compress(json.dumps(mi_index(compact, quotes), ensure_ascii=False).encode("utf-8")))
    institutional = root / "data/listed-institutional/raw" / f"{compact}.json"
    institutional.parent.mkdir(parents=True, exist_ok=True)
    institutional.write_bytes(t86_json(day=compact, rows=t86))
    return di.listed_chip_detail_day(root, day, get=network, sleep=lambda s: None)


def build_detail(root: Path) -> dict:
    """跑每日大盤包用的同一支建置腳本（logs/build_stock_detail.py，ROOT 換成 root）。"""
    web = root / "web"
    web.mkdir(exist_ok=True)
    (web / "chips-data.js").write_text('window.MARKET_CHIPS = {"futures": []};\n', encoding="utf-8")
    mpd._run_script(REPO / "logs/build_stock_detail.py", root)
    text = (web / "detail-data.js").read_text(encoding="utf-8").strip()
    return json.loads(text[len("window.MARKET_DETAIL = "):].rstrip(";"))


QUOTES = [quote_row("2330", "台積電", 50_000_000_000, "1000.00"), quote_row("2317", "鴻海", 20_000_000_000, "200.00"),
          quote_row("2454", "聯發科", 9_000_000_000, "1500.00")]
T86 = [t86_row("2330", "台積電", 5_000_000, -300_000), t86_row("2317", "鴻海", -8_000_000, 400_000),
       t86_row("2454", "聯發科", 1_000_000, 0)]
MARGIN = [margin_row("2330", "台積電", 30000, 30500, 100, 90), margin_row("2317", "鴻海", 50000, 49000, 200, 260)]


class HomepageChipListsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_latest_day_lists_are_filled_from_the_daily_ingest_output(self) -> None:
        # 11-06 晚上融資已公布；11-09 是最新一天，融資官方晚上才公布（抓的時候還查無資料）
        network = Network({"20261106": margin_payload("20261106", MARGIN)})
        ingest(self.root, "2026-11-06", t86=T86, quotes=QUOTES, network=network)
        ingest(self.root, "2026-11-09", t86=T86, quotes=QUOTES, network=network)
        detail = build_detail(self.root)

        latest = detail["by_date"]["2026-11-09"]
        self.assertEqual([r["code"] for r in latest.get("foreign_buy", [])], ["2330", "2454"], "外資買超名單")
        self.assertEqual([r["code"] for r in latest.get("foreign_sell", [])], ["2317"], "外資賣超名單")
        self.assertEqual([r["code"] for r in latest.get("trust_buy", [])], ["2317"], "投信買超名單")
        self.assertEqual([r["code"] for r in latest.get("turnover_top", [])][:1], ["2330"], "成交金額名單")

        now = detail["current"]
        for name in ("foreign_buy", "foreign_sell", "trust_buy", "trust_sell", "turnover_top",
                     "margin_up", "margin_down", "short_up", "short_down"):
            with self.subTest(list=name):
                self.assertTrue(now.get(name), f"目前前 10 的 {name} 是空的")
        # 每一項的「目前前 10」是自己最新有資料的那天：融資融券停在 11-06，其他是 11-09
        self.assertEqual(now["dates"], {"foreign": "2026-11-09", "trust": "2026-11-09", "margin": "2026-11-06",
                                        "short": "2026-11-06", "amount": "2026-11-09"})
        self.assertEqual(now["as_of"], "2026-11-09")
        # 每日大盤包建完做的同一個檢查：六張卡都對得上原件
        self.assertEqual(mpd.check_detail(detail, self.root / "data/market-snapshots/stock-detail"),
                         {"status": "ok", "data_date": "2026-11-09", "reason": None})
        # 融資融券的連續天數只算到 11-06 那天（資料只有一天 → 標「以上」）
        self.assertEqual((now["margin_up"][0]["code"], now["margin_up"][0]["streak"], now["margin_up"][0]["capped"]),
                         ("2330", 1, True))

    def test_days_missing_quotes_are_filled_back_from_the_quote_originals(self) -> None:
        # 修好之前的那幾天（11-09～11-16）只有 t86：下一次每日更新要從行情原件補回來，不另外打證交所
        network = Network({})
        ingest(self.root, "2026-11-06", t86=T86, quotes=QUOTES, network=network)
        old_quotes = self.root / "data/market-snapshots/stock-detail/quotes/quotes-20261106.json"
        old_quotes.unlink()
        result = ingest(self.root, "2026-11-09", t86=T86, quotes=QUOTES, network=network)
        self.assertEqual(result["quotes"]["status"], "copied")
        self.assertEqual(result["quotes_backfill"], {"status": "ok", "copied": ["20261106"], "missing": []})
        self.assertNotIn("MI_INDEX", {name for name, _ in network.calls})
        saved = json.loads(old_quotes.read_text(encoding="utf-8"))
        self.assertEqual(saved["data"], mi_index("20261106", QUOTES))
        self.assertTrue(build_detail(self.root)["by_date"]["2026-11-06"]["foreign_buy"])

    def test_quotes_without_an_original_are_reported_not_invented(self) -> None:
        compact = "20261110"
        t86 = self.root / "data/market-snapshots/stock-detail/t86" / f"t86-{compact}.json"
        t86.parent.mkdir(parents=True)
        t86.write_text("{}", encoding="utf-8")
        result = di.listed_chip_detail_day(self.root, "2026-11-10", get=Network({}), sleep=lambda s: None,
                                           datasets=("quotes",))
        self.assertEqual(result["quotes"]["status"], "not_published")
        self.assertEqual(result["quotes_backfill"]["missing"], [compact])
        self.assertFalse((self.root / "data/market-snapshots/stock-detail/quotes").exists())


if __name__ == "__main__":
    unittest.main()
