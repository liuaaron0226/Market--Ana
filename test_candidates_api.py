"""票07／08 候選 API 已依本人 2026-11-08「自創部分不保留」改版：不再讀短期／長期自創篩選快照。

端點保留以免舊前端報錯：一律回 200、status=retired、空名單，並指向新端點
（/api/screens、/api/rotation、/api/core-holdings）。舊快照即使存在也不讀、不回傳。
"""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import candidate_snapshots
import live_server


def get(url: str) -> tuple[int, dict[str, str], dict[str, object]]:
    try:
        response = urllib.request.urlopen(url, timeout=10)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, dict(response.headers), json.loads(response.read().decode("utf-8"))


def publish_old_style(root: Path, kind: str) -> None:
    result = {
        "status": "ok", "data_date": "2026-11-06", "method_version": f"{kind}-v1.2", "parameters": {},
        "market": "TWSE+TPEX",
        "candidates": [{"code": "8996", "name": "測試股", "baseline": {"date": "2026-11-06", "close": 100.0}}],
        "reconciliation": [], "counts": {}, "errors": [], "market_environment": None,
    }
    snapshot = candidate_snapshots.build_snapshot(kind, result, input_fingerprints={"x": "y"},
                                                  published_at="2026-11-06T10:00:00Z")
    candidate_snapshots.write_snapshot(root, snapshot)


class CandidatesRetiredEndpointTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = live_server.MarketHTTPServer(("127.0.0.1", 0), live_server.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.server.candidate_root = self.root

    def tearDown(self) -> None:
        self.server.candidate_root = None
        self.tmp.cleanup()

    def test_both_kinds_report_retired_with_pointers(self) -> None:
        for kind in ("short", "long"):
            status, headers, payload = get(f"{self.base_url}/api/candidates?kind={kind}")
            self.assertEqual(status, 200, kind)
            self.assertIn("no-store", headers["Cache-Control"])
            self.assertEqual(payload["kind"], kind)
            self.assertEqual(payload["status"], "retired")
            self.assertEqual(payload["candidates"], [])
            self.assertEqual(payload["replacement"], ["/api/screens", "/api/rotation", "/api/core-holdings"])
            self.assertIn("已改版", payload["message"])

    def test_old_snapshots_are_never_served(self) -> None:
        publish_old_style(self.root, "long")
        publish_old_style(self.root, "short")
        for kind in ("short", "long"):
            _, _, payload = get(f"{self.base_url}/api/candidates?kind={kind}")
            self.assertEqual(payload["status"], "retired")
            self.assertEqual(payload["candidates"], [])
            self.assertNotIn("method_version", payload)

    def test_missing_or_bad_kind_still_does_not_error(self) -> None:
        for query in ("", "kind=mid", "kind=long&kind=short"):
            status, _, payload = get(f"{self.base_url}/api/candidates?{query}")
            self.assertEqual(status, 200, query)
            self.assertEqual(payload["status"], "retired")


if __name__ == "__main__":
    unittest.main()
