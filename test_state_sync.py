"""state_sync：雲端工作集的打包、上傳、下載還原契約測試。

本機以目錄模擬 R2（DirectoryStore），驗證：
- manifest 記錄每個物件的 SHA 與資料日；下載還原後逐檔 SHA 相符。
- 上傳使用內容定址 immutable object key，全部成功才切換 manifest；put／copy／commit 中斷不會讓舊狀態失效。
- 物件被竄改或缺漏時還原拒絕，不留下半份工作集。
- 上市日 K 可裁成最近 N 個交易日，裁後的庫仍能算 300 根指標。
- 還原後的工作集足以完成一天的增量更新（以 daily_ingest 附加一日驗證）。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import daily_ingest as di
import stock_history as sh
import state_sync as ss
from test_stock_history import ROW_0050, ROW_2330, official_payload


def _write_quotes(root: Path, days: list[str]) -> Path:
    out = root / "data/stock-history"
    for day in days:
        target = sh.raw_file(out, day)
        target.parent.mkdir(parents=True, exist_ok=True)
        import gzip
        target.write_bytes(gzip.compress(official_payload(day, [ROW_0050, ROW_2330])))
    db = out / "stock-history.sqlite"
    sh.build_database(out, db)
    return db


class FailingStore(ss.DirectoryStore):
    def __init__(self, root: Path, fail_on: str) -> None:
        super().__init__(root)
        self.fail_on = fail_on

    def put(self, key: str, data: bytes) -> None:
        if key.endswith(self.fail_on):
            raise OSError("模擬上傳中斷")
        super().put(key, data)


class RecordingStore(ss.DirectoryStore):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.puts: list[str] = []

    def put(self, key: str, data: bytes) -> None:
        self.puts.append(key)
        super().put(key, data)


class RenameFailureStore(ss.DirectoryStore):
    def __init__(self, root: Path, *, fail_on_rename: int, reader_target: Path | None = None) -> None:
        super().__init__(root)
        self.fail_on_rename = fail_on_rename
        self.reader_target = reader_target
        self.rename_count = 0
        self.reader_manifest: dict | None = None
        self.reader_error: Exception | None = None

    def rename(self, src: str, dst: str) -> None:
        self.rename_count += 1
        if self.rename_count == self.fail_on_rename:
            raise OSError("模擬永久 COPY 失敗")
        super().rename(src, dst)
        if self.reader_target is not None and self.rename_count == 1:
            try:
                self.reader_manifest = ss.restore(self.reader_target, ss.DirectoryStore(self.root))
            except Exception as error:  # 測試要保留並行 reader 的實際錯誤供斷言
                self.reader_error = error


class CommitFailureStore(ss.DirectoryStore):
    def rename(self, src: str, dst: str) -> None:
        if dst == ss.MANIFEST:
            raise OSError("模擬永久 manifest commit 失敗")
        super().rename(src, dst)


class StateSyncTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.local = base / "local"
        self.restore = base / "restore"
        self.bucket = base / "bucket"
        days = ["20261104", "20261105", "20261106"]
        self.db = _write_quotes(self.local, days)
        (self.local / "data/liquid-universe").mkdir(parents=True)
        (self.local / "data/liquid-universe/2026-11-06.json").write_text('{"members": []}', encoding="utf-8")
        self.items = [
            ss.Item("data/stock-history/stock-history.sqlite", kind="sqlite"),
            ss.Item("data/stock-history/raw", kind="tree", recent_files=2),
            ss.Item("data/liquid-universe", kind="tree"),
        ]

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_upload_then_restore_roundtrip_matches_sha(self) -> None:
        store = ss.DirectoryStore(self.bucket)
        manifest = ss.upload(self.local, store, self.items, data_date="2026-11-06")
        self.assertEqual(manifest["data_date"], "2026-11-06")
        restored = ss.restore(self.restore, store)
        self.assertEqual(restored["data_date"], "2026-11-06")
        for name in ("data/stock-history/stock-history.sqlite", "data/liquid-universe/2026-11-06.json"):
            self.assertEqual(hashlib.sha256((self.restore / name).read_bytes()).hexdigest(),
                             hashlib.sha256((self.local / name).read_bytes()).hexdigest())

    def test_tree_recent_files_keeps_only_latest_n(self) -> None:
        store = ss.DirectoryStore(self.bucket)
        ss.upload(self.local, store, self.items, data_date="2026-11-06")
        ss.restore(self.restore, store)
        kept = sorted(p.name for p in (self.restore / "data/stock-history/raw").rglob("*.json.gz"))
        self.assertEqual(kept, ["MI_INDEX-20261105.json.gz", "MI_INDEX-20261106.json.gz"])

    def test_interrupted_upload_keeps_previous_manifest(self) -> None:
        good = ss.DirectoryStore(self.bucket)
        ss.upload(self.local, good, self.items, data_date="2026-11-05")
        broken = FailingStore(self.bucket, fail_on=".next")
        with self.assertRaises(OSError):
            ss.upload(self.local, broken, self.items, data_date="2026-11-06")
        self.assertEqual(ss.restore(self.restore, good)["data_date"], "2026-11-05")

    def test_object_copy_failure_does_not_break_old_manifest_or_concurrent_reader(self) -> None:
        good = ss.DirectoryStore(self.bucket)
        old = ss.upload(self.local, good, self.items, data_date="2026-11-05")
        self.db.write_bytes(self.db.read_bytes() + b"new-version")
        (self.local / "data/liquid-universe/2026-11-06.json").write_text('{"members":["2330"]}', encoding="utf-8")
        reader_target = Path(self.tmp.name) / "concurrent-reader"
        broken = RenameFailureStore(self.bucket, fail_on_rename=2, reader_target=reader_target)

        with self.assertRaises(OSError):
            ss.upload(self.local, broken, self.items, data_date="2026-11-06")

        self.assertIsNone(broken.reader_error)
        self.assertEqual(broken.reader_manifest["data_date"], "2026-11-05")
        self.assertEqual(ss.restore(self.restore, good)["data_date"], "2026-11-05")
        current = json.loads(good.get(ss.MANIFEST).decode("utf-8"))
        self.assertEqual(current, old)

    def test_manifest_commit_failure_keeps_previous_manifest_restorable(self) -> None:
        good = ss.DirectoryStore(self.bucket)
        old = ss.upload(self.local, good, self.items, data_date="2026-11-05")
        self.db.write_bytes(self.db.read_bytes() + b"new-version")
        broken = CommitFailureStore(self.bucket)

        with self.assertRaises(OSError):
            ss.upload(self.local, broken, self.items, data_date="2026-11-06")

        self.assertEqual(ss.restore(self.restore, good)["data_date"], "2026-11-05")
        self.assertEqual(json.loads(good.get(ss.MANIFEST).decode("utf-8")), old)

    def test_regular_upload_rejects_older_date_before_any_object_put(self) -> None:
        store = RecordingStore(self.bucket)
        ss.upload(self.local, store, self.items, data_date="2026-11-10")
        store.puts.clear()
        with self.assertRaises(ss.StateIntegrityError) as caught:
            ss.upload(self.local, store, self.items, data_date="2026-11-09")
        self.assertIn("2026-11-10", str(caught.exception))
        self.assertEqual(store.puts, [])
        self.assertEqual(ss.restore(self.restore, store)["data_date"], "2026-11-10")

    def test_upload_rejects_non_strict_or_wrong_type_date_before_any_put(self) -> None:
        for value in ("not-a-date", "2026-99-99", "20261111", None, 20261111):
            with self.subTest(value=value):
                store = RecordingStore(self.bucket / str(value))
                with self.assertRaises(ss.StateIntegrityError):
                    ss.upload(self.local, store, self.items, data_date=value)
                self.assertEqual(store.puts, [])

    def test_bad_remote_manifest_or_cursor_fails_closed_before_any_put(self) -> None:
        bad_manifests = (
            b"{not-json",
            json.dumps({"schema": "market-ana-state/v1", "objects": []}).encode(),
            json.dumps({"schema": "market-ana-state/v1", "data_date": 20261111, "objects": []}).encode(),
            json.dumps({"schema": "market-ana-state/v1", "data_date": "not-a-date", "objects": []}).encode(),
            json.dumps({"schema": "market-ana-state/v1", "data_date": "2026-11-10", "cursors": "bad", "objects": []}).encode(),
            json.dumps({"schema": "market-ana-state/v1", "data_date": "2026-11-10", "cursors": {"market_data_date": "2026-99-99"}, "objects": []}).encode(),
            json.dumps({"schema": "market-ana-state/v1", "data_date": "2026-11-10", "cursors": {"market_data_date": "2026-11-09"}, "objects": []}).encode(),
        )
        for index, raw in enumerate(bad_manifests):
            with self.subTest(index=index):
                store = RecordingStore(self.bucket / f"bad-{index}")
                store.put(ss.MANIFEST, raw)
                store.puts.clear()
                with self.assertRaises(ss.StateIntegrityError) as caught:
                    ss.upload(self.local, store, self.items, data_date="2026-11-11")
                self.assertIn("state/manifest.json", str(caught.exception))
                self.assertIn("人工", str(caught.exception))
                self.assertEqual(store.puts, [])

    def test_level_cursor_does_not_advance_market_or_block_delayed_daily(self) -> None:
        state = self.local / "data/level-signals/state.json"
        state.parent.mkdir(parents=True)
        state.write_text('{"last_processed_date":"2026-11-10","recommended_level":65}', encoding="utf-8")
        items = [*self.items, ss.Item("data/level-signals/state.json")]
        store = RecordingStore(self.bucket)
        ss.upload(self.local, store, items, data_date="2026-11-10", cursor_domain="market")

        state.write_text('{"last_processed_date":"2026-11-11","recommended_level":80}', encoding="utf-8")
        level_manifest = ss.upload(self.local, store, items, data_date="2026-11-11", cursor_domain="level")
        self.assertEqual(level_manifest["data_date"], "2026-11-10")
        self.assertEqual(level_manifest["cursors"], {
            "market_data_date": "2026-11-10",
            "level_last_processed_date": "2026-11-11",
        })

        self.db.write_bytes(self.db.read_bytes() + b"delayed-market-publication")
        delayed = ss.upload(self.local, store, items, data_date="2026-11-10", cursor_domain="market")
        self.assertEqual(delayed["data_date"], "2026-11-10")
        self.assertEqual(delayed["cursors"]["level_last_processed_date"], "2026-11-11")
        restored = ss.restore(self.restore, store)
        self.assertEqual(restored["cursors"]["level_last_processed_date"], "2026-11-11")
        self.assertEqual(json.loads((self.restore / "data/level-signals/state.json").read_text())["last_processed_date"], "2026-11-11")

        store.puts.clear()
        with self.assertRaises(ss.StateIntegrityError):
            ss.upload(self.local, store, items, data_date="2026-11-09", cursor_domain="market")
        self.assertEqual(store.puts, [])

    def test_legacy_manifest_fixed_object_key_restores_and_gains_cursors_on_next_upload(self) -> None:
        item = ss.Item("data/liquid-universe")
        store = ss.DirectoryStore(self.bucket)
        current = ss.upload(self.local, store, [item], data_date="2026-11-10", cursor_domain="market")
        entry = current["objects"][0]
        legacy_key = item.key
        store.put(legacy_key, store.get(entry["key"]))
        legacy = {key: value for key, value in current.items() if key != "cursors"}
        legacy["objects"][0]["key"] = legacy_key
        store.put(ss.MANIFEST, json.dumps(legacy).encode("utf-8"))

        self.assertEqual(ss.restore(self.restore, store)["data_date"], "2026-11-10")
        state = self.local / "data/level-signals/state.json"
        state.parent.mkdir(parents=True)
        state.write_text('{"last_processed_date":"2026-11-11"}', encoding="utf-8")
        upgraded = ss.upload(self.local, store, [item, ss.Item("data/level-signals/state.json")],
                             data_date="2026-11-11", cursor_domain="level")
        self.assertEqual(upgraded["data_date"], "2026-11-10")
        self.assertEqual(upgraded["cursors"]["market_data_date"], "2026-11-10")
        self.assertEqual(upgraded["cursors"]["level_last_processed_date"], "2026-11-11")

    def test_tampered_object_is_rejected_and_target_left_untouched(self) -> None:
        store = ss.DirectoryStore(self.bucket)
        ss.upload(self.local, store, self.items, data_date="2026-11-06")
        prefix = ss.Item("data/stock-history/stock-history.sqlite", kind="sqlite").key + "."
        key = next(k for k in store.keys() if k.startswith(prefix) and not k.endswith(".next"))
        store.put(key, b"tampered")
        with self.assertRaises(ss.StateIntegrityError):
            ss.restore(self.restore, store)
        self.assertFalse((self.restore / "data/stock-history/stock-history.sqlite").exists())

    # ---- 本人 2026-11-14「可以，留兩次」：只留目前與上一個資料日用到的物件，其餘刪除 ----

    def publish(self, store, day: str) -> dict:
        """模擬一天的發布：內容每天不同，上傳後清理。"""
        (self.local / "data/liquid-universe/2026-11-06.json").write_text(json.dumps({"day": day}), encoding="utf-8")
        ss.upload(self.local, store, self.items, data_date=day)
        return ss.collect_garbage(store)

    def referenced(self, store, key: str) -> set:
        return {entry["key"] for entry in json.loads(store.get(key))["objects"] if entry.get("key")}

    def test_garbage_collection_keeps_only_what_today_and_the_previous_day_use(self) -> None:
        store = ss.DirectoryStore(self.bucket)
        self.assertEqual(self.publish(store, "2026-11-04")["deleted"], 0)
        first = self.referenced(store, ss.MANIFEST)
        self.assertEqual(self.publish(store, "2026-11-05")["deleted"], 0)   # 第二天：前一天的還留著
        report = self.publish(store, "2026-11-06")
        today, yesterday = self.referenced(store, ss.MANIFEST), self.referenced(store, ss.PREVIOUS_MANIFEST)
        self.assertEqual(set(store.keys()), {ss.MANIFEST, ss.PREVIOUS_MANIFEST} | today | yesterday)
        self.assertEqual(report["deleted"], len(first - today - yesterday))
        self.assertGreater(report["deleted"], 0)
        self.assertEqual(ss.restore(self.restore, store)["data_date"], "2026-11-06")

    def test_the_previous_day_can_still_be_restored_after_cleanup(self) -> None:
        store = ss.DirectoryStore(self.bucket)
        for day in ("2026-11-04", "2026-11-05", "2026-11-06"):
            self.publish(store, day)
        restored = ss.restore(self.restore, store, previous=True)
        self.assertEqual(restored["data_date"], "2026-11-05")
        self.assertEqual(json.loads((self.restore / "data/liquid-universe/2026-11-06.json").read_text(encoding="utf-8")),
                         {"day": "2026-11-05"})

    def test_a_second_upload_for_the_same_day_does_not_push_the_previous_day_out(self) -> None:
        # 同一資料日重傳、或水位線另外上傳，都不算「新的一天」；上一份仍是前一個資料日。
        store = ss.DirectoryStore(self.bucket)
        self.publish(store, "2026-11-05")
        self.publish(store, "2026-11-06")
        (self.local / "data/liquid-universe/2026-11-06.json").write_text('{"day": "again"}', encoding="utf-8")
        ss.upload(self.local, store, self.items, data_date="2026-11-06")
        ss.collect_garbage(store)
        self.assertEqual(json.loads(store.get(ss.PREVIOUS_MANIFEST))["data_date"], "2026-11-05")
        self.assertEqual(ss.restore(self.restore, store, previous=True)["data_date"], "2026-11-05")

    def test_cleanup_only_touches_the_state_prefix_and_removes_abandoned_uploads(self) -> None:
        store = ss.DirectoryStore(self.bucket)
        self.publish(store, "2026-11-05")
        store.put("other/keep-me.json", b"{}")
        store.put(ss.PREFIX + "data__liquid-universe.tar.gz.deadbeef.next", b"half uploaded")
        report = ss.collect_garbage(store)
        self.assertEqual(report["deleted"], 1)
        self.assertIn("other/keep-me.json", store.keys())
        self.assertNotIn(ss.PREFIX + "data__liquid-universe.tar.gz.deadbeef.next", store.keys())

    def test_cleanup_deletes_nothing_when_it_cannot_tell_what_is_in_use(self) -> None:
        store = ss.DirectoryStore(self.bucket)
        self.assertEqual(ss.collect_garbage(store)["deleted"], 0)           # 還沒有任何 manifest
        self.publish(store, "2026-11-05")
        self.publish(store, "2026-11-06")
        store.put(ss.PREFIX + "stray", b"x")
        store.put(ss.PREVIOUS_MANIFEST, b"{broken")
        before = store.keys()
        with self.assertRaises(ss.StateIntegrityError):
            ss.collect_garbage(store)
        self.assertEqual(store.keys(), before)

    def test_cli_upload_cleans_up_and_a_cleanup_failure_does_not_fail_the_upload(self) -> None:
        import contextlib
        import io
        args = ["upload", "--data-date", "2026-11-06", "--store-dir", str(self.bucket)]
        with unittest.mock.patch.object(ss, "ROOT", self.local), \
                unittest.mock.patch.object(ss, "WORKING_SET", tuple(self.items)):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(ss.main(args), 0)
            self.assertEqual(json.loads(out.getvalue())["cleanup"], {"deleted": 0, "kept": 4})

            def broken(_store):
                raise OSError("R2 list failed")
            out = io.StringIO()
            with unittest.mock.patch.object(ss, "collect_garbage", broken), contextlib.redirect_stdout(out):
                self.assertEqual(ss.main(args), 0)
            self.assertEqual(json.loads(out.getvalue())["cleanup"], {"error": "OSError"})

    def test_r2_lists_every_page_under_a_prefix_and_deletes_single_objects(self) -> None:
        store = ss.R2Store("acct", "bucket", "a" * 32, "s" * 64)
        calls = []
        pages = {
            "": b"<ListBucketResult><IsTruncated>true</IsTruncated><Contents><Key>state/a b.gz</Key></Contents>"
                b"<NextContinuationToken>tok/1+=</NextContinuationToken></ListBucketResult>",
            "tok/1+=": b"<ListBucketResult xmlns=\"http://s3.amazonaws.com/doc/2006-03-01/\"><IsTruncated>false</IsTruncated>"
                       b"<Contents><Key>state/manifest.json</Key></Contents></ListBucketResult>",
        }

        def request(method, key, body=b"", headers=None, query=""):
            calls.append((method, key, query))
            if method == "DELETE":
                return (204, b"") if key == "state/a b.gz" else (403, b"denied")
            token = dict(pair.split("=", 1) for pair in query.split("&")).get("continuation-token", "")
            return 200, pages[__import__("urllib.parse").parse.unquote(token)]
        with unittest.mock.patch.object(store, "_request", request):
            self.assertEqual(store.keys("state/"), ["state/a b.gz", "state/manifest.json"])
            store.delete("state/a b.gz")
            with self.assertRaises(OSError):
                store.delete("state/other")
        self.assertEqual(calls[0], ("GET", "", "list-type=2&prefix=state%2F"))
        self.assertEqual(calls[1], ("GET", "", "continuation-token=tok%2F1%2B%3D&list-type=2&prefix=state%2F"))

    def test_trim_listed_history_keeps_recent_days_and_source_days(self) -> None:
        trimmed = Path(self.tmp.name) / "trim.sqlite"
        summary = ss.trim_daily_db(self.db, trimmed, keep_days=2)
        self.assertEqual(summary["days"], 2)
        connection = sqlite3.connect(trimmed)
        try:
            days = [r[0] for r in connection.execute("SELECT DISTINCT date FROM daily ORDER BY date")]
            sources = [r[0] for r in connection.execute("SELECT date FROM source_days ORDER BY date")]
        finally:
            connection.close()
        self.assertEqual(days, ["2026-11-05", "2026-11-06"])
        self.assertEqual(sources, days)

    def test_trim_keeps_full_history_for_core_holdings(self) -> None:
        # 長期核心持有從 2016 起算含息報酬：0050／2330 不得被裁。
        trimmed = Path(self.tmp.name) / "trim-core.sqlite"
        ss.trim_daily_db(self.db, trimmed, keep_days=1, keep_codes=("0050",))
        connection = sqlite3.connect(trimmed)
        try:
            core_days = [r[0] for r in connection.execute("SELECT date FROM daily WHERE code='0050' ORDER BY date")]
            other_days = [r[0] for r in connection.execute("SELECT date FROM daily WHERE code='2330' ORDER BY date")]
        finally:
            connection.close()
        self.assertEqual(core_days, ["2026-11-04", "2026-11-05", "2026-11-06"])
        self.assertEqual(other_days, ["2026-11-06"])

    def test_recent_files_counts_each_series_separately(self) -> None:
        detail = self.local / "data/market-snapshots/stock-detail"
        for series in ("margin", "t86"):
            for day in ("20261104", "20261105", "20261106"):
                path = detail / series / f"{series}-{day}.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}", encoding="utf-8")
        store = ss.DirectoryStore(self.bucket)
        item = ss.Item("data/market-snapshots/stock-detail", recent_files=2)
        ss.upload(self.local, store, [item], data_date="2026-11-06")
        ss.restore(self.restore, store)
        kept = sorted(p.name for p in (self.restore / "data/market-snapshots/stock-detail").rglob("*.json"))
        self.assertEqual(kept, ["margin-20261105.json", "margin-20261106.json", "t86-20261105.json", "t86-20261106.json"])

    def test_restored_state_can_append_next_day(self) -> None:
        store = ss.DirectoryStore(self.bucket)
        ss.upload(self.local, store, self.items, data_date="2026-11-06")
        ss.restore(self.restore, store)
        out = self.restore / "data/stock-history"
        db = out / "stock-history.sqlite"

        def get(url, params):
            return sh.Response(200, "application/json", official_payload(params["date"], [ROW_0050, ROW_2330]), "u")

        result = di.listed_quotes_day(out, db, "2026-11-09", get=get, sleep=lambda s: None)
        self.assertEqual(result["status"], "appended")
        connection = sqlite3.connect(db)
        try:
            self.assertEqual(connection.execute("SELECT MAX(date) FROM daily").fetchone()[0], "2026-11-09")
        finally:
            connection.close()

    def test_raw_quote_trees_cover_universe_warmup_window(self) -> None:
        # 大量股母體的 ETF 名單從行情原件讀：warmup 60 日＋20 日窗＋緩衝，原件至少要留 90 個交易日。
        import liquid_universe as lu
        needed = 60 + lu.WINDOW_DAYS + 10
        by_path = {item.path: item for item in ss.WORKING_SET}
        for path in ("data/stock-history/raw", "data/otc-history/raw"):
            self.assertGreaterEqual(by_path[path].recent_files, needed, path)

    def test_working_set_covers_market_pack_inputs(self) -> None:
        # U3：大盤頁資料包（cloud/market_pack_daily.py）從原件重建；Actions 上只有 R2 工作集，缺一項就在 build_dist 失敗。
        # logs/build_analysis_data.py 要求的窗：日K與成交金額＝2016 起全部月份、5 秒指數 30 日、法人 120 日、類股 60 日、期貨 1 份；
        # logs/build_chips_data.py 讀 margin 總表；兩支建置腳本與 chip_streaks 由 git 帶，不在工作集。
        by_path = {item.path: item for item in ss.WORKING_SET}
        snap = ss.MARKET_SNAPSHOT
        expected = {
            f"{snap}/index-monthly": 0,      # 全部月份（不得只留最近 24 個月）
            f"{snap}/turnover-monthly": 0,
            f"{snap}/intraday-5s": 30,
            f"{snap}/institutional": 120,
            f"{snap}/sectors": 60,
            f"{snap}/futures": 0,
            "data/market-snapshots/margin": 60,
        }
        for path, recent in expected.items():
            self.assertIn(path, by_path, path)
            self.assertEqual(by_path[path].recent_files, recent, path)
        # 人寫的劇本與白話分析（每日不重寫，原樣沿用）也要在雲端存一份，build 才組得出大盤頁。
        for path in ("web/dashboard-data.js", "web/plain-data.js"):
            self.assertIn(path, by_path, path)

    def test_working_set_carries_otc_corporate_action_originals_and_merged_events(self) -> None:
        by_path = {item.path: item for item in ss.WORKING_SET}
        self.assertIn("data/corporate-actions/events.json", by_path)
        self.assertIn("data/corporate-actions/raw/TPEx-exDailyQ", by_path)
        self.assertEqual(by_path["data/corporate-actions/raw/TPEx-exDailyQ"].recent_files, 0)

    def test_working_set_carries_the_capital_change_supplement_for_price_adjustment(self) -> None:
        # 每天只抓視窗內的新事件；累積下來的 ETF 分割、櫃買減資與上市現金股利要跟著工作集走，否則每次回到種子。
        self.assertIn("data/corporate-actions/capital-supplement.json", {item.path for item in ss.WORKING_SET})

    def test_working_set_carries_the_weekly_big_holder_files_that_cannot_be_downloaded_again(self) -> None:
        by_path = {item.path: item for item in ss.WORKING_SET}
        self.assertEqual(by_path["data/tdcc-holders/weekly"].recent_files, 60)

    def test_working_set_carries_run_log_and_previous_seed(self) -> None:
        # Actions 每次都是新機器：沒有執行紀錄，後面的 cron 時段看不到 already-done，會整天重做；
        # 沒有前一天 seed，D1 只能完整匯入（失去增量 base）。
        by_path = {item.path: item for item in ss.WORKING_SET}
        self.assertIn("data/daily-update-log", by_path)
        self.assertIn("data/daily-update-state/last-seed", by_path)

    def test_working_set_carries_previous_functions_stock_pack(self) -> None:
        # cloud/build_stock_pack.py 以上一版 cloud/.stock-pack.json 的代號決定 Functions 內建包收哪些股票；
        # 它是產生物（不入 git），Actions 新機器沒有它時只剩 0050（U3 乾淨還原實測 216 → 1 檔）。
        by_path = {item.path: item for item in ss.WORKING_SET}
        self.assertIn("cloud/.stock-pack.json", by_path)

    def test_listed_and_otc_daily_keep_five_years_for_benchmark_compare(self) -> None:
        # B1（協調窗追加）：個股與 0050 的 3Y／5Y 含息對照要 5 年日K；300 天只夠核心兩檔。
        by_path = {item.path: item for item in ss.WORKING_SET}
        self.assertGreaterEqual(ss.DAILY_KEEP_DAYS, 1270)
        self.assertEqual(by_path["data/stock-history/stock-history.sqlite"].trim_days, ss.DAILY_KEEP_DAYS)
        otc = by_path["data/otc-history/otc_history.sqlite"]
        self.assertEqual(otc.kind, "sqlite")
        self.assertIn(otc.trim_days, (0, ss.DAILY_KEEP_DAYS))

    def test_restored_working_set_keeps_five_years_for_a_non_core_stock(self) -> None:
        # 裁庫後非核心股（例如 1102）仍要有 5 年以上的日K，3Y／5Y 對照才算得出來。
        trimmed = Path(self.tmp.name) / "trim-5y.sqlite"
        ss.trim_daily_db(self.db, trimmed, keep_days=ss.DAILY_KEEP_DAYS, keep_codes=ss.CORE_CODES)
        connection = sqlite3.connect(trimmed)
        try:
            kept = connection.execute("SELECT COUNT(DISTINCT date) FROM daily WHERE code='2330'").fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(kept, 3)  # 測試庫只有 3 天：全留

    def test_init_refuses_without_explicit_confirm(self) -> None:
        # 首次初始化會覆蓋 R2 上的 manifest；沒有 --confirm 只能 plan，不得上傳。
        store = ss.DirectoryStore(self.bucket)
        with unittest.mock.patch.object(ss, "ROOT", self.local), unittest.mock.patch.object(ss, "WORKING_SET", tuple(self.items)):
            with self.assertRaises(SystemExit) as caught:
                ss.main(["init", "--data-date", "2026-11-06", "--store-dir", str(self.bucket)])
        self.assertIn("--confirm", str(caught.exception))
        self.assertEqual(store.keys(), [])

    def test_cli_rejects_impossible_or_compact_date_before_store_write(self) -> None:
        for value in ("2026-99-99", "20261111", "not-a-date"):
            with self.subTest(value=value):
                bucket = self.bucket / value
                with self.assertRaises(SystemExit):
                    ss.main(["upload", "--data-date", value, "--store-dir", str(bucket)])
                self.assertFalse(bucket.exists())

    def test_init_with_confirm_uploads_and_verifies_roundtrip(self) -> None:
        store = ss.DirectoryStore(self.bucket)
        with unittest.mock.patch.object(ss, "ROOT", self.local), unittest.mock.patch.object(ss, "WORKING_SET", tuple(self.items)):
            code = ss.main(["init", "--data-date", "2026-11-06", "--store-dir", str(self.bucket), "--confirm"])
        self.assertEqual(code, 0)
        self.assertIn(ss.MANIFEST, store.keys())
        self.assertEqual(ss.restore(self.restore, store)["data_date"], "2026-11-06")

    def test_init_refuses_to_overwrite_newer_remote_state(self) -> None:
        # R2 已有較新資料日（Actions 已在跑）時，本機 init 不得把它倒退。
        store = ss.DirectoryStore(self.bucket)
        ss.upload(self.local, store, self.items, data_date="2026-11-10")
        with unittest.mock.patch.object(ss, "ROOT", self.local), unittest.mock.patch.object(ss, "WORKING_SET", tuple(self.items)):
            with self.assertRaises(SystemExit) as caught:
                ss.main(["init", "--data-date", "2026-11-06", "--store-dir", str(self.bucket), "--confirm"])
        self.assertIn("2026-11-10", str(caught.exception))
        self.assertEqual(ss.restore(self.restore, store)["data_date"], "2026-11-10")

    def test_missing_r2_credentials_give_clear_error(self) -> None:
        with unittest.mock.patch.dict("os.environ", {"CLOUDFLARE_ACCOUNT_ID": "x"}, clear=True):
            with self.assertRaises(SystemExit) as caught:
                ss.R2Store.from_env()
        self.assertIn("缺少 R2 金鑰", str(caught.exception))
        self.assertIn("R2_ACCESS_KEY_ID", str(caught.exception))

    def test_r2_credentials_strip_pasted_whitespace(self) -> None:
        # 用剪貼簿存 secret 常會多帶換行；去掉空白後是正常長度就照用。
        env = {"CLOUDFLARE_ACCOUNT_ID": "x", "R2_ACCESS_KEY_ID": "a" * 32 + "\r\n", "R2_SECRET_ACCESS_KEY": " s\n"}
        with unittest.mock.patch.dict("os.environ", env, clear=True):
            store = ss.R2Store.from_env()
        self.assertEqual(store.access_key, "a" * 32)
        self.assertEqual(store.secret_key, "s")

    def test_malformed_r2_access_key_gives_clear_error(self) -> None:
        # 第一次 Actions 試跑：R2 回「access key has length 74, should be 32」，要在送出前就講清楚是哪個 secret 錯。
        env = {"CLOUDFLARE_ACCOUNT_ID": "x", "R2_ACCESS_KEY_ID": "a" * 74, "R2_SECRET_ACCESS_KEY": "s"}
        with unittest.mock.patch.dict("os.environ", env, clear=True):
            with self.assertRaises(SystemExit) as caught:
                ss.R2Store.from_env()
        self.assertIn("R2_ACCESS_KEY_ID", str(caught.exception))
        self.assertIn("74", str(caught.exception))
        self.assertNotIn("a" * 10, str(caught.exception))

    def test_multiline_r2_key_reports_line_lengths_only(self) -> None:
        # 剪貼簿一次帶了兩個值時，報出每行長度，方便看出貼錯了什麼，但不印內容。
        env = {"CLOUDFLARE_ACCOUNT_ID": "x", "R2_ACCESS_KEY_ID": "b" * 40 + "\r\n" + "a" * 32,
               "R2_SECRET_ACCESS_KEY": "s" * 64}
        with unittest.mock.patch.dict("os.environ", env, clear=True):
            with self.assertRaises(SystemExit) as caught:
                ss.R2Store.from_env()
        self.assertIn("R2_ACCESS_KEY_ID 40＋32（hex 72", str(caught.exception))
        self.assertIn("R2_SECRET_ACCESS_KEY 64", str(caught.exception))
        self.assertNotIn("b" * 10, str(caught.exception))

    def test_default_working_set_excludes_video_research_and_backups(self) -> None:
        paths = [item.path for item in ss.WORKING_SET]
        self.assertIn("data/stock-history/stock-history.sqlite", paths)
        self.assertIn("data/fundamentals-history", paths)
        for path in paths:
            self.assertTrue(path.startswith("data/") or path in ss.HUMAN_AUTHORED or path in ss.GENERATED_CARRY, path)
            self.assertNotIn("9ExfGG7fKF8", path)
            self.assertNotIn("backup", path)


if __name__ == "__main__":
    unittest.main()
