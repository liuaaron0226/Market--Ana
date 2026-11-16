"""個股融資原件的長期保存區：搬入補抓的日子、每日跟上、盤點缺日（融資維持率（估）要連續、長期的每日原件）。"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import sys
import tarfile
import tempfile
import unittest
import unittest.mock
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


QUOTE_FIELDS = ["證券代號", "證券名稱", "成交股數", "成交筆數", "成交金額", "開盤價", "最高價", "最低價", "收盤價"]


def quotes(root: Path, day: str, rows: list[list[str]] | None = None, *, date: str | None = None) -> Path:
    """模擬每日抓取存下的上市每日收盤行情原件（MI_INDEX，gzip）。"""
    if rows is None:
        rows = [["2330", "台積電", "44,725,218", "197,800", "99,993,671,215", "2,210.00", "2,290.00", "2,190.00", "2,235.00"],
                ["2317", "鴻海", "1,000", "3", "250,500", "250.00", "251.00", "249.00", "250.00"],
                ["00625K", "富邦上証+R", "0", "0", "0", "--", "--", "--", "--"]]     # 當天沒有成交
    payload = {"stat": "OK", "date": date or day, "tables": [
        {"title": "價格指數(臺灣證券交易所)", "fields": ["指數", "收盤指數"], "data": [["發行量加權股價指數", "40,000.00"]]},
        {"title": "每日收盤行情(全部(不含權證、牛熊證、可展延牛熊證))", "fields": QUOTE_FIELDS, "data": rows}]}
    path = root / "data/stock-history/raw" / day[:4] / f"MI_INDEX-{day}.json.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8")))
    return path


def summary(root: Path, day: str) -> Path:
    """長期保存區裡那一天的行情摘要。"""
    return root / "data/margin-history/prices" / f"prices-{day}.json"


class PriceSummaryTest(unittest.TestCase):
    """融資維持率（估）每天從起點重播，新買進的張數要用當天的成交均價入帳、維持率要用當天的收盤算。
    每日收盤行情的原件在雲端只留最近一段，所以把重播要的三個數字（收盤、成交股數、成交金額）另外摘一份長期留著。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "root"
        self.elsewhere = Path(self.tmp.name) / "elsewhere"
        self.root.mkdir()
        self.elsewhere.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def keep(self, day: str) -> None:
        fetch(self.root, day)
        mh.keep_up(self.root)

    def test_each_day_keeps_close_volume_and_turnover_of_every_traded_security(self) -> None:
        self.keep("20261106")
        quotes(self.root, "20261106")
        self.assertEqual(mh.keep_up_prices(self.root), {"stored": ["20261106"], "skipped": []})
        self.assertEqual(json.loads(summary(self.root, "20261106").read_text(encoding="utf-8")), {
            "date": "20261106",
            "prices": {"2330": [2235.0, 44725218, 99993671215], "2317": [250.0, 1000, 250500]}})   # 沒有成交的那一檔不放

    def test_only_days_from_the_first_stored_margin_day_on_are_kept(self) -> None:
        # 行情原件從 2016 年就有；重播從長期保存區最早那一份融資原件開始，更早的行情用不到。
        self.keep("20261106")
        for day in ("20261105", "20261106", "20261109"):
            quotes(self.root, day)
        self.assertEqual(mh.keep_up_prices(self.root)["stored"], ["20261106", "20261109"])
        self.assertFalse(summary(self.root, "20261105").exists())

    def test_nothing_is_kept_before_any_margin_day_is_stored(self) -> None:
        quotes(self.root, "20261106")
        self.assertEqual(mh.keep_up_prices(self.root), {"stored": [], "skipped": []})

    def test_day_already_kept_is_not_rewritten(self) -> None:
        self.keep("20261106")
        quotes(self.root, "20261106")
        mh.keep_up_prices(self.root)
        before = summary(self.root, "20261106").read_bytes()
        quotes(self.root, "20261106", [["2330", "台積電", "1", "1", "1", "1.00", "1.00", "1.00", "1.00"]])
        self.assertEqual(mh.keep_up_prices(self.root), {"stored": [], "skipped": []})
        self.assertEqual(summary(self.root, "20261106").read_bytes(), before)

    def test_original_that_cannot_be_used_is_not_kept_and_is_reported(self) -> None:
        self.keep("20261105")
        quotes(self.root, "20261105", date="20261104")                       # 內容日期和檔名不符
        quotes(self.root, "20261106", rows=[])                               # 沒有任何一檔的行情
        quotes(self.root, "20261109").write_bytes(b"not gzip")               # 檔案壞了
        report = mh.keep_up_prices(self.root)
        self.assertEqual(report["stored"], [])
        self.assertEqual(report["skipped"], [{"day": "20261105", "reason": "官方回應的日期和檔名不符"},
                                             {"day": "20261106", "reason": "沒有任何一檔的收盤行情"},
                                             {"day": "20261109", "reason": "原件讀不了（BadGzipFile）"}])
        self.assertFalse((self.root / "data/margin-history/prices").exists())

    def test_seed_brings_the_price_summaries_to_a_machine_without_the_originals(self) -> None:
        for day in ("20251103", "20251104"):
            self.keep(day)
            quotes(self.root, day)
        mh.keep_up_prices(self.root)
        packed = mh.write_seed(self.root, "prices")
        self.assertEqual((packed["days"], packed["first"], packed["last"]), (2, "20251103", "20251104"))
        target = self.elsewhere / mh.PRICE_SEED
        target.parent.mkdir(parents=True)
        target.write_bytes((self.root / mh.PRICE_SEED).read_bytes())
        self.assertEqual(mh.restore_seed(self.elsewhere, "prices"), ["20251103", "20251104"])
        for day in ("20251103", "20251104"):
            self.assertEqual(summary(self.elsewhere, day).read_bytes(), summary(self.root, day).read_bytes())
        self.assertEqual(mh.restore_seed(self.elsewhere, "prices"), [])
        # 兩種種子各放各的：融資原件的種子不會放出行情摘要，反過來也一樣。
        self.assertEqual(mh.restore_seed(self.elsewhere), [])
        self.assertFalse((self.elsewhere / "data/margin-history/raw").exists())

    def test_restore_command_brings_a_fresh_checkout_up_to_date(self) -> None:
        # 本機第一次用（或從雲端還原之後）：一個指令把兩包種子放回去，並把手上行情原件還沒摘的日子摘好。
        import contextlib
        for day in ("20251103", "20251104"):
            self.keep(day)
            quotes(self.root, day)
        mh.keep_up_prices(self.root)
        for seed in (mh.SEED, mh.PRICE_SEED):
            mh.write_seed(self.root, "raw" if seed == mh.SEED else "prices")
            (self.elsewhere / seed).parent.mkdir(parents=True, exist_ok=True)
            (self.elsewhere / seed).write_bytes((self.root / seed).read_bytes())
        quotes(self.elsewhere, "20251105")                       # 這台機器自己抓到的、種子裡沒有的一天
        out = io.StringIO()
        with contextlib.redirect_stdout(out), unittest.mock.patch.object(sys.stdout, "reconfigure", create=True):
            self.assertEqual(mh.main(["--root", str(self.elsewhere), "restore"]), 0)
        # 上櫃那兩包（10 票）在這台還沒有：放回 0 天、跟上 0 天。
        self.assertEqual(json.loads(out.getvalue()), {"raw": 2, "prices": 2, "otc-raw": 0, "otc-prices": 0,
                                                      "prices_kept": {"stored": 1, "skipped": []},
                                                      "otc_kept": {"stored": 0, "skipped": []},
                                                      "otc_prices_kept": {"stored": 0, "skipped": []}})
        self.assertEqual(sorted(p.name for p in (self.elsewhere / "data/margin-history/prices").iterdir()),
                         ["prices-20251103.json", "prices-20251104.json", "prices-20251105.json"])
        self.assertTrue(stored(self.elsewhere, "20251104").exists())

    def test_the_estimate_reads_what_is_kept_here(self) -> None:
        # 抓取存下的兩種原件 → 長期保存區 → 估算，接得起來：起點 2025-11-03 當天 2330 有 100 張（當天沒有買進）、
        # 成交均價＝250,000 ÷ 1,000＝250、收盤 255。成本＝250 × 起點係數 0.9369126317402282＝234.2282；
        # 估計的全市場融資金額＝100 張 × 1,000 × 234.2282 × 0.6＝14,053,689 元，官方給 14,054 仟元 → 差 −0.00%。
        from unittest import mock
        from twon_analysis import margin_maintenance
        payload = official("20251103", [["2330", "台積電", "0", "0", "0", "100", "100"]])
        payload["tables"][0]["data"][1][5] = "14,054"
        fetch(self.root, "20251103", payload)
        mh.keep_up(self.root)
        quotes(self.root, "20251103", [["2330", "台積電", "1,000", "3", "250,000", "250.00", "256.00", "249.00", "255.00"]])
        mh.keep_up_prices(self.root)
        with mock.patch.object(margin_maintenance, "ROOT", self.root):
            check = margin_maintenance.daily_check("2025-11-03")
            estimate = margin_maintenance.estimated_range(code="2330", market="上市", kind="common", as_of="2025-11-03",
                                                          capital_events=[])
        self.assertEqual((check["status"], check["estimated"], check["official"], check["stocks"]),
                         ("ok", 14053689, 14054000, 1))
        # 維持率＝255 ÷（234.2282 × 0.6）＝181.45%
        self.assertEqual([round(estimate[0], 2), round(estimate[1], 2), estimate[2]], [181.45, 181.45, "2025-11-03"])


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


# ---- 上櫃（10 票）：E 票每天抓的上櫃融資原件（data/otc-credit/margin，雲端只留最近 30 份）另外長期留一份 ----

OTC_FIELDS = ["代號", "名稱", "前資餘額(張)", "資買", "資賣", "現償", "資餘額", "資屬證金", "資使用率(%)", "資限額",
              "前券餘額(張)", "券賣", "券買", "券償", "券餘額", "券屬證金", "券使用率(%)", "券限額", "資券相抵(張)", "備註"]


def otc_official(day: str, rows: list[list[str]] | None = None, *, total_thousand: str | None = "111,172,301",
                 stat: str = "ok") -> dict:
    """櫃買「上櫃股票融資融券餘額」的官方回應（只寫到融資那幾欄，其餘補空字串）。表尾第二列是全市場融資金（仟元）。"""
    pad = lambda row: row + [""] * (len(OTC_FIELDS) - len(row))   # noqa: E731
    rows = [["6488", "環球晶", "1,000", "50", "40", "0", "1,010"]] if rows is None else rows
    summary = [pad(["", "合計(張)", "2,103,219", "80,272", "66,294", "1,731", "2,115,466"])]
    if total_thousand is not None:
        summary.append(pad(["", "融資金(仟元)", "109,742,145", "6,371,359", "4,856,702", "84,501", total_thousand]))
    return {"date": day, "stat": stat, "tables": [
        {"title": "上櫃股票融資融券餘額", "fields": OTC_FIELDS, "data": [pad(row) for row in rows], "summary": summary}]}


def otc_fetch(root: Path, day: str, payload: dict | None = None) -> Path:
    """模擬 E 票的每日抓取（daily_ingest.otc_credit_day）把當天的上櫃融資存進 data/otc-credit/margin。"""
    path = root / "data/otc-credit/margin" / f"{day}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"fetched_at_utc": "t", "source_url": "u", "sha256": "x", "data": otc_official(day) if payload is None else payload}
    path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return path


def otc_stored(root: Path, day: str) -> Path:
    return root / "data/margin-history/otc-raw" / f"otc-margin-{day}.json"


OTC_QUOTE_HEADER = ('"代號","名稱","收盤","漲跌","開盤","最高","最低","均價","成交股數","成交金額(元)","成交筆數","最後買價",'
                    '"最後買量(千股)","最後賣價","最後賣量(千股)","發行股數","次日參考價","次日漲停價","次日跌停價"')
OTC_QUOTE_ROWS = [("6488", "400.00", "2,000", "801,000"), ("00679B", "27.43", "25,110,647", "689,398,743"),
                  ("731234", "1.20", "1,000", "1,200"),     # 權證：一天上萬檔、融資用不到，不留
                  ("8069", "---", "0", "0")]                # 當天沒有成交


def otc_quotes(root: Path, day: str, rows: list[tuple] | None = None, *, roc_date: str | None = None) -> Path:
    """模擬每日抓取存下的上櫃每日收盤行情原件（data/otc-history/raw/YYYYMMDD.csv，MS950）。rows：（代號, 收盤, 成交股數, 成交金額）。"""
    roc = roc_date or f"{int(day[:4]) - 1911}/{day[4:6]}/{day[6:]}"
    lines = ["上櫃股票行情(含等價、零股、盤後、鉅額交易)", f"資料日期:{roc}", OTC_QUOTE_HEADER]
    for code, close, volume, turnover in (OTC_QUOTE_ROWS if rows is None else rows):
        cells = [code, "測試", close, "+0.10", close, close, close, close, volume, turnover, "1", close, "1", close, "1",
                 "1,000", close, close, close]
        lines.append(",".join(f'"{cell}"' for cell in cells))
    lines.append('總成交金額,"1",')
    path = root / "data/otc-history/raw" / f"{day}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(("\r\n".join(lines) + "\r\n").encode("cp950"))
    return path


def otc_summary(root: Path, day: str) -> Path:
    return root / "data/margin-history/otc-prices" / f"otc-prices-{day}.json"


def run_main(argv: list[str]) -> dict:
    import contextlib
    out = io.StringIO()
    with contextlib.redirect_stdout(out), unittest.mock.patch.object(sys.stdout, "reconfigure", create=True):
        code = mh.main(argv)
    assert code == 0, code
    return json.loads(out.getvalue())


class OtcStoreTest(unittest.TestCase):
    """上櫃融資維持率（估）也要連續、長期的每日原件：E 票存在 data/otc-credit/margin 的那一份在雲端只留最近 30 份，
    所以和上市一樣另外留一份不裁的（data/margin-history/otc-raw），重播要的行情另外摘一份（otc-prices）。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "root"
        self.elsewhere = Path(self.tmp.name) / "elsewhere"
        self.root.mkdir()
        self.elsewhere.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_days_fetched_by_the_daily_run_are_copied_byte_for_byte_and_survive_the_trim(self) -> None:
        sources = [otc_fetch(self.root, day) for day in ("20251103", "20251104")]
        originals = [source.read_bytes() for source in sources]
        self.assertEqual(mh.keep_up_otc(self.root), {"stored": ["20251103", "20251104"], "skipped": []})
        for day, original in zip(("20251103", "20251104"), originals):
            self.assertEqual(otc_stored(self.root, day).read_bytes(), original)
        sources[0].unlink()                                  # 雲端工作集把舊的修掉之後，長期保存區的還在
        self.assertEqual(mh.keep_up_otc(self.root), {"stored": [], "skipped": []})
        self.assertTrue(otc_stored(self.root, "20251103").exists())
        self.assertEqual(sorted(mh.held_days(self.root, "otc-raw")), ["20251103", "20251104"])

    def test_day_already_stored_is_not_rewritten(self) -> None:
        otc_fetch(self.root, "20251103")
        mh.keep_up_otc(self.root)
        before = otc_stored(self.root, "20251103").read_bytes()
        otc_fetch(self.root, "20251103", otc_official("20251103", [["6488", "環球晶", "1", "1", "1", "1", "1"]]))
        self.assertEqual(mh.keep_up_otc(self.root), {"stored": [], "skipped": []})
        self.assertEqual(otc_stored(self.root, "20251103").read_bytes(), before)

    def test_day_that_cannot_be_used_is_not_stored_and_is_reported(self) -> None:
        otc_fetch(self.root, "20251103", otc_official("20251104"))                       # 內容日期和檔名不符
        otc_fetch(self.root, "20251104", otc_official("20251104", []))                   # 沒有個股
        otc_fetch(self.root, "20251105", otc_official("20251105", total_thousand=None))  # 沒有全市場融資金
        otc_fetch(self.root, "20251106", otc_official("20251106", stat="error"))
        otc_fetch(self.root, "20251107").write_text("壞掉", encoding="utf-8")
        report = mh.keep_up_otc(self.root)
        self.assertEqual(report["stored"], [])
        self.assertEqual(report["skipped"], [{"day": "20251103", "reason": "官方回應的日期和檔名不符"},
                                             {"day": "20251104", "reason": "沒有個股融資明細"},
                                             {"day": "20251105", "reason": "沒有全市場融資金額"},
                                             {"day": "20251106", "reason": "官方回應是查無資料"},
                                             {"day": "20251107", "reason": "官方回應是查無資料"}])
        self.assertFalse((self.root / "data/margin-history/otc-raw").exists())

    def test_price_summary_keeps_stocks_and_etfs_from_the_first_stored_margin_day_on(self) -> None:
        otc_fetch(self.root, "20251104")
        mh.keep_up_otc(self.root)
        for day in ("20251103", "20251104", "20251105"):
            otc_quotes(self.root, day)
        self.assertEqual(mh.keep_up_otc_prices(self.root), {"stored": ["20251104", "20251105"], "skipped": []})
        self.assertFalse(otc_summary(self.root, "20251103").exists())
        # 權證不留，當天沒有成交（收盤是 ---）的也不放。
        self.assertEqual(json.loads(otc_summary(self.root, "20251104").read_text(encoding="utf-8")), {
            "date": "20251104", "prices": {"6488": [400.0, 2000, 801000], "00679B": [27.43, 25110647, 689398743]}})
        self.assertEqual(mh.keep_up_otc_prices(self.root), {"stored": [], "skipped": []})

    def test_price_original_that_cannot_be_used_is_reported(self) -> None:
        otc_fetch(self.root, "20251103")
        mh.keep_up_otc(self.root)
        otc_quotes(self.root, "20251103", roc_date="114/11/04")
        otc_quotes(self.root, "20251104", rows=[])
        report = mh.keep_up_otc_prices(self.root)
        self.assertEqual(report["stored"], [])
        self.assertEqual(report["skipped"], [{"day": "20251103", "reason": "原件資料日期不是 20251103"},
                                             {"day": "20251104", "reason": "沒有任何一檔的收盤行情"}])

    def test_nothing_is_kept_before_any_otc_margin_day_is_stored(self) -> None:
        otc_quotes(self.root, "20251103")
        self.assertEqual(mh.keep_up_otc_prices(self.root), {"stored": [], "skipped": []})

    def test_seed_and_restore_carry_the_otc_stores_to_another_machine(self) -> None:
        for day in ("20251103", "20251104"):
            otc_fetch(self.root, day)
            otc_quotes(self.root, day)
        report = run_main(["--root", str(self.root), "seed"])
        # 上市的保存區在這台是空的：不打包、照實寫；上櫃兩包各兩天（打包前先跟上）。
        self.assertEqual((report["raw"], report["prices"]), ({"skipped": "長期保存區是空的"}, {"skipped": "長期保存區是空的"}))
        self.assertEqual((report["otc-raw"]["days"], report["otc-prices"]["days"]), (2, 2))
        for seed in (mh.OTC_SEED, mh.OTC_PRICE_SEED):
            (self.elsewhere / seed).parent.mkdir(parents=True, exist_ok=True)
            (self.elsewhere / seed).write_bytes((self.root / seed).read_bytes())
        report = run_main(["--root", str(self.elsewhere), "restore"])
        self.assertEqual((report["otc-raw"], report["otc-prices"]), (2, 2))
        for day in ("20251103", "20251104"):
            self.assertEqual(otc_stored(self.elsewhere, day).read_bytes(), otc_stored(self.root, day).read_bytes())
            self.assertEqual(otc_summary(self.elsewhere, day).read_bytes(), otc_summary(self.root, day).read_bytes())

    def test_restore_command_also_keeps_up_with_otc_days_fetched_on_this_machine(self) -> None:
        otc_fetch(self.root, "20251103")
        otc_quotes(self.root, "20251103")
        report = run_main(["--root", str(self.root), "restore"])
        self.assertEqual(report["otc_kept"], {"stored": 1, "skipped": []})
        self.assertEqual(report["otc_prices_kept"], {"stored": 1, "skipped": []})

    def test_otc_coverage_uses_the_otc_trading_days(self) -> None:
        import sqlite3
        otc_fetch(self.root, "20251103")
        otc_fetch(self.root, "20251105")
        mh.keep_up_otc(self.root)
        db = self.root / "data/otc-history/otc_history.sqlite"
        db.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(db)
        connection.execute("CREATE TABLE daily (date TEXT, code TEXT)")
        connection.executemany("INSERT INTO daily VALUES (?, '6488')",
                               [(day,) for day in ("2025-10-31", "2025-11-03", "2025-11-04", "2025-11-05")])
        connection.commit()
        connection.close()
        self.assertEqual(mh.coverage(self.root, mh.otc_trading_days(self.root), "otc-raw"),
                         {"first": "2025-11-03", "last": "2025-11-05", "held": 2, "missing": ["2025-11-04"]})

    def test_the_otc_estimate_reads_what_is_kept_here(self) -> None:
        # 抓取存下的兩種原件 → 長期保存區 → 重播接得起來：上櫃起點那天 6488 有 1,000 張、當天沒有買進，
        # 成交均價＝801,000 ÷ 2,000＝400.5。成本＝400.5 × 上櫃起點係數；官方全市場融資金從表尾讀（仟元 × 1,000）。
        from unittest import mock
        from twon_analysis import margin_maintenance as mm
        start = mm.OTC_START_DATE.replace("-", "")
        otc_fetch(self.root, start, otc_official(start, [["6488", "環球晶", "1,000", "0", "0", "0", "1,000"]],
                                                 total_thousand="240,000"))
        otc_quotes(self.root, start, [("6488", "400.00", "2,000", "801,000")])
        mh.keep_up_otc(self.root)
        mh.keep_up_otc_prices(self.root)
        with mock.patch.object(mm, "ROOT", self.root):
            board = mm.BOARDS["上櫃"]
            raw, prices = (mm.kept_days(store, board.start, board.start) for store in (board.raw, board.prices))
            *_, (day, positions, _, official) = mm.replay(raw, prices, reader=board.reader, seed_scale=board.seed_scale)
        self.assertEqual((day, official), (mm.OTC_START_DATE, 240_000_000))
        self.assertEqual(positions["6488"].balance, 1000)
        self.assertAlmostEqual(positions["6488"].high_cost, 400.5 * mm.OTC_SEED_SCALE)
        self.assertAlmostEqual(positions["6488"].low_cost, 400.5 * mm.OTC_SEED_SCALE)


if __name__ == "__main__":
    unittest.main()
