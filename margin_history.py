"""個股融資原件的長期保存區。

融資維持率（估）要靠每天的融資買賣張數一路推算成本，所以上市個股融資原件（證交所 MI_MARGN，selectType=ALL）
必須連續、長期留著。每日抓取把當天那一份存在近期明細（個股頁籌碼用，只留最近一段；首頁籌碼明細會把那裡的
每一天都列出來，所以不能把整年放在那裡），這裡另外留一份不裁的：

- ``keep_up``：把近期明細裡、長期保存區還沒有的日子原樣複製過來（每日抓取之後呼叫）。
- ``import_backfill``：把補抓留下的資料夾搬成同一種形式（更早的日子）。
- ``coverage``：從最早一份原件起，哪些交易日沒有原件。

存進來的日子不再改寫，所以不能用的那一天（日期對不上、沒有個股明細、沒有全市場融資金額）一律不收，留著缺口。
這裡不連網、不抓任何資料。

用法：
    python margin_history.py import <補抓資料夾>   # 資料夾裡要有 raw/YYYYMMDD.json.gz 與 manifest.jsonl
    python margin_history.py coverage              # 從最早一份原件起，哪些交易日沒有原件
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STORE_DIR = "data/margin-history/raw"                       # 長期保存區：全部保留
RECENT_DIR = "data/market-snapshots/stock-detail/margin"   # 每日抓取存的近期明細：只留最近一段
DAILY_FILE = re.compile(r"^margin-(\d{8})\.json$")


def stored_path(root: Path, day: str) -> Path:
    return root / STORE_DIR / f"margin-{day}.json"


def _days_in(folder: Path) -> dict[str, Path]:
    """資料夾裡每一份「margin-YYYYMMDD.json」：{YYYYMMDD: 路徑}。其他檔名不算。"""
    found = {}
    for path in folder.glob("margin-*.json"):
        match = DAILY_FILE.match(path.name)
        if match:
            found[match.group(1)] = path
    return found


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
    stored: list[str] = []
    skipped: list[dict] = []
    for day, source in sorted(_days_in(root / RECENT_DIR).items()):
        target = stored_path(root, day)
        if target.exists():
            continue
        data = source.read_bytes()
        try:
            payload = json.loads(data.decode("utf-8")).get("data")
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            payload = None
        reason = _why_unusable(payload, day)
        if reason:
            skipped.append({"day": day, "reason": reason})
            continue
        _write_atomic(target, data)
        stored.append(day)
    return {"stored": stored, "skipped": skipped}


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


def coverage(root: Path, trading_days) -> dict:
    """從手上最早一份原件起，哪些交易日沒有原件。``trading_days`` 是 YYYY-MM-DD 的交易日清單。"""
    held = sorted(f"{day[:4]}-{day[4:6]}-{day[6:]}" for day in _days_in(root / STORE_DIR))
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


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="個股融資原件的長期保存區：搬入補抓的日子、盤點缺日")
    parser.add_argument("--root", type=Path, default=ROOT, help="專案根目錄（預設是這支程式所在的資料夾）")
    commands = parser.add_subparsers(dest="command", required=True)
    importer = commands.add_parser("import", help="把補抓的資料夾搬進長期保存區")
    importer.add_argument("source", type=Path)
    commands.add_parser("coverage", help="從最早一份原件起，列出沒有原件的交易日")
    args = parser.parse_args(argv)
    if args.command == "import":
        report = import_backfill(args.root, args.source)
        report = {"imported": len(report["imported"]), "existing": len(report["existing"]), "rejected": report["rejected"]}
    else:
        report = coverage(args.root, trading_days(args.root))
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
