"""大盤原件：截止月的月檔已存在但缺截止日時，要重抓並換成新版（11-10 大盤頁卡在 11-06 的根因）。"""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("fetch_analysis_data", ROOT / "logs/fetch_analysis_data.py")
fad = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fad)


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.status_code = 200
        self.url = "https://example.test/FMTQIK?date=20261101"
        self.content = json.dumps(payload).encode("utf-8")
        self.text = self.content.decode("utf-8")
        self.headers = {"content-type": "application/json"}

    def json(self) -> dict:
        return json.loads(self.text)


class FakeClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls = 0

    def request(self, method, url, params=None):
        self.calls += 1
        return FakeResponse(self.payload)


def month_payload(*days: str) -> dict:
    return {"stat": "OK", "data": [[day, "1", "1", "1", "1", "1"] for day in days]}


class CurrentMonthRefetchTest(unittest.TestCase):
    def setUp(self) -> None:
        (ROOT / ".tmp").mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / ".tmp")
        self.root = Path(self.tmp.name)
        self.saved = (fad.SNAPSHOT_ROOT, fad.SEED_ROOT)
        fad.SNAPSHOT_ROOT = self.root
        fad.SEED_ROOT = None
        self.target = self.root / "turnover-monthly" / "fmtqik-202611.json"
        self.target.parent.mkdir(parents=True)
        self.target.write_text(json.dumps({"source_url": "x", "data": month_payload("115/11/05", "115/11/06")}),
                               encoding="utf-8")

    def tearDown(self) -> None:
        fad.SNAPSHOT_ROOT, fad.SEED_ROOT = self.saved
        self.tmp.cleanup()

    def fetch(self, client, *, seedable: bool, require_day: str | None):
        return fad.fetch_json(client, url="u", params={}, target=self.target, label="市場成交資訊 202611",
                              records=[], seedable=seedable, require_day=require_day)

    def test_current_month_file_missing_cutoff_is_refetched_and_replaced(self) -> None:
        client = FakeClient(month_payload("115/11/05", "115/11/06", "115/11/09"))
        data = self.fetch(client, seedable=False, require_day="2026-11-09")
        self.assertEqual(client.calls, 1)
        self.assertEqual(data["data"][-1][0], "115/11/09")
        saved = json.loads(self.target.read_text(encoding="utf-8"))
        self.assertEqual(saved["data"]["data"][-1][0], "115/11/09")

    def test_current_month_file_already_covering_cutoff_is_not_refetched(self) -> None:
        client = FakeClient(month_payload("115/11/05"))
        self.fetch(client, seedable=False, require_day="2026-11-06")
        self.assertEqual(client.calls, 0)

    def test_past_month_file_is_never_refetched(self) -> None:
        client = FakeClient(month_payload("115/11/09"))
        self.fetch(client, seedable=True, require_day=None)
        self.assertEqual(client.calls, 0)


if __name__ == "__main__":
    unittest.main()
