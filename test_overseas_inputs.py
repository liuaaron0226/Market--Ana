"""海外層的公開介面（預測帳本 D 票第 9 片）。

抓取用假的網路回應；算出來的數字都是手算的固定值，算式寫在各測試的註解裡。
"""
import gzip
import hashlib
import json
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import overseas_inputs as oi

TOKEN = "SECRET-TOKEN-123"


def weekdays(start, end):
    day, out = date.fromisoformat(start), []
    while day <= date.fromisoformat(end):
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


def eodhd(rows):
    return json.dumps([{"date": d, "open": c, "high": c, "low": c, "close": c, "adjusted_close": c, "volume": 0}
                       for d, c in rows]).encode()


def treasury_xml(entries):
    body = "".join(
        "<entry><content type=\"application/xml\"><m:properties>"
        f"<d:NEW_DATE m:type=\"Edm.DateTime\">{d}T00:00:00</d:NEW_DATE>"
        + (f"<d:BC_2YEAR m:type=\"Edm.Double\">{two}</d:BC_2YEAR>" if two is not None else "")
        + f"<d:BC_10YEAR m:type=\"Edm.Double\">{ten}</d:BC_10YEAR>"
        "</m:properties></content></entry>" for d, two, ten in entries)
    return ("<?xml version=\"1.0\" encoding=\"utf-8\"?>"
            "<feed xmlns=\"http://www.w3.org/2005/Atom\" "
            "xmlns:m=\"http://schemas.microsoft.com/ado/2007/08/dataservices/metadata\" "
            "xmlns:d=\"http://schemas.microsoft.com/ado/2007/08/dataservices\">" + body + "</feed>").encode()


def fed_html(rows):
    def stamp(day):
        d = date.fromisoformat(day)
        return f"{d.day}-{d.strftime('%b').upper()}-{d.strftime('%y')}"
    cells = "".join(f"<tr>\n<th id=\"r1\" headers=\"a1\"> {stamp(d)} </th>\n"
                    f"<td headers=\"a2 a1 r1\">            {v}                </td>\n</tr>\n" for d, v in rows)
    return f"<html><table><thead><th scope=col>Date</th></thead>{cells}</table><script>ray</script></html>".encode()


def cbc_json(rows):
    return json.dumps([{"日期": d.replace("-", ""), "NTD_USD": str(v)} for d, v in rows], ensure_ascii=False).encode()


class FakeNet:
    """依網址前綴回應；記下每次請求的網址。"""

    def __init__(self, routes):
        self.routes = routes
        self.urls = []

    def __call__(self, url, **_):
        self.urls.append(url)
        for prefix, reply in self.routes.items():
            if url.startswith(prefix):
                if isinstance(reply, Exception):
                    raise reply
                return reply(url) if callable(reply) else reply
        raise RuntimeError("沒有這個網址")


# 美股那斯達克（美國日期）：10/30 100 → 11/02 98（−2%）→ 11/03 101.92（+4%）→ 11/04 101.92（0%）
#   → 11/05 101.1048（−0.8%）→ 11/06 101.1048（0%）→ 11/09 105（剛收盤那一晚，晨間版才看得到）
NASDAQ = [("2026-10-30", 100), ("2026-11-02", 98), ("2026-11-03", 101.92), ("2026-11-04", 101.92),
          ("2026-11-05", 101.1048), ("2026-11-06", 101.1048), ("2026-11-09", 105)]
SP500 = [(d, 200) for d, _ in NASDAQ]
# 日韓：基準日 11/09 有收盤；11/10 那根是未來，兩版都不能用。韓國 11/09 休市（沒有那一根）。
NIKKEI = [("2026-11-05", 1000), ("2026-11-06", 1010), ("2026-11-09", 1030.2), ("2026-11-10", 999)]
KOSPI = [("2026-11-05", 500), ("2026-11-06", 505)]
BASE = "2026-11-09"     # 週一（台股基準日）
SESSION = "2026-11-10"  # 週二開盤（晨間版）


def treasury_month(url):
    # 2 年期每天加 0.01；10 年期固定 4.50，只有 11/09 是 4.60（晨間版才看得到）。
    days = weekdays("2026-09-01", "2026-11-09")
    month = url.rsplit("=", 1)[1]
    picked = [(d, round(4 + 0.01 * i, 2), 4.6 if d == "2026-11-09" else 4.5)
              for i, d in enumerate(days) if d.replace("-", "")[:6] == month]
    return treasury_xml(picked)


def default_routes():
    fed_days = weekdays("2026-09-01", "2026-11-06")
    return {
        "https://eodhd.com/api/eod/IXIC.INDX": eodhd(NASDAQ),
        "https://eodhd.com/api/eod/GSPC.INDX": eodhd(SP500),
        "https://eodhd.com/api/eod/N225.INDX": eodhd(NIKKEI),
        "https://eodhd.com/api/eod/KS11.INDX": eodhd(KOSPI),
        "https://home.treasury.gov/": treasury_month,
        # 美元指數每週一發布上一週：資料只到 11/06（週五）。
        "https://www.federalreserve.gov/": fed_html([(d, 120 + i * 0.1) for i, d in enumerate(fed_days)]),
        "https://cpx.cbc.gov.tw/": cbc_json([(d, 32) for d in weekdays("2026-09-01", "2026-11-10")]),
    }


class OverseasTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def fetch(self, routes=None, *, reference=BASE, token=TOKEN, name="raw"):
        net = FakeNet(routes or default_routes())
        folder = self.tmp / name
        manifest = oi.fetch_sources(folder, reference_date=reference, eodhd_token=token, fetch=net,
                                    now=lambda: "2026-11-09T10:00:00Z")
        return folder, manifest, net

    def layer(self, edition="evening", routes=None, tw=None):
        folder, _, _ = self.fetch(routes, reference=BASE if edition == "evening" else SESSION)
        return oi.build_layer(folder, edition=edition, base_date=BASE,
                              session_date=SESSION if edition == "morning" else None, tw=tw)


class FetchTests(OverseasTestCase):
    def test_saves_each_original_with_fetch_time_and_hash_but_never_the_key(self):
        folder, manifest, net = self.fetch()
        by_name = {entry["name"]: entry for entry in manifest["sources"]}
        self.assertEqual(set(by_name), {"eodhd-GSPC.INDX", "eodhd-IXIC.INDX", "eodhd-N225.INDX", "eodhd-KS11.INDX",
                                        "treasury-202611", "treasury-202610", "treasury-202609", "fed-h10-broad",
                                        "cbc-usdtwd"})
        nasdaq = by_name["eodhd-IXIC.INDX"]
        raw = (folder / "eodhd-IXIC.INDX.raw").read_bytes()
        self.assertEqual(raw, eodhd(NASDAQ))
        self.assertEqual(nasdaq["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual((nasdaq["status"], nasdaq["fetched_at"], nasdaq["bytes"]), ("ok", "2026-11-09T10:00:00Z", len(raw)))
        self.assertTrue(any(TOKEN in url for url in net.urls))  # 金鑰只在請求裡
        self.assertNotIn(TOKEN, (folder / "manifest.json").read_text(encoding="utf-8"))
        self.assertIn("api_token=REDACTED", nasdaq["url"])

    def test_one_failed_source_does_not_stop_the_others(self):
        routes = default_routes()
        routes["https://eodhd.com/api/eod/KS11.INDX"] = TimeoutError()
        _, manifest, _ = self.fetch(routes)
        by_name = {entry["name"]: entry for entry in manifest["sources"]}
        self.assertEqual((by_name["eodhd-KS11.INDX"]["status"], by_name["eodhd-KS11.INDX"]["error_type"]),
                         ("error", "TimeoutError"))
        self.assertEqual(by_name["eodhd-N225.INDX"]["status"], "ok")

    def test_without_a_key_the_eodhd_sources_are_skipped_without_calling(self):
        _, manifest, net = self.fetch(token="")
        self.assertFalse(any("eodhd.com" in url for url in net.urls))
        skipped = [entry for entry in manifest["sources"] if entry["source"] == "eodhd"]
        self.assertEqual({entry["status"] for entry in skipped}, {"error"})
        self.assertEqual({entry["error_type"] for entry in skipped}, {"missing_key"})


class CutoffTests(OverseasTestCase):
    def test_evening_sees_asia_on_the_base_date_and_the_us_close_of_the_night_before(self):
        layer = self.layer("evening")
        status = layer["status"]
        # 晚間版（基準日 11/09 週一晚上）：美股 11/09 還沒開盤，最新是 11/06（週五）
        self.assertEqual((status["us_nasdaq"]["latest_date"], status["us_nasdaq"]["expected_date"],
                          status["us_nasdaq"]["status"]), ("2026-11-06", "2026-11-06", "ok"))
        # 日經：11/10 那根是未來，最新是基準日 11/09
        self.assertEqual((status["japan_nikkei"]["latest_date"], status["japan_nikkei"]["status"]), (BASE, "ok"))
        nikkei = layer["withheld"]["markets"]["japan_nikkei"]
        # 11/09 1030.2 ÷ 11/06 1010 − 1 = +2%
        self.assertEqual(nikkei["recent"][-1], {"date": BASE, "close": 1030.2, "change_pct": 2.0})

    def test_morning_sees_the_us_close_that_just_finished(self):
        layer = self.layer("morning")
        nasdaq = layer["withheld"]["markets"]["us_nasdaq"]
        # 晨間版（11/10 開盤前）：美股 11/09 剛收盤 105 ÷ 101.1048 − 1 = +3.85%
        self.assertEqual(nasdaq["recent"][-1], {"date": "2026-11-09", "close": 105, "change_pct": 3.85})
        self.assertEqual(layer["status"]["us_nasdaq"]["status"], "ok")
        # 日韓兩版一樣只到基準日
        self.assertEqual(layer["status"]["japan_nikkei"]["latest_date"], BASE)

    def test_a_closed_or_missing_market_is_marked_and_the_rest_still_comes_through(self):
        routes = default_routes()
        routes["https://eodhd.com/api/eod/GSPC.INDX"] = TimeoutError()
        layer = self.layer("evening", routes)
        self.assertEqual(layer["status"]["korea_kospi"]["status"], "stale")   # 11/09 沒有那一根（休市）
        self.assertEqual(layer["status"]["korea_kospi"]["latest_date"], "2026-11-06")
        self.assertEqual(layer["status"]["us_sp500"]["status"], "missing")
        self.assertEqual({item["key"] for item in layer["missing"]}, {"korea_kospi", "us_sp500"})
        self.assertEqual(layer["status"]["japan_nikkei"]["status"], "ok")


class PromptGateTests(OverseasTestCase):
    def test_index_numbers_from_eodhd_are_kept_but_not_handed_to_the_model(self):
        layer = self.layer("evening")
        prompt = json.dumps(layer["prompt"], ensure_ascii=False)
        self.assertNotIn("1030.2", prompt)
        self.assertNotIn("markets", layer["prompt"])
        self.assertIn("日本日經 225", layer["prompt"]["not_provided"])
        self.assertFalse(layer["status"]["japan_nikkei"]["in_prompt"])
        self.assertTrue(layer["status"]["treasury"]["in_prompt"])
        self.assertNotIn("japan_nikkei", layer["cutoffs"])
        self.assertIn("treasury", layer["cutoffs"])

    def test_prompt_notes_never_say_a_selloff_continues_intraday(self):
        layer = self.layer("evening")
        text = json.dumps(layer, ensure_ascii=False)
        self.assertNotRegex(text, "盤中[^，。」]*續跌")
        self.assertIn("不代表之後會續漲或續跌", json.dumps(layer["prompt"], ensure_ascii=False))


class TreasuryAndDollarTests(OverseasTestCase):
    def test_treasury_yields_carry_their_date_and_twenty_day_change(self):
        evening = self.layer("evening")["prompt"]["treasury"]
        # 晚間版只到 11/06：2 年期 20 個交易日加了 20 × 0.01 = 0.20 個百分點 = 20 bp；10 年期不變
        self.assertEqual((evening["date"], evening["change_20d_bp"]), ("2026-11-06", {"2y": 20.0, "10y": 0.0}))
        morning = self.layer("morning")["prompt"]["treasury"]
        self.assertEqual((morning["date"], morning["yield_10y"], morning["change_20d_bp"]["10y"]),
                         ("2026-11-09", 4.6, 10.0))

    def test_dollar_index_skips_missing_days_and_reports_the_weekly_lag(self):
        routes = default_routes()
        days = weekdays("2026-09-01", "2026-11-06")
        values = [(d, "ND" if d == "2026-11-05" else 120 + i * 0.1) for i, d in enumerate(days)]
        routes["https://www.federalreserve.gov/"] = fed_html(values)
        dollar = self.layer("evening", routes)["prompt"]["dollar_and_foreign"]["dollar_index"]
        self.assertEqual(dollar["date"], "2026-11-06")
        # 11/05 是 ND（沒有資料）：往前數 20 筆有資料的日子
        index_of = {d: i for i, d in enumerate(days)}
        last, base = 120 + index_of["2026-11-06"] * 0.1, 120 + (index_of["2026-11-06"] - 21) * 0.1
        self.assertAlmostEqual(dollar["change_20d_pct"], round((last / base - 1) * 100, 2))


def tw_series(foreign_by_day, etf):
    return {"foreign_net": foreign_by_day, "etf0050": [{"date": d, "open": o, "close": c} for d, o, c in etf]}


class NasdaqReadingTests(OverseasTestCase):
    def test_buckets_next_day_foreign_selling_and_0050_gap_with_its_own_open(self):
        # 台股：10/30 收 50。
        #   11/03（前一晚那斯達克 −2%，跌逾 1.5%）：開 49 → 跳空 −2%；收 49.49 → 盤中 +1%；外資賣
        #   11/04（+4%，漲逾 1.5%）：開 50.4798 → 跳空 +2%；收同 → 盤中 0%；外資買
        #   11/05（0%，±0.5% 內）：開 50.4798 → 跳空 0%；收 49.975002 → 盤中 −1%；外資賣
        #   11/06（−0.8%，跌 0.5～1.5%）：開 49.72512699 → 跳空 −0.5%；收同；外資買
        #   11/09（0%）：開 12 → 跳空 −76%（分割之類的事件），不算進統計
        etf = [("2026-10-30", 50, 50), ("2026-11-03", 49, 49.49), ("2026-11-04", 50.4798, 50.4798),
               ("2026-11-05", 50.4798, 49.975002), ("2026-11-06", 49.72512699, 49.72512699), ("2026-11-09", 12, 12)]
        foreign = {"2026-10-30": 1e9, "2026-11-03": -1e9, "2026-11-04": 1e9, "2026-11-05": -2e9,
                   "2026-11-06": 5e8, "2026-11-09": -3e9}
        reading = self.layer("evening", tw=tw_series(foreign, etf))["withheld"]["nasdaq_reading"]
        # 晚間版（11/09 晚上）看得到的是 11/06 那一晚：0% → ±0.5% 內
        self.assertEqual((reading["date"], reading["change_pct"], reading["bucket"]), ("2026-11-06", 0.0, "±0.5% 內"))
        stats = {row["bucket"]: row for row in reading["history"]["buckets"]}
        self.assertEqual(stats["跌逾 1.5%"], {"bucket": "跌逾 1.5%", "samples": 1, "foreign_sell_rate": 1.0,
                                             "mean_open_gap_pct": -2.0, "mean_intraday_pct": 1.0})
        self.assertEqual(stats["漲逾 1.5%"], {"bucket": "漲逾 1.5%", "samples": 1, "foreign_sell_rate": 0.0,
                                             "mean_open_gap_pct": 2.0, "mean_intraday_pct": 0.0})
        self.assertEqual(stats["±0.5% 內"]["samples"], 1)   # 11/09 分割那天沒算進來
        self.assertEqual(stats["±0.5% 內"]["mean_intraday_pct"], -1.0)
        self.assertEqual(stats["跌 0.5～1.5%"]["mean_open_gap_pct"], -0.5)
        self.assertEqual(stats["漲 0.5～1.5%"], {"bucket": "漲 0.5～1.5%", "samples": 0, "foreign_sell_rate": None,
                                               "mean_open_gap_pct": None, "mean_intraday_pct": None})
        self.assertEqual((reading["history"]["from"], reading["history"]["to"]), ("2026-11-03", "2026-11-06"))

    def test_morning_reading_uses_the_close_that_just_finished(self):
        reading = self.layer("morning", tw=tw_series({}, []))["withheld"]["nasdaq_reading"]
        self.assertEqual((reading["date"], reading["change_pct"], reading["bucket"]), ("2026-11-09", 3.85, "漲逾 1.5%"))


class DollarReadingTests(OverseasTestCase):
    def test_directions_and_long_run_correlation_with_foreign_twenty_day_total(self):
        # 台股 40 個交易日。美元指數（聯準會）在台股日子上的值 v，20 日變化 d20(t) = v(t)/v(t−20) − 1。
        # 外資每日淨額由程式外的方式構造成「近 20 日累計 = −1e11 × d20」，所以兩者完全負相關（−1）。
        days = weekdays("2026-09-01", "2026-10-26")[:40]
        values = [100 + (i % 7) * 0.3 + i * 0.05 for i in range(40)]
        d20 = {i: values[i] / values[i - 20] - 1 for i in range(20, 40)}
        flows = [1e8] * 20
        targets = {i: -1e11 * d20[i] for i in range(20, 40)}
        # F20(19) = 20 × 1e8；之後 f(t) = F20(t) − F20(t−1) + f(t−20)
        previous_total = sum(flows)
        for i in range(20, 40):
            flows.append(targets[i] - previous_total + flows[i - 20])
            previous_total = targets[i]
        foreign = dict(zip(days, flows))
        # 美元指數用前一天的日期發布（美國日期 < 台股日期），所以把它放在台股日期的前一天
        routes = default_routes()
        fed_rows = [((date.fromisoformat(d) - timedelta(days=1)).isoformat(), round(v, 6)) for d, v in zip(days, values)]
        routes["https://www.federalreserve.gov/"] = fed_html(fed_rows)
        # 台幣每天都貶一點：近 20 日美元兌台幣上升
        routes["https://cpx.cbc.gov.tw/"] = cbc_json([(d, 30 + i * 0.01) for i, d in enumerate(days)])
        folder, _, _ = self.fetch(routes, reference=days[-1])
        layer = oi.build_layer(folder, edition="evening", base_date=days[-1], tw=tw_series(foreign, []))
        reading = layer["prompt"]["dollar_and_foreign"]
        self.assertAlmostEqual(reading["history"]["correlation_dollar"], -1.0, places=6)
        self.assertEqual(reading["history"]["samples"], 20)
        self.assertEqual((reading["history"]["from"], reading["history"]["to"]), (days[20], days[-1]))
        last = d20[39]
        self.assertEqual(reading["dollar_index"]["direction"], "上升" if last > 0 else "下降")
        self.assertEqual(reading["usdtwd"]["direction"], "上升")
        self.assertEqual(reading["foreign_20d"]["direction"], "賣超" if targets[39] < 0 else "買超")
        # 美元走強且外資賣超（或美元走弱且外資買超）＝符合常態
        self.assertTrue(reading["dollar_index"]["matches_usual_pattern"])
        self.assertIn("除息季", " ".join(reading["notes"]))


class LoadTwSeriesTests(OverseasTestCase):
    def test_foreign_net_is_shares_times_close_summed_over_listed_stocks(self):
        stock, inst = self.tmp / "stock.sqlite", self.tmp / "inst.sqlite"
        con = sqlite3.connect(stock)
        con.execute("CREATE TABLE daily (date TEXT, code TEXT, open REAL, close REAL)")
        con.executemany("INSERT INTO daily VALUES (?,?,?,?)", [
            ("2026-11-06", "2330", 1000, 1010), ("2026-11-06", "2317", 200, 210), ("2026-11-06", "0050", 50, 51),
            ("2026-11-09", "2330", 1000, 1000), ("2026-11-09", "0050", 51, 52)])
        con.commit()
        con.close()
        con = sqlite3.connect(inst)
        con.execute("CREATE TABLE institutional (date TEXT, code TEXT, foreign_net INTEGER)")
        con.executemany("INSERT INTO institutional VALUES (?,?,?)", [
            ("2026-11-06", "2330", -1000), ("2026-11-06", "2317", 500), ("2026-11-09", "2330", 2000)])
        con.commit()
        con.close()
        tw = oi.load_tw_series(stock, inst, until="2026-11-06")
        # 11/06：−1000 × 1010 + 500 × 210 = −905,000；11/09 在截止日之後
        self.assertEqual(tw["foreign_net"], {"2026-11-06": -905000.0})
        self.assertEqual(tw["etf0050"], [{"date": "2026-11-06", "open": 50, "close": 51}])

    def test_missing_databases_give_nothing_instead_of_failing(self):
        self.assertIsNone(oi.load_tw_series(self.tmp / "no.sqlite", self.tmp / "no2.sqlite", until=BASE))


class ArchiveTests(OverseasTestCase):
    def test_originals_go_into_the_ledger_named_by_content_and_twice_is_the_same(self):
        folder, _, _ = self.fetch()
        ledger = self.tmp / "ledger"
        first = oi.archive_sources(folder, ledger)
        second = oi.archive_sources(folder, ledger)
        self.assertEqual(first, second)
        nasdaq = next(entry for entry in first if entry["name"] == "eodhd-IXIC.INDX")
        stored = ledger / nasdaq["archived_as"]
        self.assertTrue(nasdaq["archived_as"].startswith("sources/"))
        self.assertEqual(gzip.decompress(stored.read_bytes()), eodhd(NASDAQ))
        self.assertEqual(stored.name, hashlib.sha256(stored.read_bytes()).hexdigest() + ".raw.gz")
        self.assertEqual(nasdaq["sha256"], hashlib.sha256(eodhd(NASDAQ)).hexdigest())
        self.assertEqual(len(list((ledger / "sources").iterdir())), 9)


if __name__ == "__main__":
    unittest.main()
