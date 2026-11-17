"""把 build_stock_pack 產出的 seed.jsonl 分批匯入 D1（0004 的資料表），可續傳、預設不執行。

順序依 .scratch/chart-screening-workbench/deploy-runbook.md §4：
  1. 套用 0004（wrangler d1 migrations apply，不在本工具內）
  2. import   ：只寫 staging version（stock_data_versions／listed_securities／stock_analysis／universe_outside），
                 不碰 stock_data_state；每批是一個獨立 SQL 檔，用 INSERT OR IGNORE 讓重跑冪等
  3. verify   ：核對 staging 列數與 manifest 相符
  4. activate ：單一語句切換 active（舊 active 變 previous）；staging 未完整時由 CHECK 擋下
  rollback    ：active／previous 互換，不刪任何版本

增量模式（U2，每日流程用）：
  --base-seed-dir：上一個已切換的 seed。和它逐列比對，「整列（除 version 外）完全相同」的列不重送 payload，
                   改用一條 INSERT … SELECT 從 base 版本複製到新版本（沿用）；只有變動列送原文。
                   新版本仍是完整的一份（staging → verify → activate、rollback 都照舊），
                   只在遠端 active 正好是 base 版本時才沿用，否則退回完整匯入。
  --group-bytes ：把多條 ≤90KB 的語句合進同一個 --file（D1 的 100KB 是單一語句上限，檔案上限 5GB），
                   遠端匯入的往返次數從每批一次降到每檔一次；預設 0＝每條語句一檔（M1 行為）。

安全界線：
  - 只寫 0004 新增的資料表；不含 DROP／DELETE；絕不碰 users、audit、eval_keys、api_hits。
  - D1 單一 SQL 上限 100KB，本工具每批上限 90KB；單列超過就拒絕，不截斷。
  - 預設 dry-run（只寫出批次檔與計畫）；--local 才寫本機 D1；--remote 必須另加 --confirm。
  - 進度檔記每批名稱＋SHA-256，依目標（local／remote）分開；內容變了就不跳過。

用法（在 Market--Ana 根目錄）：
  python cloud/import_d1_seed.py plan     --seed-dir .tmp/lane-A2/full-20261108-v8-liquid
  python cloud/import_d1_seed.py import   --seed-dir … --local [--persist-to DIR]
  python cloud/import_d1_seed.py verify   --seed-dir … --local
  python cloud/import_d1_seed.py activate --seed-dir … --local
  python cloud/import_d1_seed.py rollback --local
  正式：把 --local 換成 --remote --confirm。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLOUD = ROOT / "cloud"
DATABASE = "market-ana-accounts"
MAX_BATCH_BYTES = 90_000
DAILY_ROWS_WRITTEN_LIMIT = 100_000
# 0004 新增的資料表；匯入器只會寫這些（stock_data_state 只在 activate／rollback 寫）。
STAGING_TABLES = ("stock_data_versions", "listed_securities", "stock_analysis", "universe_outside", "stock_announcements",
                  "etf_securities", "fund_securities")
# 0013、0018、0026 新增、可以晚一步才套到資料庫的表：資料庫還沒有它時，匯入就略過它的批次（記在輸出裡），其他照常。
OPTIONAL_TABLES = ("stock_announcements", "etf_securities", "fund_securities")
# 增量匯入時不從前一版複製、每次整份送的表（名錄只有幾十列；前一版匯入時表可能還沒套，複製會複製到空的）。
ALWAYS_SENT_TABLES = ("etf_securities", "fund_securities")

_SECURITY_COLUMNS = ("version", "code", "name", "market", "security_type", "isin", "listed_date", "industry",
                     "cfi_code", "latest_bar_date", "available_bars", "analysis_status", "universe_rank",
                     "universe_rule")
_OUTSIDE_COLUMNS = ("version", "code", "name", "market", "universe_rank", "reason", "universe_rule")
# 12 票：ETF 名錄（0018），欄位和 build_stock_pack.ETF_COLUMNS 相同。
_ETF_COLUMNS = ("version", "code", "name", "market", "etf_type", "latest_bar_date", "available_bars", "analysis_status",
                "universe_rank", "universe_rule")
# 基金當成一般個股 03 票：基金名錄（0026 暫定），欄位和 build_stock_pack.FUND_COLUMNS 相同。
_FUND_COLUMNS = ("version", "code", "name", "full_name", "currency", "isin", "latest_bar_date", "available_bars",
                 "analysis_status")


def compact_json(value: object) -> str:
    """與 build_stock_pack._compact_json 相同的序列化，確保 D1 內 payload 與本機 staging 逐位元相同。"""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _literal(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        raise ValueError("布林值不屬於 0004 欄位")
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


@dataclass
class Seed:
    manifest: dict
    securities: list
    analyses: list
    outside: list
    announcements: list = field(default_factory=list)   # 個股頁事實區塊 D 票：每檔一列近 90 天的公告清單；舊種子沒有這種列
    etfs: list = field(default_factory=list)            # 12 票：名單內股票型 ETF 的名錄列；舊種子沒有這種列
    funds: list = field(default_factory=list)           # 基金 03 票：基金名錄列；舊種子沒有這種列

    @property
    def version(self) -> str:
        return self.manifest["version"]


@dataclass
class Batch:
    name: str
    table: str
    row_count: int
    sql: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


def load_seed(seed_dir: Path) -> Seed:
    manifest = None
    securities, analyses, outside, announcements, etfs, funds = [], [], [], [], [], []
    with (Path(seed_dir) / "seed.jsonl").open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            record = json.loads(line)
            kind, data = record.get("type"), record.get("data")
            if kind == "version":
                if manifest is not None:
                    raise ValueError("seed 有多筆 version 記錄")
                manifest = data
            elif kind == "listed_security":
                securities.append(data)
            elif kind == "stock_analysis":
                analyses.append(data)
            elif kind == "universe_outside":
                outside.append(data)
            elif kind == "stock_announcement":
                announcements.append(data)
            elif kind == "etf_security":
                etfs.append(data)
            elif kind == "fund_security":
                funds.append(data)
            else:
                raise ValueError(f"seed 第 {number} 行有未知記錄類型：{kind}")
    if manifest is None:
        raise ValueError("seed 缺 version 記錄")
    return Seed(manifest, securities, analyses, outside, announcements, etfs, funds)


def _check_seed(seed: Seed) -> None:
    manifest = seed.manifest
    if manifest.get("catalog_count") != len(seed.securities):
        raise ValueError(f"catalog_count 與 seed 名錄列數不符：{manifest.get('catalog_count')} != {len(seed.securities)}")
    if manifest.get("analysis_count") != len(seed.analyses):
        raise ValueError(f"analysis_count 與 seed 分析列數不符：{manifest.get('analysis_count')} != {len(seed.analyses)}")
    if manifest.get("etf_count", 0) != len(seed.etfs):
        raise ValueError(f"etf_count 與 seed ETF 名錄列數不符：{manifest.get('etf_count', 0)} != {len(seed.etfs)}")
    if manifest.get("fund_count", 0) != len(seed.funds):
        raise ValueError(f"fund_count 與 seed 基金名錄列數不符：{manifest.get('fund_count', 0)} != {len(seed.funds)}")
    for row in seed.analyses:
        payload = row["payload"]
        if payload.get("schema") != "stock-analysis/v2" or payload.get("code") != row["code"]:
            raise ValueError(f"分析列不是契約 v2 或代號不符：{row['code']}")


def _row_values(seed: Seed, table: str, row: dict) -> str:
    version = seed.version
    if table == "listed_securities":
        values = [version] + [row.get(column) for column in _SECURITY_COLUMNS[1:]]
    elif table == "stock_analysis":
        values = [version, row["code"], row["as_of"], compact_json(row["payload"])]
    elif table == "universe_outside":
        values = [version] + [row.get(column) for column in _OUTSIDE_COLUMNS[1:]]
    elif table == "stock_announcements":
        values = [version, row["code"], row["as_of"], compact_json(row["payload"])]
    elif table == "etf_securities":
        values = [version] + [row.get(column) for column in _ETF_COLUMNS[1:]]
    elif table == "fund_securities":
        values = [version] + [row.get(column) for column in _FUND_COLUMNS[1:]]
    else:  # pragma: no cover - 內部呼叫只有這幾種
        raise ValueError(table)
    return "(" + ", ".join(_literal(value) for value in values) + ")"


def _insert_prefix(table: str) -> str:
    columns = {
        "listed_securities": _SECURITY_COLUMNS,
        "stock_analysis": ("version", "code", "as_of", "payload"),
        "universe_outside": _OUTSIDE_COLUMNS,
        "stock_announcements": ("version", "code", "as_of", "payload"),
        "etf_securities": _ETF_COLUMNS,
        "fund_securities": _FUND_COLUMNS,
    }[table]
    return f"INSERT OR IGNORE INTO {table} ({', '.join(columns)}) VALUES\n"


def _version_sql(seed: Seed) -> str:
    manifest = seed.manifest
    values = [manifest["version"], manifest["source_date"], manifest["source_url"], manifest["source_sha256"],
              manifest["built_at"], manifest["catalog_count"], manifest["analysis_count"],
              manifest.get("universe_rule")]
    return ("INSERT OR IGNORE INTO stock_data_versions (version, source_date, source_url, source_sha256, built_at, "
            "catalog_count, analysis_count, universe_rule) VALUES\n(" + ", ".join(_literal(v) for v in values) + ");\n")


def plan_batches(seed: Seed, max_batch_bytes: int = MAX_BATCH_BYTES) -> list:
    """依表分批（版本列必須最先，因為其他表有外鍵）。每批是一條多列 INSERT OR IGNORE。"""
    _check_seed(seed)
    batches = [Batch("0000-stock_data_versions", "stock_data_versions", 1, _version_sql(seed))]
    groups = (("listed_securities", seed.securities), ("stock_analysis", seed.analyses),
              ("universe_outside", seed.outside), ("stock_announcements", seed.announcements), ("etf_securities", seed.etfs),
              ("fund_securities", seed.funds))
    for table, rows in groups:
        prefix = _insert_prefix(table)
        pending: list = []
        size = len(prefix.encode("utf-8")) + 2

        def flush() -> None:
            if pending:
                sql = prefix + ",\n".join(pending) + ";\n"
                batches.append(Batch(f"{len(batches):04d}-{table}", table, len(pending), sql))

        for row in rows:
            values = _row_values(seed, table, row)
            row_bytes = len(values.encode("utf-8")) + 2
            if len(prefix.encode("utf-8")) + row_bytes + 2 > max_batch_bytes:
                raise ValueError(f"單列超過批次上限（{table}/{row.get('code')}：{row_bytes} bytes）")
            if pending and size + row_bytes > max_batch_bytes:
                flush()
                pending, size = [], len(prefix.encode("utf-8")) + 2
            pending.append(values)
            size += row_bytes
        flush()
    for batch in batches:
        if len(batch.sql.encode("utf-8")) >= max_batch_bytes:
            raise ValueError(f"批次超過上限：{batch.name}")
    return batches


_TABLE_COLUMNS = {
    "listed_securities": _SECURITY_COLUMNS,
    "stock_analysis": ("version", "code", "as_of", "payload"),
    "universe_outside": _OUTSIDE_COLUMNS,
    "stock_announcements": ("version", "code", "as_of", "payload"),
    "etf_securities": _ETF_COLUMNS,
    "fund_securities": _FUND_COLUMNS,
}


def _row_identity(seed: Seed, table: str, row: dict) -> str:
    """除 version 外的整列內容（與寫入 D1 的字面值相同），用來判斷能否沿用 base 版本的同一列。"""
    return _row_values(seed, table, row).split(", ", 1)[1]


def _rows_by_table(seed: Seed) -> dict:
    return {"listed_securities": seed.securities, "stock_analysis": seed.analyses, "universe_outside": seed.outside,
            "stock_announcements": seed.announcements, "etf_securities": seed.etfs, "fund_securities": seed.funds}


def _copy_forward_sql(table: str, new_version: str, base_version: str, codes: list) -> str:
    columns = _TABLE_COLUMNS[table]
    rest = ", ".join(columns[1:])
    code_list = ", ".join(_literal(code) for code in codes)
    return (f"INSERT OR IGNORE INTO {table} ({', '.join(columns)})\n"
            f"SELECT {_literal(new_version)}, {rest} FROM {table}\n"
            f"WHERE version = {_literal(base_version)} AND code IN ({code_list});\n")


def plan_incremental(seed: Seed, base: Seed, max_batch_bytes: int = MAX_BATCH_BYTES) -> tuple:
    """新版本＝變動列送原文＋未變列從 base 版本複製。回傳 (batches, stats)。

    沿用的前提是 base 版本已完整寫入且是遠端目前的 active（呼叫端負責核對）。
    同一代號在 base 裡內容完全相同才沿用；代號新增、刪除或任何欄位不同都送原文。
    """
    _check_seed(seed)
    _check_seed(base)
    if base.version == seed.version:
        raise ValueError("base 與新 seed 是同一個版本，沒有增量可言")
    batches = [Batch("0000-stock_data_versions", "stock_data_versions", 1, _version_sql(seed))]
    stats = {"base_version": base.version, "copied": {}, "sent": {}}
    base_rows = _rows_by_table(base)
    for table, rows in _rows_by_table(seed).items():
        # 12 票：ETF 名錄每次整份送（只有幾十列）。前一版匯入時資料庫可能還沒有 0018、ETF 表被略過，從前一版複製會是空的。
        old = {} if table in ALWAYS_SENT_TABLES else {row["code"]: _row_identity(base, table, row) for row in base_rows[table]}
        unchanged = [row["code"] for row in rows if old.get(row["code"]) == _row_identity(seed, table, row)]
        unchanged_set = set(unchanged)
        changed = [row for row in rows if row["code"] not in unchanged_set]
        stats["copied"][table], stats["sent"][table] = len(unchanged), len(changed)
        # 未變列：一條 INSERT … SELECT；代號清單過長時切成多條，每條仍 < max_batch_bytes。
        chunk: list = []
        for code in unchanged:
            if chunk and len(_copy_forward_sql(table, seed.version, base.version, chunk + [code]).encode()) >= max_batch_bytes:
                batches.append(Batch(f"{len(batches):04d}-{table}-copy", table, len(chunk),
                                     _copy_forward_sql(table, seed.version, base.version, chunk)))
                chunk = []
            chunk.append(code)
        if chunk:
            batches.append(Batch(f"{len(batches):04d}-{table}-copy", table, len(chunk),
                                 _copy_forward_sql(table, seed.version, base.version, chunk)))
        # 變動列：沿用完整模式的分批規則。
        prefix = _insert_prefix(table)
        pending: list = []
        size = len(prefix.encode("utf-8")) + 2
        for row in changed:
            values = _row_values(seed, table, row)
            row_bytes = len(values.encode("utf-8")) + 2
            if len(prefix.encode("utf-8")) + row_bytes + 2 > max_batch_bytes:
                raise ValueError(f"單列超過批次上限（{table}/{row.get('code')}：{row_bytes} bytes）")
            if pending and size + row_bytes > max_batch_bytes:
                batches.append(Batch(f"{len(batches):04d}-{table}", table, len(pending), prefix + ",\n".join(pending) + ";\n"))
                pending, size = [], len(prefix.encode("utf-8")) + 2
            pending.append(values)
            size += row_bytes
        if pending:
            batches.append(Batch(f"{len(batches):04d}-{table}", table, len(pending), prefix + ",\n".join(pending) + ";\n"))
    for batch in batches:
        if len(batch.sql.encode("utf-8")) >= max_batch_bytes:
            raise ValueError(f"批次超過上限：{batch.name}")
    stats["payload_bytes_sent"] = sum(len(b.sql.encode("utf-8")) for b in batches)
    return batches, stats


def drop_missing_tables(batches: list, existing: set) -> tuple:
    """資料庫還沒有的「可晚一步」表（OPTIONAL_TABLES），把它的批次拿掉；回傳 (留下的批次, 略過的表)。

    程式可能比資料庫變更檔先上線：這時公告寫不進去，但不能讓整個每日發布失敗。"""
    missing = [table for table in OPTIONAL_TABLES if table not in existing]
    return [batch for batch in batches if batch.table not in missing], missing


def group_batches(batches: list, group_bytes: int = 0) -> list:
    """把多條語句合進同一個匯入檔。group_bytes<=0 時每條語句自成一檔（名稱與 SHA 與 M1 相同，舊進度檔仍有效）。

    版本列永遠先寫（外鍵）；檔內語句順序與 batches 相同。合併後的檔仍是一串 ≤90KB 的獨立語句。
    """
    if group_bytes <= 0:
        return list(batches)
    groups: list = []
    current: list = []
    size = 0
    for batch in batches:
        batch_bytes = len(batch.sql.encode("utf-8"))
        if current and size + batch_bytes > group_bytes:
            groups.append(current)
            current, size = [], 0
        current.append(batch)
        size += batch_bytes
    if current:
        groups.append(current)
    merged = []
    for index, group in enumerate(groups):
        tables = sorted({b.table for b in group})
        merged.append(Batch(f"g{index:03d}-{len(group)}stmt", "+".join(tables), sum(b.row_count for b in group),
                            "".join(b.sql for b in group)))
    return merged


def choose_base(active_version, base: Seed | None):
    """只有遠端 active 正好是 base 版本時才沿用；否則回 None（完整匯入）並附原因。"""
    if base is None:
        return None, "no-base-seed"
    if active_version != base.version:
        return None, f"active-is-{active_version or 'none'}-not-base"
    return base, "base-is-active"


def verify_sql(version: str) -> str:
    v = _literal(version)
    return ("SELECT "
            f"(SELECT COUNT(*) FROM stock_data_versions WHERE version = {v}), "
            f"(SELECT COUNT(*) FROM listed_securities WHERE version = {v}), "
            f"(SELECT COUNT(*) FROM stock_analysis WHERE version = {v}), "
            f"(SELECT COUNT(*) FROM universe_outside WHERE version = {v});")


def activate_sql(seed: Seed, updated_at: str) -> str:
    """單一語句切換：staging 不完整時，SELECT 產生 NULL active_version，被 NOT NULL 擋下而整句失敗。"""
    v, at = _literal(seed.version), _literal(updated_at)
    complete = (f"(SELECT COUNT(*) FROM listed_securities WHERE version = {v}) = {int(seed.manifest['catalog_count'])} "
                f"AND (SELECT COUNT(*) FROM stock_analysis WHERE version = {v}) = {int(seed.manifest['analysis_count'])} "
                f"AND (SELECT COUNT(*) FROM universe_outside WHERE version = {v}) = {len(seed.outside)}")
    return ("INSERT INTO stock_data_state (key, active_version, previous_version, updated_at)\n"
            f"SELECT 'current', CASE WHEN {complete} THEN {v} END, NULL, {at}\n"
            "ON CONFLICT(key) DO UPDATE SET\n"
            "  previous_version = CASE WHEN stock_data_state.active_version = excluded.active_version\n"
            "                          THEN stock_data_state.previous_version ELSE stock_data_state.active_version END,\n"
            "  active_version = excluded.active_version,\n"
            "  updated_at = excluded.updated_at;\n")


def rollback_sql(updated_at: str) -> str:
    """active／previous 互換；沒有 previous 時 active 會變 NULL，被 NOT NULL 擋下，不會清空 active。"""
    return ("UPDATE stock_data_state SET active_version = previous_version, previous_version = active_version, "
            f"updated_at = {_literal(updated_at)} WHERE key = 'current';\n")


class Progress:
    """每批完成後立刻落檔（原子寫入）；目標不同的進度互不影響。"""

    def __init__(self, path: Path, target: str) -> None:
        self.path = Path(path)
        self.target = target
        self.data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    def _bucket(self) -> dict:
        return self.data.setdefault(self.target, {})

    def is_done(self, batch: Batch) -> bool:
        entry = self._bucket().get(batch.name)
        return bool(entry) and entry.get("sha256") == batch.sha256

    def mark_done(self, batch: Batch, detail: dict | None = None) -> None:
        self._bucket()[batch.name] = {"sha256": batch.sha256, "rows": batch.row_count,
                                      "at": datetime.now(timezone.utc).isoformat(), **(detail or {})}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)


def parse_args(argv: list | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("plan", "import", "verify", "activate", "rollback"))
    parser.add_argument("--seed-dir", type=Path)
    parser.add_argument("--work-dir", type=Path, help="批次檔與進度檔目錄（預設 <seed-dir>/d1-import）")
    parser.add_argument("--database", default=DATABASE)
    parser.add_argument("--persist-to", type=Path, help="本機 D1 存放目錄（只配 --local）")
    parser.add_argument("--max-batch-bytes", type=int, default=MAX_BATCH_BYTES)
    parser.add_argument("--stop-after", type=int, help="只執行 N 批後停止（測試中斷續跑用）")
    parser.add_argument("--base-seed-dir", type=Path,
                        help="增量：上一個已切換的 seed；遠端 active 正好是它時，未變列從它沿用")
    parser.add_argument("--assume-active",
                        help="增量 plan 用：不查 D1，直接當作 active 是這個版本（dry-run／測試）")
    parser.add_argument("--group-bytes", type=int, default=0,
                        help="把多條語句合進同一個匯入檔的上限（bytes）；0＝每條一檔")
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--local", action="store_true")
    target.add_argument("--remote", action="store_true")
    parser.add_argument("--confirm", action="store_true", help="--remote 必須加上才會真的寫入")
    return parser.parse_args(argv)


def will_execute(args: argparse.Namespace) -> bool:
    return bool(args.local or (args.remote and args.confirm))


def _wrangler(args: argparse.Namespace, *extra: str) -> subprocess.CompletedProcess:
    command = ["npx", "--yes", "wrangler@4", "d1", "execute", args.database, "--json", "--yes"]
    command.append("--remote" if args.remote else "--local")
    if args.local and args.persist_to:
        # wrangler 在 cloud/ 下執行；先轉絕對路徑，避免相對路徑落到別的本機 D1。
        command += ["--persist-to", str(Path(args.persist_to).resolve())]
    command += list(extra)
    return subprocess.run(command, cwd=CLOUD, capture_output=True, text=True, encoding="utf-8",
                          shell=(os.name == "nt"))


def _execute_file(args: argparse.Namespace, path: Path) -> dict:
    for attempt in range(3):
        result = _wrangler(args, "--file", str(path.resolve()))
        if result.returncode == 0:
            return {"stdout_tail": result.stdout.strip()[-300:]}
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"wrangler 執行失敗：{path.name}\n{result.stdout[-800:]}\n{result.stderr[-800:]}")


def _query(args: argparse.Namespace, sql: str) -> list:
    result = _wrangler(args, "--command", sql)
    if result.returncode != 0:
        raise RuntimeError(f"wrangler 查詢失敗：{result.stdout[-800:]}\n{result.stderr[-800:]}")
    payload = json.loads(result.stdout[result.stdout.index("["):])
    return payload[0]["results"]


def _work_dir(args: argparse.Namespace) -> Path:
    return args.work_dir or (args.seed_dir / "d1-import")


def main(argv: list | None = None) -> int:
    args = parse_args(argv)
    target = "remote" if args.remote else "local"
    if args.command == "rollback":
        sql = rollback_sql(datetime.now(timezone.utc).isoformat())
        if not will_execute(args):
            print("[dry-run] 不執行。將執行：\n" + sql)
            return 0
        work = args.work_dir or (ROOT / ".tmp" / "d1-import")
        work.mkdir(parents=True, exist_ok=True)
        path = work / "rollback.sql"
        path.write_text(sql, encoding="utf-8")
        _execute_file(args, path)
        print(json.dumps(_query(args, "SELECT active_version, previous_version FROM stock_data_state"),
                         ensure_ascii=False))
        return 0

    if args.seed_dir is None:
        raise SystemExit("需要 --seed-dir")
    seed = load_seed(args.seed_dir)
    work = _work_dir(args)
    mode, incremental_stats = "full", None
    statements = None
    if args.base_seed_dir is not None and args.command in ("plan", "import"):
        base_seed = load_seed(args.base_seed_dir)
        if args.assume_active is not None:
            active = args.assume_active or None
        elif will_execute(args):
            rows_state = _query(args, "SELECT active_version FROM stock_data_state WHERE key = 'current'")
            active = rows_state[0]["active_version"] if rows_state else None
        else:
            active = None
        chosen, reason = choose_base(active, base_seed)
        if chosen is not None:
            statements, incremental_stats = plan_incremental(seed, chosen, args.max_batch_bytes)
            mode = "incremental"
        incremental_stats = {**(incremental_stats or {}), "base_decision": reason}
    if statements is None:
        statements = plan_batches(seed, args.max_batch_bytes)
    skipped_tables: list = []
    if args.command == "import" and will_execute(args):
        names = ", ".join(_literal(table) for table in OPTIONAL_TABLES)
        existing = {row["name"] for row in _query(args, f"SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ({names})")}
        statements, skipped_tables = drop_missing_tables(statements, existing | set(STAGING_TABLES) - set(OPTIONAL_TABLES))
    batches = group_batches(statements, args.group_bytes)
    batch_dir = work / "batches"
    batch_dir.mkdir(parents=True, exist_ok=True)
    for batch in batches:
        (batch_dir / f"{batch.name}.sql").write_text(batch.sql, encoding="utf-8", newline="\n")
    rows = sum(batch.row_count for batch in statements)
    summary = {"version": seed.version, "mode": mode, "batches": len(batches), "statements": len(statements),
               "rows_written": rows, "daily_limit": DAILY_ROWS_WRITTEN_LIMIT,
               "max_batch_bytes": max(len(b.sql.encode()) for b in statements),
               "max_file_bytes": max(len(b.sql.encode()) for b in batches),
               "by_table": {t: sum(b.row_count for b in statements if b.table == t) for t in STAGING_TABLES}}
    if incremental_stats is not None:
        summary["incremental"] = incremental_stats
    if skipped_tables:
        summary["skipped_tables"] = skipped_tables   # 資料庫還沒套對應的變更檔
    if rows >= DAILY_ROWS_WRITTEN_LIMIT:
        raise SystemExit(f"總寫入列數 {rows} 超過每日上限")

    if args.command == "plan":
        (work / "plan.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False))
        return 0

    if not will_execute(args):
        print(f"[dry-run] 目標 {target} 不執行（--remote 需加 --confirm；本機需 --local）。計畫：")
        print(json.dumps(summary, ensure_ascii=False))
        return 0

    if args.command == "import":
        progress = Progress(work / "progress.json", target=target)
        executed = skipped = 0
        for batch in batches:
            if progress.is_done(batch):
                skipped += 1
                continue
            if args.stop_after is not None and executed >= args.stop_after:
                print(f"--stop-after {args.stop_after}：在 {batch.name} 前停止")
                break
            detail = _execute_file(args, batch_dir / f"{batch.name}.sql")
            progress.mark_done(batch, detail)
            executed += 1
        print(json.dumps({"executed": executed, "skipped": skipped, **summary}, ensure_ascii=False))
        return 0

    if args.command == "verify":
        row = _query(args, verify_sql(seed.version))[0]
        actual = list(row.values())
        expected = [1, len(seed.securities), len(seed.analyses), len(seed.outside)]
        print(json.dumps({"expected": expected, "actual": actual}, ensure_ascii=False))
        return 0 if actual == expected else 1

    if args.command == "activate":
        path = work / "activate.sql"
        path.write_text(activate_sql(seed, datetime.now(timezone.utc).isoformat()), encoding="utf-8")
        _execute_file(args, path)
        print(json.dumps(_query(args, "SELECT active_version, previous_version FROM stock_data_state"),
                         ensure_ascii=False))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
