"""EODHD 共用（G 票延伸，本人 2026-11-17「可以共用」）：大盤預測的海外層（D 票）與關注的國外同類股（B 票）
同一個代號、同一個收盤之後只抓一次，兩邊讀同一份（R2 的 eodhd-cache/）。

假的 EODHD 記下每一次請求；雲端儲存用本機資料夾。預期的次數是照收盤時間手算的：
- 美國的代號在美國收盤後（UTC 22:00）才有新的一天；亞洲的在亞洲收盤後（UTC 08:00）。
- 抓過之後、下一個收盤之前再要，就讀同一份，不再打 EODHD。
"""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import eodhd_cache as ec
import overseas_inputs as oi
import overseas_peers as op
from state_sync import DirectoryStore
from test_overseas_inputs import eodhd

UTC = timezone.utc


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


class FakeEodhd:
    def __init__(self, status=200):
        self.urls = []
        self.status = status

    def __call__(self, url):
        self.urls.append(url)
        symbol = url.split("/eod/", 1)[1].split("?", 1)[0]
        if self.status != 200:
            return self.status, b""
        return 200, eodhd([("2026-11-16", 100.0), ("2026-11-17", 101.0)]) if symbol != "EMPTY.INDX" else b"[]"

    def symbols(self):
        return [url.split("/eod/", 1)[1].split("?", 1)[0] for url in self.urls]


class SharedCacheTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.store = DirectoryStore(self.tmp / "r2")
        self.net = FakeEodhd()

    def tearDown(self):
        self._tmp.cleanup()

    def cache(self, now: str, token: str = "k", net=None):
        return ec.SharedEodhd(self.store, token, http=net or self.net, now=lambda: at(now))

    def forecast(self, now: str, reference: str, out: str):
        """大盤預測抓海外原件（D 票 fetch_sources），EODHD 那幾檔走共用的那一份。"""
        def other(url, **_):  # 財政部、聯準會、央行：不是這次的主題，一律回空的
            raise OSError("not in this test")
        return oi.fetch_sources(self.tmp / out, reference_date=reference, eodhd_token="k", fetch=other,
                                eodhd_cache=self.cache(now))


class WhenToFetchTests(SharedCacheTestCase):
    def test_each_market_is_fetched_once_after_its_close_and_reused_until_the_next_close(self):
        # 週二晚間版（UTC 10:00）：快取是空的，四檔都要抓。
        self.forecast("2026-11-17T10:00:00", "2026-11-17", "tue-evening")
        self.assertEqual(len(self.net.urls), 4)
        # 週三晨間版（UTC 週二 23:20）：美國週二已收盤（22:00）→ 標普、那斯達克重抓；日韓上一次收盤是週二 08:00，晚間版
        # 那一份就是最新的 → 不抓。
        self.net.urls.clear()
        self.forecast("2026-11-17T23:20:00", "2026-11-18", "wed-morning")
        self.assertEqual(sorted(self.net.symbols()), ["GSPC.INDX", "IXIC.INDX"])
        # 週三晚間版（UTC 10:00）：美國的那一份是週二收盤後抓的 → 不抓；日韓週三 08:00 收盤 → 重抓。
        self.net.urls.clear()
        manifest = self.forecast("2026-11-18T10:00:00", "2026-11-18", "wed-evening")
        self.assertEqual(sorted(self.net.symbols()), ["KS11.INDX", "N225.INDX"])
        # 讀共用那一份的原件照樣進 manifest（雜湊對得上、整理得出海外層）。
        eodhd_entries = [entry for entry in manifest["sources"] if entry["source"] == "eodhd"]
        self.assertEqual({entry["status"] for entry in eodhd_entries}, {"ok"})
        layer = oi.build_layer(self.tmp / "wed-evening", edition="evening", base_date="2026-11-17")
        self.assertEqual(layer["status"]["japan_nikkei"]["latest_date"], "2026-11-17")

    def test_the_weekend_does_not_count_as_a_close(self):
        # 週一 UTC 05:00：美國上一次收盤是週五 22:00，亞洲是週五 08:00。
        self.assertEqual(ec.last_close(ec.market_zone("GSPC.INDX"), at("2026-11-16T05:00:00")), at("2026-11-13T22:00:00"))
        self.assertEqual(ec.last_close(ec.market_zone("005930.KO"), at("2026-11-16T05:00:00")), at("2026-11-13T08:00:00"))
        # 不認得的指數兩邊都算：哪一邊收盤了都要重抓。
        self.assertEqual(ec.last_close(ec.market_zone("XYZ.INDX"), at("2026-11-16T09:00:00")), at("2026-11-16T08:00:00"))


class SharedBetweenLinesTests(SharedCacheTestCase):
    def test_peers_read_what_the_forecast_already_fetched_and_fetch_the_rest_only_once(self):
        # 晚間版先抓了日經（UTC 10:00）；關注的國外同類股（B 票）在同一天稍晚要日經與三星。
        self.forecast("2026-11-17T10:00:00", "2026-11-17", "tue-evening")
        self.net.urls.clear()
        source = op.CachedEodhdSource(self.cache("2026-11-17T10:30:00"))
        nikkei = source.daily("N225.INDX", datetime(2026, 11, 1).date(), datetime(2026, 11, 17).date())
        samsung = source.daily("005930.KO", datetime(2026, 11, 1).date(), datetime(2026, 11, 17).date())
        again = op.CachedEodhdSource(self.cache("2026-11-17T11:00:00")).daily(
            "005930.KO", datetime(2026, 11, 1).date(), datetime(2026, 11, 17).date())

        self.assertEqual(self.net.symbols(), ["005930.KO"])
        self.assertEqual([bar["close"] for bar in nikkei], [100.0, 101.0])
        self.assertEqual(samsung, again)

    def test_peers_keep_their_errors_when_nothing_is_cached(self):
        source = op.CachedEodhdSource(self.cache("2026-11-17T10:00:00", net=FakeEodhd(status=429)))
        with self.assertRaises(op.QuotaExceeded):
            source.daily("005930.KO", datetime(2026, 11, 1).date(), datetime(2026, 11, 17).date())
        with self.assertRaises(op.NotFound):
            op.CachedEodhdSource(self.cache("2026-11-17T10:00:00")).daily(
                "EMPTY.INDX", datetime(2026, 11, 1).date(), datetime(2026, 11, 17).date())


class FailureTests(SharedCacheTestCase):
    def test_a_failed_refresh_falls_back_to_the_last_copy_and_says_so(self):
        self.forecast("2026-11-17T10:00:00", "2026-11-17", "tue-evening")
        broken = FakeEodhd(status=429)
        raw, meta = self.cache("2026-11-18T10:00:00", net=broken).raw("N225.INDX")

        self.assertEqual(json.loads(raw)[-1]["date"], "2026-11-17")
        self.assertEqual((meta["fetched_at"], meta["refresh_error"]), ("2026-11-17T10:00:00Z", "HTTP 429"))

    def test_without_a_key_only_the_shared_copy_is_used(self):
        self.forecast("2026-11-17T10:00:00", "2026-11-17", "tue-evening")
        raw, meta = self.cache("2026-11-17T12:00:00", token="").raw("N225.INDX")
        self.assertIsNotNone(raw)
        with self.assertRaises(ec.FetchError):
            self.cache("2026-11-17T12:00:00", token="").raw("NEVER.INDX")

    def test_the_cache_never_holds_the_key(self):
        self.forecast("2026-11-17T10:00:00", "2026-11-17", "tue-evening")
        for key in self.store.keys(ec.PREFIX):
            self.assertNotIn(b"api_token=k", self.store.get(key))


class WorkflowTests(unittest.TestCase):
    """三支排程都走共用的那一份：大盤預測晚間、晨間抓海外原件，每日更新抓國外同類股。"""

    def test_every_eodhd_reader_uses_the_shared_copy(self):
        from test_forecast_workflow import settings_only, step
        root = Path(__file__).parent / ".github/workflows"
        for name in ("forecast-ledger.yml", "forecast-morning.yml"):
            fetch = step(settings_only(root / name), "抓海外原件（不擋預測）")
            self.assertIn("--shared-cache", fetch, name)
            for env in ("R2_ACCESS_KEY_ID: ${{ secrets.R2_ACCESS_KEY_ID }}", "R2_BUCKET: market-ana-state"):
                self.assertIn(env, fetch, name)
        peers = step(settings_only(root / "daily-update.yml"), "國外同類股行情（只在發布成功時）")
        self.assertIn("python overseas_peers.py daily --shared-cache", peers)
        # 關注排程提名同類股時的「來源拿得到行情」檢查也讀同一份（韓股、指數）。
        nominate = step(settings_only(root / "watch-ledger.yml"), "提名國外同類股（站主確認後才使用）")
        self.assertIn("--shared-cache", nominate)
        self.assertIn("R2_ACCESS_KEY_ID: ${{ secrets.R2_ACCESS_KEY_ID }}", nominate)


if __name__ == "__main__":
    unittest.main()
