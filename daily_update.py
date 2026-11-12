"""每日收盤後更新的協調器（票 11／外部票 08）。

只負責順序、就緒等待、紀錄與失敗保護；每一步實際做什麼由注入的 Step 決定，
GitHub Actions 與本機都用同一個入口。規則：
- 週末與官方休市日不跑、不報錯（status=skipped）。
- 官方資料未就緒就輪詢，超過停止期限就失敗，不硬算。
- D1 先匯入 staging、驗證後才切換 active，再部署 Pages；切換前任何一步失敗都不切換。
- 切換後部署或驗證失敗，呼叫 rollback 把 active 切回上一版。
- 同一資料日已成功就不重跑（冪等）。
- --dry-run 只跑不會動到遠端的步驟。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable

ROOT = Path(__file__).resolve().parent
TPE = timezone(timedelta(hours=8))

STEP_ORDER = [
    "fetch_incremental",    # 增量抓官方原件（上市／上櫃日K、法人、估值、月營收、指數）
    "build_databases",      # 由原件重建或附加 SQLite
    "build_universe",       # 大量股母體快照（遲滯規則）
    "publish_snapshots",    # 籌碼選股與資金流向快照
    "build_seed",           # 個股分析 seed（0004 staging）
    "build_dist",           # cloud/build.mjs
    "d1_import",            # 匯入 staging（可續跑）
    "d1_verify",            # 核數
    "d1_activate",          # 切換 active
    "pages_deploy",         # 上傳 Pages
    "post_deploy_check",    # 正式站驗證
]
REMOTE_STEPS = {"d1_import", "d1_verify", "d1_activate", "pages_deploy", "post_deploy_check"}
ACTIVATE_STEP = "d1_activate"


@dataclass
class RunContext:
    data_date: str
    dry_run: bool
    work_dir: Path
    outputs: dict = field(default_factory=dict)


@dataclass
class Step:
    name: str
    fn: Callable[[RunContext], dict]
    mutates_remote: bool = False


@dataclass
class TradingVerdict:
    status: str  # open / closed / unknown
    reason: str


@dataclass
class ReadyResult:
    ready: bool
    attempts: int
    reason: str


@dataclass
class RunConfig:
    data_date: str
    log_dir: Path
    dry_run: bool = False
    holidays: set[str] | None = None
    ready_interval: float = 600
    ready_deadline: datetime | None = None
    work_dir: Path | None = None
    trigger_event: str = ""
    trigger_source: str = ""
    trigger_slot: str = ""
    legacy_allow_not_ready: bool = False


# Caller metadata is a CI routing contract, not authentication. Unknown values stay strict.
RETRY_SLOTS = {"30 7 * * 1-5": (15, 30), "30 8 * * 1-5": (16, 30),
               "0 10 * * 1-5": (18, 0), "30 12 * * 1-5": (20, 30)}


def may_wait_for_retry(config: RunConfig, now: datetime) -> bool:
    local = now.astimezone(TPE)
    if local.date().isoformat() != config.data_date or (local.hour, local.minute) >= (20, 30):
        return False
    if not any((config.trigger_event, config.trigger_source, config.trigger_slot)):
        # Compatibility for an old, explicitly opted-in caller; never infer from dispatch alone.
        return config.legacy_allow_not_ready and (local.hour, local.minute) >= (15, 30)
    expected_event = {"github-schedule": "schedule", "cloudflare-cron": "workflow_dispatch"}
    if expected_event.get(config.trigger_source) != config.trigger_event:
        return False
    slot = RETRY_SLOTS.get(config.trigger_slot)
    return slot is not None and slot < (20, 30) and (local.hour, local.minute) >= slot


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(TPE)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


def trading_day(day: str, *, probe: Callable[[str], bool], holidays: set[str] | None = None) -> TradingVerdict:
    """週末直接休市；官方休市日清單命中就休市；清單不可得時以官方當日資料是否存在判斷。"""
    if date.fromisoformat(day).weekday() >= 5:
        return TradingVerdict("closed", "weekend")
    if holidays is not None:
        if day in holidays:
            return TradingVerdict("closed", "official-holiday")
        return TradingVerdict("open", "not-in-official-holidays")
    # 沒有休市清單時，官方沒有當日資料不代表一定休市（也可能尚未公布），交由就緒等待處理。
    return TradingVerdict("open", "official-data-present") if probe(day) else TradingVerdict("unknown", "no-official-data-yet")


def wait_until_ready(check: Callable[[], bool], *, clock, interval: float, deadline: datetime) -> ReadyResult:
    attempts = 0
    while True:
        attempts += 1
        if check():
            return ReadyResult(True, attempts, "ready")
        if interval <= 0 or clock.now() + timedelta(seconds=interval) > deadline:
            return ReadyResult(False, attempts, "deadline")
        clock.sleep(interval)


def _log_path(log_dir: Path, data_date: str) -> Path:
    return log_dir / f"{data_date}.json"


def _write_record(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    partial.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(partial, path)


def _stamp(clock) -> str:
    return clock.now().isoformat(timespec="seconds")


def run(config: RunConfig, *, steps: Iterable[Step], probe: Callable[[str], bool],
        ready_check: Callable[[], bool], clock=None, rollback: Callable[[RunContext], dict] | None = None,
        readiness_observations: list[dict] | None = None) -> dict:
    clock = clock or SystemClock()
    log_path = _log_path(config.log_dir, config.data_date)
    if log_path.exists() and not config.dry_run:
        previous = json.loads(log_path.read_text(encoding="utf-8"))
        if previous.get("status") == "success":
            return {**previous, "status": "already-done"}

    record: dict = {"data_date": config.data_date, "dry_run": config.dry_run, "started_at": _stamp(clock),
                    "steps": [], "status": "running"}

    verdict = trading_day(config.data_date, probe=probe, holidays=config.holidays)
    record["trading_day"] = {"status": verdict.status, "reason": verdict.reason}
    if verdict.status == "closed":
        record.update(status="skipped", finished_at=_stamp(clock))
        _write_record(log_path, record)
        write_status(config.log_dir, record)
        return record

    deadline = config.ready_deadline or datetime.fromisoformat(config.data_date).replace(hour=21, tzinfo=TPE)
    ready = wait_until_ready(ready_check, clock=clock, interval=config.ready_interval, deadline=deadline)
    record["readiness"] = {"ready": ready.ready, "attempts": ready.attempts, "reason": ready.reason,
                           "sources": list(readiness_observations or [])}
    source_error = any(row.get("status") == "error" for row in record["readiness"]["sources"])
    if not ready.ready:
        if source_error:
            record["readiness"]["reason"] = "source-error"
        waiting = not source_error and may_wait_for_retry(config, clock.now())
        record.update(status="waiting" if waiting else "failed", failed_step="wait_ready",
                      published=False, finished_at=_stamp(clock),
                      error=("官方來源探測失敗；保留上一版，不發布" if source_error else
                             "等待官方資料，未發布；下個時段重試" if waiting else
                             "官方資料在停止期限前未就緒；保留上一版，不硬算"))
        _write_record(log_path, record)
        write_status(config.log_dir, record)
        return record

    ctx = RunContext(config.data_date, config.dry_run, config.work_dir or (ROOT / ".tmp" / "daily-update" / config.data_date))
    ctx.outputs["_log_dir"] = str(config.log_dir)
    activated = False
    for step in steps:
        entry = {"name": step.name, "started_at": _stamp(clock)}
        if config.dry_run and step.mutates_remote:
            entry.update(status="dry-run", finished_at=_stamp(clock))
            record["steps"].append(entry)
            continue
        try:
            output = step.fn(ctx) or {}
            ctx.outputs[step.name] = output
            entry.update(status="pending" if "pending" in output else "ok", output=_record_safe(output),
                         finished_at=_stamp(clock))
            record["steps"].append(entry)
            if step.name == ACTIVATE_STEP:
                activated = True
        except Exception as error:  # noqa: BLE001 — 任何一步失敗都要記錄並停下
            entry.update(status="failed", error=f"{type(error).__name__}: {error}",
                         trace=traceback.format_exc(limit=3), finished_at=_stamp(clock))
            record["steps"].append(entry)
            record.update(status="failed", failed_step=step.name)
            if activated and rollback is not None:
                try:
                    record["rollback"] = {"status": "ok", "output": rollback(ctx) or {}}
                except Exception as rb_error:  # noqa: BLE001
                    record["rollback"] = {"status": "failed", "error": f"{type(rb_error).__name__}: {rb_error}"}
            break
    else:
        # 有步驟尚未接上時不能報 success，以免把半套流程當成每日更新已完成。
        record["status"] = "incomplete" if any(s["status"] == "pending" for s in record["steps"]) else "success"

    record["finished_at"] = _stamp(clock)
    _write_record(log_path, record)
    write_status(config.log_dir, record)
    return record


# ---- 每日更新狀態（前端讀 /api/data-status 顯示「資料至 YYYY-MM-DD（今日更新失敗）」） ----

STATUS_SCHEMA = "data-status/v1"
_STATUS_FILE = "status.json"
# 給前端看的原因只留白話，不放路徑或堆疊；完整錯誤留在當日執行紀錄（不上雲）。
_PUBLIC_REASONS = {
    "wait_ready": "官方資料在停止期限前未就緒",
    "fetch_incremental": "官方資料未就緒或抓取被停止",
    "build_databases": "資料庫未更新到當日",
    "build_universe": "大量股名單重建失敗",
    "publish_snapshots": "籌碼選股與資金流向發布失敗",
    "build_seed": "個股分析建置失敗",
    "build_dist": "網站資料包建置失敗",
    "d1_import": "雲端資料庫匯入失敗",
    "d1_verify": "雲端資料庫核對不符",
    "d1_activate": "切換新版資料失敗",
    "pages_deploy": "網站部署失敗",
    "post_deploy_check": "部署後驗證失敗，已退回上一版",
}


def write_status(log_dir: Path, record: dict) -> dict:
    """依本次紀錄更新 status.json：成功才推進最後成功日；失敗或未完成標 stale；休市不標 stale。"""
    path = log_dir / _STATUS_FILE
    previous = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    last_date, last_at = previous.get("last_success_date"), previous.get("last_success_at")
    outcome = record.get("status")
    if outcome in ("success", "already-done"):
        last_date, last_at = record["data_date"], record.get("finished_at")
    failed_step = record.get("failed_step")
    run = {"data_date": record["data_date"], "status": outcome, "finished_at": record.get("finished_at")}
    if outcome == "waiting":
        run.update(reason="等待官方資料，未發布；下個時段重試", published=False)
    elif failed_step:
        run["failed_step"] = failed_step
        run["reason"] = ("官方來源探測失敗，未發布" if record.get("readiness", {}).get("reason") == "source-error"
                         else _PUBLIC_REASONS.get(failed_step, "更新失敗"))
    elif outcome == "incomplete":
        run["reason"] = "流程有步驟尚未接上，未發布新資料"
    stale = outcome in ("failed", "incomplete", "waiting", "running")
    status = {"schema": STATUS_SCHEMA, "last_success_date": last_date, "last_success_at": last_at,
              "run": run, "stale": stale}
    _write_record(path, status)
    return status


# ---- 真實步驟（本機與 GitHub Actions 共用）；本切片只接 dry-run 能跑的部分 ----

def _sh(args: list[str], cwd: Path = ROOT) -> dict:
    import subprocess
    completed = subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               shell=(os.name == "nt" and args[0] == "npx"))
    if completed.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:3])} exit {completed.returncode}: {completed.stderr[-400:]}")
    return {"cmd": " ".join(args), "tail": completed.stdout[-400:], "_stdout": completed.stdout}


def _record_safe(output: dict) -> dict:
    """執行紀錄不存整段 stdout（底線開頭的欄位只供步驟內部使用）。"""
    return {k: (_record_safe(v) if isinstance(v, dict) else v) for k, v in output.items() if not k.startswith("_")}


def _latest_source_day(db: Path, table: str = "source_days") -> str | None:
    import sqlite3
    if not db.exists():
        return None
    connection = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        row = connection.execute(f"SELECT MAX(date) FROM {table}").fetchone()
    finally:
        connection.close()
    return row[0] if row else None


def local_state_probe(day: str) -> bool:
    """本機 dry-run 用：當日是否已有上市日K（等同官方已公布並入庫）。"""
    return (_latest_source_day(ROOT / "data/stock-history/stock-history.sqlite") or "") >= day


# 官方就緒：兩市場行情與法人四份都公布才開跑（估值、指數、營收不擋）。
READY_SOURCES = ("listed_quotes", "otc_quotes", "listed_institutional", "otc_institutional")


def official_ready(day: str, *, probes: dict[str, Callable] | None = None,
                   observations: list[dict] | None = None, clock=None) -> bool:
    """四源全部檢查；只存白名單小紀錄，不保存回應本文／例外訊息／憑證。"""
    probes = _official_probes() if probes is None else probes
    clock = clock or SystemClock()
    rows = []
    reasons = {"ready", "not-published", "http-error", "invalid-payload", "date-mismatch"}
    for name in READY_SOURCES:
        row = {"source": name, "observed_at": _stamp(clock), "as_of": None,
               "ready": False, "status": "error", "reason": "missing-probe", "missing": []}
        if name in probes:
            try:
                result = probes[name](day)
                if isinstance(result, bool):
                    result = {"ready": result, "reason": "ready" if result else "not-published"}
                reason = result.get("reason") if isinstance(result, dict) else "invalid-payload"
                if reason not in reasons:
                    reason = "invalid-payload"
                as_of = result.get("as_of") if isinstance(result, dict) else None
                if isinstance(as_of, str):
                    try:
                        row["as_of"] = as_of if date.fromisoformat(as_of).isoformat() == as_of else None
                    except ValueError:
                        pass
                if row["as_of"] is not None and row["as_of"] != day:
                    reason = "date-mismatch"
                row["reason"] = reason
                row["ready"] = isinstance(result, dict) and result.get("ready") is True and reason == "ready"
                row["status"] = "ready" if row["ready"] else "not-ready" if reason == "not-published" else "error"
                http_status = result.get("http_status") if isinstance(result, dict) else None
                if type(http_status) is int and 100 <= http_status <= 599:
                    row["http_status"] = http_status
            except Exception:  # transport/parser failures stay strict; never copy exception text
                row["reason"] = "probe-error"
        else:
            row["missing"].append("probe")
        if row["as_of"] is None:
            row["missing"].append("as_of")
        rows.append(row)
    if observations is not None:
        observations[:] = rows  # bounded: latest attempt, at most four source records
    return all(row["ready"] for row in rows)


def _response_as_of(response) -> str | None:
    """只解析來源自己帶的日期；絕不以查詢日冒充來源日。"""
    try:
        if "json" in (getattr(response, "content_type", "") or "").lower():
            payload = json.loads(response.content.decode("utf-8"))
            raw = str(payload.get("date") or "") if isinstance(payload, dict) else ""
            if re.fullmatch(r"\d{8}", raw):
                raw = f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
            return date.fromisoformat(raw).isoformat() if raw else None
        text = response.content[:2000].decode("cp950", errors="replace")
        match = re.search(r'(?:資料日期[:：]\s*|^"?)(\d{3,4})[年/](\d{1,2})[月/](\d{1,2})(?:日|\b)', text, re.MULTILINE)
        if match:
            year, month, day = map(int, match.groups())
            return date(year + 1911 if year < 1911 else year, month, day).isoformat()
    except (ValueError, UnicodeError, TypeError):
        pass
    return None


def _official_probes() -> dict[str, Callable]:
    """各來源單次請求；只回日期／狀態小紀錄，不寫原件或回傳原本文。"""
    import daily_ingest as di
    import listed_institutional as li
    import otc_history as oh
    import otc_market_data as omd
    import stock_history as sh

    def twse(url: str, params: Callable[[str], dict]) -> Callable:
        def probe(day: str) -> dict:
            response = sh.http_get(url, params(day.replace("-", "")))
            no_data = di._no_data_json(response.content)
            ready = response.status == 200 and not no_data and \
                '"stat":"OK"' in response.content.decode("utf-8", "replace").replace(" ", "")
            as_of = _response_as_of(response)
            ready = ready and as_of is not None
            return {"ready": ready, "as_of": as_of, "http_status": response.status,
                    "reason": "http-error" if response.status != 200 else
                              "ready" if ready else "not-published" if no_data else "invalid-payload"}
        return probe

    def tpex(url: str, params: Callable[[str], dict], empty: Callable) -> Callable:
        def probe(day: str) -> dict:
            import requests
            with requests.Session() as session:
                response = oh.http_get(session, url, params(day))
            no_data = empty(response)
            as_of = _response_as_of(response)
            ready = response.status == 200 and not no_data and as_of is not None
            return {"ready": ready, "as_of": as_of, "http_status": response.status,
                    "reason": "http-error" if response.status != 200 else "ready" if ready else
                              "not-published" if no_data else "invalid-payload"}
        return probe

    return {
        "listed_quotes": twse(sh.MI_INDEX_URL, lambda d: {"date": d, "type": "ALLBUT0999", "response": "json"}),
        "listed_institutional": twse(li.T86_URL, lambda d: {"date": d, "selectType": "ALLBUT0999", "response": "json"}),
        "otc_quotes": tpex(oh.DAILY_QUOTES_URL, lambda d: {"date": di._roc_slash(d), "id": "", "response": "csv"},
                           di._otc_quotes_empty),
        "otc_institutional": tpex(omd.INSTITUTIONAL_URL,
                                  lambda d: omd._request_params("institutional", d.replace("-", "")),
                                  di._tpex_json_empty),
    }


def _ingest_fetch(day: str, **kwargs) -> dict:
    import daily_ingest
    return daily_ingest.fetch_day(day, **kwargs)


def _ingest_build(day: str, **kwargs) -> dict:
    """附加入庫已在 fetch_day 逐資料集完成；這裡核對各庫最新資料日確實到 day，並重建基本面序列檔。"""
    import json as _json
    import fundamentals_history as fh
    databases = {
        "stock_history": _latest_source_day(ROOT / "data/stock-history/stock-history.sqlite"),
        "otc_history": _latest_source_day(ROOT / "data/otc-history/otc_history.sqlite"),
    }
    behind = [name for name, latest in databases.items() if (latest or "") < day]
    if behind:
        return {"status": "behind", "databases": databases, "behind": behind}
    out = ROOT / "data/fundamentals-history"
    history = fh.load_history(out)
    payload = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), **history.to_dict()}
    target = out / "history.json"
    partial = target.with_name(target.name + ".partial")
    partial.write_text(_json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(partial, target)
    return {"status": "ok", "databases": databases,
            "fundamentals": {"monthly": len(payload["monthly"]), "quarterly": len(payload["quarterly"])}}


CLOUD = ROOT / "cloud"
D1_DATABASE = "market-ana-accounts"
PAGES_PROJECT = "market-ana"
PRODUCTION_SITE = "https://market-ana.pages.dev"
ACCESS_LOGIN_HOST = "cloudflareaccess.com"
PROBE_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) market-ana-post-deploy-check"
# D1 單一語句上限 100KB（每條 ≤90KB）；一個 --file 可含多條語句（上限 5GB，官方 limits 頁）。
# 一檔約 2MB：353 檔一天約 5～10 次往返，而不是 181 次。
D1_GROUP_BYTES = 2_000_000
# 前一天成功發布的 seed 放在這裡（R2 工作集也帶它），增量匯入以它為 base。
LAST_SEED_DIR = ROOT / "data/daily-update-state/last-seed"
# 最近一次成功重建的大盤頁資料包（R2 工作集也帶它）；當天官方月檔未更新時沿用。
LAST_MARKET_DIR = ROOT / "data/daily-update-state/last-market-pack"


def _remember_market_pack(source: Path) -> None:
    import shutil
    partial = LAST_MARKET_DIR.with_name(LAST_MARKET_DIR.name + ".partial")
    if partial.exists():
        shutil.rmtree(partial)
    shutil.copytree(source, partial)
    if LAST_MARKET_DIR.exists():
        shutil.rmtree(LAST_MARKET_DIR)
    os.replace(partial, LAST_MARKET_DIR)


def _npx(*args: str) -> list[str]:
    return ["npx", "--yes", "wrangler@4", *args]


def _seed_dir(ctx: RunContext) -> Path:
    return Path(ctx.outputs.get("build_seed", {}).get("seed_dir") or ctx.work_dir / "seed")


def _previous_seed_dir(ctx: RunContext) -> Path | None:
    """上一次成功切換的 seed；沒有或版本相同就回 None（完整匯入）。"""
    manifest = LAST_SEED_DIR / "manifest.json"
    if not manifest.exists():
        return None
    current = _seed_dir(ctx) / "manifest.json"
    if current.exists() and json.loads(current.read_text(encoding="utf-8")).get("version") == \
            json.loads(manifest.read_text(encoding="utf-8")).get("version"):
        return None
    return LAST_SEED_DIR


def _remember_seed(ctx: RunContext) -> dict:
    """切換成功後把今天的 seed 記成下一次的 base（原子換目錄）。"""
    import shutil
    source = _seed_dir(ctx)
    LAST_SEED_DIR.parent.mkdir(parents=True, exist_ok=True)
    partial = LAST_SEED_DIR.with_name(LAST_SEED_DIR.name + ".partial")
    if partial.exists():
        shutil.rmtree(partial)
    partial.mkdir()
    for name in ("manifest.json", "seed.jsonl"):
        shutil.copy2(source / name, partial / name)
    old = LAST_SEED_DIR.with_name(LAST_SEED_DIR.name + ".old")
    if LAST_SEED_DIR.exists():
        if old.exists():
            shutil.rmtree(old)
        os.replace(LAST_SEED_DIR, old)
    os.replace(partial, LAST_SEED_DIR)
    if old.exists():
        shutil.rmtree(old)
    return {"remembered": json.loads((LAST_SEED_DIR / "manifest.json").read_text(encoding="utf-8"))["version"]}


def _install_stock_pack(source: Path) -> dict:
    """把今天的 Functions 內建個股包放到 cloud/.stock-pack.json（build.mjs 讀這個檔）。"""
    import shutil
    target = CLOUD / ".stock-pack.json"
    partial = target.with_name(target.name + ".partial")
    shutil.copy2(source, partial)
    os.replace(partial, target)
    return {"installed": target.name}


def _write_status_for_build(ctx: RunContext) -> dict:
    """build.mjs 要讀 cloud/.data-status.json：寫入「若今天部署成功」時的狀態（最後成功日＝今天）。

    部署前就先放今天的日期是刻意的：部署失敗時不會有新的 Functions 上線，正式站仍是舊包裡的舊狀態；
    部署成功才會讓前端看到今天。失敗後的下一次部署（隔天）會帶著 run.status=failed 的上一天紀錄。
    """
    log_dir = ctx.outputs.get("_log_dir")
    previous_path = Path(log_dir) / _STATUS_FILE if log_dir else None
    previous = json.loads(previous_path.read_text(encoding="utf-8")) if previous_path and previous_path.exists() else {}
    status = {"schema": STATUS_SCHEMA, "last_success_date": ctx.data_date,
              "last_success_at": datetime.now(TPE).isoformat(timespec="seconds"),
              "run": {"data_date": ctx.data_date, "status": "success",
                      "finished_at": datetime.now(TPE).isoformat(timespec="seconds")},
              "stale": False}
    if previous.get("run", {}).get("status") == "failed" and previous["run"].get("data_date") != ctx.data_date:
        status["previous_failure"] = {k: previous["run"].get(k) for k in ("data_date", "failed_step", "reason")}
    _write_record(CLOUD / ".data-status.json", status)
    return {"status_file": ".data-status.json", "last_success_date": ctx.data_date}


def post_deploy_verdict(*, probes: dict, active_version: str | None, expected_version: str | None) -> dict:
    """未登入的每個路徑都必須被導到 Access 登入；D1 active 必須是今天匯入的版本。"""
    exposed = [path for path, (status, location) in probes.items()
               if not (status in (301, 302, 303, 307, 308) and location and ACCESS_LOGIN_HOST in location)]
    if exposed:
        return {"ok": False, "reason": f"未登入可直接存取或未導向 Access：{', '.join(exposed)}"}
    if expected_version and active_version != expected_version:
        return {"ok": False, "reason": f"D1 active 是 {active_version}，不是今天的 {expected_version}"}
    return {"ok": True, "reason": "Access 仍保護所有路徑；D1 active 為今天的版本"}


def _probe_unauthenticated(paths: Iterable[str]) -> dict:
    import urllib.error
    import urllib.request

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return None

    opener = urllib.request.build_opener(NoRedirect)
    out = {}
    for path in paths:
        try:
            # Python 預設 UA 會被 Cloudflare 直接 403（error 1010），看不到 Access 的 302；用一般瀏覽器 UA。
            request = urllib.request.Request(PRODUCTION_SITE + path, method="GET",
                                             headers={"User-Agent": PROBE_USER_AGENT})
            response = opener.open(request, timeout=30)
            out[path] = (response.status, response.headers.get("location"))
        except urllib.error.HTTPError as error:
            out[path] = (error.code, error.headers.get("location"))
    return out


def _remote_active_version() -> str | None:
    result = _sh(_npx("d1", "execute", D1_DATABASE, "--remote", "--json", "--command",
                      "SELECT active_version FROM stock_data_state WHERE key = 'current'"), cwd=CLOUD)
    text = result.get("_stdout") or ""
    payload = json.loads(text[text.index("["):])
    rows = payload[0]["results"]
    return rows[0]["active_version"] if rows else None


def real_steps() -> list[Step]:
    """正式流程的步驟表（U2 起全部接上）。dry-run 時會改動遠端的步驟由 run() 跳過，只記錄。"""

    def fetch_incremental(ctx: RunContext) -> dict:
        report = _ingest_fetch(ctx.data_date)
        if report.get("status") != "ok":
            blocking = report.get("blocking") or [name for name, result in report.get("datasets", {}).items()
                                                  if result.get("status") not in ("appended", "already-present")]
            details = {name: report.get("datasets", {}).get(name) for name in blocking}
            raise RuntimeError(f"必要資料集未就緒：{', '.join(blocking)} {details}")
        return report

    def build_databases(ctx: RunContext) -> dict:
        report = _ingest_build(ctx.data_date)
        if report.get("status") != "ok":
            raise RuntimeError(f"資料庫未更新到 {ctx.data_date}：{report}")
        return report

    def build_universe(ctx: RunContext) -> dict:
        out = ctx.work_dir / "liquid-universe"
        return _sh([sys.executable, "liquid_universe.py", "--as-of", ctx.data_date, "--snapshot-root", str(out)])

    def publish_snapshots(ctx: RunContext) -> dict:
        out = ctx.work_dir / "market-screen-snapshots"
        # 快照不可覆寫：同一資料日重試時沿用已封存的那批，不用新的發布時間重發（否則會被衝突保護擋下）。
        if (out / "universe" / f"{ctx.data_date}.json").exists():
            return {"reused": str(out)}
        return _sh([sys.executable, "market_screens_publish.py", "--as-of", ctx.data_date,
                    "--published-at", datetime.now(TPE).isoformat(timespec="seconds"),
                    "--snapshot-root", str(out), "--universe-root", str(ctx.work_dir / "liquid-universe")])

    def build_seed(ctx: RunContext) -> dict:
        out = ctx.work_dir / "seed"
        # build_stock_pack 不覆寫既有目錄；同一資料日重試時沿用已建好的 seed（版本相同，進度檔可續傳）。
        if (out / "manifest.json").exists():
            manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            return {"seed_dir": str(out), "version": manifest.get("version"), "reused": True}
        universe = ctx.work_dir / "liquid-universe" / f"{ctx.data_date}.json"
        result = _sh([sys.executable, str(CLOUD / "build_stock_pack.py"), "--out", str(out), "--universe", str(universe)])
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8")) if (out / "manifest.json").exists() else {}
        return {**result, "seed_dir": str(out), "version": manifest.get("version")}

    def build_dist(ctx: RunContext) -> dict:
        report: dict = {}
        report["screens"] = _sh([sys.executable, str(CLOUD / "build_screens_pack.py"),
                                 "--snapshot-root", str(ctx.work_dir / "market-screen-snapshots"),
                                 "--out", str(CLOUD / ".screens-pack.json")])
        # B1：和 0050 比（母體內每檔＋0050 的含息對照），用當日母體快照重算。
        report["compare"] = _sh([sys.executable, str(CLOUD / "build_compare_pack.py"),
                                 "--universe", str(ctx.work_dir / "liquid-universe" / f"{ctx.data_date}.json"),
                                 "--out", str(CLOUD / ".compare-pack.json")])
        market_dir = ctx.work_dir / "market-pack"
        try:
            report["market"] = _sh([sys.executable, str(CLOUD / "market_pack_daily.py"), "--date", ctx.data_date,
                                    "--out", str(market_dir)])
            _remember_market_pack(market_dir)
        except RuntimeError as error:
            # 證交所成交金額月檔常比個股資料晚公布：大盤頁沿用上一版（前端依資料日顯示），其餘照常發布。
            if not (LAST_MARKET_DIR / "analysis-data.js").exists():
                raise
            market_dir = LAST_MARKET_DIR
            report["market"] = {"fallback": True, "reason": str(error)[-300:]}
        report["stock_pack"] = _install_stock_pack(_seed_dir(ctx) / "stock-pack-v2.json")
        report["status"] = _write_status_for_build(ctx)
        env_prefix = ["cmd", "/c", f"set MARKET_DATA_DIR={market_dir}&&"] if os.name == "nt" else \
            ["env", f"MARKET_DATA_DIR={market_dir}"]
        report["build"] = _sh([*env_prefix, "node", str(CLOUD / "build.mjs")], cwd=CLOUD)
        return report

    def d1_import(ctx: RunContext) -> dict:
        args = [sys.executable, str(CLOUD / "import_d1_seed.py"), "import", "--seed-dir", str(_seed_dir(ctx)),
                "--work-dir", str(ctx.work_dir / "d1-import"), "--group-bytes", str(D1_GROUP_BYTES),
                "--remote", "--confirm"]
        base = _previous_seed_dir(ctx)
        if base is not None:
            args += ["--base-seed-dir", str(base)]
        return {**_sh(args), "base_seed": str(base) if base else None}

    def d1_verify(ctx: RunContext) -> dict:
        return _sh([sys.executable, str(CLOUD / "import_d1_seed.py"), "verify", "--seed-dir", str(_seed_dir(ctx)),
                    "--work-dir", str(ctx.work_dir / "d1-import"), "--remote", "--confirm"])

    def d1_activate(ctx: RunContext) -> dict:
        return _sh([sys.executable, str(CLOUD / "import_d1_seed.py"), "activate", "--seed-dir", str(_seed_dir(ctx)),
                    "--work-dir", str(ctx.work_dir / "d1-import"), "--remote", "--confirm"])

    def pages_deploy(ctx: RunContext) -> dict:
        return _sh(_npx("pages", "deploy", "dist", "--project-name", PAGES_PROJECT, "--branch", "main",
                        "--commit-dirty=true"), cwd=CLOUD)

    def post_deploy_check(ctx: RunContext) -> dict:
        expected = ctx.outputs.get("build_seed", {}).get("version")
        verdict = post_deploy_verdict(probes=_probe_unauthenticated(["/", "/api/data-status", "/api/stocks/search?q=2330"]),
                                      active_version=_remote_active_version(), expected_version=expected)
        if not verdict["ok"]:
            raise RuntimeError(verdict["reason"])
        # 全部驗證通過才把今天的 seed 記成明天增量的 base；途中失敗會 rollback，base 也就不能前進。
        return {**verdict, **_remember_seed(ctx)}

    steps = {
        "fetch_incremental": fetch_incremental,
        "build_databases": build_databases,
        "build_universe": build_universe,
        "publish_snapshots": publish_snapshots,
        "build_seed": build_seed,
        "build_dist": build_dist,
        "d1_import": d1_import,
        "d1_verify": d1_verify,
        "d1_activate": d1_activate,
        "pages_deploy": pages_deploy,
        "post_deploy_check": post_deploy_check,
    }
    return [Step(name, steps[name], mutates_remote=name in REMOTE_STEPS) for name in STEP_ORDER]


def remote_rollback(ctx: RunContext) -> dict:
    """切換後部署或驗證失敗：D1 active 切回上一版。Pages 若已上傳新版，回退要另用 Pages 的部署回退（不自動做）。"""
    return _sh([sys.executable, str(CLOUD / "import_d1_seed.py"), "rollback",
                "--work-dir", str(ctx.work_dir / "d1-import"), "--remote", "--confirm"])


def main(argv: list[str] | None = None, *, clock=None) -> int:
    clock = clock or SystemClock()
    parser = argparse.ArgumentParser(description="每日收盤後更新協調器")
    parser.add_argument("--date", help="資料日 YYYY-MM-DD；預設為台北時間今天")
    parser.add_argument("--log-dir", type=Path, default=ROOT / "data/daily-update-log")
    parser.add_argument("--dry-run", action="store_true", help="不執行任何會改動遠端的步驟")
    parser.add_argument("--no-wait", action="store_true", help="只檢查一次就緒，不輪詢")
    parser.add_argument("--local-ready", action="store_true",
                        help="以本機已入庫判斷就緒（重跑已抓過的歷史日用；預設問官方是否已公布）")
    parser.add_argument("--write-status-only", action="store_true",
                        help="只寫 cloud/.data-status.json（最後成功日＝--date），給手動 build／部署用，不跑任何步驟")
    parser.add_argument("--allow-not-ready", action="store_true",
                        help="U3：Actions 前幾個 cron 時段用；官方未就緒時 exit 0（等下個時段），其他失敗照樣 exit 1")
    parser.add_argument("--result-file", type=Path,
                        help="U3：把本次結果寫成 JSON（workflow 依 sync_state 決定要不要把工作集同步回 R2）")
    parser.add_argument("--trigger-event", default="", help="可信 CI 事件名稱；缺省為嚴格模式")
    parser.add_argument("--trigger-source", default="", help="github-schedule／cloudflare-cron")
    parser.add_argument("--trigger-slot", default="", help="原排程 UTC cron；僅已知前段時段可等待")
    args = parser.parse_args(argv)
    data_date = args.date or clock.now().astimezone(TPE).date().isoformat()
    if date.fromisoformat(data_date).isoformat() != data_date:
        parser.error("date 必須是真實的 YYYY-MM-DD")
    if args.write_status_only:
        ctx = RunContext(data_date, False, ROOT / ".tmp" / "daily-update" / data_date,
                         outputs={"_log_dir": str(args.log_dir)})
        print(json.dumps(_write_status_for_build(ctx), ensure_ascii=False))
        return 0
    import daily_ingest
    holidays = daily_ingest.fetch_holidays(args.log_dir / "holiday-schedule.json") or None
    config = RunConfig(data_date=data_date, log_dir=args.log_dir, dry_run=args.dry_run, holidays=holidays,
                       ready_interval=0 if args.no_wait else 600,
                       ready_deadline=clock.now() if args.no_wait else None,
                       trigger_event=args.trigger_event, trigger_source=args.trigger_source,
                       trigger_slot=args.trigger_slot, legacy_allow_not_ready=args.allow_not_ready)
    observations: list[dict] = []

    def ready_check() -> bool:
        local = local_state_probe(data_date)
        if args.local_ready or local:
            # 保留原本機重跑分支；沒有讀到來源日期就明示缺項，不假填請求日。
            observations[:] = [{"source": "local_state", "observed_at": _stamp(clock), "as_of": None,
                               "ready": local, "status": "ready" if local else "not-ready",
                               "reason": "ready" if local else "not-published", "missing": ["as_of"]}]
            return local
        return official_ready(data_date, observations=observations, clock=clock)

    record = run(config, steps=real_steps(), probe=local_state_probe, ready_check=ready_check,
                 rollback=remote_rollback, clock=clock, readiness_observations=observations)
    summary = {k: record.get(k) for k in ("data_date", "status", "failed_step")}
    published = record.get("status") == "success" and not args.dry_run
    summary.update(published=published, sync_state=published, readiness=record.get("readiness"),
                   message=("等待官方資料，未發布；下個時段重試" if record["status"] == "waiting" else
                            "本次已發布" if published else "本次未發布"))
    print(json.dumps(summary, ensure_ascii=False))
    if args.result_file:
        # exit0 包括 waiting／dry-run／already-done；只有真發布才允許 R2／AI 接續。
        _write_record(args.result_file, summary)
    return 0 if record["status"] in {"success", "skipped", "already-done", "waiting"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
