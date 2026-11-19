"""各產業強勢股（本人 2026-11-20 定案）：近 3 個交易日累計漲幅最大、每天成交金額夠大、只列有漲的、漲停加記號。

預期值都是手算的固定數字，不照程式的算法再算一次。
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import sector_rotation as sr

DATES = ["2026-11-05", "2026-11-06", "2026-11-09"]
WAN = 10_000


def _days(*days: tuple[float | None, float | None, int | None], name: str = "", suspended: int = 0) -> dict:
    """三個交易日各一筆 (收盤, 漲跌, 成交金額)，由舊到新。"""
    return {day: {"name": name, "close": close, "change": change, "turnover": turnover, "suspended": suspended}
            for day, (close, change, turnover) in zip(DATES, days)}


class StrongStocksTest(unittest.TestCase):
    def test_ranks_members_by_three_day_compounded_gain(self):
        quotes = {
            # 前一日 100 → 102（+2%）→ 107.1（+5%）→ 117.81（+10%）：1.02×1.05×1.10＝1.1781
            "1001": _days((102, 2, 6000 * WAN), (107.1, 5.1, 6000 * WAN), (117.81, 10.71, 6000 * WAN), name="甲一"),
            # 前一日 100 → 101（+1%）→ 101（0%）→ 103.02（+2%）：1.01×1.02＝1.0302
            "1002": _days((101, 1, 9000 * WAN), (101, 0, 9000 * WAN), (103.02, 2.02, 9000 * WAN), name="甲二"),
        }
        result = sr.strong_stocks({"甲業": ["1002", "1001"]}, quotes, DATES)
        self.assertEqual(result["date"], "2026-11-09")
        self.assertEqual(result["groups"]["甲業"], [
            {"code": "1001", "name": "甲一", "gain_pct": 17.8, "today_pct": 10.0, "limit_up": True},
            {"code": "1002", "name": "甲二", "gain_pct": 3.0, "today_pct": 2.0, "limit_up": False},
        ])

    def test_needs_fifty_million_average_daily_turnover_over_the_three_days(self):
        up = ((101, 1), (102.01, 1.01), (103.0301, 1.0201))   # 每天 +1%
        quotes = {
            # 4,000 萬＋5,000 萬＋6,000 萬，平均剛好 5,000 萬：列入
            "2001": _days(*[(c, ch, t * WAN) for (c, ch), t in zip(up, (4000, 5000, 6000))], name="剛好"),
            # 三天合計少 3 元，平均 49,999,999 元：不列
            "2002": _days(*[(c, ch, t) for (c, ch), t in zip(up, (5000 * WAN, 5000 * WAN, 5000 * WAN - 3))], name="差一點"),
        }
        result = sr.strong_stocks({"乙業": ["2001", "2002"]}, quotes, DATES)
        self.assertEqual([row["code"] for row in result["groups"]["乙業"]], ["2001"])

    def test_lists_only_members_that_rose_over_the_three_days(self):
        money = 8000 * WAN
        quotes = {
            # +1%、−1%、0%：1.01×0.99＝0.9999，累計小跌 → 不列
            "3001": _days((101, 1, money), (99.99, -1.01, money), (99.99, 0, money), name="小跌"),
            # 三天都平盤：累計 0% → 不列
            "3002": _days((50, 0, money), (50, 0, money), (50, 0, money), name="沒動"),
            # −2%、+1%、+2%：0.98×1.01×1.02＝1.009596 → +0.96%，畫面寫到小數一位是 +1.0% → 列
            "3003": _days((98, -2, money), (98.98, 0.98, money), (100.9596, 1.9796, money), name="回升"),
            # 只有第一天 +0.04%（2500 → 2501）：畫面上會是 +0.0%，等於沒漲 → 不列
            "3004": _days((2501, 1, money), (2501, 0, money), (2501, 0, money), name="幾乎沒動"),
        }
        result = sr.strong_stocks({"丙業": ["3001", "3002", "3003", "3004"], "丁業": ["3001"]}, quotes, DATES)
        self.assertEqual(result["groups"]["丙業"],
                         [{"code": "3003", "name": "回升", "gain_pct": 1.0, "today_pct": 2.0, "limit_up": False}])
        self.assertEqual(result["groups"]["丁業"], [])   # 整個產業都沒漲：留一個空清單，畫面才知道要寫「沒有上漲的股票」

    def test_keeps_three_per_industry_and_breaks_ties_by_turnover(self):
        def rising(first_pct: int, money: int, name: str) -> dict:
            # 第一天漲 first_pct%，後兩天平盤：累計就是 first_pct%
            close = 100 + first_pct
            return _days((close, first_pct, money), (close, 0, money), (close, 0, money), name=name)
        quotes = {
            "4001": rising(3, 6000 * WAN, "三趴小量"),
            "4002": rising(5, 6000 * WAN, "五趴"),
            "4003": rising(3, 9000 * WAN, "三趴大量"),
            "4004": rising(2, 9000 * WAN, "兩趴"),
            "4005": rising(8, 6000 * WAN, "八趴"),
        }
        result = sr.strong_stocks({"戊業": ["4001", "4002", "4003", "4004", "4005"]}, quotes, DATES)
        # 8% → 5% → 兩檔 3% 取成交金額大的那一檔；第四名以後不列
        self.assertEqual([(row["code"], row["gain_pct"]) for row in result["groups"]["戊業"]],
                         [("4005", 8.0), ("4002", 5.0), ("4003", 3.0)])

    def test_marks_limit_up_when_today_rose_nine_and_a_half_percent_or_more(self):
        money = 8000 * WAN
        quotes = {
            # 前一日 200 → 219：19 ÷ 200＝剛好 9.5%
            "5001": _days((200, 0, money), (200, 0, money), (219, 19, money), name="剛好漲停"),
            # 前一日 200 → 218.92：18.92 ÷ 200＝9.46%（畫面取到小數一位也是 +9.5%，但還沒到漲停）
            "5002": _days((200, 0, money), (200, 0, money), (218.92, 18.92, money), name="差一點"),
            # 前一日 6.00 → 6.57：0.57 ÷ 6＝9.5%，電腦的小數算出來是 0.0949999…，不能因此漏標
            "5003": _days((6, 0, money), (6, 0, money), (6.57, 0.57, money), name="低價漲停"),
        }
        result = sr.strong_stocks({"己業": ["5001", "5002", "5003"]}, quotes, DATES)
        self.assertEqual({row["code"]: (row["today_pct"], row["limit_up"]) for row in result["groups"]["己業"]},
                         {"5001": (9.5, True), "5002": (9.5, False), "5003": (9.5, True)})

    def test_skips_members_without_three_full_trading_days(self):
        money = 8000 * WAN
        up = ((101, 1, money), (102.01, 1.01, money), (103.0301, 1.0201, money))   # 每天 +1%
        quotes = {
            "6001": _days(*up, name="完整"),
            "6002": {day: quote for day, quote in _days(*up, name="剛上市").items() if day != DATES[0]},   # 少第一天
            "6003": _days(*up, name="停牌", suspended=1),
            "6004": _days(up[0], (None, None, 0), up[2], name="中間沒成交"),
            "6005": _days(up[0], up[1], (103.0301, 1.0201, None), name="沒有成交金額"),
            # "6006"：名錄裡有、行情庫裡完全沒有
            "6007": _days(up[0], (5, 5, money), up[2], name="沒有參考價"),   # 收盤 5、漲跌 5：前一日參考價是 0，算不出漲跌幅
        }
        result = sr.strong_stocks({"庚業": ["6001", "6002", "6003", "6004", "6005", "6006", "6007"]}, quotes, DATES)
        self.assertEqual([row["code"] for row in result["groups"]["庚業"]], ["6001"])

    def test_a_day_without_an_official_change_counts_as_flat(self):
        money = 8000 * WAN
        # 除權息當天官方不公布漲跌（漲跌是空的、收盤有值）：那天當 0%，不讓股票消失、也不把息值算成下跌
        quotes = {"7001": _days((101, 1, money), (95, None, money), (96.9, 1.9, money), name="除息")}
        result = sr.strong_stocks({"辛業": ["7001"]}, quotes, DATES)
        # 1.01 × 1 × 1.02＝1.0302
        self.assertEqual(result["groups"]["辛業"],
                         [{"code": "7001", "name": "除息", "gain_pct": 3.0, "today_pct": 2.0, "limit_up": False}])

    def test_today_without_an_official_change_shows_no_today_figure(self):
        money = 8000 * WAN
        # 今天就是不比價的那一天：今日漲跌沒有數字可寫（不拿 0 冒充平盤），前兩天的漲幅照算
        quotes = {"7002": _days((102, 2, money), (104.04, 2.04, money), (99, None, money), name="今天除息")}
        result = sr.strong_stocks({"辛業": ["7002"]}, quotes, DATES)
        # 1.02 × 1.02 × 1＝1.0404
        self.assertEqual(result["groups"]["辛業"],
                         [{"code": "7002", "name": "今天除息", "gain_pct": 4.0, "today_pct": None, "limit_up": False}])

    def test_uses_the_last_three_of_the_given_trading_days(self):
        money = 8000 * WAN
        quotes = {"8001": {"2026-11-04": {"name": "舊", "close": 50, "change": -50, "turnover": money, "suspended": 0},
                           **_days((101, 1, money), (101, 0, money), (101, 0, money), name="新")}}
        result = sr.strong_stocks({"壬業": ["8001"]}, quotes, ["2026-11-04", *DATES])
        self.assertEqual(result["date"], "2026-11-09")
        self.assertEqual(result["groups"]["壬業"][0]["gain_pct"], 1.0)   # 11-04 的 −50% 不在這 3 天裡

    def test_states_the_agreed_rule_alongside_the_result(self):
        result = sr.strong_stocks({}, {}, DATES)
        # 本人 2026-11-20 定的三個數字：近 3 日、每個產業最多 3 檔、平均每天成交 5,000 萬元
        self.assertEqual({key: result[key] for key in ("window_days", "limit", "min_avg_turnover")},
                         {"window_days": 3, "limit": 3, "min_avg_turnover": 50_000_000})
        self.assertEqual(result["groups"], {})

    def test_refuses_fewer_than_three_trading_days(self):
        with self.assertRaises(ValueError):
            sr.strong_stocks({}, {}, DATES[1:])


class LoadQuotesTest(unittest.TestCase):
    """行情庫轉接：上市、上櫃兩個日線庫各讀這幾天，只取要的代號。"""

    def _db(self, folder: Path, name: str, rows: list[tuple]) -> Path:
        path = folder / name
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("CREATE TABLE daily (date TEXT, code TEXT, name TEXT, kind TEXT, close REAL, "
                               "change REAL, turnover INTEGER, suspended INTEGER)")
            connection.executemany("INSERT INTO daily VALUES (?, ?, ?, 'common', ?, ?, ?, ?)", rows)
            connection.commit()
        return path

    def test_reads_both_markets_for_the_wanted_codes_and_days(self):
        with tempfile.TemporaryDirectory() as folder:
            listed = self._db(Path(folder), "listed.sqlite", [
                ("2026-11-04", "2330", "台積電", 900.0, 5.0, 100, 0),       # 不在要的日期裡
                ("2026-11-06", "2330", "台積電", 905.0, -5.0, 250, 0),      # 夾在中間、但沒有要這一天
                ("2026-11-05", "2330", "台積電", 910.0, 10.0, 200, 0),
                ("2026-11-09", "2330", "台積電", 905.0, None, 300, 0),      # 不比價的日子：漲跌是空的
                ("2026-11-09", "9999", "不要的", 10.0, 1.0, 400, 0),
            ])
            otc = self._db(Path(folder), "otc.sqlite", [("2026-11-06", "6488", "環球晶", 400.0, -4.0, 500, 1)])
            quotes = sr.load_quotes(["2330", "6488"], ["2026-11-05", "2026-11-09"], listed_db=listed, otc_db=otc)
            both_days = sr.load_quotes(["6488"], ["2026-11-06"], listed_db=listed, otc_db=otc)
        self.assertEqual({**quotes, **both_days}, {
            "2330": {"2026-11-05": {"name": "台積電", "close": 910.0, "change": 10.0, "turnover": 200, "suspended": 0},
                     "2026-11-09": {"name": "台積電", "close": 905.0, "change": None, "turnover": 300, "suspended": 0}},
            "6488": {"2026-11-06": {"name": "環球晶", "close": 400.0, "change": -4.0, "turnover": 500, "suspended": 1}},
        })


if __name__ == "__main__":
    unittest.main()
