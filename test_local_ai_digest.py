import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from local_ai_digest import LocalAiDigestError, LocalAiDigestStore


class LocalAiDigestStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "portfolio.sqlite"
        connection = sqlite3.connect(self.path)
        connection.executescript((Path(__file__).parent / "cloud/migrations/0007_ai_daily.sql").read_text(encoding="utf-8"))
        rows = [
            ("2026-11-10", "stock", "2330", {"code": "2330", "status": "收盤 1000。", "action": "續抱", "reason": "量價仍穩定。"}, "generated"),
            ("2026-11-10", "stock", "*", {"count": 1, "requested": 1}, "generated"),
            ("2026-11-11", "stock", "2330", None, "unproduced"),
            ("2026-11-11", "stock", "*", {"count": 0, "requested": 1}, "unproduced"),
        ]
        for day, kind, subject, content, status in rows:
            connection.execute(
                "INSERT INTO ai_daily (data_date,digest_type,subject,content_json,input_sha256,model,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?, ?, ?)",
                (day, kind, subject, None if content is None else json.dumps(content, ensure_ascii=False), "a" * 64, "test", status, day + "T09:00:00Z", day + "T09:00:00Z"),
            )
        connection.commit()
        connection.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_owner_gets_latest_date_without_falling_back_to_yesterday(self):
        payload = LocalAiDigestStore(self.path).get(scope="portfolio", owner=True, codes=["2330"])
        self.assertEqual(payload["data_date"], "2026-11-11")
        self.assertEqual(payload["stocks"]["items"], [{"code": "2330", "generation_status": "unproduced", "content": None}])

    def test_non_owner_portfolio_scope_is_forbidden(self):
        with self.assertRaises(LocalAiDigestError) as caught:
            LocalAiDigestStore(self.path).get(scope="portfolio", owner=False)
        self.assertEqual(caught.exception.status, 403)

    def test_missing_table_returns_empty_today_state(self):
        empty = Path(self.tmp.name) / "empty.sqlite"
        sqlite3.connect(empty).close()
        payload = LocalAiDigestStore(empty).get(scope="portfolio", owner=True)
        self.assertIsNone(payload["data_date"])
        self.assertEqual(payload["stocks"], {"status": "unproduced", "items": []})


if __name__ == "__main__":
    unittest.main()
