"""外資讀數（G 票第 13、14 片）：給固定的每日資料，看讀數與歷史統計。預期值都是手算的（算式寫在旁邊）。"""
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import foreign_flow as ff


def weekdays(start, count):
    day, out = date.fromisoformat(start), []
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


def flow(values, tsmc=None, turnover=100.0):
    dates = weekdays("2026-07-01", len(values))
    tsmc = tsmc or [0.0] * len(values)
    return [{"date": d, "foreign": v, "foreign_tsmc": t, "turnover": turnover, "n_buy": 300, "n_sell": 400}
            for d, v, t in zip(dates, values, tsmc)]


# 九天：外資 [-5,-5,-5,+10,-5,-5,+10,-5,-5]，連續天數 [-1,-2,-3,+1,-1,-2,+1,-1,-2]；今天是連賣第 2 天。
SPOT = [-5, -5, -5, 10, -5, -5, 10, -5, -5]
TSMC = [-2, -2, -2, 5, -2, -8, 5, -2, -8]


class SpotReadingsTest(unittest.TestCase):
    def test_streak_continuation_and_cumulative_share(self):
        rows = flow(SPOT, TSMC)
        reading = ff.spot_readings(rows, rows[-1]["date"])["streak_and_cumulative"]
        self.assertEqual(reading["status"], "ok")
        self.assertEqual(reading["streak"], {"side": "sell", "days": 2})
        # 之前「剛好連賣 2 天」的日子：第 2 天（隔天續賣）、第 6 天（隔天買超）→ 1/2
        self.assertEqual(reading["history"]["next_day_same_side"]["exactly_n_days"],
                         {"probability_pct": 50.0, "samples": 2})
        # 「連賣 2 天以上」再加第 3 天（連賣第 3 天，隔天買超）→ 1/3
        self.assertEqual(reading["history"]["next_day_same_side"]["n_days_or_more"],
                         {"probability_pct": 33.33, "samples": 3})
        self.assertTrue(reading["history"]["next_day_same_side"]["small_sample"])
        # 近 5 日：-5-5+10-5-5 = -10 億，占成交金額 500 億的 -2%；不到 20 天就沒有 20 日累計
        self.assertEqual(reading["cumulative"]["5"], {"net_billion": -10.0, "share_of_turnover_pct": -2.0})
        self.assertEqual(reading["cumulative"]["20"], {"net_billion": None, "share_of_turnover_pct": None})
        self.assertEqual(reading["history"]["period"], {"from": "2026-07-01", "to": "2026-07-13", "days": 9})

    def test_cum20_percentile_uses_only_days_up_to_base(self):
        # 22 天：前 20 天每天 +1，最後兩天 -30。20 日累計占比：第 20 天 20/2000=1%、第 21 天 (19-30)/2000=-0.55%、
        # 第 22 天 (18-60)/2000=-2.1%。今天 -2.1% 在三個裡最低 → 小於等於它的占 1/3 → 33 分位
        rows = flow([1] * 20 + [-30, -30])
        reading = ff.spot_readings(rows, rows[-1]["date"])["streak_and_cumulative"]
        self.assertEqual(reading["cumulative"]["20"], {"net_billion": -42.0, "share_of_turnover_pct": -2.1})
        self.assertEqual(reading["history"]["cum20_percentile"], 33)
        # 基準日往前一天：只看得到前 21 天，今天 -0.55% 在 [1, -0.55] 裡 → 1/2 → 50
        earlier = ff.spot_readings(rows, rows[-2]["date"])["streak_and_cumulative"]
        self.assertEqual(earlier["history"]["cum20_percentile"], 50)

    def test_tsmc_split_and_long_run_shares(self):
        rows = flow(SPOT, TSMC)
        reading = ff.spot_readings(rows, rows[-1]["date"])["tsmc_split"]
        # 今天外資 -5、台積電 -8 → 其餘 +3
        self.assertEqual((reading["foreign_billion"], reading["tsmc_billion"], reading["rest_billion"]), (-5.0, -8.0, 3.0))
        self.assertEqual((reading["stocks_bought"], reading["stocks_sold"]), (300, 400))
        # 台積電絕對值 36 ÷ 外資絕對值 55 = 65.45%；賣超 7 天裡扣台積電是買超的有第 6、9 天 → 2/7 = 28.57%
        self.assertEqual(reading["history"]["tsmc_share_of_foreign_pct"], 65.45)
        self.assertEqual(reading["history"]["sell_days_but_rest_bought_pct"], 28.57)
        self.assertEqual(reading["history"]["sell_days"], 7)

    def test_flow_that_stops_before_base_date_is_missing(self):
        rows = flow(SPOT)
        out = ff.spot_readings(rows[:-1], rows[-1]["date"])
        self.assertEqual(out["streak_and_cumulative"]["status"], "missing")
        self.assertEqual(out["tsmc_split"]["status"], "missing")


def index(closes, start="2026-07-01"):
    return [{"date": d, "close": c} for d, c in zip(weekdays(start, len(closes)), closes)]


class TrendReadingTest(unittest.TestCase):
    def test_above_both_lines(self):
        rows = index(list(range(1, 201)))
        reading = ff.trend_reading(rows, rows[-1]["date"])
        # 年線 = (1+…+200)/200 = 100.5；季線 = (141+…+200)/60 = 170.5
        self.assertEqual(reading["year_line"], {"days": 200, "value": 100.5, "position": "above", "distance_pct": 99.0})
        self.assertEqual(reading["quarter_line"], {"days": 60, "value": 170.5, "position": "above",
                                                   "distance_pct": 17.3})

    def test_below_both_lines(self):
        rows = index(list(range(1, 200)) + [50])
        reading = ff.trend_reading(rows, rows[-1]["date"])
        # 年線 = (19900 + 50)/200 = 99.75；季線 = (141+…+199 + 50)/60 = 10080/60 = 168
        self.assertEqual(reading["year_line"]["position"], "below")
        self.assertEqual(reading["year_line"]["value"], 99.75)
        self.assertEqual(reading["year_line"]["distance_pct"], -49.87)
        self.assertEqual((reading["quarter_line"]["value"], reading["quarter_line"]["position"]), (168.0, "below"))

    def test_too_short_for_the_year_line(self):
        rows = index(list(range(1, 61)))
        reading = ff.trend_reading(rows, rows[-1]["date"])
        self.assertEqual(reading["year_line"], {"days": 200, "value": None, "position": None})
        self.assertEqual(reading["quarter_line"]["value"], 30.5)


# 十個交易日 2026-07-08～07-21；07-15 是七月第三個星期三（結算日，第 6 天）。
FUT_DATES = weekdays("2026-07-08", 10)
FUT_NET = [-100, -110, -130, -120, -150, -300, -310, -305, -340, -360]
FUT_CLOSE = [100, 101, 99, 100, 102, 101, 103, 104, 102, 105]
SMALL = {"rel_window": 3, "chg_window": 2, "forward": [1, 2], "mini_per_big": 4,
         "cuts": {"rel60": [50], "chg5": [50], "chg1": [50]}}


def positions(dates=FUT_DATES, nets=FUT_NET):
    return [{"date": d, "net": n} for d, n in zip(dates, nets)]


def fut_index(dates=FUT_DATES, closes=FUT_CLOSE):
    return [{"date": d, "close": c} for d, c in zip(dates, closes)]


class SettlementDaysTest(unittest.TestCase):
    def test_third_wednesday_and_holiday_rolls_to_next_trading_day(self):
        days = weekdays("2026-07-01", 70)
        self.assertEqual(sorted(ff.settlement_days(days)), ["2026-07-15", "2026-08-19", "2026-09-16"])
        without = [d for d in days if d != "2026-08-19"]
        self.assertIn("2026-08-20", ff.settlement_days(without))


class FuturesReadingTest(unittest.TestCase):
    def test_three_changes_with_bucket_statistics(self):
        reading = ff.futures_reading(positions(), fut_index(), "2026-07-21", SMALL)
        self.assertEqual(reading["status"], "ok")
        self.assertEqual(reading["net_contracts"], -360)
        self.assertFalse(reading["settlement_day_today"])
        # 相對近 3 日平均：-360 - (-310-305-340)/3 = -41.67 → -42 口
        self.assertEqual(reading["relative_to_60d_mean"]["contracts"], -42)
        # 近 2 日：-360 - (-305) = -55（07-20、07-21 都不是結算日）
        self.assertEqual(reading["change_5d"]["contracts"], -55)
        change = reading["change_1d"]
        self.assertEqual(change["contracts"], -20)
        # 當日增減的歷史（結算日 07-15 不算）：-10,-20,+10,-30,-10,+5,-35,-20；小於等於 -20 的 4/8 → 50 分位；
        # 中位切點（第 4 小）是 -20，所以落在「最低 50%」，同組是 07-10、07-14、07-20、07-21。
        history = change["history"]
        self.assertEqual((history["percentile"], history["bucket"], history["bucket_days"]), (50, "最低 50%", 4))
        # 之後 1 日：99→100 +1.0101%、102→101 -0.9804%、102→105 +2.9412%（07-21 還沒有）→ 平均 0.99%
        # 全部 9 天的之後 1 日平均 = 5.0187/9 = 0.56%
        self.assertEqual(history["after"]["1"], {"mean_pct": 0.99, "samples": 3, "all_days_mean_pct": 0.56})
        # 之後 2 日：99→102 +3.0303%、102→103 +0.9804% → 2.01%；全部 8 天 5.9816/8 = 0.75%
        self.assertEqual(history["after"]["2"], {"mean_pct": 2.01, "samples": 2, "all_days_mean_pct": 0.75})
        self.assertEqual(reading["history_period"], {"from": "2026-07-08", "to": "2026-07-21", "days": 10})

    def test_settlement_day_changes_are_not_applicable(self):
        on = ff.futures_reading(positions(), fut_index(), "2026-07-15", SMALL)
        self.assertTrue(on["settlement_day_today"])
        self.assertIsNone(on["change_1d"]["contracts"])
        self.assertIn("結算日", on["change_1d"]["not_applicable"])
        self.assertIsNone(on["change_5d"]["contracts"])
        after = ff.futures_reading(positions(), fut_index(), "2026-07-16", SMALL)
        self.assertEqual(after["change_1d"]["contracts"], -10)       # -310 - (-300)
        self.assertIsNone(after["change_5d"]["contracts"])            # 視窗 07-15～07-16 含結算日
        self.assertIn("結算日", after["change_5d"]["not_applicable"])

    def test_not_enough_days_for_the_average(self):
        reading = ff.futures_reading(positions(), fut_index(), "2026-07-10", SMALL)
        self.assertIsNone(reading["relative_to_60d_mean"]["contracts"])
        self.assertIn("不到", reading["relative_to_60d_mean"]["not_applicable"])

    def test_positions_without_the_base_day_are_missing(self):
        reading = ff.futures_reading(positions()[:-1], fut_index(), "2026-07-21", SMALL)
        self.assertEqual(reading, {"status": "missing", "reason": "期貨部位截止日不是基準日"})

    def test_a_gap_restarts_the_series_instead_of_bridging_it(self):
        # 少了 07-13（第 4 天）：只用 07-14 以後，07-21 的近 3 日平均改成 07-16～07-20
        rows = [row for row in positions() if row["date"] != "2026-07-13"]
        reading = ff.futures_reading(rows, fut_index(), "2026-07-21", SMALL)
        self.assertEqual(reading["history_period"]["from"], "2026-07-14")
        self.assertEqual(reading["relative_to_60d_mean"]["contracts"], -42)


# 那斯達克（美國日期）：07-02 跌 2%、07-06 漲 1.0204%、07-07 平盤；07-03 美股休市。
NASDAQ = [{"date": "2026-07-01", "close": 100}, {"date": "2026-07-02", "close": 98},
          {"date": "2026-07-06", "close": 99}, {"date": "2026-07-07", "close": 99}]
# 台股：07-03、07-06 前一個美股交易日都是 07-02（跌逾 1.5%）；07-07 配 07-06（漲 0.5～1.5%）；07-08 配 07-07（±0.5% 內）
TW_FLOW = [{"date": d, "foreign": f, "foreign_tsmc": 0, "turnover": 100, "n_buy": 1, "n_sell": 1}
           for d, f in (("2026-07-02", 1), ("2026-07-03", -10), ("2026-07-06", 5), ("2026-07-07", -3), ("2026-07-08", 2))]
PROXY = [{"date": "2026-07-03", "open": 49, "close": 49.49, "reference": 50},   # 跳空 -2%、盤中 +1%
         {"date": "2026-07-06", "open": 50, "close": 50, "reference": 50.5},    # 跳空 -0.9901%、盤中 0
         {"date": "2026-07-07", "open": 51, "close": 51, "reference": 50},      # 跳空 +2%、盤中 0
         {"date": "2026-07-08", "open": 51, "close": 52, "reference": None}]    # 除權息之類沒有參考價：不算價格


class NasdaqReadingTest(unittest.TestCase):
    def test_ranges_use_the_etf_open_against_its_reference_price(self):
        reading = ff.nasdaq_reading(NASDAQ, PROXY, TW_FLOW, "2026-07-08")
        self.assertEqual((reading["us_close_date"], reading["change_pct"], reading["range"]), ("2026-07-07", 0.0, "±0.5% 內"))
        table = {row["range"]: row for row in reading["history"]["next_taiwan_day_by_range"]}
        # 跌逾 1.5%：07-03 外資賣、07-06 買 → 50%；跳空 (-2 - 0.9901)/2 = -1.5%；盤中 (1 + 0)/2 = 0.5%
        self.assertEqual(table["跌逾 1.5%"], {"range": "跌逾 1.5%", "foreign_sell_pct": 50.0, "foreign_days": 2,
                                           "open_gap_mean_pct": -1.5, "intraday_mean_pct": 0.5, "price_days": 2})
        self.assertEqual((table["漲 0.5～1.5%"]["foreign_sell_pct"], table["漲 0.5～1.5%"]["open_gap_mean_pct"]), (100.0, 2.0))
        self.assertEqual((table["±0.5% 內"]["foreign_sell_pct"], table["±0.5% 內"]["open_gap_mean_pct"],
                          table["±0.5% 內"]["price_days"]), (0.0, None, 0))
        self.assertEqual(table["漲逾 1.5%"]["foreign_days"], 0)

    def test_evening_sees_the_previous_night_and_morning_sees_the_same_night(self):
        evening = ff.nasdaq_reading(NASDAQ, PROXY, TW_FLOW, "2026-07-07", "evening")
        morning = ff.nasdaq_reading(NASDAQ, PROXY, TW_FLOW, "2026-07-07", "morning")
        self.assertEqual((evening["us_close_date"], evening["range"]), ("2026-07-06", "漲 0.5～1.5%"))
        self.assertEqual((morning["us_close_date"], morning["range"]), ("2026-07-07", "±0.5% 內"))

    def test_without_nasdaq_it_is_missing(self):
        self.assertEqual(ff.nasdaq_reading([], PROXY, TW_FLOW, "2026-07-08")["status"], "missing")


# 24 個台股交易日：美元兌台幣前 20 天 100，之後 101、102、103、104；外資前 20 天 0，之後每天 -10 億（成交金額 100 億）。
FX_DATES = weekdays("2026-07-01", 24)
FX = [{"date": d, "close": 100 if i < 20 else 81 + i} for i, d in enumerate(FX_DATES)]
FX_FLOW = [{"date": d, "foreign": 0 if i < 20 else -10, "foreign_tsmc": 0, "turnover": 100, "n_buy": 1, "n_sell": 1}
           for i, d in enumerate(FX_DATES)]


class DollarReadingTest(unittest.TestCase):
    def test_twd_change_direction_and_correlation(self):
        reading = ff.dollar_reading([], FX, FX_FLOW, FX_DATES[-1])
        self.assertEqual(reading["status"], "ok")
        self.assertEqual(reading["foreign_20d_net_billion"], -40.0)
        self.assertTrue(reading["dividend_season"])          # 基準日 08-03
        self.assertEqual(reading["dollar_index"]["status"], "missing")
        twd = reading["usd_twd"]
        # 104 ÷ 100 - 1 = 4%：美元兌台幣上升（台幣貶）、外資賣超 → 照常見關係
        self.assertEqual((twd["as_of"], twd["change_20d_pct"], twd["direction"], twd["matches_usual_relation"]),
                         (FX_DATES[-1], 4.0, "up", True))
        # 20 日變化 1、2、3、4% 對外資 20 日累計 -0.5、-1、-1.5、-2% → 完全反向
        self.assertEqual(twd["history"], {"correlation_with_foreign_20d": -1.0, "samples": 4})

    def test_us_dated_dollar_index_is_one_night_behind_in_the_evening(self):
        reading = ff.dollar_reading(FX, [], FX_FLOW, FX_DATES[-1])
        dollar = reading["dollar_index"]
        # 晚間版看不到美國日期等於基準日的那一筆：103 ÷ 100 - 1 = 3%；相關少一組
        self.assertEqual((dollar["as_of"], dollar["change_20d_pct"]), (FX_DATES[-2], 3.0))
        self.assertEqual(dollar["history"], {"correlation_with_foreign_20d": -1.0, "samples": 3})

    def test_both_missing(self):
        self.assertEqual(ff.dollar_reading([], [], FX_FLOW, FX_DATES[-1])["status"], "missing")

class BuildLayerTest(unittest.TestCase):
    def test_layer_marks_missing_readings_and_carries_the_notes(self):
        rows = flow(SPOT, TSMC)
        base = rows[-1]["date"]
        layer = ff.build_layer(base_date=base, index_daily=index([100] * 9), daily_flow=rows, futures_positions=[])
        self.assertEqual(layer["base_date"], base)
        self.assertEqual(layer["missing"], ["nasdaq_last_session", "dollar_and_twd_vs_foreign", "futures_net_position"])
        self.assertEqual(layer["readings"]["streak_and_cumulative"]["streak"], {"side": "sell", "days": 2})
        self.assertIn("不代表之後會續漲或續跌", layer["notes"]["spot"])
        self.assertIn("空單多不代表看空", layer["notes"]["futures"])
        self.assertIn("只有近三年", layer["notes"]["futures"])
        other = ff.build_layer(base_date=rows[-2]["date"], index_daily=index([100] * 8), daily_flow=rows[:-1],
                               futures_positions=[])
        self.assertEqual(layer["version"], other["version"])


class DailyFlowTest(unittest.TestCase):
    def test_shares_times_close_from_the_two_databases(self):
        with tempfile.TemporaryDirectory() as tmp:
            inst, stock = Path(tmp) / "inst.sqlite", Path(tmp) / "stock.sqlite"
            con = sqlite3.connect(inst)
            con.execute("CREATE TABLE institutional (date TEXT, code TEXT, foreign_net INTEGER)")
            con.executemany("INSERT INTO institutional VALUES (?,?,?)", [
                ("2026-11-13", "2330", -2_000_000), ("2026-11-13", "2317", 5_000_000),
                ("2026-11-13", "9999", 1_000_000),       # 日線沒有這檔：算不出金額，不計
                ("2026-11-16", "2330", 100_000)])        # 日線還沒到的日子：不算
            con.commit(); con.close()
            con = sqlite3.connect(stock)
            con.execute("CREATE TABLE daily (date TEXT, code TEXT, close REAL, turnover INTEGER)")
            con.executemany("INSERT INTO daily VALUES (?,?,?,?)", [
                ("2026-11-13", "2330", 1000.0, 60_000_000_000), ("2026-11-13", "2317", 200.0, 20_000_000_000),
                ("2026-11-13", "0050", 50.0, 20_000_000_000)])
            con.commit(); con.close()
            rows = ff.daily_flow_from_sources(inst, stock)
        # 2330：-200 萬股 × 1000 = -20 億；2317：+500 萬股 × 200 = +10 億；合計 -10 億；成交金額 1000 億
        self.assertEqual(rows, [{"date": "2026-11-13", "foreign": -10.0, "foreign_tsmc": -20.0, "turnover": 1000.0,
                                 "n_buy": 1, "n_sell": 1}])


def make_dbs(folder, inst_rows, stock_rows):
    inst, stock = Path(folder) / "inst.sqlite", Path(folder) / "stock.sqlite"
    con = sqlite3.connect(inst)
    con.execute("CREATE TABLE institutional (date TEXT, code TEXT, foreign_net INTEGER)")
    con.executemany("INSERT INTO institutional VALUES (?,?,?)", inst_rows)
    con.commit(); con.close()
    con = sqlite3.connect(stock)
    con.execute("CREATE TABLE daily (date TEXT, code TEXT, close REAL, turnover INTEGER)")
    con.executemany("INSERT INTO daily VALUES (?,?,?,?)", stock_rows)
    con.commit(); con.close()
    return inst, stock


class SeedAndT86Test(unittest.TestCase):
    def test_old_single_column_and_new_split_columns(self):
        old = {"stat": "OK", "fields": ["證券代號", "證券名稱", "外資買賣超股數"], "data": [["2330", "台積電", "-1,000"]]}
        new = {"stat": "OK", "fields": ["證券代號 ", "證券名稱", "外陸資買賣超股數(不含外資自營商)", "外資自營商買賣超股數"],
               "data": [["2330 ", "台積電", "-1,000", "200"], ["2317", "鴻海", "--", "5"]]}
        self.assertEqual(ff.t86_foreign_shares(old), {"2330": -1000.0})
        self.assertEqual(ff.t86_foreign_shares(new), {"2330": -800.0, "2317": 5.0})

    def test_seed_then_database_days_after_it(self):
        import gzip
        import json
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "raw"
            raw.mkdir()
            payload = {"stat": "OK", "fields": ["證券代號", "外資買賣超股數"], "data": [["2330", "1,000,000"]]}
            (raw / "20251024.json.gz").write_bytes(gzip.compress(json.dumps(payload).encode("utf-8")))
            (raw / "20251023.json.gz").write_bytes(gzip.compress(json.dumps(payload).encode("utf-8")))  # 日線沒有這天
            inst, stock = make_dbs(tmp, [("2025-10-24", "2330", 9), ("2025-10-27", "2330", -3_000_000)], [
                ("2025-10-24", "2330", 1000.0, 50_000_000_000), ("2025-10-27", "2330", 1000.0, 40_000_000_000)])
            seed = Path(tmp) / "seed.json"
            summary = ff.build_flow_seed(raw, stock, seed)
            rows = ff.load_daily_flow(inst, stock, seed=seed)
            capped = ff.load_daily_flow(inst, stock, seed=seed, until="2025-10-24")
        self.assertEqual(summary, {"days": 1, "from": "2025-10-24", "to": "2025-10-24", "skipped": ["20251023.json.gz"]})
        # 10-24 用起始資料（100 萬股 × 1000 = 10 億，不是資料庫裡的 9 股）；10-27 從資料庫：-300 萬 × 1000 = -30 億
        self.assertEqual([(row["date"], row["foreign"]) for row in rows], [("2025-10-24", 10.0), ("2025-10-27", -30.0)])
        self.assertEqual([row["date"] for row in capped], ["2025-10-24"])

if __name__ == "__main__":
    unittest.main()
