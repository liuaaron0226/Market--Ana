"""自選股關注 I 票：多給 Gemini 看的四樣輸入（布林通道、融資融券、同產業其他股票、匯率與美債利率）。

經關注帳本的公開介面（假模型程式經真的子程序邊界）與各輸入的讀取介面測試；預期值都是手算的固定數字。
固定日 K、假模型、回答範本沿用 ``test_watch_ledger``。
"""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import treasury_yields
import watch_inputs as wi
import watch_ledger as wl
from test_forecast_ledger import calls, envelope, weekdays
from test_watch_ledger import BASE, WatchLedgerTestCase, answer, recording_model, stock_input

WITH_BOLLINGER = ("technical", "bollinger", "market", "news")

# 固定日 K 的最後 20 根收盤（第 3–22 根）：102、103、…、113、120、116、112、108、104、106、108、110
#   中線 = MA20 = 108.7；離差平方和 384.2 → 母體變異數 19.21 → 標準差 4.382921…
#   上緣 = 108.7 + 8.765843 = 117.47、下緣 = 108.7 − 8.765843 = 99.93（取到小數兩位）
#   收盤 110 在中線到上緣；寬度 = 17.531686 / 108.7 = 0.1613；只有 22 根，湊不滿近 120 個交易日 → 窄不窄寫 None
BOLL_UPPER, BOLL_LOWER = 117.47, 99.93


class BollingerLayerTests(WatchLedgerTestCase):
    def test_model_sees_where_the_close_sits_in_the_band(self):
        record = self.write(layers=WITH_BOLLINGER)["record"]
        self.assertEqual(record["input"]["bollinger"], {
            "date": BASE, "close": 110, "middle": 108.7, "upper": BOLL_UPPER, "lower": BOLL_LOWER,
            "zone": "中線到上緣", "width_ratio": 0.1613, "squeezed": None,
            "source": "本站以還原後日 K 自行計算（20 日均價 ± 2 個標準差）",
        })

    def test_band_edges_join_the_candidate_levels_with_names(self):
        candidates = self.write(layers=WITH_BOLLINGER)["record"]["input"]["candidates"]
        levels = [(c["id"], c["name"], c["value"], c["kind"], c["side"]) for c in candidates]
        self.assertIn(("boll_upper", "布林上緣", BOLL_UPPER, "bollinger_upper", "above"), levels)
        self.assertIn(("boll_lower", "布林下緣", BOLL_LOWER, "bollinger_lower", "at_or_below"), levels)
        # 依數值由高到低排進原本的清單：上緣在 120 與 115 之間、下緣在 103 與 95 之間。
        ids = [c["id"] for c in candidates]
        self.assertEqual(ids[ids.index("boll_upper") - 1:ids.index("boll_upper") + 2],
                         ["high_20", "boll_upper", "gap_down_upper_20260923"])
        self.assertEqual(ids[ids.index("boll_lower") - 1:ids.index("boll_lower") + 2],
                         ["swing_low_20260925", "boll_lower", "low_20"])

    def test_without_the_layer_nothing_changes(self):
        record = self.write()["record"]
        self.assertNotIn("bollinger", record["input"])
        self.assertFalse([c for c in record["input"]["candidates"] if c["id"].startswith("boll_")])

    def test_gemini_may_pick_the_upper_edge_as_its_trigger_line(self):
        reply = answer(status="接近", trigger="boll_upper", wrong="boll_lower")
        record = self.write(responses=[envelope(reply)], layers=WITH_BOLLINGER)["record"]
        self.assertEqual(record["kind"], "judgment")
        self.assertEqual(record["trigger_line"], {"id": "boll_upper", "name": "布林上緣", "value": BOLL_UPPER,
                                                  "kind": "bollinger_upper"})
        self.assertEqual(record["wrong_line"]["name"], "布林下緣")

    def test_band_edges_follow_the_same_side_rules_as_other_levels(self):
        # 「接近」卻挑收盤下方的下緣當觸發線：放錯邊，重試一次仍錯 → 漏記。
        wrong_side = answer(status="接近", trigger="boll_lower", wrong="low_20")
        result = self.write(responses=[envelope(wrong_side)] * 2, layers=WITH_BOLLINGER)
        self.assertEqual((result["record"]["kind"], result["record"]["miss_reason"]), ("miss", "answer_invalid"))
        self.assertEqual(calls(self.bin), 2)
        # 「可以留意」挑已經站上的下緣當觸發線、看錯線在它下方：合格。
        self.reset_calls()
        ready = answer(status="可以留意", trigger="boll_lower", wrong="low_20")
        record = self.write(responses=[envelope(ready)], layers=WITH_BOLLINGER, root=self.tmp / "ready")["record"]
        self.assertEqual((record["kind"], record["trigger_line"]["id"]), ("judgment", "boll_lower"))
        # 看錯線不在觸發線下方也一樣擋：上緣當看錯線、觸發線是收盤上方較低的 MA10。
        self.reset_calls()
        inverted = answer(status="接近", trigger="ma10", wrong="boll_upper")
        result = self.write(responses=[envelope(inverted)] * 2, layers=WITH_BOLLINGER, root=self.tmp / "inverted")
        self.assertEqual(result["record"]["miss_reason"], "answer_invalid")

    def test_prompt_says_the_band_is_only_a_position_description(self):
        self.write(model=recording_model(self.bin, answer()), layers=WITH_BOLLINGER)
        prompt = (self.bin / "prompt.txt").read_text(encoding="utf-8")
        self.assertIn("布林通道只是位置描述；單獨當買賣規則在歷史上贏不過買了放著", prompt)

    def test_prompt_without_the_layer_does_not_mention_the_band(self):
        self.write(model=recording_model(self.bin, answer()))
        self.assertNotIn("布林", (self.bin / "prompt.txt").read_text(encoding="utf-8"))

    def test_input_version_names_the_band(self):
        record = self.write(layers=WITH_BOLLINGER)["record"]
        self.assertTrue(record["input_version"].startswith("technical+bollinger+market+news-v1-"))
        self.assertEqual(record["input_version"], wl.input_version_of(WITH_BOLLINGER))
        # 沒打開這一層的舊版本不受影響（同一組層，版本和加這一層之前一樣）。
        self.assertNotEqual(wl.input_version_of(WITH_BOLLINGER), wl.input_version_of())

    def test_versions_without_the_new_layers_are_the_same_as_before_this_ticket(self):
        # D 票寫定的版本是 …-f24f144e（I 票之前）：沒打開新層的系列不因 I 票的層換版本。
        # 本人 2026-11-18 晚「放寬檢查（推薦）」改了所有組合共用的提示（照抄名稱裡的數字放行），所以全部一起換成新系列。
        self.assertEqual(wl.input_version_of(), "technical+market+news-v1-4669c18a")


# ---- 固定原件 ----

LISTED_FIELDS = ["代號", "名稱", "買進", "賣出", "現金償還", "前日餘額", "今日餘額", "次一營業日限額",
                 "買進", "賣出", "現券償還", "前日餘額", "今日餘額", "次一營業日限額", "資券互抵", "註記"]
MARGIN_DAYS = ["20261102", "20261103", "20261104", "20261105", "20261106", "20261109", "20261110"]
# 2330 每天的（融資今日餘額, 融券今日餘額），單位張；最後一天 11/10 在基準日之後。
MARGIN_2330 = [(1000, 50), (1010, 45), (1020, 40), (1005, 35), (990, 32), (1100, 30), (1500, 10)]


def listed_margin_file(root, day, rows):
    """證交所 MI_MARGN（selectType=ALL）原件，和每日抓取存的外包同一種形式。rows：{代號: (融資餘額, 融券餘額)}。"""
    stock_rows = [[code, "名稱", "0", "0", "0", "0", f"{margin:,}", "0", "0", "0", "0", "0", f"{short:,}", "0", "0", ""]
                  for code, (margin, short) in rows.items()]
    payload = {"stat": "OK", "date": day, "tables": [
        {"fields": ["項目", "買進", "賣出", "現金(券)償還", "前日餘額", "今日餘額"],
         "data": [["融資金額(仟元)", "0", "0", "0", "0", "0"]]},
        {"fields": LISTED_FIELDS, "data": stock_rows}]}
    path = root / "data/margin-history/raw" / f"margin-{day}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"fetched_at_utc": "x", "source_url": "https://www.twse.com.tw/", "data": payload},
                               ensure_ascii=False), encoding="utf-8")


OTC_FIELDS = ["代號", "名稱", "前資餘額(張)", "資買", "資賣", "現償", "資餘額", "資屬證金", "資使用率(%)", "資限額",
              "前券餘額(張)", "券賣", "券買", "券償", "券餘額", "券屬證金", "券使用率(%)", "券限額", "資券相抵(張)", "備註"]


def otc_margin_file(root, day, rows):
    """每日更新存的上櫃融資融券原件（daily_ingest.otc_credit_day，櫃買網站 margin/balance）。
    rows：{代號: (融資餘額, 融券餘額)}，單位張。"""
    data = [[code, "名稱", "0", "0", "0", "0", f"{margin:,}", "0", "0", "0", "0", "0", "0", "0", f"{short:,}",
             "0", "0", "0", "0", ""] for code, (margin, short) in rows.items()]
    path = root / "data/otc-credit/margin" / f"{day}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"fetched_at_utc": "x", "source_url": "u", "sha256": "x", "data": {
        "stat": "ok", "date": day, "tables": [{"fields": OTC_FIELDS, "data": data}]}}, ensure_ascii=False),
        encoding="utf-8")


def treasury_xml(rows):
    """財政部每日 par yield curve 的 XML（和 2026-11 實際抓到的同一種結構）。rows：[(日期, 2 年, 10 年)]，None 是官方留空。"""
    entries = []
    for number, (day, two, ten) in enumerate(rows, start=1):
        fields = [f'<d:Id m:type="Edm.Int32">{number}</d:Id>',
                  f'<d:NEW_DATE m:type="Edm.DateTime">{day}T00:00:00</d:NEW_DATE>',
                  '<d:BC_1MONTH m:type="Edm.Double">4.05</d:BC_1MONTH>']
        if two is not None:
            fields.append(f'<d:BC_2YEAR m:type="Edm.Double">{two}</d:BC_2YEAR>')
        if ten is not None:
            fields.append(f'<d:BC_10YEAR m:type="Edm.Double">{ten}</d:BC_10YEAR>')
        entries.append('<entry><id>x</id><content type="application/xml"><m:properties>'
                       + "".join(fields) + "</m:properties></content></entry>")
    return ('<?xml version="1.0" encoding="utf-8" standalone="yes" ?>'
            '<feed xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices" '
            'xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata" xmlns="http://www.w3.org/2005/Atom">'
            '<title type="text">DailyTreasuryYieldCurveRateData</title>' + "".join(entries) + "</feed>").encode("utf-8")


def cbc_rows(rows):
    """央行每日收盤匯率（FTDOpenData_Day）的 JSON 回應。rows：[(日期, 收盤)]。"""
    return json.dumps([{"日期": day.replace("-", ""), "NTD_USD": f"{close:.3f}"} for day, close in rows]).encode("utf-8")


class InputsTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


class MarginInputTests(InputsTestCase):
    def test_listed_stock_balances_and_five_day_change_with_the_data_date(self):
        for day, balances in zip(MARGIN_DAYS, MARGIN_2330):
            listed_margin_file(self.root, day, {"2330": balances, "2317": (1, 1)})
        # 基準日 11/09：當天融資 1100、融券 30；往前第 5 個有原件的交易日是 11/02（1000、50）
        #   → 融資 +100、融券 −20。11/10 在基準日之後，不算。
        self.assertEqual(wi.margin_input(self.root, "2330", "2026-11-09"), {
            "source": "twse", "attribution": wi.SOURCES["twse_margin"], "data_date": "2026-11-09", "unit": "張",
            "margin_balance": 1100, "margin_change_5d": 100, "short_balance": 30, "short_change_5d": -20,
            "compared_with": "2026-11-02",
        })

    def test_change_is_null_until_five_earlier_days_are_on_file(self):
        for day, balances in zip(MARGIN_DAYS[:3], MARGIN_2330[:3]):
            listed_margin_file(self.root, day, {"2330": balances})
        found = wi.margin_input(self.root, "2330", "2026-11-04")
        self.assertEqual((found["margin_balance"], found["margin_change_5d"], found["short_change_5d"],
                          found["compared_with"]), (1020, None, None, None))

    def test_latest_day_before_the_base_date_is_labelled_with_its_own_date(self):
        # 基準日 11/09 的原件還沒進來：給 11/06 那一天的數字，資料日期照實寫 11/06。
        for day, balances in zip(MARGIN_DAYS[:5], MARGIN_2330[:5]):
            listed_margin_file(self.root, day, {"2330": balances})
        self.assertEqual(wi.margin_input(self.root, "2330", "2026-11-09")["data_date"], "2026-11-06")

    def test_no_file_mentions_the_stock(self):
        listed_margin_file(self.root, "20261109", {"2317": (1, 1)})
        self.assertIsNone(wi.margin_input(self.root, "2330", "2026-11-09"))

    def test_otc_stock_reads_what_the_daily_update_already_stores(self):
        days = [("20261102", (500, 9)), ("20261103", (510, 9)), ("20261104", (520, 8)), ("20261105", (530, 8)),
                ("20261106", (540, 7)), ("20261109", (600, 5))]
        for day, balances in days:
            otc_margin_file(self.root, day, {"5347": balances, "6488": (1, 1)})
        # 11/09：融資 600、融券 5；往前第 5 個交易日 11/02：500、9 → +100、−4。
        self.assertEqual(wi.margin_input(self.root, "5347", "2026-11-09"), {
            "source": "tpex", "attribution": wi.SOURCES["tpex_margin"], "data_date": "2026-11-09", "unit": "張",
            "margin_balance": 600, "margin_change_5d": 100, "short_balance": 5, "short_change_5d": -4,
            "compared_with": "2026-11-02",
        })


class SectorInputTests(InputsTestCase):
    DAYS = weekdays("2026-11-02", 6)   # 11/02–11/09，基準日 11/09

    def member(self, name, industry, closes, days=None):
        return {"name": name, "industry": industry,
                "daily": [{"date": day, "close": close} for day, close in zip(days or self.DAYS, closes)]}

    def test_peers_in_the_same_official_industry_with_one_and_five_day_changes(self):
        members = {
            "2330": self.member("台積電", "半導體業", [600] * 6),
            "2303": self.member("聯電", "半導體業", [50, 51, 52, 53, 54, 55]),
            "2454": self.member("聯發科", "半導體業", [1000, 990, 980, 1000, 1010, 1000]),
            "3008": self.member("大立光", "光電業", [2000] * 6),
            "2408": self.member("南亞科", "半導體業", [70] * 5, days=self.DAYS[:5]),   # 基準日沒有日 K
        }
        # 聯電：當天 55/54−1 = +1.85%、近 5 日 55/50−1 = +10%；聯發科：1000/1010−1 = −0.99%、1000/1000−1 = 0%
        # 平均：當天 (1.851852 − 0.990099)/2 = 0.43%、近 5 日 (10 + 0)/2 = 5%
        self.assertEqual(wi.sector_input("2330", "2026-11-09", members), {
            "industry": "半導體業", "source": wi.SOURCES["catalog"], "data_date": "2026-11-09",
            "peers": [{"code": "2303", "name": "聯電", "change_1d_pct": 1.85, "change_5d_pct": 10.0},
                      {"code": "2454", "name": "聯發科", "change_1d_pct": -0.99, "change_5d_pct": 0.0}],
            "average_1d_pct": 0.43, "average_5d_pct": 5.0, "no_data": ["2408"],
        })

    def test_short_history_leaves_the_five_day_change_out_of_the_average(self):
        members = {
            "2330": self.member("台積電", "半導體業", [600] * 6),
            "2303": self.member("聯電", "半導體業", [50, 51, 52, 53, 54, 55]),
            "6770": self.member("力積電", "半導體業", [20, 22], days=self.DAYS[-2:]),
        }
        found = wi.sector_input("2330", "2026-11-09", members)
        self.assertEqual(found["peers"][1], {"code": "6770", "name": "力積電", "change_1d_pct": 10.0,
                                             "change_5d_pct": None})
        # 當天平均 (1.851852 + 10)/2 = 5.93；近 5 日只有聯電一檔 → 10
        self.assertEqual((found["average_1d_pct"], found["average_5d_pct"]), (5.93, 10.0))

    def test_unknown_industry_or_no_peers(self):
        self.assertIsNone(wi.sector_input("9999", "2026-11-09", {}))
        alone = wi.sector_input("2330", "2026-11-09", {"2330": self.member("台積電", "半導體業", [600] * 6)})
        self.assertEqual((alone["peers"], alone["average_1d_pct"], alone["average_5d_pct"]), ([], None, None))

    def test_industries_come_from_the_official_catalogs(self):
        listed = ("<table><tr><td>2330　台積電</td><td>TW0002330008</td><td>1994/09/05</td><td>上市</td>"
                  "<td>半導體業</td><td>ESVUFR</td><td></td></tr></table>")
        otc = ("<table><tr><td>5347　世界</td><td>TW0005347009</td><td>1998/03/09</td><td>上櫃</td>"
               "<td>半導體業</td><td>ESVUFR</td><td></td></tr></table>")
        for folder, name, html in (("data/listed-catalog/raw", "twse-isin-listed-20261108", listed),
                                   ("data/otc-catalog/raw", "tpex-isin-otc-20261108", otc)):
            raw = html.encode("cp950")
            (self.root / folder).mkdir(parents=True)
            (self.root / folder / f"{name}.html").write_bytes(raw)
            (self.root / folder / f"{name}.meta.json").write_text(json.dumps({
                "sha256": hashlib.sha256(raw).hexdigest(), "source_url": "u",
                "retrieved_at_utc": "2026-11-08T00:00:00Z"}))
        self.assertEqual(wi.catalog_industries(self.root), {"2330": ("台積電", "半導體業"), "5347": ("世界", "半導體業")})


class EnvironmentInputTests(InputsTestCase):
    def fx(self, folder, rows):
        path = self.root / "data/level-signals" / folder / "cbc-usdtwd.raw"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(cbc_rows(rows))

    def store_treasury(self, rows):
        for month in sorted({day[:7].replace("-", "") for day, _, _ in rows}):
            treasury_yields.store_month(self.root, month,
                                        treasury_xml([row for row in rows if row[0][:7].replace("-", "") == month]),
                                        fetched_at="t", source_url="u")

    def test_exchange_rate_and_treasury_yields_with_their_own_dates(self):
        days = weekdays("2026-10-13", 20)   # 10/13–11/09
        # 匯率：第 1 根（10/12）30.000、最後一根（11/09）30.600 → 近 20 個交易日 30.6/30 − 1 = +2%（新臺幣貶值）
        self.fx("2026-11-09", [("2026-10-12", 30.0)] + [(day, 30.6 if day == days[-1] else 30.3) for day in days])
        # 美債：10 月與 11 月各一個月份檔；第 1 筆 2 年 4.50、10 年 4.90，最後一筆（美國 11/06）4.70、5.15
        #   → 近 20 個交易日 +0.20、+0.25 個百分點。美國 11/09 那一筆還沒公布。
        us_days = weekdays("2026-10-09", 21)   # 10/09–11/06
        self.store_treasury([(day, 4.7 if day == us_days[-1] else 4.5, 5.15 if day == us_days[-1] else 4.9)
                             for day in us_days])
        self.assertEqual(wi.environment_input(self.root, "2026-11-09", edition="evening"), {
            "usdtwd": {"date": "2026-11-09", "close": 30.6, "change_20d_pct": 2.0, "quote": "每 1 美元兌新臺幣",
                       "source": wi.SOURCES["cbc_usdtwd"]},
            "us_treasury": {"date": "2026-11-06", "yield_2y_pct": 4.7, "yield_10y_pct": 5.15,
                            "change_20d_2y_pt": 0.2, "change_20d_10y_pt": 0.25, "source": wi.SOURCES["us_treasury"]},
            "missing": [],
        })

    def forecast_record(self, *, edition="evening", base="2026-11-09", fetched_at="2026-11-09T19:00:00+08:00",
                        fx_rows=None, treasury_rows=None):
        """預測帳本裡同一天、同一版的紀錄，sources 列出海外原件（overseas_inputs.archive_sources 的形狀）與壓好的原件。"""
        import gzip
        import hashlib
        ledger = self.root / "forecast-ledger"
        (ledger / "sources").mkdir(parents=True, exist_ok=True)
        (ledger / "forecasts").mkdir(parents=True, exist_ok=True)
        sources = []
        originals = [("cbc-usdtwd", "cbc", cbc_rows(fx_rows))] if fx_rows else []
        for month in sorted({day[:7].replace("-", "") for day, _, _ in treasury_rows or []}):
            originals.append((f"treasury-{month}", "treasury",
                              treasury_xml([row for row in treasury_rows if row[0][:7].replace("-", "") == month])))
        for name, kind, content in originals:
            packed = gzip.compress(content, mtime=0)
            archived = hashlib.sha256(packed).hexdigest() + ".raw.gz"
            (ledger / "sources" / archived).write_bytes(packed)
            sources.append({"name": name, "source": kind, "status": "ok", "fetched_at": fetched_at,
                            "archived_as": f"sources/{archived}"})
        record = {"base_date": base, "edition": edition, "kind": "forecast", "sources": sources}
        (ledger / "forecasts" / f"{base}__{edition}__m0.4__v.json").write_text(json.dumps(record), encoding="utf-8")
        return ledger

    def test_reads_the_forecast_ledgers_originals_when_the_local_ones_are_absent(self):
        # 2026-11-19 查到：關注排程讀不到水位訊號的匯率原件與美債保存區（都不在工作集），environment 天天缺。
        # 大盤預測每天已經抓了同一批原件存在帳本 sources/：讀同一天、同一版那一份（數字和上一個例子一樣）
        days = weekdays("2026-10-13", 20)
        us_days = weekdays("2026-10-09", 21)
        ledger = self.forecast_record(
            fx_rows=[("2026-10-12", 30.0)] + [(day, 30.6 if day == days[-1] else 30.3) for day in days],
            treasury_rows=[(day, 4.7 if day == us_days[-1] else 4.5, 5.15 if day == us_days[-1] else 4.9)
                           for day in us_days])
        found = wi.environment_input(self.root, "2026-11-09", edition="evening", forecast_ledger=ledger,
                                     cutoff="2026-11-09T20:30:00+08:00")
        self.assertEqual(found["usdtwd"]["close"], 30.6)
        self.assertEqual(found["usdtwd"]["change_20d_pct"], 2.0)
        self.assertEqual((found["us_treasury"]["date"], found["us_treasury"]["change_20d_10y_pt"]), ("2026-11-06", 0.25))
        self.assertEqual(found["missing"], [])

    def test_originals_fetched_after_the_moment_of_writing_are_not_used(self):
        days = weekdays("2026-10-13", 20)
        ledger = self.forecast_record(fx_rows=[(day, 30.0) for day in ["2026-10-12", *days]],
                                      fetched_at="2026-11-09T21:00:00+08:00")
        self.assertIsNone(wi.environment_input(self.root, "2026-11-09", edition="evening", forecast_ledger=ledger,
                                               cutoff="2026-11-09T20:30:00+08:00"))

    def test_another_edition_or_day_is_not_used(self):
        days = weekdays("2026-10-13", 20)
        ledger = self.forecast_record(edition="morning", fx_rows=[(day, 30.0) for day in ["2026-10-12", *days]])
        self.assertIsNone(wi.environment_input(self.root, "2026-11-09", edition="evening", forecast_ledger=ledger,
                                               cutoff="2026-11-09T20:30:00+08:00"))

    def test_missing_series_are_named_and_do_not_hide_the_others(self):
        self.fx("2026-11-09", [(day, 30.0) for day in weekdays("2026-10-12", 21)])
        found = wi.environment_input(self.root, "2026-11-09", edition="evening")
        self.assertEqual((found["us_treasury"], found["missing"]), (None, ["us_treasury"]))
        self.assertEqual(found["usdtwd"]["change_20d_pct"], 0.0)
        self.assertIsNone(wi.environment_input(self.root / "empty", "2026-11-09", edition="evening"))

    def test_exchange_rate_never_comes_from_after_the_as_of_date(self):
        self.fx("2026-11-06", [(day, 30.0) for day in weekdays("2026-10-09", 21)])
        self.fx("2026-11-10", [(day, 31.0) for day in weekdays("2026-10-13", 21)])
        self.assertEqual(wi.environment_input(self.root, "2026-11-09", edition="evening")["usdtwd"]["date"], "2026-11-06")

    def test_evening_edition_never_sees_the_us_close_of_the_same_date(self):
        # 台灣晚間寫判斷時，美國同一天還沒收盤：晚間版只能用美國前一個交易日；晨間版看得到美國剛收盤的那一天。
        # 之後補進來的同日資料也不會讓晚間版重跑時看到（重跑的輸入和當初相同）。
        self.store_treasury([("2026-11-06", 4.6, 5.0), ("2026-11-09", 4.7, 5.1)])
        evening = wi.environment_input(self.root, "2026-11-09", edition="evening")["us_treasury"]
        morning = wi.environment_input(self.root, "2026-11-09", edition="morning")["us_treasury"]
        self.assertEqual((evening["date"], evening["yield_2y_pct"]), ("2026-11-06", 4.6))
        self.assertEqual((morning["date"], morning["yield_2y_pct"]), ("2026-11-09", 4.7))
        with self.assertRaises(ValueError):
            wi.environment_input(self.root, "2026-11-09", edition="noon")

    def test_yield_left_blank_by_the_treasury_is_null_and_change_needs_twenty_earlier_days(self):
        self.store_treasury([("2026-11-05", 4.6, 5.0), ("2026-11-06", None, 5.1)])
        found = wi.environment_input(self.root, "2026-11-09", edition="evening")["us_treasury"]
        self.assertEqual((found["date"], found["yield_2y_pct"], found["yield_10y_pct"], found["change_20d_2y_pt"],
                          found["change_20d_10y_pt"]), ("2026-11-06", None, 5.1, None, None))


class TreasuryStoreTests(InputsTestCase):
    def test_parses_the_two_and_ten_year_yields_by_date(self):
        content = treasury_xml([("2026-11-02", 4.59, 4.91), ("2026-11-13", 4.88, 5.15)])
        self.assertEqual(treasury_yields.parse(content), [
            {"date": "2026-11-02", "yield_2y": 4.59, "yield_10y": 4.91},
            {"date": "2026-11-13", "yield_2y": 4.88, "yield_10y": 5.15}])

    def test_month_file_is_replaced_as_the_month_fills_in_but_never_shrinks(self):
        one = treasury_xml([("2026-11-02", 4.59, 4.91)])
        two = treasury_xml([("2026-11-02", 4.59, 4.91), ("2026-11-03", 4.6, 4.92)])
        self.assertEqual(treasury_yields.store_month(self.root, "202611", one, fetched_at="t1", source_url="u"), "stored")
        self.assertEqual(treasury_yields.store_month(self.root, "202611", two, fetched_at="t2", source_url="u"), "stored")
        self.assertEqual(treasury_yields.store_month(self.root, "202611", two, fetched_at="t3", source_url="u"),
                         "unchanged")
        with self.assertRaises(ValueError):
            treasury_yields.store_month(self.root, "202611", one, fetched_at="t4", source_url="u")
        self.assertEqual([row["date"] for row in treasury_yields.load(self.root)], ["2026-11-02", "2026-11-03"])
        meta = json.loads((self.root / treasury_yields.STORE_DIR / "ust-202611.meta.json").read_text(encoding="utf-8"))
        self.assertEqual((meta["fetched_at_utc"], meta["source_url"]), ("t2", "u"))

    def test_a_damaged_month_file_does_not_block_that_month_forever(self):
        held = self.root / treasury_yields.STORE_DIR / "ust-202611.xml"
        held.parent.mkdir(parents=True)
        held.write_bytes(b"<feed broken")
        content = treasury_xml([("2026-11-02", 4.59, 4.91)])
        self.assertEqual(treasury_yields.store_month(self.root, "202611", content, fetched_at="t", source_url="u"),
                         "stored")
        self.assertEqual(held.read_bytes(), content)

    def test_unexpected_responses_are_refused_without_writing(self):
        for content in (b"<html>maintenance</html>", b"not xml", treasury_xml([]),
                        treasury_xml([("2026-10-30", 4.5, 4.9)])):   # 不是 11 月
            with self.assertRaises(ValueError):
                treasury_yields.store_month(self.root, "202611", content, fetched_at="t", source_url="u")
        self.assertEqual(treasury_yields.load(self.root), [])

    def test_fetch_month_asks_the_treasury_for_that_month(self):
        seen = []

        def fetch(url):
            seen.append(url)
            return treasury_xml([("2026-11-02", 4.59, 4.91)])

        self.assertEqual(treasury_yields.fetch_month(self.root, "202611", fetch=fetch, now=lambda: "t"), "stored")
        self.assertEqual(seen, ["https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"
                                "?data=daily_treasury_yield_curve&field_tdr_date_value_month=202611"])


# ---- 交給 Gemini 的輸入 ----

ALL_NEW = ("technical", "bollinger", "sector", "margin", "environment", "market", "news")


def extras(margin_source="twse"):
    """I 票三層的固定輸入（各讀取介面回傳的形狀；數字是上面各測試手算過的）。"""
    return {
        "sector": {"industry": "半導體業", "source": wi.SOURCES["catalog"], "data_date": BASE,
                   "peers": [{"code": "2303", "name": "聯電", "change_1d_pct": 1.85, "change_5d_pct": 10.0}],
                   "average_1d_pct": 1.85, "average_5d_pct": 10.0, "no_data": []},
        "margin": {"source": margin_source, "attribution": "出處", "data_date": BASE, "unit": "張",
                   "margin_balance": 1100, "margin_change_5d": 100, "short_balance": 30, "short_change_5d": -20,
                   "compared_with": "2026-09-23"},
        "environment": {"usdtwd": {"date": BASE, "close": 30.6, "change_20d_pct": 2.0, "quote": "每 1 美元兌新臺幣",
                                   "source": "出處"},
                        "us_treasury": None, "missing": ["us_treasury"]},
    }


class NewLayersInPromptTests(WatchLedgerTestCase):
    def sent(self):
        prompt = (self.bin / "prompt.txt").read_text(encoding="utf-8")
        return prompt, json.loads(prompt[prompt.index("{"):prompt.rindex("}") + 1])

    def test_model_sees_each_input_with_its_data_date(self):
        self.write(stock_input(**extras()), model=recording_model(self.bin, answer()), layers=ALL_NEW)
        _, sent = self.sent()
        self.assertEqual((sent["margin"]["margin_change_5d"], sent["margin"]["data_date"]), (100, BASE))
        self.assertEqual((sent["sector"]["peers"][0]["change_5d_pct"], sent["sector"]["data_date"]), (10.0, BASE))
        self.assertEqual(sent["environment"]["usdtwd"]["date"], BASE)
        self.assertEqual(sent["environment"]["missing"], ["us_treasury"])
        self.assertEqual((sent["bollinger"]["upper"], sent["bollinger"]["date"]), (BOLL_UPPER, BASE))

    def test_missing_inputs_are_named_and_the_judgment_is_still_written(self):
        record = self.write(stock_input(sector=None, margin=None, environment=None), layers=ALL_NEW)["record"]
        self.assertEqual(record["kind"], "judgment")
        self.assertEqual(record["missing_layers"], ["sector", "margin", "environment"])
        self.assertEqual(record["input_version"], wl.input_version_of(ALL_NEW))

    def test_otc_margin_goes_into_the_prompt_like_listed_margin(self):
        # 上櫃融資融券在政府資料開放平台 11387 標政府資料開放授權（ANALYSIS_PLAN.md 2026-11-16 E 票第 1 片查到）。
        self.write(stock_input(**extras(margin_source="tpex")), model=recording_model(self.bin, answer()),
                   layers=ALL_NEW)
        _, sent = self.sent()
        self.assertEqual((sent["margin"]["source"], sent["margin"]["margin_balance"]), ("tpex", 1100))

    def test_each_new_input_is_its_own_input_version(self):
        versions = {wl.input_version_of(ALL_NEW[:count] + ("market", "news")) for count in range(1, 6)}
        self.assertEqual(len(versions), 5)
        self.assertTrue(wl.input_version_of(ALL_NEW).startswith(
            "technical+bollinger+sector+margin+environment+market+news-v1-"))


# ---- H 票：威科夫最後供應點（本人 2026-11-18 追加「要，也加進去」）----

WITH_LPSY = ("technical", "wyckoff_lpsy", "market", "news")


def lpsy_layer():
    """共用判斷算出的一層（test_wyckoff_lpsy 的固定日線：第 80 天跌破 95、第 81 天量 0.7 的反彈 → 是）。"""
    import test_wyckoff_lpsy as lpsy
    import wyckoff_lpsy
    return wyckoff_lpsy.stock_layer(*lpsy.basic_break().add(close=92, high=94, low=91, amount=70).stock_bars())


class LpsyLayerTests(WatchLedgerTestCase):
    def sent(self):
        prompt = (self.bin / "prompt.txt").read_text(encoding="utf-8")
        return prompt, json.loads(prompt[prompt.index("{"):prompt.rindex("}") + 1])

    def test_model_sees_the_reading_with_stock_level_history_and_the_avoid_note(self):
        layer = lpsy_layer()
        self.write(stock_input(wyckoff_lpsy=layer), model=recording_model(self.bin, answer()), layers=WITH_LPSY)
        prompt, sent = self.sent()
        self.assertEqual(sent["wyckoff_lpsy"]["reading"]["status"], "yes")
        self.assertEqual(sent["wyckoff_lpsy"]["reading"]["volume_ratio"], 0.7)
        self.assertEqual((sent["wyckoff_lpsy"]["history"]["count"], sent["wyckoff_lpsy"]["history"]["t"]), (1492, -4.8))
        self.assertEqual(sent["layer_parameters"]["wyckoff_lpsy"], {"layer": layer["version"]})
        for words in ("wyckoff_lpsy", "避開記號", "不是放空訊號", "不能單獨決定 status", "只能照寫", "不得拿來推論賣壓"):
            self.assertIn(words, prompt)

    def test_a_missing_reading_is_named_and_the_judgment_is_still_written(self):
        record = self.write(stock_input(wyckoff_lpsy=None), layers=WITH_LPSY)["record"]
        self.assertEqual(record["kind"], "judgment")
        self.assertEqual(record["missing_layers"], ["wyckoff_lpsy"])

    def test_the_layer_is_its_own_input_version_and_off_leaves_the_old_version_unchanged(self):
        self.assertNotEqual(wl.input_version_of(WITH_LPSY), wl.input_version_of(("technical", "market", "news")))
        self.assertTrue(wl.input_version_of(WITH_LPSY).startswith("technical+wyckoff_lpsy+market+news-v1-"))
        # 沒打開這一層的版本和 H 票之前相同（上一版排程那一組）
        self.assertEqual(wl.input_version_of(("technical", "bollinger", "sector", "margin", "environment", "market",
                                              "news", "lessons")),
                         "technical+bollinger+sector+margin+environment+market+news+lessons-v1-bb7f6136")  # 11-18 放寬檢查後（之前 …-b68cbcd8）


# ---- I 票（11-18 晚）：估值、借券與季 EPS、個股五塊訊號 ----

def valuation_db(root, rows):
    """rows：[(日期, 本益比, 淨值比, 殖利率)]，代號都是 2330。"""
    import sqlite3

    import valuation_history
    path = root / "data/valuation-history/valuation.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(valuation_history.SCHEMA_SQL)
    con.executemany("INSERT INTO valuation (market, date, code, name, close, dividend_yield_pct, dividend_year_roc, pe, "
                    "pb, fin_period_roc, schema) VALUES ('上市', ?, '2330', '台積電', 100, ?, 114, ?, ?, '115/2', 'v8')",
                    [(day, dy, pe, pb) for day, pe, pb, dy in rows])
    con.commit()
    con.close()


class ValuationInputTests(InputsTestCase):
    def test_latest_values_and_where_they_sit_in_the_past_year(self):
        valuation_db(self.root, [
            ("2025-09-30", 5.0, 0.5, 9.0),      # 超過一年，不算進區間
            ("2025-11-03", 10.0, 1.0, None),
            ("2026-03-02", 20.0, 2.0, None),
            ("2026-06-01", 15.0, 1.5, None),
            ("2026-09-01", 12.0, 1.2, None),
            ("2026-10-30", 18.0, 1.0, None),
            ("2026-11-02", 30.0, 3.0, None),    # 基準日之後，看不到
        ])
        found = wi.valuation_input(self.root, "2330", "2026-10-30")
        self.assertEqual(found["data_date"], "2026-10-30")
        self.assertEqual(found["period"], {"from": "2025-11-03", "to": "2026-10-30", "days": 5})
        # 本益比：最低 10、最高 20，現在 18 → (18 − 10) ÷ (20 − 10) ＝ 80%
        self.assertEqual(found["pe"], {"value": 18.0, "low_1y": 10.0, "high_1y": 20.0, "position_pct": 80.0})
        # 淨值比：現在 1.0 就是最低 → 0%
        self.assertEqual(found["pb"], {"value": 1.0, "low_1y": 1.0, "high_1y": 2.0, "position_pct": 0.0})
        self.assertEqual(found["dividend_yield_pct"], {"value": None, "low_1y": None, "high_1y": None,
                                                      "position_pct": None})

    def test_a_loss_making_pe_is_null_and_has_no_position(self):
        valuation_db(self.root, [("2026-10-29", 10.0, 1.0, 3.0), ("2026-10-30", None, 1.0, 3.0)])
        found = wi.valuation_input(self.root, "2330", "2026-10-30")
        self.assertEqual(found["pe"], {"value": None, "low_1y": 10.0, "high_1y": 10.0, "position_pct": None})
        # 一年內都一樣：最高等於最低，沒有位置可說
        self.assertIsNone(found["dividend_yield_pct"]["position_pct"])

    def test_no_rows_or_no_database(self):
        self.assertIsNone(wi.valuation_input(self.root, "2330", "2026-10-30"))
        valuation_db(self.root, [("2026-10-30", 10.0, 1.0, 3.0)])
        self.assertIsNone(wi.valuation_input(self.root, "2317", "2026-10-30"))


class LendingEpsInputTests(unittest.TestCase):
    QUARTERS = [{"period": p, "eps_twd": e, "available_on": a} for p, e, a in (
        ("2025-Q1", 2.0, "2025-05-15"), ("2025-Q2", 4.5, "2025-08-14"), ("2025-Q3", 7.5, "2025-11-14"),
        ("2025-Q4", 10.0, "2026-03-31"), ("2026-Q1", 3.0, "2026-05-15"), ("2026-Q2", 6.5, "2026-08-14"),
        ("2026-Q3", 9.9, "2026-11-16"))]   # 第三季法定期限在基準日之後，看不到

    def test_lending_balance_and_five_day_change_in_lots(self):
        borrow = [{"date": f"2026-10-{d:02d}", "balance": b} for d, b in
                  ((23, 1_000_000), (26, 1_100_000), (27, 1_200_000), (28, 1_300_000), (29, 1_400_000),
                   (30, 1_550_400))]
        found = wi.lending_eps_input({"borrow": borrow}, self.QUARTERS, "2026-10-30")
        # 1,550,400 股 ＝ 1,550 張；和往前第 5 筆比 1,550,400 − 1,000,000 ＝ 550,400 股 ＝ 550 張
        self.assertEqual(found["lending"], {"data_date": "2026-10-30", "balance_lots": 1550, "change_5d_lots": 550,
                                            "compared_with": "2026-10-23"})

    def test_last_four_single_quarter_eps_from_year_to_date_figures(self):
        found = wi.lending_eps_input({}, self.QUARTERS, "2026-10-30")
        self.assertIsNone(found["lending"])
        # 單季＝累計減上一季累計：2025-Q3 7.5 − 4.5 ＝ 3.0；Q4 10 − 7.5 ＝ 2.5；2026-Q1 就是 3.0；Q2 6.5 − 3.0 ＝ 3.5
        self.assertEqual(found["eps"]["quarters"], [
            {"period": "2025-Q3", "eps_ytd": 7.5, "eps_quarter": 3.0, "available_on": "2025-11-14"},
            {"period": "2025-Q4", "eps_ytd": 10.0, "eps_quarter": 2.5, "available_on": "2026-03-31"},
            {"period": "2026-Q1", "eps_ytd": 3.0, "eps_quarter": 3.0, "available_on": "2026-05-15"},
            {"period": "2026-Q2", "eps_ytd": 6.5, "eps_quarter": 3.5, "available_on": "2026-08-14"},
        ])
        self.assertEqual(found["eps"]["last_four_sum"], 12.0)   # 3.0 + 2.5 + 3.0 + 3.5

    def test_a_quarter_without_the_previous_year_to_date_has_no_single_quarter(self):
        found = wi.lending_eps_input({}, [q for q in self.QUARTERS if q["period"] != "2025-Q2"], "2026-10-30")
        first = found["eps"]["quarters"][0]
        self.assertEqual((first["period"], first["eps_quarter"]), ("2025-Q3", None))
        self.assertIsNone(found["eps"]["last_four_sum"])

    def test_nothing_at_all_is_none(self):
        self.assertIsNone(wi.lending_eps_input({}, [], "2026-10-30"))


class SignalsInputTests(unittest.TestCase):
    def test_keeps_the_blocks_and_drops_the_chart_arrays(self):
        signals = {"data_date": "2026-10-30", "limit_up_momentum": {"score": 72},
                   "versus_market": {"rows": [{"days": 5, "gap": 1.2}]}, "ma_alignment": None,
                   "margin_maintenance": {"low_pct": 150, "high_pct": 170, "data_date": "2026-10-29"},
                   "heavy_zones": {"close": 100, "zones": [{"low": 90, "high": 95}], "chart": {"closes": [1, 2]}},
                   "blocks": ["limit_up_momentum", "versus_market", "margin_maintenance", "heavy_zones"], "failed": []}
        found = wi.signals_input(signals)
        self.assertNotIn("chart", found["heavy_zones"])
        self.assertEqual(found["heavy_zones"]["zones"], [{"low": 90, "high": 95}])
        self.assertEqual(found["margin_maintenance"]["low_pct"], 150)
        self.assertIsNone(found["ma_alignment"])
        self.assertEqual(found["data_date"], "2026-10-30")
        self.assertIn("chart", signals["heavy_zones"])   # 不改呼叫端那一份

    def test_no_blocks_is_none(self):
        self.assertIsNone(wi.signals_input(None))
        self.assertIsNone(wi.signals_input({"data_date": "2026-10-30", "blocks": [], "failed": []}))


class NamesInWatchReasonTests(WatchLedgerTestCase):
    """自選股判斷有同一條「理由不得有數字」檢查，用同一套白名單（本人 2026-11-18 晚「放寬檢查（推薦）」）。"""

    def test_a_reason_copying_a_candidate_name_is_accepted(self):
        reply = answer(reason="站上前波高點（09/21）之前先觀察量能")
        record = self.write(stock_input(), model=recording_model(self.bin, reply))["record"]
        self.assertEqual(record["kind"], "judgment")

    def test_a_price_beside_the_name_is_refused(self):
        record = self.write(stock_input(), model=recording_model(self.bin, answer(reason="前波高點一百二十二元有壓")))["record"]
        self.assertEqual(record["kind"], "miss")
        self.assertIn("不得包含數字", record["attempts"][-1]["detail"])


WITH_NEW_THREE = ("technical", "valuation", "lending_eps", "signals", "market", "news")


def new_three():
    return {
        "valuation": {"data_date": BASE, "pe": {"value": 18.0, "low_1y": 10.0, "high_1y": 20.0, "position_pct": 80.0}},
        "lending_eps": {"lending": None, "eps": {"quarters": [], "last_four_sum": None}},
        "signals": {"data_date": BASE, "ma_alignment": {"summary": "多頭排列"}, "blocks": ["ma_alignment"]},
    }


class NewThreeLayersTests(WatchLedgerTestCase):
    def sent(self):
        prompt = (self.bin / "prompt.txt").read_text(encoding="utf-8")
        return prompt, json.loads(prompt[prompt.index("{"):prompt.rindex("}") + 1])

    def test_model_sees_each_layer_and_the_prompt_says_how_to_read_it(self):
        self.write(stock_input(**new_three()), model=recording_model(self.bin, answer()), layers=WITH_NEW_THREE)
        prompt, sent = self.sent()
        self.assertEqual(sent["valuation"]["pe"]["position_pct"], 80.0)
        self.assertEqual(sent["signals"]["ma_alignment"]["summary"], "多頭排列")
        self.assertIn("lending_eps", sent)
        for words in ("valuation", "本益比", "不代表便宜", "lending_eps", "借券", "單季", "signals", "融資維持率（估）",
                      "不是買賣訊號"):
            self.assertIn(words, prompt)

    def test_missing_layers_are_named_and_the_judgment_is_still_written(self):
        record = self.write(stock_input(valuation=None, lending_eps=None, signals=None),
                            layers=WITH_NEW_THREE)["record"]
        self.assertEqual(record["kind"], "judgment")
        self.assertEqual(record["missing_layers"], ["valuation", "lending_eps", "signals"])

    def test_the_layers_are_a_new_input_version_and_off_leaves_the_old_version_unchanged(self):
        self.assertTrue(wl.input_version_of(WITH_NEW_THREE).startswith(
            "technical+valuation+lending_eps+signals+market+news-v1-"))
        self.assertEqual(wl.input_version_of(("technical", "bollinger", "sector", "margin", "environment", "market",
                                              "news", "lessons")),
                         "technical+bollinger+sector+margin+environment+market+news+lessons-v1-bb7f6136")  # 11-18 放寬檢查後（之前 …-b68cbcd8）

if __name__ == "__main__":
    unittest.main()
