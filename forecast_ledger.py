"""預測帳本：加權指數的事前預測，寫下鎖定、到期對帳、彙總。

規格：``.scratch/forecast-ledger/spec.md``。對外只有：

- ``write_forecast``：給一份當天輸入與一個模型命令列程式，得到一筆鎖定的預測或一筆鎖定的漏記。
- ``reconcile``：給截至某一天的指數日 K，替到期的格子新增對帳紀錄。
- ``summarize``：把帳本整理成依版本、期間、方法與輸入版本分組的摘要。
- ``read_ledger``：讀出全部紀錄，順便核對封存雜湊（原件被改動時丟 ``LedgerIntegrityError``）。
- ``method_version_of``：從方法稿全文讀出版本號。

候選關卡、照歷史亂猜、固定規則、回答驗證、鎖定與到期日計算都封在這裡。帳本只能新增：
同一個識別（基準日＋版本＋方法版本＋輸入版本）只會有一筆；鎖定的做法沿用
``candidate_snapshots``（正規化 JSON 的 SHA-256、``os.link`` 不覆寫）。
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from ai_digest import CHINESE_NUMERAL_CHARS

SCHEMA = "forecast-ledger/v1"
TAIPEI = timezone(timedelta(hours=8))
HORIZONS = (1, 5, 20)
EDITIONS = ("evening", "morning")
ZONES = ("above", "between", "below")
REGIMES = ("多頭延續", "多頭中的回檔", "高檔轉弱", "空頭延續", "空頭中的反彈", "盤整")
PULLBACK_REGIME = "多頭中的回檔"
TREND_LABELS = ("偏多", "偏空", "盤整")
REQUIRED_INPUTS = ("index", "institutional", "futures")
# 晨間版須在開盤（09:00）前 15 分鐘鎖定。
MORNING_DEADLINE = (8, 45)

# 候選關卡與基準的參數：在第一筆紀錄之前寫定，整組算進輸入版本，改了就是新系列。
PARAMETERS: dict[str, Any] = {
    "layers": ["base"],
    "ma_periods": [5, 10, 20, 60, 120, 240],
    "high_low_windows": [20, 60, 240],
    "gap_lookback_bars": 240,
    "min_distance_pct": 0.3,
    "distance_percentiles": {"up": 80, "down": 20},
    "recent_bars_for_model": 60,
}

_MA_NAMES = {5: "五日線", 10: "十日線", 20: "月線", 60: "季線", 120: "半年線", 240: "年線"}
_WINDOW_NAMES = {20: "近二十日", 60: "近六十日", 240: "近一年"}
_HORIZON_NAMES = {1: "一日", 5: "五日", 20: "二十日"}

_CELL_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["strong_line", "weak_line", "zone", "reason", "reason_refs", "invalidation", "invalidation_refs"],
    "properties": {
        "strong_line": {"type": "string"}, "weak_line": {"type": "string"},
        "zone": {"type": "string", "enum": list(ZONES)},
        "reason": {"type": "string", "minLength": 1, "maxLength": 160},
        "reason_refs": {"type": "array", "maxItems": 3, "items": {"type": "string"}, "uniqueItems": True},
        "invalidation": {"type": "string", "minLength": 1, "maxLength": 160},
        "invalidation_refs": {"type": "array", "maxItems": 3, "items": {"type": "string"}, "uniqueItems": True},
    },
}
OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False, "required": ["base_date", "regime", "cells"],
    "properties": {
        "base_date": {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}$"},
        "regime": {"type": "string", "enum": list(REGIMES)},
        "cells": {
            "type": "object", "additionalProperties": False, "required": [str(h) for h in HORIZONS],
            "properties": {str(h): _CELL_SCHEMA for h in HORIZONS},
        },
    },
}


_PROMPT_RULES = (
    "你是台股大盤分析員。只根據 INPUT_JSON 與方法稿做判斷，不可連網、不可抓資料、不可使用工具。",
    "替加權指數寫一份預測：cells 的 1、5、20 分別是基準日之後第一、五、二十個交易日的收盤。",
    "每一格從 candidates 挑一個 side=above 的 id 當 strong_line（轉強線）、一個 side=below 的 id 當 weak_line（轉弱線）；"
    "帶 horizon 的候選只能用在同一格。zone 只能是 above（收盤大於等於轉強線）、between、below（收盤小於轉弱線）之一。",
    "reason（理由）與 invalidation（什麼情況代表看錯）只寫繁體中文質化判斷，不得出現任何阿拉伯／全形／中文數字、日期或價格；"
    "要引用價位就把候選 id 放進 reason_refs／invalidation_refs，程式會代入名字與數值。",
    f"regime 從固定清單選一個：{'、'.join(REGIMES)}。base_date 照抄 INPUT_JSON 的 base_date。",
)
# 輸入版本：參數、回答格式、提示規則任何一項改了就是新系列，和舊系列分開對帳。
INPUT_VERSION = "base-v1-" + hashlib.sha256(json.dumps(
    {"parameters": PARAMETERS, "output_schema": OUTPUT_SCHEMA, "prompt_rules": _PROMPT_RULES},
    ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:8]


class LedgerIntegrityError(Exception):
    """紀錄的封存雜湊不符：原件被改動，或不是由本模組寫入。"""


# ---- 共用小工具 ----

def _canonical(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha(payload: Any) -> str:
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _seal(record: dict[str, Any]) -> dict[str, Any]:
    record["record_sha256"] = _sha({k: v for k, v in record.items() if k != "record_sha256"})
    return record


def _verify(record: Mapping[str, Any], path: Path) -> None:
    body = {k: v for k, v in record.items() if k != "record_sha256"}
    if record.get("record_sha256") != _sha(body):
        raise LedgerIntegrityError(f"{path} 封存雜湊不符：原件被改動")
    if "input" in record and record.get("input_sha256") != _sha(record["input"]):
        raise LedgerIntegrityError(f"{path} 輸入雜湊不符：存檔的輸入被改動")


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _format_number(value: float) -> str:
    try:
        rendered = format(Decimal(str(value)), ",f")
    except InvalidOperation:
        return str(value)
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _against_existing(path: Path, record: Mapping[str, Any]) -> str:
    existing = json.loads(path.read_text(encoding="utf-8"))
    return "unchanged" if existing.get("record_sha256") == record["record_sha256"] else "conflict"


def _append(path: Path, record: Mapping[str, Any]) -> str:
    """只新增：檔案不存在才寫；已存在時同內容回 ``unchanged``、不同內容回 ``conflict``，都不覆寫。"""
    if path.exists():
        return _against_existing(path, record)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f".{path.name}.{os.getpid()}.partial")
    partial.write_bytes(json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n")
    try:
        # os.link 在目標已存在時失敗：兩個寫入者競爭時後者不會蓋掉前者。
        os.link(partial, path)
    except FileExistsError:
        return _against_existing(path, record)
    finally:
        partial.unlink(missing_ok=True)
    return "written"


def _read(path: Path) -> dict[str, Any]:
    record = json.loads(path.read_text(encoding="utf-8"))
    _verify(record, path)
    return record


# ---- 方法版本 ----

def method_version_of(method_text: str) -> str:
    """方法稿開頭「版本：X.Y」那一行的版本號；用的是開始記錄當時最新的方法稿。"""
    match = re.search(r"^版本：\s*([0-9]+(?:\.[0-9]+)*)", method_text or "", re.MULTILINE)
    if not match:
        raise ValueError("方法稿缺少「版本：」行，無法標示方法版本")
    return match.group(1)


# ---- 候選關卡 ----

def _clean_daily(index_daily: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for row in index_daily:
        values = {key: _number(row.get(key)) for key in ("open", "high", "low", "close")}
        rows.append({"date": str(row.get("date")), **values})
    dates = [row["date"] for row in rows]
    if dates != sorted(dates) or len(set(dates)) != len(dates):
        raise ValueError("index_daily 必須依日期遞增且日期不可重複")
    return rows


def _label_date(day: str, base_date: str) -> str:
    """同一年只寫月／日；和基準日不同年時連年份一起寫，免得一年前的關卡被看成今年。"""
    return f"{day[5:7]}/{day[8:10]}" if day[:4] == base_date[:4] else day.replace("-", "/")


def _structural_levels(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    levels: list[dict[str, Any]] = []
    closes = [row["close"] for row in rows]
    for period in PARAMETERS["ma_periods"]:
        window = closes[-period:]
        if len(window) == period and all(value is not None for value in window):
            levels.append({"id": f"ma{period}", "name": _MA_NAMES[period], "kind": "ma",
                           "value": sum(window) / period})
    for size in PARAMETERS["high_low_windows"]:
        window = rows[-size:]
        if len(window) < size or any(row["high"] is None or row["low"] is None for row in window):
            continue
        high = max(window, key=lambda row: row["high"])
        low = min(window, key=lambda row: row["low"])
        label = _WINDOW_NAMES[size]
        levels.append({"id": f"high{size}", "name": f"{label}高點（{_label_date(high['date'], rows[-1]['date'])}）", "kind": "high",
                       "value": high["high"], "date": high["date"]})
        levels.append({"id": f"low{size}", "name": f"{label}低點（{_label_date(low['date'], rows[-1]['date'])}）", "kind": "low",
                       "value": low["low"], "date": low["date"]})
    last = rows[-1]
    if last["high"] is not None and last["low"] is not None:
        levels.append({"id": "prev_high", "name": "前一交易日高點", "kind": "prev", "value": last["high"]})
        levels.append({"id": "prev_low", "name": "前一交易日低點", "kind": "prev", "value": last["low"]})
    levels.extend(_open_gaps(rows))
    return levels


def _open_gaps(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    start = max(1, len(rows) - PARAMETERS["gap_lookback_bars"])
    for index in range(start, len(rows)):
        previous, current = rows[index - 1], rows[index]
        if None in (previous["high"], previous["low"], current["high"], current["low"]):
            continue
        later = [row for row in rows[index + 1:] if row["high"] is not None and row["low"] is not None]
        if current["low"] > previous["high"]:
            lower, upper = previous["high"], min([current["low"], *(row["low"] for row in later)])
        elif current["high"] < previous["low"]:
            lower, upper = max([current["high"], *(row["high"] for row in later)]), previous["low"]
        else:
            continue
        if upper <= lower:
            continue  # 已回補
        stamp = current["date"].replace("-", "")
        gaps.append({"id": f"gap{stamp}_upper", "name": f"缺口上緣（{_label_date(current['date'], rows[-1]['date'])}）", "kind": "gap",
                     "value": upper, "date": current["date"]})
        gaps.append({"id": f"gap{stamp}_lower", "name": f"缺口下緣（{_label_date(current['date'], rows[-1]['date'])}）", "kind": "gap",
                     "value": lower, "date": current["date"]})
    return gaps


def _forward_returns(closes: list[float | None], horizon: int) -> list[float]:
    out = []
    for index in range(len(closes) - horizon):
        start, end = closes[index], closes[index + horizon]
        if start not in (None, 0) and end is not None:
            out.append(end / start - 1)
    return out


def _nearest_rank(values: list[float], percentile: int) -> float:
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile / 100 * len(ordered)))
    return ordered[rank - 1]


_DISTANCE_SIDES = (
    # (邊, id 前綴, 百分位參數, 名稱用字, 歷史說明, 方向)
    ("above", "dist_up", "up", "上方", "漲幅八成位", 1),
    ("below", "dist_down", "down", "下方", "跌幅兩成位", -1),
)


def _candidate_levels(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    close = rows[-1]["close"]
    min_gap = PARAMETERS["min_distance_pct"] / 100
    kept = []
    for level in _structural_levels(rows):
        value = round(level["value"], 2)
        if abs(value / close - 1) < min_gap:
            continue  # 離收盤太近
        kept.append({**level, "value": value, "side": "above" if value > close else "below"})
    closes = [row["close"] for row in rows]
    for side, prefix, percentile_key, word, history, direction in _DISTANCE_SIDES:
        if any(level["side"] == side for level in kept):
            continue
        for horizon in HORIZONS:
            returns = _forward_returns(closes, horizon)
            if not returns:
                continue
            move = _nearest_rank(returns, PARAMETERS["distance_percentiles"][percentile_key])
            move = direction * max(direction * move, min_gap)  # 至少離收盤一個門檻
            kept.append({"id": f"{prefix}_{horizon}",
                         "name": f"{_HORIZON_NAMES[horizon]}{word}距離關卡（歷史{history}）",
                         "kind": "distance", "value": round(close * (1 + move), 2), "side": side,
                         "horizon": horizon, "samples": len(returns)})
    return sorted(kept, key=lambda level: (-level["value"], level["id"]))


def _usable(level: Mapping[str, Any], horizon: int) -> bool:
    return level.get("horizon") in (None, horizon)


# ---- 輸入 ----

def _readiness(data: Mapping[str, Any], rows: list[dict[str, Any]]) -> list[str]:
    base_date = data.get("base_date")
    problems = []
    if not rows or rows[-1]["date"] != base_date or rows[-1]["close"] is None:
        problems.append("index_daily 最後一根不是基準日收盤")
    cutoffs = data.get("data_cutoffs") or {}
    problems.extend(f"{name} 截止日不是基準日" for name in REQUIRED_INPUTS if cutoffs.get(name) != base_date)
    labels = data.get("trend_labels") or {}
    problems.extend(f"走勢標籤 {key} 缺漏" for key in ("short", "medium") if labels.get(key) not in TREND_LABELS)
    return problems


def _model_input(data: Mapping[str, Any], rows: list[dict[str, Any]], edition: str,
                 candidates: list[dict[str, Any]] | None) -> dict[str, Any]:
    return {
        "schema": "forecast-input/v1",
        "input_version": INPUT_VERSION,
        "parameters": copy.deepcopy(PARAMETERS),
        "base_date": data.get("base_date"),
        "edition": edition,
        "index_recent": rows[-PARAMETERS["recent_bars_for_model"]:],
        "trend_labels": copy.deepcopy(dict(data.get("trend_labels") or {})),
        "chips": copy.deepcopy(data.get("chips")),
        "data_cutoffs": copy.deepcopy(dict(data.get("data_cutoffs") or {})),
        "candidates": candidates,
        "history": {"from": rows[0]["date"] if rows else None, "to": rows[-1]["date"] if rows else None,
                    "bars": len(rows), "sha256": _sha(rows)},
        "method_text": data.get("method_text"),
    }


def _build_prompt(model_input: Mapping[str, Any]) -> str:
    return "\n".join([*_PROMPT_RULES, "INPUT_JSON:",
                      json.dumps(model_input, ensure_ascii=False, sort_keys=True, separators=(",", ":"))])


# ---- 模型呼叫與回答驗證 ----

def _model_command(model_bin: Path | str, prompt: str, model: str) -> list[str]:
    path = Path(model_bin)
    prefix = [sys.executable, str(path)] if path.suffix.lower() == ".py" else [str(path)]
    return [*prefix, "-p", prompt, "--model", model, "--effort", "medium", "--output-format", "json",
            "--json-schema", json.dumps(OUTPUT_SCHEMA, ensure_ascii=False, separators=(",", ":")),
            "--print-timeout", "300s", "--sandbox"]


def _call_model(model_bin: Path | str, prompt: str, model: str, timeout: float) -> tuple[str, Any, str]:
    """回傳 (狀態, structured_output, 說明)；狀態是 ok／model_failed／model_timeout／output_garbled。"""
    try:
        completed = subprocess.run(_model_command(model_bin, prompt, model), input=b"", capture_output=True,
                                   timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return "model_timeout", None, f"超過 {timeout} 秒沒有回應"
    except OSError as error:
        return "model_failed", None, f"{type(error).__name__}: {error}"
    try:
        envelope = json.loads(completed.stdout.decode("utf-8").strip())
    except (UnicodeDecodeError, json.JSONDecodeError):
        if completed.returncode != 0:
            return "model_failed", None, f"結束碼 {completed.returncode}"
        return "output_garbled", None, "輸出不是 UTF-8 JSON"
    if completed.returncode != 0 or not isinstance(envelope, dict) or envelope.get("status") != "SUCCESS":
        return "model_failed", None, f"結束碼 {completed.returncode}"
    return "ok", envelope.get("structured_output"), ""


def _text_problem(text: Any) -> str | None:
    if not isinstance(text, str) or not text.strip():
        return "文字為空"
    if len(text) > 160:
        return "文字過長"
    if any(character.isnumeric() or character in CHINESE_NUMERAL_CHARS for character in text):
        return "自由文字不得包含數字；價位請用候選編號"
    return None


def _render(text: str, refs: list[str], by_id: Mapping[str, Mapping[str, Any]]) -> str:
    if not refs:
        return text.strip()
    facts = "；".join(f"{by_id[ref]['name']} {_format_number(by_id[ref]['value'])} 點" for ref in refs)
    return f"{text.strip()}【資料：{facts}】"


def _line(level: Mapping[str, Any]) -> dict[str, Any]:
    return {"id": level["id"], "name": level["name"], "value": level["value"]}


def _validate(structured: Any, model_input: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(structured, dict) or set(structured) != {"base_date", "regime", "cells"}:
        return None, "回答缺欄位或含未允許欄位"
    if structured["base_date"] != model_input["base_date"]:
        return None, "回答的基準日不符"
    if structured["regime"] not in REGIMES:
        return None, "盤勢類型不在清單內"
    cells = structured["cells"]
    if not isinstance(cells, dict) or set(cells) != {str(h) for h in HORIZONS}:
        return None, "三格不齊或多出格子"
    by_id = {level["id"]: level for level in model_input["candidates"]}
    rendered: dict[str, Any] = {}
    for horizon in HORIZONS:
        key = str(horizon)
        item = cells[key]
        if not isinstance(item, dict) or set(item) != set(_CELL_SCHEMA["required"]):
            return None, f"{key} 日格式不正確"
        for field, side in (("strong_line", "above"), ("weak_line", "below")):
            level = by_id.get(item[field])
            if level is None or not _usable(level, horizon):
                return None, f"{key} 日 {field} 不是這一格的候選關卡"
            if level["side"] != side:
                return None, f"{key} 日 {field} 放錯邊"
        if item["zone"] not in ZONES:
            return None, f"{key} 日落點不在清單內"
        for text_field, refs_field in (("reason", "reason_refs"), ("invalidation", "invalidation_refs")):
            problem = _text_problem(item[text_field])
            if problem:
                return None, f"{key} 日 {text_field}：{problem}"
            refs = item[refs_field]
            if (not isinstance(refs, list) or len(refs) > 3 or len(set(refs)) != len(refs)
                    or any(ref not in by_id or not _usable(by_id[ref], horizon) for ref in refs)):
                return None, f"{key} 日 {refs_field} 含不存在的候選編號"
        rendered[key] = {
            "horizon": horizon,
            "strong_line": _line(by_id[item["strong_line"]]),
            "weak_line": _line(by_id[item["weak_line"]]),
            "zone": item["zone"],
            "reason": _render(item["reason"], item["reason_refs"], by_id),
            "reason_refs": list(item["reason_refs"]),
            "invalidation": _render(item["invalidation"], item["invalidation_refs"], by_id),
            "invalidation_refs": list(item["invalidation_refs"]),
        }
    return {"regime": structured["regime"], "cells": rendered}, None


# ---- 寫一份預測 ----

def _deadline(edition: str, base_date: str, session_date: str | None) -> datetime:
    if edition == "evening":
        return datetime.fromisoformat(base_date).replace(tzinfo=TAIPEI) + timedelta(days=1)
    if not session_date or session_date <= base_date:
        raise ValueError("晨間版須給基準日之後的開盤日 session_date")
    hour, minute = MORNING_DEADLINE
    return datetime.fromisoformat(session_date).replace(hour=hour, minute=minute, tzinfo=TAIPEI)


def _stem(base_date: str, edition: str, method_version: str, input_version: str) -> str:
    date.fromisoformat(base_date)
    return f"{base_date}__{edition}__m{method_version}__{input_version}"


def _forecast_path(root: Path, base_date: str, edition: str, method_version: str) -> Path:
    return Path(root) / "forecasts" / f"{_stem(base_date, edition, method_version, INPUT_VERSION)}.json"


_FIXED_RULE = {"偏多": "above", "偏空": "below", "盤整": "between"}


def _zone(value: float, strong: float, weak: float) -> str:
    """大於等於轉強線算線上、小於轉弱線算線下，其餘兩線之間（同「隔日三區」）。

    對帳時代入收盤與兩條線；照歷史亂猜時代入漲跌幅與兩條線的距離。
    """
    if value >= strong:
        return "above"
    if value < weak:
        return "below"
    return "between"


def _historical_shares(rows: list[dict[str, Any]], horizon: int, strong: float, weak: float) -> dict[str, Any]:
    """照歷史亂猜：歷史上隔同樣多個交易日，漲跌幅落在兩條線距離的哪一區各有幾次。"""
    close = rows[-1]["close"]
    up, down = strong / close - 1, weak / close - 1
    counts = {zone: 0 for zone in ZONES}
    first = last = None
    for index in range(len(rows) - horizon):
        start, end = rows[index]["close"], rows[index + horizon]["close"]
        if start in (None, 0) or end is None:
            continue
        counts[_zone(end / start - 1, up, down)] += 1
        first = first or rows[index]["date"]
        last = rows[index + horizon]["date"]
    return {**counts, "samples": sum(counts.values()), "from": first, "to": last}


def _with_baselines(accepted: dict[str, Any], rows: list[dict[str, Any]],
                    labels: Mapping[str, Any]) -> dict[str, Any]:
    for horizon in HORIZONS:
        item = accepted["cells"][str(horizon)]
        item["baseline"] = _historical_shares(rows, horizon, item["strong_line"]["value"], item["weak_line"]["value"])
        item["fixed_rule_zone"] = _FIXED_RULE[labels["short" if horizon in (1, 5) else "medium"]]
    return accepted


def _default_now() -> datetime:
    return datetime.now(TAIPEI)


def write_forecast(
    root: Path | str,
    data: Mapping[str, Any],
    *,
    edition: str,
    model_bin: Path | str,
    model: str,
    now: Callable[[], datetime] | None = None,
    official_since: str | None = None,
    session_date: str | None = None,
    timeout_seconds: float = 360,
) -> dict[str, Any]:
    """寫一份預測。回傳 ``{"outcome": written|unchanged|conflict|not_ready, "record": 帳本裡那一筆}``。

    同一識別已有紀錄時不再呼叫模型：輸入相同回 ``unchanged``，輸入不同回 ``conflict``（拒寫）。
    資料沒到齊時期限前什麼都不寫（``not_ready``，可之後重跑）；過了期限仍沒到齊才記漏記。
    整份全有或全無；其他任何失敗都寫成一筆鎖定的漏記。
    """
    if edition not in EDITIONS:
        raise ValueError(f"edition 必須是 {EDITIONS} 之一")
    clock = now or _default_now
    base_date = str(data.get("base_date"))
    method_version = method_version_of(str(data.get("method_text") or ""))
    path = _forecast_path(Path(root), base_date, edition, method_version)
    deadline = _deadline(edition, base_date, session_date)

    rows = _clean_daily(data.get("index_daily") or [])
    problems = _readiness(data, rows)
    candidates = None if problems else _candidate_levels(rows)
    model_input = _model_input(data, rows, edition, candidates)
    input_sha = _sha(model_input)

    if path.exists():
        existing = _read(path)
        return {"outcome": "unchanged" if existing["input_sha256"] == input_sha else "conflict", "record": existing}

    record: dict[str, Any] = {
        "schema": SCHEMA,
        "forecast_id": f"{base_date}|{edition}|{method_version}|{INPUT_VERSION}",
        "base_date": base_date, "edition": edition,
        "method_version": method_version, "input_version": INPUT_VERSION,
        "model": model,
        "run_mode": "official" if official_since and base_date >= official_since else "trial",
        "data_cutoffs": copy.deepcopy(dict(data.get("data_cutoffs") or {})),
        "deadline": deadline.isoformat(),
        "input": model_input, "input_sha256": input_sha,
        "attempts": [],
    }

    def finish(kind: str, **fields: Any) -> dict[str, Any]:
        record.update(kind=kind, locked_at=clock().astimezone(TAIPEI).isoformat(), **fields)
        if kind == "forecast" and datetime.fromisoformat(record["locked_at"]) >= deadline:
            for key in ("regime", "cells"):
                record.pop(key, None)
            record.update(kind="miss", miss_reason="deadline_passed", miss_detail="鎖定時已過期限")
        outcome = _append(path, _seal(record))
        return {"outcome": outcome, "record": _read(path) if outcome != "written" else record}

    if problems:
        if clock() < deadline:
            return {"outcome": "not_ready", "record": None, "detail": "；".join(problems)}
        return finish("miss", miss_reason="input_not_ready", miss_detail="；".join(problems))
    if clock() >= deadline:
        return finish("miss", miss_reason="deadline_passed", miss_detail="開始寫之前已過期限")

    prompt = _build_prompt(model_input)
    for _ in range(2):
        status, structured, detail = _call_model(model_bin, prompt, model, timeout_seconds)
        if status != "ok":
            record["attempts"].append({"status": status, "detail": detail})
            return finish("miss", miss_reason=status, miss_detail=detail)
        accepted, problem = _validate(structured, model_input)
        if accepted is not None:
            return finish("forecast", **_with_baselines(accepted, rows, data["trend_labels"]))
        record["attempts"].append({"status": "answer_invalid", "detail": problem})
    return finish("miss", miss_reason="answer_invalid", miss_detail=record["attempts"][-1]["detail"])


# ---- 到期對帳 ----

def _expiry(position: Mapping[str, int], rows: list[dict[str, Any]], base_date: str,
            horizon: int) -> tuple[str, dict[str, Any] | None]:
    """（pending｜undeterminable｜due, 到期那根日 K）。基準日不在日 K 裡或到期收盤缺漏都算無法判定。"""
    base_index = position.get(base_date)
    if base_index is None:
        return "undeterminable", None
    if base_index + horizon >= len(rows):
        return "pending", None
    expiry = rows[base_index + horizon]
    return ("undeterminable" if expiry["close"] is None else "due"), expiry


def reconcile(root: Path | str, index_daily: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """替所有預測的每一格檢查一次到期。到期日是基準日之後第 1／5／20 個「指數日 K 有資料」的日子。

    回傳本次新增的對帳紀錄（``appended``）、還沒到期（``pending``）、資料缺漏無法判定
    （``undeterminable``）與重算結果不同被拒寫的衝突（``conflicts``）。後兩者都不算猜錯。
    """
    folder = Path(root)
    rows = _clean_daily(index_daily)
    position = {row["date"]: index for index, row in enumerate(rows)}
    result: dict[str, list[dict[str, Any]]] = {"appended": [], "pending": [], "undeterminable": [], "conflicts": []}
    for forecast in read_ledger(folder)["forecasts"]:
        if forecast["kind"] != "forecast":
            continue
        stem = _stem(forecast["base_date"], forecast["edition"], forecast["method_version"], forecast["input_version"])
        for horizon in HORIZONS:
            path = folder / "reconciliations" / f"{stem}__h{horizon}.json"
            ref = {"forecast_id": forecast["forecast_id"], "horizon": horizon}
            status, expiry = _expiry(position, rows, forecast["base_date"], horizon)
            if status != "due":
                if not path.exists():
                    extra = {} if status == "pending" else {"expiry_date": expiry["date"] if expiry else None}
                    result[status].append({**ref, **extra})
                continue
            item = forecast["cells"][str(horizon)]
            record = _seal({
                "schema": SCHEMA, "kind": "reconciliation", **ref,
                "forecast_sha256": forecast["record_sha256"],
                "edition": forecast["edition"], "base_date": forecast["base_date"],
                "expiry_date": expiry["date"], "expiry_close": expiry["close"],
                "actual_zone": _zone(expiry["close"], item["strong_line"]["value"], item["weak_line"]["value"]),
            })
            outcome = _append(path, record)
            if outcome == "written":
                result["appended"].append(record)
            elif outcome == "conflict":
                existing = json.loads(path.read_text(encoding="utf-8"))
                conflict = _seal({
                    "schema": SCHEMA, "kind": "reconciliation_conflict", **ref,
                    "existing_sha256": existing["record_sha256"], "recomputed": record,
                })
                _append(folder / "conflicts" / f"{stem}__h{horizon}__{record['record_sha256'][:16]}.json", conflict)
                result["conflicts"].append({**ref, "expiry_date": expiry["date"],
                                            "existing_zone": existing["actual_zone"],
                                            "recomputed_zone": record["actual_zone"]})
    return result


# ---- 彙總 ----

def _rates(pairs: list[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    """pairs 是（這一格的預測, 它的對帳紀錄）。照歷史亂猜的比例 = 各筆所選那一區歷史比例的平均。"""
    if not pairs:
        return {"reconciled": 0, "hit_rate": None, "baseline_rate": None, "fixed_rule_rate": None}
    count = len(pairs)
    shares = [cell["baseline"][cell["zone"]] / cell["baseline"]["samples"]
              for cell, _ in pairs if cell["baseline"]["samples"]]
    return {
        "reconciled": count,
        "hit_rate": sum(cell["zone"] == outcome["actual_zone"] for cell, outcome in pairs) / count,
        "baseline_rate": sum(shares) / len(shares) if shares else None,
        "fixed_rule_rate": sum(cell["fixed_rule_zone"] == outcome["actual_zone"] for cell, outcome in pairs) / count,
    }


def summarize(root: Path | str, index_daily: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """依（試跑／正式、版本、期間、方法版本、輸入版本）分組；試跑與正式各自成組，不混算。

    還沒對帳的格子依截至目前的指數日 K 分成「還沒到期」與「無法判定」，兩者都不算猜錯。
    """
    ledger = read_ledger(root)
    rows = _clean_daily(index_daily)
    position = {row["date"]: index for index, row in enumerate(rows)}
    reconciled_by_cell = {(row["forecast_id"], row["horizon"]): row for row in ledger["reconciliations"]}
    series: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for record in ledger["forecasts"]:
        key = (record["run_mode"], record["edition"], record["method_version"], record["input_version"])
        bucket = series.setdefault(key, {"misses": 0, "forecasts": []})
        if record["kind"] == "miss":
            bucket["misses"] += 1
        else:
            bucket["forecasts"].append(record)
    groups = []
    for (run_mode, edition, method_version, input_version), bucket in sorted(series.items()):
        for horizon in HORIZONS:
            reconciled, pullback = [], []
            open_cells = {"pending": 0, "undeterminable": 0}
            for record in bucket["forecasts"]:
                cell = record["cells"][str(horizon)]
                outcome = reconciled_by_cell.get((record["forecast_id"], horizon))
                if outcome is None:
                    status, _ = _expiry(position, rows, record["base_date"], horizon)
                    open_cells["pending" if status == "pending" else "undeterminable"] += 1
                    continue
                reconciled.append((cell, outcome))
                if record["regime"] == PULLBACK_REGIME:
                    pullback.append((cell, outcome))
            groups.append({
                "run_mode": run_mode, "edition": edition, "horizon": horizon,
                "method_version": method_version, "input_version": input_version,
                **_rates(reconciled), **open_cells, "misses": bucket["misses"],
                "pullback": _rates(pullback),
            })
    return {"groups": groups, "conflicts": len(ledger["conflicts"])}


# ---- 讀帳本 ----

def read_ledger(root: Path | str) -> dict[str, list[dict[str, Any]]]:
    folder = Path(root)
    out: dict[str, list[dict[str, Any]]] = {}
    for kind in ("forecasts", "reconciliations", "conflicts"):
        out[kind] = [_read(path) for path in sorted((folder / kind).glob("*.json"))] if (folder / kind).is_dir() else []
    return out
