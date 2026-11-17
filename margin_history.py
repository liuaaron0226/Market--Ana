"""個股融資原件的長期保存區。

融資維持率（估）要靠每天的融資買賣張數一路推算成本，所以上市個股融資原件（證交所 MI_MARGN，selectType=ALL）
必須連續、長期留著。每日抓取把當天那一份存在近期明細（個股頁籌碼用，只留最近一段；首頁籌碼明細會把那裡的
每一天都列出來，所以不能把整年放在那裡），這裡另外留一份不裁的：

- ``keep_up``：把近期明細裡、長期保存區還沒有的日子原樣複製過來（每日抓取之後呼叫）。
- ``import_backfill``：把補抓留下的資料夾搬成同一種形式（更早的日子）。
- ``coverage``：從最早一份原件起，哪些交易日沒有原件。
- ``keep_up_prices``：重播還要每天的收盤與成交均價。上市每日收盤行情的原件（MI_INDEX）在雲端只留最近一段，
  所以把每一檔的收盤、成交股數、成交金額另外摘成「行情摘要」長期留著（從最早那一份融資原件的日子起）。
- ``write_seed``／``restore_seed``：把長期保存區打包成隨專案走的種子；別台機器（雲端）缺的日子從種子放回去。
  融資原件與行情摘要各一包。
- 上櫃（10 票）：櫃買的個股融資原件（E 票每天存在 data/otc-credit/margin，雲端只留最近 30 份）用 ``keep_up_otc``
  另外留一份不裁的；上櫃每日收盤行情（data/otc-history/raw 的 CSV，雲端只留最近一段）用 ``keep_up_otc_prices``
  摘成同一種行情摘要。上櫃的原件與摘要也各一包種子。

存進來的日子不再改寫，所以不能用的那一天（日期對不上、沒有個股明細、沒有全市場融資金額）一律不收，留著缺口。
這裡不連網、不抓任何資料。

用法：
    python margin_history.py import <補抓資料夾>   # 資料夾裡要有 raw/YYYYMMDD.json.gz 與 manifest.jsonl
    python margin_history.py coverage              # 從最早一份原件起，哪些交易日沒有原件
    python margin_history.py seed                  # 把長期保存區打包成 seeds/ 底下的兩包種子（要入庫）
    python margin_history.py restore               # 把每一包種子裡缺的日子放回長期保存區，並把上市、上櫃都跟上（每日抓取也會做）
    python margin_history.py otc-coverage          # 上櫃：從最早一份原件起，哪些交易日沒有原件
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import sqlite3
import sys
import tarfile
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent
STORE_DIR = "data/margin-history/raw"                       # 長期保存區：全部保留
RECENT_DIR = "data/market-snapshots/stock-detail/margin"   # 每日抓取存的近期明細：只留最近一段
SEED = "seeds/margin-history.tar.xz"                        # 隨專案走的種子：長期保存區打成一包
DAILY_FILE = re.compile(r"margin-(\d{8})\.json", re.ASCII)
PRICE_DIR = "data/margin-history/prices"                    # 行情摘要：每天每一檔的收盤、成交股數、成交金額，全部保留
QUOTES_DIR = "data/stock-history/raw"                       # 上市每日收盤行情原件（<年>/MI_INDEX-YYYYMMDD.json.gz）
PRICE_SEED = "seeds/margin-prices.tar.xz"                   # 行情摘要的種子
PRICE_FILE = re.compile(r"prices-(\d{8})\.json", re.ASCII)
QUOTES_FILE = re.compile(r"MI_INDEX-(\d{8})\.json\.gz", re.ASCII)
# 上櫃（10 票）：E 票抓的上櫃融資原件、上櫃每日收盤行情原件，與它們在長期保存區的那一份。
OTC_CREDIT_DIR = "data/otc-credit/margin"                   # E 票每日抓取存的上櫃融資（YYYYMMDD.json，雲端只留最近 30 份）
OTC_CREDIT_FILE = re.compile(r"(\d{8})\.json", re.ASCII)
OTC_STORE_DIR = "data/margin-history/otc-raw"               # 上櫃個股融資原件：全部保留
OTC_SEED = "seeds/otc-margin-history.tar.xz"
OTC_DAILY_FILE = re.compile(r"otc-margin-(\d{8})\.json", re.ASCII)
OTC_QUOTES_DIR = "data/otc-history/raw"                     # 上櫃每日收盤行情原件（YYYYMMDD.csv，MS950）
OTC_QUOTES_FILE = re.compile(r"(\d{8})\.csv", re.ASCII)
OTC_PRICE_DIR = "data/margin-history/otc-prices"            # 上櫃行情摘要
OTC_PRICE_SEED = "seeds/otc-margin-prices.tar.xz"
OTC_PRICE_FILE = re.compile(r"otc-prices-(\d{8})\.json", re.ASCII)
# 上櫃行情摘要只留股票（四位數代號）與 ETF（00 開頭）：上櫃每日行情一天一萬多檔，九成是權證，融資用不到。
# ETF 不給這一塊，但它們的融資算在櫃買公布的上櫃全市場融資金裡，每日對帳要一起加總，所以也要留。
OTC_KEPT_CODE = re.compile(r"\d{4}|00\d{3,4}[A-Z]?", re.ASCII)
# 估算讀櫃買原件時用到的欄位（twon_analysis.margin_maintenance.read_otc_margin）；少一欄就不收進長期保存區。
OTC_MARGIN_COLUMNS = ("代號", "資買", "資賣", "現償", "資餘額")
# 長期保存區的四種檔："raw"／"otc-raw" 是上市／上櫃個股融資原件、"prices"／"otc-prices" 是上市／上櫃行情摘要；
# 各自的資料夾、種子、檔名。
STORES = {"raw": (STORE_DIR, SEED, DAILY_FILE), "prices": (PRICE_DIR, PRICE_SEED, PRICE_FILE),
          "otc-raw": (OTC_STORE_DIR, OTC_SEED, OTC_DAILY_FILE), "otc-prices": (OTC_PRICE_DIR, OTC_PRICE_SEED, OTC_PRICE_FILE)}


def stored_path(root: Path, day: str) -> Path:
    return root / STORE_DIR / f"margin-{day}.json"


def _days_in(folder: Path, pattern: re.Pattern = DAILY_FILE) -> dict[str, Path]:
    """資料夾裡每一份每日檔（預設是「margin-YYYYMMDD.json」）：{YYYYMMDD: 路徑}。其他檔名不算。"""
    found = {}
    if folder.is_dir():
        for path in folder.iterdir():
            match = pattern.fullmatch(path.name)
            if match:
                found[match.group(1)] = path
    return found


def held_days(root: Path, store: str = "raw") -> dict[str, Path]:
    """長期保存區裡某一種檔的每一天：{YYYYMMDD: 路徑}。估算那一邊（twon_analysis.margin_maintenance）從這裡知道有哪幾天。"""
    folder, _, pattern = STORES[store]
    return _days_in(root / folder, pattern)


def _write_atomic(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".partial")
    partial.write_bytes(data)
    os.replace(partial, target)


def _why_unusable(payload: object, day: str) -> str | None:
    """這一天的官方回應不能拿來估算的原因；可以用就回 None。"""
    if not isinstance(payload, dict) or payload.get("stat") != "OK":
        return "官方回應是查無資料"
    if str(payload.get("date")) != day:
        return "官方回應的日期和檔名不符"
    stocks = total = False
    for table in payload.get("tables") or []:
        fields = [str(field).strip() for field in table.get("fields") or []]
        rows = table.get("data") or []
        if "代號" in fields:
            stocks = bool(rows)
        elif "項目" in fields:
            total = any("融資金額" in str(row[0]) for row in rows if row)
    if not stocks:
        return "沒有個股融資明細"
    if not total:
        return "沒有全市場融資金額"
    return None


def keep_up(root: Path) -> dict:
    """把近期明細裡、長期保存區還沒有的日子原樣複製過來。已經有的不重寫；不能用的不收並回報。"""
    return _copy_new_days(_days_in(root / RECENT_DIR), lambda day: stored_path(root, day), _why_unusable)


def _copy_new_days(sources: dict[str, Path], target_of: Callable[[str], Path],
                   why_unusable: Callable[[object, str], str | None]) -> dict:
    """每日抓取存的原件（外包格式，官方回應在 data）→ 長期保存區：還沒有的日子原樣複製；不能用的不收並回報原因。"""
    stored: list[str] = []
    skipped: list[dict] = []
    for day, source in sorted(sources.items()):
        target = target_of(day)
        if target.exists():
            continue
        data = source.read_bytes()
        try:
            payload = json.loads(data.decode("utf-8")).get("data")
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            payload = None
        reason = why_unusable(payload, day)
        if reason:
            skipped.append({"day": day, "reason": reason})
            continue
        _write_atomic(target, data)
        stored.append(day)
    return {"stored": stored, "skipped": skipped}


def _price_summary(packed: bytes, day: str) -> dict:
    """一份每日收盤行情原件 → 行情摘要 {"date", "prices": {代號: [收盤, 成交股數, 成交金額]}}；當天沒有成交的不放。
    不能用（日期對不上、沒有任何一檔的行情）就丟 ValueError。"""
    payload = json.loads(gzip.decompress(packed).decode("utf-8"))
    if not isinstance(payload, dict) or str(payload.get("date")) != day:
        raise ValueError("官方回應的日期和檔名不符")
    prices: dict[str, list] = {}
    for table in payload.get("tables") or []:
        fields = [str(field).strip() for field in table.get("fields") or []]
        if "證券代號" not in fields or "收盤價" not in fields:
            continue
        code_at, close_at = fields.index("證券代號"), fields.index("收盤價")
        volume_at, turnover_at = fields.index("成交股數"), fields.index("成交金額")
        for row in table.get("data") or []:
            close = str(row[close_at]).replace(",", "").strip()
            if close in ("", "--"):
                continue
            prices[str(row[code_at]).strip()] = [float(close), int(str(row[volume_at]).replace(",", "")),
                                                 int(str(row[turnover_at]).replace(",", ""))]
    if not prices:
        raise ValueError("沒有任何一檔的收盤行情")
    return {"date": day, "prices": prices}


def keep_up_prices(root: Path) -> dict:
    """把每日收盤行情原件摘成行情摘要：從長期保存區最早那一份融資原件的日子起，還沒摘過的每一天。
    已經有的不重寫；不能用的不收並回報。長期保存區還沒有任何融資原件時什麼都不做。"""
    originals: dict[str, Path] = {}
    for year in sorted((root / QUOTES_DIR).glob("*")):
        originals.update(_days_in(year, QUOTES_FILE))
    return _summarize_new_days(_days_in(root / STORE_DIR), originals, root / PRICE_DIR, "prices", _price_summary)


def _summarize_new_days(held: dict[str, Path], originals: dict[str, Path], folder: Path, prefix: str,
                        summarize: Callable[[bytes, str], dict]) -> dict:
    """行情原件 → 行情摘要（folder/<prefix>-YYYYMMDD.json）：從最早那一份融資原件（held）的日子起，還沒摘過的每一天。
    已經有的不重寫；不能用的不收並回報。還沒有任何融資原件時什麼都不做。"""
    stored: list[str] = []
    skipped: list[dict] = []
    if not held:
        return {"stored": stored, "skipped": skipped}
    first = min(held)
    for day, source in sorted(originals.items()):
        target = folder / f"{prefix}-{day}.json"
        if day < first or target.exists():
            continue
        try:
            summary = summarize(source.read_bytes(), day)
        except (OSError, EOFError, UnicodeDecodeError, ValueError, KeyError, IndexError, TypeError) as error:
            # gzip 壞掉是 OSError／EOFError。自己判斷出來的原因照寫（和融資原件那一邊同一種寫法）；
            # 檔案讀不了、格式不對的只寫錯誤的種類。
            reason = str(error) if type(error) is ValueError else f"原件讀不了（{type(error).__name__}）"
            skipped.append({"day": day, "reason": reason})
            continue
        _write_atomic(target, (json.dumps(summary, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
        stored.append(day)
    return {"stored": stored, "skipped": skipped}


def _why_unusable_otc(payload: object, day: str) -> str | None:
    """這一天的櫃買回應（上櫃股票融資融券餘額）不能拿來估算的原因；可以用就回 None。"""
    if not isinstance(payload, dict) or str(payload.get("stat") or "").lower() != "ok":
        return "官方回應是查無資料"
    if str(payload.get("date")) != day:
        return "官方回應的日期和檔名不符"
    tables = payload.get("tables") or []
    table = tables[0] if tables and isinstance(tables[0], dict) else {}
    fields = [str(field).strip() for field in table.get("fields") or []]
    if not table.get("data"):
        return "沒有個股融資明細"
    missing = [name for name in OTC_MARGIN_COLUMNS if name not in fields]
    if missing:
        # 存進來就不再改寫：缺一欄，之後每天的重播都會讀不了，所以這裡就擋下。
        return f"欄位跟預期不同（少了 {'、'.join(missing)}）"
    if not any(len(row) > 1 and "融資金" in str(row[1]) for row in table.get("summary") or []):
        return "沒有全市場融資金額"
    return None


def keep_up_otc(root: Path) -> dict:
    """把 E 票抓到的上櫃融資原件裡、長期保存區還沒有的日子原樣複製過來。已經有的不重寫；不能用的不收並回報。"""
    return _copy_new_days(_days_in(root / OTC_CREDIT_DIR, OTC_CREDIT_FILE),
                          lambda day: root / OTC_STORE_DIR / f"otc-margin-{day}.json", _why_unusable_otc)


def _otc_price_summary(content: bytes, day: str) -> dict:
    """一份上櫃每日收盤行情原件（CSV）→ 行情摘要，形狀和上市的相同；只留股票與 ETF，當天沒有成交的不放。
    不能用（日期對不上、欄位不對、沒有任何一檔）就丟 ValueError。"""
    import otc_history

    prices = {record["code"]: [record["close"], record["volume_shares"] or 0, record["turnover"] or 0]
              for record in otc_history.parse_csv(content, day)
              if record["close"] is not None and OTC_KEPT_CODE.fullmatch(record["code"])}
    if not prices:
        raise ValueError("沒有任何一檔的收盤行情")
    return {"date": day, "prices": prices}


def keep_up_otc_prices(root: Path) -> dict:
    """把上櫃每日收盤行情原件摘成行情摘要：從長期保存區最早那一份上櫃融資原件的日子起，還沒摘過的每一天。"""
    return _summarize_new_days(_days_in(root / OTC_STORE_DIR, OTC_DAILY_FILE), _days_in(root / OTC_QUOTES_DIR, OTC_QUOTES_FILE),
                               root / OTC_PRICE_DIR, "otc-prices", _otc_price_summary)


def import_backfill(root: Path, source: Path) -> dict:
    """把補抓的個股融資原件搬成每日抓取存的形式（抓取時間、來源網址、官方回應放在 data）。

    ``source`` 底下要有 raw/YYYYMMDD.json.gz 與 manifest.jsonl（每日一列；同一天重抓過會有好幾列，以最後一列為準）。
    """
    entries: dict[str, dict] = {}
    for line in (source / "manifest.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            entry = json.loads(line)
            entries[str(entry["day"])] = entry
    imported: list[str] = []
    existing: list[str] = []
    rejected: list[dict] = []
    for day, entry in sorted(entries.items()):
        target = stored_path(root, day)
        if target.exists():        # 已經在長期保存區的那一天不動
            existing.append(day)
            continue
        packed = source / "raw" / f"{day}.json.gz"
        if entry.get("status") != "ok":
            reason = "補抓時沒有抓成功"
        elif not packed.exists():
            reason = "清單上有、但找不到原件檔"
        else:
            body = gzip.decompress(packed.read_bytes())
            if hashlib.sha256(body).hexdigest() != entry.get("sha256"):
                reason = "內容和清單上的雜湊不符"
            else:
                payload = json.loads(body.decode("utf-8"))
                reason = _why_unusable(payload, day)
        if reason:
            rejected.append({"day": day, "reason": reason})
            continue
        wrapped = {"fetched_at_utc": entry["fetched_at_utc"], "source_url": entry["source_url"], "data": payload}
        _write_atomic(target, (json.dumps(wrapped, ensure_ascii=False) + "\n").encode("utf-8"))
        imported.append(day)
    return {"imported": imported, "existing": existing, "rejected": rejected}


def _seed_members(tar: tarfile.TarFile, pattern: re.Pattern):
    """種子裡每一份每日檔：(YYYYMMDD, 成員)。檔名不對的、不是一般檔案的都不算。"""
    for member in tar:
        match = pattern.fullmatch(member.name)
        if match and member.isfile():
            yield match.group(1), member


def write_seed(root: Path, store: str = "raw") -> dict:
    """把長期保存區的每一天打成一包種子。store："raw" 是個股融資原件，"prices" 是行情摘要。

    種子只能多不能少：保存區是空的、或少了種子已經有的日子（例如在資料比較少的機器上重打），就不打。
    同一台機器上內容相同會產生相同的檔，重打不會讓版本庫多一份。
    """
    folder, seed, pattern = STORES[store]
    days = _days_in(root / folder, pattern)
    if not days:
        raise ValueError("長期保存區是空的，不打包種子")
    if (root / seed).exists():
        with tarfile.open(root / seed, mode="r:xz") as tar:
            dropped = sorted(day for day, _ in _seed_members(tar, pattern) if day not in days)
        if dropped:
            raise ValueError(f"長期保存區少了種子已經有的 {len(dropped)} 天（{dropped[0]}～{dropped[-1]}），不打包；"
                             "先用 restore_seed 把它們放回來")
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:xz") as tar:
        for day in sorted(days):
            data = days[day].read_bytes()
            member = tarfile.TarInfo(days[day].name)
            member.size = len(data)
            tar.addfile(member, io.BytesIO(data))
    packed = buffer.getvalue()
    _write_atomic(root / seed, packed)
    return {"days": len(days), "first": min(days), "last": max(days), "bytes": len(packed)}


def restore_seed(root: Path, store: str = "raw") -> list[str]:
    """把種子裡、長期保存區還沒有的日子放回去；已經有的不動。沒有種子就什麼都不做。store 同 write_seed。

    種子是從檢查過的長期保存區打出來的，這裡照原樣放回、不再逐份檢查內容。
    """
    folder, seed_name, pattern = STORES[store]
    seed = root / seed_name
    if not seed.exists():
        return []
    restored: list[str] = []
    with tarfile.open(seed, mode="r:xz") as tar:
        for day, member in _seed_members(tar, pattern):
            target = root / folder / member.name
            if target.exists():
                continue
            _write_atomic(target, tar.extractfile(member).read())
            restored.append(day)
    return sorted(restored)


def coverage(root: Path, trading_days, store: str = "raw") -> dict:
    """從手上最早一份原件起，哪些交易日沒有原件。``trading_days`` 是 YYYY-MM-DD 的交易日清單；store 是 "raw" 或 "otc-raw"。"""
    held = sorted(f"{day[:4]}-{day[4:6]}-{day[6:]}" for day in held_days(root, store))
    if not held:
        return {"first": None, "last": None, "held": 0, "missing": []}
    have = set(held)
    missing = sorted(day for day in trading_days if day >= held[0] and day not in have)
    return {"first": held[0], "last": held[-1], "held": len(held), "missing": missing}


def trading_days(root: Path) -> list[str]:
    """上市行情庫記得的交易日（YYYY-MM-DD）；行情庫不在就回空清單。"""
    database = root / "data/stock-history/stock-history.sqlite"
    if not database.exists():
        return []
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        return [row[0] for row in connection.execute("SELECT DISTINCT date FROM source_days ORDER BY date")]
    finally:
        connection.close()


def otc_trading_days(root: Path) -> list[str]:
    """上櫃行情庫記得的交易日（YYYY-MM-DD）；行情庫不在就回空清單。"""
    database = root / "data/otc-history/otc_history.sqlite"
    if not database.exists():
        return []
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        return [row[0] for row in connection.execute("SELECT DISTINCT date FROM daily ORDER BY date")]
    finally:
        connection.close()


def _counted(kept: dict) -> dict:
    """keep_up_prices、keep_up_otc、keep_up_otc_prices 的結果改成只印天數（一次可能幾百天）。"""
    return {"stored": len(kept["stored"]), "skipped": kept["skipped"]}


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="個股融資原件的長期保存區：搬入補抓的日子、盤點缺日")
    parser.add_argument("--root", type=Path, default=ROOT, help="專案根目錄（預設是這支程式所在的資料夾）")
    commands = parser.add_subparsers(dest="command", required=True)
    importer = commands.add_parser("import", help="把補抓的資料夾搬進長期保存區")
    importer.add_argument("source", type=Path)
    commands.add_parser("coverage", help="從最早一份原件起，列出沒有原件的交易日")
    commands.add_parser("seed", help="把長期保存區打包成隨專案走的種子")
    commands.add_parser("restore", help="把種子裡缺的日子放回長期保存區，並把上市、上櫃都跟上")
    commands.add_parser("otc-coverage", help="上櫃：從最早一份原件起，列出沒有原件的交易日")
    args = parser.parse_args(argv)
    if args.command == "import":
        report = import_backfill(args.root, args.source)
        report = {"imported": len(report["imported"]), "existing": len(report["existing"]), "rejected": report["rejected"]}
    elif args.command == "seed":
        # 先把上市行情摘要、上櫃原件與行情摘要跟上，再打包；這台機器上還是空的那一種不打（照實寫）
        report = {"prices_kept": _counted(keep_up_prices(args.root)), "otc_kept": _counted(keep_up_otc(args.root)),
                  "otc_prices_kept": _counted(keep_up_otc_prices(args.root))}
        for store in STORES:
            report[store] = write_seed(args.root, store) if held_days(args.root, store) else {"skipped": "長期保存區是空的"}
    elif args.command == "restore":
        report = {store: len(restore_seed(args.root, store)) for store in STORES}
        report["prices_kept"] = _counted(keep_up_prices(args.root))
        report["otc_kept"] = _counted(keep_up_otc(args.root))
        report["otc_prices_kept"] = _counted(keep_up_otc_prices(args.root))
    elif args.command == "otc-coverage":
        report = coverage(args.root, otc_trading_days(args.root), "otc-raw")
    else:
        report = coverage(args.root, trading_days(args.root))
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
