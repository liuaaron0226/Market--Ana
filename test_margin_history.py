"""個股融資原件的長期保存區：搬入補抓的日子、每日跟上、盤點缺日（融資維持率（估）要連續、長期的每日原件）。"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

import margin_history as mh

STOCK_FIELDS = ["代號", "名稱", "買進", "賣出", "現金償還", "前日餘額", "今日餘額"]
ROW_2330 = ["2330", "台積電", "100", "80", "5", "35000", "35015"]
ROW_2317 = ["2317", "鴻海", "50", "60", "0", "52000", "51990"]


def official(day: str, rows: list[list[str]] | None = None, *, with_total: bool = True) -> dict:
    summary = [["融資(交易單位)", "1", "2", "3", "4", "5"]]
    if with_total:
        summary.append(["融資金額(仟元)", "1", "2", "3", "4", "539900000"])
    return {"stat": "OK", "date": day, "tables": [
        {"fields": ["項目", "買進", "賣出", "現金(券)償還", "前日餘額", "今日餘額"], "data": summary},
        {"fields": STOCK_FIELDS, "data": [ROW_2330, ROW_2317] if rows is None else rows},
    ]}


class Backfill:
    """模擬補抓留下的資料夾：raw/YYYYMMDD.json.gz 加 manifest.jsonl。"""

    def __init__(self, base: Path) -> None:
        self.path = base / "backfill"
        (self.path / "raw").mkdir(parents=True)
        self.entries: list[dict] = []

    def add(self, day: str, payload: dict, **overrides) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        (self.path / "raw" / f"{day}.json.gz").write_bytes(gzip.compress(body))
        self.log({"day": day, "fetched_at_utc": "2026-11-15T16:22:27+00:00",
                  "source_url": f"https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={day}&selectType=ALL&response=json",
                  "status": "ok", "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(),
                  "rows": len(payload["tables"][1]["data"]), **overrides})

    def log(self, entry: dict) -> None:
        self.entries.append(entry)
        (self.path / "manifest.jsonl").write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in self.entries), encoding="utf-8")


def stored(root: Path, day: str) -> Path:
    """長期保存區裡的那一天。"""
    return root / "data/margin-history/raw" / f"margin-{day}.json"


def recent(root: Path, day: str) -> Path:
    """每日抓取存的近期明細（個股頁籌碼用，只留最近一段）。"""
    return root / "data/market-snapshots/stock-detail/margin" / f"margin-{day}.json"


def fetch(root: Path, day: str, payload: dict | None = None, *, mark: str = "daily") -> None:
    """模擬每日抓取把當天的個股融資存進近期明細。"""
    path = recent(root, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"fetched_at_utc": mark, "source_url": "u", "data": official(day) if payload is None else payload}
    path.write_text(json.dumps(body, ensure_ascii=False) + "\n", encoding="utf-8")


class ImportBackfillTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "root"
        self.root.mkdir()
        self.backfill = Backfill(Path(self.tmp.name))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_backfilled_day_is_stored_in_the_same_form_as_the_daily_fetch(self) -> None:
        payload = official("20251103")
        self.backfill.add("20251103", payload)
        report = mh.import_backfill(self.root, self.backfill.path)
        self.assertEqual(report["imported"], ["20251103"])
        saved = json.loads(stored(self.root, "20251103").read_text(encoding="utf-8"))
        # 每日抓取存的外包格式：抓取時間、來源網址、官方回應原樣放在 data。
        self.assertEqual(saved["data"], payload)
        self.assertEqual(saved["fetched_at_utc"], "2026-11-15T16:22:27+00:00")
        self.assertIn("date=20251103", saved["source_url"])

    def test_day_whose_bytes_do_not_match_the_recorded_hash_is_left_out(self) -> None:
        self.backfill.add("20251103", official("20251103"))
        self.backfill.add("20251104", official("20251104"), sha256="0" * 64)
        report = mh.import_backfill(self.root, self.backfill.path)
        self.assertEqual(report["imported"], ["20251103"])
        self.assertEqual([r["day"] for r in report["rejected"]], ["20251104"])
        self.assertIn("雜湊", report["rejected"][0]["reason"])
        self.assertFalse(stored(self.root, "20251104").exists())

    def test_incomplete_day_is_left_out_with_the_reason(self) -> None:
        # 估算要用每檔的買賣張數，起點與每日對帳要用全市場融資金額；少一樣、或日期對不上，這一天就不能用。
        self.backfill.add("20251103", official("20251104"))                      # 內容是別天的
        self.backfill.add("20251104", official("20251104", rows=[]))             # 沒有個股
        self.backfill.add("20251105", official("20251105", with_total=False))    # 沒有全市場融資金額
        self.backfill.add("20251106", {"stat": "很抱歉，沒有符合條件的資料!", "tables": [{}, {"data": []}]})
        self.backfill.add("20251107", official("20251107"))
        report = mh.import_backfill(self.root, self.backfill.path)
        self.assertEqual(report["imported"], ["20251107"])
        reasons = {r["day"]: r["reason"] for r in report["rejected"]}
        self.assertIn("日期", reasons["20251103"])
        self.assertIn("個股", reasons["20251104"])
        self.assertIn("融資金額", reasons["20251105"])
        self.assertIn("查無", reasons["20251106"])
        for day in ("20251103", "20251104", "20251105", "20251106"):
            self.assertFalse(stored(self.root, day).exists())

    def test_days_the_backfill_failed_to_fetch_do_not_stop_the_rest(self) -> None:
        # 補抓的清單裡，沒抓成功的日子只有一列紀錄、沒有檔案；後來重抓成功會再多一列。
        self.backfill.log({"day": "20251103", "fetched_at_utc": "x", "source_url": "u", "status": "error"})
        self.backfill.add("20251103", official("20251103"))
        self.backfill.log({"day": "20251104", "fetched_at_utc": "x", "source_url": "u", "status": "bad"})
        self.backfill.add("20251105", official("20251105"))
        (self.backfill.path / "raw/20251105.json.gz").unlink()       # 清單說有、檔案卻不在
        self.backfill.add("20251106", official("20251106"))
        report = mh.import_backfill(self.root, self.backfill.path)
        self.assertEqual(report["imported"], ["20251103", "20251106"])
        reasons = {r["day"]: r["reason"] for r in report["rejected"]}
        self.assertEqual(sorted(reasons), ["20251104", "20251105"])    # 後來抓成功的 11-03 不算被退回
        self.assertIn("沒有抓成功", reasons["20251104"])
        self.assertIn("找不到", reasons["20251105"])

    def test_day_already_in_the_long_term_store_is_never_overwritten(self) -> None:
        own = stored(self.root, "20261106")
        own.parent.mkdir(parents=True)
        own.write_text('{"fetched_at_utc": "daily", "source_url": "u", "data": {"kept": true}}\n', encoding="utf-8")
        self.backfill.add("20261106", official("20261106"))
        report = mh.import_backfill(self.root, self.backfill.path)
        self.assertEqual(report["imported"], [])
        self.assertEqual(report["existing"], ["20261106"])
        self.assertEqual(json.loads(own.read_text(encoding="utf-8"))["data"], {"kept": True})

    def test_importing_twice_changes_nothing_the_second_time(self) -> None:
        self.backfill.add("20251103", official("20251103"))
        mh.import_backfill(self.root, self.backfill.path)
        before = stored(self.root, "20251103").read_bytes()
        report = mh.import_backfill(self.root, self.backfill.path)
        self.assertEqual((report["imported"], report["existing"]), ([], ["20251103"]))
        self.assertEqual(stored(self.root, "20251103").read_bytes(), before)


class KeepUpTest(unittest.TestCase):
    """每日抓取把當天的個股融資存在近期明細（那裡只留最近一段）；長期保存區要跟上，而且之後不再少。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_days_only_in_the_recent_folder_are_copied_byte_for_byte(self) -> None:
        fetch(self.root, "20261106")
        fetch(self.root, "20261109")
        report = mh.keep_up(self.root)
        self.assertEqual(report["stored"], ["20261106", "20261109"])
        for day in ("20261106", "20261109"):
            self.assertEqual(stored(self.root, day).read_bytes(), recent(self.root, day).read_bytes())

    def test_stored_days_survive_after_the_recent_folder_is_trimmed(self) -> None:
        fetch(self.root, "20261106")
        mh.keep_up(self.root)
        recent(self.root, "20261106").unlink()          # 近期明細被修到只剩最近幾天
        fetch(self.root, "20261109")
        report = mh.keep_up(self.root)
        self.assertEqual(report["stored"], ["20261109"])
        self.assertTrue(stored(self.root, "20261106").exists())

    def test_day_already_stored_is_not_rewritten(self) -> None:
        fetch(self.root, "20261106", mark="first")
        mh.keep_up(self.root)
        fetch(self.root, "20261106", mark="second")
        self.assertEqual(mh.keep_up(self.root)["stored"], [])
        self.assertEqual(json.loads(stored(self.root, "20261106").read_text(encoding="utf-8"))["fetched_at_utc"], "first")

    def test_incomplete_day_is_not_stored_and_is_reported(self) -> None:
        # 存進去就不再改寫，所以不能用的那一天不能進長期保存區；留著缺口，盤點才看得到。
        fetch(self.root, "20261106", official("20261106", with_total=False))
        fetch(self.root, "20261109")
        report = mh.keep_up(self.root)
        self.assertEqual(report["stored"], ["20261109"])
        self.assertEqual([s["day"] for s in report["skipped"]], ["20261106"])
        self.assertIn("融資金額", report["skipped"][0]["reason"])
        self.assertFalse(stored(self.root, "20261106").exists())

    def test_files_that_are_not_a_daily_original_are_ignored(self) -> None:
        fetch(self.root, "20261106")
        folder = recent(self.root, "20261106").parent
        (folder / "margin-20261106-copy.json").write_text("{}", encoding="utf-8")
        (folder / "margin-latest.json").write_text("{}", encoding="utf-8")
        report = mh.keep_up(self.root)
        self.assertEqual((report["stored"], report["skipped"]), (["20261106"], []))
        self.assertEqual(sorted(p.name for p in stored(self.root, "20261106").parent.iterdir()), ["margin-20261106.json"])

    def test_nothing_fetched_yet_is_not_an_error(self) -> None:
        self.assertEqual(mh.keep_up(self.root), {"stored": [], "skipped": []})


class SeedTest(unittest.TestCase):
    """雲端的工作集一開始只有最近一段；更早的日子隨專案帶一份種子，長期保存區缺了就從種子放回去。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "root"
        self.elsewhere = Path(self.tmp.name) / "elsewhere"     # 另一台機器：只有專案裡的種子，沒有資料
        self.root.mkdir()
        self.elsewhere.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def keep(self, day: str, mark: str = "daily") -> None:
        fetch(self.root, day, mark=mark)
        mh.keep_up(self.root)

    def carry_seed(self) -> None:
        target = self.elsewhere / mh.SEED
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((self.root / mh.SEED).read_bytes())

    def test_seed_puts_every_packed_day_back_byte_for_byte(self) -> None:
        self.keep("20251103")
        self.keep("20251104")
        packed = mh.write_seed(self.root)
        self.assertEqual((packed["days"], packed["first"], packed["last"]), (2, "20251103", "20251104"))
        self.carry_seed()
        self.assertEqual(mh.restore_seed(self.elsewhere), ["20251103", "20251104"])
        for day in ("20251103", "20251104"):
            self.assertEqual(stored(self.elsewhere, day).read_bytes(), stored(self.root, day).read_bytes())

    def test_day_already_in_the_store_is_left_alone(self) -> None:
        self.keep("20251103", mark="packed")
        self.keep("20251104", mark="packed")
        mh.write_seed(self.root)
        self.carry_seed()
        fetch(self.elsewhere, "20251104", mark="own")
        mh.keep_up(self.elsewhere)
        self.assertEqual(mh.restore_seed(self.elsewhere), ["20251103"])
        own = json.loads(stored(self.elsewhere, "20251104").read_text(encoding="utf-8"))
        self.assertEqual(own["fetched_at_utc"], "own")
        self.assertEqual(mh.restore_seed(self.elsewhere), [])          # 再放一次不會有事

    def test_no_seed_is_not_an_error(self) -> None:
        self.assertEqual(mh.restore_seed(self.elsewhere), [])
        self.assertFalse((self.elsewhere / "data").exists())

    def test_only_daily_originals_come_out_of_the_seed(self) -> None:
        # 種子裡若混進別的東西（或想寫到保存區外面的路徑），一律不理。
        target = self.elsewhere / mh.SEED
        target.parent.mkdir(parents=True)
        with tarfile.open(target, mode="w:xz") as tar:
            for name in ("margin-20251103.json", "../margin-20251104.json", "notes.txt", "raw/margin-20251105.json"):
                member = tarfile.TarInfo(name)
                member.size = 2
                tar.addfile(member, io.BytesIO(b"{}"))
        self.assertEqual(mh.restore_seed(self.elsewhere), ["20251103"])
        written = sorted(p.relative_to(self.elsewhere).as_posix() for p in self.elsewhere.rglob("*") if p.is_file())
        self.assertEqual(written, ["data/margin-history/raw/margin-20251103.json", mh.SEED])

    def test_packing_the_same_days_again_gives_the_same_file(self) -> None:
        # 種子是入庫的檔案；內容沒變就不該每次打包都變成一個新版本。
        self.keep("20251103")
        mh.write_seed(self.root)
        first = (self.root / mh.SEED).read_bytes()
        stored(self.root, "20251103").touch()
        mh.write_seed(self.root)
        self.assertEqual((self.root / mh.SEED).read_bytes(), first)


    def test_packing_refuses_to_drop_days_the_seed_already_has(self) -> None:
        # 在資料比較少的機器上重打（例如沒有資料的工作副本），不能把入庫的那一年悄悄換成比較少的一包。
        self.keep("20251103")
        self.keep("20251104")
        mh.write_seed(self.root)
        before = (self.root / mh.SEED).read_bytes()
        stored(self.root, "20251103").unlink()
        with self.assertRaises(ValueError) as refused:
            mh.write_seed(self.root)
        self.assertIn("20251103", str(refused.exception))
        self.assertEqual((self.root / mh.SEED).read_bytes(), before)
        self.keep("20251103")
        self.keep("20251105")                                   # 只多不少就可以重打
        self.assertEqual(mh.write_seed(self.root)["days"], 3)

    def test_packing_an_empty_store_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            mh.write_seed(self.root)
        self.assertFalse((self.root / mh.SEED).exists())


class CoverageTest(unittest.TestCase):
    def test_lists_trading_days_without_an_original_since_the_first_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for day in ("20251103", "20251105"):
                path = stored(root, day)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}", encoding="utf-8")
            (stored(root, "20251103").parent / "margin-notes.json").write_text("{}", encoding="utf-8")
            calendar = ["2025-10-31", "2025-11-03", "2025-11-04", "2025-11-05", "2025-11-06"]
            report = mh.coverage(root, calendar)
            # 起點以前的交易日不算缺；起點之後每個交易日都要有。
            self.assertEqual(report, {"first": "2025-11-03", "last": "2025-11-05", "held": 2,
                                      "missing": ["2025-11-04", "2025-11-06"]})

    def test_nothing_stored_yet_reports_no_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(mh.coverage(Path(tmp), ["2025-11-03"]),
                             {"first": None, "last": None, "held": 0, "missing": []})


if __name__ == "__main__":
    unittest.main()
