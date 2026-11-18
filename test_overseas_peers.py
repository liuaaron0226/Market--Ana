"""國外同類股的每日行情抓取（自選股關注 B 票）。用假的網路回應，檢查雲端資料庫裡存下了什麼。"""
from __future__ import annotations

import io
import json
import os
import sqlite3
import unittest
from contextlib import redirect_stdout
from unittest import mock
from datetime import date, datetime, timezone
from pathlib import Path

import overseas_peers as op

ROOT = Path(__file__).resolve().parent
TODAY = date(2026, 11, 16)
NOW = "2026-11-16T08:00:00Z"


def ms(day: str) -> int:
    """Massive 的日線時間戳記：美東當天 0 點（UTC 05:00）。"""
    return int(datetime.fromisoformat(f"{day}T05:00:00+00:00").timestamp() * 1000)


def massive(bars: list[tuple[str, float]]) -> tuple[int, bytes]:
    results = [{"o": c - 1, "h": c + 1, "l": c - 2, "c": c, "v": 1000, "t": ms(d)} for d, c in bars]
    return 200, json.dumps({"ticker": "X", "status": "OK", "resultsCount": len(results), "results": results}).encode()


def eodhd(bars: list[tuple[str, float]]) -> tuple[int, bytes]:
    return 200, json.dumps([{"date": d, "open": c, "high": c, "low": c, "close": c * 2, "adjusted_close": c, "volume": 5}
                            for d, c in bars]).encode()


class FakeHttp:
    """依網址裡的代號回固定的回應，並記下呼叫過哪些網址。"""

    def __init__(self, routes: dict[str, tuple[int, bytes] | Exception]):
        self.routes = routes
        self.urls: list[str] = []

    def __call__(self, url: str) -> tuple[int, bytes]:
        self.urls.append(url)
        for key, answer in self.routes.items():
            if f"/{key}/" in url or f"/{key}?" in url:
                if isinstance(answer, Exception):
                    raise answer
                return answer
        raise AssertionError(f"沒有準備這個網址的回應：{url}")


class FakeD1:
    """以本機 SQLite 套上 0011、0016 兩個 migration 模擬雲端資料庫。"""

    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.executescript("CREATE TABLE user_watchlist (email TEXT, code TEXT, watching INTEGER, PRIMARY KEY (email, code));")
        self.db.executescript((ROOT / "cloud/migrations/0016_watch_peers.sql").read_text(encoding="utf-8"))
        self.files = 0

    def query(self, sql: str) -> list[dict]:
        cursor = self.db.execute(sql)
        names = [column[0] for column in cursor.description]
        return [dict(zip(names, row)) for row in cursor.fetchall()]

    def execute(self, statements: list[str]) -> None:
        self.files += 1
        self.db.executescript("\n".join(statements))

    def peer(self, code: str, symbol: str, market: str, *, watching: int = 1, status: str = "confirmed") -> None:
        self.db.execute("INSERT OR IGNORE INTO user_watchlist VALUES ('o@x', ?, ?)", (code, watching))
        self.db.execute("INSERT INTO watch_peers VALUES ('o@x', ?, ?, ?, ?, ?, NULL, 'owner', ?)", (code, symbol, symbol, market, status, NOW))

    def quotes(self, symbol: str) -> list[tuple]:
        return self.db.execute("SELECT date, close, source FROM overseas_quotes WHERE symbol = ? ORDER BY date", (symbol,)).fetchall()

    def status(self, symbol: str) -> tuple | None:
        return self.db.execute("SELECT ok, succeeded_at, problem FROM overseas_fetch_status WHERE symbol = ?", (symbol,)).fetchone()


def run(d1: FakeD1, http: FakeHttp, *, massive_key: str | None = "mk", eodhd_key: str | None = "ek", today: date = TODAY,
        now: str = NOW) -> dict:
    sources = {"massive": op.MassiveSource(massive_key, http) if massive_key else None,
               "eodhd": op.EodhdSource(eodhd_key, http) if eodhd_key else None}
    return op.daily(d1, sources, today=today, now=now, sleep=lambda seconds: None)


class DailyFetchTest(unittest.TestCase):
    def test_normal_response_stores_one_year_with_close_date_and_fetch_time(self):
        d1 = FakeD1()
        d1.peer("2330", "NVDA.US", "US")
        d1.peer("2330", "005930.KO", "KR")
        http = FakeHttp({"NVDA": massive([("2026-11-12", 180.5), ("2026-11-13", 182)]),
                         "005930.KO": eodhd([("2026-11-13", 55000)])})
        summary = run(d1, http)
        self.assertEqual(d1.quotes("NVDA.US"), [("2026-11-12", 180.5, "massive"), ("2026-11-13", 182.0, "massive")])
        self.assertEqual(d1.quotes("005930.KO"), [("2026-11-13", 55000.0, "eodhd")], "用還原後的收盤")
        self.assertEqual(d1.status("NVDA.US"), (1, NOW, None))
        self.assertEqual(d1.db.execute("SELECT DISTINCT fetched_at FROM overseas_quotes").fetchall(), [(NOW,)])
        nvda_url = next(url for url in http.urls if "NVDA" in url)
        self.assertIn("/v2/aggs/ticker/NVDA/range/1/day/2025-11-15/2026-11-16", nvda_url, "沒有舊資料時取約一年")
        self.assertEqual(summary, {"symbols": 2, "ok": 2, "problems": {}, "rows": 3})
        self.assertNotIn("mk", json.dumps(summary))

    def test_only_confirmed_peers_of_watched_stocks_and_japan_has_no_source(self):
        d1 = FakeD1()
        d1.peer("2330", "NVDA.US", "US")
        d1.peer("2330", "AMD.US", "US", status="removed")
        d1.peer("2317", "AAPL.US", "US", watching=0)
        d1.peer("2330", "8035.TSE", "JP")
        http = FakeHttp({"NVDA": massive([("2026-11-13", 1)])})
        summary = run(d1, http)
        self.assertEqual(len(http.urls), 1)
        self.assertEqual(summary["symbols"], 1)
        self.assertIsNone(d1.status("8035.TSE"), "日本個股不抓、不記失敗")

    def test_unknown_symbol_quota_and_bad_format_store_nothing_and_do_not_block_others(self):
        d1 = FakeD1()
        for symbol, market in [("AAA.US", "US"), ("BBB.US", "US"), ("CCC.US", "US"), ("DDD.US", "US"), ("KS11.INDX", "INDEX"), ("N225.INDX", "INDEX")]:
            d1.peer("2330", symbol, market)
        http = FakeHttp({
            "AAA": (200, json.dumps({"status": "OK", "resultsCount": 0}).encode()),                   # 查無代號
            "BBB": (200, json.dumps({"status": "OK", "results": [{"c": "abc", "t": 1}]}).encode()),   # 格式不是預期
            "CCC": OSError("connection reset"),
            "DDD": massive([("2026-11-13", 10)]),
            "KS11.INDX": (402, b"You exceeded your daily API requests limit"),                         # 額度用完
            "N225.INDX": eodhd([("2026-11-13", 1)]),
        })
        summary = run(d1, http)
        for symbol in ["AAA.US", "BBB.US", "CCC.US", "KS11.INDX", "N225.INDX"]:
            self.assertEqual(d1.quotes(symbol), [], symbol)
        self.assertEqual(d1.quotes("DDD.US"), [("2026-11-13", 10.0, "massive")])
        self.assertEqual(d1.status("AAA.US"), (0, None, "not_found"))
        self.assertEqual(d1.status("BBB.US"), (0, None, "format"))
        self.assertEqual(d1.status("CCC.US"), (0, None, "network"))
        self.assertEqual(d1.status("KS11.INDX"), (0, None, "quota"))
        self.assertEqual(d1.status("N225.INDX"), (0, None, "quota"), "額度用完後同一個來源不再打")
        self.assertFalse(any("N225" in url for url in http.urls))
        self.assertEqual(summary["problems"], {"not_found": 1, "format": 1, "network": 1, "quota": 2})

    def test_failure_keeps_old_data_and_last_success_time(self):
        d1 = FakeD1()
        d1.peer("2330", "NVDA.US", "US")
        run(d1, FakeHttp({"NVDA": massive([("2026-11-12", 1), ("2026-11-13", 2)])}), now="2026-11-14T08:00:00Z")
        run(d1, FakeHttp({"NVDA": (429, b"{}")}))
        self.assertEqual([row[0] for row in d1.quotes("NVDA.US")], ["2026-11-12", "2026-11-13"])
        self.assertEqual(d1.status("NVDA.US"), (0, "2026-11-14T08:00:00Z", "quota"))

    def test_incremental_fetch_overwrites_recent_days_and_prunes_older_than_a_year(self):
        d1 = FakeD1()
        d1.peer("2330", "NVDA.US", "US")
        run(d1, FakeHttp({"NVDA": massive([("2025-11-01", 1), ("2026-11-12", 2)])}), today=date(2026, 11, 13))
        http = FakeHttp({"NVDA": massive([("2026-11-12", 2.5), ("2026-11-13", 3)])})
        run(d1, http)
        self.assertIn("/range/1/day/2026-11-05/2026-11-16", http.urls[0], "從最後一筆往前一週重抓")
        self.assertEqual(d1.quotes("NVDA.US"), [("2026-11-12", 2.5, "massive"), ("2026-11-13", 3.0, "massive")], "一年以前的清掉")

    def test_missing_key_marks_that_source_only(self):
        d1 = FakeD1()
        d1.peer("2330", "NVDA.US", "US")
        d1.peer("2330", "005930.KO", "KR")
        http = FakeHttp({"005930.KO": eodhd([("2026-11-13", 1)])})
        run(d1, http, massive_key=None)
        self.assertEqual(d1.status("NVDA.US"), (0, None, "no_key"))
        self.assertEqual(d1.status("005930.KO")[0], 1)

    def test_eodhd_symbols_beyond_daily_budget_are_not_fetched(self):
        d1 = FakeD1()
        routes = {}
        for i in range(op.EODHD_SYMBOL_LIMIT + 1):
            symbol = f"{i:06d}.KO"
            d1.peer("2330", symbol, "KR")
            routes[symbol] = eodhd([("2026-11-13", 1)])
        http = FakeHttp(routes)
        summary = run(d1, http)
        self.assertEqual(len(http.urls), op.EODHD_SYMBOL_LIMIT)
        self.assertEqual(summary["problems"], {"over_limit": 1})

    def test_massive_calls_are_paced_for_the_free_plan(self):
        d1 = FakeD1()
        d1.peer("2330", "AAA.US", "US")
        d1.peer("2330", "BBB.US", "US")
        pauses: list[float] = []
        http = FakeHttp({"AAA": massive([("2026-11-13", 1)]), "BBB": massive([("2026-11-13", 1)])})
        op.daily(d1, {"massive": op.MassiveSource("k", http), "eodhd": None}, today=TODAY, now=NOW, sleep=pauses.append)
        self.assertEqual(pauses, [op.MassiveSource.pause])
        self.assertGreaterEqual(op.MassiveSource.pause, 12, "免費方案每分鐘 5 次")


class PurgeTest(unittest.TestCase):
    def test_purge_one_source_deletes_its_quotes_and_status_only(self):
        d1 = FakeD1()
        d1.peer("2330", "NVDA.US", "US")
        d1.peer("2330", "005930.KO", "KR")
        run(d1, FakeHttp({"NVDA": massive([("2026-11-13", 1)]), "005930.KO": eodhd([("2026-11-13", 1)])}))
        d1.execute(op.purge_sql("massive"))
        self.assertEqual(d1.quotes("NVDA.US"), [])
        self.assertIsNone(d1.status("NVDA.US"))
        self.assertEqual(len(d1.quotes("005930.KO")), 1)
        d1.execute(op.purge_sql("all"))
        self.assertEqual(d1.quotes("005930.KO"), [])
        with self.assertRaises(ValueError):
            op.purge_sql("other")


def home_routes(bars: list[tuple[str, float]]) -> dict:
    return {symbol: eodhd(bars) for symbol in op.HOME_INDICES}


def run_indices(d1: FakeD1, http: FakeHttp, *, key: str | None = "ek", today: date = TODAY, now: str = NOW) -> dict:
    return op.indices(d1, op.EodhdSource(key, http) if key else None, today=today, now=now)


class HomeIndicesTest(unittest.TestCase):
    """大盤頁切換國外指數（本人 11-19）：五個指數不管有沒有人關注都抓、舊資料一直累積。"""

    def test_the_five_indices_are_the_ones_the_owner_picked(self):
        self.assertEqual(op.HOME_INDICES, ("DJI.INDX", "IXIC.INDX", "GSPC.INDX", "N225.INDX", "KS11.INDX"))

    def test_first_run_stores_everything_eodhd_returns_without_a_one_year_cut(self):
        d1 = FakeD1()
        http = FakeHttp(home_routes([("2025-10-01", 10), ("2026-11-13", 20)]))
        summary = run_indices(d1, http)
        for symbol in op.HOME_INDICES:
            self.assertEqual(d1.quotes(symbol), [("2025-10-01", 10.0, "eodhd"), ("2026-11-13", 20.0, "eodhd")], "用還原後的收盤")
            self.assertEqual(d1.status(symbol), (1, NOW, None))
        self.assertEqual(len(http.urls), 5)
        self.assertIn("from=2025-10-12&to=2026-11-16", http.urls[0], "沒有舊資料時取免費方案給得到的約 400 天")
        self.assertEqual(summary, {"symbols": 5, "ok": 5, "problems": {}, "rows": 10})

    def test_later_runs_refetch_the_last_week_and_keep_older_days(self):
        d1 = FakeD1()
        run_indices(d1, FakeHttp(home_routes([("2024-01-02", 1), ("2026-11-12", 2)])), today=date(2026, 11, 13))
        http = FakeHttp(home_routes([("2026-11-12", 3), ("2026-11-13", 4)]))
        run_indices(d1, http)
        self.assertIn("from=2026-11-05&to=2026-11-16", http.urls[0], "從最後一筆往前一週重抓")
        self.assertEqual([row[:2] for row in d1.quotes("DJI.INDX")], [("2024-01-02", 1.0), ("2026-11-12", 3.0), ("2026-11-13", 4.0)])

    def test_peer_retention_does_not_delete_old_index_days(self):
        d1 = FakeD1()
        run_indices(d1, FakeHttp(home_routes([("2024-01-02", 1)])))
        d1.peer("2330", "NVDA.US", "US")
        d1.db.execute("INSERT INTO overseas_quotes VALUES ('NVDA.US', '2024-01-02', 1, 1, 1, 1, 1, 'massive', ?)", (NOW,))
        run(d1, FakeHttp({"NVDA": massive([("2026-11-13", 1)])}))
        self.assertEqual([row[0] for row in d1.quotes("NVDA.US")], ["2026-11-13"], "同類股照舊只留一年")
        self.assertEqual([row[0] for row in d1.quotes("KS11.INDX")], ["2024-01-02"], "首頁的五個指數一直累積")

    def test_one_failure_keeps_old_data_and_does_not_block_the_others(self):
        d1 = FakeD1()
        run_indices(d1, FakeHttp(home_routes([("2026-11-12", 1)])), now="2026-11-14T08:00:00Z")
        routes = home_routes([("2026-11-13", 2)])
        routes["N225.INDX"] = (200, b"<html>")
        run_indices(d1, FakeHttp(routes))
        self.assertEqual([row[0] for row in d1.quotes("N225.INDX")], ["2026-11-12"])
        self.assertEqual(d1.status("N225.INDX"), (0, "2026-11-14T08:00:00Z", "format"))
        self.assertEqual([row[0] for row in d1.quotes("KS11.INDX")], ["2026-11-12", "2026-11-13"])

    def test_quota_stops_the_remaining_calls(self):
        d1 = FakeD1()
        routes = home_routes([("2026-11-13", 2)])
        routes["IXIC.INDX"] = (402, b"{}")
        http = FakeHttp(routes)
        summary = run_indices(d1, http)
        self.assertEqual(len(http.urls), 2, "額度用完之後不再打")
        self.assertEqual(summary["problems"], {"quota": 4})

    def test_command_line_runs_only_the_indices(self):
        d1 = FakeD1()
        d1.peer("2330", "NVDA.US", "US")
        out = io.StringIO()
        with mock.patch.object(op, "WranglerD1", return_value=d1), mock.patch.dict(os.environ, {"EODHD_API_KEY": "", "MASSIVE_API_KEY": "mk"}), \
                redirect_stdout(out):
            self.assertEqual(op.main(["indices"]), 0)
        self.assertEqual(json.loads(out.getvalue())["problems"], {"no_key": 5})
        self.assertIsNone(d1.status("NVDA.US"), "同類股不在這一輪")

    def test_schedules_write_the_indices_morning_evening_and_after_publishing(self):
        """本人 11-19「早上就看得到」：晨間版（美股昨晚）、晚間版（日韓今天）只在正式執行時寫；每日更新發布後也寫。"""
        for name in ("forecast-morning.yml", "forecast-ledger.yml"):
            text = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
            step = text[text.index("- name: 首頁國外指數寫進網站資料庫"):]
            step = step[:step.index("\n      - name:")]
            self.assertIn("if: steps.mode.outputs.dry_run == 'false'", step, name)
            self.assertIn("continue-on-error: true", step, name)
            self.assertIn("python overseas_peers.py indices --shared-cache", step, name)
            self.assertLess(text.index("python overseas_inputs.py fetch"), text.index("首頁國外指數寫進網站資料庫"), "預測抓完快取之後")
        daily = (ROOT / ".github/workflows/daily-update.yml").read_text(encoding="utf-8")
        self.assertIn("python overseas_peers.py indices --shared-cache", daily)

    def test_missing_key_marks_all_five_and_writes_no_quotes(self):
        d1 = FakeD1()
        summary = run_indices(d1, FakeHttp({}), key=None)
        self.assertEqual(summary["problems"], {"no_key": 5})
        self.assertEqual(d1.quotes("DJI.INDX"), [])


if __name__ == "__main__":
    unittest.main()
