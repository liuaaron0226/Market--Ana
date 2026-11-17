"""預測帳本：加權指數的事前預測，寫下鎖定、到期對帳、彙總。

規格：``.scratch/forecast-ledger/spec.md``。對外只有：

- ``write_forecast``：給一份當天輸入與一個模型命令列程式，得到一筆鎖定的預測或一筆鎖定的漏記。
- ``reconcile``：給截至某一天的指數日 K，替到期的格子新增對帳紀錄。
- ``summarize``：把帳本整理成依版本、期間、方法與輸入版本分組的摘要。
- ``read_ledger``：讀出全部紀錄，順便核對封存雜湊（原件被改動時丟 ``LedgerIntegrityError``）。
- ``method_version_of``：從方法稿全文讀出版本號。

候選關卡、照歷史亂猜、固定規則、回答驗證與到期日計算都封在這裡。帳本只能新增：
同一個識別（基準日＋版本＋方法版本＋輸入版本）只會有一筆；鎖定、雜湊與模型呼叫在 ``ledger_core``
（和關注帳本共用；做法沿用 ``candidate_snapshots``：正規化 JSON 的 SHA-256、``os.link`` 不覆寫）。
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

import foreign_flow
import gemini_models
# 鎖定、雜湊、模型呼叫、期限與方法版本和關注帳本共用（ledger_core），這裡只留預測帳本自己的規則。
from ledger_core import (
    EDITIONS,
    TAIPEI,
    LedgerIntegrityError,  # noqa: F401  （對外沿用原名）
    build_prompt,
    call_model,
    method_version_of,
    read_folders,
    refs_problem,
    render_refs,
)
from ledger_core import add_months as _add_months
from ledger_core import compact_json as _json
from ledger_core import sql_insert as _insert
from ledger_core import append as _append
from ledger_core import clean_daily as _clean_daily
from ledger_core import deadline as _deadline
from ledger_core import default_now as _default_now
from ledger_core import label_date as _label_date
from ledger_core import read_record as _read
from ledger_core import seal as _seal
from ledger_core import sha as _sha
from ledger_core import text_problem as _text_problem
from overseas_inputs import PARAMETERS as _OVERSEAS_PARAMETERS

# 過去的經驗（E 票）：相似盤勢層與教訓層，自成一個模組。
import forecast_experience as _experience

SCHEMA = "forecast-ledger/v1"
# 寫預測用的模型（本人 2026-11-17 換 Argon；名稱只在 gemini_models 改）。模型算進輸入版本，見 input_version()。
MODEL = gemini_models.FORECAST
HORIZONS = (1, 5, 20)
ZONES = ("above", "between", "below")
REGIMES = ("多頭延續", "多頭中的回檔", "高檔轉弱", "空頭延續", "空頭中的反彈", "盤整")
PULLBACK_REGIME = "多頭中的回檔"
TREND_LABELS = ("偏多", "偏空", "盤整")
REQUIRED_INPUTS = ("index", "institutional", "futures")

# 候選關卡與基準的參數：在第一筆紀錄之前寫定，整組算進輸入版本，改了就是新系列。
PARAMETERS: dict[str, Any] = {
    # 交給模型的輸入層；某一層「有或沒有」才換輸入版本（教訓層內容隨時間增長不換）。
    # 海外層（D 票）、外資層（G 票，本人 2026-11-15 定案加入外資讀數；讀數與歷史統計由 foreign_flow 算，模型只讀）、
    # 相似盤勢與教訓層（E 票）同時上，輸入版本照五層一起算。
    "layers": ["base", "overseas", "foreign_flow", "similar", "lessons"],
    "foreign_flow_layer": foreign_flow.VERSION,
    "ma_periods": [5, 10, 20, 60, 120, 240],
    "high_low_windows": [20, 60, 240],
    "gap_lookback_bars": 240,
    "min_distance_pct": 0.3,
    "distance_percentiles": {"up": 80, "down": 20},
    "recent_bars_for_model": 60,
    # 基本層交給模型的資料組成（規格〈交給 Gemini 的輸入〉）；加減任何一項都會換輸入版本。
    "base_inputs": ["index_ohlc_amount", "indicators_short_medium_long", "trend_labels", "chips_institutional_futures",
                    "candidates", "bollinger", "method_text"],
    # 相似盤勢的歸類定義與教訓層的筆數（本人 2026-11-16 選「照這樣」）；改任何一個數字都是新系列。
    "similar_regimes": _experience.PARAMETERS["similar_regimes"],
    "lessons": _experience.PARAMETERS["lessons"],
    # 本人 2026-11-16「兩樣都加」：加權指數的布林位置（20 日、2 倍；近 120 根最窄兩成算變窄），上下緣列進候選關卡。
    "bollinger": {"period": 20, "k": 2, "squeeze_window": 120, "squeeze_share": 0.2},
    # 海外層（D 票）：美日韓指數、美債、美元指數、美元兌台幣、外資讀數 1 與 4；哪些來源交給模型也算在內。
    "overseas": _OVERSEAS_PARAMETERS,
}

_BOLL_ZONES = {"below": "下緣以下", "lower_half": "下緣到中線", "upper_half": "中線到上緣", "above": "上緣以上"}
_BOLL_NOTE = "布林位置只是位置描述；歷史上用它調整持股水位沒有通過及格線（.scratch/bollinger-study/report.html）。"
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
    # 本人 2026-11-15 看過真 Gemini 乾跑後選「先改提示再乾跑」：下面兩條是那次加的。
    "理由與看錯條件只能引用 INPUT_JSON 裡有的資料（日 K、指標、法人與期貨、外資讀數、候選關卡、布林位置、overseas、方法稿）；"
    "INPUT_JSON 沒給的資料（例如融資、新聞、overseas.not_provided 列出的項目）不得當成事實寫進去。",
    # D 票（2026-11-16）：布林位置與海外層的使用規則。
    "bollinger 只是位置描述：歷史上用布林通道調整持股水位沒有通過及格線，不可單獨當成轉強或轉弱的理由。",
    "overseas 每一項都標了資料日期：美國的收盤是台北前一晚（晨間版是剛收盤那一晚），status 不是 ok 的項目代表休市、"
    "沒抓到或不是預期那天的收盤，不得當成最新行情。",
    # G 票：外資讀數（本人 2026-11-15「要，加進去」「要，加第六個」）。
    "foreign_flow 是程式算好的外資讀數與歷史統計，只能照讀，不可自己重算或改數字；status 是 missing 的讀數當作沒有。"
    "外資的買賣解釋當天的漲跌，不代表之後會續漲或續跌：不得把外資賣超、外資續賣的機率（或期貨加空）直接當成之後會跌的理由，"
    "也不得把期貨淨空單的多寡當成看空的理由；不得把不是外資統計講的事（例如均線壓力）說成外資統計的結論。"
    "要和 foreign_flow.notes 與各讀數附的歷史統計一起看。",
    "每一格到期後，程式會把你的落點命中率和「照歷史亂猜」並排比較：照歷史亂猜是不看任何判斷、只看歷史上同樣距離的兩條線"
    "收盤落在各區的比例。兩條線拉得越寬，「兩線之間」在歷史上本來就越常中，所以只為了保險把線拉寬，比不贏照歷史亂猜。",
    f"regime 從固定清單選一個：{'、'.join(REGIMES)}。base_date 照抄 INPUT_JSON 的 base_date。",
    # 過去的經驗（E 票）：本人「不要讓歷史重演，根據過去的經驗去輔助判斷」。
    "similar_regimes 是程式依事先寫定的定義，把歷史上的日子歸成「多頭中的回檔」「高檔轉弱」兩類，"
    "統計各類起點之後一、五、二十個交易日的漲跌（每一段行情只算一次），all_days 是不分類的對照；today 是今天屬於哪一類。"
    "這是事實可以參考，但 low_sample 為 true 的類別樣本太少，不能當成規律；「高檔轉弱」只代表和歷史上某些情況相似，"
    "不保證會大跌、也不保證能避開。today 是多頭中的回檔時，二十日那一格的轉弱線要挑「跌破才算趨勢轉壞」的那條線。",
    "lessons 是程式整理的過去對帳事實：各期間猜中與沒中的次數，以及最近幾次沒中的那一格當時選的落點、兩條線、盤勢、"
    "看錯條件與實際落點。寫之前先看這些，避免同一種錯一犯再犯；lessons 是空的代表還沒有對完帳的紀錄。",
)
def input_version(model: str) -> str:
    """輸入版本：參數、回答格式、提示規則、模型任何一項改了就是新系列，和舊系列分開對帳（教訓層也只讀同一系列）。

    本人 2026-11-17「預測也換 Argon」：換模型＝新的輸入版本；換模型前 Flash 寫的試跑與重播是
    ``base+overseas+foreign_flow+similar+lessons-v1-1fbcaa45``（那時模型還沒算進來）。"""
    return "+".join(PARAMETERS["layers"]) + "-v1-" + hashlib.sha256(json.dumps(
        {"parameters": PARAMETERS, "output_schema": OUTPUT_SCHEMA, "prompt_rules": _PROMPT_RULES, "model": model},
        ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:8]


INPUT_VERSION = input_version(MODEL)


# ---- 布林通道 ----

def _band(closes: list[Any], end: int) -> tuple[float, float, float] | None:
    """第 end 根（含）往前 period 根的（中線, 上緣, 下緣）；和 web/kchart.js 的 bollinger() 同算法（母體標準差）。"""
    period, k = PARAMETERS["bollinger"]["period"], PARAMETERS["bollinger"]["k"]
    window = closes[end - period + 1:end + 1]
    if end + 1 < period or len(window) != period or any(value is None for value in window):
        return None
    mid = sum(window) / period
    width = k * math.sqrt(sum((value - mid) ** 2 for value in window) / period)
    return mid, mid + width, mid - width


def _bollinger(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """基準日收盤在布林通道的哪一區，以及通道寬度是不是近 120 根裡最窄的兩成（資料不足時是 None）。"""
    closes = [row["close"] for row in rows]
    last = len(closes) - 1
    band = _band(closes, last) if closes else None
    if band is None:
        return None
    mid, upper, lower = band
    close = closes[-1]
    zone = "below" if close < lower else "above" if close > upper else "lower_half" if close < mid else "upper_half"
    window, share = PARAMETERS["bollinger"]["squeeze_window"], PARAMETERS["bollinger"]["squeeze_share"]
    squeezed = None  # 不到 period + window − 1 根，近 120 根裡有算不出通道的日子：不判斷
    if last + 1 >= window:
        widths = [None if (b := _band(closes, end)) is None or b[0] <= 0 else (b[1] - b[2]) / b[0]
                  for end in range(last - window + 1, last + 1)]
        if None not in widths:
            squeezed = sum(width < widths[-1] for width in widths) < window * share
    return {"middle": round(mid, 2), "upper": round(upper, 2), "lower": round(lower, 2), "zone": _BOLL_ZONES[zone],
            "width_pct": round((upper - lower) / mid * 100, 2), "squeezed": squeezed, "note": _BOLL_NOTE}


# ---- 候選關卡 ----

def _structural_levels(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    levels: list[dict[str, Any]] = []
    closes = [row["close"] for row in rows]
    band = _band(closes, len(closes) - 1) if closes else None
    if band is not None:
        levels.append({"id": "boll_upper", "name": "布林上緣", "kind": "bollinger", "value": band[1]})
        levels.append({"id": "boll_lower", "name": "布林下緣", "kind": "bollinger", "value": band[2]})
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
        # 布林上下緣永遠在收盤兩側附近，不算「圖上的結構關卡」：創新高／新低時照樣補距離關卡。
        if any(level["side"] == side and level["kind"] != "bollinger" for level in kept):
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


def _cutoffs(data: Mapping[str, Any]) -> dict[str, Any]:
    """台股各項的截止日，加上交給模型的海外各項資料日期。"""
    return {**copy.deepcopy(dict(data.get("data_cutoffs") or {})), **copy.deepcopy(dict(data.get("overseas_cutoffs") or {}))}


# 只存在原件、不進模型輸入的海外欄位：各項狀態、缺漏、條款不允許交給模型的數字、原件清單。
_RECORD_ONLY = ("overseas_status", "missing_inputs", "record_only", "sources")


def _model_input(data: Mapping[str, Any], rows: list[dict[str, Any]], edition: str,
                 candidates: list[dict[str, Any]] | None, experience: Mapping[str, Any], version: str) -> dict[str, Any]:
    return {
        "schema": "forecast-input/v1",
        "input_version": version,
        "parameters": copy.deepcopy(PARAMETERS),
        "base_date": data.get("base_date"),
        "edition": edition,
        "index_recent": rows[-PARAMETERS["recent_bars_for_model"]:],
        "trend_labels": copy.deepcopy(dict(data.get("trend_labels") or {})),
        "indicators": copy.deepcopy(data.get("indicators")),
        "chips": copy.deepcopy(data.get("chips")),
        "foreign_flow": copy.deepcopy(data.get("foreign_flow")),
        "data_cutoffs": _cutoffs(data),
        "candidates": candidates,
        "bollinger": _bollinger(rows),
        "overseas": copy.deepcopy(data.get("overseas")),
        "history": {"from": rows[0]["date"] if rows else None, "to": rows[-1]["date"] if rows else None,
                    "bars": len(rows), "sha256": _sha(rows)},
        "method_text": data.get("method_text"),
        **experience,
    }


def _experience_layers(root: Path, base_date: str, rows: list[dict[str, Any]], ready: bool,
                       version: str) -> dict[str, Any]:
    """相似盤勢層只在日 K 截到基準日時才算（不偷看之後）；教訓層只用寫這一份時已知結果的對帳，
    而且只讀同一輸入版本的紀錄（換模型後不拿舊模型的對帳當教訓）。"""
    ledger = read_ledger(root)
    forecasts = [record for record in ledger["forecasts"] if record.get("input_version") == version]
    return {
        "similar_regimes": _experience.similar_regimes(rows) if ready else None,
        "lessons": _experience.lessons(forecasts, ledger["reconciliations"], base_date),
    }


# ---- 模型呼叫與回答驗證 ----

def _render(text: str, refs: list[str], by_id: Mapping[str, Mapping[str, Any]]) -> str:
    return render_refs(text, refs, by_id, "點")


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
        usable = {level_id: level for level_id, level in by_id.items() if _usable(level, horizon)}
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
            if refs_problem(item[refs_field], usable):
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

def _stem(base_date: str, edition: str, method_version: str, input_version: str) -> str:
    date.fromisoformat(base_date)
    return f"{base_date}__{edition}__m{method_version}__{input_version}"


def _forecast_path(root: Path, base_date: str, edition: str, method_version: str, version: str) -> Path:
    return Path(root) / "forecasts" / f"{_stem(base_date, edition, method_version, version)}.json"


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
    replay: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """寫一份預測。回傳 ``{"outcome": written|unchanged|conflict|not_ready|model_failed, "record": 帳本裡那一筆}``
    （``model_failed`` 只出現在重播）。

    同一識別已有紀錄時不再呼叫模型：輸入相同回 ``unchanged``，輸入不同回 ``conflict``（拒寫）。
    資料沒到齊時期限前什麼都不寫（``not_ready``，可之後重跑）；過了期限仍沒到齊才記漏記。
    整份全有或全無；其他任何失敗都寫成一筆鎖定的漏記。

    ``replay``（本人 2026-11-17「要，重播當參考」）：寫成歷史重播紀錄，``run_mode`` 是 replay、識別加 ``replay|``
    前綴，附上這份說明（模型知識截止日、資料何時抓的）。重播要寫在另一本帳裡。模型呼叫失敗（多半是額度）不鎖，
    回 ``model_failed`` 讓那天之後能再重播；回答不合格照正式的規矩鎖成漏記。
    """
    if edition not in EDITIONS:
        raise ValueError(f"edition 必須是 {EDITIONS} 之一")
    if replay is not None and not replay:
        raise ValueError("重播要附上說明（模型知識截止日、資料抓取時間），不能是空的")
    clock = now or _default_now
    base_date = str(data.get("base_date"))
    method_version = method_version_of(str(data.get("method_text") or ""))
    version = input_version(model)
    path = _forecast_path(Path(root), base_date, edition, method_version, version)
    deadline = _deadline(edition, base_date, session_date)

    rows = _clean_daily(data.get("index_daily") or [])
    problems = _readiness(data, rows)
    candidates = None if problems else _candidate_levels(rows)
    model_input = _model_input(data, rows, edition, candidates,
                               _experience_layers(Path(root), base_date, rows, not problems, version), version)
    input_sha = _sha(model_input)

    if path.exists():
        existing = _read(path)
        return {"outcome": "unchanged" if existing["input_sha256"] == input_sha else "conflict", "record": existing}

    record: dict[str, Any] = {
        "schema": SCHEMA,
        "forecast_id": f"{'replay|' if replay else ''}{base_date}|{edition}|{method_version}|{version}",
        "base_date": base_date, "edition": edition,
        "method_version": method_version, "input_version": version,
        "model": model,
        "run_mode": "replay" if replay else "official" if official_since and base_date >= official_since else "trial",
        "data_cutoffs": _cutoffs(data),
        **{key: copy.deepcopy(data[key]) for key in _RECORD_ONLY if key in data},
        "deadline": deadline.isoformat(),
        "input": model_input, "input_sha256": input_sha,
        "attempts": [],
    }
    if replay:
        record["replay"] = copy.deepcopy(dict(replay))
    if data.get("record_only"):
        # 只存進紀錄、不交給模型（例如來源條款只允許私用的海外資料）；跟著整筆一起封存。
        record["record_only"] = copy.deepcopy(dict(data["record_only"]))

    def finish(kind: str, **fields: Any) -> dict[str, Any]:
        record.update(kind=kind, locked_at=clock().astimezone(TAIPEI).isoformat(), **fields)
        if kind == "forecast" and datetime.fromisoformat(record["locked_at"]) >= deadline:
            for key in ("regime", "cells", "similar_regimes"):
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

    prompt = build_prompt(_PROMPT_RULES, model_input)
    for _ in range(2):
        status, structured, detail = call_model(model_bin, prompt, model, timeout_seconds, OUTPUT_SCHEMA)
        if status != "ok":
            if replay:
                return {"outcome": "model_failed", "record": None, "detail": f"{status}：{detail}"}
            record["attempts"].append({"status": status, "detail": detail})
            return finish("miss", miss_reason=status, miss_detail=detail)
        accepted, problem = _validate(structured, model_input)
        if accepted is not None:
            return finish("forecast", **_with_baselines(accepted, rows, data["trend_labels"]),
                          similar_regimes=model_input["similar_regimes"])
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


# 正式起算日（F 票）：基準日在這一天（含）以後的預測記為正式、之前的是試跑。帳本在寫的當下就鎖定試跑或正式，
# 所以只能訂在還沒寫的日子；本人點頭之前維持 None（全部試跑）。設定之後記進 ANALYSIS_PLAN.md。
OFFICIAL_SINCE: str | None = None
# 呼叫端沒指定時用的記號：在呼叫當下才讀 OFFICIAL_SINCE（不在函式定義時就綁死）。
OFFICIAL_SINCE_DEFAULT = object()


def official_since(value: str | None | object = OFFICIAL_SINCE_DEFAULT) -> str | None:
    return OFFICIAL_SINCE if value is OFFICIAL_SINCE_DEFAULT else value  # type: ignore[return-value]

# 本人定案：正式結論 1 日滿 3 個月、5 日滿 6 個月、20 日滿 1 年；之前只標「累積中」。
VERDICT_MONTHS = {1: 3, 5: 6, 20: 12}


def _verdict(run_mode: str, horizon: int, official_since: str | None, as_of: str | None) -> str:
    """「試跑」不下結論；正式紀錄在正式起算日之後滿規定時間才「可下結論」，之前一律「累積中」。
    歷史重播只是參考成績，永遠不下結論。"""
    if run_mode == "replay":
        return "歷史重播（參考）"
    if run_mode != "official":
        return "試跑"
    if not official_since or not as_of:
        return "累積中"
    due = _add_months(date.fromisoformat(official_since), VERDICT_MONTHS[horizon])
    return "可下結論" if date.fromisoformat(as_of) >= due else "累積中"


# F 票：「明顯比照歷史亂猜準」的判定，在第一筆正式紀錄之前寫定，之後不因結果好看或難看而改。
# 白話版與本人的回覆記在 ANALYSIS_PLAN.md〈事前預測紀錄＋到期對帳〉。
JUDGEMENT: dict[str, Any] = {
    # 同時判 6 組（晚間／晨間 × 1／5／20 日），門檻用單邊 1% 而不是 5%，免得六組裡碰巧一組過關。
    "z_threshold": 2.326,
    # 漏記超過一成，那一組不下結論。
    "max_miss_share": 0.1,
}


def judge(outcomes: Sequence[tuple[bool, float]], *, horizon: int, misses: int = 0) -> dict[str, Any]:
    """判定一組正式紀錄是否明顯比照歷史亂猜準。

    ``outcomes`` 是每一格的（猜中與否, 寫下當時鎖定的那一區歷史比例 p）；``misses`` 是到期日已過的漏記，一律算猜錯，
    p 用這一組各格的平均（沒有任何格時用三分之一）。期間 5 日、20 日相鄰的紀錄看的幾乎是同一段行情，
    判斷是不是運氣時樣本數除以期間天數（變異數乘上期間天數）。資料缺漏、無法判定的格子不放進來。
    """
    shares = [share for _, share in outcomes]
    miss_share = misses / (len(outcomes) + misses) if outcomes or misses else 0.0
    average = sum(shares) / len(shares) if shares else 1 / 3
    cells = [(bool(hit), share) for hit, share in outcomes] + [(False, average)] * misses
    hits = sum(hit for hit, _ in cells)
    expected = sum(share for _, share in cells)
    variance = sum(share * (1 - share) for _, share in cells) * horizon
    result = {"hits": hits, "expected_hits": round(expected, 4), "cells": len(cells), "misses": misses,
              "effective_samples": len(cells) / horizon, "z": None}
    if miss_share > JUDGEMENT["max_miss_share"]:
        return {**result, "conclusion": "漏記太多"}
    if variance <= 0:
        return {**result, "conclusion": "沒有明顯差別"}
    z = (hits - expected) / math.sqrt(variance)
    threshold = JUDGEMENT["z_threshold"]
    conclusion = "明顯比亂猜準" if z >= threshold else "明顯比亂猜差" if z <= -threshold else "沒有明顯差別"
    return {**result, "z": round(z, 4), "conclusion": conclusion}


def summarize(root: Path | str, index_daily: Sequence[Mapping[str, Any]], *,
              official_since: str | None = None) -> dict[str, Any]:
    """依（試跑／正式、版本、期間、方法版本、輸入版本）分組；試跑與正式各自成組，不混算。

    還沒對帳的格子依截至目前的指數日 K 分成「還沒到期」與「無法判定」，兩者都不算猜錯。
    每組另標 ``verdict``：試跑／累積中／可下結論（只是顯示，不觸發任何其他功能）。
    """
    ledger = read_ledger(root)
    rows = _clean_daily(index_daily)
    as_of = rows[-1]["date"] if rows else None
    position = {row["date"]: index for index, row in enumerate(rows)}
    reconciled_by_cell = {(row["forecast_id"], row["horizon"]): row for row in ledger["reconciliations"]}
    series: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for record in ledger["forecasts"]:
        key = (record["run_mode"], record["edition"], record["method_version"], record["input_version"])
        bucket = series.setdefault(key, {"misses": [], "forecasts": [], "models": set()})
        bucket["models"].add(str(record.get("model")))
        if record["kind"] == "miss":
            bucket["misses"].append(record)
        else:
            bucket["forecasts"].append(record)
    groups = []
    for (run_mode, edition, method_version, version), bucket in sorted(series.items()):
        # 每一組從自己的第一筆正式紀錄起算（起算後換方法或輸入版本的新一組，不沿用舊組已累積的時間）。
        first = min((record["base_date"] for record in bucket["forecasts"] + bucket["misses"]), default=None)
        since = max(official_since, first) if official_since and first else official_since
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
            verdict = _verdict(run_mode, horizon, since, as_of)
            judgement = None
            if verdict == "可下結論":
                # 漏記只在它的到期日已過時才算一次猜錯（還沒到期的漏記和還沒到期的預測一樣不算）。
                misses_due = sum(_expiry(position, rows, miss["base_date"], horizon)[0] != "pending"
                                 for miss in bucket["misses"])
                judgement = judge([(cell["zone"] == outcome["actual_zone"],
                                    cell["baseline"][cell["zone"]] / cell["baseline"]["samples"])
                                   for cell, outcome in reconciled if cell["baseline"]["samples"]],
                                  horizon=horizon, misses=misses_due)
            groups.append({
                "run_mode": run_mode, "edition": edition, "horizon": horizon,
                "method_version": method_version, "input_version": version,
                # 輸入版本含模型，同一組只會有一個；網頁靠它標出舊模型的系列（例如「Flash 舊版（參考）」）。
                "model": "／".join(sorted(bucket["models"])),
                **_rates(reconciled), **open_cells, "misses": len(bucket["misses"]),
                "pullback": _rates(pullback),
                "verdict": verdict, "judgement": judgement,
            })
    return {"as_of": as_of, "official_since": official_since, "groups": groups, "conflicts": len(ledger["conflicts"])}


# ---- 發布到網站讀的資料庫 ----

# 給網站顯示的欄位；完整輸入（含方法稿）與模型的重試細節只留在原件。
_PUBLIC_FIELDS = ("forecast_id", "base_date", "edition", "method_version", "input_version", "model", "run_mode",
                  "kind", "locked_at", "deadline", "data_cutoffs", "regime", "cells", "miss_reason", "miss_detail",
                  "similar_regimes", "overseas_status", "missing_inputs", "record_sha256")


def d1_sql(root: Path | str, index_daily: Sequence[Mapping[str, Any]], *, published_at: str,
           official_since: str | None = None, replay: bool = False) -> str:
    """把帳本整理成給網站資料庫的指令檔。只新增：同一筆再發布一次不會多一筆、也不會改內容。

    不包 BEGIN／COMMIT：部署工具（wrangler d1 execute）自己會包交易。
    ``replay``：重播帳本寫進另外三張 ``forecast_replay_*`` 表（分開存、分開顯示）。
    """
    table = "forecast_replay_{}" if replay else "forecast_{}"
    ledger = read_ledger(root)
    statements = []
    for record in ledger["forecasts"]:
        public = {key: record[key] for key in _PUBLIC_FIELDS if key in record}
        statements.append(_insert(table.format("records"), {
            "forecast_id": record["forecast_id"], "base_date": record["base_date"], "edition": record["edition"],
            "method_version": record["method_version"], "input_version": record["input_version"],
            "run_mode": record["run_mode"], "kind": record["kind"], "record_json": _json(public),
            "record_sha256": record["record_sha256"], "published_at": published_at,
        }, "forecast_id"))
    for row in ledger["reconciliations"]:
        statements.append(_insert(table.format("reconciliations"), {
            "forecast_id": row["forecast_id"], "horizon": row["horizon"], "expiry_date": row["expiry_date"],
            "actual_zone": row["actual_zone"], "record_json": _json(row), "record_sha256": row["record_sha256"],
            "published_at": published_at,
        }, "forecast_id,horizon"))
    summary = summarize(root, index_daily, official_since=official_since)
    if summary["as_of"]:
        statements.append(_insert(table.format("summaries"), {
            "summary_sha256": _sha(summary), "as_of": summary["as_of"], "summary_json": _json(summary),
            "published_at": published_at,
        }, "summary_sha256"))
    return "\n".join(statements) + "\n"


# ---- 讀帳本 ----

def read_ledger(root: Path | str) -> dict[str, list[dict[str, Any]]]:
    return read_folders(root, ("forecasts", "reconciliations", "conflicts"))
