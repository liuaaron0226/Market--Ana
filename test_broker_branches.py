"""主力分點抓取腳本（.scratch/broker-branches 票 01）：從命令列入口測，網路用假的回應（Big5 的頁面原文），
資料庫用本機 SQLite 套同一份變更檔，檢查存下了什麼、公開的輸出印了什麼。不連外。"""
import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import broker_branches as bb

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "test_fixtures/broker_branches"
MIGRATION = (ROOT / "cloud/migrations/0036_broker_branches.sql").read_text(encoding="utf-8")
PORTFOLIO = (ROOT / "cloud/migrations/0005_user_portfolio.sql").read_text(encoding="utf-8")
NOW = datetime(2026, 11, 24, 2, 0, tzinfo=timezone.utc)          # 台北 2026-11-24（週二）10:00
STAMP = "2026-11-24T02:00:00Z"
START = date(2025, 11, 25)                                       # --years 1 的起日
TODAY = date(2026, 11, 24)
SUB = "0039003200300046"                                         # 16 個十六進位字元的分點代碼
TAIBANK = ("1040", "1040", "臺銀")
DUNNAN = ("8888", "8880", "國泰-敦南")
ZHANQIAN = (SUB, "9200", "凱基-站前")


class Response:
    def __init__(self, status=200, content=b""):
        self.status, self.content = status, content


def summary_page(rows, day="2026/11/23"):
    """rows：[(b, bhid, 名稱, 買進, 賣出)]；買超的排左邊、賣超的排右邊（賣超那一格是正數），照真的頁面一列放兩個分點。"""
    def cell(row):
        b, bhid, name, buy, sell = row
        return (f'<TD class="t4t1" nowrap><a href="/z/zc/zco/zco0/zco0.djhtm?a=2330&b={b}&BHID={bhid}">{name}</a></TD>\n'
                f'<TD class="t3n1">{buy:,}</TD><TD class="t3n1">{sell:,}</TD><TD class="t3n1">{abs(buy - sell):,}</TD><TD class="t3n1">1.5%</TD>')
    empty = '<TD class="t4t1">&nbsp;</TD>' + '<TD class="t3n1">&nbsp;</TD>' * 4
    buys, sells = [r for r in rows if r[3] >= r[4]], [r for r in rows if r[3] < r[4]]
    body = "".join(f"<TR>{cell(buys[i]) if i < len(buys) else empty}{cell(sells[i]) if i < len(sells) else empty}</TR>\n"
                   for i in range(max(len(buys), len(sells))))
    return Response(content=(f'<HTML><BODY><TABLE><TR><TD colspan="10"><DIV class="t11">最後更新日：{day}</DIV></TD></TR>\n'
                             f'<TR><TD class="t2">券商名稱</TD><TD class="t2">買進</TD></TR>\n{body}</TABLE></BODY></HTML>').encode("big5"))


def detail_page(rows):
    """rows：[(date 物件, 買進, 賣出)]，照真的頁面新的在上面。"""
    body = "".join(f'<TR><TD class="t4n0">{day:%Y/%m/%d}</TD><TD class="t3n1">{buy:,}</TD><TD class="t3n1">{sell:,}</TD>'
                   f'<TD class="t3n1">{buy + sell:,}</TD><TD class="t3n1">{buy - sell:,}</TD></TR>\n'
                   for day, buy, sell in sorted(rows, reverse=True))
    return Response(content=(f'<HTML><BODY><TABLE><TR><TD class="t2">日期</TD><TD class="t2">買進(張)</TD><TD class="t2">賣出(張)</TD>'
                             f'<TD class="t2">買賣總額(張)</TD><TD class="t2">買賣超(張)</TD></TR>\n{body}</TABLE></BODY></HTML>').encode("big5"))


def weekdays(start, end):
    day = start
    while day <= end:
        if day.weekday() < 5:
            yield day
        day += timedelta(days=1)


def ymd(text):
    return date(*(int(part) for part in text.split("-")))


class Web:
    """假的來源：依網址的種類交給三個函式；記下每一次要的網址。"""

    def __init__(self, *, window=None, latest=None, detail=None):
        self.window, self.latest, self.detail = window, latest, detail
        self.calls = []

    def __call__(self, url, params=None):
        self.calls.append(url)
        parts = urlsplit(url)
        assert f"{parts.scheme}://{parts.netloc}" == bb.HOST, url
        query = {key: values[0] for key, values in parse_qs(parts.query).items()}
        if parts.path == "/z/zc/zco/zco.djhtm":
            return self.window(query["a"], ymd(query["e"]), ymd(query["f"]))
        if parts.path.startswith("/z/zc/zco/zco_"):
            return self.latest(parts.path.split("_", 1)[1].split(".")[0])
        assert parts.path == "/z/zc/zco/zco0/zco0.djhtm" and query["ver"] == "V3", url
        span = (ymd(query["D"]), ymd(query["E"])) if "D" in query else None
        return self.detail(query["A"], query["b"], query["BHID"], query["C"], span)

    def kinds(self):
        return ["detail" if "zco0.djhtm" in url else "summary" for url in self.calls]


def wrangler_json(rows):
    return json.dumps([{"results": rows, "success": True, "meta": {}}])


class Case(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.executescript(MIGRATION)
        self.sleeps = []

    def run_cli(self, *argv, web=None):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = bb.main([*argv, "--sql-output", str(self.folder / "out.sql"), "--report", str(self.folder / "report.json")],
                           get=web, sleep=self.sleeps.append, now=NOW)
        self.assertEqual(code, 0)
        self.stdout = out.getvalue()
        self.sql = (self.folder / "out.sql").read_text(encoding="utf-8")
        self.db.executescript(self.sql)
        return json.loads(self.stdout)

    def write(self, name, rows):
        path = self.folder / name
        path.write_text("some wrangler preamble\n" + wrangler_json(rows), encoding="utf-8")
        return str(path)

    def stocks_file(self, *codes):
        return self.write("stocks.json", [{"code": code} for code in codes])

    def state_file(self, purpose):
        """照排程的做法：用腳本自己印的查詢讀資料庫，存成 wrangler --json 的樣子。"""
        cursor = self.db.execute(bb.state_sql(purpose))
        names = [column[0] for column in cursor.description]
        return self.write(f"state-{purpose}.json", [dict(zip(names, row)) for row in cursor.fetchall()])

    def stock(self, code="2330"):
        row = self.db.execute("SELECT backfilled_from, updated_through, last_attempt_at, last_error FROM broker_branch_stocks "
                              "WHERE code = ?", (code,)).fetchone()
        return row and dict(zip(("backfilled_from", "updated_through", "last_attempt_at", "last_error"), row))

    def days(self, branch, code="2330"):
        return self.db.execute("SELECT date, buy, sell, net FROM broker_branch_days WHERE code = ? AND branch_id = ? ORDER BY date",
                               (code, branch)).fetchall()

    def report(self):
        return json.loads((self.folder / "report.json").read_text(encoding="utf-8"))


class ParseTest(unittest.TestCase):
    def test_a_summary_page_gives_both_sides_with_sell_side_negative(self):
        parsed = bb.parse_summary(bb.decode((FIXTURES / "summary.djhtm").read_bytes()))
        self.assertEqual(parsed["date"], "2026-11-23")
        rows = {row["id"]: row for row in parsed["rows"]}
        self.assertEqual(list(rows), ["0039004100300030", "1470", "1040", SUB, "8888"])
        self.assertEqual(rows["1040"], {"id": "1040", "bhid": "1040", "name": "臺銀", "buy": 23786.0, "sell": 739.0,
                                        "net": 23047.0, "side": "buy"})
        self.assertEqual((rows["1470"]["net"], rows["1470"]["side"], rows["1470"]["name"]), (-20500.0, "sell", "台灣摩根士丹利"))
        self.assertEqual((rows[SUB]["bhid"], rows[SUB]["name"], rows[SUB]["net"]), ("9200", "凱基-站前", -8599.5))
        self.assertEqual((rows["0039004100300030"]["bhid"], rows["8888"]["bhid"], rows["8888"]["name"]), ("9A00", "8880", "國泰-敦南"))

    def test_a_detail_page_gives_one_row_per_day_oldest_first(self):
        rows = bb.parse_detail(bb.decode((FIXTURES / "detail.djhtm").read_bytes()))
        self.assertEqual([row["date"] for row in rows], ["2026-11-18", "2026-11-19", "2026-11-20", "2026-11-23"])
        self.assertEqual(rows[1], {"date": "2026-11-19", "buy": 120.0, "sell": 1320.0, "net": -1200.0})
        self.assertEqual(rows[3], {"date": "2026-11-23", "buy": 5503.0, "sell": 526.0, "net": 4977.0})

    def test_pages_that_are_not_the_expected_shape_are_a_format_change(self):
        page = bb.decode((FIXTURES / "summary.djhtm").read_bytes())
        for broken in ("<html>請稍後再試</html>", page.replace("56,145", "--"), page.replace("最後更新日", "日期"),
                       page.replace("40,612", "612")):
            with self.assertRaises(bb.FormatChanged):
                bb.parse_summary(broken)
        self.assertEqual(bb.parse_summary("<html></html>", require_rows=False), {"date": None, "rows": []})
        with self.assertRaises(bb.FormatChanged):
            bb.parse_detail(bb.decode((FIXTURES / "detail.djhtm").read_bytes()).replace("4,977", "n/a"))

    def test_the_urls_follow_the_source(self):
        self.assertEqual(bb.summary_url("2330"), "https://fubon-ebrokerdj.fbs.com.tw/z/zc/zco/zco_2330.djhtm")
        self.assertEqual(bb.summary_url("2330", date(2026, 9, 1), date(2026, 11, 5)),
                         "https://fubon-ebrokerdj.fbs.com.tw/z/zc/zco/zco.djhtm?a=2330&e=2026-9-1&f=2026-11-5")
        self.assertEqual(bb.detail_url("2330", "9200", SUB, date(2026, 9, 1), date(2026, 11, 5)),
                         f"https://fubon-ebrokerdj.fbs.com.tw/z/zc/zco/zco0/zco0.djhtm?A=2330&BHID=9200&b={SUB}&C=1&D=2026-9-1&E=2026-11-5&ver=V3")
        self.assertTrue(bb.detail_url("2330", "1040", "1040", preset=3).endswith("?A=2330&BHID=1040&b=1040&C=3&ver=V3"))

    def test_three_years_are_twelve_consecutive_windows_newest_first(self):
        windows = bb.span_windows(date(2023, 11, 25), TODAY)
        self.assertEqual(len(windows), 12)
        self.assertEqual(windows[0], (date(2026, 8, 25), TODAY))
        self.assertEqual(windows[-1][0], date(2023, 11, 25))
        for newer, older in zip(windows, windows[1:]):
            self.assertEqual(older[1] + timedelta(days=1), newer[0])


def backfill_web(*, detail=None):
    """近一年四段：最新一段上榜的是臺銀與凱基-站前，第二段是臺銀與國泰-敦南，更早兩段沒有資料。"""
    def window(code, start, end):
        if end == TODAY:
            return summary_page([(*TAIBANK, 900, 100), (*ZHANQIAN, 50, 700)])
        if end == date(2026, 8, 24):
            return summary_page([(*TAIBANK, 300, 100), (*DUNNAN, 10, 400)])
        return Response(content="<HTML><BODY>查無資料</BODY></HTML>".encode("big5"))

    def whole_span(code, branch, bhid, preset, span):
        return detail_page([(START, 5, 0), (date(2026, 6, 1), 10, 30), (date(2026, 11, 23), 100, 20), (date(2026, 11, 20), 0, 0)])

    return Web(window=window, detail=detail or whole_span)


class BackfillTest(Case):
    def test_one_stock_is_the_union_of_every_windows_top_branches_each_fetched_for_the_whole_span(self):
        web = backfill_web()
        shown = self.run_cli("backfill", "--code", "2330", "--years", "1", web=web)
        self.assertEqual(web.kinds(), ["summary"] * 4 + ["detail"] * 3)
        self.assertIn("a=2330&e=2026-8-25&f=2026-11-24", web.calls[0])
        self.assertIn("a=2330&e=2025-11-25&f=2026-2-24", web.calls[3])
        for url, (branch, bhid, _) in zip(web.calls[4:], sorted((ZHANQIAN, TAIBANK, DUNNAN))):
            self.assertIn(f"A=2330&BHID={bhid}&b={branch}&C=1&D=2025-11-25&E=2026-11-24&ver=V3", url)
        self.assertEqual(self.sleeps, [3.0] * 6, "每個請求之間都停一下")
        self.assertEqual(dict(self.db.execute("SELECT branch_id, name FROM broker_branches")),
                         {"1040": "臺銀", "8888": "國泰-敦南", SUB: "凱基-站前"})
        self.assertEqual(self.db.execute("SELECT bhid, updated_at FROM broker_branches WHERE branch_id = ?", (SUB,)).fetchone(), ("9200", STAMP))
        for branch in ("1040", "8888", SUB):
            self.assertEqual(self.days(branch), [("2025-11-25", 5.0, 0.0, 5.0), ("2026-06-01", 10.0, 30.0, -20.0),
                                                 ("2026-11-23", 100.0, 20.0, 80.0)], "買賣都是 0 的日子不存")
        self.assertEqual(self.stock(), {"backfilled_from": "2025-11-25", "updated_through": "2026-11-23",
                                        "last_attempt_at": STAMP, "last_error": None})
        self.assertEqual((shown["status"], shown["mode"], shown["pages"], shown["rows"], shown["candidates"]), ("ok", "backfill", 7, 9, 3))
        self.assertEqual(shown["stocks"], {"count": 1, "statuses": {"done": 1}, "errors": {}})
        self.assertEqual((shown["earliest"], shown["latest"], shown["stopped"]), ("2025-11-25", "2026-11-23", None))

    def test_the_default_span_is_three_years(self):
        web = backfill_web()
        self.run_cli("backfill", "--code", "2330", web=web)
        self.assertEqual(web.kinds().count("summary"), 12)
        self.assertIn("D=2023-11-25&E=2026-11-24", web.calls[12])
        self.assertEqual(self.stock()["backfilled_from"], "2023-11-25")

    def test_a_truncated_detail_is_continued_until_the_start(self):
        def capped(code, branch, bhid, preset, span):   # 來源一次最多回一百天
            start, end = span
            return detail_page([(day, 3, 1) for day in weekdays(max(start, end - timedelta(days=99)), end)])

        web = backfill_web(detail=capped)
        shown = self.run_cli("backfill", "--code", "2330", "--years", "1", web=web)
        expected = [day.isoformat() for day in weekdays(START, TODAY)]
        for branch in ("1040", "8888", SUB):
            self.assertEqual([row[0] for row in self.days(branch)], expected)
        first = [url for url in web.calls if "b=1040" in url]
        self.assertEqual(len(first), 4)
        self.assertIn("D=2025-11-25&E=2026-11-24", first[0])
        self.assertIn("D=2025-11-25&E=2026-8-16", first[1], "接著要的是還沒拿到的更早那一段")
        self.assertEqual(shown["detail"]["continuations"], 9)
        self.assertEqual(self.stock()["last_error"], None)

    def test_a_branch_that_simply_started_later_costs_no_extra_page_once_a_full_answer_was_seen(self):
        def detail(code, branch, bhid, preset, span):
            if branch == "1040":      # 排序後第二個；第一個（凱基-站前）一次就回到起日
                return detail_page([(date(2026, 10, 1), 3, 1)])
            return detail_page([(day, 3, 1) for day in weekdays(*span)])

        web = backfill_web(detail=detail)
        shown = self.run_cli("backfill", "--code", "2330", "--years", "1", web=web)
        self.assertEqual(web.kinds().count("detail"), 3)
        self.assertNotIn("continuations", shown["detail"])
        self.assertEqual(self.days("1040"), [("2026-10-01", 3.0, 1.0, 2.0)])

    def test_continuation_has_a_hard_cap(self):
        def crawl(code, branch, bhid, preset, span):   # 每次只回最後兩天
            return detail_page([(day, 3, 1) for day in list(weekdays(*span))[-2:]])

        web = backfill_web(detail=crawl)
        shown = self.run_cli("backfill", "--code", "2330", "--years", "1", "--budget", "30", web=web)
        self.assertEqual(sum(f"b={SUB}" in url for url in web.calls), bb.MAX_DETAIL_PAGES)
        self.assertEqual(shown["pages"], 30)
        self.assertGreaterEqual(shown["detail"]["capped"], 1)

    def test_the_page_budget_stops_cleanly_and_the_next_run_continues(self):
        stocks = self.stocks_file("2330", "2454")
        web = backfill_web()
        shown = self.run_cli("backfill", "--owner-stocks", "--stocks", stocks, "--state", self.state_file("backfill"),
                             "--limit", "1", "--years", "1", "--budget", "5", web=web)
        self.assertEqual(web.kinds(), ["summary"] * 4 + ["detail"])
        self.assertEqual((shown["pages"], shown["stopped"], shown["stocks"]["statuses"], shown["waiting"]),
                         (5, "page-budget", {"in-progress": 1}, 1))
        self.assertEqual(self.stock(), {"backfilled_from": None, "updated_through": None, "last_attempt_at": STAMP,
                                        "last_error": "backfill-in-progress"})
        self.assertEqual(len(self.days(SUB)), 3)
        self.assertEqual(self.days("1040"), [])

        web = backfill_web()
        shown = self.run_cli("backfill", "--owner-stocks", "--stocks", stocks, "--state", self.state_file("backfill"),
                             "--limit", "1", "--years", "1", web=web)
        self.assertEqual(web.kinds(), ["summary"] * 4 + ["detail"] * 2, "做完的分點不再抓")
        self.assertFalse([url for url in web.calls if f"b={SUB}" in url])
        self.assertEqual(shown["stocks"]["statuses"], {"done": 1})
        self.assertEqual(self.stock(), {"backfilled_from": "2025-11-25", "updated_through": "2026-11-23", "last_attempt_at": STAMP,
                                        "last_error": None})
        self.assertEqual(len(self.days("1040")), 3)

        web = backfill_web()   # 第三輪：2330 做完了，輪到下一檔
        self.run_cli("backfill", "--owner-stocks", "--stocks", stocks, "--state", self.state_file("backfill"), "--limit", "1",
                     "--years", "1", web=web)
        self.assertTrue(all("=2454&" in url for url in web.calls))
        self.assertEqual(self.stock("2454")["backfilled_from"], "2025-11-25")

    def test_a_429_stops_the_whole_run(self):
        web = Web(window=lambda *a: Response(status=429))
        shown = self.run_cli("backfill", "--owner-stocks", "--stocks", self.stocks_file("2330", "2454"),
                             "--state", self.state_file("backfill"), "--limit", "2", web=web)
        self.assertEqual(len(web.calls), 1)
        self.assertEqual((shown["stopped"], shown["page_errors"], shown["stocks"]), ("HTTP 429", {"HTTP 429": 1},
                         {"count": 1, "statuses": {"failed": 1}, "errors": {"HTTP 429": 1}}))
        self.assertEqual(self.stock()["last_error"], "HTTP 429")
        self.assertIsNone(self.stock("2454"), "沒輪到的那一檔什麼都不記")

    def test_three_failures_in_a_row_stop_the_whole_run(self):
        def boom(*a):
            raise ConnectionError("https://example.invalid/?a=2330")

        for source, kind in ((lambda *a: Response(status=500), "HTTP 500"), (boom, "ConnectionError")):
            with self.subTest(kind):
                web = Web(window=source)
                shown = self.run_cli("backfill", "--owner-stocks", "--stocks", self.stocks_file("1101", "2330", "2454", "2603"),
                                     "--state", self.state_file("backfill"), "--limit", "4", web=web)
                self.assertEqual(len(web.calls), 3)
                self.assertEqual((shown["stopped"], shown["stocks"]["errors"]), (kind, {kind: 3}))
                self.assertIsNone(self.stock("2603"))

    def test_a_changed_page_fails_that_stock_and_keeps_what_was_stored(self):
        self.db.execute("INSERT INTO broker_branch_days VALUES ('2330', '1040', '2026-11-20', 9, 1, 8)")
        self.db.execute("INSERT INTO broker_branch_stocks VALUES ('2330', NULL, NULL, '2026-11-20T02:00:00Z', 'backfill-in-progress')")
        web = Web(window=lambda *a: Response(content="<HTML><BODY>系統維護中</BODY></HTML>".encode("big5")))
        shown = self.run_cli("backfill", "--code", "2330", web=web)
        self.assertEqual(len(web.calls), 1)
        self.assertEqual(shown["stocks"]["errors"], {"format-changed": 1})
        self.assertEqual(self.days("1040"), [("2026-11-20", 9.0, 1.0, 8.0)])
        self.assertEqual(self.stock()["last_error"], "format-changed")
        self.assertNotIn("DELETE", self.sql.upper())

    def test_the_sql_has_no_transaction_and_big_inserts_are_split(self):
        def busy(code, branch, bhid, preset, span):
            return detail_page([(day, 3, 1) for day in weekdays(*span)])

        self.run_cli("backfill", "--code", "2330", "--years", "1", web=backfill_web(detail=busy))
        statements = [line for line in self.sql.splitlines() if line]
        for word in ("BEGIN", "COMMIT", "DELETE", "DROP"):
            self.assertFalse([line for line in statements if line.upper().startswith(word)])
        inserts = [line for line in statements if line.startswith("INSERT OR REPLACE INTO broker_branch_days")]
        total = self.db.execute("SELECT COUNT(*) FROM broker_branch_days").fetchone()[0]
        self.assertEqual(total, 3 * len(list(weekdays(START, TODAY))))
        self.assertEqual(len(inserts), -(-total // bb.ROWS_PER_STATEMENT))
        self.assertLess(max(len(line.encode("utf-8")) for line in statements), 50_000, "D1 一句的上限約 100 KB")
        self.assertTrue(all(line.endswith(";") for line in statements))

    def test_public_output_names_no_stock_and_no_branch_but_the_private_report_does(self):
        self.run_cli("backfill", "--code", "2330", "--years", "1", web=backfill_web())
        for private in ("2330", "1040", "8888", SUB, "臺銀", "國泰", "凱基", "9200"):
            self.assertNotIn(private, self.stdout)
        self.assertEqual(len(self.stdout.strip().splitlines()), 1)
        report = self.report()
        self.assertEqual(report["stocks"][0]["code"], "2330")
        self.assertEqual(report["stocks"][0]["branches"]["1040"], "臺銀")

    def test_wrong_arguments_exit_2_without_echoing_the_code(self):
        err = io.StringIO()
        for argv in (["backfill", "--sql-output", "x.sql"], ["backfill", "--code", "23 30;x", "--sql-output", "x.sql"],
                     ["backfill", "--code", "2330"], ["daily", "--sql-output", "x.sql"], ["stocks-sql", "--owner-email", "nope"]):
            with self.subTest(argv), contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as raised:
                bb.main(argv, get=None, sleep=self.sleeps.append, now=NOW)
            self.assertEqual(raised.exception.code, 2)
        self.assertNotIn("23 30", err.getvalue())

    def test_unreadable_inputs_fetch_nothing_and_still_exit_0(self):
        web = backfill_web()
        shown = self.run_cli("backfill", "--owner-stocks", "--stocks", str(self.folder / "missing.json"),
                             "--state", self.state_file("backfill"), web=web)
        self.assertEqual((shown, web.calls, self.sql), ({"status": "no-input", "mode": "backfill"}, [], ""))
        # 手動回補一檔：讀不到狀態（表還沒建）照樣從頭抓
        (self.folder / "broken.json").write_text('{"error": "no such table"}', encoding="utf-8")
        shown = self.run_cli("backfill", "--code", "2330", "--years", "1", "--state", str(self.folder / "broken.json"), web=web)
        self.assertEqual(shown["stocks"]["statuses"], {"done": 1})


class DailyTest(Case):
    def seed(self, *, last_attempt="2026-11-23T02:00:00Z", through="2026-11-20"):
        self.db.executemany("INSERT INTO broker_branches VALUES (?, ?, ?, 'x')",
                            [("1040", "1040", "臺銀"), (SUB, "9200", "凱基-站前"), ("9999", "9999", "舊主力")])
        self.db.executemany("INSERT INTO broker_branch_days VALUES ('2330', ?, ?, ?, ?, ?)",
                            [("1040", "2026-11-20", 9, 1, 8), (SUB, "2026-10-01", 2, 1, 1), ("9999", "2025-12-01", 7, 1, 6)])
        self.db.execute("INSERT INTO broker_branch_stocks VALUES ('2330', '2023-11-25', ?, ?, NULL)", (through, last_attempt))
        self.db.execute("INSERT INTO broker_branch_stocks VALUES ('2454', NULL, NULL, ?, 'backfill-in-progress')", (last_attempt,))

    def web(self):
        def detail(code, branch, bhid, preset, span):
            self.assertIsNone(span)
            return detail_page([(date(2026, 11, 19), 40, 10), (date(2026, 11, 23), 1, 2)] if preset == "3" else
                               [(date(2026, 11, 19), 6, 1), (date(2026, 11, 23), 7, 7)])
        return Web(latest=lambda code: summary_page([(*TAIBANK, 500, 100), (*DUNNAN, 20, 320), ("9999", "9999", "舊主力", 60, 10)]),
                   detail=detail)

    def run_daily(self, *extra, web=None):
        web = web or self.web()
        shown = self.run_cli("daily", "--stocks", self.stocks_file("2330", "2454", "FUND01"), "--state", self.state_file("daily"),
                             *extra, web=web)
        return web, shown

    def test_the_days_top_branches_are_stored_and_new_ones_get_sixty_days(self):
        self.seed()
        web, shown = self.run_daily()
        self.assertEqual(web.calls[0], "https://fubon-ebrokerdj.fbs.com.tw/z/zc/zco/zco_2330.djhtm")
        self.assertEqual(len(web.calls), 3, "只抓已回補的那一檔；本週跑過了所以不補齊")
        for url, (branch, bhid) in zip(web.calls[1:], (("8888", "8880"), ("9999", "9999"))):
            self.assertTrue(url.endswith(f"?A=2330&BHID={bhid}&b={branch}&C=3&ver=V3"), url)
        self.assertEqual(self.days("1040"), [("2026-11-20", 9.0, 1.0, 8.0), ("2026-11-23", 500.0, 100.0, 400.0)])
        self.assertEqual(self.days("8888"), [("2026-11-19", 40.0, 10.0, 30.0), ("2026-11-23", 1.0, 2.0, -1.0)],
                         "明細頁的數字蓋掉前 15 大那一頁的（同一天）")
        self.assertEqual(self.db.execute("SELECT name, bhid FROM broker_branches WHERE branch_id = '8888'").fetchone(), ("國泰-敦南", "8880"))
        self.assertEqual(self.stock(), {"backfilled_from": "2023-11-25", "updated_through": "2026-11-23", "last_attempt_at": STAMP,
                                        "last_error": None})
        self.assertEqual(self.stock("2454")["last_attempt_at"], "2026-11-23T02:00:00Z", "還沒回補完的不在每日更新裡")
        self.assertEqual((shown["mode"], shown["pages"], shown["new_candidates"], shown["owner_stocks"], shown["stocks"]),
                         ("daily", 3, 2, 2, {"count": 1, "statuses": {"done": 1}, "errors": {}}))
        for private in ("2330", "1040", "8888", "臺銀", "國泰"):
            self.assertNotIn(private, self.stdout)

    def test_a_sell_side_branch_is_stored_negative(self):
        self.seed()
        web = Web(latest=lambda code: summary_page([(*TAIBANK, 100, 900)]))
        self.run_daily(web=web)
        self.assertEqual(self.days("1040")[-1], ("2026-11-23", 100.0, 900.0, -800.0))

    def test_the_first_run_of_a_week_refreshes_recently_active_branches(self):
        self.seed(last_attempt="2026-11-20T02:00:00Z")   # 上週五
        web, shown = self.run_daily()
        refreshed = [url for url in web.calls if "&C=1&ver=V3" in url]
        self.assertEqual([parse_qs(urlsplit(url).query)["b"][0] for url in refreshed], [SUB, "1040"],
                         "近期有動作的各補最近 20 日；很久沒動作的（9999）已經當成新上榜補過 60 日")
        self.assertEqual(len(web.calls), 5)
        self.assertEqual(self.days(SUB), [("2026-10-01", 2.0, 1.0, 1.0), ("2026-11-19", 6.0, 1.0, 5.0), ("2026-11-23", 7.0, 7.0, 0.0)])
        self.assertEqual(self.stock()["last_attempt_at"], STAMP)

    def test_data_more_than_a_week_behind_is_refreshed_without_waiting_for_next_week(self):
        self.seed(through="2026-11-10")
        web, _ = self.run_daily()
        self.assertEqual(sum("&C=1&ver=V3" in url for url in web.calls), 2)

    def test_a_refresh_cut_short_by_the_budget_is_still_due_next_run(self):
        self.seed(last_attempt="2026-11-20T02:00:00Z")
        web, shown = self.run_daily("--budget", "4")
        self.assertEqual((len(web.calls), shown["stopped"], shown["stocks"]["statuses"]), (4, "page-budget", {"in-progress": 1}))
        self.assertEqual(self.stock(), {"backfilled_from": "2023-11-25", "updated_through": "2026-11-23",
                                        "last_attempt_at": "2026-11-20T02:00:00Z", "last_error": None})
        self.assertEqual(len(self.days(SUB)), 3, "抓到的照存")
        web, _ = self.run_daily()
        self.assertEqual(sum("&C=1&ver=V3" in url for url in web.calls), 4, "下一輪還是本週第一次（新上榜的兩個現在也算近期有動作）")
        self.assertEqual(self.stock()["last_attempt_at"], STAMP)

    def test_a_changed_page_keeps_old_data_and_records_format_changed(self):
        self.seed()
        web = Web(latest=lambda code: Response(content="<HTML><BODY>&#x9a57;&#x8b49;</BODY></HTML>".encode("big5")))
        _, shown = self.run_daily(web=web)
        self.assertEqual(shown["stocks"], {"count": 1, "statuses": {"failed": 1}, "errors": {"format-changed": 1}})
        self.assertEqual(self.days("1040"), [("2026-11-20", 9.0, 1.0, 8.0)])
        self.assertEqual(self.stock(), {"backfilled_from": "2023-11-25", "updated_through": "2026-11-20", "last_attempt_at": STAMP,
                                        "last_error": "format-changed"})

    def test_a_503_stops_before_the_other_stocks(self):
        self.seed()
        self.db.execute("UPDATE broker_branch_stocks SET backfilled_from = '2023-11-25' WHERE code = '2454'")
        web = Web(latest=lambda code: Response(status=503))
        _, shown = self.run_daily(web=web)
        self.assertEqual((len(web.calls), shown["stopped"]), (1, "HTTP 503"))


class QueryTest(Case):
    def test_stocks_sql_reads_only_the_owners_watchlist_and_holdings(self):
        self.db.executescript(PORTFOLIO)
        watch = "INSERT INTO user_watchlist (email, code, name, market, in_universe, created_at, updated_at) VALUES (?, ?, 'n', '上市', 1, 't', 't')"
        hold = ("INSERT INTO user_holdings (email, code, name, market, shares, average_cost, in_universe, created_at, updated_at) "
                "VALUES (?, ?, 'n', '上市', 1, 1, 1, 't', 't')")
        self.db.executemany(watch, [("Owner@example.com", "2330"), ("owner@example.com", "2454"), ("friend@example.com", "1101")])
        self.db.executemany(hold, [("owner@example.com", "2330"), ("owner@example.com", "2603"), ("friend@example.com", "2882")])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(bb.main(["stocks-sql", "--owner-email", "owner@example.com"]), 0)
        query = out.getvalue().strip()
        self.assertTrue(query.upper().startswith("SELECT"))
        self.assertEqual(query.count(";"), 1)
        self.assertEqual([row[0] for row in self.db.execute(query)], ["2330", "2454", "2603"])
        self.assertNotIn("friend", query)

    def test_state_sql_is_one_read_only_query_per_purpose(self):
        self.db.executemany("INSERT INTO broker_branch_stocks VALUES (?, ?, NULL, NULL, NULL)", [("2330", "2023-11-25"), ("2454", None)])
        self.db.execute("INSERT INTO broker_branches VALUES ('1040', '1040', '臺銀', 'x')")
        self.db.executemany("INSERT INTO broker_branch_days VALUES (?, '1040', ?, 1, 0, 1)",
                            [("2330", "2026-11-20"), ("2330", "2026-11-23"), ("2454", "2026-11-23")])
        for purpose, code in (("daily", "2330"), ("backfill", "2454")):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                bb.main(["state-sql", "--for", purpose])
            query = out.getvalue().strip()
            self.assertTrue(query.upper().startswith("SELECT") and query.count(";") == 1)
            state = bb.read_state(self.state_file(purpose))
            self.assertEqual(set(state["stocks"]), {"2330", "2454"})
            self.assertEqual(list(state["branches"]), [code])
            self.assertEqual(state["branches"][code]["1040"]["bhid"], "1040")
        self.assertEqual(bb.read_state(self.state_file("daily"))["branches"]["2330"]["1040"],
                         {"bhid": "1040", "first": "2026-11-20", "last": "2026-11-23"})


if __name__ == "__main__":
    unittest.main()
