"""每日增量抓取與建庫（U1）：只抓指定交易日缺的官方原件，再把那一天附加進既有 SQLite。

設計原則：
- 抓取一律呼叫各模組既有的 fetch（限流、遇 403／429／格式異常即停、原件已存在不重抓、不覆寫都沿用）。
  官方「無資料」（尚未公布或休市）由本模組先判斷：不寫原件、不當成 0、不停整批，回 ``not_published``。
- 附加一律用各模組既有的解析函式（parse_*），只把「整庫重建」改成「在原子替換的複本上附加一天」，
  所以附加結果和由全部原件整庫重建逐列相同（測試鎖定）。同一天已入庫就跳過（冪等）。
- 複本寫完才 os.replace 換上；中途失敗原庫不動。
"""
from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent

# 官方「查無資料」：證交所回 stat 非 OK；櫃買法人 JSON 回 stat＝無資料、行情與本益比 CSV 回 0 列。
NO_DATA_MARKERS = ("沒有符合條件", "無資料", "查無")


def _compact(day: str) -> str:
    return day.replace("-", "")


def _roc_slash(day: str) -> str:
    return f"{day[:4]}/{day[5:7]}/{day[8:]}"


def _no_data_json(content: bytes) -> bool:
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    stat = str(payload.get("stat") or "")
    return stat != "OK" and (any(mark in stat for mark in NO_DATA_MARKERS) or payload.get("total") == 0)


class _Gate:
    """先送一次請求看官方是否已公布；未公布就不交給模組 fetch（避免把查無寫成原件或停整批）。

    已公布時把同一份回應交給模組 fetch 使用，不重複請求同一天。
    """

    def __init__(self, get: Callable, is_empty: Callable[[object], bool]) -> None:
        self._get = get
        self._is_empty = is_empty
        self.first = None
        self.requests = 0

    def probe(self, *args) -> bool:
        self.requests += 1
        self.first = self._get(*args)
        return getattr(self.first, "status", 200) == 200 and self._is_empty(self.first)

    def replay(self, *args):
        if self.first is not None:
            response, self.first = self.first, None
            return response
        self.requests += 1
        return self._get(*args)


def _session_getter(module_get: Callable) -> Callable:
    """模組 http_get 的形狀是 (session, url, params)，session 不能是 None。

    沒有注入 get 時，閘道探測與模組 fetch 共用一個真實 requests.Session；呼叫端傳入的 session 參數忽略。
    """
    import requests
    session = requests.Session()
    return lambda _session, url, params: module_get(session, url, params)


def _sqlite_copy(db_path: Path) -> Path:
    partial = db_path.with_name(db_path.name + ".partial")
    partial.unlink(missing_ok=True)
    shutil.copyfile(db_path, partial)
    return partial


def _has_row(db_path: Path, sql: str, args: tuple) -> bool:
    if not db_path.exists():
        return False
    connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        return connection.execute(sql, args).fetchone() is not None
    finally:
        connection.close()


def _append(db_path: Path, write: Callable[[sqlite3.Connection], None], build_new: Callable[[], None]) -> None:
    """有庫就在複本附加後原子換上；沒有庫就交給模組整庫重建（只含既有原件）。"""
    if not db_path.exists():
        build_new()
        return
    partial = _sqlite_copy(db_path)
    connection = sqlite3.connect(partial)
    try:
        with connection:
            write(connection)
    except Exception:
        connection.close()
        partial.unlink(missing_ok=True)
        raise
    connection.close()
    os.replace(partial, db_path)


# ---- 上市日 K（stock_history，MI_INDEX） ----

def listed_quotes_day(out: Path, db_path: Path, day: str, *, get: Callable | None = None,
                      sleep: Callable = time.sleep) -> dict:
    import stock_history as sh
    import etf_history

    compact = _compact(day)
    if _has_row(db_path, "SELECT 1 FROM source_days WHERE date = ?", (day,)):
        return {"status": "already-present", "requests": 0}
    raw = sh.raw_file(out, compact)
    gate = None
    if not raw.exists():
        gate = _Gate(get or sh.http_get, lambda r: _no_data_json(r.content))
        if gate.probe(sh.MI_INDEX_URL, {"date": compact, "type": "ALLBUT0999", "response": "json"}):
            return {"status": "not_published", "requests": gate.requests}
        summary = sh.fetch_history(out, [compact], get=gate.replay, sleep=sleep)
        if summary["stopped"]:
            return {"status": "stopped", "reason": summary["stopped"]["reason"], "requests": gate.requests}
        if not raw.exists():
            return {"status": "not_published", "reason": "; ".join(g["reason"] for g in summary["gaps"]),
                    "requests": gate.requests}
    requests = gate.requests if gate else 0

    def write(connection: sqlite3.Connection) -> None:
        import gzip
        content = gzip.decompress(raw.read_bytes())
        payload = json.loads(content.decode("utf-8"))
        if payload.get("date") != compact:
            raise ValueError(f"{raw} 內容日期 {payload.get('date')} 與檔名不符")
        table = sh.quote_table(payload)
        rows = [sh.parse_row(compact, table["fields"], r) for r in table["data"] if sh.security_kind(r[0], r[1])]
        # 已回補進庫的股票型 ETF（etf_history）每天跟著帶上新的一天
        rows += etf_history.day_rows("上市", content, compact, etf_history.recorded_in(connection))
        connection.executemany(f"INSERT INTO daily VALUES ({','.join('?' * len(sh.COLUMNS))})",
                               [[r[c] for c in sh.COLUMNS] for r in rows])
        connection.execute("INSERT INTO source_days VALUES (?, ?, ?, ?)",
                           (day, str(raw.relative_to(out)), hashlib.sha256(content).hexdigest(), len(rows)))

    _append(db_path, write, lambda: sh.build_database(out, db_path))
    return {"status": "appended", "requests": requests}


# ---- 上櫃日 K（otc_history，dailyQuotes） ----

def _otc_quotes_empty(response) -> bool:
    import otc_history as oh
    content_type = getattr(response, "content_type", "") or ""
    if "csv" not in content_type.lower():
        return False
    try:
        rows = oh.parse_csv(response.content, "00000000")
    except ValueError:
        rows = None
    if rows is not None:
        return not rows
    # 日期不符的 ValueError 由模組 fetch 處理；這裡只看「表頭後沒有任何證券列」
    import csv
    import io
    lines = list(csv.reader(io.StringIO(response.content.decode("cp950", errors="replace"))))
    return not any(len(r) > 4 and r[0].strip()[:1].isdigit() and r[0].strip() != "0" for r in lines)


def otc_quotes_day(out: Path, db_path: Path, day: str, catalog, *, get: Callable | None = None,
                   sleep: Callable = time.sleep) -> dict:
    import otc_history as oh
    import etf_history

    compact = _compact(day)
    if _has_row(db_path, "SELECT 1 FROM source_days WHERE date = ?", (day,)):
        return {"status": "already-present", "requests": 0}
    raw = out / "raw" / f"{compact}.csv"
    gate = None
    if not raw.exists():
        gate = _Gate(get or _session_getter(oh.http_get), _otc_quotes_empty)
        if gate.probe(None, oh.DAILY_QUOTES_URL, {"date": _roc_slash(day), "id": "", "response": "csv"}):
            return {"status": "not_published", "requests": gate.requests}
        summary = oh.fetch_history(out, [compact], get=gate.replay, sleep=sleep)
        if summary["stopped"]:
            return {"status": "stopped", "reason": summary["stopped"]["reason"], "requests": gate.requests}
    requests = gate.requests if gate else 0

    def write(connection: sqlite3.Connection) -> None:
        content = raw.read_bytes()
        expected = raw.with_name(raw.name + ".sha256").read_text(encoding="ascii").split()[0]
        if hashlib.sha256(content).hexdigest() != expected:
            raise ValueError(f"{raw.name} SHA-256 不符")
        rows = [{**row, "kind": "common"} for row in oh.parse_csv(content, compact) if catalog.lookup(row["code"])]
        rows += etf_history.day_rows("上櫃", content, compact, etf_history.recorded_in(connection))
        connection.executemany(f"INSERT INTO daily VALUES ({','.join('?' * len(oh.COLUMNS))})",
                               [[r[c] for c in oh.COLUMNS] for r in rows])
        connection.execute("INSERT INTO source_days VALUES (?, ?, ?, ?)",
                           (day, str(raw.relative_to(out)), expected, len(rows)))

    _append(db_path, write, lambda: oh.build_database(out, db_path, catalog))
    return {"status": "appended", "requests": requests}


# ---- 上市法人（listed_institutional，T86） ----

def listed_institutional_day(base: Path, db_path: Path, day: str, *, snapshot_dir: Path | None = None,
                             get: Callable | None = None, sleep: Callable = time.sleep) -> dict:
    import listed_institutional as li

    compact = _compact(day)
    raw_name = f"raw/{compact}.json"
    if _has_row(db_path, "SELECT 1 FROM source_files WHERE raw_file = ?", (raw_name,)):
        return {"status": "already-present", "requests": 0}
    raw = base / raw_name
    snapshot_dir = snapshot_dir or li.SNAPSHOT_DIR
    gate = None
    if not raw.exists():
        gate = _Gate(get or _session_getter(li.http_get), lambda r: _no_data_json(r.content))
        if not (snapshot_dir / f"t86-{compact}.json").exists() and gate.probe(
                None, li.T86_URL, {"date": compact, "selectType": "ALLBUT0999", "response": "json"}):
            return {"status": "not_published", "requests": gate.requests}
        summary = li.fetch(base, [compact], snapshot_dir=snapshot_dir, get=gate.replay, sleep=sleep)
        if summary["stopped"]:
            return {"status": "stopped", "reason": summary["stopped"]["reason"], "requests": gate.requests}
    requests = gate.requests if gate else 0

    def write(connection: sqlite3.Connection) -> None:
        content = raw.read_bytes()
        expected = raw.with_name(raw.name + ".sha256").read_text(encoding="ascii").split()[0]
        if hashlib.sha256(content).hexdigest() != expected:
            raise ValueError(f"{raw.name} SHA-256 不符")
        rows = li.parse_t86_json(content, compact)
        connection.executemany(f"INSERT INTO institutional VALUES ({','.join('?' * len(li.COLUMNS))})",
                               [[r[c] for c in li.COLUMNS] for r in rows])
        connection.execute("INSERT INTO source_files VALUES (?, ?, ?)", (raw_name, expected, len(rows)))

    _append(db_path, write, lambda: li.build_db(base, db_path))
    return {"status": "appended", "requests": requests}


# ---- 上櫃法人＋櫃買指數（otc_market_data） ----

def _tpex_json_empty(response) -> bool:
    if "json" not in (getattr(response, "content_type", "") or "").lower():
        return False
    try:
        payload = json.loads(response.content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    tables = payload.get("tables") or []
    return any(m in str(payload.get("stat") or "") for m in NO_DATA_MARKERS) or (
        bool(tables) and all(not t.get("data") for t in tables))


def otc_institutional_day(inst_dir: Path, db_path: Path, day: str, *, get: Callable | None = None,
                          sleep: Callable = time.sleep) -> dict:
    import otc_market_data as omd

    compact = _compact(day)
    raw_name = f"raw/{compact}.csv"
    if _has_row(db_path, "SELECT 1 FROM source_files WHERE dataset = 'institutional' AND raw_file = ?", (raw_name,)):
        return {"status": "already-present", "requests": 0}
    raw = inst_dir / raw_name
    gate = None
    if not raw.exists():
        # 櫃買法人未公布時回 JSON（stat＝無資料可供下載），已公布才是 CSV。
        gate = _Gate(get or _session_getter(omd.http_get), _tpex_json_empty)
        if gate.probe(None, omd.INSTITUTIONAL_URL, omd._request_params("institutional", compact)):
            return {"status": "not_published", "requests": gate.requests}
        summary = omd.fetch_dataset("institutional", inst_dir, [compact], get=gate.replay, sleep=sleep)
        if summary["stopped"]:
            return {"status": "stopped", "reason": summary["stopped"]["reason"], "requests": gate.requests}
    requests = gate.requests if gate else 0

    def write(connection: sqlite3.Connection) -> None:
        content = raw.read_bytes()
        expected = raw.with_name(raw.name + ".sha256").read_text(encoding="ascii").split()[0]
        if hashlib.sha256(content).hexdigest() != expected:
            raise ValueError(f"{raw.name} SHA-256 不符")
        rows = omd.parse_institutional_csv(content, compact)
        connection.executemany(f"INSERT INTO institutional VALUES ({','.join('?' * len(omd._INST_COLUMNS))})",
                               [[r[c] for c in omd._INST_COLUMNS] for r in rows])
        connection.execute("INSERT INTO source_files VALUES ('institutional', ?, ?, ?)", (raw_name, expected, len(rows)))

    _append(db_path, write, lambda: omd.build_market_db(inst_dir.parent, db_path))
    return {"status": "appended", "requests": requests}


def refresh_otc_index_month(index_dir: Path, day: str, *, get: Callable | None = None,
                            sleep: Callable = time.sleep) -> dict:
    """櫃買指數按月查詢：當月原件會隨每日增加列數，所以當月要重抓、驗證含指定日後換新（sidecar、meta 一起換）。

    回應裡沒有指定日就視為未公布，不動既有原件。過去月份原件已完整，沿用不重抓。
    """
    import otc_market_data as omd

    month = _compact(day)[:6]
    raw = index_dir / "raw" / f"{month}.json"
    if raw.exists():
        rows = omd.parse_index_month_json(raw.read_bytes(), month)
        if any(r["date"] == day for r in rows):
            return {"status": "already-present", "requests": 0}
    response = (get or _session_getter(omd.http_get))(None, omd.INDEX_URL, omd._request_params("index", month))
    if getattr(response, "status", 200) != 200:
        return {"status": "stopped", "reason": f"HTTP {response.status}", "requests": 1}
    if "json" not in (getattr(response, "content_type", "") or "").lower():
        return {"status": "stopped", "reason": f"非預期 Content-Type：{response.content_type}", "requests": 1}
    try:
        rows = omd.parse_index_month_json(response.content, month)
    except (UnicodeDecodeError, ValueError, KeyError, json.JSONDecodeError) as error:
        return {"status": "stopped", "reason": f"非預期格式：{error}", "requests": 1}
    if not any(r["date"] == day for r in rows):
        return {"status": "not_published", "requests": 1}
    digest = hashlib.sha256(response.content).hexdigest()
    omd._write_once(raw, response.content)
    omd._write_once(raw.with_name(raw.name + ".sha256"), f"{digest}  {raw.name}\n".encode("ascii"))
    meta_path = index_dir / "meta.json"
    try:
        files = {e["key"]: e for e in json.loads(meta_path.read_text(encoding="utf-8")).get("files", [])}
    except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError):
        files = {}
    from datetime import datetime, timezone
    files[month] = {"key": month, "raw_file": f"raw/{raw.name}", "sha256": digest, "bytes": len(response.content),
                    "source_url": getattr(response, "url", omd.INDEX_URL),
                    "fetched_at_utc": datetime.now(timezone.utc).isoformat(), "refreshed_for_day": day}
    omd._write_once(meta_path, (json.dumps({
        "dataset": "index", "source_url": omd.INDEX_URL, "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "files": [files[k] for k in sorted(files)]}, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return {"status": "refreshed", "requests": 1, "rows": len(rows)}


def rebuild_otc_index_table(otc_dir: Path, db_path: Path) -> dict:
    """當月指數原件換新後，只重寫 tpex_index 與其 source_files 列（法人列不動）；結果等同整庫重建。"""
    import otc_market_data as omd

    paths = omd._verified(otc_dir / "raw-index", "*.json")

    def write(connection: sqlite3.Connection) -> None:
        connection.execute("DELETE FROM tpex_index")
        connection.execute("DELETE FROM source_files WHERE dataset = 'index'")
        for path in paths:
            rows = omd.parse_index_month_json(path.read_bytes(), path.stem)
            connection.executemany(f"INSERT INTO tpex_index VALUES ({','.join('?' * len(omd._INDEX_COLUMNS))})",
                                   [[r[c] for c in omd._INDEX_COLUMNS] for r in rows])
            connection.execute("INSERT INTO source_files VALUES ('index', ?, ?, ?)",
                               (f"raw/{path.name}", hashlib.sha256(path.read_bytes()).hexdigest(), len(rows)))

    _append(db_path, write, lambda: omd.build_market_db(otc_dir, db_path))
    return {"status": "rebuilt", "months": len(paths)}


def otc_market_day(otc_dir: Path, db_path: Path, day: str, *, get: Callable | None = None,
                   sleep: Callable = time.sleep) -> dict:
    inst = otc_institutional_day(otc_dir / "raw-institutional", db_path, day, get=get, sleep=sleep)
    if inst["status"] == "stopped":
        return {"institutional": inst, "index": {"status": "skipped"}}
    if inst.get("requests"):
        sleep(5.0)
    index = refresh_otc_index_month(otc_dir / "raw-index", day, get=get, sleep=sleep)
    if index["status"] == "refreshed":
        rebuild_otc_index_table(otc_dir, db_path)
    return {"institutional": inst, "index": index}


# ---- 估值（valuation_history：上市 BWIBBU_d；上櫃 peQryDate） ----

def twse_valuation_day(out: Path, db_path: Path, day: str, *, get: Callable | None = None,
                       sleep: Callable = time.sleep) -> dict:
    import valuation_history as vh

    compact = _compact(day)
    if _has_row(db_path, "SELECT 1 FROM source_days WHERE market = '上市' AND date = ?", (day,)):
        return {"status": "already-present", "requests": 0}
    raw = out / "raw" / f"{compact}.json"
    gate = None
    if not raw.exists():
        gate = _Gate(get or _session_getter(vh._http_get), lambda r: _no_data_json(r.content))
        if gate.probe(None, vh.BWIBBU_URL, {"date": compact, "selectType": "ALL", "response": "json"}):
            return {"status": "not_published", "requests": gate.requests}
        summary = vh.fetch_history(out, [compact], get=gate.replay, sleep=sleep)
        if summary["stopped"]:
            return {"status": "stopped", "reason": summary["stopped"]["reason"], "requests": gate.requests}
        if not raw.exists():
            return {"status": "not_published", "requests": gate.requests}
    requests = gate.requests if gate else 0

    def write(connection: sqlite3.Connection) -> None:
        content = raw.read_bytes()
        expected = raw.with_name(raw.name + ".sha256").read_text(encoding="ascii").split()[0]
        if hashlib.sha256(content).hexdigest() != expected:
            raise ValueError(f"{raw.name} SHA-256 不符")
        records = vh.parse_payload(content, compact)
        connection.executescript(vh.SCHEMA_SQL)
        connection.executemany(
            f"INSERT INTO valuation ({','.join(vh.COLUMNS)}) VALUES ({','.join('?' * len(vh.COLUMNS))})",
            [["上市" if c == "market" else r[c] for c in vh.COLUMNS] for r in records])
        connection.execute("INSERT INTO source_days (market, date, raw_file, sha256, rows, schema) VALUES (?,?,?,?,?,?)",
                           ("上市", day, f"twse/raw/{raw.name}", expected, len(records),
                            records[0]["schema"] if records else "empty"))

    _append(db_path, write, lambda: vh.build_twse(out, db_path))
    return {"status": "appended", "requests": requests}


def _tpex_csv_empty(response) -> bool:
    import otc_market_data as omd
    if "csv" not in (getattr(response, "content_type", "") or "").lower():
        return False
    return omd._footer_count(omd._rows(response.content)) == 0


def tpex_valuation_day(tpex_dir: Path, db_path: Path, day: str, *, get: Callable | None = None,
                       sleep: Callable = time.sleep) -> dict:
    import otc_market_data as omd
    import valuation_history as vh

    compact = _compact(day)
    if _has_row(db_path, "SELECT 1 FROM source_days WHERE market = '上櫃' AND date = ?", (day,)):
        return {"status": "already-present", "requests": 0}
    raw = tpex_dir / "raw" / f"{compact}.csv"
    gate = None
    if not raw.exists():
        gate = _Gate(get or _session_getter(omd.http_get), _tpex_csv_empty)
        if gate.probe(None, omd.VALUATION_URL, omd._request_params("valuation", compact)):
            return {"status": "not_published", "requests": gate.requests}
        summary = omd.fetch_dataset("valuation", tpex_dir, [compact], get=gate.replay, sleep=sleep)
        if summary["stopped"]:
            return {"status": "stopped", "reason": summary["stopped"]["reason"], "requests": gate.requests}
    requests = gate.requests if gate else 0

    def write(connection: sqlite3.Connection) -> None:
        content = raw.read_bytes()
        expected = raw.with_name(raw.name + ".sha256").read_text(encoding="ascii").split()[0]
        if hashlib.sha256(content).hexdigest() != expected:
            raise ValueError(f"{raw.name} SHA-256 不符")
        records = omd.to_valuation_records(omd.parse_valuation_csv(content, compact))
        connection.executescript(vh.SCHEMA_SQL)
        connection.executemany(
            f"INSERT INTO valuation ({','.join(vh.COLUMNS)}) VALUES ({','.join('?' * len(vh.COLUMNS))})",
            [["上櫃" if c == "market" else r[c] for c in vh.COLUMNS] for r in records])
        connection.execute("INSERT INTO source_days (market, date, raw_file, sha256, rows, schema) VALUES (?,?,?,?,?,?)",
                           ("上櫃", day, f"tpex/raw/{raw.name}", expected, len(records), omd.VALUATION_SCHEMA))

    _append(db_path, write, lambda: omd.build_tpex_valuation(tpex_dir, db_path))
    return {"status": "appended", "requests": requests}


# ---- 月營收、季報（fundamentals_history）：依法定截止日判斷該抓哪幾期 ----

def due_fundamentals(today: date, *, monthly_lookback: int = 2, quarter_lookback: int = 2) -> dict:
    """回傳截至 today「法定截止日已過」的最近幾期（截止日當天起算可取得，與 available_on 同口徑）。"""
    from fundamentals_history import _monthly_available_on, _quarter_available_on

    monthly: list[str] = []
    year, month = today.year, today.month
    for _ in range(monthly_lookback + 2):
        month -= 1
        if month == 0:
            year, month = year - 1, 12
        period = f"{year:04d}-{month:02d}"
        if _monthly_available_on(period)[0] <= today.isoformat():
            monthly.append(period)
    monthly = sorted(monthly)[-monthly_lookback:]
    quarters: list[str] = []
    year, quarter = today.year, (today.month - 1) // 3 + 1
    for _ in range(quarter_lookback + 3):
        quarter -= 1
        if quarter == 0:
            year, quarter = year - 1, 4
        period = f"{year:04d}-Q{quarter}"
        if _quarter_available_on(period)[0] <= today.isoformat():
            quarters.append(period)
    quarters = sorted(quarters)[-quarter_lookback:]
    return {"monthly": monthly, "quarters": quarters}


def missing_fundamentals(meta: dict, due: dict) -> dict:
    have_monthly = {(f["market"], f["period"], f.get("source_scope") or "domestic")
                    for f in meta.get("files", []) if f.get("dataset") == "monthly_revenue"}
    have_quarters = {(f["market"], f["period"], f.get("statement"))
                     for f in meta.get("files", []) if f.get("dataset") == "quarterly_financials"}
    monthly = [p for p in due["monthly"]
               if not all((m, p, s) in have_monthly for m in ("listed", "otc") for s in ("domestic", "foreign"))]
    quarters = [p for p in due["quarters"]
                if not all((m, p, st) in have_quarters for m in ("listed", "otc") for st in ("income", "balance", "cashflow"))]
    return {"monthly": monthly, "quarters": quarters}


def fundamentals_day(out: Path, day: str, *, fetch: Callable | None = None) -> dict:
    """抓法定截止日已過、但本機還沒有的月營收（國內＋國外表）與季報；沒有缺就不發任何請求。"""
    import fundamentals_history as fh

    meta_path = out / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {"files": []}
    missing = missing_fundamentals(meta, due_fundamentals(date.fromisoformat(day)))
    if not missing["monthly"] and not missing["quarters"]:
        return {"status": "up-to-date", "requests": 0}
    summary = (fetch or fh.fetch_history)(out, monthly_periods=missing["monthly"], quarterly_periods=missing["quarters"],
                                          monthly_scopes=("domestic", "foreign"))
    if summary.get("stopped"):
        return {"status": "stopped", "reason": summary["stopped"], "missing": missing}
    return {"status": "fetched", "missing": missing, "fetched": summary.get("fetched", 0)}


# ---- 上市個股籌碼明細（twon_analysis.chips 讀的 stock-detail 與 twt93u） ----

CHIP_SOURCES = {
    "margin": ("https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN",
               lambda d: {"date": d, "selectType": "ALL", "response": "json"}),
    "twt93u": ("https://www.twse.com.tw/rwd/zh/marginTrading/TWT93U", lambda d: {"date": d, "response": "json"}),
    # 大盤信用交易總表（首頁融資／融券卡，logs/build_chips_data.py 讀 market-snapshots/margin）
    "margin_summary": ("https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN",
                       lambda d: {"date": d, "selectType": "MS", "response": "json"}),
}


# 上市融資（證交所 MI_MARGN）官方大約晚上才公布，傍晚那一班抓不到當天、發布後當天不再重跑。
# G 票（本人 11-16 選做法一，不加排程）：每天順便把最近幾個交易日缺的補回來，天數和上櫃 otc_credit_day 相同。
LISTED_MARGIN_DATASETS = ("margin", "margin_summary")


def _chip_target(root: Path, dataset: str, compact: str) -> Path:
    if dataset == "twt93u":
        return root / "data/fundamentals/twt93u" / f"twt93u-{compact}.json"
    if dataset == "margin_summary":
        return root / "data/market-snapshots/margin" / f"mi-margn-{compact}.json"
    return root / "data/market-snapshots/stock-detail" / dataset / f"{dataset}-{compact}.json"


def _write_atomic(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".partial")
    partial.write_bytes(data)
    os.replace(partial, target)


def listed_chip_detail_day(root: Path, day: str, *, get: Callable | None = None, sleep: Callable = time.sleep,
                           datasets: tuple[str, ...] = ("t86", "margin", "twt93u", "margin_summary")) -> dict:
    """個股頁上市籌碼要的三份原件，格式沿用既有檔（不另造新格式）：
    - t86：直接從 listed_institutional 已驗 SHA 的原件包一層 {"data": ...} 複製，不重抓。
    - margin（MI_MARGN）：同 logs/fetch_stock_detail.py 的外包格式。
    - twt93u（借券）：同 fundamentals.py，存官方原始位元組。
    官方查無（未公布）不寫檔、不當 0；已存在不重抓。兩次請求間隔 5 秒。
    上市融資（margin、margin_summary）官方晚上才公布：另外把最近幾個交易日缺的補回來，結果在 margin_backfill（G 票）。
    個股融資原件另外在長期保存區（data/margin-history/raw）留一份，那裡不裁；結果多一項 margin_history。
    長期保存區缺的日子先從專案帶的種子（seeds/margin-history.tar.xz）放回去，再跟上近期明細。
    同一步也把每日收盤行情摘成行情摘要留在長期保存區（融資維持率（估）重播要每天的收盤與成交均價；種子是
    seeds/margin-prices.tar.xz），結果在 margin_history 底下的 prices。
    """
    from datetime import datetime, timezone
    import stock_history as sh

    compact = _compact(day)
    get = get or sh.http_get
    results: dict[str, dict] = {}
    requested = False
    for dataset in datasets:
        target = _chip_target(root, dataset, compact)
        if target.exists():
            results[dataset] = {"status": "existing"}
            continue
        if dataset == "t86":
            raw = root / "data/listed-institutional/raw" / f"{compact}.json"
            if not raw.exists():
                results[dataset] = {"status": "not_published", "reason": "上市法人原件尚未抓到"}
                continue
            payload = json.loads(raw.read_bytes())
            if "fields" not in payload and isinstance(payload.get("data"), dict):
                payload = payload["data"]  # 由既有快照複製的原件已包一層，與 parse_t86_json 同規則解開
            body = {"fetched_at_utc": datetime.now(timezone.utc).isoformat(),
                    "source_url": f"listed-institutional/raw/{raw.name}", "data": payload}
            _write_atomic(target, (json.dumps(body, ensure_ascii=False) + "\n").encode("utf-8"))
            results[dataset] = {"status": "copied"}
            continue
        if requested:
            sleep(5.0)
        requested = True
        results[dataset] = _fetch_chip(target, dataset, compact, get)
    backfill_sets = [dataset for dataset in LISTED_MARGIN_DATASETS if dataset in datasets]
    if backfill_sets and any(result.get("status") == "stopped" for result in results.values()):
        # 當天就被擋或回應不對：不再往回多打證交所（比照上櫃一停就全停），下一班再補
        results["margin_backfill"] = {"status": "skipped", "reason": "當天的請求已經停下", "requests": 0}
    elif backfill_sets:
        results["margin_backfill"] = _backfill_listed_margin(root, day, backfill_sets, get, sleep,
                                                             already_requested=requested)
    if "margin" in datasets:
        # 近期明細只留最近一段；融資維持率（估）要連續的每日原件，另外長期留一份（已有的不重寫）。
        # 這一步出錯不能蓋掉上面已經抓到的結果；缺哪些交易日一起回報，每日更新的紀錄才看得到缺口。
        import margin_history
        try:
            kept = _restore_then_keep_up(root, "raw", margin_history.keep_up)
            missing = margin_history.coverage(root, margin_history.trading_days(root))["missing"]
            results["margin_history"] = {"status": "ok", **kept, "missing": missing}
        except Exception as error:  # noqa: BLE001 — 長期保存區失敗只記在自己這一項
            results["margin_history"] = {"status": "error", "reason": f"{type(error).__name__}: {error}"}
        # 重播還要每天的收盤與成交均價：把每日收盤行情摘一份進長期保存區（更早的日子在另一包種子裡）。
        # 出錯只記在 prices 這一項，不影響融資原件那一邊。
        try:
            prices = {"status": "ok", **_restore_then_keep_up(root, "prices", margin_history.keep_up_prices)}
        except Exception as error:  # noqa: BLE001
            prices = {"status": "error", "reason": f"{type(error).__name__}: {error}"}
        results["margin_history"]["prices"] = prices
    return results


def _fetch_chip(target: Path, dataset: str, compact: str, get: Callable) -> dict:
    """抓一份上市籌碼原件並寫入 target；回 {"status": saved／not_published／stopped, ...}。"""
    from datetime import datetime, timezone

    url, params = CHIP_SOURCES[dataset]
    try:
        response = get(url, params(compact))
    except Exception as error:  # noqa: BLE001 — 連線錯誤只停這個資料集
        return {"status": "stopped", "reason": f"連線錯誤：{type(error).__name__}: {error}"}
    if response.status != 200:
        return {"status": "stopped", "reason": f"HTTP {response.status}"}
    try:
        payload = json.loads(response.content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"status": "stopped", "reason": f"非 JSON 回應（{response.content_type}）"}
    if payload.get("stat") != "OK":
        return {"status": "not_published", "reason": str(payload.get("stat"))}
    if str(payload.get("date")) != compact:   # 問這天卻回別天：查法可能改了，不寫入
        return {"status": "stopped", "reason": f"資料日不符（回 {payload.get('date')!r}）"}
    if dataset in LISTED_MARGIN_DATASETS:
        body = {"fetched_at_utc": datetime.now(timezone.utc).isoformat(), "source_url": response.url, "data": payload}
        _write_atomic(target, (json.dumps(body, ensure_ascii=False) + "\n").encode("utf-8"))
    else:
        _write_atomic(target, response.content)
    return {"status": "saved"}


def _backfill_listed_margin(root: Path, day: str, datasets: list[str], get: Callable, sleep: Callable, *,
                            already_requested: bool) -> dict:
    """資料日以前（不含當天，當天由呼叫端抓）最近幾個交易日缺的上市融資原件，由舊到新逐份補抓。"""
    saved: list[str] = []
    not_published: list[str] = []
    requests_made = 0
    for date_text in _otc_trading_days(root, day, OTC_CREDIT_BACKFILL_DAYS):
        if date_text >= day:
            continue
        compact = _compact(date_text)
        for dataset in datasets:
            target = _chip_target(root, dataset, compact)
            if target.exists():
                continue
            if already_requested or requests_made:
                sleep(5.0)
            requests_made += 1
            outcome = _fetch_chip(target, dataset, compact, get)
            if outcome["status"] == "stopped":   # 被拒絕、連不上、格式或日期不對：不再往下問，已補的保留
                return {"status": "stopped", "reason": f"{date_text} {dataset}：{outcome['reason']}",
                        "requests": requests_made, "not_published": not_published, "saved": saved}
            (saved if outcome["status"] == "saved" else not_published).append(f"{date_text} {dataset}")
    return {"status": "ok", "requests": requests_made, "not_published": not_published, "saved": saved}


def _restore_then_keep_up(root: Path, store: str, keep_up: Callable[[Path], dict]) -> dict:
    """長期保存區的一種檔（margin_history.STORES）：缺的日子先從專案帶的種子放回去，再跟上手上的原件。"""
    import margin_history
    try:
        seed = {"restored": margin_history.restore_seed(root, store)}
    except Exception as error:  # noqa: BLE001 — 種子壞了不能擋住當天那一份進長期保存區
        seed = {"restored": [], "seed_error": f"{type(error).__name__}: {error}"}
    return {**seed, **keep_up(root)}


# ---- 交易日曆（證交所官方休市日） ----

HOLIDAY_URL = "https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule"
# 官方開放資料只給「當年或隔年」其中一年：2026-11 起改成只給 2027。抓到的新年份要和已有的年份合併保存，
# 專案另帶一份官方 2026 年原件當種子，快取遺失或被換掉時補回（本人 2026-11-16 要求當晚所有功能上線時發現）。
HOLIDAY_SEED = Path(__file__).resolve().parent / "seeds" / "twse-holiday-schedule.json"
_OPEN_MARKERS = ("開始交易", "最後交易")


def parse_holiday_schedule(content: bytes) -> set[str]:
    """證交所 OpenAPI holidaySchedule：民國日期 YYYMMDD；「開始／最後交易日」是交易日，不算休市。"""
    holidays: set[str] = set()
    for row in json.loads(content.decode("utf-8")):
        name = str(row.get("Name") or "")
        if any(mark in name for mark in _OPEN_MARKERS):
            continue
        roc = str(row["Date"])
        holidays.add(f"{int(roc[:-4]) + 1911}-{roc[-4:-2]}-{roc[-2:]}")
    return holidays


def merge_holiday_contents(*contents: bytes | None) -> bytes:
    """把幾份官方 holidaySchedule 原件合成一份（依日期去重，後面的蓋前面的），格式和官方一樣是一個陣列。"""
    rows: dict[str, dict] = {}
    for content in contents:
        if not content:
            continue
        for row in json.loads(content.decode("utf-8")):
            rows[str(row["Date"])] = row
    return json.dumps([rows[key] for key in sorted(rows)], ensure_ascii=False, indent=1).encode("utf-8")


def _read(path: Path | None) -> bytes | None:
    try:
        return path.read_bytes() if path is not None and path.exists() else None
    except OSError:
        return None


def fetch_holidays(cache: Path, *, get: Callable | None = None, seed: Path | None = HOLIDAY_SEED) -> set[str]:
    """抓一次官方休市日，和快取、種子合併後存回快取；抓不到就用快取加種子，再沒有就回空集合（交由就緒判斷處理）。"""
    try:
        if get is None:
            import requests
            response = requests.get(HOLIDAY_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
            content, status = response.content, response.status_code
        else:
            response = get(HOLIDAY_URL)
            content, status = response.content, response.status
        if status == 200:
            parse_holiday_schedule(content)   # 格式不對就丟例外，不寫進快取
            merged = merge_holiday_contents(_read(seed), _read(cache), content)
            cache.parent.mkdir(parents=True, exist_ok=True)
            partial = cache.with_name(cache.name + ".partial")
            partial.write_bytes(merged)
            os.replace(partial, cache)
            return parse_holiday_schedule(merged)
    except Exception:  # noqa: BLE001 — 抓不到休市清單不擋流程，退回快取或就緒判斷
        pass
    known = merge_holiday_contents(_read(seed), _read(cache))
    return parse_holiday_schedule(known)


# ---- 櫃買除權除息（exDailyQ） ----


def otc_corporate_actions_day(root: Path, day: str, *, get: Callable | None = None,
                              sleep: Callable = time.sleep) -> dict:
    """抓指定日的官方公司行動原件並重建共同 events.json；無事件日仍保留涵蓋證據。"""
    import otc_corporate_actions as oca

    raw_dir = root / "data/corporate-actions/raw/TPEx-exDailyQ"
    compact = _compact(day)
    if raw_dir.exists():
        try:
            coverage = oca.verified_coverage(raw_dir)
        except ValueError as error:
            return {"status": "stopped", "reason": f"櫃買除權息原件帳務無效：{error}", "requests": 0}
    else:
        coverage = []
    if coverage and oca.coverage_covers(coverage, day, day):
        events = oca.rebuild_events(root / "data/corporate-actions/events.json", raw_dir)
        return {"status": "already-present", "requests": 0, "events": events}
    latest = max((end for _, end in coverage), default=None)
    start = day if latest is None or latest >= day else (date.fromisoformat(latest) + timedelta(days=1)).isoformat()
    summary = oca.fetch_periods(raw_dir, [(compact, start, day)],
                                get=get or oca.http_get, sleep=sleep)
    if summary["stopped"]:
        return {"status": "stopped", "reason": summary["stopped"]["reason"],
                "requests": summary["requested"]}
    events = oca.rebuild_events(root / "data/corporate-actions/events.json", raw_dir)
    return {"status": "appended" if summary["fetched"] else "already-present",
            "requests": summary["requested"], "events": events}


# ---- 還原價要用的股本變動資料（上市公司行動＋補充資料） ----

# 視窗：資料日前 45 天到後 30 天。往前涵蓋停牌換發與補登，往後帶入已公告的恢復買賣日。
CAPITAL_WINDOW_BEFORE, CAPITAL_WINDOW_AFTER = 45, 30


def _capital_window(day: str) -> tuple[str, str]:
    base = date.fromisoformat(day)
    return ((base - timedelta(days=CAPITAL_WINDOW_BEFORE)).isoformat(),
            (base + timedelta(days=CAPITAL_WINDOW_AFTER)).isoformat())


def listed_corporate_actions_day(root: Path, day: str, *, get: Callable | None = None,
                                 sleep: Callable = time.sleep) -> dict:
    """更新共同 events.json 的上市事件（除權息、面額變更、減資）。雲端原本只有櫃買會每天更新。"""
    import corporate_actions as ca

    start, end = _capital_window(day)
    summary = ca.refresh_window(root / "data/corporate-actions/events.json", start, end,
                                get=get or ca.http_get, sleep=sleep)
    if summary["stopped"]:
        return {"status": "stopped", "reason": summary["stopped"], "requests": summary["requested"]}
    return {"status": "refreshed", "requests": summary["requested"], "window": [start, end],
            "events": {key: summary[key] for key in ("replaced", "fetched", "total")}}


def capital_supplement_day(root: Path, day: str, *, get: Callable | None = None,
                           sleep: Callable = time.sleep) -> dict:
    """更新還原價的補充資料：ETF 分割、櫃買減資與面額變更、上市除權息的現金股利。"""
    import capital_supplement as cs

    start, end = _capital_window(day)
    events_path = root / "data/corporate-actions/events.json"
    events = json.loads(events_path.read_text(encoding="utf-8")) if events_path.exists() else []
    otc_raw = root / "data/corporate-actions/raw/TPEx-exDailyQ"
    summary = cs.refresh(root / "data/corporate-actions/capital-supplement.json", events, start, end,
                         seed=root / "seeds/capital-supplement.json", get=get or cs.http_get, sleep=sleep,
                         otc_raw_dir=otc_raw if otc_raw.exists() else None)
    result = {"requests": summary["requested"], "window": [start, end], "events": summary["events"],
              "cash_dividends": summary["cash_dividends"], "missing_details": summary["missing_details"]}
    if summary["stopped"]:
        return {"status": "stopped", "reason": summary["stopped"], **result}
    return {"status": "refreshed", **result}


# ---- 集保大戶持股（每週一份） ----


def tdcc_holders_day(root: Path, day: str, *, get: Callable | None = None) -> dict:
    """集保股權分散表通常以週五為資料日。最近一個週五那份已經存了，就不再下載（一份 2.4MB）。"""
    import tdcc_holders as th

    out = root / "data/tdcc-holders"
    base = date.fromisoformat(day)
    friday = base - timedelta(days=(base.weekday() - 4) % 7)
    if (out / "weekly" / f"{friday.isoformat()}.json").exists():
        return {"status": "already-present", "requests": 0, "data_date": friday.isoformat()}
    return th.refresh(out, get=get or th.http_get)


# ---- 上櫃融資融券與借券（個股頁事實區塊 E 票；櫃買網站可指定日期，政府資料開放授權） ----

OTC_CREDIT_SOURCES = {
    "margin": "https://www.tpex.org.tw/www/zh-tw/margin/balance",   # 融資、融券（張）
    "sbl": "https://www.tpex.org.tw/www/zh-tw/margin/sbl",          # 融券與借券賣出（股）
}
OTC_CREDIT_BACKFILL_DAYS = 20   # 本人 11-16「補抓 20 個交易日」：每次都檢查最近 20 個交易日缺哪天補哪天
# 寫入前要看得到的欄位：融資融券要有「資餘額」「券餘額」；借券要有「當日還券」之後的「當日餘額」（前面那個是融券的）。
OTC_CREDIT_REQUIRED = {"margin": ("資餘額", "券餘額"), "sbl": ("當日還券", "當日餘額")}


def _otc_credit_fields_ok(kind: str, fields: list) -> bool:
    names = [str(f).strip() for f in fields]
    if kind == "margin":
        return all(name in names for name in OTC_CREDIT_REQUIRED["margin"])
    return "當日還券" in names and "當日餘額" in names[names.index("當日還券"):]


def _otc_trading_days(root: Path, day: str, count: int) -> list[str]:
    """上櫃行情庫裡資料日以前（含）最近 count 個交易日；行情庫沒有這一天時仍把它放在最後。"""
    db = root / "data/otc-history/otc_history.sqlite"
    days: list[str] = []
    if db.exists():
        connection = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        try:
            days = [row[0] for row in connection.execute(
                "SELECT DISTINCT date FROM daily WHERE date <= ? ORDER BY date DESC LIMIT ?", (day, count))]
        finally:
            connection.close()
    days = sorted(set(days) | {day})
    return days[-count:]


def otc_credit_day(root: Path, day: str, *, get: Callable | None = None, sleep: Callable = time.sleep,
                   backfill_days: int = OTC_CREDIT_BACKFILL_DAYS) -> dict:
    """最近 backfill_days 個交易日裡，缺哪天的融資融券或借券原件就補哪天（同一網域請求之間固定等 5 秒）。

    官方還沒公布（stat 不是 ok 或沒有資料）不寫入，記進 not_published；被拒絕、連不上、格式改了或資料日不符
    就停下（不寫入那一份），已存的保留。原件和上市明細同一個外包格式：取得時間、來源網址、雜湊、官方原文。
    上櫃融資原件另外在長期保存區（data/margin-history/otc-raw）留一份、上櫃行情摘一份（10 票，融資維持率（估）
    要連續的每日原件）；結果多一項 margin_history，停下時也照樣跟上已存的。"""
    result = _fetch_otc_credit(root, _otc_trading_days(root, day, backfill_days), OTC_CREDIT_SOURCES, get, sleep)
    return {**result, "margin_history": _keep_otc_margin_history(root)}


def otc_margin_backfill(root: Path, since: str, until: str, *, get: Callable | None = None,
                        sleep: Callable = time.sleep) -> dict:
    """上櫃融資維持率（估）的回補（10 票；要本人同意才跑）：since～until 之間上櫃行情庫的每個交易日，
    缺的上櫃融資原件（只有融資融券那一份，不抓借券）逐日補回 data/otc-credit/margin，再跟進長期保存區。
    和每日抓取同一個網址、同一種存法與停下規則；請求之間等 5 秒，一年約 250 個請求、約 21 分鐘。"""
    import margin_history
    days = [date_text for date_text in margin_history.otc_trading_days(root) if since <= date_text <= until]
    result = _fetch_otc_credit(root, days, {"margin": OTC_CREDIT_SOURCES["margin"]}, get, sleep)
    return {**result, "margin_history": _keep_otc_margin_history(root)}


def _keep_otc_margin_history(root: Path) -> dict:
    """上櫃融資原件與上櫃行情摘要跟進長期保存區（缺的先從種子放回）；出錯只記在這一項，不影響抓到的原件。"""
    import margin_history
    try:
        kept = _restore_then_keep_up(root, "otc-raw", margin_history.keep_up_otc)
        missing = margin_history.coverage(root, margin_history.otc_trading_days(root), "otc-raw")["missing"]
        history = {"status": "ok", **kept, "missing": missing}
    except Exception as error:  # noqa: BLE001 — 長期保存區失敗只記在自己這一項
        return {"status": "error", "reason": f"{type(error).__name__}: {error}"}
    try:
        history["prices"] = {"status": "ok", **_restore_then_keep_up(root, "otc-prices", margin_history.keep_up_otc_prices)}
    except Exception as error:  # noqa: BLE001
        history["prices"] = {"status": "error", "reason": f"{type(error).__name__}: {error}"}
    return history


def _fetch_otc_credit(root: Path, days: list[str], sources: dict[str, str], get: Callable | None,
                      sleep: Callable) -> dict:
    """days（YYYY-MM-DD）的每一天、sources 的每一種，缺的就抓（規則見 otc_credit_day）。"""
    import otc_market_data as omd

    get = get or _session_getter(omd.http_get)
    base = root / "data/otc-credit"
    saved = requests_made = 0
    not_published: list[str] = []
    for date_text in days:
        compact = _compact(date_text)
        for kind, url in sources.items():
            target = base / kind / f"{compact}.json"
            if target.exists():
                continue
            if requests_made:
                sleep(5.0)
            requests_made += 1
            stop = lambda reason: {"status": "stopped", "reason": f"{kind} {date_text}：{reason}", "saved": saved,  # noqa: E731
                                   "requests": requests_made, "not_published": not_published}
            try:
                response = get(None, url, {"date": date_text.replace("-", "/"), "id": "", "response": "json"})
            except Exception as error:  # noqa: BLE001 — 連不上就停在這裡，已存的與未公布的照實回報
                return stop(type(error).__name__)
            if getattr(response, "status", 200) != 200:
                return stop(f"HTTP {response.status}")
            try:
                payload = json.loads(response.content.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return stop("非 JSON 回應")
            tables = payload.get("tables") or []
            if str(payload.get("stat") or "").lower() != "ok" or not tables or not tables[0].get("data"):
                not_published.append(f"{date_text} {kind}")
                continue
            if str(payload.get("date") or "") != compact:
                return stop(f"資料日不符（回 {payload.get('date')!r}）")
            if not _otc_credit_fields_ok(kind, tables[0].get("fields") or []):
                return stop("欄位跟預期不同，不寫入")
            import hashlib
            from datetime import datetime, timezone
            _write_atomic(target, json.dumps({
                "fetched_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "source_url": getattr(response, "url", "") or url, "sha256": hashlib.sha256(response.content).hexdigest(),
                "data": payload}, ensure_ascii=False).encode("utf-8"))
            saved += 1
    return {"status": "ok", "saved": saved, "requests": requests_made, "not_published": not_published}


# 公司重大訊息（個股頁事實區塊 D 票）不在這裡抓：每日更新工作流程在這之前先跑 `announcements.py daily --r2`，
# 補齊 R2 上的原件、抓當日、把新的傳回 R2。在這裡再抓一次只會存在本機、不會進雲端。


# ---- 外資台指期部位（預測帳本 G 票第 14 片；期交所只開放查近三年，要每天存下來才拉得長） ----

FOREIGN_FUTURES_BACKFILL_DAYS = 10   # 漏抓的日子在這幾個交易日內補回來；平常每天只有當天那兩個請求


def foreign_futures_day(root: Path, day: str, *, post: Callable | None = None, sleep: Callable = time.sleep,
                        backfill_days: int = FOREIGN_FUTURES_BACKFILL_DAYS) -> dict:
    """期交所「三大法人－區分各期貨契約」的大台、小台各一份，原件存 data/foreign-futures/raw/；最近幾天缺的一併補。"""
    import foreign_futures as ff

    return ff.fetch_recent(root / "data/foreign-futures", _otc_trading_days(root, day, backfill_days),
                           post=post, sleep=sleep)


# ---- 每日協調器呼叫的兩個入口 ----

REQUIRED_DATASETS = ("listed_quotes", "otc_quotes", "listed_institutional", "otc_institutional")
OPTIONAL_DATASETS = ("otc_index", "twse_valuation", "tpex_valuation", "otc_corporate_actions",
                     "listed_corporate_actions", "capital_supplement", "tdcc_holders",
                     "fundamentals", "taiex_index", "listed_chip_detail", "otc_credit", "foreign_futures")


def _interval(previous: dict | None, sleep: Callable) -> None:
    if previous and previous.get("requests"):
        sleep(5.0)


def refresh_taiex_month(index_dir: Path, day: str, *, get: Callable | None = None) -> dict:
    """加權指數月檔（MI_5MINS_HIST）同時是 stock_history 的交易日曆：當月檔要含指定日才算就緒。"""
    month = _compact(day)[:6]
    target = index_dir / f"taiex-{month}.json"
    if target.exists():
        rows = json.loads(target.read_text(encoding="utf-8"))["data"].get("data") or []
        roc = f"{int(day[:4]) - 1911}/{day[5:7]}/{day[8:]}"
        if any(r[0] == roc for r in rows):
            return {"status": "already-present", "requests": 0}
    url = "https://www.twse.com.tw/indicesReport/MI_5MINS_HIST"
    params = {"response": "json", "date": f"{month}01"}
    if get is None:
        import requests
        response = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
        status, content, final_url = response.status_code, response.content, response.url
    else:
        response = get(url, params)
        status, content, final_url = response.status, response.content, response.url
    if status != 200:
        return {"status": "stopped", "reason": f"HTTP {status}", "requests": 1}
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"status": "stopped", "reason": "非 JSON 回應", "requests": 1}
    roc = f"{int(day[:4]) - 1911}/{day[5:7]}/{day[8:]}"
    if payload.get("stat") != "OK" or not any(r[0] == roc for r in payload.get("data") or []):
        return {"status": "not_published", "requests": 1}
    from datetime import datetime, timezone
    body = {"fetched_at_utc": datetime.now(timezone.utc).isoformat(), "source_url": final_url,
            "http_status": status, "data": payload}
    index_dir.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".partial")
    partial.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(partial, target)
    return {"status": "refreshed", "requests": 1}


def fetch_day(day: str, *, root: Path = ROOT, sleep: Callable = time.sleep, include_fundamentals: bool = True) -> dict:
    """抓指定交易日缺的官方原件並附加入庫（各資料集一次只抓一天，沿用模組限流）。

    回傳每個資料集的狀態；必要資料集（兩市場行情與法人）未公布或被擋時整體為 incomplete。
    """
    import otc_history as oh
    import stock_history as sh

    data = root / "data"
    datasets: dict[str, dict] = {}
    started = time.time()

    def timed(name: str, fn: Callable[[], dict]) -> None:
        t0 = time.time()
        try:
            result = fn()
        except Exception as error:  # noqa: BLE001 — 單一資料集失敗要記錄，不吞掉
            result = {"status": "error", "reason": f"{type(error).__name__}: {error}"}
        result["seconds"] = round(time.time() - t0, 1)
        datasets[name] = result

    index_dir = Path(getattr(sh, "DEFAULT_INDEX_DIR"))
    timed("taiex_index", lambda: refresh_taiex_month(index_dir, day))
    sleep(3.0)
    timed("listed_quotes", lambda: listed_quotes_day(data / "stock-history", data / "stock-history/stock-history.sqlite",
                                                     day, sleep=sleep))
    _interval(datasets["listed_quotes"], sleep)
    timed("listed_institutional", lambda: listed_institutional_day(
        data / "listed-institutional", data / "listed-institutional/listed_institutional.sqlite", day, sleep=sleep))
    _interval(datasets["listed_institutional"], sleep)
    timed("twse_valuation", lambda: twse_valuation_day(data / "valuation-history/twse",
                                                       data / "valuation-history/valuation.sqlite", day, sleep=sleep))
    catalog = oh.load_default_catalog()
    timed("otc_quotes", lambda: otc_quotes_day(data / "otc-history", data / "otc-history/otc_history.sqlite", day,
                                               catalog, sleep=sleep))
    _interval(datasets["otc_quotes"], sleep)
    # 股票型 ETF 的日線：名錄裡新出現的才從原件補歷史，沒有新的就只讀名錄（不發請求）；非必要資料集，失敗不擋發布
    import etf_history
    timed("etf_history", lambda: etf_history.refresh(root))

    def otc_market() -> dict:
        result = otc_market_day(data / "otc-history", data / "otc-history/otc_market.sqlite", day, sleep=sleep)
        datasets["otc_index"] = result["index"]
        return result["institutional"]

    timed("otc_institutional", otc_market)
    # otc_market 內的法人或指數任一項可能剛打過同一個 www.tpex.org.tw；下一個 TPEx 來源前都要等。
    _interval({"requests": max(datasets["otc_institutional"].get("requests", 0),
                                datasets.get("otc_index", {}).get("requests", 0))}, sleep)
    timed("tpex_valuation", lambda: tpex_valuation_day(data / "valuation-history/tpex",
                                                       data / "valuation-history/valuation.sqlite", day, sleep=sleep))
    _interval(datasets["tpex_valuation"], sleep)
    timed("otc_corporate_actions", lambda: otc_corporate_actions_day(root, day, sleep=sleep))
    # 還原價用的股本變動：先更新上市事件，補充資料才看得到今天新出現的除權息。兩者都是非必要資料集，失敗不擋發布。
    # 這兩個每次都會打證交所與櫃買（視窗內的官方表會補登、更正，不能用「已有就不抓」）。前一個資料集就算
    # 沒發請求、或出錯沒回 requests，也固定等 5 秒：不靠別人的回報來判斷離上一個同站請求多久。
    sleep(5.0)
    timed("listed_corporate_actions", lambda: listed_corporate_actions_day(root, day, sleep=sleep))
    sleep(5.0)
    timed("capital_supplement", lambda: capital_supplement_day(root, day, sleep=sleep))
    sleep(5.0)
    timed("tdcc_holders", lambda: tdcc_holders_day(root, day))  # 集保另一個網域，不與證交所／櫃買共用間隔
    timed("listed_chip_detail", lambda: {"status": "ok", "datasets": listed_chip_detail_day(root, day, sleep=sleep)})
    sleep(5.0)   # 前一步打證交所，這裡固定再等一下才打櫃買（不靠前一步回報的請求數）
    timed("otc_credit", lambda: otc_credit_day(root, day, sleep=sleep))
    timed("foreign_futures", lambda: foreign_futures_day(root, day, sleep=sleep))  # 期交所，另一個網域
    if include_fundamentals:
        timed("fundamentals", lambda: fundamentals_day(data / "fundamentals-history", day))
    blocking = [name for name in REQUIRED_DATASETS if datasets.get(name, {}).get("status") not in
                ("appended", "already-present")]
    return {"status": "ok" if not blocking else "incomplete", "blocking": blocking, "datasets": datasets,
            "seconds": round(time.time() - started, 1)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="每日增量抓取並附加入庫（只抓指定交易日缺的原件）")
    parser.add_argument("--date", required=True, help="資料日 YYYY-MM-DD")
    parser.add_argument("--no-fundamentals", action="store_true")
    parser.add_argument("--otc-margin-backfill", metavar="SINCE",
                        help="只回補上櫃融資原件：SINCE（YYYY-MM-DD）到 --date 之間缺的交易日（10 票，要本人同意才跑）")
    args = parser.parse_args(argv)
    if args.otc_margin_backfill:
        report = otc_margin_backfill(ROOT, args.otc_margin_backfill, args.date)
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0 if report["status"] == "ok" else 2
    report = fetch_day(args.date, include_fundamentals=not args.no_fundamentals)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if report["status"] == "ok" else 2


if __name__ == "__main__":
    sys.exit(main())
