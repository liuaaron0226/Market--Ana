"""票 05／06：官方上市與上櫃普通股名錄到既有價量資格、本機搜尋的公開契約。"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import frozen_history
import live_server
from listed_catalog import ListedSecurity, SearchPage, load_listed_catalog


ROOT = Path(__file__).resolve().parent
RAW = ROOT / "data/listed-catalog/raw/twse-isin-listed-20261108.html"
META = ROOT / "data/listed-catalog/raw/twse-isin-listed-20261108.meta.json"
# 契約數字（1102 有 2640 根、5906 最新列停牌…）以 2026-11-06 收盤為準；行情庫每日增量後，
# 測試改讀凍結在該日的副本，不隨正式資料庫最新日期漂移（T1，2026-11-09）。
FROZEN_AS_OF = "2026-11-06"
HISTORY_DB: Path = frozen_history.STOCK_HISTORY_DB
_restore_frozen = None


def setUpModule() -> None:
    global _restore_frozen, HISTORY_DB
    _restore_frozen = frozen_history.freeze_live_server(FROZEN_AS_OF)
    HISTORY_DB = live_server.STOCK_HISTORY_DB


def tearDownModule() -> None:
    if _restore_frozen is not None:
        _restore_frozen()


def get_json(url: str) -> tuple[int, dict[str, object]]:
    try:
        response = urllib.request.urlopen(url, timeout=10)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, json.loads(response.read().decode("utf-8"))


class ListedSearchEndpointContractTest(unittest.TestCase):
    """V2 前的全名錄契約（對應雲端 build 的 --all-securities）；大量股母體另見 LiquidUniverseEndpointTest。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = live_server.MarketHTTPServer(("127.0.0.1", 0), live_server.Handler)
        cls.server.universe_enabled = False
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def search(self, **params: object) -> tuple[int, dict[str, object]]:
        query = urllib.parse.urlencode(params)
        return get_json(f"{self.base_url}/api/stocks/search?{query}")

    def test_exact_code_uses_cloud_contract_and_excludes_etf(self) -> None:
        status, payload = self.search(q="1102")

        self.assertEqual(status, 200)
        self.assertEqual(payload["source_date"], "2026-11-08")
        self.assertIsNone(payload["next_cursor"])
        self.assertEqual(payload["items"], [{
            "code": "1102",
            "name": "亞泥",
            "market": "上市",
            "security_type": "普通股",
            "latest_bar_date": "2026-11-06",
            "available_bars": 2640,
            "analysis_status": "available",
            "data_date": "2026-11-06",
            "analysis_available": True,
            "source_date": "2026-11-08",
        }])

        _, etf_payload = self.search(q="0050")
        self.assertEqual(etf_payload["items"], [])
        self.assertEqual(etf_payload["source_date"], "2026-11-08")

    def test_full_and_partial_name_order_is_stable_across_cursor_pages(self) -> None:
        status, full_name = self.search(q="台積電", limit=1)
        self.assertEqual(status, 200)
        self.assertEqual([item["code"] for item in full_name["items"]], ["2330"])
        self.assertIsNone(full_name["next_cursor"])

        _, first = self.search(q="積電", limit=1)
        _, second = self.search(q="積電", limit=1, cursor=first["next_cursor"])
        self.assertEqual([item["code"] for item in first["items"]], ["2330"])
        self.assertEqual(first["next_cursor"], "1")
        self.assertEqual([item["code"] for item in second["items"]], ["6770"])
        self.assertIsNone(second["next_cursor"])

    def test_stale_analysis_remains_available_but_exposes_old_data_date(self) -> None:
        stale = ListedSecurity(
            code="1234",
            name="測試舊資料",
            market="上市",
            security_type="普通股",
            isin="TW0001234000",
            listed_date="2000/01/01",
            industry="測試",
            cfi_code="ESVUFR",
            source_url="https://example.invalid",
            source_date="2026-11-08",
            available_bars=300,
            latest_bar_date="2026-10-30",
            analysis_eligible=True,
        )

        class StaleCatalog:
            def search(self, query: str, limit: int = 20, cursor: str | None = None) -> SearchPage:
                del query, limit, cursor
                return SearchPage((stale,), 1, None)

        original = live_server._LISTED_CATALOG
        live_server._LISTED_CATALOG = StaleCatalog()
        try:
            status, payload = self.search(q="1234")
        finally:
            live_server._LISTED_CATALOG = original

        self.assertEqual(status, 200)
        self.assertEqual(payload["items"][0]["analysis_status"], "stale")
        self.assertTrue(payload["items"][0]["analysis_available"])
        self.assertEqual(payload["items"][0]["data_date"], "2026-10-30")

    def test_suspended_latest_row_is_unavailable_like_cloud_build(self) -> None:
        # 5906 最新一列為停牌：雲端建置標 unavailable，本機不能說成「可分析／過期」。
        status, payload = self.search(q="5906")

        self.assertEqual(status, 200)
        item = payload["items"][0]
        self.assertEqual(item["analysis_status"], "unavailable")
        self.assertFalse(item["analysis_available"])
        self.assertEqual(item["data_date"], "2026-11-04")

    def test_unavailable_stock_page_does_not_expand_analysis_values(self) -> None:
        # 搜尋說「無法分析」的停牌股，個股 API 也不能回分析數值（雲端同檔為 404 not-in-cloud）。
        status, payload = get_json(f"{self.base_url}/api/stocks/5906/analysis")

        self.assertEqual(status, 200)
        self.assertEqual(payload["stock_state"], "suspended")
        self.assertEqual(payload["analysis_status"], "unavailable")
        self.assertTrue(payload["qa"]["blocking"])
        self.assertNotIn("quote", payload)
        self.assertNotIn("levels", payload)

    def test_stock_analysis_serves_contract_v2_without_presets_or_scores(self) -> None:
        status, payload = get_json(f"{self.base_url}/api/stocks/2330/analysis")

        self.assertEqual(status, 200)
        self.assertEqual(payload["schema"], "stock-analysis/v2")
        for removed in ("preset", "available_presets", "score", "score_complete", "strategies",
                        "dimensions", "risk_flags", "trend"):
            self.assertNotIn(removed, payload)
        # R1：交易規則改為頂層欄位（原站明示不提供），不再放在 undisclosed。
        self.assertEqual(payload["trading_rules"]["status"], "not_provided_by_source")
        self.assertNotIn("trading_rules", payload["undisclosed"])
        text = json.dumps(payload, ensure_ascii=False)
        for banned in ("2TON", "漲停寶寶", "甜甜價", "甜價", "FIT-V1"):
            self.assertNotIn(banned, text)

    def test_stock_analysis_rejects_preset_parameter_like_cloud(self) -> None:
        for query in ("preset=2TON-FIT-V1", "preset=MOMENTUM-V1", "preset="):
            with self.subTest(query=query):
                status, payload = get_json(f"{self.base_url}/api/stocks/2330/analysis?{query}")
                self.assertEqual(status, 400)
                self.assertIn("分析方案已移除", payload["message"])

    def test_every_listed_status_matches_cloud_build_rule(self) -> None:
        # 本機搜尋與雲端 seed 必須對同一快照給出同一狀態，避免本機說可分析、雲端卻沒有分析列。
        build = live_server.cloud_build_module()
        data_date, suspended = build._history_state()
        securities = build._all_catalog_securities()

        mismatches = [
            security.code
            for security in securities
            if live_server.catalog_analysis_status(security) != build.analysis_status(
                security.available_bars,
                security.latest_bar_date,
                suspended.get(security.code, False),
                data_date,
            )
        ]

        self.assertEqual(len(securities), 1058)
        self.assertEqual(mismatches, [])

    def test_unknown_analysis_exposes_not_found_state_without_values(self) -> None:
        status, payload = get_json(f"{self.base_url}/api/stocks/9999/analysis")
        self.assertEqual(status, 404)
        self.assertEqual(payload["stock_state"], "not_found")
        self.assertTrue(payload["qa"]["blocking"])
        self.assertNotIn("quote", payload)

    def test_invalid_limit_and_cursor_are_rejected(self) -> None:
        for params in ({"q": "台", "limit": 51}, {"q": "台", "cursor": "oops"}):
            status, payload = self.search(**params)
            self.assertEqual(status, 400)
            self.assertEqual(payload["error"], "bad-request")

    # 票06：上櫃併入同一搜尋契約；排序與雲端 gate 的 SQL 相同（精確代號 → 完整名稱 → 代號）。
    def test_otc_exact_code_uses_same_contract_with_market(self) -> None:
        status, payload = self.search(q="6488")

        self.assertEqual(status, 200)
        self.assertEqual(payload["source_date"], "2026-11-08")
        self.assertEqual(payload["items"], [{
            "code": "6488",
            "name": "環球晶",
            "market": "上櫃",
            "security_type": "普通股",
            "latest_bar_date": "2026-11-06",
            "available_bars": 250,
            "analysis_status": "available",
            "data_date": "2026-11-06",
            "analysis_available": True,
            "source_date": "2026-11-08",
        }])

    def test_partial_name_spans_both_markets_in_stable_code_order(self) -> None:
        _, payload = self.search(q="環球")
        self.assertEqual(
            [(item["code"], item["market"]) for item in payload["items"]],
            [("4441", "上市"), ("6488", "上櫃")],
        )

        _, full_name = self.search(q="環球晶")
        self.assertEqual([item["code"] for item in full_name["items"]], ["6488"])

        _, first = self.search(q="晶", limit=5)
        _, second = self.search(q="晶", limit=5, cursor=first["next_cursor"])
        codes = [item["code"] for item in first["items"] + second["items"]]
        self.assertEqual(codes, sorted(codes))
        self.assertEqual(first["next_cursor"], "5")
        self.assertIn("上櫃", {item["market"] for item in first["items"] + second["items"]})

    def test_every_two_market_status_matches_cloud_build_rule(self) -> None:
        build = live_server.cloud_build_module()
        data_date, suspended = build._history_state()
        securities = build.catalog_securities()

        mismatches = [
            security.code
            for security in securities
            if live_server.catalog_analysis_status(security) != build.analysis_status(
                security.available_bars,
                security.latest_bar_date,
                suspended.get(security.code, False),
                data_date,
            )
        ]

        self.assertEqual(len(securities), 1955)
        self.assertEqual(mismatches, [])

    def test_otc_stale_unavailable_and_insufficient_follow_listed_rules(self) -> None:
        _, stale = self.search(q="4804")
        self.assertEqual(stale["items"][0]["market"], "上櫃")
        self.assertEqual(stale["items"][0]["analysis_status"], "stale")
        self.assertTrue(stale["items"][0]["analysis_available"])
        self.assertEqual(stale["items"][0]["data_date"], "2026-04-13")

        _, stale_page = get_json(f"{self.base_url}/api/stocks/4804/analysis")
        self.assertEqual(stale_page["stock_state"], "stale")
        self.assertEqual(stale_page["data_date"], "2026-04-13")
        self.assertIn("levels", stale_page)

        status, suspended_page = get_json(f"{self.base_url}/api/stocks/2064/analysis")
        self.assertEqual(status, 200)
        self.assertEqual(suspended_page["analysis_status"], "unavailable")
        self.assertEqual(suspended_page["stock_state"], "suspended")
        self.assertNotIn("levels", suspended_page)
        self.assertNotIn("quote", suspended_page)

        _, insufficient = self.search(q="2249")
        self.assertEqual(insufficient["items"][0]["analysis_status"], "insufficient")
        self.assertFalse(insufficient["items"][0]["analysis_available"])
        _, insufficient_page = get_json(f"{self.base_url}/api/stocks/2249/analysis")
        self.assertEqual(insufficient_page["stock_state"], "just_listed")
        self.assertNotIn("levels", insufficient_page)

    def test_otc_analysis_matches_two_market_build_seed(self) -> None:
        # S4：V05 改為近似後重建的 v9（母體同 V2 v8）；v8 的條件分組已是舊版。
        staging = ROOT / ".tmp/lane-S4/full-20261108-v9-s4/staging.sqlite"
        if not staging.exists():
            self.skipTest("S4 v9 產物不存在")
        connection = sqlite3.connect(f"file:{staging.as_posix()}?mode=ro", uri=True)
        try:
            rows = connection.execute("SELECT payload FROM stock_analysis WHERE code = '6488'").fetchall()
        finally:
            connection.close()

        self.assertEqual(len(rows), 1)
        status, payload = get_json(f"{self.base_url}/api/stocks/6488/analysis")
        self.assertEqual(status, 200)
        self.assertEqual(payload, json.loads(rows[0][0]))


UNIVERSE_SNAPSHOT = ROOT / ".tmp/lane-V2/universe/2026-11-06.json"


@unittest.skipUnless(UNIVERSE_SNAPSHOT.exists(), "V2 母體快照不在本機")
class LiquidUniverseEndpointTest(unittest.TestCase):
    """本人 2026-11-09「整個網站都只做大量股」：本機搜尋與個股分析只涵蓋 liquid_universe 母體（同雲端 gate）。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = live_server.MarketHTTPServer(("127.0.0.1", 0), live_server.Handler)
        cls.server.universe_path = UNIVERSE_SNAPSHOT
        live_server.universe_state(UNIVERSE_SNAPSHOT)  # 先載入名錄與名次，避免第一個請求逾時
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def search(self, **params: object) -> tuple[int, dict[str, object]]:
        return get_json(f"{self.base_url}/api/stocks/search?{urllib.parse.urlencode(params)}")

    def test_search_only_returns_universe_members_with_rank_and_rule(self) -> None:
        _, payload = self.search(q="環球")
        # 4441 不在母體（名次之外），6488 第 20 名
        self.assertEqual([item["code"] for item in payload["items"]], ["6488"])
        item = payload["items"][0]
        self.assertEqual(item["universe_rank"], 20)
        self.assertEqual(item["universe_rule"], "liquid-v1")

        _, inside = self.search(q="1102")
        self.assertEqual(inside["items"][0]["universe_rank"], 319)  # 遲滯保留

    def test_outside_universe_stock_returns_404_with_rank_like_cloud(self) -> None:
        status, payload = get_json(f"{self.base_url}/api/stocks/4441/analysis")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"], "outside-universe")
        self.assertEqual(payload["reason"], "below_rank")
        self.assertIsInstance(payload["universe_rank"], int)
        self.assertGreater(payload["universe_rank"], 300)
        self.assertEqual(payload["universe_rule"], "liquid-v1")
        self.assertIn(f"第 {payload['universe_rank']} 名", payload["message"])
        self.assertIn("本站只涵蓋大量股", payload["message"])
        self.assertNotIn("levels", payload)
        self.assertNotIn("quote", payload)

    def test_outside_universe_unranked_reason_is_not_called_low_volume(self) -> None:
        status, payload = get_json(f"{self.base_url}/api/stocks/5906/analysis")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"], "outside-universe")
        self.assertEqual(payload["reason"], "suspended_on_data_date")
        self.assertIsNone(payload["universe_rank"])
        self.assertIn("停牌", payload["message"])
        self.assertNotIn("大量股", payload["message"])

    def test_core_holding_0050_and_members_still_analyzable(self) -> None:
        for code in ("0050", "2330", "6488"):
            with self.subTest(code=code):
                status, payload = get_json(f"{self.base_url}/api/stocks/{code}/analysis")
                self.assertEqual(status, 200)
                self.assertEqual(payload["schema"], "stock-analysis/v2")


class ListedCatalogContractTest(unittest.TestCase):
    def test_official_common_stock_outside_old_pack_is_analysis_eligible(self) -> None:
        catalog = load_listed_catalog(RAW, META, HISTORY_DB)

        security = catalog.lookup("1102")

        self.assertIsNotNone(security)
        self.assertEqual(security.code, "1102")
        self.assertEqual(security.name, "亞泥")
        self.assertEqual(security.market, "上市")
        self.assertEqual(security.security_type, "普通股")
        self.assertEqual(security.source_url, "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2")
        self.assertEqual(security.source_date, "2026-11-08")
        self.assertGreaterEqual(security.available_bars, 20)
        self.assertEqual(security.latest_bar_date, "2026-11-06")
        self.assertTrue(security.analysis_eligible)

        # ETF 原入口仍由既有分析處理，但不能混入本票的普通股名錄。
        self.assertIsNone(catalog.lookup("0050"))

    def test_official_security_type_excludes_preferred_stock_and_depositary_receipt(self) -> None:
        catalog = load_listed_catalog(RAW, META, HISTORY_DB)

        # ESVUFR 1,057 檔，加上 ESVTFR 的 3673 TPK-KY，共 1,058 檔 voting shares。
        self.assertEqual(len(catalog), 1058)
        self.assertEqual(catalog.lookup("3673").security_type, "普通股")
        self.assertIsNone(catalog.lookup("1101B"))
        self.assertIsNone(catalog.lookup("9103"))

    def test_searches_official_catalog_by_exact_code_full_name_and_partial_name(self) -> None:
        catalog = load_listed_catalog(RAW, META, HISTORY_DB)

        self.assertEqual([item.code for item in catalog.search("1102").items], ["1102"])
        self.assertEqual([item.code for item in catalog.search("台積電").items], ["2330"])
        self.assertEqual([item.code for item in catalog.search("積電").items], ["2330", "6770"])

    def test_search_paginates_without_hiding_later_matches(self) -> None:
        catalog = load_listed_catalog(RAW, META, HISTORY_DB)

        first = catalog.search("台", limit=2)
        second = catalog.search("台", limit=2, cursor=first.next_cursor)

        self.assertGreater(first.total, 2)
        self.assertEqual(first.next_cursor, "2")
        self.assertEqual(second.total, first.total)
        self.assertTrue({item.code for item in first.items}.isdisjoint(item.code for item in second.items))

    def test_rejects_raw_catalog_that_does_not_match_preserved_source_hash(self) -> None:
        metadata = json.loads(META.read_text(encoding="utf-8"))
        metadata["sha256"] = "0" * 64
        with tempfile.TemporaryDirectory() as directory:
            mismatched_meta = Path(directory) / "meta.json"
            mismatched_meta.write_text(json.dumps(metadata), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "SHA-256"):
                load_listed_catalog(RAW, mismatched_meta, HISTORY_DB)


if __name__ == "__main__":
    unittest.main()
