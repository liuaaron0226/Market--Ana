"""長期候選到期評估（票10核心）與 long-v1.2 有限歷史描述。

``evaluate_snapshot`` 讀一份已封存的長期快照（candidate_snapshots），配後續日 K（上市既有 SQLite、
上櫃 otc_history，皆 mode=ro）算 6／12 個日曆月檢查點：報酬、對該檔基準（0050 或櫃買指數）的超額報酬、
期間最大回撤／有利／不利幅度，以及失效條件是否在檢查點前已成立（基本面只經 ``fundamentals.as_of``
依當時可得資料判斷）。評估另存獨立紀錄並以 SHA-256 封存，不覆寫原快照；未到期標 pending，
不拿今天資料冒充到期結果。

價格口徑：個股與 0050 都用未還原收盤，現金股利不還原（兩邊同為價格報酬、不含息）；只有分割、
減資、面額變更與配股等股本變動以參考價橋接，並標「有條件可比」。無法橋接的缺口標不可評估並保留，
不自動剔除不利案例。不輸出命中率或勝率。

``--history`` 用當時可得資料在可行月底重跑 long-v1.2（另跑關閉估值降級的對照），評估到期者並和基準、
同母體隨機等權組合比較；結果只寫 ``.tmp/lane-F2/``。這是樣本極少的描述，不是有效性驗證。
"""
from __future__ import annotations

import argparse
import calendar
import copy
import hashlib
import json
import os
import random
import sqlite3
import statistics
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import candidate_snapshots

SCHEMA_VERSION = "long-evaluation-v1"
EVAL_KIND = "long-eval"
EVAL_VERSION = "long-eval-v1"
PRICE_BASIS = ("未還原收盤；現金股利不還原（個股與 0050 同為價格報酬、不含息；櫃買指數為價格指數）；"
               "分割、減資、面額變更、配股等股本變動以參考價橋接")
# 參考價（收盤－漲跌）與前一有效收盤差距在 0.5% 以內視為沒有公司行動：容忍停牌隔日漲跌基準的小誤差。
REFERENCE_TOLERANCE_PCT = 0.5
# 參考價／前收偏離 30% 以上且無事件表佐證時，視為股本變動（分割、減資、大額配股）並橋接。
CAPITAL_CHANGE_RATIO = 1.3
# 「X 不比價」且查無事件、收盤又較前收跳動超過 10% 時無從橋接，該日之後的檢查點不可評估。
X_UNBRIDGEABLE_PCT = 10.0
ADJUSTING_KINDS = {"stock_dividend", "stock_and_cash_dividend", "capital_reduction", "par_value_change", "capital_change_detected"}
EVENT_KINDS = {"除息": "cash_dividend", "除權": "stock_dividend", "除權息": "stock_and_cash_dividend",
               "減資": "capital_reduction", "變更面額": "par_value_change"}

# 景氣循環分類（本人 11-08 指出長期候選偏向週期股）：官方產業別能直接對應的用產業別；
# 面板、記憶體與台塑化沒有獨立官方產業別，改用列名代號清單。分類只供描述統計，不影響篩選。
CYCLICAL_INDUSTRIES = {"航運業": "航運", "鋼鐵工業": "鋼鐵", "塑膠工業": "塑化", "造紙工業": "紙業", "水泥工業": "水泥"}
CYCLICAL_CODES = {
    **{code: "面板" for code in ("2409", "3481", "6116")},
    **{code: "記憶體" for code in ("2408", "2344", "2337", "3260", "4967", "8271", "3006", "5351", "8299")},
    "6505": "塑化",
}
CYCLE_BASIS = ("官方產業別：航運業、鋼鐵工業、塑膠工業、造紙工業、水泥工業；面板（2409、3481、6116）、"
               "記憶體（2408、2344、2337、3260、4967、8271、3006、5351、8299）與台塑化 6505 用列名代號。"
               "化學工業、橡膠、玻璃陶瓷等未列入，因本人列舉範圍未含。")


class EvaluationConflict(Exception):
    """同一快照、同一評估日已有內容不同的評估紀錄；不覆寫舊紀錄。"""


class EvaluationIntegrityError(Exception):
    """評估紀錄封存雜湊不符。"""


# ---------- 封存與留存 ----------

def _canonical(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _seal_hash(record: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical({key: value for key, value in record.items() if key != "evaluation_sha256"})).hexdigest()


def seal(record: Mapping[str, Any]) -> dict[str, Any]:
    sealed = copy.deepcopy(dict(record))
    sealed["evaluation_sha256"] = _seal_hash(sealed)
    return sealed


def _verify(record: Mapping[str, Any]) -> None:
    if record.get("evaluation_sha256") != _seal_hash(record):
        raise EvaluationIntegrityError("評估紀錄封存雜湊不符：內容被改動或未經 seal 封存")


def _eval_path(root: Path, publish_date: str, eval_as_of: str) -> Path:
    date.fromisoformat(publish_date)
    date.fromisoformat(eval_as_of)
    return root / EVAL_KIND / publish_date / f"{eval_as_of}.json"


def write_evaluation(root: Path, record: Mapping[str, Any]) -> dict[str, Any]:
    """原子寫入獨立評估紀錄；同內容重試回 unchanged，不同內容丟 EvaluationConflict。原快照不動。"""
    _verify(record)
    target = _eval_path(root, record["source_publish_date"], record["eval_as_of"])
    if target.exists():
        if read_evaluation(root, record["source_publish_date"], record["eval_as_of"])["evaluation_sha256"] == record["evaluation_sha256"]:
            return {"outcome": "unchanged", "path": target}
        raise EvaluationConflict(f"{target} 已有不同內容的評估；不覆寫")
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f".{target.name}.{os.getpid()}.partial")
    partial.write_bytes(json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n")
    try:
        os.link(partial, target)
    except FileExistsError:
        if read_evaluation(root, record["source_publish_date"], record["eval_as_of"])["evaluation_sha256"] != record["evaluation_sha256"]:
            raise EvaluationConflict(f"{target} 已被另一次評估寫入不同內容") from None
        return {"outcome": "unchanged", "path": target}
    finally:
        partial.unlink(missing_ok=True)
    return {"outcome": "written", "path": target}


def read_evaluation(root: Path, publish_date: str, eval_as_of: str) -> dict[str, Any]:
    record = json.loads(_eval_path(root, publish_date, eval_as_of).read_text(encoding="utf-8"))
    _verify(record)
    return record


# ---------- 價格與公司行動 ----------

def _valid(row: Mapping[str, Any]) -> bool:
    return row.get("close") is not None and not row.get("suspended")


def _event_kind(event: Mapping[str, Any] | None, ratio: float) -> str:
    if event is not None:
        return EVENT_KINDS.get(str(event.get("type")), "unclassified")
    if ratio > CAPITAL_CHANGE_RATIO or ratio < 1 / CAPITAL_CHANGE_RATIO:
        return "capital_change_detected"
    return "unclassified"


def adjust_series(rows: Sequence[Mapping[str, Any]], events: Mapping[str, Mapping[str, Any]]) -> tuple[list[dict], list[dict]]:
    """以第一列為基準回傳 (調整後有效收盤列, 公司行動紀錄)。

    參考價優先用「收盤－漲跌」，X 不比價日改用事件表參考價。只有股本變動類事件累乘橋接倍數；
    現金股利只記錄不還原。指數列沒有漲跌欄位，不做任何調整。
    """
    out: list[dict] = []
    actions: list[dict] = []
    factor, prev = 1.0, None
    for row in rows:
        if not _valid(row):
            continue
        day, close = str(row["date"]), float(row["close"])
        if prev is not None:
            event = events.get(day)
            sign, change = row.get("change_sign"), row.get("change")
            reference = None
            if sign in ("+", "-", "") and change is not None:
                reference = round(close - float(change), 4)
            elif event is not None and event.get("reference") is not None:
                reference = float(event["reference"])
            if reference is None:
                if sign == "X":
                    jump = round((close / prev - 1) * 100, 4)
                    kind = "unbridgeable" if abs(jump) > X_UNBRIDGEABLE_PCT else "unclassified"
                    actions.append({"date": day, "kind": kind, "prev_close": prev, "close": close, "jump_pct": jump,
                                    "message": "X 不比價且事件表查無公司行動" + ("，跳動過大無法橋接" if kind == "unbridgeable" else "，未調整")})
            elif reference > 0 and abs(reference / prev - 1) * 100 > REFERENCE_TOLERANCE_PCT:
                ratio = prev / reference
                kind = _event_kind(event, ratio)
                if kind in ADJUSTING_KINDS:
                    factor *= ratio
                actions.append({"date": day, "kind": kind, "prev_close": prev, "reference": reference,
                                "ratio": round(ratio, 6), "adjusted": kind in ADJUSTING_KINDS,
                                "event_type": None if event is None else event.get("type"),
                                "source": "corporate_actions/events.json" if event is not None else "reference_price_gap"})
        out.append({"date": day, "close": close, "adj_close": round(close * factor, 6)})
        prev = close
    return out, actions


def _comparability(actions: Sequence[Mapping[str, Any]]) -> str:
    kinds = {action["kind"] for action in actions}
    if "unbridgeable" in kinds:
        return "not_comparable"
    if kinds & (ADJUSTING_KINDS | {"unclassified"}):
        return "caveat"
    return "comparable"


# ---------- 論點追蹤 ----------

def _period_end(period: str, source: str) -> str:
    if source == "monthly_revenue":
        year, month = map(int, period.split("-"))
        return date(year, month, calendar.monthrange(year, month)[1]).isoformat()
    year, quarter = int(period[:4]), int(period[-1])
    month = quarter * 3
    return date(year, month, calendar.monthrange(year, month)[1]).isoformat()


def invalidation_order(fundamental: Mapping[str, Any] | None, price_date: str | None) -> str:
    """只有日 K 與法定截止日時，基本面失效與跌破防守的先後。

    價格在資料期間結束前就跌破＝價格先；期間結束後、法定截止日前跌破＝公司可能提前公告，先後未知；
    同日未知；截止日之後才跌破＝基本面先。
    """
    if fundamental is None:
        return "price_only" if price_date else "none"
    if price_date is None:
        return "fundamentals_only"
    if price_date <= _period_end(fundamental["period"], fundamental["source"]):
        return "price_first"
    if price_date < fundamental["date"]:
        return "unknown_announcement_may_precede_price"
    if price_date == fundamental["date"]:
        return "same_day_unknown"
    return "fundamentals_first"


def _first_available(rows: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    """每個期間取最早可得的那筆（首次公告值），事件時點以首次可得日為準。"""
    found: dict[str, Mapping[str, Any]] = {}
    for row in sorted(rows, key=lambda item: (item.get("period", ""), item.get("available_on", ""))):
        found.setdefault(str(row["period"]), row)
    return found


def _shift_month(period: str, months: int) -> str:
    year, month = map(int, period.split("-"))
    index = year * 12 + month - 1 + months
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def thesis_events(code: str, fundamentals: Any, anchor: str, eval_as_of: str) -> list[dict]:
    """發布日之後、評估日以前新可得的月營收與季報，依原失效條件標強化／轉弱／失效；不改快照。"""
    import long_term_screen as lts

    visible = fundamentals.as_of(code, eval_as_of)
    events: list[dict] = []
    monthly = _first_available(visible["monthly"])
    streak = 0
    for period in sorted(monthly):
        current, base = monthly[period].get("revenue_thousand_twd"), monthly.get(_shift_month(period, -12), {}).get("revenue_thousand_twd")
        if current is None or base is None or base <= 0:
            streak = 0
            continue
        yoy = round((current / base - 1) * 100, 4)
        streak = streak + 1 if yoy < 0 else 0
        row = monthly[period]
        if row.get("available_on", "") <= anchor:
            continue
        status = "invalidated" if streak >= 2 else ("weakened" if yoy < 0 else "reaffirmed")
        reason = {"invalidated": f"月營收年增連續 {streak} 個月為負（失效條件：連續兩個月）",
                  "weakened": "單月營收年增轉負", "reaffirmed": "單月營收年增為正"}[status]
        events.append({"date": row["available_on"], "available_on_basis": row.get("available_on_basis"), "period": period,
                       "source": "monthly_revenue", "status": status, "monthly_yoy_pct": yoy, "reason": reason})

    quarterly_rows = visible["quarterly"]
    first_quarter = _first_available(quarterly_rows)
    for period in sorted(first_quarter, key=lambda item: (int(item[:4]), int(item[-1]))):
        row = first_quarter[period]
        if row.get("available_on", "") <= anchor:
            continue
        cutoff = row["available_on"]
        known = {key: value for key, value in _first_available([item for item in quarterly_rows if item.get("available_on", "") <= cutoff]).items()}
        base = {"date": cutoff, "available_on_basis": row.get("available_on_basis"), "period": period, "source": "quarterly"}
        if row.get("statement_form") not in (None, "general"):
            events.append({**base, "status": "not_applicable", "reason": "產業別財報格式，一般業失效條件不適用"})
            continue
        profit, missing = lts._profit_metrics(known, period)
        if profit is None:
            events.append({**base, "status": "unverifiable", "reason": "近四季換算所需季報不完整，無法判斷", "details": missing})
            continue
        failures = []
        if profit["ytd_net_income_yoy_pct"] is None or profit["ytd_net_income_yoy_pct"] < lts.PARAMETERS["min_ytd_net_income_yoy_pct"]:
            failures.append("累計淨利年增低於 10% 或去年同期非正")
        if profit["ttm_eps_twd"] <= 0 or profit["ttm_net_income_thousand_twd"] <= 0:
            failures.append("近四季 EPS／淨利不為正")
        if profit["ttm_net_income_thousand_twd"] < profit["prev_ttm_net_income_thousand_twd"]:
            failures.append("近四季淨利轉為下滑")
        ratio = profit["ocf_to_net_income_ttm"]
        if ratio is not None and ratio < lts.PARAMETERS["min_ocf_to_net_income_ttm"]:
            failures.append("近四季營業現金流／淨利低於 0.8")
        if profit["debt_ratio_pct"] is not None and profit["debt_ratio_pct"] > lts.PARAMETERS["max_debt_ratio_pct"]:
            failures.append("負債比高於 60%")
        events.append({**base, "status": "invalidated" if failures else "reaffirmed",
                       "reason": "；".join(failures) if failures else "新季報仍符合原獲利、現金流與負債條件",
                       "ytd_net_income_yoy_pct": profit["ytd_net_income_yoy_pct"], "ttm_eps_twd": profit["ttm_eps_twd"]})
    return sorted(events, key=lambda item: (item["date"], item["source"], item["period"]))


# ---------- 單一候選評估 ----------

def _max_drawdown(values: Sequence[float]) -> float:
    peak, worst = values[0], 0.0
    for value in values:
        peak = max(peak, value)
        worst = min(worst, value / peak - 1)
    return round(worst * 100, 4)


def _pct(end: float, start: float) -> float:
    return round((end / start - 1) * 100, 4)


def _checkpoint(months: int, calendar_date: str, *, sec: list[dict], bench: list[dict], trading_days: Sequence[str],
                eval_as_of: str, last_date: str, baseline: Mapping[str, Any], bench_meta: Mapping[str, Any],
                actions: Sequence[Mapping[str, Any]], blocking: str | None, thesis: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "months": months, "calendar_date": calendar_date, "status": "pending", "reason_code": None, "message": None,
        "comparison_date": None, "non_trading_calendar_date": None, "security_close_delayed": None,
        "close": None, "adjusted_close": None, "return_pct": None, "benchmark": dict(bench_meta),
        "benchmark_return_pct": None, "excess_return_pct": None, "max_drawdown_pct": None,
        "max_favorable_pct": None, "max_adverse_pct": None, "thesis_status": None, "thesis_event_dates": [],
    }
    if blocking:
        return {**out, "status": "not_evaluable", "reason_code": blocking, "message": "固定基準與行情對不上，無法形成可比數據"}
    if calendar_date > eval_as_of or last_date < calendar_date:
        return {**out, "message": "追蹤中：尚未到期，不以今天資料冒充到期結果"}
    after = [day for day in trading_days if calendar_date <= day <= eval_as_of]
    if not after:
        return {**out, "message": "追蹤中：到期日之後尚無交易日資料"}
    sec_close = {row["date"]: row for row in sec}
    bench_close = {row["date"]: row for row in bench}
    comparison = next((day for day in after if day in sec_close and day in bench_close), None)
    out["non_trading_calendar_date"] = calendar_date not in trading_days
    if comparison is None:
        code = "no_security_close_after_checkpoint" if not any(day in sec_close for day in after) else "no_benchmark_close_after_checkpoint"
        return {**out, "status": "not_evaluable", "reason_code": code,
                "message": "到期日之後沒有可比收盤（停牌、下市或轉市場），保留案例不剔除"}
    if any(action["kind"] == "unbridgeable" and action["date"] <= comparison for action in actions):
        return {**out, "status": "not_evaluable", "reason_code": "unbridgeable_corporate_action", "comparison_date": comparison,
                "message": "期間有無法橋接的公司行動缺口，不形成報酬數據"}
    window = [row["adj_close"] for row in sec if row["date"] <= comparison]
    bench_start = bench[0]["adj_close"]
    end_row = sec_close[comparison]
    ret = _pct(end_row["adj_close"], float(baseline["close"]))
    bench_ret = _pct(bench_close[comparison]["adj_close"], bench_start)
    dates = sorted({event["date"] for event in thesis["events"] if event["date"] <= comparison}
                   | ({thesis["price_breach"]["date"]} if thesis["price_breach"] and thesis["price_breach"]["date"] <= comparison else set()))
    statuses = {event["status"] for event in thesis["events"] if event["date"] <= comparison}
    breached = bool(thesis["price_breach"] and thesis["price_breach"]["date"] <= comparison)
    thesis_status = ("invalidated" if breached or "invalidated" in statuses else
                     "weakened" if "weakened" in statuses else "intact" if statuses else "no_new_information")
    return {
        **out, "status": "evaluated", "comparison_date": comparison, "security_close_delayed": comparison != after[0],
        "close": end_row["close"], "adjusted_close": end_row["adj_close"], "return_pct": ret,
        "benchmark": {**bench_meta, "close_end": bench_close[comparison]["close"]},
        "benchmark_return_pct": bench_ret, "excess_return_pct": round(ret - bench_ret, 4),
        "max_drawdown_pct": _max_drawdown(window), "max_favorable_pct": _pct(max(window), float(baseline["close"])),
        "max_adverse_pct": _pct(min(window), float(baseline["close"])),
        "thesis_status": thesis_status, "thesis_event_dates": dates,
    }


def _bench_market(code: str) -> str:
    return "TPEx" if code == "TPEX_INDEX" else "TWSE"


def evaluate_candidate(card: Mapping[str, Any], provider: Any, fundamentals: Any, *, eval_as_of: str) -> dict[str, Any]:
    code, market = str(card["code"]), card.get("market")
    plan = card["evaluation_plan"]
    baseline = card["baseline"]
    start = str(baseline["date"])
    sec, actions = adjust_series(provider.security_rows(code, market, start, eval_as_of), provider.corporate_events(code, market))
    bench_plan = plan["benchmark"]
    bench_code = str(bench_plan["code"])
    bench, bench_actions = adjust_series(provider.benchmark_rows(bench_code, start, eval_as_of),
                                         provider.corporate_events(bench_code, _bench_market(bench_code)))
    blocking = None
    if not sec or sec[0]["date"] != start or abs(sec[0]["close"] - float(baseline["close"])) > 1e-6:
        blocking = "baseline_mismatch"
    elif (not bench or bench[0]["date"] != bench_plan.get("date") or bench_plan.get("close") is None
          or abs(bench[0]["close"] - float(bench_plan["close"])) > 1e-6):
        blocking = "benchmark_baseline_mismatch"
    events = thesis_events(code, fundamentals, start, eval_as_of)
    defense = (card.get("defense") or {}).get("level")
    breach_row = next((row for row in sec[1:] if defense is not None and row["adj_close"] < float(defense)), None)
    breach = None if breach_row is None else {"date": breach_row["date"], "close": breach_row["close"],
                                               "adjusted_close": breach_row["adj_close"], "defense": defense,
                                               "source": "price", "status": "invalidated", "reason": f"日收盤跌破防守位 {defense}"}
    first_invalid = next((event for event in events if event["status"] == "invalidated"), None)
    thesis = {"events": events, "first_fundamental_invalidation": first_invalid, "price_breach": breach,
              "order": invalidation_order(first_invalid, breach["date"] if breach else None),
              "order_note": "只有日 K 與法定截止日；公司可能在截止日前公告，期間結束後至截止日前的先後標未知"}
    trading_days = provider.trading_days(market, start, eval_as_of)
    last_date = provider.last_date(market)
    bench_meta = {"code": bench_code, "name": bench_plan.get("name"), "date_start": bench_plan.get("date"),
                  "close_start": bench_plan.get("close"), "price_basis": bench_plan.get("price_basis"),
                  "limitation": bench_plan.get("limitation"), "corporate_actions": bench_actions}
    checkpoints = [
        _checkpoint(int(item["months"]), str(item["calendar_date"]), sec=sec, bench=bench, trading_days=trading_days,
                    eval_as_of=eval_as_of, last_date=last_date, baseline=baseline, bench_meta=bench_meta,
                    actions=actions, blocking=blocking, thesis=thesis)
        for item in plan["checkpoints"]
    ]
    return {
        "code": code, "name": card.get("name"), "market": market, "industry": card.get("industry"),
        "decision_at_publish": card.get("decision"), "baseline": dict(baseline),
        "defense": card.get("defense"), "comparability": _comparability(actions), "corporate_actions": actions,
        "thesis": thesis, "checkpoints": checkpoints,
    }


def evaluate_snapshot(snapshot: Mapping[str, Any], provider: Any, fundamentals: Any, *, eval_as_of: str) -> dict[str, Any]:
    """對一份長期快照產出封存的評估紀錄；快照本身只讀。"""
    if snapshot.get("snapshot_sha256") != candidate_snapshots.seal_hash(snapshot):
        raise candidate_snapshots.SnapshotIntegrityError("快照封存雜湊不符，拒絕評估")
    if snapshot.get("kind") != "long":
        raise ValueError("只評估長期快照")
    date.fromisoformat(eval_as_of)
    record = {
        "schema_version": SCHEMA_VERSION, "kind": EVAL_KIND, "eval_version": EVAL_VERSION,
        "batch_id": snapshot["batch_id"], "source_snapshot_sha256": snapshot["snapshot_sha256"],
        "source_publish_date": snapshot["publish_date"], "data_date": snapshot["data_date"],
        "method_version": snapshot["method_version"], "status": snapshot["status"], "eval_as_of": eval_as_of,
        "price_basis": PRICE_BASIS,
        "candidates": [evaluate_candidate(card, provider, fundamentals, eval_as_of=eval_as_of) for card in snapshot.get("candidates", [])],
        "notes": [
            "預測觀察（以發布日收盤為固定基準）不等於可成交損益；「等待」狀態的候選同樣以基準價追蹤，不改虛擬帳本。",
            "未到期只顯示追蹤中；不輸出命中率或勝率。",
            "同股多批入選的窗口重疊，彙總時不可當獨立樣本（見 overlap_summary）。",
        ],
    }
    return seal(record)


def overlap_summary(records: Sequence[Mapping[str, Any]], *, months: int) -> dict[str, Any]:
    """同股多批入選時，以不重疊窗口數估獨立樣本，避免重疊窗口誇大樣本數。"""
    windows: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for record in records:
        for item in record.get("candidates", []):
            for checkpoint in item.get("checkpoints", []):
                if checkpoint.get("months") == months:
                    windows[str(item["code"])].append((str(item["baseline"]["date"]), str(checkpoint["calendar_date"])))
    independent = 0
    for spans in windows.values():
        last_end = ""
        for begin, end in sorted(spans):
            if begin >= last_end:
                independent += 1
                last_end = end
    return {"months": months, "samples": sum(len(spans) for spans in windows.values()), "independent_windows": independent,
            "codes": len(windows)}


# ---------- 描述統計工具 ----------

def classify_cycle(code: str, industry: str | None) -> dict[str, Any]:
    if code in CYCLICAL_CODES:
        return {"cyclical": True, "group": CYCLICAL_CODES[code], "basis": "curated_code_list"}
    if industry in CYCLICAL_INDUSTRIES:
        return {"cyclical": True, "group": CYCLICAL_INDUSTRIES[industry], "basis": "official_industry"}
    return {"cyclical": False, "group": None, "basis": "official_industry"}


def operating_vs_net(quarterly_rows: Sequence[Mapping[str, Any]], period: str) -> dict[str, Any]:
    """比較年初至今稅後淨利與營業利益的年增金額，看淨利成長是否主要來自業外。

    營業利益為稅前、淨利為稅後，兩者增額只能近似比較：淨利增額大於營業利益增額的兩倍，
    或營業利益不增反減，即標「業外主導」。
    """
    rows = {str(row["period"]): row for row in quarterly_rows}
    prior_period = f"{int(period[:4]) - 1}-Q{period[-1]}"
    current, prior = rows.get(period), rows.get(prior_period)
    fields = ("operating_income_thousand_twd", "net_income_thousand_twd")
    base = {"period": period, "prior_period": prior_period, "delta_net_income_thousand_twd": None,
            "delta_operating_income_thousand_twd": None, "non_operating_dominant": None}
    if current is None or prior is None or any(current.get(f) is None or prior.get(f) is None for f in fields):
        return {**base, "reason": "缺本季或去年同期的營業利益／淨利"}
    delta_ni = current["net_income_thousand_twd"] - prior["net_income_thousand_twd"]
    delta_op = current["operating_income_thousand_twd"] - prior["operating_income_thousand_twd"]
    return {
        **base,
        "net_income_thousand_twd": current["net_income_thousand_twd"],
        "operating_income_thousand_twd": current["operating_income_thousand_twd"],
        "delta_net_income_thousand_twd": delta_ni, "delta_operating_income_thousand_twd": delta_op,
        "non_operating_dominant": bool(delta_ni > 0 and delta_op < 0.5 * delta_ni),
        "net_minus_operating_thousand_twd": current["net_income_thousand_twd"] - current["operating_income_thousand_twd"],
    }


# ---------- 真實資料供應（全部 mode=ro） ----------

class SqliteProvider:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.dbs = {"TWSE": root / "data/stock-history/stock-history.sqlite", "TPEx": root / "data/otc-history/otc_history.sqlite"}
        self.market_db = root / "data/otc-history/otc_market.sqlite"
        self._days: dict[str, list[str]] = {}
        for market, db in self.dbs.items():
            with self._connect(db) as connection:
                self._days[market] = [row[0] for row in connection.execute("SELECT date FROM source_days ORDER BY date")]
        events: dict[str, dict[str, dict]] = defaultdict(dict)
        path = root / "data/corporate-actions/events.json"
        for event in json.loads(path.read_text(encoding="utf-8")):
            events[str(event["code"])][str(event["date"])] = event
        self._events = dict(events)

    @staticmethod
    def _connect(db: Path) -> sqlite3.Connection:
        return sqlite3.connect(f"file:{db.resolve().as_posix()}?mode=ro", uri=True)

    def security_rows(self, code: str, market: str, start: str, end: str) -> list[dict]:
        with self._connect(self.dbs[market]) as connection:
            rows = connection.execute(
                "SELECT date, close, change_sign, change, suspended FROM daily WHERE code = ? AND date BETWEEN ? AND ? ORDER BY date",
                (code, start, end)).fetchall()
        return [{"date": d, "close": c, "change_sign": s, "change": ch, "suspended": None if su is None else bool(su)} for d, c, s, ch, su in rows]

    def bulk_rows(self, market: str, start: str, end: str) -> dict[str, list[dict]]:
        grouped: dict[str, list[dict]] = defaultdict(list)
        with self._connect(self.dbs[market]) as connection:
            for code, d, c, s, ch, su in connection.execute(
                    "SELECT code, date, close, change_sign, change, suspended FROM daily WHERE date BETWEEN ? AND ? ORDER BY code, date",
                    (start, end)):
                grouped[code].append({"date": d, "close": c, "change_sign": s, "change": ch, "suspended": None if su is None else bool(su)})
        return grouped

    def benchmark_rows(self, code: str, start: str, end: str) -> list[dict]:
        if code == "TPEX_INDEX":
            with self._connect(self.market_db) as connection:
                return [{"date": d, "close": c} for d, c in connection.execute(
                    "SELECT date, close FROM tpex_index WHERE date BETWEEN ? AND ? ORDER BY date", (start, end))]
        return self.security_rows(code, "TWSE", start, end)

    def trading_days(self, market: str, start: str, end: str) -> list[str]:
        return [day for day in self._days[market] if start <= day <= end]

    def last_date(self, market: str) -> str:
        return self._days[market][-1]

    def corporate_events(self, code: str, market: str) -> dict[str, dict]:
        # 事件表是證交所 TWT49U／TWTAUU／TWTB8U，只涵蓋上市；上櫃改由參考價差偵測。
        return self._events.get(code, {}) if market == "TWSE" else {}


# ---------- 有限歷史描述 ----------

def _month_end_dates(days: Sequence[str], start_month: str, end_day: str) -> list[str]:
    last: dict[str, str] = {}
    for day in days:
        if day[:7] >= start_month and day <= end_day:
            last[day[:7]] = day
    return [last[month] for month in sorted(last)]


def _universe(root: Path) -> list[dict]:
    from listed_catalog import load_listed_catalog
    from otc_catalog import load_otc_catalog

    listed_html = root / "data/listed-catalog/raw/twse-isin-listed-20261108.html"
    otc_html = root / "data/otc-catalog/raw/tpex-isin-otc-20261108.html"
    listed = load_listed_catalog(listed_html, listed_html.with_suffix(".meta.json"), root / "data/stock-history/stock-history.sqlite")
    otc = load_otc_catalog(otc_html, otc_html.with_suffix(".meta.json"), root / "data/otc-history/otc_history.sqlite")
    out = []
    # 與 long_term_screen.load_real_input 相同：名錄沒有公開列舉 API，沿用已驗 SHA／CFI 後的內部條目。
    for market, catalog in (("TWSE", listed), ("TPEx", otc)):
        for code, entry in sorted(getattr(catalog, "_entries").items()):
            out.append({"code": code, "name": entry[0], "market": market, "industry": entry[4]})
    return out


def _bars_upto(db: Path, day: str, limit: int) -> dict[str, list[dict]]:
    with SqliteProvider._connect(db) as connection:
        rows = connection.execute(
            """SELECT date, code, open, high, low, close, volume_shares, turnover, suspended FROM daily
               WHERE date IN (SELECT date FROM source_days WHERE date <= ? ORDER BY date DESC LIMIT ?) ORDER BY code, date""",
            (day, limit)).fetchall()
    grouped: dict[str, list[dict]] = defaultdict(list)
    for d, code, o, h, lo, c, v, t, s in rows:
        grouped[code].append({"date": d, "open": o, "high": h, "low": lo, "close": c, "volume": v, "turnover": t,
                              "suspended": None if s is None else bool(s)})
    return grouped


def _first_reason(row: Mapping[str, Any]) -> dict | None:
    return row["reasons"][0] if row.get("reasons") else None


def _distribution(codes: Sequence[str], info: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    industries = Counter(info[code]["industry"] for code in codes)
    groups = Counter(classify_cycle(code, info[code]["industry"])["group"] or "非週期" for code in codes)
    cyclical = sum(classify_cycle(code, info[code]["industry"])["cyclical"] for code in codes)
    return {"n": len(codes), "cyclical": cyclical, "cyclical_share_pct": round(cyclical / len(codes) * 100, 2) if codes else None,
            "cycle_groups": dict(groups.most_common()), "industries": dict(industries.most_common())}


def _forward_return(rows: list[dict], events: Mapping[str, Any], start: str, calendar_date: str,
                    trading_days: Sequence[str], eval_as_of: str) -> float | None:
    sec, actions = adjust_series([row for row in rows if start <= row["date"] <= eval_as_of], events)
    if not sec or sec[0]["date"] != start:
        return None
    after = [day for day in trading_days if calendar_date <= day <= eval_as_of]
    closes = {row["date"]: row["adj_close"] for row in sec}
    comparison = next((day for day in after if day in closes), None)
    if comparison is None or any(a["kind"] == "unbridgeable" and a["date"] <= comparison for a in actions):
        return None
    return _pct(closes[comparison], sec[0]["close"])


def _mean(values: Sequence[float]) -> float | None:
    return round(statistics.fmean(values), 4) if values else None


def run_history(root: Path, out_dir: Path, *, eval_as_of: str, seed: int = 20261108, draws: int = 2000,
                log: Any = print) -> dict[str, Any]:
    import long_term_screen as lts
    from fundamentals_history import load_history
    from otc_market_data import index_range

    started = time.time()
    provider = SqliteProvider(root)
    universe_all = _universe(root)
    info = {item["code"]: item for item in universe_all}
    history = load_history(root / "data/fundamentals-history")
    data = history.to_dict()
    fundamentals = lts.IndexedFundamentals(history)
    valuation_db = root / "data/valuation-history/valuation.sqlite"
    valuation = lts.ValuationIndex(valuation_db, universe_all)
    dates = _month_end_dates(provider._days["TWSE"], "2025-06", eval_as_of)
    if eval_as_of not in dates and eval_as_of in provider._days["TWSE"]:
        dates.append(eval_as_of)
    rng = random.Random(seed)
    snap_roots = {"v1.2": out_dir / "history/snapshots-v12", "valuation_off": out_dir / "history/snapshots-valoff"}
    eval_roots = {"v1.2": out_dir / "history/evals-v12", "valuation_off": out_dir / "history/evals-valoff"}
    rows_out = []
    records: dict[str, list[dict]] = {"v1.2": [], "valuation_off": []}
    for day in dates:
        otc_days_before = len([d for d in provider._days["TPEx"] if d <= day])
        scope = ("TWSE", "TPEx") if otc_days_before >= lts.PARAMETERS["min_price_bars"] else ("TWSE",)
        universe = [item for item in universe_all if item["market"] in scope]
        bars = {**{code: rows for code, rows in _bars_upto(provider.dbs["TWSE"], day, 130).items()}}
        if "TPEx" in scope:
            otc_bars = _bars_upto(provider.dbs["TPEx"], day, 130)
        else:
            otc_bars = {}
        bars_by_code = {item["code"]: (bars if item["market"] == "TWSE" else otc_bars).get(item["code"], []) for item in universe}
        latest_month = max((r["period"] for r in data["monthly"] if r["available_on"] <= day), default=None)
        latest_quarter = max((r["period"] for r in data["quarterly"] if r["available_on"] <= day),
                             key=lambda p: (int(p[:4]), int(p[-1])), default=None)
        price_ok = day in provider._days["TWSE"] and ("TPEx" not in scope or day in provider._days["TPEx"])
        source_states = {"catalog": {"state": "ok"},
                         "fundamentals": {"state": "ok", "latest_monthly_period": latest_month, "latest_quarter_period": latest_quarter},
                         "price": {"state": "ok" if price_ok else "stale", "as_of": day if price_ok else None}}
        entry: dict[str, Any] = {"date": day, "scope": list(scope), "otc_trading_days_before": otc_days_before, "variants": {}}
        bench_row = next((r for r in provider.security_rows("0050", "TWSE", day, day) if _valid(r)), None)
        tpex_rows = index_range(provider.market_db, day, day)
        benchmark = {"date": day, "close": float(bench_row["close"])} if bench_row else None
        tpex_benchmark = {"date": day, "close": float(tpex_rows[-1]["close"])} if tpex_rows else None
        for variant, val in (("v1.2", valuation), ("valuation_off", None)):
            result = lts.screen_long_term(data_date=day, universe=universe, fundamentals=fundamentals, bars_by_code=bars_by_code,
                                          source_states=source_states, valuation=val)
            recon = result["reconciliation"]
            selected = [card["code"] for card in result["candidates"]]
            qualified = [row["code"] for row in recon if row["outcome"] == "selected"
                         or ((_first_reason(row) or {}).get("code") == "rank_cutoff")]
            applicable = [row["code"] for row in recon if (_first_reason(row) or {}).get("category") != "not_applicable"]
            complete = [row["code"] for row in recon if (_first_reason(row) or {}).get("category") not in ("not_applicable", "missing_data")
                        and row["outcome"] != "not_evaluated"]
            summary: dict[str, Any] = {
                "status": result["status"], "errors": result.get("errors", [])[:3], "counts": result["counts"],
                "selected": [{"code": card["code"], "name": card["name"], "market": card["market"], "industry": card["industry"],
                              "cycle": classify_cycle(card["code"], card["industry"]), "decision": card["decision"],
                              "valuation_tier": card["valuation"].get("tier"),
                              "pe_pct": card["valuation"].get("pe_percentile_in_industry"),
                              "pb_pct": card["valuation"].get("pb_percentile_in_industry"),
                              "ytd_net_income_yoy_pct": card["metrics"]["ytd_net_income_yoy_pct"],
                              "op_vs_net": operating_vs_net(fundamentals.as_of(card["code"], day)["quarterly"],
                                                            card["data_availability"]["quarterly"]["period"])}
                             for card in result["candidates"]],
                "distribution": {"universe": _distribution([u["code"] for u in universe], info),
                                 "applicable": _distribution(applicable, info), "data_complete": _distribution(complete, info),
                                 "qualified": _distribution(qualified, info), "selected": _distribution(selected, info)},
                "qualified_op_vs_net": Counter(
                    str(operating_vs_net(fundamentals.as_of(code, day)["quarterly"], lts.expected_latest_periods(day)[1])["non_operating_dominant"])
                    for code in qualified),
            }
            summary["qualified_op_vs_net"] = dict(summary["qualified_op_vs_net"])
            if result["status"] in candidate_snapshots.STATUSES:
                planned_result = dict(result)
                if variant == "valuation_off":
                    planned_result["method_version"] = f'{result["method_version"]}[valuation_off]'
                planned = lts.attach_evaluation_plan(planned_result, published_at=f"{day}T08:00:00Z",
                                                     benchmark=benchmark, tpex_benchmark=tpex_benchmark)
                snap = candidate_snapshots.build_snapshot("long", planned, input_fingerprints={"history_run": f"{day}:{variant}"},
                                                          published_at=f"{day}T08:00:00Z")
                candidate_snapshots.write_snapshot(snap_roots[variant], snap)
                record = evaluate_snapshot(snap, provider, fundamentals, eval_as_of=eval_as_of)
                write_evaluation(eval_roots[variant], record)
                records[variant].append(record)
                summary["evaluation"] = [{"code": item["code"], "comparability": item["comparability"],
                                          "thesis_order": item["thesis"]["order"],
                                          "checkpoints": [{k: cp[k] for k in ("months", "status", "reason_code", "comparison_date", "return_pct",
                                                                              "benchmark_return_pct", "excess_return_pct", "max_drawdown_pct",
                                                                              "thesis_status")}
                                                          for cp in item["checkpoints"]]}
                                         for item in record["candidates"]]
                six = lts._add_calendar_months(date.fromisoformat(day), 6)
                if variant == "v1.2" and six <= eval_as_of and complete:
                    pool_returns: dict[str, float] = {}
                    for market in scope:
                        grouped = provider.bulk_rows(market, day, eval_as_of)
                        days = provider.trading_days(market, day, eval_as_of)
                        for code in complete:
                            if info[code]["market"] != market:
                                continue
                            value = _forward_return(grouped.get(code, []), provider.corporate_events(code, market), day, six, days, eval_as_of)
                            if value is not None:
                                pool_returns[code] = value
                    codes = sorted(pool_returns)
                    sample_means = sorted(statistics.fmean(pool_returns[c] for c in rng.sample(codes, min(5, len(codes))))
                                          for _ in range(draws)) if len(codes) >= 5 else []
                    strat = [cp["return_pct"] for item in record["candidates"] for cp in item["checkpoints"]
                             if cp["months"] == 6 and cp["status"] == "evaluated"]
                    strat_mean = _mean(strat)
                    cyc = [v for c, v in pool_returns.items() if classify_cycle(c, info[c]["industry"])["cyclical"]]
                    non = [v for c, v in pool_returns.items() if not classify_cycle(c, info[c]["industry"])["cyclical"]]
                    q_returns = [pool_returns[c] for c in qualified if c in pool_returns]
                    summary["six_month_comparison"] = {
                        "calendar_date": six, "pool_n": len(codes), "pool_equal_weight_mean_pct": _mean(list(pool_returns.values())),
                        "pool_cyclical_mean_pct": _mean(cyc), "pool_cyclical_n": len(cyc), "pool_non_cyclical_mean_pct": _mean(non),
                        "pool_non_cyclical_n": len(non), "qualified_mean_pct": _mean(q_returns), "qualified_n": len(q_returns),
                        "strategy_mean_pct": strat_mean, "strategy_n": len(strat),
                        "random5_draws": len(sample_means),
                        "random5_p10_p50_p90_pct": [round(sample_means[int(len(sample_means) * q)], 4) for q in (0.1, 0.5, 0.9)] if sample_means else None,
                        "strategy_percentile_in_random5": (round(sum(m < strat_mean for m in sample_means) / len(sample_means) * 100, 1)
                                                           if sample_means and strat_mean is not None else None),
                    }
            entry["variants"][variant] = summary
        rows_out.append(entry)
        log(f"[{time.time() - started:6.0f}s] {day} scope={'+'.join(scope)} "
            + " ".join(f"{v}:{s['status']}/{len(s['selected'])}" for v, s in entry["variants"].items()))
    output = {
        "eval_as_of": eval_as_of, "eval_version": EVAL_VERSION, "cycle_basis": CYCLE_BASIS, "dates": rows_out,
        "overlap": {variant: [overlap_summary(recs, months=m) for m in (6, 12)] for variant, recs in records.items()},
        "elapsed_seconds": round(time.time() - started, 1),
        "caveat": "樣本極少的描述，不是有效性驗證；門檻在看過近期資料後才訂，名錄為今日快照（存活者偏差）。",
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "history-result.json").write_text(json.dumps(output, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true", help="在可行月底重跑 long-v1.2 並評估（只寫 .tmp/lane-F2）")
    parser.add_argument("--evaluate", help="評估指定長期快照 JSON 路徑")
    parser.add_argument("--eval-as-of", required=True)
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent / ".tmp/lane-F2"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    if args.history:
        run_history(root, Path(args.out), eval_as_of=args.eval_as_of, log=lambda m: print(m, flush=True))
        return 0
    if not args.evaluate:
        parser.error("請用 --history 或 --evaluate <snapshot.json>")
    import long_term_screen as lts
    from fundamentals_history import load_history

    snap = json.loads(Path(args.evaluate).read_text(encoding="utf-8"))
    record = evaluate_snapshot(snap, SqliteProvider(root), lts.IndexedFundamentals(load_history(root / "data/fundamentals-history")),
                               eval_as_of=args.eval_as_of)
    written = write_evaluation(Path(args.out) / "evals", record)
    print(json.dumps({"outcome": written["outcome"], "path": str(written["path"]), "sha256": record["evaluation_sha256"]}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
