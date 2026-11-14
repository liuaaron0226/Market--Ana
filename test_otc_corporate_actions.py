"""櫃買中心除權除息原件：解析、保存、合併與涵蓋區間契約測試。"""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import otc_corporate_actions as oca


FIELDS = [
    "除權息日期", "代號", "名稱", "除權息前收盤價", "除權息參考價", "權值", "息值", "權值+息值",
    "權/息", "漲停價", "跌停價", "開始交易基準價", "減除股利參考價", "現金股利", "每仟股無償配股",
    "現金增資股數", "現金增資認購價", "公開承銷股數", "員工認購股數", "原股東認購股數", "按持股比例仟股認購",
]


def payload(*rows: list[str]) -> bytes:
    return json.dumps({"stat": "ok", "tables": [{"fields": FIELDS, "data": list(rows), "totalCount": len(rows)}]},
                      ensure_ascii=False).encode("utf-8")


ETF_DIVIDEND = ["114/01/02", "00950B", "凱基A級公司債", "14.98", "14.90", "0.000000", "0.080000",
                "0.080000", "除息", "9999.95", "0.01", "14.90", "14.90", "0.08000000", "0.00000000",
                "0", "0.00", "0", "0", "0", "0.00000000"]
COMMON_RIGHT = ["114/01/02", "4109", "加捷生醫", "22.30", "21.58", "0.724292", "0.000000", "0.724292",
                "除權", "24.50", "19.45", "22.30", "22.30", "0.00000000", "0.00000000", "20000000",
                "18.00", "2000000", "2000000", "16000000", "162.04717272"]
COMMON_BOTH = ["114/07/01", "6488", "環球晶", "400.00", "388.00", "2.000000", "10.000000", "12.000000",
               "除權息", "426.50", "349.50", "388.00", "390.00", "10.00000000", "5.00000000", "0",
               "0.00", "0", "0", "0", "0.00000000"]


class ParseTest(unittest.TestCase):
    def test_parses_roc_date_keeps_etf_and_common_stock_and_deduplicates(self) -> None:
        events = oca.parse_exdailyq(payload(ETF_DIVIDEND, COMMON_RIGHT, COMMON_BOTH, ETF_DIVIDEND),
                                    source="TPEx-exDailyQ-2025.json")
        self.assertEqual([(e["date"], e["code"], e["type"]) for e in events], [
            ("2025-01-02", "00950B", "除息"),
            ("2025-01-02", "4109", "除權"),
            ("2025-07-01", "6488", "除權息"),
        ])
        self.assertEqual(events[0]["value"], 0.08)
        self.assertEqual(events[1]["value"], 0.724292)
        self.assertEqual(events[2]["value"], 12.0)
        self.assertEqual(events[2]["source"], "TPEx-exDailyQ-2025.json")
        self.assertEqual(set(events[0]), {
            "date", "code", "type", "close_before", "reference", "value", "limit_up", "limit_down", "source",
        })


class _Response:
    def __init__(self, content: bytes, *, status: int = 200, content_type: str = "application/json;charset=UTF-8",
                 url: str = "https://www.tpex.org.tw/exDailyQ") -> None:
        self.status, self.content, self.content_type, self.url = status, content, content_type, url


class FetchAndMergeTest(unittest.TestCase):
    def test_fetch_saves_sha_atomically_waits_five_seconds_and_exposes_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            calls: list[tuple[str, str]] = []
            sleeps: list[float] = []

            def get(session, url, params):
                calls.append((params["startDate"], params["endDate"]))
                row = ETF_DIVIDEND if params["startDate"].startswith("2025") else COMMON_RIGHT
                return _Response(payload(row), url=url + "?response=json")

            periods = [("2024", "2024-01-01", "2024-12-31"), ("2025", "2025-01-01", "2025-12-31")]
            result = oca.fetch_periods(out, periods, get=get, sleep=sleeps.append)
            self.assertEqual(result["fetched"], 2)
            self.assertEqual(calls, [("2024/01/01", "2024/12/31"), ("2025/01/01", "2025/12/31")])
            self.assertEqual(sleeps, [5.0])
            self.assertFalse(list(out.rglob("*.partial")))
            raw = out / "TPEx-exDailyQ-2025.json"
            sidecar = raw.with_name(raw.name + ".sha256").read_text(encoding="ascii").split()
            self.assertEqual(sidecar, [hashlib.sha256(raw.read_bytes()).hexdigest(), raw.name])
            self.assertTrue(oca.coverage_covers(oca.verified_coverage(out), "2024-06-01", "2025-11-30"))
            self.assertFalse(oca.coverage_covers(oca.verified_coverage(out), "2023-12-31", "2025-11-30"))

    def test_valid_orphan_raw_is_recovered_into_meta_without_refetch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            content = payload(ETF_DIVIDEND)
            raw = out / "TPEx-exDailyQ-2025.json"
            raw.write_bytes(content)
            digest = hashlib.sha256(content).hexdigest()
            raw.with_name(raw.name + ".sha256").write_text(f"{digest}  {raw.name}\n", encoding="ascii")
            (out / "meta.json").write_text(json.dumps({"files": []}), encoding="utf-8")
            calls = []
            result = oca.fetch_periods(out, [("2025", "2025-01-01", "2025-12-31")],
                                       get=lambda *_: calls.append(1), sleep=lambda _: None)
            self.assertEqual(result["existing"], 1)
            self.assertEqual(calls, [])
            self.assertTrue(oca.coverage_covers(oca.verified_coverage(out), "2025-01-01", "2025-12-31"))
            entry = json.loads((out / "meta.json").read_text(encoding="utf-8"))["files"][0]
            self.assertTrue(entry["recovered_existing"])
            self.assertEqual(entry["rows"], 1)

    def test_http_or_captcha_response_stops_without_retry_or_raw_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            calls = 0

            def get(session, url, params):
                nonlocal calls
                calls += 1
                return _Response(b"<html>captcha</html>", content_type="text/html")

            result = oca.fetch_periods(Path(tmp), [("2025", "2025-01-01", "2025-12-31")],
                                       get=get, sleep=lambda _: None)
            self.assertEqual(calls, 1)
            self.assertIn("Content-Type", result["stopped"]["reason"])
            self.assertFalse(list(Path(tmp).glob("TPEx-exDailyQ-*.json")))

    def test_rebuild_events_updates_shared_file_without_partial_or_losing_listed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "raw"
            events_path = root / "events.json"
            listed = {"date": "2025-01-02", "code": "2330", "type": "除息", "close_before": 1000.0,
                      "reference": 995.0, "value": 5.0, "limit_up": 1094.0, "limit_down": 896.0,
                      "source": "TWT49U-202501.json.gz"}
            events_path.write_text(json.dumps([listed], ensure_ascii=False), encoding="utf-8")
            oca.fetch_periods(raw, [("2025", "2025-01-01", "2025-12-31")],
                              get=lambda *_: _Response(payload(ETF_DIVIDEND)), sleep=lambda _: None)
            result = oca.rebuild_events(events_path, raw)
            merged = json.loads(events_path.read_text(encoding="utf-8"))
            self.assertEqual(result, {"listed_or_existing": 1, "otc": 1, "total": 2})
            self.assertEqual({e["code"] for e in merged}, {"2330", "00950B"})
            self.assertFalse(events_path.with_name(events_path.name + ".partial").exists())

    def test_cash_dividends_of_same_day_rights_and_dividend_come_from_the_verified_raw_files(self) -> None:
        # 還原價只還原配股那部分：需要「除權息」同日事件的每股現金股利。純除息、純除權不列。
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "raw"
            oca.fetch_periods(raw, [("2025", "2025-01-01", "2025-12-31")],
                              get=lambda *_: _Response(payload(ETF_DIVIDEND, COMMON_RIGHT, COMMON_BOTH)),
                              sleep=lambda _: None)
            self.assertEqual(oca.cash_dividends(raw), {"6488|2025-07-01": 10.0})
            (raw / "TPEx-exDailyQ-2025.json").write_bytes(payload(COMMON_BOTH))  # 原件被改動：SHA 不符就不採用
            with self.assertRaises(ValueError):
                oca.cash_dividends(raw)

    def test_merge_replaces_only_prior_tpex_events_and_never_changes_listed_events(self) -> None:
        listed = {"date": "2025-01-02", "code": "2330", "type": "除息", "close_before": 1000.0,
                  "reference": 995.0, "value": 5.0, "limit_up": 1094.0, "limit_down": 896.0,
                  "source": "TWT49U-202501.json.gz"}
        old_otc = {**oca.parse_exdailyq(payload(ETF_DIVIDEND), source="TPEx-exDailyQ-old.json")[0], "value": 9.0}
        new_otc = oca.parse_exdailyq(payload(ETF_DIVIDEND, COMMON_RIGHT), source="TPEx-exDailyQ-2025.json")
        merged = oca.merge_events([listed, old_otc], new_otc)
        self.assertEqual(next(e for e in merged if e["code"] == "2330"), listed)
        self.assertEqual(len(merged), 3)
        self.assertEqual(next(e for e in merged if e["code"] == "00950B")["value"], 0.08)


if __name__ == "__main__":
    unittest.main()
