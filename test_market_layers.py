"""I 票：大盤事前預測多的五層（大盤融資融券、櫃買指數、產業成交比重、選股快照與資金輪動、OFR 金融壓力）。

預期值都是手算的固定數字（算式寫在旁邊）。"""
import unittest
from datetime import date, timedelta
from pathlib import Path

import market_layers as ml


def weekdays(count, start="2026-03-02"):
    day, out = date.fromisoformat(start), []
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


D = weekdays(70)


def margin_rows(balances, margin_units=None, short_units=None):
    margin_units = margin_units or [2_000_000] * len(balances)
    short_units = short_units or [60_000] * len(balances)
    return [{"date": D[i], "margin_balance_billion": b, "margin_units": m, "short_units": s, "index_close": 10000.0}
            for i, (b, m, s) in enumerate(zip(balances, margin_units, short_units))]


class MarginLayerTest(unittest.TestCase):
    def test_balance_changes_and_short_to_margin_ratio(self):
        rows = margin_rows([1000.0, 1010.0, 1005.0, 1020.0, 1030.0, 1040.0, 1050.5],
                           margin_units=[2_000_000] * 6 + [2_100_000],
                           short_units=[60_000] * 6 + [63_000])
        layer = ml.margin_layer(rows, D[6])
        self.assertEqual(layer["status"], "ok")
        self.assertEqual(layer["data_date"], D[6])
        self.assertTrue(layer["same_day_as_base"])
        self.assertEqual(layer["margin_balance_billion"], 1050.5)
        self.assertEqual(layer["change_1d_billion"], 10.5)          # 1050.5 − 1040
        self.assertEqual(layer["change_5d_billion"], 40.5)          # 1050.5 − 1010（往前第 5 筆）
        self.assertEqual(layer["change_5d_pct"], 4.01)              # 40.5 ÷ 1010 ＝ 4.0099%
        self.assertEqual(layer["short_units"], 63_000)
        self.assertEqual(layer["short_change_5d_units"], 3_000)     # 63,000 − 60,000
        self.assertEqual(layer["short_to_margin_pct"], 3.0)         # 63,000 ÷ 2,100,000
        self.assertIsNone(layer["maintenance_ratio"])               # 大盤維持率沒有來源，不給
        self.assertEqual(layer["compared_with"], D[1])
        self.assertIn("note", layer)

    def test_rows_after_the_base_date_are_not_seen_and_a_lag_is_marked(self):
        rows = margin_rows([1000.0, 1001.0, 1002.0])
        layer = ml.margin_layer(rows, D[3])          # 基準日 D[3] 還沒有融資資料，只看到 D[2]
        self.assertEqual(layer["data_date"], D[2])
        self.assertFalse(layer["same_day_as_base"])
        self.assertIsNone(layer["change_5d_billion"])  # 不足 6 筆
        self.assertEqual(ml.margin_layer(rows, D[1])["margin_balance_billion"], 1001.0)

    def test_no_rows_is_missing(self):
        self.assertEqual(ml.margin_layer([], D[0])["status"], "missing")


def index_rows(closes, offset=0):
    return [{"date": D[offset + i], "open": c, "high": c, "low": c, "close": c} for i, c in enumerate(closes)]


class OtcLayerTest(unittest.TestCase):
    def setUp(self):
        # 櫃買 61 根：第 0 根 250，第 1～55 根 200，第 56～60 根 204、208、212、216、220
        self.otc = index_rows([250.0] + [200.0] * 55 + [204.0, 208.0, 212.0, 216.0, 220.0])
        # 加權 61 根：第 0 根 8000，中間 10000，最後一根 10500
        self.taiex = index_rows([8000.0] + [10000.0] * 59 + [10500.0])

    def test_relative_strength_against_the_taiex(self):
        layer = ml.otc_layer(self.otc, self.taiex, D[60])
        self.assertEqual(layer["status"], "ok")
        self.assertEqual(layer["data_date"], D[60])
        rows = {row["days"]: row for row in layer["versus_taiex"]}
        # 5 日：220 ÷ 200 − 1 ＝ 10%；加權 10500 ÷ 10000 − 1 ＝ 5%；差 5 個百分點
        self.assertEqual(rows[5], {"days": 5, "since": D[55], "otc_pct": 10.0, "taiex_pct": 5.0, "gap_pp": 5.0})
        self.assertEqual(rows[20]["gap_pp"], 5.0)
        # 60 日：220 ÷ 250 − 1 ＝ −12%；加權 10500 ÷ 8000 − 1 ＝ 31.25%；差 −43.25
        self.assertEqual(rows[60], {"days": 60, "since": D[0], "otc_pct": -12.0, "taiex_pct": 31.25, "gap_pp": -43.25})
        self.assertEqual(len(layer["recent"]), 60)
        self.assertEqual(layer["recent"][-1], {"date": D[60], "open": 220.0, "high": 220.0, "low": 220.0, "close": 220.0})
        self.assertEqual(layer["recent"][0]["date"], D[1])

    def test_a_row_is_left_out_when_the_taiex_lacks_that_day(self):
        taiex = [row for row in self.taiex if row["date"] != D[55]]
        layer = ml.otc_layer(self.otc, taiex, D[60])
        self.assertEqual([row["days"] for row in layer["versus_taiex"]], [20, 60])

    def test_base_date_without_an_otc_bar_is_missing(self):
        self.assertEqual(ml.otc_layer(self.otc[:60], self.taiex, D[60])["status"], "missing")


def rotation_batch(data_date, days):
    """days：[(日期, {產業: 當日成交額（億元）, ...}, {產業: 象限})]。"""
    return {"kind": "rotation", "data_date": data_date, "result": {
        "quadrants": {"leading": {"label": "資金領先"}, "weakening": {"label": "資金減速"},
                      "improving": {"label": "資金回補"}, "lagging": {"label": "資金落後"}},
        "snapshots": [{"date": day, "bubbles": [{"name": name, "amount_today": amount,
                                                 "quadrant": (quadrants or {}).get(name, "lagging")}
                                                for name, amount in amounts.items()],
                       "summary": {"text": "測試摘要"},
                       "recovery_signals": [{"name": "乙業"}]}
                      for day, amounts, quadrants in days]}}


class SectorShareLayerTest(unittest.TestCase):
    def test_share_today_and_change_against_five_days_earlier(self):
        days = [(D[0], {"甲業": 200.0, "乙業": 200.0}, None)] + \
               [(D[i], {"甲業": 250.0, "乙業": 250.0}, None) for i in range(1, 5)] + \
               [(D[5], {"甲業": 300.0, "乙業": 100.0}, None)]
        layer = ml.sector_share_layer(rotation_batch(D[5], days), D[5])
        self.assertEqual(layer["status"], "ok")
        self.assertEqual(layer["data_date"], D[5])
        self.assertEqual(layer["compared_with"], D[0])
        self.assertEqual(layer["total_amount_100m"], 400.0)
        # 甲：300 ÷ 400 ＝ 75%，5 日前 200 ÷ 400 ＝ 50%，差 +25 個百分點；乙相反
        self.assertEqual(layer["industries"], [
            {"name": "甲業", "share_pct": 75.0, "share_5d_ago_pct": 50.0, "change_pp": 25.0, "amount_100m": 300.0},
            {"name": "乙業", "share_pct": 25.0, "share_5d_ago_pct": 50.0, "change_pp": -25.0, "amount_100m": 100.0},
        ])

    def test_a_snapshot_of_another_day_is_missing(self):
        batch = rotation_batch(D[4], [(D[4], {"甲業": 1.0}, None)])
        self.assertEqual(ml.sector_share_layer(batch, D[5])["status"], "missing")
        self.assertEqual(ml.sector_share_layer(None, D[5])["status"], "missing")


def screen_batch(kind, data_date, names):
    return {"kind": kind, "data_date": data_date, "result": {
        "display_name": kind, "state": "available", "total": 212,
        "items": [{"code": f"{1000 + i}", "name": name, "security_type": "普通股", "metric_lots": 1000.4 - i,
                   "change_pct": 1.234, "close": 50.0, "streak_days": 13 - i} for i, name in enumerate(names)]}}


class ScreensLayerTest(unittest.TestCase):
    def test_rotation_quadrants_and_top_five_of_each_rank(self):
        rotation = rotation_batch(D[5], [(D[5], {"甲業": 300.0, "乙業": 100.0, "丙業": 50.0},
                                          {"甲業": "leading", "乙業": "improving", "丙業": "improving"})])
        screens = {kind: screen_batch(kind, D[5], ["一", "二", "三", "四", "五", "六"]) for kind in ml.SCREEN_KINDS}
        layer = ml.screens_layer(rotation, screens, D[5])
        self.assertEqual(layer["status"], "ok")
        self.assertEqual(layer["rotation"]["quadrants"], {"資金領先": ["甲業"], "資金減速": [], "資金回補": ["乙業", "丙業"],
                                                          "資金落後": []})
        self.assertEqual(layer["rotation"]["summary"], "測試摘要")
        self.assertEqual(layer["rotation"]["recovery_signals"], ["乙業"])
        ranks = {rank["kind"]: rank for rank in layer["ranks"]}
        self.assertEqual(set(ranks), set(ml.SCREEN_KINDS))
        self.assertEqual(ranks["foreign_streak-1d-buy"]["top"][0]["streak_days"], 13)
        self.assertEqual(ranks["rotation_relative_value-1d-buy"]["total"], 212)
        top = ranks["foreign_net_rank-1d-buy"]["top"]
        self.assertEqual(len(top), 5)
        self.assertEqual(top[0], {"code": "1000", "name": "一", "security_type": "普通股", "lots": 1000,
                                  "streak_days": 13, "change_pct": 1.23})

    def test_a_rank_of_another_day_is_left_out(self):
        rotation = rotation_batch(D[5], [(D[5], {"甲業": 1.0}, None)])
        screens = {"foreign_net_rank-1d-buy": screen_batch("foreign_net_rank-1d-buy", D[4], ["一"])}
        layer = ml.screens_layer(rotation, screens, D[5])
        self.assertEqual(layer["ranks"], [])
        self.assertEqual(layer["status"], "ok")

    def test_nothing_for_the_base_date_is_missing(self):
        self.assertEqual(ml.screens_layer(None, {}, D[5])["status"], "missing")


class OfrLayerTest(unittest.TestCase):
    def rows(self, count):
        start = date(2026, 1, 1)
        return [{"date": (start + timedelta(days=i)).isoformat(), "credit": float(i) / 10} for i in range(count)]

    def test_value_and_twenty_day_change_up_to_the_cutoff(self):
        rows = self.rows(30)                       # 0.0, 0.1, … 2.9
        layer = ml.ofr_layer(rows, "2026-01-22")   # 截到第 21 筆（2.1）
        self.assertEqual(layer["status"], "ok")
        self.assertEqual(layer["observed_at"], "2026-01-22")
        self.assertEqual(layer["value"], 2.1)
        self.assertEqual(layer["change_20d"], 2.0)    # 2.1 − 0.1
        self.assertIsNone(layer["percentile_3y"])     # 歷史不到 600 筆不給分位

    def test_too_short_is_missing(self):
        self.assertEqual(ml.ofr_layer(self.rows(10), "2026-12-31")["status"], "missing")

    def test_a_stale_series_is_missing(self):
        # 最後一筆 2026-01-30；截止日 2026-02-07 往前 7 天是 01-31 → 太舊
        layer = ml.ofr_layer(self.rows(30), "2026-02-07")
        self.assertEqual(layer["status"], "missing")
        self.assertEqual(ml.ofr_layer(self.rows(30), "2026-02-06")["status"], "ok")   # 往前 7 天是 01-30，還算


def ofr_csv(last_day, count=60):
    """假的 OFR 官方 CSV：從 last_day 往回 count 個日曆天，每天一筆，Credit 值等於序號 ÷ 10（最舊 0.0）。"""
    end = date.fromisoformat(last_day)
    days = [end - timedelta(days=count - 1 - i) for i in range(count)]
    lines = ["Date,OFR FSI,Credit,Equity valuation"] + [f"{d.isoformat()},0,{i / 10},0" for i, d in enumerate(days)]
    return ("\n".join(lines) + "\n").encode("utf-8")


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class OfrFetchTests(unittest.TestCase):
    """本人 2026-11-18 晚「預測時直接抓（推薦）」：寫預測之前向 OFR 官方抓 FSI，設逾時；抓不到那一層寫缺。"""

    def test_fetch_asks_the_official_csv_with_a_timeout_and_parses_the_credit_series(self):
        asked = []

        def opener(request, timeout):
            asked.append((request.full_url, timeout, request.get_header("User-agent")))
            return FakeResponse(ofr_csv("2026-02-06"))

        rows = ml.fetch_ofr(opener=opener, timeout=12)
        self.assertEqual(asked[0][:2], ("https://www.financialresearch.gov/financial-stress-index/data/fsi.csv", 12))
        self.assertNotIn("@", asked[0][2] or "")   # 不帶任何個人資料
        self.assertEqual(rows[-1], {"date": "2026-02-06", "credit": 5.9})

    def test_the_series_is_cut_three_weekdays_before_the_run_day(self):
        # 實測：11-18（週三）台北晚間抓到的最後一筆是 11-13（週五），週一、週二還沒公布：
        # 所以只用「寫預測那天往前第三個平日（含）以前」的分數。晚間版以基準日算、晨間版以開盤日算。
        sources = ml.load_sources(None, None, base_date="2026-02-06", ofr=lambda: ml.parse_ofr(ofr_csv("2026-02-06")))
        evening = ml.build_layers(sources, base_date="2026-02-06", index_daily=[], edition="evening")
        self.assertEqual(evening["ofr_stress"]["observed_at"], "2026-02-03")   # 週五往前三個平日是週二
        self.assertEqual(evening["ofr_stress"]["value"], 5.6)                   # 02-03 是第 56 筆（0 起算）
        morning = ml.build_layers(sources, base_date="2026-02-06", index_daily=[], edition="morning",
                                  session_date="2026-02-09")
        self.assertEqual(morning["ofr_stress"]["observed_at"], "2026-02-04")   # 週一開盤日往前三個平日是上週三

    def test_a_timeout_makes_the_layer_missing_with_its_error_kind(self):
        def timed_out():
            raise TimeoutError("timed out")

        sources = ml.load_sources(None, None, base_date="2026-02-06", ofr=timed_out)
        self.assertEqual(sources["errors"]["ofr_stress"], "TimeoutError")
        layer = ml.build_layers(sources, base_date="2026-02-06", index_daily=[], edition="evening")["ofr_stress"]
        self.assertEqual(layer["status"], "missing")
        self.assertIn("TimeoutError", layer["reason"])

    def test_without_a_fetcher_the_layer_is_missing(self):
        sources = ml.load_sources(None, None, base_date="2026-02-06")
        self.assertEqual(ml.build_layers(sources, base_date="2026-02-06", index_daily=[],
                                         edition="evening")["ofr_stress"]["status"], "missing")


class VersionTest(unittest.TestCase):
    def test_the_notes_given_to_the_model_are_part_of_the_versioned_parameters(self):
        self.assertEqual(ml.PARAMETERS["notes"], ml.NOTES)


class BuildLayersTest(unittest.TestCase):
    def test_every_layer_present_and_missing_ones_listed(self):
        layers = ml.build_layers({}, base_date=D[5], index_daily=index_rows([10000.0] * 6), edition="evening")
        self.assertEqual(set(layers), set(ml.LAYER_NAMES))
        self.assertTrue(all(layers[name]["status"] == "missing" for name in ml.LAYER_NAMES))

    def test_evening_ofr_cutoff_is_three_weekdays_before_the_base_date_and_morning_before_the_session(self):
        start = date(2026, 1, 1)
        rows = [{"date": (start + timedelta(days=i)).isoformat(), "credit": float(i)} for i in range(40)]
        evening = ml.build_layers({"ofr_credit": rows}, base_date="2026-02-05", index_daily=[], edition="evening")
        self.assertEqual(evening["ofr_stress"]["observed_at"], "2026-02-02")   # 週四往前三個平日是週一
        morning = ml.build_layers({"ofr_credit": rows}, base_date="2026-02-05", index_daily=[], edition="morning",
                                  session_date="2026-02-06")
        self.assertEqual(morning["ofr_stress"]["observed_at"], "2026-02-03")   # 開盤日週五往前三個平日是週二

if __name__ == "__main__":
    unittest.main()
