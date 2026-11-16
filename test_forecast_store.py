"""預測帳本存回雲端儲存（C 票第 6 片；本人 2026-11-15 同意的測試接縫 2）。

用 state_sync.DirectoryStore 以目錄模擬 R2；帳本只能新增：已存在的物件內容不同時拒絕、絕不覆寫。
"""
import tempfile
import unittest
from pathlib import Path

import forecast_store as fs
from state_sync import DirectoryStore


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))  # 原樣位元組，不做 Windows 換行轉換


class ForecastStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.ledger = tmp / "ledger"
        self.store = DirectoryStore(tmp / "r2")
        write(self.ledger / "forecasts" / "2026-11-06__evening__m0.3__base-v1-x.json", '{"a": 1}\n')
        write(self.ledger / "reconciliations" / "2026-11-06__evening__m0.3__base-v1-x__h1.json", '{"b": 2}\n')

    def tearDown(self):
        self._tmp.cleanup()

    def test_push_uploads_every_ledger_file_under_its_own_prefix(self):
        result = fs.push(self.ledger, self.store)
        self.assertEqual(result, {"uploaded": 2, "unchanged": 0})
        self.assertEqual(self.store.keys(), [
            "forecast-ledger/forecasts/2026-11-06__evening__m0.3__base-v1-x.json",
            "forecast-ledger/reconciliations/2026-11-06__evening__m0.3__base-v1-x__h1.json",
        ])
        self.assertEqual(self.store.get("forecast-ledger/forecasts/2026-11-06__evening__m0.3__base-v1-x.json"), b'{"a": 1}\n')

    def test_pushing_again_uploads_nothing(self):
        fs.push(self.ledger, self.store)
        self.assertEqual(fs.push(self.ledger, self.store), {"uploaded": 0, "unchanged": 2})

    def test_push_refuses_to_overwrite_a_different_remote_object(self):
        self.store.put("forecast-ledger/forecasts/2026-11-06__evening__m0.3__base-v1-x.json", b'{"a": "other"}\n')
        with self.assertRaises(fs.LedgerStoreConflict):
            fs.push(self.ledger, self.store)
        self.assertEqual(self.store.get("forecast-ledger/forecasts/2026-11-06__evening__m0.3__base-v1-x.json"), b'{"a": "other"}\n')

    def test_push_skips_partial_files(self):
        write(self.ledger / "forecasts" / ".2026-11-07.json.123.partial", "half")
        self.assertEqual(fs.push(self.ledger, self.store)["uploaded"], 2)

    def test_pull_restores_the_ledger_into_an_empty_folder(self):
        fs.push(self.ledger, self.store)
        target = Path(self._tmp.name) / "restored"
        self.assertEqual(fs.pull(self.store, target), {"downloaded": 2, "unchanged": 0})
        self.assertEqual((target / "reconciliations" / "2026-11-06__evening__m0.3__base-v1-x__h1.json").read_text(encoding="utf-8"), '{"b": 2}\n')

    def test_pull_leaves_archived_originals_in_the_cloud_and_push_still_adds_new_ones(self):
        # 海外原件（D 票）以內容雜湊命名：同名就是同內容，不必每次拉回本機；新抓的照樣存上去。
        write(self.ledger / "sources" / "aaa.raw.gz", "old original")
        fs.push(self.ledger, self.store)
        target = Path(self._tmp.name) / "restored"
        self.assertEqual(fs.pull(self.store, target), {"downloaded": 2, "unchanged": 0})
        self.assertFalse((target / "sources").exists())
        write(target / "sources" / "bbb.raw.gz", "new original")
        self.assertEqual(fs.push(target, self.store), {"uploaded": 1, "unchanged": 2})
        self.assertEqual(self.store.get("forecast-ledger/sources/aaa.raw.gz"), b"old original")

    def test_pull_refuses_to_replace_a_different_local_file(self):
        fs.push(self.ledger, self.store)
        write(self.ledger / "forecasts" / "2026-11-06__evening__m0.3__base-v1-x.json", '{"a": "local"}\n')
        with self.assertRaises(fs.LedgerStoreConflict):
            fs.pull(self.store, self.ledger)
        self.assertEqual((self.ledger / "forecasts" / "2026-11-06__evening__m0.3__base-v1-x.json").read_text(encoding="utf-8"), '{"a": "local"}\n')

    def test_pull_ignores_objects_outside_the_ledger_prefix(self):
        self.store.put("state/manifest.json", b"{}")
        target = Path(self._tmp.name) / "restored"
        self.assertEqual(fs.pull(self.store, target), {"downloaded": 0, "unchanged": 0})
        self.assertFalse((target / "manifest.json").exists())

    def test_pull_refuses_keys_that_would_escape_the_ledger_folder(self):
        # 目錄模擬的 R2 寫入時會把 .. 正規化掉；真的 R2 鍵可以原樣帶 ..，用最小的記憶體假物件重現。
        class Remote:
            def keys(self, prefix=""):
                return ["forecast-ledger/../evil.json"]

            def get(self, key):
                return b"{}"

        target = Path(self._tmp.name) / "restored"
        with self.assertRaises(ValueError):
            fs.pull(Remote(), target)
        self.assertFalse((Path(self._tmp.name) / "evil.json").exists())

    def test_pull_refuses_windows_style_escapes(self):
        for key in ("forecast-ledger/..\\evil.json", "forecast-ledger/C:/evil.json", "forecast-ledger/runs\\x.json"):
            class Remote:
                def keys(self, prefix="", key=key):
                    return [key]

                def get(self, _key):
                    return b"{}"

            with self.subTest(key=key), self.assertRaises(ValueError):
                fs.pull(Remote(), Path(self._tmp.name) / "restored")

    def test_the_command_line_keeps_the_watch_ledger_under_its_own_prefix(self):
        # 自選股關注的帳本（G 票）用同一支程式存回 R2，只是放在 watch-ledger/ 底下，和預測帳本分開。
        store_dir = Path(self._tmp.name) / "r2"
        self.assertEqual(fs.main(["push", "--ledger", str(self.ledger), "--store-dir", str(store_dir),
                                  "--prefix", "watch-ledger/"]), 0)
        self.assertEqual(DirectoryStore(store_dir).keys(), [
            "watch-ledger/forecasts/2026-11-06__evening__m0.3__base-v1-x.json",
            "watch-ledger/reconciliations/2026-11-06__evening__m0.3__base-v1-x__h1.json",
        ])
        restored = Path(self._tmp.name) / "restored"
        self.assertEqual(fs.main(["pull", "--ledger", str(restored), "--store-dir", str(store_dir),
                                  "--prefix", "watch-ledger/"]), 0)
        self.assertEqual(sorted(path.name for path in restored.rglob("*.json")),
                         ["2026-11-06__evening__m0.3__base-v1-x.json", "2026-11-06__evening__m0.3__base-v1-x__h1.json"])


if __name__ == "__main__":
    unittest.main()
