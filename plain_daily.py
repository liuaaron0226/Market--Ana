"""首頁「籌碼與資金」的白話分析：每天收盤資料建好後，依當天數字重寫（本人 2026-11-17 定案，取代 11-06「文字人寫」）。

流程（cloud/market_pack_daily.py 在大盤資料包建好、截止日核對過之後呼叫 ``produce``）：
1. ``build_facts``：程式從當天的大盤日K（analysis-data.js）與籌碼（chips-data.js）算出一組事實，
   每一項事實是一段已經排好數字、單位與方向的短句（例如 foreign_today＝「賣超 912 億」）。
   只用證交所、櫃買、期交所的公開總表數字；不碰個股名單、任何人的自選股或庫存，也不用 Fugle。
2. Gemini（官方 agy＋本人 AI Pro 訂閱）照同一組事實寫白話：自由文字**不得有任何數字、日期、代號**，
   要提數字就寫 ``{事實代號}``，程式核對代號存在、屬於該段的主題、方向沒有寫反，再換成事實短句。
   這是 ai_digest.py「自由文字不得含數字、事實由程式依 refs 補上」的同一套做法，換成句中代入，讀起來才像人話。
3. 模型失敗、逾時、或任何一項核對不過：整份改用 ``template_raw`` 的程式套句（同一個核對器也要通過），
   畫面標「依當天數字自動整理」，不冒充 AI；一天只呼叫一次模型、不重試。
4. 開關 MARKET_ANA_PLAIN_DAILY（repository variable，獨立於策略總開關）：
   off（預設，含沒設、拼錯）＝不呼叫模型，沿用人寫那份；dry＝呼叫模型並核對，結果只進執行紀錄，網站照舊；
   on＝發布當天重寫的那份。
"""
from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai_digest import BANNED_TEXT, CHINESE_NUMERAL_CHARS

SCHEMA = "market-plain/v2"
MODEL_DEFAULT = "gemini-3.8-flash"
MODEL_TIMEOUT_SECONDS = 360
MODES = ("off", "dry", "on")
PREFIX = "window.MARKET_PLAIN = "

# 首頁六個重點的順序就是畫面順序（直白分析卡顯示前四個，詳情顯示全部）；標題由程式加上「主角：」。
POINT_KEYS = ("foreign", "trust", "margin", "short", "position", "volume")
SUBJECTS = {"foreign": "外資", "trust": "投信", "margin": "融資", "short": "融券", "position": "位置", "volume": "成交量"}
# 每一點只能代入自己主題的事實（另加日期）；結論、擔心、安心三段可以用全部。
SECTION_TOPICS = {
    "foreign": {"foreign", "futures", "date"},
    "trust": {"trust", "date"},
    "margin": {"margin", "date"},
    "short": {"short", "date"},
    "position": {"position", "date"},
    "volume": {"volume", "position", "date"},
}
# 含「一」但不是數量的常用詞：先拿掉再查數字（其餘中文數字一律不准，例如「兩邊」「十分」「一天」）。
YI_WORDS = ("進一步", "一直", "一些", "一樣", "一起", "一般", "一定", "一旦", "一致", "一度", "一路", "同一", "唯一",
            "單一", "一邊", "一面", "一時", "一再")
PLACEHOLDER = re.compile(r"\{([a-z0-9_]+)\}")
LIMITS = {"one_line": 80, "title": 24, "line": 90, "item": 80}

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["data_date", "one_line", "points", "worry", "relief"],
    "properties": {
        "data_date": {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}$"},
        "one_line": {"type": "string", "minLength": 1, "maxLength": LIMITS["one_line"]},
        "points": {
            "type": "array", "minItems": 6, "maxItems": 6,
            "items": {
                "type": "object", "additionalProperties": False, "required": ["key", "title", "lines"],
                "properties": {
                    "key": {"type": "string", "enum": list(POINT_KEYS)},
                    "title": {"type": "string", "minLength": 1, "maxLength": LIMITS["title"]},
                    "lines": {"type": "array", "minItems": 1, "maxItems": 4,
                              "items": {"type": "string", "minLength": 1, "maxLength": LIMITS["line"]}},
                },
            },
        },
        "worry": {"type": "array", "minItems": 1, "maxItems": 3, "items": {"type": "string", "minLength": 1, "maxLength": LIMITS["item"]}},
        "relief": {"type": "array", "minItems": 1, "maxItems": 3, "items": {"type": "string", "minLength": 1, "maxLength": LIMITS["item"]}},
    },
}


def normalize_mode(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in MODES else "off"


# ---- 數字排版 ----

def _num(value: float) -> str:
    return f"{round(value):,}"


def _yi(value: float, *, trillion: bool = True) -> str:
    """億元；一萬億以上改寫兆（一位小數）。和首頁卡片寫法一致的（成交金額、融資餘額）不換兆。"""
    value = abs(value)
    return f"{value / 10000:.1f} 兆" if trillion and value >= 10000 else f"{_num(value)} 億"


def _lots(value: float) -> str:
    value = abs(value)
    return f"{value / 10000:.1f} 萬張" if value >= 10000 else f"{_num(value)} 張"


def _pct(value: float) -> str:
    value = abs(value)
    return f"{value:.0f}%" if value >= 10 else f"{value:.1f}%"


def _md(day: str) -> str:
    return f"{day[5:7]}/{day[8:10]}"


def _side(value: float) -> str:
    return "賣超" if value < 0 else "買超" if value > 0 else "買賣持平"


def _sign(value: float) -> int:
    return (value > 0) - (value < 0)


def _streak(values: list[float]) -> int:
    """最新一天起往回，和最新一天同方向（同號）的連續天數；最新一天是零就算零天。"""
    sign = _sign(values[-1])
    count = 0
    for value in reversed(values):
        if sign == 0 or _sign(value) != sign:
            break
        count += 1
    return count


def _ago(rows: list, n: int):
    return rows[max(0, len(rows) - 1 - n)]


def _ma(closes: list[float], n: int) -> float | None:
    return sum(closes[-n:]) / n if len(closes) >= n else None


# ---- 1. 事實 ----

def build_facts(analysis: Mapping[str, Any], chips: Mapping[str, Any]) -> dict[str, Any]:
    """當天的事實表：{facts: {代號: {topic, text}}, signals: 方向旗標, data_dates}。資料不夠就丟例外（呼叫端改沿用舊版）。"""
    daily = [row for row in analysis["daily"] if isinstance(row.get("close"), (int, float))]
    F, U, M = list(chips["foreign"]), list(chips["futures"]), list(chips["margin"])
    if len(daily) < 21 or len(F) < 20 or len(U) < 6 or len(M) < 21:
        raise ValueError("資料天數不足，算不出白話分析要的事實")
    facts: dict[str, dict[str, str]] = {}

    def fact(key: str, topic: str, text: str) -> None:
        facts[key] = {"topic": topic, "text": text}

    today, prev = daily[-1], daily[-2]
    price_day = min(today["date"], F[-1]["date"], U[-1]["date"])
    margin_day = M[-1]["date"]
    fact("price_date", "date", _md(price_day))
    fact("margin_date", "date", _md(margin_day))

    # 外資現貨
    nets = [float(row["net_billion"]) for row in F]
    foreign_now, foreign_streak = nets[-1], _streak(nets)
    fact("foreign_today", "foreign", _side(foreign_now) + ("" if foreign_now == 0 else f" {_yi(foreign_now)}"))
    if foreign_now != 0:
        fact("foreign_streak", "foreign", f"連 {foreign_streak} 天{_side(foreign_now)}" if foreign_streak >= 2
             else ("由賣轉買" if foreign_now > 0 else "由買轉賣"))
    f5, f20 = sum(nets[-5:]), sum(nets[-20:])
    fact("foreign_5d", "foreign", f"近 5 個交易日{_side(f5)}約 {_yi(f5)}" if f5 else "近 5 個交易日買賣持平")
    fact("foreign_20d", "foreign", f"近 20 個交易日累計{_side(f20)}約 {_yi(f20)}" if f20 else "近 20 個交易日買賣持平")

    # 外資台指期淨部位（負數＝淨空單）
    fut_now, fut_week = float(U[-1]["net_contracts"]), float(_ago(U, 5)["net_contracts"])
    side = "淨空單" if fut_now < 0 else "淨多單"
    fact("futures_net", "futures", f"{side} {_num(abs(fut_now))} 口")
    held_now, held_week = (-fut_now, -fut_week) if fut_now < 0 else (fut_now, fut_week)
    diff = held_now - held_week
    fact("futures_week", "futures", f"比一週前{'增加' if diff > 0 else '減少'} {_num(abs(diff))} 口" if round(diff)
         else "和一週前差不多")
    contracts = [float(row["net_contracts"]) for row in U]
    growing = 0
    for a, b in zip(reversed(contracts[:-1]), reversed(contracts[1:])):
        if (fut_now < 0 and b < a) or (fut_now >= 0 and b > a):
            growing += 1
        else:
            break
    if growing >= 2:
        fact("futures_streak", "futures", f"連 {growing} 天增加")

    # 投信
    trusts = [float(row["trust_billion"]) for row in F]
    t5 = sum(trusts[-5:])
    fact("trust_today", "trust", _side(trusts[-1]) + ("" if trusts[-1] == 0 else f" {_yi(trusts[-1])}"))
    fact("trust_5d", "trust", f"近 5 個交易日{_side(t5)}約 {_yi(t5)}" if t5 else "近 5 個交易日買賣持平")
    trust_cover = t5 / abs(f5) if f5 < 0 < t5 else None
    if trust_cover is not None:
        fact("trust_vs_foreign", "trust", f"約是同期外資賣超的 {_pct(trust_cover * 100)}")

    # 融資
    balances = [float(row["margin_balance_billion"]) for row in M]
    m_change = balances[-1] - balances[-2]
    fact("margin_balance", "margin", _yi(balances[-1], trillion=False))
    fact("margin_change", "margin", f"比前一天{'增加' if m_change > 0 else '減少'} {_yi(m_change)}" if round(m_change)
         else "和前一天差不多")
    peak = max(range(len(M)), key=lambda i: balances[i])
    fact("margin_peak", "margin", f"是近 {len(M)} 個交易日最高" if peak == len(M) - 1
         else f"比 {_md(M[peak]['date'])} 的高點 {_yi(balances[peak])}少了 {_yi(balances[peak] - balances[-1])}")
    changes = [b - a for a, b in zip(balances, balances[1:])]
    if m_change < 0 and m_change == min(changes):
        fact("margin_rank", "margin", f"單日減少金額是近 {len(M)} 個交易日最多")
    elif m_change > 0 and m_change == max(changes):
        fact("margin_rank", "margin", f"單日增加金額是近 {len(M)} 個交易日最多")

    # 融券
    shorts = [float(row["short_units"]) for row in M]
    s_change, s20 = shorts[-1] - shorts[-2], float(_ago(M, 20)["short_units"])
    fact("short_units", "short", _lots(shorts[-1]))
    fact("short_change", "short", f"比前一天{'增加' if s_change > 0 else '減少'}約 {_lots(s_change)}" if s_change
         else "和前一天一樣")
    if s20:
        ratio = (shorts[-1] / s20 - 1) * 100
        fact("short_vs_20d", "short", f"比 20 個交易日前{'多' if ratio > 0 else '少'} {_pct(ratio)}" if round(ratio, 1)
             else "和 20 個交易日前差不多")

    # 指數位置
    close, prev_close = float(today["close"]), float(prev["close"])
    change = close - prev_close
    fact("close", "position", f"{_num(close)} 點")
    fact("index_change", "position", f"{'上漲' if change > 0 else '下跌'} {_num(abs(change))} 點（{_pct(change / prev_close * 100)}）"
         if round(change) else "和前一天差不多")
    high, low = float(today.get("high") or close), float(today.get("low") or close)
    fact("day_high", "position", f"{_num(high)} 點")
    fact("day_low", "position", f"{_num(low)} 點")
    spot = (close - low) / (high - low) if high > low else 0.5
    fact("close_in_range", "position", "收在當天高檔" if spot >= 2 / 3 else "收在當天低檔" if spot <= 1 / 3 else "收在當天中間")
    base20 = float(daily[-21]["close"])
    move = (close / base20 - 1) * 100
    fact("change_20d", "position", f"近 20 個交易日{'上漲' if move > 0 else '下跌'} {_pct(move)}" if round(move, 1)
         else "近 20 個交易日幾乎沒動")
    closes = [float(row["close"]) for row in daily]
    signals: dict[str, Any] = {}
    for n, label in ((20, "20 日均線"), (60, "60 日均線"), (240, "年線")):
        average = _ma(closes, n)
        if average is None:
            continue
        below = close < average
        signals[f"below_ma{n}"] = below
        fact(f"ma{n}_line", "position", f"{label}（約 {_num(average)} 點）")
        fact(f"ma{n}_side", "position", f"在{' ' if label[0].isdigit() else ''}{label}之{'下' if below else '上'}")

    # 成交金額（和首頁卡片同一個算法：最近 20 天含今天的平均）
    amounts = [float(row["amount"]) / 1e8 for row in daily[-20:] if isinstance(row.get("amount"), (int, float))]
    if amounts:
        fact("amount", "volume", _yi(amounts[-1], trillion=False))
        ratio = amounts[-1] / (sum(amounts) / len(amounts))
        signals["amount_ratio"] = ratio
        fact("amount_ratio", "volume", f"是近 20 個交易日平均的 {ratio:.1f} 倍")

    signals.update({
        "foreign": _sign(foreign_now), "foreign_5d": _sign(f5), "foreign_streak": foreign_streak,
        "futures_short": fut_now < 0, "futures_growing": diff > 0 and bool(round(diff)),
        "trust_5d": _sign(t5), "trust_cover": trust_cover, "margin": _sign(round(m_change)), "short": _sign(s_change), "index": _sign(round(change)),
        "margin_lagging": margin_day < price_day,
    })
    return {"data_dates": {"price_and_foreign": price_day, "margin": margin_day, "futures": U[-1]["date"]},
            "facts": facts, "signals": signals}


# ---- 2. 核對 ----

def _contradictions(signals: Mapping[str, Any]) -> list[tuple[set[str], tuple[str, ...]]]:
    """（套用的段落, 不可出現的字）：描述「今天」的結論與六點不可以把方向寫反；擔心、安心是假設條件，不查。"""
    checks: list[tuple[set[str], tuple[str, ...]]] = []
    everywhere = {"one_line", *POINT_KEYS}
    if signals["foreign"] < 0:
        checks.append((everywhere, ("外資買超", "外資轉買", "現貨買超", "現貨轉買", "轉為買超")))
    elif signals["foreign"] > 0:
        checks.append((everywhere, ("外資賣超", "外資轉賣", "外資連賣", "現貨賣超", "現貨轉賣", "現貨連賣", "轉為賣超")))
    if signals["futures_short"] and signals["futures_growing"]:
        checks.append(({"one_line", "foreign"}, ("空單減少", "空單下降", "空單縮小", "空單變少")))
    elif signals["futures_short"]:
        checks.append(({"one_line", "foreign"}, ("空單增加", "空單變多", "空單擴大", "空單還在加")))
    if signals["margin"] < 0:
        checks.append((everywhere, ("融資增加", "融資回升", "融資變多")))
    elif signals["margin"] > 0:
        checks.append((everywhere, ("融資減少", "融資下降", "融資大減", "融資變少")))
    if signals["short"] < 0:
        checks.append(({"short"}, ("融券增加", "融券變多", "空單增加", "空單變多")))
    elif signals["short"] > 0:
        checks.append(({"short"}, ("融券減少", "融券變少", "空單回補", "空單減少")))
    if signals["index"] < 0:
        checks.append((everywhere, ("收紅", "收漲")))
    elif signals["index"] > 0:
        checks.append((everywhere, ("收黑", "收跌")))
    return checks


def _text_problem(text: Any, section: str, bundle: Mapping[str, Any], limit: int) -> str | None:
    if not isinstance(text, str) or not text.strip():
        return f"{section}：文字為空"
    if len(text) > limit:
        return f"{section}：文字太長"
    topics = SECTION_TOPICS.get(section)
    for key in PLACEHOLDER.findall(text):
        fact = bundle["facts"].get(key)
        if fact is None:
            return f"{section}：代號 {{{key}}} 不在事實表"
        if topics is not None and fact["topic"] not in topics:
            return f"{section}：{{{key}}} 不屬於這一點的主題"
    prose = PLACEHOLDER.sub("", text)
    if "{" in prose or "}" in prose:
        return f"{section}：大括號沒有對好"
    if any(term.lower() in prose.lower() for term in BANNED_TEXT):
        return f"{section}：包含正式頁禁止字樣"
    bare = prose
    for word in YI_WORDS:
        bare = bare.replace(word, "")
    if any(character.isnumeric() or character in CHINESE_NUMERAL_CHARS for character in bare):
        return f"{section}：自由文字不得有數字、日期或代號，數字要用事實代號"
    for sections, terms in _contradictions(bundle["signals"]):
        if section in sections:
            hit = next((term for term in terms if term in prose), None)
            if hit:
                return f"{section}：「{hit}」和當天數字的方向矛盾"
    return None


def _render(text: str, bundle: Mapping[str, Any]) -> str:
    return PLACEHOLDER.sub(lambda match: bundle["facts"][match.group(1)]["text"], text).strip()


def validate(output: Any, bundle: Mapping[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    """全部通過才回傳可發布的內容（數字已代入）；任何一處不過就整份不用（回傳 None 與原因）。"""
    if not isinstance(output, dict) or set(output) != set(OUTPUT_SCHEMA["required"]):
        return None, ["最外層欄位不對"]
    problems: list[str] = []
    if output.get("data_date") != bundle["data_dates"]["price_and_foreign"]:
        problems.append("資料日和當天資料不符")
    points = output.get("points")
    if (not isinstance(points, list) or len(points) != len(POINT_KEYS) or any(not isinstance(p, dict) for p in points)
            or sorted(str(p.get("key")) for p in points) != sorted(POINT_KEYS)):
        return None, [*problems, "points 要剛好六點、每個主題一次"]
    problems.append(_text_problem(output.get("one_line"), "one_line", bundle, LIMITS["one_line"]))
    for point in points:
        key = point["key"]
        if set(point) != {"key", "title", "lines"}:
            problems.append(f"{key}：欄位不對")
            continue
        title = point.get("title")
        if isinstance(title, str) and ("：" in title or ":" in title):
            problems.append(f"{key}：標題不要冒號與主角，程式會加")
        problems.append(_text_problem(title, key, bundle, LIMITS["title"]))
        lines = point.get("lines")
        if not isinstance(lines, list) or not 1 <= len(lines) <= 4:
            problems.append(f"{key}：lines 要一到四句")
            continue
        problems.extend(_text_problem(line, key, bundle, LIMITS["line"]) for line in lines)
    for section in ("worry", "relief"):
        items = output.get(section)
        if not isinstance(items, list) or not 1 <= len(items) <= 3:
            problems.append(f"{section}：要一到三項")
            continue
        problems.extend(_text_problem(item, section, bundle, LIMITS["item"]) for item in items)
    problems = [problem for problem in problems if problem]
    if problems:
        return None, problems
    by_key = {point["key"]: point for point in points}
    return {
        "one_line": _render(output["one_line"], bundle),
        "points": [{"key": key, "title": f"{SUBJECTS[key]}：{_render(by_key[key]['title'], bundle)}",
                    "lines": [_render(line, bundle) for line in by_key[key]["lines"]]} for key in POINT_KEYS],
        "worry": [_render(item, bundle) for item in output["worry"]],
        "relief": [_render(item, bundle) for item in output["relief"]],
    }, []


# ---- 3. 程式套句（模型失敗時用；也走同一個核對器） ----

def template_raw(bundle: Mapping[str, Any]) -> dict[str, Any]:
    facts, s = bundle["facts"], bundle["signals"]
    has = facts.__contains__
    selling, buying = s["foreign"] < 0, s["foreign"] > 0
    fut_bear = s["futures_short"] and s["futures_growing"]
    below20 = s.get("below_ma20")

    weak, strong = [], []
    (weak if selling else strong).append("外資現貨在賣" if selling else "外資現貨在買" if buying else "外資現貨買賣持平")
    if s["futures_short"]:
        (weak if fut_bear else strong).append("期貨空單在加" if fut_bear else "期貨空單沒有再加")
    else:
        strong.append("期貨是淨多單")
    if below20 is not None:
        (weak if below20 else strong).append("指數{ma20_side}")
    if len(weak) >= 2:
        one_line = "短線偏弱：" + "、".join(weak) + ("；但" + "、".join(strong) if strong else "") + "。"
    elif len(strong) >= 2:
        one_line = "短線偏強：" + "、".join(strong) + ("；但" + "、".join(weak) if weak else "") + "。"
    else:
        one_line = "多空訊號分歧：" + "、".join(weak + strong) + "。"

    streak = "{foreign_streak}，" if has("foreign_streak") else ""
    futures_tail = "，{futures_streak}" if has("futures_streak") else ""
    if s["futures_short"]:
        futures_word = "期貨空單還在加" if fut_bear else "期貨空單沒有再加"
    else:
        futures_word = "期貨是淨多單"
    spot_word = "現貨在賣" if selling else "現貨在買" if buying else "現貨買賣持平"
    foreign_title = "現貨和期貨都偏空" if selling and fut_bear else f"{spot_word}，{futures_word}"
    foreign_lines = [f"現貨{streak}今天{{foreign_today}}，{{foreign_20d}}。",
                     f"台指期{{futures_net}}，{{futures_week}}{futures_tail}。"]
    if selling and fut_bear:
        foreign_lines.append("現貨和期貨同時偏空，賣壓主要來自外資；是看壞還是避險，光看數字分不出來。")
    elif selling or fut_bear:
        foreign_lines.append("現貨和期貨的方向不同，外資的態度還不明確。")

    cover = s.get("trust_cover")
    if s["trust_5d"] > 0:
        trust_title = ("和外資一起買" if cover is None else "有在買，但抵不過外資賣" if cover < 1 else "買得比外資賣得多")
    else:
        trust_title = "也在賣" if s["foreign_5d"] < 0 else "在賣" if s["trust_5d"] < 0 else "幾乎沒動作"
    trust_lines = ["{trust_5d}，今天{trust_today}。"]
    if has("trust_vs_foreign"):
        trust_lines.append("買超金額{trust_vs_foreign}。")

    margin_title = {-1: "散戶借錢買股的錢在減少", 1: "散戶借錢買股的錢在增加"}.get(s["margin"], "借錢買股的錢變化不大")
    margin_lines = ["融資餘額 {margin_balance}，{margin_change}" + ("（資料到 {margin_date}）" if s["margin_lagging"] else "") + "。",
                    "{margin_peak}。"]
    if has("margin_rank"):
        margin_lines.append("{margin_rank}。")
    if s["margin"] < 0:
        margin_lines.append("槓桿變低，之後的賣壓會減輕；但也代表有資金離場。")
    elif s["margin"] > 0:
        margin_lines.append("借錢買股的人變多，行情轉弱時賣壓會比較重。")

    short_title = {-1: "空單在回補", 1: "空單在增加"}.get(s["short"], "空單變化不大")
    short_lines = ["融券餘額約 {short_units}，{short_change}" + ("（資料到 {margin_date}）" if s["margin_lagging"] else "") + "。"]
    if has("short_vs_20d"):
        short_lines.append("餘額{short_vs_20d}。")
    if s["short"] < 0:
        short_lines.append("放空的人買回，融券減少；剩下的空單之後也要買回。")
    elif s["short"] > 0:
        short_lines.append("放空的人變多，之後買回時會變成買盤。")

    below60 = s.get("below_ma60")
    if below20 and below60:
        position_title = "短線偏弱"
    elif below20 is False and below60 is False:
        position_title = "短線偏強"
    else:
        position_title = "短線多空拉鋸"
    if "below_ma240" in s:
        position_title += "，已在年線之下" if s["below_ma240"] else "，還在年線之上"
    sides = "、".join(f"{{ma{n}_side}}" for n in (20, 60, 240) if has(f"ma{n}_side"))
    position_lines = ["今天收 {close}，{index_change}，{close_in_range}。", "指數{change_20d}。"]
    if sides:
        position_lines.append(f"收盤{sides}。")

    ratio = s.get("amount_ratio")
    if ratio is None:
        volume_title, volume_lines = "今天沒有成交金額資料", ["成交金額還沒公布。"]
    else:
        volume_title = "量比平常大" if ratio >= 1.2 else "量比平常小" if ratio <= 0.8 else "量和平常差不多"
        volume_lines = ["成交 {amount}，{amount_ratio}。"]
        if ratio >= 1.2:
            volume_lines.append("量大代表換手劇烈，要看接下來幾天能不能延續。")

    worry, relief = [], []
    if fut_bear:
        worry.append("外資期貨空單還在增加。")
    if selling:
        worry.append("外資現貨{foreign_streak}，賣壓還沒停。" if has("foreign_streak") and s["foreign_streak"] >= 2
                     else "外資現貨{foreign_streak}。" if has("foreign_streak") else "外資現貨還在賣。")
    worry.append("收盤跌破今天最低點 {day_low}，短線可能再走弱。")
    if below20 and has("ma20_line"):
        relief.append("收盤站回 {ma20_line}。")
    if selling or fut_bear:
        relief.append("外資現貨轉為買超，或期貨空單開始減少。")
    else:
        relief.append("外資現貨持續買超、期貨空單沒有增加。")

    return {
        "data_date": bundle["data_dates"]["price_and_foreign"],
        "one_line": one_line,
        "points": [
            {"key": "foreign", "title": foreign_title, "lines": foreign_lines},
            {"key": "trust", "title": trust_title, "lines": trust_lines},
            {"key": "margin", "title": margin_title, "lines": margin_lines},
            {"key": "short", "title": short_title, "lines": short_lines},
            {"key": "position", "title": position_title, "lines": position_lines},
            {"key": "volume", "title": volume_title, "lines": volume_lines},
        ],
        "worry": worry[:3],
        "relief": relief[:3],
    }


def _finish(body: Mapping[str, Any], bundle: Mapping[str, Any], *, source: str, model: str | None,
            fallback_reason: str | None, clock: Callable[[], datetime]) -> dict[str, Any]:
    day = bundle["data_dates"]["price_and_foreign"]
    return {
        "schema": SCHEMA, "id": f"{day.replace('-', '')}-plain-{source}", "source": source, "model": model,
        "generated_at": clock().isoformat(timespec="seconds"), "data_dates": dict(bundle["data_dates"]),
        **body, "fallback_reason": fallback_reason,
    }


def template_plain(bundle: Mapping[str, Any], *, fallback_reason: str | None = None,
                   clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> dict[str, Any]:
    body, problems = validate(template_raw(bundle), bundle)
    if body is None:  # 程式套句自己沒過核對＝程式錯，丟出去讓呼叫端沿用舊版並記下
        raise RuntimeError("程式套句沒通過核對：" + "；".join(problems))
    return _finish(body, bundle, source="template", model=None, fallback_reason=fallback_reason, clock=clock)


# ---- 4. 模型 ----

def build_prompt(bundle: Mapping[str, Any]) -> str:
    facts = {key: {"topic": fact["topic"], "text": fact["text"]} for key, fact in bundle["facts"].items()}
    rules = [
        "你是台股盤後籌碼整理員，替一般投資人寫首頁「籌碼與資金」的白話分析。只能根據 FACTS，不可連網、不可使用工具，不可加入 FACTS 沒有的事實（美股、新聞、個股、傳聞都不要寫）。",
        "所有文字一律不得出現阿拉伯數字、全形數字、中文數字（一、兩、十、百、千、萬、億、兆等）、日期、股票代號或百分比。要提到數字或日期時寫 {代號}，例如「現貨{foreign_streak}，今天{foreign_today}」；程式會換成核對過的數字。只能用 FACTS 裡有的代號，代號換進去後要讀得通順。",
        f"只有這些含「一」的詞可以用：{'、'.join(YI_WORDS)}；其他像「一天」「一個月」「兩邊」都不可以，請改寫（例如「逐日增加」「近期」「現貨和期貨」）。",
        "points 剛好六點，key 各一次：foreign（外資現貨與台指期）、trust（投信）、margin（融資）、short（融券）、position（指數位置）、volume（成交量）。每點只能用該點 topic 的代號（foreign 可用 foreign 與 futures；volume 可用 volume 與 position；全部都可用 date）。",
        "title 是一句短判斷，不要寫主角也不要冒號（程式會自動加上「外資：」這類前綴）。lines 一到四句短句。",
        "one_line 是一句話結論。worry 是之後要擔心的情況、relief 是什麼情況下比較安心，各一到三項；跟外資有關的擔心要寫出「外資」二字。",
        "方向不可以和 FACTS 相反（例如外資賣超時不可寫外資轉買、融資減少時不可寫融資增加）。分不出原因的事不要說死，例如外資是看壞還是避險、融資減少是被追繳還是停損。",
        "不寫投資建議或保證，不寫模型、提示詞、方法、參考站、會員、反推等字樣。繁體中文、白話、短句。",
        f"data_date 填 {bundle['data_dates']['price_and_foreign']}。",
    ]
    return "\n".join([*rules, "DATA_DATES:", json.dumps(bundle["data_dates"], ensure_ascii=False),
                      "FACTS:", json.dumps(facts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))])


ModelCaller = Callable[[str, Mapping[str, Any]], tuple[str, Any, str]]


def agy_caller(agy_bin: Path | str | None, *, home: Path | str | None, model: str = MODEL_DEFAULT,
               timeout: float = MODEL_TIMEOUT_SECONDS) -> ModelCaller:
    """官方 agy（ledger_core.call_model 的串流模式）。登入檔放在隔離的 HOME，只給 agy 這個子行程用。"""
    def call(prompt: str, schema: Mapping[str, Any]) -> tuple[str, Any, str]:
        if not agy_bin or not Path(agy_bin).exists():
            return "agy_unavailable", None, "agy 沒有安裝成功"
        from ledger_core import call_model
        env = {**os.environ, "HOME": str(home)} if home else None
        return call_model(agy_bin, prompt, model, timeout, schema, env=env)
    return call


def produce(analysis: Mapping[str, Any], chips: Mapping[str, Any], *, mode: str, call_model: ModelCaller,
            model: str = MODEL_DEFAULT, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
            ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """回傳（要發布的白話分析或 None＝沿用舊版, 執行紀錄）。永遠不丟例外：不能因為白話分析讓大盤資料包發布不了。"""
    mode = normalize_mode(mode)
    audit: dict[str, Any] = {"mode": mode, "model": model, "source": None, "failure": None, "rejected": []}
    if mode == "off":
        return None, audit
    try:
        bundle = build_facts(analysis, chips)
        template = template_plain(bundle, clock=clock)
    except Exception as error:  # noqa: BLE001
        audit["failure"] = f"facts_failed: {type(error).__name__}: {str(error)[:200]}"
        return None, audit
    audit["data_dates"] = bundle["data_dates"]
    try:
        status, structured, detail = call_model(build_prompt(bundle), OUTPUT_SCHEMA)
    except Exception as error:  # noqa: BLE001
        status, structured, detail = "model_failed", None, f"{type(error).__name__}: {str(error)[:200]}"
    candidate = None
    if status == "ok":
        body, problems = validate(structured, bundle)
        audit["rejected"] = problems
        if body is not None:
            candidate = _finish(body, bundle, source="ai", model=model, fallback_reason=None, clock=clock)
        else:
            audit["failure"] = "validation_failed"
    else:
        audit["failure"] = status
        audit["detail"] = str(detail)[:300]
    if candidate is None:
        reason = "模型沒有回來" if status != "ok" else "核對沒過"
        candidate = {**template, "fallback_reason": reason}
    audit["source"] = candidate["source"]
    if mode == "dry":
        audit["candidate"] = candidate
        audit["template"] = template
        return None, audit
    return candidate, audit


def write_plain(path: Path, plain: Mapping[str, Any]) -> None:
    path.write_text(PREFIX + json.dumps(plain, ensure_ascii=False, separators=(",", ":")) + ";\n", encoding="utf-8")


def _read_assignment(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8").strip()
    return json.loads(text[text.index("=") + 1:].strip().rstrip(";"))


def main(argv: list[str] | None = None) -> int:
    """本機乾跑：讀一個大盤資料包資料夾（analysis-data.js、chips-data.js），把結果寫成檔案；不碰網站、不寫資料庫。"""
    import argparse
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("--market-dir", type=Path, required=True)
    parser.add_argument("--mode", choices=("dry", "on"), default="dry")
    parser.add_argument("--agy-bin", type=Path, help="沒給就不呼叫模型，只看程式套句")
    parser.add_argument("--agy-home", type=Path, help="agy 登入檔所在的 HOME（沒給就用目前的 HOME）")
    parser.add_argument("--model", default=MODEL_DEFAULT)
    parser.add_argument("--audit", type=Path, required=True, help="完整結果與核對紀錄（JSON）")
    parser.add_argument("--write-plain", type=Path, help="on 模式下把要發布的那份寫成 plain-data.js")
    args = parser.parse_args(argv)
    analysis = _read_assignment(args.market_dir / "analysis-data.js")
    chips = _read_assignment(args.market_dir / "chips-data.js")
    caller = agy_caller(args.agy_bin, home=args.agy_home, model=args.model)
    plain, audit = produce(analysis, chips, mode=args.mode, call_model=caller, model=args.model)
    args.audit.write_text(json.dumps({**audit, "published": plain}, ensure_ascii=False, indent=2), encoding="utf-8")
    if plain is not None and args.write_plain:
        write_plain(args.write_plain, plain)
    print(json.dumps({"mode": audit["mode"], "source": audit["source"], "failure": audit["failure"],
                      "rejected": audit["rejected"][:5]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
