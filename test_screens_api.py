"""G3：本機籌碼選股／產業資金流向／長期核心持有 API；只讀封存快照，不在請求時即時計算選股。"""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import frozen_history
import live_server
import market_screens_publish as msp
from test_market_screens_publish import BANNED, inputs

# 「過期」判斷比的是行情庫最新一天；封存批次固定 2026-11-06，行情庫也凍結在同一天，
# 不隨每日增量漂移（T1，2026-11-09）。
FROZEN_AS_OF = "2026-11-06"
_restore_frozen = None


def setUpModule() -> None:
    global _restore_frozen
    _restore_frozen = frozen_history.freeze_live_server(FROZEN_AS_OF)


def tearDownModule() -> None:
    if _restore_frozen is not None:
        _restore_frozen()


def get(url: str) -> tuple[int, dict[str, str], dict[str, object]]:
    try:
        response = urllib.request.urlopen(url, timeout=30)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, dict(response.headers), json.loads(response.read().decode("utf-8"))


class ScreensEndpointTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = live_server.MarketHTTPServer(("127.0.0.1", 0), live_server.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.server.screen_root = self.root

    def tearDown(self) -> None:
        self.server.screen_root = None
        self.tmp.cleanup()

    def publish(self) -> None:
        msp.publish(self.root, data_date="2026-11-06", published_at="2026-11-06T10:00:00Z", **inputs())

    def test_catalog_lists_tabs_with_professional_names_and_states_before_publish(self) -> None:
        status, headers, payload = get(f"{self.base}/api/screens")
        self.assertEqual(status, 200)
        self.assertIn("no-store", headers["Cache-Control"])
        self.assertEqual(payload["status"], "not_ready")
        keys = [tab["key"] for tab in payload["tabs"]]
        self.assertEqual(keys[:4], ["foreign_net_rank", "trust_net_rank", "foreign_streak", "trust_streak"])
        self.assertEqual(len(keys), 9)  # 人工精選名單不放：原站無公式，不是可重建的選股
        text = json.dumps(payload, ensure_ascii=False)
        for banned in BANNED:
            self.assertNotIn(banned, text, banned)

    def test_rank_returns_published_batch(self) -> None:
        self.publish()
        status, _, payload = get(f"{self.base}/api/screens?key=foreign_net_rank&days=1&side=buy")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["state"], "available")
        self.assertEqual(payload["data_date"], "2026-11-06")
        self.assertEqual(payload["display_name"], "外資淨買超排行")
        self.assertEqual([row["code"] for row in payload["items"]], ["6488", "1101"])
        self.assertTrue(payload["items"][0]["rotation_star"])
        self.assertTrue(payload["snapshot_sha256"])

    def test_only_inflow_filter_is_applied_from_stars(self) -> None:
        self.publish()
        _, _, payload = get(f"{self.base}/api/screens?key=foreign_net_rank&days=1&side=buy&only_inflow=1")
        self.assertEqual([row["code"] for row in payload["items"]], ["6488"])
        self.assertTrue(payload["only_inflow_applied"])

    def test_unknown_and_unavailable_pages_carry_no_numbers(self) -> None:
        self.publish()
        _, _, unknown = get(f"{self.base}/api/screens?key=rotation_relative_value")
        self.assertEqual(unknown["state"], "unknown")
        self.assertEqual(unknown["items"], [])
        self.assertTrue(unknown["display_conditions"])
        _, _, unavailable = get(f"{self.base}/api/screens?key=broker_concentration")
        self.assertEqual(unavailable["state"], "unavailable")
        self.assertEqual(unavailable["items"], [])

    def test_unpublished_variant_is_not_ready(self) -> None:
        _, _, payload = get(f"{self.base}/api/screens?key=foreign_net_rank&days=1&side=buy")
        self.assertEqual(payload["status"], "not_ready")
        self.assertEqual(payload["items"], [])

    def test_bad_params_are_rejected(self) -> None:
        for query in ("key=nope", "key=foreign_net_rank&days=2", "key=foreign_net_rank&side=hold",
                      "key=foreign_streak&days=5", "key=curated_list", "key=a&key=b"):
            status, _, payload = get(f"{self.base}/api/screens?{query}")
            self.assertEqual(status, 400, query)
            self.assertEqual(payload["error"], "bad-request")

    def test_tampered_batch_is_refused(self) -> None:
        self.publish()
        path = self.root / "screen-foreign_net_rank-1d-buy" / "2026-11-06.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["result"]["items"][0]["code"] = "9999"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        status, _, payload = get(f"{self.base}/api/screens?key=foreign_net_rank&days=1&side=buy")
        self.assertEqual(status, 500)
        self.assertEqual(payload["error"], "snapshot-integrity")

    def test_stale_when_newer_market_data_exists(self) -> None:
        msp.publish(self.root, data_date="2026-11-05", published_at="2026-11-05T10:00:00Z", **inputs())
        _, _, payload = get(f"{self.base}/api/screens?key=foreign_net_rank&days=1&side=buy")
        self.assertEqual(payload["status"], "stale")
        self.assertEqual(payload["latest_market_data_date"], "2026-11-06")

    def test_rotation_endpoint(self) -> None:
        _, _, empty = get(f"{self.base}/api/rotation")
        self.assertEqual(empty["status"], "not_ready")
        self.publish()
        status, _, payload = get(f"{self.base}/api/rotation")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["title"], "產業資金流向")
        self.assertEqual(payload["latest"]["date"], "2026-11-06")
        self.assertEqual(payload["group_definition"]["kind"], "official_industry")
        self.assertNotIn("snapshots", payload)  # 只回最新一天，時間軸留給後續切片

    def test_stock_membership(self) -> None:
        self.publish()
        _, _, payload = get(f"{self.base}/api/screens/membership?code=6488")
        self.assertEqual(payload["code"], "6488")
        ranks = {(m["key"], m["days"], m["side"]): m["rank"] for m in payload["memberships"]}
        self.assertEqual(ranks[("foreign_net_rank", 1, "buy")], 1)
        self.assertEqual(payload["industry"]["name"], "半導體業")
        self.assertEqual(payload["industry"]["quadrant"], "leading")
        self.assertEqual(payload["industry"]["label"], "資金領先")
        _, _, none = get(f"{self.base}/api/screens/membership?code=2330")
        self.assertEqual(none["memberships"], [])
        status, _, _ = get(f"{self.base}/api/screens/membership?code=../x")
        self.assertEqual(status, 400)

    def test_core_holdings(self) -> None:
        status, headers, payload = get(f"{self.base}/api/core-holdings")
        self.assertEqual(status, 200)
        self.assertIn("no-store", headers["Cache-Control"])
        self.assertEqual([h["code"] for h in payload["holdings"]], ["0050", "2330"])
        self.assertEqual(payload["holdings"][0]["role"], "核心持有")
        self.assertTrue(payload["notes"])


class CandidatesRetiredTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = live_server.MarketHTTPServer(("127.0.0.1", 0), live_server.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def test_default_screen_root_is_separate_from_candidate_root(self) -> None:
        self.assertEqual(live_server.SCREEN_SNAPSHOT_ROOT, live_server.ROOT / "data/market-screen-snapshots")


if __name__ == "__main__":
    unittest.main()
