"""公開的執行紀錄（public_log.py）：朋友的 email、站主個股的代號與判斷、模型回的字都不能出現在印出來的那一份。"""
import json
import tempfile
import unittest
from pathlib import Path

import public_log as pl

OWNER_CODES = ("2330", "6488")
EMAILS = ("zoe@example.com", "amy@example.com")


def shown(value) -> str:
    return json.dumps(value, ensure_ascii=False)


class PublicLogTests(unittest.TestCase):
    def assert_nothing_private(self, value, *more):
        text = shown(value)
        for secret in (*OWNER_CODES, *EMAILS, *more):
            self.assertNotIn(secret, text)

    def test_friends_get_stable_labels_in_email_order(self):
        self.assertEqual(pl.friend_labels(EMAILS), {"amy@example.com": "friend-01", "zoe@example.com": "friend-02"})
        self.assertEqual(pl.friend_labels(reversed(EMAILS)), pl.friend_labels(EMAILS))

    def test_items_split_into_market_ones_listed_and_stock_ones_counted(self):
        entries = ["market:above_ma20:2026-11-19", "2330:above_ma20:2026-11-19",
                   {"id": "market:news:abc:2026-11-19", "note": "成立：模型寫的理由", "evidence": {"title": "標題"}},
                   {"id": "6488:below_ma20:2026-11-19", "note": "否定"}]
        split = pl.split_items(entries, ("note",))
        self.assertEqual(split["stocks"], 2)
        self.assertEqual(split["market"], ["market:above_ma20:2026-11-19",
                                           {"id": "market:news:abc:2026-11-19", "note_chars": 9, "evidence": {"title": "標題"}}])
        self.assert_nothing_private(split, "模型寫的理由")
        self.assertEqual(pl.split_items(None), {"market": [], "stocks": 0})

    def test_market_news_report_hides_friends_stock_items_and_model_text(self):
        report = {"status": "ok", "run_id": "2026-11-19-close", "items": 12, "model": "ok", "model_note": "模型回的一段話",
                  "friends": {"zoe@example.com": "ok 判了三則", "amy@example.com": "1/2 quota 今天額度用完"},
                  "sources": {"cna": {"status": "ok"}},
                  "diagnosis": {"calls": {"items1": {"status": "bad_json", "attempts": 2, "note": "模型回的字" * 20,
                                                     "text_retry": "prepare：不是如果就"},
                                          "watch1": {"status": "ok", "attempts": 1, "sent": 4}},
                                "items_kept": 10,
                                "watch": {"offered": 6, "unknown_ids": ["2330:nope", "market_x"], "picked": 3,
                                          "created": ["market:above_ma20:2026-11-19", "2330:above_ma20:2026-11-19"],
                                          "closed_for_room": ["6488:below_ma20:2026-11-10"]}}}
        public = pl.market_news(report)
        self.assert_nothing_private(public, "模型回的", "判了三則", "額度用完")
        self.assertEqual(public["friends"], {"friend-01": "1/2 quota", "friend-02": "ok"})
        self.assertEqual(public["model_note_chars"], 7)
        self.assertEqual(public["diagnosis"]["calls"]["items1"],
                         {"status": "bad_json", "attempts": 2, "note_chars": 100, "text_retry": "prepare"})
        self.assertEqual(public["diagnosis"]["calls"]["watch1"], {"status": "ok", "attempts": 1, "sent": 4})
        self.assertEqual(public["diagnosis"]["watch"],
                         {"offered": 6, "unknown_ids": 2, "picked": 3,
                          "created": {"market": ["market:above_ma20:2026-11-19"], "stocks": 1},
                          "closed_for_room": {"market": [], "stocks": 1}})
        self.assertEqual((public["run_id"], public["items"], public["sources"]), ("2026-11-19-close", 12, {"cna": {"status": "ok"}}))
        self.assertEqual(report["friends"]["zoe@example.com"], "ok 判了三則", "原本的報告不動")

    def test_impact_watch_report_keeps_market_items_and_only_counts_the_owner_stocks(self):
        report = {"status": "ok", "data_date": "2026-11-19", "checked": 2, "watching": 1, "expired": [], "errors": [],
                  "met": [{"id": "market:above_ma20:2026-11-18", "note": "成立：加權站上月線。"}], "failed": [],
                  "stocks": {"checked": 3, "met": [{"id": "2330:above_ma20:2026-11-18", "note": "成立：台積電站上月線。"}],
                             "failed": [], "expired": ["6488:below_ma20:2026-11-01"], "removed": [], "watching": 1,
                             "errors": [], "facts": 2, "stocks": 2},
                  "plain": {"picked": 1, "created": ["market:foreign_turn_buy:2026-11-19"], "closed_for_room": []},
                  "dry_run": False}
        public = pl.impact_watch(report)
        self.assert_nothing_private(public, "台積電")
        self.assertEqual(public["met"], {"market": [{"id": "market:above_ma20:2026-11-18", "note": "成立：加權站上月線。"}], "stocks": 0})
        self.assertEqual(public["stocks"], {"checked": 3, "met": 1, "failed": 0, "expired": 1, "removed": 0, "watching": 1,
                                            "errors": 0, "facts": 2, "stocks": 2})
        self.assertEqual(public["plain"]["created"], {"market": ["market:foreign_turn_buy:2026-11-19"], "stocks": 0})
        self.assertEqual(pl.impact_watch({"status": "ok", "stocks": {"error": "RuntimeError"}})["stocks"], {"error": "RuntimeError"})
        self.assertEqual(pl.impact_watch({"status": "skipped", "reason": "大盤日 K 不足"}), {"status": "skipped", "reason": "大盤日 K 不足"})

    def test_impact_watch_news_report_keeps_what_the_usage_step_reads(self):
        report = {"status": "ok", "data_date": "2026-11-20", "checked": 2, "with_news": 2, "model_calls": 2, "dry_run": False,
                  "model": "gemini-3.8-flash", "effort": "high", "watching": 0,
                  "calls": [{"status": "model_failed", "note": "模型回的字"}, {"status": "ok"}],
                  "met": [{"id": "market:news:abc:2026-11-19", "note": "成立：清單公布了。", "evidence": {"title": "標題", "link": "https://x"}}],
                  "failed": [{"id": "2330:news:def:2026-11-19", "note": "否定：沒有發生。", "evidence": {"title": "台積電的新聞"}}],
                  "expired": ["6488:news:ghi:2026-11-01"], "invalid": [{"id": "2330:news:jkl:2026-11-19", "problem": "missing"}]}
        public = pl.impact_watch_news(report)
        self.assert_nothing_private(public, "模型回的字", "清單公布了", "台積電")
        self.assertEqual((public["model_calls"], public["dry_run"], public["model"]), (2, False, "gemini-3.8-flash"))
        self.assertEqual(public["calls"], [{"status": "model_failed", "note_chars": 5}, {"status": "ok"}])
        self.assertEqual(public["met"]["market"][0]["evidence"]["title"], "標題")
        self.assertEqual((public["failed"], public["expired"], public["invalid"]),
                         ({"market": [], "stocks": 1}, {"market": [], "stocks": 1}, {"market": [], "stocks": 1}))

    def test_watch_run_summaries_only_count(self):
        summary = {"edition": "evening", "base_date": "2026-11-19", "dry_run": False, "outcome": "written",
                   "reconciliation": {"hit": 1}, "backfilled_misses": [{"code": "2330", "base_date": "2026-11-18"}],
                   "stocks": [{"code": "2330", "outcome": "written", "kind": "judgment", "status": "ready", "miss_reason": None},
                              {"code": "6488", "outcome": "written", "kind": "miss", "status": None, "miss_reason": "model_failed"}]}
        public = pl.watch_judge(summary)
        self.assert_nothing_private(public)
        self.assertEqual(public["stocks"], {"count": 2, "outcomes": {"written": 2}, "kinds": {"judgment": 1, "miss": 1},
                                            "statuses": {"ready": 1, "none": 1}, "missed": 1})
        self.assertEqual((public["backfilled_misses"], public["edition"], public["reconciliation"]), (1, "evening", {"hit": 1}))
        nominated = pl.watch_nominate(list(OWNER_CODES), [{"code": "2330", "outcome": "nominated"}, {"code": "6488", "outcome": "unchanged"}])
        self.assertEqual(nominated, {"candidates": 2, "outcomes": {"nominated": 1, "unchanged": 1}})

    def test_stock_news_sources_are_merged_per_source(self):
        report = {"status": "ok", "stocks": 3, "stored": 9,
                  "sources": {"gnews:2330": {"status": "ok"}, "gnews:6488": {"status": "error", "reason": "HTTP 429"},
                              "gnews-backfill:2330": {"status": "ok"}, "cna": {"status": "ok"}, "mops": {"status": "ok"}}}
        public = pl.watch_news_daily(report)
        self.assert_nothing_private(public)
        self.assertEqual(public["sources"], {"gnews": {"ok": 1, "error": 1, "reasons": {"HTTP 429": 1}},
                                             "gnews-backfill": {"ok": 1, "error": 0},
                                             "cna": {"ok": 1, "error": 0}, "mops": {"ok": 1, "error": 0}})
        self.assertEqual(pl.watch_news_daily({"status": "not-migrated"}), {"status": "not-migrated"})

    def test_stock_news_failures_say_why_without_the_search_or_the_news(self):
        url = "https://news.google.com/rss/search?q=%22台積電%22+(2330+OR+股)"
        reasons = ["HTTP 503", "來源要求放慢，這一輪沒抓", "來源要求放慢，這一輪沒抓",
                   f"ConnectionError: HTTPSConnectionPool(host='news.google.com'): Max retries exceeded with url: {url}",
                   "格式不對：不是 XML（syntax error: line 1）", "發布時間看不懂：'台積電 2330'", "台積電 2330 怪訊息", None]
        report = {"status": "ok", "stocks": 8, "stored": 0,
                  "sources": {f"gnews:{2330 + n}": {"status": "error", **({"reason": reason} if reason else {})}
                              for n, reason in enumerate(reasons)}}
        public = pl.watch_news_daily(report)
        self.assert_nothing_private(public, "台積電", "news.google.com/rss/search", "syntax error")
        self.assertEqual(public["sources"], {"gnews": {"ok": 0, "error": 8, "reasons": {
            "HTTP 503": 1, "skipped-after-slow-down": 2, "ConnectionError": 1, "format-changed": 2, "other": 2}}})

    def test_the_full_report_goes_to_the_private_file(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "impact-watch" / "2026-11-19.json"
            self.assertTrue(pl.write_private(target, {"stocks": {"met": ["2330:above_ma20:2026-11-18"]}}))
            self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["stocks"]["met"], ["2330:above_ma20:2026-11-18"])
            self.assertFalse(pl.write_private(None, {"a": 1}), "沒給路徑就不寫")
            blocker = Path(folder) / "file"
            blocker.write_text("x", encoding="utf-8")
            self.assertFalse(pl.write_private(blocker / "inside.json", {"a": 1}), "寫不成不丟例外")


class PublicWorkflowTests(unittest.TestCase):
    """排程檔：站主的 email 只從 secret 來；有站主個股內容的紀錄不進步驟摘要、不放 artifact。"""

    def source(self, name):
        return (Path(__file__).parent / ".github/workflows" / name).read_text(encoding="utf-8")

    def test_the_owner_email_is_a_secret_in_every_workflow(self):
        for path in sorted((Path(__file__).parent / ".github/workflows").glob("*.yml")):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("vars.OWNER_EMAIL", text, path.name)
            self.assertNotRegex(text, r"[\w.+-]+@(gmail|yahoo|hotmail|outlook)\.", path.name)

    def test_the_watch_ledger_keeps_its_records_in_the_ledger_not_in_an_artifact(self):
        text = self.source("watch-ledger.yml")
        self.assertNotIn("actions/upload-artifact", text)
        self.assertNotIn('cat "$RUNNER_TEMP/watch-run/run.json"', text)
        self.assertIn('--log-output "$LEDGER/runs/$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT-nominate.json"', text)
        self.assertIn("public_log.watch_judge", text)

    def test_the_daily_update_artifact_leaves_out_the_private_folders(self):
        text = self.source("daily-update.yml")
        self.assertIn("path: data/daily-update-log/*.json", text)
        self.assertNotIn("path: data/daily-update-log/\n", text)
        self.assertIn('--private-report "data/daily-update-log/impact-watch/$DAY.json"', text)
        self.assertNotIn('c.get("one_line"))', text, "模型寫的一句話不進步驟摘要")


if __name__ == "__main__":
    unittest.main()
