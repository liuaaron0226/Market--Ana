"""集保查詢頁補抓過去一年的大戶持股（本人 2026-11-15「補，慢慢抓」）。

開放資料只有最新一週；過去 50 週要從官方查詢頁一檔一週地查。量大（約 17,700 次），所以規矩是：
慢慢查、可以中斷續跑、一被拒絕或回應不對勁就停，寧可缺也不存錯的數字。
"""
import json
import tempfile
import unittest
from pathlib import Path

import tdcc_backfill as tb

LEVELS = ["1-999", "1,000-5,000", "5,001-10,000", "10,001-15,000", "15,001-20,000", "20,001-30,000", "30,001-40,000",
          "40,001-50,000", "50,001-100,000", "100,001-200,000", "200,001-400,000", "400,001-600,000",
          "600,001-800,000", "800,001-1,000,000", "1,000,001以上"]


def page(code="2330", roc_date="114年11月21日", *, token="tok-1", rows=True, adjust=False,
         dates=("20261113", "20261106", "20251121"), big=(560, 325, 226, 1534), pct=(1.05, 0.87, 0.78, 86.63)) -> str:
    """照官方查詢頁的結構組一頁（表單＋結果表）。"""
    options = "".join(f'<option value="{value}">{value}</option>' for value in dates)
    body = ""
    if rows:
        counts = [1346423, 363647, 43848, 14960, 7045, 6900, 3272, 1887, 3820, 1901, 1271, *big]
        shares = [0.71, 2.69, 1.21, 0.70, 0.47, 0.65, 0.43, 0.32, 1.03, 1.01, 1.37, *pct]
        cells = [(str(index + 1), LEVELS[index], f"{counts[index]:,}", "1,000", f"{shares[index]:.2f}") for index in range(15)]
        if adjust:
            cells.append(("16", "差異數調整（說明4）", "", "-245,454", "0.00"))
        cells.append((str(len(cells) + 1), "合　計", "1,797,619", "25,932,615,521", "100.00"))
        body = "".join(
            f'<tr>\n<td align="center">{a}</td>\n<td align="center">{b}</td>\n<td align="right">{c}</td>\n'
            f'<td align="right">{d}</td>\n<td align="right">{e}</td>\n</tr>\n' for a, b, c, d, e in cells)
    return f"""<html><body><form id="form1" action="/portal/zh/smWeb/qryStock" method="post">
<input type="hidden" name="SYNCHRONIZER_TOKEN" value="{token}"/>
<input type="hidden" name="SYNCHRONIZER_URI" value="/portal/zh/smWeb/qryStock"/>
<input type="hidden" name="method" value="submit"/><input type="hidden" name="firDate" value="{dates[0]}"/>
<select name="scaDate" id="scaDate">{options}</select></form>
<div><p>證券代號：{code if rows else ''} <br>證券名稱：{'台積電' if rows else ''}</p>
<span class="font">資料日期：{roc_date if rows else ''}</span></div>
<table class="table"><thead><tr><th>序</th><th>持股/單位數分級</th><th>人數</th><th>股數/單位數</th>
<th>占集保庫存數比例 (%)</th></tr></thead>{body}</table></body></html>"""


class ParseTest(unittest.TestCase):
    def test_a_result_page_gives_the_same_numbers_the_weekly_file_keeps(self) -> None:
        self.assertEqual(tb.parse_result(page()), {
            "code": "2330", "data_date": "2025-11-21",
            "holders": {"pct_1000": 86.63, "holders_1000": 1534, "pct_400": 89.33, "holders_400": 2645, "holders": 1797619}})

    def test_the_adjustment_row_does_not_shift_the_total(self) -> None:
        # 有些週多一列「差異數調整」，合計就從第 16 列變成第 17 列；要認「合計」這個字，不認列號。
        result = tb.parse_result(page(roc_date="115年11月13日", adjust=True))
        self.assertEqual((result["data_date"], result["holders"]["holders"]), ("2026-11-13", 1797619))

    def test_an_empty_table_means_the_stock_had_no_data_that_week(self) -> None:
        self.assertIsNone(tb.parse_result(page(rows=False)))

    def test_a_page_that_is_not_the_query_page_is_refused(self) -> None:
        for html in ["<html><body>Access denied</body></html>", page().replace("占集保庫存數比例", "x"),
                     page().replace('<td align="center">15</td>', '<td align="center">99</td>')]:
            with self.assertRaises(ValueError):
                tb.parse_result(html)

    def test_form_gives_the_token_and_the_weeks_on_offer(self) -> None:
        form = tb.parse_form(page(token="abc"))
        self.assertEqual(form["fields"]["SYNCHRONIZER_TOKEN"], "abc")
        self.assertEqual(form["dates"], ["20261113", "20261106", "20251121"])
        with self.assertRaises(ValueError):
            tb.parse_form("<html>blocked</html>")


class FakeSite:
    """記下每次請求；POST 依（代號、日期）回對應的一頁。"""

    def __init__(self, answers=None, dates=("20261113", "20261106", "20251121")) -> None:
        self.answers = answers or {}
        self.dates = dates
        self.requests: list = []
        self.token = 0

    def __call__(self, data):
        self.requests.append(None if data is None else (data["stockNo"], data["scaDate"], data["SYNCHRONIZER_TOKEN"]))
        self.token += 1
        if data is None:
            return 200, page(rows=False, token=f"t{self.token}", dates=self.dates)
        answer = self.answers.get((data["stockNo"], data["scaDate"]), "ok")
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, tuple):
            return answer
        compact = data["scaDate"]
        roc = f"{int(compact[:4]) - 1911}年{compact[4:6]}月{compact[6:]}日"
        return 200, page(code=data["stockNo"], roc_date=roc, rows=answer != "empty", token=f"t{self.token}", dates=self.dates)


class BackfillTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.progress = Path(self.tmp.name) / "progress.jsonl"
        self.sleeps: list[float] = []

    def run_backfill(self, site, codes=("2330", "2317"), **kwargs) -> dict:
        return tb.backfill(list(codes), self.progress, fetch=site, sleep=self.sleeps.append, log=lambda *_: None,
                           skip_dates={"2026-11-13"}, **kwargs)

    def rows(self) -> list[dict]:
        return [json.loads(line) for line in self.progress.read_text(encoding="utf-8").splitlines()]

    def test_newest_week_first_for_every_stock_and_a_pause_before_each_query(self) -> None:
        site = FakeSite()
        report = self.run_backfill(site)
        self.assertEqual(report["status"], "done")
        # 已經有的那一週（開放資料抓過的）不查；其餘先把最近一週所有股票查完，再往前一週。
        self.assertEqual([request[:2] for request in site.requests[1:]],
                         [("2330", "20261106"), ("2317", "20261106"), ("2330", "20251121"), ("2317", "20251121")])
        # 每次查詢都帶上一頁給的新權杖
        self.assertEqual([request[2] for request in site.requests[1:]], ["t1", "t2", "t3", "t4"])
        self.assertEqual(self.sleeps, [3.0] * 4)
        self.assertEqual([(row["code"], row["date"], row["holders"]["holders_1000"]) for row in self.rows()],
                         [("2330", "2026-11-06", 1534), ("2317", "2026-11-06", 1534),
                          ("2330", "2025-11-21", 1534), ("2317", "2025-11-21", 1534)])

    def test_the_form_page_is_reopened_every_so_often_like_a_visitor_reloading(self) -> None:
        # 11-15 實跑：同一個連線階段查了 153 次、約 9 分半後，官方回 HTTP 404；馬上重新開表單頁再查同一檔就正常。
        # 所以每查一批就重新開一次表單頁；規矩不變——任何一次不是 200 還是立刻停。
        site = FakeSite()
        report = self.run_backfill(site, refresh_every=3)
        self.assertEqual(report["status"], "done")
        self.assertEqual([request and request[0] for request in site.requests], [None, "2330", "2317", "2330", None, "2317"])
        self.assertEqual(site.requests[-1][2], "t5", "重新開頁之後用新頁面的權杖")
        self.assertEqual(self.sleeps, [3.0] * 5, "重新開頁前也等一樣久")

    def test_a_second_run_only_asks_for_what_is_still_missing(self) -> None:
        self.run_backfill(FakeSite(), limit=3)
        self.assertEqual(len(self.rows()), 3)
        site = FakeSite()
        report = self.run_backfill(site)
        self.assertEqual([request[:2] for request in site.requests[1:]], [("2317", "20251121")])
        self.assertEqual((report["status"], report["fetched"], len(self.rows())), ("done", 1, 4))

    def test_a_week_without_that_stock_is_remembered_as_missing_not_asked_again(self) -> None:
        self.run_backfill(FakeSite({("2317", "20261106"): "empty"}))
        self.assertIn({"code": "2317", "date": "2026-11-06", "holders": None}, self.rows())
        site = FakeSite()
        self.run_backfill(site)
        self.assertEqual(site.requests, [None], "只讀一次表單看有哪些週，不再查那一檔")

    def test_stops_at_once_when_the_site_refuses(self) -> None:
        site = FakeSite({("2317", "20261106"): (403, "<html>Forbidden</html>")})
        report = self.run_backfill(site)
        self.assertEqual((report["status"], report["reason"]), ("stopped", "HTTP 403"))
        self.assertEqual(len(site.requests), 3, "被拒絕後不再送任何請求")
        self.assertEqual(len(self.rows()), 1)

    def test_stops_when_the_answer_is_for_a_different_stock_or_week(self) -> None:
        wrong = page(code="2454", roc_date="115年11月06日", token="x", dates=("20261113", "20261106", "20251121"))
        report = self.run_backfill(FakeSite({("2317", "20261106"): (200, wrong)}))
        self.assertEqual(report["status"], "stopped")
        self.assertIn("2454", report["reason"])
        self.assertEqual([row["code"] for row in self.rows()], ["2330"])

    def test_a_long_run_of_empty_answers_is_treated_as_a_silent_refusal(self) -> None:
        # 連續一大串「查無資料」多半是被擋或權杖失效，不是真的都沒資料：停下來，而且這一串不記成「沒有」。
        codes = [str(1101 + index) for index in range(tb.EMPTY_RUN_LIMIT + 2)]
        site = FakeSite({(code, "20261106"): "empty" for code in codes})
        report = self.run_backfill(site, codes=codes)
        self.assertEqual(report["status"], "stopped")
        self.assertFalse(self.progress.exists() and self.rows())
        self.assertEqual(len(site.requests), 1 + tb.EMPTY_RUN_LIMIT)

    def test_one_network_hiccup_is_retried_once_after_a_long_wait_then_it_stops(self) -> None:
        site = FakeSite({("2317", "20261106"): TimeoutError("timed out")})
        report = self.run_backfill(site)
        self.assertEqual((report["status"], report["reason"]), ("stopped", "連線錯誤：TimeoutError"))
        self.assertIn(60.0, self.sleeps)
        self.assertEqual([request and request[:2] for request in site.requests],
                         [None, ("2330", "20261106"), ("2317", "20261106"), None, ("2317", "20261106")])


class ExportTest(unittest.TestCase):
    def test_export_groups_what_was_found_by_week_and_leaves_out_the_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            progress = Path(tmp) / "progress.jsonl"
            tb.backfill(["2330", "2317"], progress, fetch=FakeSite({("2317", "20251121"): "empty"}), sleep=lambda _: None,
                        log=lambda *_: None, skip_dates={"2026-11-13"})
            history = tb.export(progress)
        self.assertEqual(history["schema"], "tdcc-holders-history/v1")
        self.assertEqual(sorted(history["weeks"]), ["2025-11-21", "2026-11-06"])
        self.assertEqual(sorted(history["weeks"]["2026-11-06"]), ["2317", "2330"])
        self.assertEqual(history["weeks"]["2025-11-21"], {"2330": [86.63, 1534, 89.33, 2645, 1797619]})


if __name__ == "__main__":
    unittest.main()
