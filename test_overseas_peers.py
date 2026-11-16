"""國外同類股的每日行情抓取（自選股關注 B 票）。用假的網路回應，檢查雲端資料庫裡存下了什麼。"""
from __future__ import annotations

import json
import sqlite3
import unittest
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
    """以本機 SQLite 套上 0011、0014 兩個 migration 模擬雲端資料庫。"""

    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.executescript("CREATE TABLE user_watchlist (email TEXT, code TEXT, watching INTEGER, PRIMARY KEY (email, code));")
        self.db.executescript((ROOT / "cloud/migrations/0014_watch_peers.sql").read_text(encoding="utf-8"))
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


if __name__ == "__main__":
    unittest.main()
