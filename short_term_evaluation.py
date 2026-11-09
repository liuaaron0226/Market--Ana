"""短期候選的到期評估（票09核心）與 short-v1.x 時間分割歷史驗證。

到期評估：讀一份已封存的短期快照，以市場交易日曆取發布日後第 1～3 個交易日，逐日核對
可比口徑收盤是否較固定基準收盤至少 +5%（盤中觸及不算），另列期間最大有利／不利、進場觸發、
防守觸及與價格失效；只有日 K 無法分辨同一根 K 先後時標「先後未知」。未到期一律 pending。
評估結果是另存的事件紀錄（``<root>/short-eval/<batch_id>/<evaluated_as_of>.json``），
不改原快照；同一份紀錄重跑不變，內容不同即拒絕覆寫。

可比口徑：以官方「收盤−漲跌＝參考價」偵測公司行動（除權息、分割、減資），參考價與前一收盤
不同時以「前收／參考價」調整；官方標「X 不比價」的日子改用公司行動事件表的停止買賣前收盤與
參考價。兩者都無法解釋的日子起標「不可比」，不硬算成功或失敗。原始收盤一併保留。

時間分割驗證（``--backtest``）：逐交易日只用當日以前的資料重跑 short_term_screen，分成固定
的開發期／保留期統計，並對照 0050 與同流動性條件的隨機股票。上市三大法人只有近 20 日原件，
長期回測必須以「籌碼門視為通過」的消融版本進行，結果不代表完整 short-v1.x。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sqlite3
import statistics
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from types import MappingProxyType
from typing import Any

import candidate_snapshots

ROOT = Path(__file__).resolve().parent
SCHEMA_VERSION = "short-eval-v1"
EVAL_KIND = "short-eval"
THRESHOLD_PCT = 5.0
WINDOW_DAYS = 3
BENCHMARK_CODE = "0050"
# 時間分割固定切點：開發期 < 2022-01-01 ≤ 保留期。寫死在程式，不事後挑選。
HOLDOUT_START = "2022-01-01"
LIQUIDITY_VARIANTS = (20_000_000, 50_000_000, 100_000_000)
_PRICE_TOLERANCE = 0.006
_INVALIDATION_LEVEL = re.compile(r"關鍵位\s*(\d+(?:\.\d+)?)")


# ---------------------------------------------------------------- 到期評估（單檔）

def _pct(value: float, base: float) -> float:
    return round((value / base - 1) * 100, 4)


def _comparable_path(
    code: str,
    bars: Mapping[str, Mapping[str, Any]],
    events: Mapping[tuple[str, str], Mapping[str, Any]],
    base_close: float,
    path_days: Sequence[str],
) -> dict[str, dict[str, Any]]:
    """自基準日後逐交易日累積公司行動調整係數；回傳每日可比收盤／高／低與狀態。"""
    factor = 1.0
    prev_close = base_close
    broken = False
    out: dict[str, dict[str, Any]] = {}
    for day in path_days:
        row = bars.get(day)
        if row is None or row.get("close") is None:
            out[day] = {"status": "no_close", "suspended": bool(row and row.get("suspended")), "corporate_actions": []}
            continue
        close = float(row["close"])
        actions: list[dict[str, Any]] = []
        if not broken:
            if row.get("change_sign") == "X":
                event = events.get((day, code))
                if event and event.get("close_before") and event.get("reference"):
                    ratio = float(event["close_before"]) / float(event["reference"])
                    factor *= ratio
                    actions.append({"source": "official_event", "type": event.get("type"), "ratio": round(ratio, 6),
                                    "close_before": event["close_before"], "reference": event["reference"]})
                else:
                    broken = True
            elif row.get("change") is not None:
                reference = round(close - float(row["change"]), 4)
                if abs(reference - prev_close) > max(_PRICE_TOLERANCE, prev_close * 1e-6):
                    if reference > 0:
                        ratio = prev_close / reference
                        factor *= ratio
                        actions.append({"source": "inferred_from_official_change", "ratio": round(ratio, 6),
                                        "previous_close": prev_close, "reference": reference})
                    else:
                        broken = True
            else:
                broken = True
        entry: dict[str, Any] = {"status": "not_comparable" if broken else "ok", "close": close, "corporate_actions": actions}
        if not broken:
            entry.update(
                comparable_close=close * factor,
                comparable_high=float(row["high"]) * factor if row.get("high") is not None else None,
                comparable_low=float(row["low"]) * factor if row.get("low") is not None else None,
            )
        out[day] = entry
        prev_close = close
    return out


def _window(calendar: Sequence[str], publish_date: str) -> list[str]:
    return [day for day in calendar if day > publish_date][:WINDOW_DAYS]


def _invalidation_level(candidate: Mapping[str, Any]) -> float | None:
    for text in candidate.get("invalidation") or []:
        match = _INVALIDATION_LEVEL.search(str(text))
        if match:
            return float(match.group(1))
    return None


def _benchmark(
    benchmark_bars: Mapping[str, Mapping[str, Any]],
    events: Mapping[tuple[str, str], Mapping[str, Any]],
    base_date: str,
    calendar: Sequence[str],
    window: Sequence[str],
    as_of: str,
) -> dict[str, Any]:
    base_row = benchmark_bars.get(base_date)
    if not base_row or base_row.get("close") is None or len(window) < WINDOW_DAYS or window[-1] > as_of:
        return {"code": BENCHMARK_CODE, "day3_return_pct": None}
    path_days = [day for day in calendar if base_date < day <= window[-1]]
    path = _comparable_path(BENCHMARK_CODE, benchmark_bars, events, float(base_row["close"]), path_days)
    last = path.get(window[-1], {})
    value = _pct(last["comparable_close"], float(base_row["close"])) if last.get("status") == "ok" else None
    return {"code": BENCHMARK_CODE, "base_close": base_row["close"], "day3_return_pct": value}


def evaluate_candidate(
    candidate: Mapping[str, Any],
    *,
    publish_date: str,
    calendar: Sequence[str],
    bars: Mapping[str, Mapping[str, Any]],
    events: Mapping[tuple[str, str], Mapping[str, Any]],
    benchmark_bars: Mapping[str, Mapping[str, Any]],
    as_of: str,
) -> dict[str, Any]:
    """單檔到期評估；只讀發布日之後、``as_of`` 以前的資料，不改候選內容。"""
    code = str(candidate["code"])
    baseline = candidate["baseline"]
    base_date, base_close = str(baseline["date"]), float(baseline["close"])
    window = _window(calendar, publish_date)
    known = [day for day in window if day <= as_of]
    path_days = [day for day in calendar if base_date < day <= (known[-1] if known else base_date)]
    path = _comparable_path(code, bars, events, base_close, path_days)
    defense = (candidate.get("defense") or {}).get("level")
    trigger = (candidate.get("entry_trigger") or {}).get("level")
    invalidation_level = _invalidation_level(candidate)
    target = base_close * (1 + THRESHOLD_PCT / 100)

    days: list[dict[str, Any]] = []
    first_hit = first_event = invalidation_day = trigger_day = None
    favorable: list[float] = []
    adverse: list[float] = []
    closes: list[float] = []
    for index, day in enumerate(known, start=1):
        point = path[day]
        entry: dict[str, Any] = {"index": index, "date": day, "status": point["status"],
                                 "close": point.get("close"), "corporate_actions": point["corporate_actions"]}
        if point.get("close") is not None:
            entry["raw_close_return_pct"] = _pct(point["close"], base_close)
        if point["status"] == "ok":
            comparable = _pct(point["comparable_close"], base_close)
            entry["comparable_close_return_pct"] = comparable
            closes.append(comparable)
            high, low = point.get("comparable_high"), point.get("comparable_low")
            if high is not None:
                favorable.append(_pct(high, base_close))
                entry["comparable_high_pct"] = favorable[-1]
            if low is not None:
                adverse.append(_pct(low, base_close))
                entry["comparable_low_pct"] = adverse[-1]
            if first_hit is None and round(comparable, 6) >= THRESHOLD_PCT:
                first_hit = index
            if first_event is None:
                touched_target = high is not None and high >= target - 1e-9
                touched_defense = defense is not None and low is not None and low <= float(defense) + 1e-9
                if touched_target and touched_defense:
                    first_event = "unknown_same_bar"
                elif touched_target:
                    first_event = "target_first"
                elif touched_defense:
                    first_event = "defense_first"
            if invalidation_day is None and invalidation_level is not None and point["comparable_close"] < invalidation_level:
                invalidation_day = index
            if trigger_day is None and trigger is not None and high is not None and high >= float(trigger) - 1e-9:
                trigger_day = index
        days.append(entry)

    if len(known) < WINDOW_DAYS:
        outcome = "pending"
    elif first_hit is not None:
        outcome = "hit"
    elif all(day["status"] == "ok" for day in days):
        outcome = "miss"
    else:
        outcome = "unevaluable"
    max_favorable = max(favorable) if favorable else None
    benchmark = _benchmark(benchmark_bars, events, base_date, calendar, window, as_of)
    day3 = days[2].get("comparable_close_return_pct") if len(days) == WINDOW_DAYS else None
    return {
        "code": code,
        "market": candidate.get("market"),
        "baseline": {"date": base_date, "close": base_close},
        "publish_date": publish_date,
        "window_dates": window,
        "outcome": outcome,
        "first_close_hit_day": first_hit,
        "days": days,
        "best_close_pct": max(closes) if closes else None,
        "day3_comparable_pct": day3,
        "max_favorable_pct": max_favorable,
        "max_adverse_pct": min(adverse) if adverse else None,
        "intraday_touch_only": outcome != "hit" and max_favorable is not None and max_favorable >= THRESHOLD_PCT,
        "first_intraday_event": first_event or ("none" if days else None),
        "entry_trigger_level": trigger,
        "entry_trigger_day": trigger_day,
        "invalidation_close_level": invalidation_level,
        "invalidation_close_day": invalidation_day,
        "invalidated_before_hit": invalidation_day is not None and (first_hit is None or invalidation_day < first_hit),
        "unchecked_conditions": ["法人淨買超轉為零或負值（需逐日法人資料，本評估未核）"],
        "benchmark": benchmark,
        "relative_day3_pct": (
            round(day3 - benchmark["day3_return_pct"], 4)
            if day3 is not None and benchmark.get("day3_return_pct") is not None else None
        ),
    }


# ---------------------------------------------------------------- 快照評估與事件紀錄

def _canonical(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def seal(record: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical({k: v for k, v in record.items() if k != "eval_sha256"})).hexdigest()


def summarize_outcomes(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """含成功、失敗、不可評估與待到期的分母；命中率只以可評估（hit＋miss）為分母。"""
    outcomes = Counter(str(row["outcome"]) for row in results)
    decided = outcomes["hit"] + outcomes["miss"]
    day3 = [row["day3_comparable_pct"] for row in results if row.get("day3_comparable_pct") is not None]
    best = [row["best_close_pct"] for row in results if row.get("best_close_pct") is not None]
    adverse = [row["max_adverse_pct"] for row in results if row.get("max_adverse_pct") is not None]
    return {
        "n": len(results),
        "outcomes": dict(sorted(outcomes.items())),
        "hit_rate_evaluable": round(outcomes["hit"] / decided, 4) if decided else None,
        "mean_day3_pct": round(statistics.fmean(day3), 4) if day3 else None,
        "median_day3_pct": round(statistics.median(day3), 4) if day3 else None,
        "mean_best_close_pct": round(statistics.fmean(best), 4) if best else None,
        "mean_adverse_pct": round(statistics.fmean(adverse), 4) if adverse else None,
        "worst_adverse_pct": min(adverse) if adverse else None,
    }


Loader = Callable[[str, str], tuple[Sequence[str], Mapping[str, Mapping[str, Any]],
                                     Mapping[tuple[str, str], Mapping[str, Any]], Mapping[str, Mapping[str, Any]]]]


def evaluate_snapshot(snapshot: Mapping[str, Any], *, as_of: str, loader: Loader) -> dict[str, Any]:
    """一份已封存短期快照的到期評估紀錄；快照雜湊不符直接拒絕。"""
    if snapshot.get("snapshot_sha256") != candidate_snapshots.seal_hash(snapshot):
        raise candidate_snapshots.SnapshotIntegrityError("快照封存雜湊不符：不評估被改動的快照")
    if snapshot.get("kind") != "short":
        raise ValueError("只評估短期（kind=short）快照")
    results = []
    for candidate in snapshot.get("candidates", []):
        calendar, bars, events, benchmark_bars = loader(str(candidate["code"]), str(candidate.get("market")))
        results.append(evaluate_candidate(candidate, publish_date=snapshot["publish_date"], calendar=calendar,
                                          bars=bars, events=events, benchmark_bars=benchmark_bars, as_of=as_of))
    if snapshot.get("status") != "ok":
        status = str(snapshot.get("status"))
    else:
        status = "pending" if any(row["outcome"] == "pending" for row in results) else "final"
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": EVAL_KIND,
        "batch_id": snapshot["batch_id"],
        "publish_date": snapshot["publish_date"],
        "data_date": snapshot["data_date"],
        "method_version": snapshot["method_version"],
        "snapshot_sha256": snapshot["snapshot_sha256"],
        "evaluated_as_of": as_of,
        "criteria": {
            "threshold_pct": THRESHOLD_PCT,
            "window_trading_days": WINDOW_DAYS,
            "basis": "發布日後第1～3交易日，任一日可比口徑收盤 ≥ 固定基準收盤×1.05；盤中觸及不算",
            "benchmark": BENCHMARK_CODE,
            "note": "價格達標、操作條件成立與失效分開記錄；不是成交或損益",
        },
        "status": status,
        "results": results,
        "summary": summarize_outcomes(results),
    }
    record["eval_sha256"] = seal(record)
    return record


def _eval_path(root: Path, batch_id: str, as_of: str) -> Path:
    return root / EVAL_KIND / batch_id / f"{as_of}.json"


def write_evaluation(root: Path, record: Mapping[str, Any]) -> dict[str, Any]:
    """事件式附加：同一 ``evaluated_as_of`` 內容相同為 unchanged，不同即拒絕；不動快照。"""
    if record.get("eval_sha256") != seal(record):
        raise candidate_snapshots.SnapshotIntegrityError("評估紀錄雜湊不符")
    target = _eval_path(root, record["batch_id"], record["evaluated_as_of"])
    if target.exists():
        existing = json.loads(target.read_text(encoding="utf-8"))
        if existing.get("eval_sha256") == record["eval_sha256"]:
            return {"outcome": "unchanged", "path": target}
        raise candidate_snapshots.SnapshotConflict(f"{target} 已有不同內容的評估紀錄；評估修正須另留版本")
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f".{target.name}.{os.getpid()}.partial")
    partial.write_bytes(json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n")
    try:
        os.link(partial, target)
    except FileExistsError:
        existing = json.loads(target.read_text(encoding="utf-8"))
        if existing.get("eval_sha256") != record["eval_sha256"]:
            raise candidate_snapshots.SnapshotConflict(f"{target} 已被另一次評估寫入不同內容") from None
        return {"outcome": "unchanged", "path": target}
    finally:
        partial.unlink(missing_ok=True)
    return {"outcome": "written", "path": target}


def list_evaluations(root: Path, batch_id: str) -> list[dict[str, Any]]:
    folder = root / EVAL_KIND / batch_id
    records = []
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("eval_sha256") != seal(record):
            raise candidate_snapshots.SnapshotIntegrityError(f"{path} 評估紀錄雜湊不符")
        records.append(record)
    return records


# ---------------------------------------------------------------- 真實資料讀取（唯讀）

def _connect_ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)


_ROW_SQL = ("SELECT date, code, open, high, low, close, volume_shares, turnover, suspended, change_sign, change, kind "
            "FROM daily WHERE date BETWEEN ? AND ?")


def _row_dict(row: Sequence[Any]) -> dict[str, Any]:
    day, _code, open_, high, low, close, volume, turnover, suspended, sign, change, _kind = row
    return {"date": day, "open": open_, "high": high, "low": low, "close": close, "volume": volume,
            "turnover": turnover, "suspended": None if suspended is None else bool(suspended),
            "change_sign": sign, "change": change}


def load_events(root: Path = ROOT) -> dict[tuple[str, str], dict[str, Any]]:
    path = root / "data/corporate-actions/events.json"
    if not path.exists():
        return {}
    return {(item["date"], item["code"]): item for item in json.loads(path.read_text(encoding="utf-8"))}


def market_calendar(root: Path = ROOT) -> list[str]:
    connection = _connect_ro(root / "data/stock-history/stock-history.sqlite")
    try:
        return [row[0] for row in connection.execute("SELECT DISTINCT date FROM daily ORDER BY date")]
    finally:
        connection.close()


def make_real_loader(since: str, root: Path = ROOT) -> Loader:
    """快照評估用：上市讀既有 SQLite、上櫃讀 otc_history（皆 mode=ro）。"""
    calendar = market_calendar(root)
    events = load_events(root)
    dbs = {"TWSE": root / "data/stock-history/stock-history.sqlite", "TPEX": root / "data/otc-history/otc_history.sqlite"}

    def bars_for(db: Path, code: str) -> dict[str, dict[str, Any]]:
        connection = _connect_ro(db)
        try:
            rows = connection.execute(_ROW_SQL + " AND code = ?", (since, "9999-12-31", code)).fetchall()
        finally:
            connection.close()
        return {row[0]: _row_dict(row) for row in rows}

    benchmark = bars_for(dbs["TWSE"], BENCHMARK_CODE)

    def loader(code: str, market: str) -> tuple[list[str], dict, dict, dict]:
        return calendar, bars_for(dbs.get(market, dbs["TWSE"]), code), events, benchmark

    return loader


# ---------------------------------------------------------------- 時間分割歷史驗證

def period_of(day: str) -> str:
    return "development" if day < HOLDOUT_START else "holdout"


def sample_benchmark(pool: Sequence[str], k: int, *, seed_key: str) -> list[str]:
    """同條件隨機基準：以 seed_key 決定的固定抽樣，重跑結果相同。"""
    seed = int(hashlib.sha256(seed_key.encode("utf-8")).hexdigest()[:16], 16)
    ordered = sorted(pool)
    return sorted(random.Random(seed).sample(ordered, min(k, len(ordered))))


def _avg_turnover20(rows: Sequence[Mapping[str, Any]]) -> float | None:
    valid = [row for row in rows if row.get("close") is not None and not row.get("suspended")][-20:]
    if len(valid) < 20 or any(row.get("turnover") is None for row in valid):
        return None
    return sum(int(row["turnover"]) for row in valid) / 20


def _avg_volume20(rows: Sequence[Mapping[str, Any]]) -> int:
    valid = [row for row in rows if row.get("close") is not None and not row.get("suspended")][-20:]
    return round(sum(int(row["volume"] or 0) for row in valid) / 20) if len(valid) >= 20 else 0


def _compact(result: Mapping[str, Any]) -> dict[str, Any]:
    keys = ("code", "market", "outcome", "first_close_hit_day", "best_close_pct", "day3_comparable_pct",
            "max_favorable_pct", "max_adverse_pct", "intraday_touch_only", "first_intraday_event",
            "entry_trigger_day", "invalidation_close_day", "invalidated_before_hit", "relative_day3_pct")
    out = {key: result.get(key) for key in keys}
    out["benchmark_day3_pct"] = (result.get("benchmark") or {}).get("day3_return_pct")
    out["corporate_action_in_window"] = any(day["corporate_actions"] for day in result.get("days", []))
    return out


def _run_chunk(task: Mapping[str, Any]) -> dict[str, Any]:
    """一段交易日的回測：只載這段所需的資料（前 260 日暖機＋後 3 日到期）。"""
    import short_term_screen as screen

    root = Path(task["root"])
    calendar: list[str] = task["calendar"]
    dates: list[str] = task["dates"]
    mode: str = task["mode"]
    chip_mode: str = task["chip_mode"]
    original = screen.PARAMETERS
    variant = MappingProxyType({**original, "min_avg_turnover_twd": min(LIQUIDITY_VARIANTS), "max_candidates": 10_000})
    events = load_events(root)
    first = calendar.index(dates[0])
    last = calendar.index(dates[-1])
    load_from = calendar[max(0, first - 260)]
    load_to = calendar[min(len(calendar) - 1, last + WINDOW_DAYS)]
    as_of = calendar[-1]

    per_code: dict[str, dict[str, list]] = {}
    kinds: dict[str, str] = {}
    sources = [("TWSE", root / "data/stock-history/stock-history.sqlite")]
    if mode == "TWSE+TPEX":
        sources.append(("TPEX", root / "data/otc-history/otc_history.sqlite"))
    for market, db in sources:
        connection = _connect_ro(db)
        try:
            for row in connection.execute(_ROW_SQL + " ORDER BY code, date", (load_from, load_to)):
                code, kind = row[1], row[11]
                if kind not in ("common", "etf") and code != BENCHMARK_CODE:
                    continue
                kinds[code] = kind
                slot = per_code.setdefault(code, {"market": market, "dates": [], "rows": []})
                slot["dates"].append(row[0])
                slot["rows"].append(_row_dict(row))
        finally:
            connection.close()
    by_date_maps = {code: dict(zip(slot["dates"], slot["rows"])) for code, slot in per_code.items()}
    benchmark_map = by_date_maps.get(BENCHMARK_CODE, {})

    real_chips: dict[str, dict[str, Any]] = {}
    if chip_mode == "real":
        from twon_analysis import chips as twse_chips
        import otc_market_data
        t86_days = twse_chips.available_days()
        otc_days = otc_market_data.institutional_days(otc_market_data.DEFAULT_MARKET_DB)
        real_chips = {"t86_days": t86_days, "otc_days": otc_days, "twse": twse_chips, "otc": otc_market_data}

    days_out: list[dict[str, Any]] = []
    picks_out: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    for day in dates:
        index = calendar.index(day)
        window_start = calendar[max(0, index - 249)]
        last3 = calendar[max(0, index - 2): index + 1]
        universe: list[dict[str, Any]] = []
        bars_by_code: dict[str, list] = {}
        for code, slot in per_code.items():
            if kinds.get(code) != "common":
                continue
            from bisect import bisect_left, bisect_right
            lo, hi = bisect_left(slot["dates"], window_start), bisect_right(slot["dates"], day)
            if hi == 0 or slot["dates"][hi - 1] != day:
                continue
            rows = slot["rows"][lo:hi]
            valid = sum(row["open"] is not None and row["close"] is not None and not row["suspended"] for row in rows)
            universe.append({"code": code, "name": code, "market": slot["market"], "security_type": "普通股",
                             "analysis_eligible": valid >= 20})
            bars_by_code[code] = rows
        bench_slot = per_code.get(BENCHMARK_CODE)
        if bench_slot is None:
            continue
        from bisect import bisect_left, bisect_right
        market_rows = bench_slot["rows"][bisect_left(bench_slot["dates"], window_start): bisect_right(bench_slot["dates"], day)]
        market_rows = [row for row in market_rows if row["close"] is not None]
        market_as_of = market_rows[-1]["date"] if market_rows else None

        if chip_mode == "real":
            twse_days = [d for d in real_chips["t86_days"] if d <= day][-3:]
            otc_days = [d for d in real_chips["otc_days"] if d <= day][-3:]
            chips_by_code = {}
            for security in universe:
                code = security["code"]
                if security["market"] == "TWSE":
                    values, use_days = real_chips["twse"].institutional(code, twse_days), twse_days
                else:
                    values = real_chips["otc"].institutional(real_chips["otc"].DEFAULT_MARKET_DB, code, otc_days)
                    use_days = otc_days
                chips_by_code[code] = [{"date": d, "institutional_net": None if v is None else v["total"]}
                                       for d, v in zip(use_days, values)]
            chip_state = {"state": "ok" if twse_days else "failed", "as_of": twse_days[-1] if twse_days else None,
                          "days": twse_days, "markets": ["TWSE"]}
            tpex_chip_state = {"state": "ok" if otc_days else "failed", "as_of": otc_days[-1] if otc_days else None,
                               "days": otc_days}
        else:
            # 消融：籌碼門視為通過（淨買超＝20日均量，對所有股相同比例，不影響排序）。
            chips_by_code = {
                security["code"]: [{"date": d, "institutional_net": _avg_volume20(bars_by_code[security["code"]])} for d in last3]
                for security in universe
            }
            chip_state = {"state": "ok", "as_of": day, "days": last3, "markets": ["TWSE"]}
            tpex_chip_state = {"state": "ok", "as_of": day, "days": last3}
        source_states: dict[str, dict[str, Any]] = {
            "catalog": {"state": "ok", "as_of": day},
            "price": {"state": "ok", "as_of": day},
            "chips": chip_state,
            "market": {"state": "ok" if market_rows else "failed", "as_of": market_as_of},
        }
        if mode == "TWSE+TPEX":
            source_states.update(catalog_tpex={"state": "ok", "as_of": day}, price_tpex={"state": "ok", "as_of": day},
                                 chips_tpex=tpex_chip_state)
        inputs = dict(data_date=day, market=mode, universe=universe, bars_by_code=bars_by_code,
                      chips_by_code=chips_by_code, market_bars=market_rows or None, source_states=source_states)

        screen.PARAMETERS = variant
        try:
            result = screen.screen_short_term(**inputs)
        finally:
            screen.PARAMETERS = original
        qualified = result["candidates"]
        picks_by_threshold: dict[int, list[dict[str, Any]]] = {}
        for threshold in LIQUIDITY_VARIANTS:
            picks_by_threshold[threshold] = [
                c for c in qualified if c["metrics"]["avg_turnover_20"] >= threshold
            ][: original["max_candidates"]]
        if task.get("check_every") and len(days_out) % task["check_every"] == 0:
            real = screen.screen_short_term(**inputs)
            checks.append({"date": day, "status_equal": real["status"] == result["status"] or bool(qualified),
                           "codes_real": [c["code"] for c in real["candidates"]],
                           "codes_derived": [c["code"] for c in picks_by_threshold[original["min_avg_turnover_twd"]]]})
        reasons = Counter()
        for row in result["reconciliation"]:
            for reason in row["reasons"]:
                if "code" in reason:
                    reasons[reason["code"]] += 1
        days_out.append({
            "date": day, "period": period_of(day), "status": result["status"],
            "market_gate_passed": bool((result.get("market_environment") or {}).get("passed")),
            "qualified_at_min_threshold": len(qualified),
            "picks": {str(t): [c["code"] for c in picks] for t, picks in picks_by_threshold.items()},
            "top_reasons": dict(reasons.most_common(4)),
        })

        evaluated: dict[str, dict[str, Any]] = {}
        for threshold, picks in picks_by_threshold.items():
            for candidate in picks:
                code = candidate["code"]
                if code not in evaluated:
                    evaluated[code] = _compact(evaluate_candidate(
                        candidate, publish_date=day, calendar=calendar, bars=by_date_maps[code], events=events,
                        benchmark_bars=benchmark_map, as_of=as_of))
                picks_out.append({"date": day, "period": period_of(day), "threshold": threshold, "group": "screen",
                                  **evaluated[code]})
            # 同流動性條件的隨機基準：當日有效收盤、未停牌、20日均額達同一門檻的普通股。
            if picks:
                pool = [
                    s["code"] for s in universe
                    if s["analysis_eligible"] and (row := by_date_maps[s["code"]].get(day)) and row["close"] is not None
                    and not row["suspended"] and (avg := _avg_turnover20(bars_by_code[s["code"]])) is not None
                    and avg >= threshold
                ]
                for code in sample_benchmark(pool, len(picks), seed_key=f"{threshold}|{day}|{mode}"):
                    row = by_date_maps[code][day]
                    pseudo = {"code": code, "market": per_code[code]["market"],
                              "baseline": {"date": day, "close": row["close"]}}
                    picks_out.append({"date": day, "period": period_of(day), "threshold": threshold, "group": "random",
                                      **_compact(evaluate_candidate(pseudo, publish_date=day, calendar=calendar,
                                                                    bars=by_date_maps[code], events=events,
                                                                    benchmark_bars=benchmark_map, as_of=as_of))})
    return {"days": days_out, "picks": picks_out, "checks": checks}


def _benchmark_days(calendar: Sequence[str], dates: Sequence[str], root: Path) -> list[dict[str, Any]]:
    """每個回測日的 0050 三日可比報酬（無條件基準）。"""
    events = load_events(root)
    connection = _connect_ro(root / "data/stock-history/stock-history.sqlite")
    try:
        rows = connection.execute(_ROW_SQL + " AND code = ?", (dates[0], "9999-12-31", BENCHMARK_CODE)).fetchall()
    finally:
        connection.close()
    bars = {row[0]: _row_dict(row) for row in rows}
    out = []
    for day in dates:
        if day not in bars or bars[day]["close"] is None:
            continue
        pseudo = {"code": BENCHMARK_CODE, "market": "TWSE", "baseline": {"date": day, "close": bars[day]["close"]}}
        result = evaluate_candidate(pseudo, publish_date=day, calendar=calendar, bars=bars, events=events,
                                    benchmark_bars=bars, as_of=calendar[-1])
        out.append({"date": day, "period": period_of(day), **_compact(result)})
    return out


def run_backtest(*, start: str, end: str, mode: str, chip_mode: str, workers: int, chunk_days: int,
                 out_dir: Path, root: Path = ROOT, check_every: int = 0) -> dict[str, Any]:
    import short_term_screen as screen

    calendar = market_calendar(root)
    dates = [day for day in calendar if start <= day <= end]
    if not dates:
        raise ValueError("回測區間沒有交易日")
    chunks = [dates[i:i + chunk_days] for i in range(0, len(dates), chunk_days)]
    tasks = [{"root": str(root), "calendar": calendar, "dates": chunk, "mode": mode, "chip_mode": chip_mode,
              "check_every": check_every} for chunk in chunks]
    started = time.time()
    days: list[dict[str, Any]] = []
    picks: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_run_chunk, task) for task in tasks]
        for done, future in enumerate(as_completed(futures), start=1):
            part = future.result()
            days += part["days"]
            picks += part["picks"]
            checks += part["checks"]
            print(json.dumps({"progress": f"{done}/{len(futures)}", "elapsed_s": round(time.time() - started)}), flush=True)
    days.sort(key=lambda row: row["date"])
    picks.sort(key=lambda row: (row["date"], row["threshold"], row["group"], row["code"]))
    bench = _benchmark_days(calendar, dates, root)
    report = summarize_backtest(days, picks, bench)
    report["meta"] = {
        "method_version": screen.METHOD_VERSION,
        "engine_sha256": hashlib.sha256((root / "short_term_screen.py").read_bytes()).hexdigest(),
        "mode": mode, "chip_mode": chip_mode, "start": dates[0], "end": dates[-1], "trading_days": len(dates),
        "holdout_start": HOLDOUT_START, "data_as_of": calendar[-1], "elapsed_s": round(time.time() - started),
        "consistency_checks": {"n": len(checks), "mismatch": [c for c in checks if c["codes_real"] != c["codes_derived"]]},
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = f"{mode.replace('+', '-')}-{chip_mode}-{dates[0]}-{dates[-1]}"
    (out_dir / f"days-{tag}.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in days), encoding="utf-8")
    (out_dir / f"picks-{tag}.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in picks), encoding="utf-8")
    (out_dir / f"report-{tag}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def summarize_backtest(days: Sequence[Mapping[str, Any]], picks: Sequence[Mapping[str, Any]],
                       bench: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    periods = ("development", "holdout", "all")
    out: dict[str, Any] = {}
    for period in periods:
        in_period = (lambda row: True) if period == "all" else (lambda row, p=period: row["period"] == p)
        period_days = [row for row in days if in_period(row)]
        statuses = Counter(row["status"] for row in period_days)
        section: dict[str, Any] = {
            "trading_days": len(period_days),
            "status_days": dict(sorted(statuses.items())),
            "market_gate_pass_days": sum(row["market_gate_passed"] for row in period_days),
            "benchmark_0050_unconditional": summarize_outcomes([row for row in bench if in_period(row)]),
            "by_threshold": {},
        }
        for threshold in LIQUIDITY_VARIANTS:
            key = str(threshold)
            screen_rows = [row for row in picks if in_period(row) and row["threshold"] == threshold and row["group"] == "screen"]
            random_rows = [row for row in picks if in_period(row) and row["threshold"] == threshold and row["group"] == "random"]
            pick_days = {row["date"] for row in screen_rows}
            pick_dates = sorted(pick_days)
            bench_on_pick_days = [row for row in bench if row["date"] in pick_days]
            section["by_threshold"][key] = {
                "days_with_picks": len(pick_days),
                "empty_day_ratio": round(1 - len(pick_days) / len(period_days), 4) if period_days else None,
                "screen": summarize_outcomes(screen_rows),
                "random_same_liquidity": summarize_outcomes(random_rows),
                "benchmark_0050_on_pick_days": summarize_outcomes(bench_on_pick_days),
                "mean_relative_day3_vs_0050": (
                    round(statistics.fmean(r["relative_day3_pct"] for r in screen_rows if r.get("relative_day3_pct") is not None), 4)
                    if any(r.get("relative_day3_pct") is not None for r in screen_rows) else None
                ),
                "intraday_touch_only": sum(bool(r.get("intraday_touch_only")) for r in screen_rows),
                "invalidated_before_hit": sum(bool(r.get("invalidated_before_hit")) for r in screen_rows),
                "first_intraday_event": dict(Counter(str(r.get("first_intraday_event")) for r in screen_rows)),
                "overlap_note": "相鄰交易日的三日觀察窗重疊，同股連續入選不是獨立樣本",
                "first_pick_date": pick_dates[0] if pick_dates else None,
            }
        out[period] = section
    return out


# ---------------------------------------------------------------- CLI

def _evaluate_cli(args: argparse.Namespace) -> int:
    snapshot_root = Path(args.snapshot_root)
    eval_root = Path(args.eval_root)
    snapshots = candidate_snapshots.list_snapshots(snapshot_root, "short")
    outputs = []
    for snapshot in snapshots:
        record = evaluate_snapshot(snapshot, as_of=args.as_of, loader=make_real_loader(snapshot["data_date"]))
        outcome = write_evaluation(eval_root, record)["outcome"] if args.write else "dry_run"
        outputs.append({"batch_id": record["batch_id"], "status": record["status"], "outcome": outcome,
                        "summary": record["summary"], "eval_sha256": record["eval_sha256"]})
    print(json.dumps(outputs, ensure_ascii=True, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluate", action="store_true", help="評估快照根目錄下所有短期快照")
    parser.add_argument("--snapshot-root", default=str(ROOT / ".tmp/lane-C3/snapshots"))
    parser.add_argument("--eval-root", default=str(ROOT / ".tmp/lane-F1/evaluations"))
    parser.add_argument("--as-of", help="評估截止日（只讀此日以前的資料）")
    parser.add_argument("--write", action="store_true", help="與 --evaluate 併用：寫入事件紀錄")
    parser.add_argument("--backtest", action="store_true", help="時間分割歷史驗證")
    parser.add_argument("--start", default="2016-04-07")
    parser.add_argument("--end", default="9999-12-31")
    parser.add_argument("--mode", default="TWSE", choices=("TWSE", "TWSE+TPEX"))
    parser.add_argument("--chip-mode", default="ablated", choices=("ablated", "real"))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--chunk-days", type=int, default=60)
    parser.add_argument("--check-every", type=int, default=25, help="每幾個交易日用原參數重跑核對推導一致")
    parser.add_argument("--out", default=str(ROOT / ".tmp/lane-F1"))
    args = parser.parse_args()
    if args.evaluate:
        if not args.as_of:
            parser.error("--evaluate 需要 --as-of")
        return _evaluate_cli(args)
    if args.backtest:
        report = run_backtest(start=args.start, end=args.end, mode=args.mode, chip_mode=args.chip_mode,
                              workers=args.workers, chunk_days=args.chunk_days, out_dir=Path(args.out),
                              check_every=args.check_every)
        print(json.dumps(report["meta"], ensure_ascii=True, sort_keys=True))
        return 0
    parser.error("請用 --evaluate 或 --backtest")
    return 2


if __name__ == "__main__":
    sys.exit(main())
