"""關注帳本：關注股的每日判斷，寫下鎖定、到期對帳、彙總成績。

規格：``.scratch/watchlist-watch/spec.md``。和預測帳本同一個形狀，鎖定、雜湊、模型呼叫、期限、方法版本
共用 ``ledger_core``。對外只有：

- ``write_judgment``：給一檔關注股的輸入資料與一個模型命令列程式，得到一筆鎖定的判斷或一筆鎖定的漏記。
- ``write_judgments``：依固定順序替一批關注股各寫一份；額度用完時後面的檔記漏記並寫明原因。
- ``reconcile``：給帳本、截至某一天的指數日 K 與各檔個股日 K，替到期的轉變日新增對帳紀錄。
- ``summarize``：把帳本整理成成績摘要（全部關注股合計與單一檔；贏大盤、隨便挑、還沒到期、漏記）。
- ``d1_sql``：把帳本（判斷、對帳、成績摘要）整理成寫入網站資料庫的指令檔；重複發布不多一筆、不改內容。
- ``read_ledger``：讀出全部紀錄，順便核對封存雜湊（原件被改動時丟 ``LedgerIntegrityError``）。
- ``input_version_of``：打開某幾層輸入時的輸入版本。

個股候選關卡、回答驗證、兩條線的規則、轉變日、到期日、「隨便挑」都封在這裡。帳本只能新增：
一檔、一個基準日、一版（晚間／晨間）只會有一筆。個股候選關卡沿用個股頁的支撐壓力計算
（``twon_analysis``），以還原後的價格為準；只讀不改，在這裡包一層。
"""
from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime
from fractions import Fraction
from pathlib import Path
from typing import Any, NamedTuple

from ledger_core import exists as ledger_exists
from ledger_core import (
    EDITIONS,
    REFS_LIMIT,
    TAIPEI,
    TEXT_LIMIT,
    LedgerIntegrityError,  # noqa: F401  （對外沿用同一個例外）
    add_months,
    append,
    build_prompt,
    call_model,
    clean_daily,
    compact_json,
    deadline,
    default_now,
    label_date,
    method_version_of,
    read_folders,
    read_record,
    refs_problem,
    render_refs,
    seal,
    sha,
    sql_insert,
    text_problem,
)
import bollinger
import gemini_models
import licensed_inputs
import watch_inputs
import wyckoff_lpsy
from twon_analysis import analyze_v2
from twon_analysis import signals as stock_signals
from twon_analysis.indicators import Bar
from twon_analysis.price_adjustment import adjust_bars, price_factors

SCHEMA = "watch-ledger/v1"
STATUSES = ("還沒到", "接近", "可以留意")
READY = "可以留意"
# 對帳的期間：轉變日之後第 5、第 20 個台股交易日。
HORIZONS = (5, 20)

# 輸入分層（規格〈交給 Gemini 的輸入〉）。排程打開哪幾層由呼叫端給；打開的層算進輸入版本。
# bollinger、sector、margin、environment 是 I 票（2026-11-16 本人定案）多加的四樣，每一樣各自一層，
# 好讓輸入版本照實寫出多了哪幾樣；規格把前三樣歸在技術層、匯率與美債利率歸在環境層。
# wyckoff_lpsy 是 H 票（本人 2026-11-18 追加「要，也加進去」）：這一檔今天是否處在威科夫最後供應點，由呼叫端用
# wyckoff_lpsy（和大盤事前預測共用的判斷）算好，含息還原價要逐日漲跌，所以不是帳本從日 K 自己算。
# valuation、lending_eps、signals 是 I 票第二輪（本人 2026-11-18 晚「更多整合所有網站可以獲取的資料」→
# 「大盤＋自選股判斷」）：估值、借券與季 EPS、個股五塊訊號，由呼叫端（watch_run.LocalSource）從工作集算好。
# J 票（本人 2026-11-19 00:1x「富果、新聞來源、國外同類股都交給 Gemini 做詳細分析」）：intraday（富果分時、均價線、
# 五檔、內外盤比、60 分 K）、headlines（新聞標題與摘要）、peers（國外同類股）三層，由呼叫端用 licensed_inputs 整理好。
LAYERS = ("technical", "bollinger", "wyckoff_lpsy", "sector", "margin", "valuation", "lending_eps", "signals",
          "intraday", "environment", "market", "news", "headlines", "peers", "lessons")
DEFAULT_LAYERS = ("technical", "market", "news")
# 每一層在一檔輸入資料裡的欄位。消息層這一版只收官方公告（重大訊息、月營收）。布林通道由帳本從日 K 自己算，沒有欄位。
# 教訓層沒有欄位：由帳本自己從過去的判斷與對帳整理（只放事實），呼叫端給的內容不用。
_LAYER_FIELDS = {"technical": "technical", "wyckoff_lpsy": "wyckoff_lpsy", "sector": "sector", "margin": "margin",
                 "valuation": "valuation", "lending_eps": "lending_eps", "signals": "signals",
                 "intraday": "intraday", "headlines": "news_headlines", "peers": "peers",
                 "environment": "environment", "market": "market_forecast", "news": "announcements"}
# I 票四層各自的參數與提示規則：只有打開那一層時才算進輸入版本與提示，沒打開時舊版本一個字都不變。
_LAYER_PARAMETERS: dict[str, dict[str, Any]] = {
    "bollinger": {"period": bollinger.PERIOD, "deviations": bollinger.DEVIATIONS,
                  "squeeze_window": bollinger.SQUEEZE_WINDOW, "squeeze_share": bollinger.SQUEEZE_SHARE},
    "wyckoff_lpsy": {"layer": wyckoff_lpsy.STOCK_VERSION},
    "sector": {"classification": "官方產業別", "changes": ["當天", "近 5 日"]},
    "margin": {"changes": "近 5 日"},
    # I 票第二輪三層：數字從讀取那一側的常數帶出，改了常數就換輸入版本。
    "valuation": {"metrics": list(watch_inputs.VALUATION_METRICS), "range_days": watch_inputs.VALUATION_YEAR_DAYS},
    "lending_eps": {"lending_compare_rows": watch_inputs.LENDING_DAYS, "eps_quarters": watch_inputs.EPS_QUARTERS,
                    "eps_basis": "年初至今累計換算單季"},
    "signals": {"blocks": list(stock_signals.BLOCK_NAMES), "dropped": ["heavy_zones.chart"]},
    # J 票三層：整理方式與說明都在 licensed_inputs.PARAMETERS（說明改一個字也換版本）。
    "intraday": {"layer": licensed_inputs.VERSION, "parts": ["summary", "bars_10min", "hourly", "book"],
                 "parameters": licensed_inputs.PARAMETERS},
    "headlines": {"layer": licensed_inputs.VERSION, "fields": ["id", "source", "published_at", "title", "summary"],
                  "parameters": licensed_inputs.PARAMETERS},
    "peers": {"layer": licensed_inputs.VERSION, "changes": ["1 日", "5 日"], "parameters": licensed_inputs.PARAMETERS},
    "environment": {"changes": "近 20 日", "series": ["usdtwd", "ust_2y", "ust_10y"]},
}
_LAYER_RULES: dict[str, tuple[str, ...]] = {
    "lessons": (
        "lessons 是程式從帳本整理的這一檔過去事實：每一次變成「可以留意」的基準日（transitions）、當時的觸發線與看錯線，"
        "以及之後第五、第二十個交易日有沒有贏過同期加權指數（results；null 是到基準日為止還沒有結果）。"
        "寫之前先看這些，避免同一種錯一犯再犯；只能當成過去的事實引用，不得自己推算沒給的結果。",
    ),
    "bollinger": (
        "bollinger 是這一檔收盤在布林通道（20 日均價 ± 2 個標準差）的位置：zone 是收盤落在哪一區，"
        "squeezed 是通道寬度是否在近 120 個交易日最窄的兩成（null 是資料不足）；上緣與下緣也在 candidates 裡"
        "（boll_upper、boll_lower），挑選規則和其他候選相同。"
        "布林通道只是位置描述；單獨當買賣規則在歷史上贏不過買了放著，不可只憑它判斷。",
    ),
    "wyckoff_lpsy": (
        "wyckoff_lpsy 是程式算好的「這一檔今天是否處在威科夫最後供應點」（yes／no／undeterminable，跌破整理區之後量縮、"
        "碰不到原整理區低點的一天反彈）與固定的個股歷史統計，只能照讀，不可自己重算或改數字；undeterminable 當作沒有。"
        "它是避開記號，不是放空訊號：歷史上之後二十個交易日平均比同日選股池少賺約一個百分點，扣掉賣出成本後好處只剩一半；"
        "可以當成「條件還沒到」的參考之一，但不能單獨決定 status，理由裡也不得寫成賣出、放空或之後會跌；no 也不代表看多。"
        # 本人 2026-11-18 看過乾跑後定案收緊（大盤、個股兩邊都加）。
        "理由裡提到它時只能照寫是或否與歷史次數，不得拿來推論賣壓、套牢、壓力位置或之後的方向。",
    ),
    "sector": (
        "sector 是官方產業別相同、在網站分析範圍內的其他個股當天與近 5 日的漲跌（百分比）與全組平均；"
        "data_date 是資料日期。只能當成同產業的對照，不是這一檔自己的走勢。",
    ),
    "margin": (
        "margin 是這一檔的融資與融券餘額（張）與近 5 個交易日的變化；data_date 是資料日期。",
    ),
    "valuation": (
        "valuation 是這一檔的本益比（pe）、股價淨值比（pb）、殖利率（dividend_yield_pct）最新一筆，和近一年的最低、最高與"
        "位置（position_pct：0 是一年最低、100 是一年最高）；value 是 null 代表沒有（例如虧損沒有本益比）。"
        "估值只描述現在比過去一年貴或便宜：位置低不代表便宜、會漲，位置高也不代表會跌，不能單獨決定 status。",
    ),
    "lending_eps": (
        "lending_eps.lending 是這一檔的借券賣出餘額（張）與往前第 5 筆的變化；借券增加可能是避險或套利，"
        "不代表一定看空，不得單獨寫成賣壓。lending_eps.eps 是近 4 季的每股盈餘：eps_ytd 是年初至今累計、"
        "eps_quarter 是換算的單季（null 是算不出來），available_on 是法定公布期限；只能照讀，不得自己推估下一季。",
    ),
    "signals": (
        "signals 是網站個股頁的五塊資料（程式算好）：limit_up_momentum 是最近有多會漲停（分數與級距的歷史比例）、"
        "versus_market 是近 5／20／60 日和加權指數比的差距（百分點）、ma_alignment 是均線排列、"
        "margin_maintenance 是融資維持率（估）的範圍（估計值，不是官方數字）、heavy_zones 是爆大量成交區與收盤的相對位置。"
        "沒有的塊代表算不出來，不得猜。這些都只是描述，不是買賣訊號，不能單獨決定 status。",
    ),
    "intraday": (
        "intraday 是富果的這一檔基準日盤中資料（程式整理好，只能照讀）：" + licensed_inputs.NOTES["stock_intraday"]
        + "missing 列出的部分是沒抓到的，不得猜。",
    ),
    "headlines": (
        "news.headlines 是這一檔的新聞：" + licensed_inputs.NOTES["headlines"]
        + "要引用新聞就把它的 id 放進 news_refs（和官方公告一樣），理由裡不要抄標題。",
    ),
    "peers": (
        "peers 是國外同類股：" + licensed_inputs.NOTES["peers"] + "no_data 列出的是沒有資料的代號。",
    ),
    "environment": (
        "environment 是大環境：新臺幣對美元收盤匯率與美國公債 2 年、10 年殖利率，各自附資料日期與近 20 個交易日的變化；"
        "某一項是 null 就是缺資料，不得猜測。",
    ),
}
# 依來源條款留位置、不放內容的資料。11-15 起國外同類股與新聞標題留在這裡；本人 2026-11-19 00:1x「富果、新聞來源、
# 國外同類股都交給 Gemini 做詳細分析」推翻那次的限制，所以現在是空的（紀錄照樣帶這個欄位，舊紀錄看得出差別）。
WITHHELD_INPUTS: dict[str, str] = {}
OFFICIAL_SOURCES = ("twse", "tpex")
_ANNOUNCEMENT_FIELDS = ("id", "source", "kind", "published_at", "title", "body")
_STOCK_KEYS = ("open", "high", "low", "close", "volume")

# 在第一筆紀錄之前寫定，整組算進輸入版本，改了就是新系列。
PARAMETERS: dict[str, Any] = {
    "recent_bars_for_model": 60,
    "min_bars": analyze_v2.MIN_ANALYSIS_BARS,
    # 個股候選關卡沿用個股頁的支撐壓力計算；那一套改版時這裡跟著換輸入版本。
    "candidate_source": analyze_v2.ANALYSIS_VERSION,
    "official_sources": list(OFFICIAL_SOURCES),
    "horizons": list(HORIZONS),
}

_REFS_SCHEMA = {"type": "array", "maxItems": REFS_LIMIT, "items": {"type": "string"}, "uniqueItems": True}
OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["base_date", "code", "status", "trigger_line", "wrong_line", "reason", "reason_refs", "news_refs"],
    "properties": {
        "base_date": {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}$"},
        "code": {"type": "string"},
        "status": {"type": "string", "enum": list(STATUSES)},
        "trigger_line": {"type": "string"}, "wrong_line": {"type": "string"},
        "reason": {"type": "string", "minLength": 1, "maxLength": TEXT_LIMIT},
        "reason_refs": _REFS_SCHEMA, "news_refs": _REFS_SCHEMA,
    },
}

_PROMPT_RULES = (
    "你是台股個股分析員。只根據 INPUT_JSON 與方法稿做判斷，不可連網、不可抓資料、不可使用工具。",
    "替 INPUT_JSON 的這一檔關注股寫一份判斷。這是給站主看的「條件到了沒」的提醒與依據，不是買賣建議；"
    "不寫目標價、停損價、部位或任何下單指示。",
    f"status 從固定清單選一個：{'、'.join(STATUSES)}。",
    "trigger_line（觸發線：要站上才算數的價位）與 wrong_line（看錯線：跌破代表這次看錯）都只能從 candidates 挑一個 id，"
    "不可自己寫價位。",
    "status 是「還沒到」或「接近」時，trigger_line 只能挑 side=above 的候選（在基準日收盤上方）；"
    "status 是「可以留意」時，trigger_line 是已經站上的那一條，只能挑 side=at_or_below 的候選。"
    "wrong_line 的數值一律要低於 trigger_line。candidates 裡找不到符合的價位時，不能選那個 status。",
    "reason（理由）只寫繁體中文質化判斷，不得出現任何阿拉伯／全形／中文數字、日期或價格；"
    "要引用價位就把候選 id 放進 reason_refs，要引用消息就把 news.announcements 裡的 id 放進 news_refs，"
    "程式會代入名字與數值。"
    # 本人 2026-11-18 晚「放寬檢查（推薦）」：和大盤預測同一套白名單（ledger_core.text_problem）。
    "唯一的例外：照抄 candidates 的 name（例如五日線、前一交易日高點）或方法稿原文裡的用詞（例如兩個頻道、第一頻道）時，名字裡本來就有的數字可以寫，但要一字不差照抄；名字旁邊不得再加任何數字（例如價位、幾成、幾天），也不得寫 candidates 與方法稿裡沒有的線名或自己縮寫的詞。",
    "理由只能引用 INPUT_JSON 裡有的資料；INPUT_JSON 沒給的資料（missing_layers 列出的層、值是 null 的欄位、"
    "新聞、海外市場）不得當成事實寫進去。",
    "每一次從別的狀態變成「可以留意」，程式會在之後第五與第二十個交易日對帳：這一檔的漲跌幅有沒有贏過同期加權指數，"
    "並和「隨便挑一天買這一檔」的比例並排。",
    "base_date 與 code 照抄 INPUT_JSON。",
)


def _enabled_layers(layers: Sequence[str]) -> list[str]:
    unknown = [layer for layer in layers if layer not in LAYERS]
    if unknown:
        raise ValueError(f"不認得的輸入層：{unknown}")
    withheld = [f"{layer}（{WITHHELD_INPUTS[layer]}）" for layer in layers if layer in WITHHELD_INPUTS]
    if withheld:
        raise ValueError(f"這一版不能打開的輸入層：{'、'.join(withheld)}")
    if "technical" not in layers:
        raise ValueError("技術層是必要的輸入層")
    return [layer for layer in LAYERS if layer in layers]


def _layer_parameters(enabled: Sequence[str]) -> dict[str, Any]:
    return {layer: _LAYER_PARAMETERS[layer] for layer in enabled if layer in _LAYER_PARAMETERS}


_NOT_GIVEN_RULE = next(rule for rule in _PROMPT_RULES if rule.startswith("理由只能引用 INPUT_JSON 裡有的資料"))
# J 票：打開新聞或國外同類股時，「沒給的資料」那一句改成只指沒給的那些（沒打開時一個字都不變，舊版本照舊）。
_NOT_GIVEN_RULE_WITH_LICENSED = (
    "理由只能引用 INPUT_JSON 裡有的資料；INPUT_JSON 沒給的資料（missing_layers 列出的層、值是 null 的欄位、"
    "news.headlines 以外的新聞、peers 以外的海外市場）不得當成事實寫進去。")


def _prompt_rules(enabled: Sequence[str]) -> tuple[str, ...]:
    base = _PROMPT_RULES
    if "headlines" in enabled or "peers" in enabled:
        base = tuple(_NOT_GIVEN_RULE_WITH_LICENSED if rule == _NOT_GIVEN_RULE else rule for rule in _PROMPT_RULES)
    return base + tuple(rule for layer in enabled for rule in _LAYER_RULES.get(layer, ()))


# 換成這些模型就是新系列（模型算進版本雜湊）。Flash 時代的版本號不帶模型、維持原樣；本人 11-18 晚每日判斷改 Argon。
_MODEL_IN_VERSION = (gemini_models.ARGON,)


# 本人 2026-11-19 晚（設定頁 AI 模型第 5 題）「換思考強度也另開一組」。11-19 以前判斷實際都以 medium 呼叫
# （gemini_models.WATCH_JUDGE_EFFORT 當時沒有接到判斷上），所以 medium 不算進雜湊、版本號照舊，其他強度另成一組。
DEFAULT_EFFORT = "medium"


def input_version_of(layers: Sequence[str] = DEFAULT_LAYERS, model: str | None = None,
                     effort: str = DEFAULT_EFFORT) -> str:
    """輸入版本：打開的層、參數、回答格式、提示規則、模型、思考強度任何一項改了就是新系列，和舊系列分開對帳。

    某一層當天沒有資料不換版本（那一份判斷另外標 ``missing_layers``）；版本只看排程打開了哪幾層。
    I 票的四層只在打開時才加進雜湊，所以沒打開它們的版本和 I 票之前相同。
    """
    enabled = _enabled_layers(layers)
    basis: dict[str, Any] = {"layers": enabled, "parameters": PARAMETERS, "output_schema": OUTPUT_SCHEMA,
                             "prompt_rules": _prompt_rules(enabled)}
    if model in _MODEL_IN_VERSION:
        basis["model"] = model
    if effort != DEFAULT_EFFORT:
        basis["effort"] = effort
    layer_parameters = _layer_parameters(enabled)
    if layer_parameters:
        basis["layer_parameters"] = layer_parameters
    return f"{'+'.join(enabled)}-v1-{sha(basis)[:8]}"


# ---- 個股日 K 與候選關卡 ----

def _raw_bars(stock_daily: Any) -> list[Bar]:
    """實際成交價的日 K；開高低收不齊的那一天（停牌、缺漏）不入列。"""
    rows = clean_daily(stock_daily or [], name="stock_daily", keys=_STOCK_KEYS)
    return [Bar(row["date"], row["open"], row["high"], row["low"], row["close"], int(row["volume"] or 0))
            for row in rows if None not in (row["open"], row["high"], row["low"], row["close"])]


def _capital_events(events: Any) -> list[dict[str, Any]]:
    """股本變動事件（``price_adjustment.capital_events`` 的形狀）；形狀不對的在這裡就擋下。"""
    out = [dict(event) if isinstance(event, Mapping) else None for event in events or []]
    if any(event is None or not event.get("date") or not event.get("type") for event in out):
        raise ValueError("capital_events 的每一筆都要有 date 與 type")
    return out


def _restored(raw: list[Bar], events: list[dict[str, Any]]) -> tuple[list[Bar], list[dict[str, Any]]]:
    """（還原後的日 K, 該還原卻缺官方數字的事件），和個股頁同一套。

    分割、減資、配股已還原；現金股利不還原。缺官方數字的事件不猜係數，那一段維持實際成交價。
    """
    bars, unresolved = adjust_bars(raw, events)
    return bars, [{"date": event["date"], "type": event["type"], "reason": event["unresolved_reason"]}
                  for event in unresolved]


def _candidate_levels(bars: list[Bar]) -> list[dict[str, Any]]:
    """個股頁支撐壓力計算列出的每一個價位，各給識別碼、名字、數值、種類，並標出在基準日收盤的哪一側。"""
    close, base_date = bars[-1].close, bars[-1].date
    levels = []
    for factor in analyze_v2._candidates(bars, analyze_v2.indicators_v2(bars)):
        day = factor.get("date")
        levels.append({
            "id": f"{factor['kind']}_{day.replace('-', '')}" if day else factor["kind"],
            "name": f"{factor['label']}（{label_date(day, base_date)}）" if day else factor["label"],
            "kind": factor["kind"], "value": factor["value"],
            "side": "above" if factor["value"] > close else "at_or_below",
            **({"date": day} if day else {}),
        })
    return sorted(levels, key=lambda level: (-level["value"], level["id"]))


BOLLINGER_SOURCE = "本站以還原後日 K 自行計算（20 日均價 ± 2 個標準差）"


def _bollinger(bars: list[Bar]) -> dict[str, Any] | None:
    """這一檔收盤在布林通道的位置（還原後的日 K，和個股頁圖上同算法）。"""
    found = bollinger.position([b.date for b in bars], [b.close for b in bars])
    return None if found is None else {**found, "source": BOLLINGER_SOURCE}


def _bollinger_levels(band: Mapping[str, Any] | None, close: float) -> list[dict[str, Any]]:
    """布林上緣、下緣當成兩個有名字的候選關卡。"""
    if band is None:
        return []
    return [{"id": f"boll_{edge}", "name": name, "kind": f"bollinger_{edge}", "value": band[edge],
             "side": "above" if band[edge] > close else "at_or_below"}
            for edge, name in (("upper", "布林上緣"), ("lower", "布林下緣"))]


# ---- 輸入 ----

def _readiness(data: Mapping[str, Any], bars: list[Bar], index_rows: list[dict[str, Any]]) -> list[str]:
    base_date = data.get("base_date")
    problems = []
    if not bars or bars[-1].date != base_date:
        problems.append("stock_daily 最後一根不是基準日收盤")
    if len(bars) < PARAMETERS["min_bars"]:
        problems.append(f"stock_daily 有效日 K 不足 {PARAMETERS['min_bars']} 根")
    if not index_rows or index_rows[-1]["date"] != base_date or index_rows[-1]["close"] is None:
        problems.append("index_daily 最後一根不是基準日收盤")
    if not isinstance(data.get("technical"), Mapping) or not data.get("technical"):
        problems.append("技術層缺漏")
    return problems


def _judgments_folder(root: Path) -> Path:
    return root / "judgments"


def _judgment_path(root: Path, code: str, base_date: str, edition: str) -> Path:
    """一檔、一個基準日、一版只有一個檔案；方法或輸入改版也不會多出第二筆。"""
    if not re.fullmatch(r"[0-9A-Za-z]{1,12}", code):
        raise ValueError(f"代號不合法：{code!r}")
    date.fromisoformat(base_date)
    return _judgments_folder(root) / f"{base_date}__{edition}__{code}.json"


def _announcements(items: Any) -> list[dict[str, Any]] | None:
    """消息層這一版只收官方公告；新聞標題不在這裡，放不進提示。"""
    if items is None:
        return None
    out = []
    for item in items:
        if not isinstance(item, Mapping) or item.get("source") not in OFFICIAL_SOURCES:
            raise ValueError(f"消息層這一版只收官方公告（來源須是 {OFFICIAL_SOURCES} 之一）")
        out.append({key: item[key] for key in _ANNOUNCEMENT_FIELDS if key in item})
    if len({item.get("id") for item in out}) != len(out) or any(not isinstance(item.get("id"), str) for item in out):
        raise ValueError("每一則官方公告都要有不重複的編號")
    return out


def _history(rows: Sequence[Any]) -> dict[str, Any]:
    dates = [row.date if isinstance(row, Bar) else row["date"] for row in rows]
    body = [[row.date, row.open, row.high, row.low, row.close, row.volume] if isinstance(row, Bar) else row
            for row in rows]
    return {"from": dates[0] if dates else None, "to": dates[-1] if dates else None,
            "bars": len(rows), "sha256": sha(body)}


def _lessons(root: Path, code: str, base_date: str) -> dict[str, Any]:
    """教訓層：這一檔在基準日之前的每一個轉變日（晚間、晨間都算），連同到期日不晚於基準日的對帳結果。

    只放事實（兩條線的名字與數值、漲跌幅、有沒有贏大盤），不放模型寫的理由；基準日之後才知道的結果一律 null，
    就算重跑時帳本裡已經有了也一樣，重跑同一天的輸入才會相同。
    """
    ledger = read_ledger(root)
    known = {(row["judgment_id"], row["horizon"]): row for row in ledger["reconciliations"]
             if row["expiry_date"] <= base_date}
    transitions = []
    for judgment in ledger["judgments"]:
        if judgment["code"] != code or not judgment.get("transition") or judgment["base_date"] >= base_date:
            continue
        results = {}
        for horizon in HORIZONS:
            row = known.get((judgment["judgment_id"], horizon))
            results[str(horizon)] = None if row is None else {
                key: row[key] for key in ("expiry_date", "stock_return", "index_return", "beat_index")}
        transitions.append({
            "base_date": judgment["base_date"], "edition": judgment["edition"],
            **{line: {key: judgment[line][key] for key in ("name", "value")} for line in ("trigger_line", "wrong_line")},
            "results": results,
        })
    return {"transitions": transitions}


# 這幾層在輸入裡有固定的位置（news.headlines、peers），不另外放到最上層。
_FIXED_PLACES = ("headlines", "peers")


def _model_input(data: Mapping[str, Any], edition: str, layers: list[str], raw: list[Bar], bars: list[Bar],
                 events: list[dict[str, Any]], unresolved: list[dict[str, Any]],
                 candidates: list[dict[str, Any]] | None, index_rows: list[dict[str, Any]],
                 band: dict[str, Any] | None, lessons: dict[str, Any] | None, model: str | None = None,
                 effort: str = DEFAULT_EFFORT) -> dict[str, Any]:
    values = {layer: copy.deepcopy(data.get(_LAYER_FIELDS[layer])) if layer in _LAYER_FIELDS else None
              for layer in layers}
    if "lessons" in values:
        values["lessons"] = lessons
    if "news" in values:
        values["news"] = _announcements(values["news"])
    if "bollinger" in values:
        values["bollinger"] = band
    # I 票的四層只在打開時才出現在輸入裡，沒打開它們的輸入和 I 票之前相同。
    extras = {layer: values[layer] for layer in layers if layer in _LAYER_PARAMETERS and layer not in _FIXED_PLACES}
    if extras:
        extras = {"layer_parameters": _layer_parameters(layers), **extras}
    return {
        "schema": "watch-input/v1",
        "input_version": input_version_of(layers, model, effort),
        "parameters": copy.deepcopy(PARAMETERS),
        "code": data.get("code"), "name": data.get("name"),
        "base_date": data.get("base_date"), "edition": edition,
        "base_close": bars[-1].close if bars else None,
        "stock_recent": [{"date": b.date, "open": b.open, "high": b.high, "low": b.low, "close": b.close,
                          "volume": b.volume} for b in bars[-PARAMETERS["recent_bars_for_model"]:]],
        "price_basis": {"restored": analyze_v2.LABELS["price_basis"], "unresolved_capital_events": unresolved},
        "candidates": candidates,
        "layers": layers,
        "missing_layers": [layer for layer in layers if values[layer] is None],
        **extras,
        "technical": values.get("technical"),
        "market": values.get("market"),
        # J 票：新聞標題與國外同類股打開時才有內容（沒打開這兩層的輸入和之前相同）。
        "news": {"announcements": values.get("news"), "headlines": values.get("headlines")},
        "peers": values.get("peers"),
        "lessons": values.get("lessons"),
        "data_cutoffs": copy.deepcopy(dict(data.get("data_cutoffs") or {})),
        # 算候選關卡與「隨便挑」用的完整日 K：範圍、根數與雜湊一起鎖定，舊資料被更正時重跑會被擋下。
        "history": {"stock": {**_history(raw), "capital_events": sha(events)}, "index": _history(index_rows)},
        "method_text": data.get("method_text"),
    }


# ---- 回答 ----

def _line(level: Mapping[str, Any]) -> dict[str, Any]:
    return {key: level[key] for key in ("id", "name", "value", "kind")}


def _validate(structured: Any, model_input: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """合格就回傳要鎖進判斷的欄位；不合格回傳原因。兩條線的規則見規格〈兩條線的規則〉。"""
    if not isinstance(structured, dict) or set(structured) != set(OUTPUT_SCHEMA["required"]):
        return None, "回答缺欄位或含未允許欄位"
    if structured["base_date"] != model_input["base_date"] or structured["code"] != model_input["code"]:
        return None, "回答的基準日或代號不符"
    status = structured["status"]
    if status not in STATUSES:
        return None, "狀態不在清單內"
    by_id = {level["id"]: level for level in model_input["candidates"]}
    trigger, wrong = by_id.get(structured["trigger_line"]), by_id.get(structured["wrong_line"])
    if trigger is None or wrong is None:
        return None, "觸發線或看錯線不是候選關卡"
    if trigger["side"] != ("at_or_below" if status == READY else "above"):
        return None, "觸發線放錯邊：「可以留意」要挑已經站上的，其餘要挑收盤上方的"
    if wrong["value"] >= trigger["value"]:
        return None, "看錯線不在觸發線下方"
    # 數字白名單：當份完整提示（規則＋輸入，含方法稿），和大盤預測同一套（owner 11-19 轉本人「放寬檢查」）。
    corpus = "\n".join(_prompt_rules(model_input["layers"])) + "\n" + json.dumps(model_input, ensure_ascii=False)
    problem = text_problem(structured["reason"], names=[level["name"] for level in model_input["candidates"] or []],
                           method_text=corpus)
    if problem:
        return None, f"理由：{problem}"
    if refs_problem(structured["reason_refs"], by_id):
        return None, "理由引用了不存在的候選編號"
    news = {item["id"]: item for item in [*(model_input["news"]["announcements"] or []),
                                          *(model_input["news"]["headlines"] or [])]}
    if refs_problem(structured["news_refs"], news):
        return None, "理由引用了輸入裡沒有的消息編號"
    return {
        "status": status,
        "trigger_line": _line(trigger),
        "wrong_line": _line(wrong),
        "reason": render_refs(structured["reason"], structured["reason_refs"], by_id, "元"),
        "reason_refs": list(structured["reason_refs"]),
        "reason_news": [{key: news[ref].get(key) for key in ("id", "title", "source", "published_at")}
                        for ref in structured["news_refs"]],
    }, None


# ---- 贏大盤、「隨便挑」、轉變日 ----

def _exact(value: float) -> Fraction:
    return Fraction(str(value))


def _beats(stock_start: float, stock_end: float, index_start: float, index_end: float) -> bool:
    """個股漲跌幅大於加權指數同期漲跌幅才算贏；剛好相同不算。用分數比，不受浮點誤差影響。"""
    return _exact(stock_end) / _exact(stock_start) > _exact(index_end) / _exact(index_start)


def _random_pick(bars: list[Bar], index_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """隨便挑：不靠任何判斷，在這一檔可得的歷史上任一個交易日買進，隔 5、20 個交易日後贏大盤的有幾次。

    交易日以指數日 K 為準；起點或終點那天個股沒有日 K（停牌）的樣本不算。
    """
    close_on = {b.date: b.close for b in bars}
    out: dict[str, Any] = {}
    for horizon in HORIZONS:
        wins = samples = 0
        first = last = None
        for index in range(len(index_rows) - horizon):
            start, end = index_rows[index], index_rows[index + horizon]
            stock_start, stock_end = close_on.get(start["date"]), close_on.get(end["date"])
            if not stock_start or stock_end is None or not start["close"] or end["close"] is None:
                continue
            samples += 1
            wins += _beats(stock_start, stock_end, start["close"], end["close"])
            first = first or start["date"]
            last = end["date"]
        out[str(horizon)] = {"wins": wins, "samples": samples, "from": first, "to": last}
    return out


def _previous_judgment(root: Path, code: str, edition: str, base_date: str) -> dict[str, Any] | None:
    """同一檔、同一版（晚間／晨間）的系列裡，這個基準日之前最近一份有判斷的；漏記跳過。

    系列不分方法版本、輸入版本與試跑／正式：改版那一天還是「可以留意」不算從別的狀態變過來。
    """
    folder = _judgments_folder(root)
    earlier = [record for record in map(read_record, sorted(folder.glob(f"*__{edition}__{code}.json")))
               if record["kind"] == "judgment" and record["base_date"] < base_date] if folder.is_dir() else []
    return earlier[-1] if earlier else None


def _transition(status: str, previous: Mapping[str, Any] | None) -> bool:
    """轉變日：這一份是「可以留意」，而前一份有判斷的不是（或根本沒有前一份）。連續的「可以留意」只算第一份。"""
    return status == READY and (previous is None or previous["status"] != READY)


# ---- 寫一份判斷 ----

# 模型錯誤輸出裡代表「額度用完」的字樣；看到之後同一批後面的檔不再呼叫，直接記漏記並寫明原因。
_QUOTA_PATTERN = re.compile(r"quota|resource[_ ]exhausted|rate.?limit|\b429\b|額度", re.IGNORECASE)


class _CallGate:
    """一批判斷共用的模型呼叫額度：次數用完或模型回報額度用完之後，不再放行。"""

    def __init__(self, budget: int | None = None):
        self.left = budget
        self.blocked: str | None = None

    def refusal(self) -> str | None:
        if self.blocked:
            return f"同一批前面的呼叫已回報額度用完：{self.blocked}"
        if self.left is not None and self.left <= 0:
            return "這一批的模型呼叫額度已用完"
        return None

    def spent(self, status: str, detail: str) -> None:
        if self.left is not None:
            self.left -= 1
        if status == "model_failed" and _QUOTA_PATTERN.search(detail):
            self.blocked = detail


def _write(
    root: Path | str,
    data: Mapping[str, Any],
    gate: _CallGate,
    *,
    edition: str,
    model_bin: Path | str,
    model: str,
    effort: str = DEFAULT_EFFORT,
    now: Callable[[], datetime] | None,
    official_since: str | None,
    session_date: str | None,
    layers: Sequence[str],
    timeout_seconds: float,
) -> dict[str, Any]:
    if edition not in EDITIONS:
        raise ValueError(f"edition 必須是 {EDITIONS} 之一")
    clock = now or default_now
    folder = Path(root)
    enabled = _enabled_layers(layers)
    code, base_date = str(data.get("code")), str(data.get("base_date"))
    method_version = method_version_of(str(data.get("method_text") or ""))
    input_version = input_version_of(enabled, model, effort)
    due = deadline(edition, base_date, session_date)
    raw = _raw_bars(data.get("stock_daily"))
    events = _capital_events(data.get("capital_events"))
    index_rows = clean_daily(data.get("index_daily") or [], keys=("close",))
    bars, unresolved = _restored(raw, events)
    problems = _readiness(data, bars, index_rows)
    band = _bollinger(bars) if "bollinger" in enabled and not problems else None
    candidates = None if problems else sorted(
        _candidate_levels(bars) + _bollinger_levels(band, bars[-1].close),
        key=lambda level: (-level["value"], level["id"]))
    lessons = _lessons(folder, code, base_date) if "lessons" in enabled else None
    model_input = _model_input(data, edition, enabled, raw, bars, events, unresolved, candidates, index_rows, band,
                               lessons, model, effort)
    input_sha = sha(model_input)
    path = _judgment_path(folder, code, base_date, edition)

    if ledger_exists(path):
        existing = read_record(path)
        return {"outcome": "unchanged" if existing["input_sha256"] == input_sha else "conflict", "record": existing}

    record: dict[str, Any] = {
        "schema": SCHEMA, "judgment_id": f"{code}|{base_date}|{edition}|{method_version}|{input_version}",
        "code": code, "name": data.get("name"), "base_date": base_date, "edition": edition,
        "method_version": method_version, "input_version": input_version, "model": model, "effort": effort,
        "run_mode": "official" if official_since and base_date >= official_since else "trial",
        "layers": enabled, "missing_layers": list(model_input["missing_layers"]),
        "withheld_inputs": dict(WITHHELD_INPUTS),
        "data_cutoffs": copy.deepcopy(dict(data.get("data_cutoffs") or {})),
        "deadline": due.isoformat(), "base_close": model_input["base_close"],
        "input": model_input, "input_sha256": input_sha,
        "attempts": [],
    }

    def finish(kind: str, **fields: Any) -> dict[str, Any]:
        record.update(kind=kind, locked_at=clock().astimezone(TAIPEI).isoformat(), **fields)
        if kind == "judgment" and datetime.fromisoformat(record["locked_at"]) >= due:
            for key in fields:
                record.pop(key, None)
            record.update(kind="miss", miss_reason="deadline_passed", miss_detail="鎖定時已過期限")
        outcome = append(path, seal(record))
        return {"outcome": outcome, "record": read_record(path) if outcome != "written" else record}

    if problems:
        if clock() < due:
            return {"outcome": "not_ready", "record": None, "detail": "；".join(problems)}
        return finish("miss", miss_reason="input_not_ready", miss_detail="；".join(problems))
    if clock() >= due:
        return finish("miss", miss_reason="deadline_passed", miss_detail="開始寫之前已過期限")

    # 先讀這一檔的系列（順便核對雜湊）：原件被改動時在呼叫模型之前就停下。
    previous = _previous_judgment(folder, code, edition, base_date)
    prompt = build_prompt(_prompt_rules(enabled), model_input)
    for _ in range(2):
        refusal = gate.refusal()
        if refusal:
            return finish("miss", miss_reason="quota_exhausted", miss_detail=refusal)
        status, structured, detail = call_model(model_bin, prompt, model, timeout_seconds, OUTPUT_SCHEMA, effort=effort)
        gate.spent(status, detail)
        if status != "ok":
            record["attempts"].append({"status": status, "detail": detail})
            return finish("miss", miss_reason="quota_exhausted" if gate.blocked else status, miss_detail=detail)
        accepted, problem = _validate(structured, model_input)
        if accepted is not None:
            return finish("judgment", **accepted, random_pick=_random_pick(bars, index_rows),
                          transition=_transition(accepted["status"], previous),
                          compared_with=previous["judgment_id"] if previous else None)
        reason = structured.get("reason") if isinstance(structured, dict) else None
        rejected = ({"rejected_text": reason[:400]} if isinstance(reason, str) and problem and "理由：" in problem
                    and "數字" in problem else {})
        record["attempts"].append({"status": "answer_invalid", "detail": problem, **rejected})
    return finish("miss", miss_reason="answer_invalid", miss_detail=record["attempts"][-1]["detail"])


def write_judgment(
    root: Path | str,
    data: Mapping[str, Any],
    *,
    edition: str,
    model_bin: Path | str,
    model: str,
    effort: str = DEFAULT_EFFORT,
    now: Callable[[], datetime] | None = None,
    official_since: str | None = None,
    session_date: str | None = None,
    layers: Sequence[str] = DEFAULT_LAYERS,
    timeout_seconds: float = 360,
) -> dict[str, Any]:
    """寫一份判斷。回傳 ``{"outcome": written|unchanged|conflict|not_ready, "record": 帳本裡那一筆}``。

    ``data`` 是一檔關注股的輸入：``code``、``name``、``base_date``、``stock_daily``（實際成交價日 K）、
    ``capital_events``（股本變動事件，可省）、``index_daily``（加權指數日 K）、``method_text``、``data_cutoffs``，
    以及各層的資料 ``technical``、``market_forecast``、``announcements``、``lessons``，
    I 票的 ``sector``、``margin``、``environment``（由 ``watch_inputs`` 從本機原件讀出；布林通道由帳本從日 K 自己算）。
    各層的內容照原樣交給模型：只能放條款允許的資料，由呼叫端負責（I 票四樣的來源都是政府資料開放授權、
    本站自己算的，或美國財政部的公有領域資料）。

    同一檔、同一基準日、同一版已有紀錄時不再呼叫模型：輸入相同回 ``unchanged``，輸入不同回 ``conflict``（拒寫）。
    資料沒到齊時期限前什麼都不寫（``not_ready``，可之後重跑）；過了期限仍沒到齊才記漏記。
    其他任何失敗（模型失敗、逾時、亂碼、重試後仍不合格、額度用完）都寫成一筆鎖定的漏記。
    正式或試跑由 ``official_since`` 決定（基準日在正式起算日當天或之後才是正式）；起算日由呼叫端在輸入到齊時寫定。
    """
    return _write(root, data, _CallGate(), edition=edition, model_bin=model_bin, model=model, effort=effort, now=now,
                  official_since=official_since, session_date=session_date, layers=layers,
                  timeout_seconds=timeout_seconds)


def write_judgments(
    root: Path | str,
    stocks: Sequence[Mapping[str, Any]],
    *,
    edition: str,
    model_bin: Path | str,
    model: str,
    effort: str = DEFAULT_EFFORT,
    now: Callable[[], datetime] | None = None,
    official_since: str | None = None,
    session_date: str | None = None,
    layers: Sequence[str] = DEFAULT_LAYERS,
    timeout_seconds: float = 360,
    call_budget: int | None = None,
) -> list[dict[str, Any]]:
    """依給定的順序（打開關注的先後）一檔一檔寫判斷；每筆結果同 ``write_judgment``，多帶 ``code``。

    ``call_budget`` 是這一批最多能呼叫模型幾次（重試也算一次）。次數用完，或模型回報額度用完之後，
    排在後面的檔各記一筆漏記並寫明原因是額度；已鎖定與資料沒到齊的檔不受影響。
    某一檔的輸入不能用（``rejected``）或沒寫成，不影響其他檔。帳本原件被改動（``LedgerIntegrityError``）
    不在此列：那是帳本本身出事，整批停下。
    """
    gate = _CallGate(call_budget)
    results = []
    for data in stocks:
        try:
            result = _write(root, data, gate, edition=edition, model_bin=model_bin, model=model, effort=effort, now=now,
                            official_since=official_since, session_date=session_date, layers=layers,
                            timeout_seconds=timeout_seconds)
        except ValueError as error:
            result = {"outcome": "rejected", "record": None, "detail": str(error)}
        results.append({"code": str(data.get("code")), **result})
    return results


# ---- 到期對帳 ----

class _Market(NamedTuple):
    """截至目前的行情：指數日 K（交易日以它為準）與各檔個股的實際成交價、還原係數。"""

    index_rows: list[dict[str, Any]]
    position: dict[str, int]
    stocks: dict[str, dict[str, Any]]

    @classmethod
    def of(cls, index_daily: Sequence[Mapping[str, Any]], stocks: Mapping[str, Mapping[str, Any]]) -> _Market:
        index_rows = clean_daily(index_daily, keys=("close",))
        return cls(index_rows, {row["date"]: index for index, row in enumerate(index_rows)},
                   {code: _stock_view(entry) for code, entry in stocks.items()})

    @property
    def as_of(self) -> str | None:
        return self.index_rows[-1]["date"] if self.index_rows else None

    def cell(self, code: str, base_date: str, horizon: int) -> tuple[str, str | None, dict[str, Any] | None]:
        """一個轉變日的一格：（pending｜undeterminable｜due, 到期日, 對帳結果）。

        到期日是基準日之後第 5／20 個「指數日 K 有資料」的日子。指數或個股的日 K 還沒到那一天是還沒到期；
        基準日或到期日缺收盤（停牌、缺漏）、期間內有還原不了的股本變動，都是無法判定——都不算看錯。
        個股的漲跌幅以到期日為準還原（還原價和個股頁一樣取到小數兩位）：之後才發生的股本變動不會改變已經對過的結果。
        """
        base_index = self.position.get(base_date)
        if base_index is None:
            return "undeterminable", None, None
        if base_index + horizon >= len(self.index_rows):
            return "pending", None, None
        expiry = self.index_rows[base_index + horizon]
        stock = self.stocks.get(code)
        if stock is None:
            return "undeterminable", expiry["date"], None
        if stock["last"] is None or stock["last"] < expiry["date"]:
            return "pending", None, None
        start, end = stock["by_date"].get(base_date), stock["by_date"].get(expiry["date"])
        index_start, index_end = self.index_rows[base_index]["close"], expiry["close"]
        if (start is None or end is None or not index_start or index_end is None
                or any(base_date < day <= expiry["date"] for day in stock["unresolved"])):
            return "undeterminable", expiry["date"], None
        restored = round(start[0] * (start[1] / end[1]), 2)
        if restored <= 0:
            return "undeterminable", expiry["date"], None
        return "due", expiry["date"], {
            "expiry_date": expiry["date"],
            "stock_base_close": start[0], "stock_base_close_restored": restored, "stock_expiry_close": end[0],
            "index_base_close": index_start, "index_expiry_close": index_end,
            "stock_return": round(end[0] / restored - 1, 6), "index_return": round(index_end / index_start - 1, 6),
            "beat_index": _beats(restored, end[0], index_start, index_end),
        }


def _stock_view(entry: Mapping[str, Any]) -> dict[str, Any]:
    """一檔個股截至目前的實際成交價日 K，連同每一根的還原係數與該還原卻缺官方數字的事件日。"""
    raw = _raw_bars(entry.get("stock_daily"))
    factors, unresolved = price_factors(raw, _capital_events(entry.get("capital_events")))
    return {"by_date": {b.date: (b.close, factor) for b, factor in zip(raw, factors)},
            "last": raw[-1].date if raw else None, "unresolved": [event["date"] for event in unresolved]}


def _cell_stem(judgment: Mapping[str, Any], horizon: int) -> str:
    return f"{judgment['base_date']}__{judgment['edition']}__{judgment['code']}__h{horizon}"


def reconcile(root: Path | str, index_daily: Sequence[Mapping[str, Any]],
              stocks: Mapping[str, Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """替帳本裡每一個轉變日的 5 日、20 日兩格檢查一次到期。

    ``stocks`` 是每檔代號的 ``{"stock_daily": 實際成交價日 K, "capital_events": 股本變動事件}``。
    回傳本次新增的對帳紀錄（``appended``）、還沒到期（``pending``）、無法判定（``undeterminable``）
    與重算結果不同被拒寫的衝突（``conflicts``）。對帳紀錄只新增。
    """
    folder = Path(root)
    market = _Market.of(index_daily, stocks)
    result: dict[str, list[dict[str, Any]]] = {"appended": [], "pending": [], "undeterminable": [], "conflicts": []}
    for judgment in read_ledger(folder)["judgments"]:
        if not judgment.get("transition"):
            continue
        for horizon in HORIZONS:
            stem = _cell_stem(judgment, horizon)
            path = folder / "reconciliations" / f"{stem}.json"
            ref = {"judgment_id": judgment["judgment_id"], "horizon": horizon}
            status, expiry_date, outcome = market.cell(judgment["code"], judgment["base_date"], horizon)
            if status != "due":
                if not ledger_exists(path):
                    result[status].append(ref if status == "pending" else {**ref, "expiry_date": expiry_date})
                continue
            record = seal({
                "schema": SCHEMA, "kind": "reconciliation", **ref,
                "judgment_sha256": judgment["record_sha256"],
                "code": judgment["code"], "edition": judgment["edition"], "base_date": judgment["base_date"],
                **outcome,
            })
            written = append(path, record)
            if written == "written":
                result["appended"].append(record)
            elif written == "conflict":
                existing = read_record(path)
                conflict = seal({
                    "schema": SCHEMA, "kind": "reconciliation_conflict", **ref,
                    "existing_sha256": existing["record_sha256"], "recomputed": record,
                })
                append(folder / "conflicts" / f"{stem}__{record['record_sha256'][:16]}.json", conflict)
                result["conflicts"].append({**ref, "expiry_date": expiry_date,
                                            "existing_beat_index": existing["beat_index"],
                                            "recomputed_beat_index": record["beat_index"]})
    return result


# ---- 正式起算日 ----

def _official_path(root: Path, input_version: str) -> Path:
    # 輸入版本當檔名：只擋路徑字元，格式本身由 input_version_of 決定（H 票的 wyckoff_lpsy 層名有底線）。
    if not re.fullmatch(r"[0-9A-Za-z+._-]{1,200}", input_version):
        raise ValueError(f"輸入版本不合法：{input_version!r}")
    return root / "official" / f"{input_version}.json"


def start_official(root: Path | str, *, since: str, input_version: str) -> dict[str, Any]:
    """寫下某一個輸入版本的正式起算日。回傳 ``{"outcome": written|unchanged|conflict|not_ready, "record": ...}``。

    要在 ``since`` 之前，有一個基準日、有一檔早晚兩版都用這個輸入版本寫成判斷才寫得下（漏記不算：
    補記的「整晚沒跑」也是漏記）；
    哪幾層算「輸入到齊」由呼叫端決定（排程打開的那一組）。每個輸入版本各自一個起算日，寫了就不能改：
    同內容再寫一次是 ``unchanged``，不同的日子是 ``conflict``（拒寫）。
    """
    folder = Path(root)
    date.fromisoformat(since)  # 起算日格式不對就停下
    path = _official_path(folder, input_version)
    if not ledger_exists(path):
        editions: dict[tuple[str, str], set[str]] = {}
        for record in read_ledger(folder)["judgments"]:
            if record["input_version"] == input_version and record["base_date"] < since and record["kind"] == "judgment":
                editions.setdefault((record["base_date"], record["code"]), set()).add(record["edition"])
        both = [day for (day, _), ran in editions.items() if ran >= set(EDITIONS)]
        if not both:
            return {"outcome": "not_ready", "record": None}
        written = append(path, seal({"schema": SCHEMA, "kind": "official_start", "input_version": input_version,
                                     "since": since, "ran_on": max(both)}))
        if written == "written":
            return {"outcome": "written", "record": read_record(path)}
    # 已經寫過（包括同時有另一個寫入者先寫下）：只看起算日是不是同一天。
    existing = read_record(path)
    return {"outcome": "unchanged" if existing["since"] == since else "conflict", "record": existing}


def official_starts(root: Path | str) -> dict[str, str]:
    """各輸入版本已寫下的正式起算日。"""
    return {record["input_version"]: record["since"] for record in read_folders(root, ("official",))["official"]}


# ---- 彙總 ----

# 「可下結論」的判定（本人 2026-11-16 定案，第一筆正式紀錄之前寫定、之後不改）：
# 正式的那一組，20 日已對帳的轉變日滿 30 次，而且正式起算滿 6 個月，5 日與 20 日兩格才一起標「可下結論」。
# 合計與單一檔用同一個門檻。只是顯示樣本夠了，不自動說有效或無效，也不觸發任何其他功能。
CONCLUSION_MIN_RECONCILED = 30
CONCLUSION_MIN_MONTHS = 6


def _stage(run_mode: str, reconciled_twenty: int, official_since: str | None, as_of: str | None) -> str:
    """「試跑」不下結論；正式的那一組次數與時間都滿了才「可下結論」，之前一律「累積中」。"""
    if run_mode != "official":
        return "試跑"
    if not official_since or not as_of or reconciled_twenty < CONCLUSION_MIN_RECONCILED:
        return "累積中"
    due = add_months(date.fromisoformat(official_since), CONCLUSION_MIN_MONTHS)
    return "可下結論" if date.fromisoformat(as_of) >= due else "累積中"


def _tally(records: list[dict[str, Any]], horizon: int, reconciled: Mapping[tuple[str, int], Mapping[str, Any]],
           market: _Market) -> dict[str, Any]:
    transitions = [record for record in records if record.get("transition")]
    outcomes, shares = [], []
    open_cells = {"pending": 0, "undeterminable": 0}
    for record in transitions:
        outcome = reconciled.get((record["judgment_id"], horizon))
        if outcome is None:
            # 已到期、只是還沒跑對帳的那一格也算還沒對，不算無法判定。
            status, _, _ = market.cell(record["code"], record["base_date"], horizon)
            open_cells["undeterminable" if status == "undeterminable" else "pending"] += 1
            continue
        outcomes.append(outcome)
        picked = record["random_pick"][str(horizon)]
        if picked["samples"]:
            shares.append(picked["wins"] / picked["samples"])
    wins = sum(outcome["beat_index"] for outcome in outcomes)
    return {
        "judgments": sum(record["kind"] == "judgment" for record in records),
        "misses": sum(record["kind"] == "miss" for record in records),
        "transitions": len(transitions), "reconciled": len(outcomes), "wins": wins,
        "win_rate": wins / len(outcomes) if outcomes else None,
        # 隨便挑：已對帳的各轉變日那一份判斷當時鎖定的比例，取平均（和贏大盤比例是同一批轉變日）。
        "random_rate": sum(shares) / len(shares) if shares else None,
        **open_cells,
    }


def summarize(root: Path | str, index_daily: Sequence[Mapping[str, Any]],
              stocks: Mapping[str, Mapping[str, Any]], *, official_since: str | None = None) -> dict[str, Any]:
    """依（試跑／正式、版本、方法版本、輸入版本）分組，每組有全部關注股合計（``scope="all"``）與
    單一檔（``scope="stock"``）兩種口徑，各有 5 日、20 日兩格；試跑與正式各自成組，不混算。

    還沒對帳的轉變日依截至目前的日 K 分成「還沒到期」與「無法判定」，兩者都不算看錯。
    每組另標 ``stage``：試跑／累積中／可下結論（只是顯示，不觸發任何其他功能）。「滿 6 個月」從那一組輸入版本
    自己寫在帳本裡的正式起算日算；帳本裡沒有時才用 ``official_since``。
    """
    ledger = read_ledger(root)
    market = _Market.of(index_daily, stocks)
    starts = official_starts(root)
    reconciled = {(row["judgment_id"], row["horizon"]): row for row in ledger["reconciliations"]}
    series: dict[tuple[str, str, str, str], dict[str, list[dict[str, Any]]]] = {}
    for record in ledger["judgments"]:
        key = (record["run_mode"], record["edition"], record["method_version"], record["input_version"])
        series.setdefault(key, {}).setdefault(record["code"], []).append(record)
    groups = []
    for (run_mode, edition, method_version, input_version), by_code in sorted(series.items()):
        everything = [record for records in by_code.values() for record in records]
        scopes = [("all", None, everything), *(("stock", code, records) for code, records in sorted(by_code.items()))]
        for scope, code, records in scopes:
            tallies = {horizon: _tally(records, horizon, reconciled, market) for horizon in HORIZONS}
            stage = _stage(run_mode, tallies[20]["reconciled"], starts.get(input_version, official_since),
                           market.as_of)
            for horizon in HORIZONS:
                groups.append({"scope": scope, "code": code, "run_mode": run_mode, "edition": edition,
                               "horizon": horizon, "method_version": method_version, "input_version": input_version,
                               **tallies[horizon], "stage": stage})
    return {"as_of": market.as_of, "official_since": official_since, "official_starts": starts, "groups": groups,
            "conflicts": len(ledger["conflicts"])}


# ---- 發布到網站讀的資料庫 ----

# 給網站顯示的欄位；完整輸入（含方法稿）、模型的重試細節與「隨便挑」的明細只留在原件（成績看彙總）。
_PUBLIC_FIELDS = ("judgment_id", "code", "name", "base_date", "edition", "method_version", "input_version", "model",
                  "run_mode", "kind", "locked_at", "deadline", "layers", "missing_layers", "withheld_inputs",
                  "data_cutoffs", "base_close", "status", "trigger_line", "wrong_line", "reason", "reason_news",
                  "transition", "compared_with", "miss_reason", "miss_detail", "record_sha256")


def d1_sql(root: Path | str, index_daily: Sequence[Mapping[str, Any]], stocks: Mapping[str, Mapping[str, Any]], *,
           published_at: str, official_since: str | None = None) -> str:
    """把帳本整理成給網站資料庫的指令檔（migrations/0017）。只新增：同一筆再發布一次不會多一筆、也不會改內容。

    ``index_daily``、``stocks`` 和 ``summarize`` 的參數相同，用來算一併發布的成績摘要。
    不包 BEGIN／COMMIT：部署工具（wrangler d1 execute）自己會包交易。
    """
    ledger = read_ledger(root)
    statements = []
    for record in ledger["judgments"]:
        statements.append(sql_insert("watch_judgments", {
            "judgment_id": record["judgment_id"], "code": record["code"], "base_date": record["base_date"],
            "edition": record["edition"], "method_version": record["method_version"],
            "input_version": record["input_version"], "run_mode": record["run_mode"], "kind": record["kind"],
            "status": record.get("status"),
            "record_json": compact_json({key: record[key] for key in _PUBLIC_FIELDS if key in record}),
            "record_sha256": record["record_sha256"], "published_at": published_at,
        }, None))
    for row in ledger["reconciliations"]:
        statements.append(sql_insert("watch_reconciliations", {
            "judgment_id": row["judgment_id"], "horizon": row["horizon"], "code": row["code"], "edition": row["edition"],
            "expiry_date": row["expiry_date"], "beat_index": row["beat_index"], "record_json": compact_json(row),
            "record_sha256": row["record_sha256"], "published_at": published_at,
        }, "judgment_id,horizon"))
    summary = summarize(root, index_daily, stocks, official_since=official_since)
    if summary["as_of"]:
        statements.append(sql_insert("watch_summaries", {
            "summary_sha256": sha(summary), "as_of": summary["as_of"], "summary_json": compact_json(summary),
            "published_at": published_at,
        }, "summary_sha256"))
    return "\n".join(statements) + "\n"


# ---- 讀帳本 ----

def read_ledger(root: Path | str) -> dict[str, list[dict[str, Any]]]:
    return read_folders(root, ("judgments", "reconciliations", "conflicts"))
