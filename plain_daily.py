"""首頁「籌碼與資金」的白話分析：每天收盤資料建好後，依當天數字重寫（本人 2026-11-17 定案，取代 11-06「文字人寫」）。

流程（cloud/market_pack_daily.py 在大盤資料包建好、截止日核對過之後呼叫 ``rewrite_pack``）：
1. ``build_facts``：程式從當天的大盤日K（analysis-data.js）與籌碼（chips-data.js）算出一張事實表。每一項事實有兩種寫法：
   ``short`` 給六點裡用（那一點的標題已經說了主角，例如「現貨今天賣超 912 億」），
   ``text`` 給結論、擔心、安心用（自帶主角，例如「外資現貨今天賣超 912 億」）。數字、單位、方向、主角都由程式排好，
   模型沒辦法把一個數字掛到別的主角身上。
   只用證交所、期交所的公開總表數字；不碰個股名單、任何人的自選股或庫存，也不用 Fugle。
2. Gemini（官方 agy＋本人 AI Pro 訂閱）照同一張事實表寫白話：自由文字**不得有任何數字、日期、代號**，
   要提數字就寫 ``{事實代號}``，程式核對代號存在、屬於該段的主題、方向沒有寫反，再換成事實短句。
   這是 ai_digest.py「自由文字不得含數字、事實由程式依 refs 補上」的同一套做法，換成句中代入，讀起來才像人話。
3. 模型失敗、逾時、或任何一項核對不過：整份改用 ``template_raw`` 的程式套句（同一個核對器也要通過），
   畫面標「依當天數字自動整理」，不冒充 AI；一天只呼叫一次模型、不重試。
4. 開關 MARKET_ANA_PLAIN_DAILY（repository variable，獨立於策略總開關）：
   off（預設，含沒設、拼錯）＝不呼叫模型，沿用人寫那份；dry＝呼叫模型並核對，結果只進執行紀錄，網站照舊；
   on＝發布當天重寫的那份。模型與思考程度由 workflow 一處常數（PLAIN_MODEL、PLAIN_EFFORT）決定。
"""
from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai_digest import _validate_text as free_text_problem
from ledger_core import call_model as call_agy

SCHEMA = "market-plain/v2"
# 本人 11-17 更正：這張用 Flash、思考程度 medium（和其他 agy 呼叫一樣）。workflow 用 PLAIN_MODEL／PLAIN_EFFORT 覆寫。
MODEL_DEFAULT = "gemini-3.8-flash"
EFFORT_DEFAULT = "medium"
MODEL_TIMEOUT_SECONDS = 360
MODES = ("off", "dry", "on")
PREFIX = "window.MARKET_PLAIN = "

# 首頁六個重點的順序就是畫面順序（直白分析卡顯示前四個，詳情顯示全部）；標題由程式加上「主角：」。
POINT_KEYS = ("foreign", "trust", "margin", "short", "position", "volume")
SUBJECTS = {"foreign": "外資", "trust": "投信", "margin": "融資", "short": "融券", "position": "位置", "volume": "成交量"}
# 每一點只能代入自己主題的事實（另加日期）；結論、擔心、安心三段可以用全部，但代入的是自帶主角的完整句。
SECTION_TOPICS = {
    "foreign": ("foreign", "futures", "date"),
    "trust": ("trust", "date"),
    "margin": ("margin", "date"),
    "short": ("short", "date"),
    "position": ("position", "date"),
    "volume": ("volume", "position", "date"),
}
# 含「一」但不是數量的常用詞：先拿掉再查數字（其餘中文數字一律不准，例如「兩邊」「十分」「一天」）。
YI_WORDS = ("進一步", "一直", "一些", "一樣", "一起", "一般", "一定", "一旦", "一致", "一度", "一路", "同一", "唯一",
            "單一", "一邊", "一面", "一時", "一再")
PLACEHOLDER = re.compile(r"\{([a-z0-9_]+)\}")
LIMITS = {"one_line": 80, "title": 24, "line": 90, "item": 80}
# 交給 agy 子行程的環境變數不帶任何金鑰（每日更新那一步的環境裡有 Cloudflare、R2 的金鑰）。
SECRET_ENV_PARTS = ("TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "KEY", "R2_", "CLOUDFLARE", "AGY_LOGIN", "PRIVATE")

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


def _flow(value: float) -> str:
    """「賣超 912 億」；零就是「買賣持平」。"""
    return _side(value) + ("" if value == 0 else f" {_yi(value)}")


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

class _Facts:
    """事實表：每一項有主題、點內短句（short）、自帶主角的完整句（text）。"""

    def __init__(self) -> None:
        self.items: dict[str, dict[str, str]] = {}

    def add(self, key: str, topic: str, short: str, subject: str = "") -> None:
        self.items[key] = {"topic": topic, "short": short, "text": f"{subject}{short}"}


def build_facts(analysis: Mapping[str, Any], chips: Mapping[str, Any]) -> dict[str, Any]:
    """當天的事實表：{facts, signals: 方向旗標, data_dates}。資料不夠就丟例外（呼叫端改沿用舊版）。"""
    daily = [row for row in analysis["daily"] if isinstance(row.get("close"), (int, float))]
    foreign_rows, futures_rows, margin_rows = list(chips["foreign"]), list(chips["futures"]), list(chips["margin"])
    if len(daily) < 21 or len(foreign_rows) < 20 or len(futures_rows) < 6 or len(margin_rows) < 21:
        raise ValueError("資料天數不足，算不出白話分析要的事實")
    facts = _Facts()
    # 資料日以加權指數那一天為準；外資、期貨、融資融券各記自己的日子，落後時句子裡和畫面小字都另外標。
    price_day = daily[-1]["date"]
    foreign_day, futures_day, margin_day = foreign_rows[-1]["date"], futures_rows[-1]["date"], margin_rows[-1]["date"]
    facts.add("price_date", "date", _md(price_day))
    facts.add("margin_date", "date", _md(margin_day))
    when = (lambda day: "今天" if day >= price_day else f" {_md(day)} ")

    # 外資現貨
    nets = [float(row["net_billion"]) for row in foreign_rows]
    foreign_now, foreign_streak = nets[-1], _streak(nets)
    facts.add("foreign_today", "foreign", f"現貨{when(foreign_day)}{_flow(foreign_now)}".replace("  ", " "), "外資")
    if foreign_streak >= 2:
        facts.add("foreign_streak", "foreign", f"現貨連 {foreign_streak} 天{_side(foreign_now)}", "外資")
    elif foreign_now and _sign(nets[-2]) == -_sign(foreign_now):   # 前一天是反方向才叫「轉」；前一天持平就不說
        facts.add("foreign_streak", "foreign", "現貨由賣轉買" if foreign_now > 0 else "現貨由買轉賣", "外資")
    f5, f20 = sum(nets[-5:]), sum(nets[-20:])
    facts.add("foreign_5d", "foreign", f"現貨近 5 個交易日{_side(f5)}約 {_yi(f5)}" if f5 else "現貨近 5 個交易日買賣持平", "外資")
    facts.add("foreign_20d", "foreign", f"現貨近 20 個交易日累計{_side(f20)}約 {_yi(f20)}" if f20 else "現貨近 20 個交易日買賣持平", "外資")

    # 外資台指期淨部位（負數＝淨空單）。「淨空單／淨多單」只有期貨用，點內短句也不會和現貨、融券搞混。
    fut_now, fut_week = float(futures_rows[-1]["net_contracts"]), float(_ago(futures_rows, 5)["net_contracts"])
    side = "淨空單" if fut_now < 0 else "淨多單"
    lag = f"（{_md(futures_day)}）" if futures_day < price_day else ""
    facts.add("futures_net", "futures", f"台指期{side} {_num(abs(fut_now))} 口{lag}", "外資")
    held_now, held_week = (-fut_now, -fut_week) if fut_now < 0 else (fut_now, fut_week)
    diff = held_now - held_week
    facts.add("futures_week", "futures", f"{side}比一週前{'增加' if diff > 0 else '減少'} {_num(abs(diff))} 口" if round(diff)
              else f"{side}和一週前差不多", "外資台指期")
    contracts = [float(row["net_contracts"]) for row in futures_rows]
    growing = 0
    for before, after in zip(reversed(contracts[:-1]), reversed(contracts[1:])):
        if (fut_now < 0 and after < before) or (fut_now >= 0 and after > before):
            growing += 1
        else:
            break
    if growing >= 2:
        facts.add("futures_streak", "futures", f"{side}連 {growing} 天增加", "外資台指期")

    # 投信（和外資同一份三大法人總表）
    trusts = [float(row["trust_billion"]) for row in foreign_rows]
    t5 = sum(trusts[-5:])
    facts.add("trust_today", "trust", f"{when(foreign_day)}{_flow(trusts[-1])}".strip(), "投信")
    facts.add("trust_5d", "trust", f"近 5 個交易日{_side(t5)}約 {_yi(t5)}" if t5 else "近 5 個交易日買賣持平", "投信")
    trust_cover = t5 / abs(f5) if f5 < 0 < t5 else None
    if trust_cover is not None:
        facts.add("trust_vs_foreign", "trust", f"買超金額約是同期外資賣超的 {_pct(trust_cover * 100)}", "投信")

    # 融資
    balances = [float(row["margin_balance_billion"]) for row in margin_rows]
    m_change = balances[-1] - balances[-2]
    facts.add("margin_balance", "margin", f"餘額 {_yi(balances[-1], trillion=False)}", "融資")
    facts.add("margin_change", "margin", f"比前一天{'增加' if m_change > 0 else '減少'} {_yi(m_change)}" if round(m_change)
              else "和前一天差不多", "融資餘額")
    peak = max(range(len(margin_rows)), key=lambda i: balances[i])
    facts.add("margin_peak", "margin", f"餘額是近 {len(margin_rows)} 個交易日最高" if peak == len(margin_rows) - 1
              else f"餘額比 {_md(margin_rows[peak]['date'])} 的高點 {_yi(balances[peak])}少了 {_yi(balances[peak] - balances[-1])}", "融資")
    changes = [after - before for before, after in zip(balances, balances[1:])]
    if m_change < 0 and m_change == min(changes):
        facts.add("margin_rank", "margin", f"單日減少金額是近 {len(margin_rows)} 個交易日最多", "融資")
    elif m_change > 0 and m_change == max(changes):
        facts.add("margin_rank", "margin", f"單日增加金額是近 {len(margin_rows)} 個交易日最多", "融資")

    # 融券
    shorts = [float(row["short_units"]) for row in margin_rows]
    s_change, s20 = shorts[-1] - shorts[-2], float(_ago(margin_rows, 20)["short_units"])
    facts.add("short_units", "short", f"餘額約 {_lots(shorts[-1])}", "融券")
    facts.add("short_change", "short", f"比前一天{'增加' if s_change > 0 else '減少'}約 {_lots(s_change)}" if s_change
              else "和前一天一樣", "融券餘額")
    if s20:
        ratio = (shorts[-1] / s20 - 1) * 100
        facts.add("short_vs_20d", "short", f"餘額比 20 個交易日前{'多' if ratio > 0 else '少'} {_pct(ratio)}" if round(ratio, 1)
                  else "餘額和 20 個交易日前差不多", "融券")

    # 指數位置
    today, prev = daily[-1], daily[-2]
    close, prev_close = float(today["close"]), float(prev["close"])
    change = close - prev_close
    facts.add("close", "position", f"收 {_num(close)} 點", "加權指數")
    facts.add("index_change", "position", f"{'上漲' if change > 0 else '下跌'} {_num(abs(change))} 點（{_pct(change / prev_close * 100)}）"
              if round(change) else "和前一天差不多", "加權指數")
    high, low = float(today.get("high") or close), float(today.get("low") or close)
    facts.add("day_high", "position", f"盤中最高 {_num(high)} 點", "今天")
    facts.add("day_low", "position", f"盤中最低 {_num(low)} 點", "今天")
    spot = (close - low) / (high - low) if high > low else 0.5
    facts.add("close_in_range", "position", "收在當天高檔" if spot >= 2 / 3 else "收在當天低檔" if spot <= 1 / 3 else "收在當天中間", "加權指數")
    move = (close / float(daily[-21]["close"]) - 1) * 100
    facts.add("change_20d", "position", f"近 20 個交易日{'上漲' if move > 0 else '下跌'} {_pct(move)}" if round(move, 1)
              else "近 20 個交易日幾乎沒動", "加權指數")
    closes = [float(row["close"]) for row in daily]
    signals: dict[str, Any] = {}
    for n, label in ((20, "20 日均線"), (60, "60 日均線"), (240, "年線")):
        average = _ma(closes, n)
        if average is None:
            continue
        below = close < average
        signals[f"below_ma{n}"] = below
        facts.add(f"ma{n}_line", "position", f"{label}（約 {_num(average)} 點）")
        facts.add(f"ma{n}_side", "position", f"在{' ' if label[0].isdigit() else ''}{label}之{'下' if below else '上'}", "加權指數")

    # 成交金額（和首頁卡片同一個算法：最近 20 天含今天的平均）
    amounts = [float(row["amount"]) / 1e8 for row in daily[-20:] if isinstance(row.get("amount"), (int, float))]
    if amounts:
        ratio = amounts[-1] / (sum(amounts) / len(amounts))
        signals["amount_ratio"] = ratio
        facts.add("amount", "volume", f"成交 {_yi(amounts[-1], trillion=False)}", "大盤")
        facts.add("amount_ratio", "volume", f"是近 20 個交易日平均的 {ratio:.1f} 倍", "成交金額")

    signals.update({
        "foreign": _sign(foreign_now), "foreign_5d": _sign(f5), "foreign_streak": foreign_streak,
        "futures_short": fut_now < 0, "futures_growing": diff > 0 and bool(round(diff)),
        "trust_5d": _sign(t5), "trust_cover": trust_cover, "margin": _sign(round(m_change)), "short": _sign(s_change),
        "index": _sign(round(change)), "margin_lagging": margin_day < price_day,
    })
    return {"data_dates": {"price_and_foreign": price_day, "foreign": foreign_day, "futures": futures_day, "margin": margin_day},
            "facts": facts.items, "signals": signals}


# ---- 2. 核對 ----

def _contradictions(signals: Mapping[str, Any]) -> list[tuple[set[str], tuple[str, ...]]]:
    """（套用的段落, 不可出現的字）：描述「今天」的結論與六點不可以把方向寫反；擔心、安心是假設條件，不查。"""
    checks: list[tuple[set[str], tuple[str, ...]]] = []
    everywhere = {"one_line", *POINT_KEYS}
    if signals["foreign"] < 0:
        checks.append((everywhere, ("外資買超", "外資轉買", "外資買進", "外資大買", "外資回補", "外資進場", "外資回流",
                                    "現貨買超", "現貨轉買", "轉為買超")))
    elif signals["foreign"] > 0:
        checks.append((everywhere, ("外資賣超", "外資轉賣", "外資連賣", "外資賣出", "外資大賣", "外資出場", "外資撤出",
                                    "外資提款", "現貨賣超", "現貨轉賣", "現貨連賣", "轉為賣超")))
    if signals["futures_short"] and signals["futures_growing"]:
        checks.append(({"one_line", "foreign"}, ("空單減少", "空單下降", "空單縮小", "空單變少", "空單回補")))
    elif signals["futures_short"]:
        checks.append(({"one_line", "foreign"}, ("空單增加", "空單變多", "空單擴大", "空單還在加", "加空")))
    if signals["margin"] < 0:
        checks.append((everywhere, ("融資增加", "融資回升", "融資變多")))
    elif signals["margin"] > 0:
        checks.append((everywhere, ("融資減少", "融資下降", "融資大減", "融資變少")))
    if signals["short"] < 0:
        checks.append(({"short"}, ("融券增加", "融券變多", "空單增加", "空單變多")))
    elif signals["short"] > 0:
        checks.append(({"short"}, ("融券減少", "融券變少", "空單回補", "空單減少")))
    if signals["index"] < 0:
        checks.append((everywhere, ("收紅", "收漲", "上漲作收")))
    elif signals["index"] > 0:
        checks.append((everywhere, ("收黑", "收跌", "下跌作收")))
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
    bare = prose
    for word in YI_WORDS:
        bare = bare.replace(word, "")
    reason = free_text_problem(bare) if bare.strip() else None   # 空白、禁止字樣、數字：和 ai_digest 同一個檢查
    if reason:
        return f"{section}：{reason}"
    for sections, terms in _contradictions(bundle["signals"]):
        if section in sections:
            hit = next((term for term in terms if term in prose), None)
            if hit:
                return f"{section}：「{hit}」和當天數字的方向矛盾"
    return None


def _render(text: str, bundle: Mapping[str, Any], *, form: str) -> str:
    return PLACEHOLDER.sub(lambda match: bundle["facts"][match.group(1)][form], text).strip()


def validate(output: Any, bundle: Mapping[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    """全部通過才回傳可發布的內容（數字已代入）；任何一處不過就整份不用（回傳 None 與原因）。"""
    if not isinstance(output, dict) or set(output) != set(OUTPUT_SCHEMA["required"]):
        return None, ["最外層欄位不對"]
    problems: list[str | None] = []
    if output.get("data_date") != bundle["data_dates"]["price_and_foreign"]:
        problems.append("資料日和當天資料不符")
    points = output.get("points")
    if (not isinstance(points, list) or len(points) != len(POINT_KEYS) or any(not isinstance(p, dict) for p in points)
            or sorted(str(p.get("key")) for p in points) != sorted(POINT_KEYS)):
        return None, [p for p in problems if p] + ["points 要剛好六點、每個主題一次"]
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
    found = [problem for problem in problems if problem]
    if found:
        return None, found
    by_key = {point["key"]: point for point in points}
    return {
        "one_line": _render(output["one_line"], bundle, form="text"),
        "points": [{"key": key, "title": f"{SUBJECTS[key]}：{_render(by_key[key]['title'], bundle, form='short')}",
                    "lines": [_render(line, bundle, form="short") for line in by_key[key]["lines"]]} for key in POINT_KEYS],
        "worry": [_render(item, bundle, form="text") for item in output["worry"]],
        "relief": [_render(item, bundle, form="text") for item in output["relief"]],
    }, []


# ---- 3. 程式套句（模型失敗時用；也走同一個核對器） ----

def template_raw(bundle: Mapping[str, Any]) -> dict[str, Any]:
    facts, signals = bundle["facts"], bundle["signals"]
    has = facts.__contains__
    selling, buying = signals["foreign"] < 0, signals["foreign"] > 0
    futures_bear = signals["futures_short"] and signals["futures_growing"]
    below20, below60 = signals.get("below_ma20"), signals.get("below_ma60")
    spot_word = "現貨在賣" if selling else "現貨在買" if buying else "現貨買賣持平"
    futures_word = ("期貨空單還在加" if futures_bear else "期貨空單沒有再加") if signals["futures_short"] else "期貨是淨多單"
    lag = "（資料到 {margin_date}）" if signals["margin_lagging"] else ""

    weak, strong = [], []
    (weak if selling else strong).append(f"外資{spot_word}")
    (weak if futures_bear else strong).append(futures_word)
    if below20 is not None:
        (weak if below20 else strong).append("{ma20_side}")
    if len(weak) >= 2:
        one_line = "短線偏弱：" + "、".join(weak) + ("；但" + "、".join(strong) if strong else "") + "。"
    elif len(strong) >= 2:
        one_line = "短線偏強：" + "、".join(strong) + ("；但" + "、".join(weak) if weak else "") + "。"
    else:
        one_line = "多空訊號分歧：" + "、".join(weak + strong) + "。"

    foreign_lines = ["，".join(f"{{{key}}}" for key in ("foreign_streak", "foreign_today", "foreign_20d") if has(key)) + "。",
                     "，".join(f"{{{key}}}" for key in ("futures_net", "futures_week", "futures_streak") if has(key)) + "。"]
    if selling and futures_bear:
        foreign_lines.append("現貨和期貨同時偏空，賣壓主要來自外資；是看壞還是避險，光看數字分不出來。")
    elif selling or futures_bear:
        foreign_lines.append("現貨和期貨的方向不同，外資的態度還不明確。")

    cover = signals.get("trust_cover")
    if signals["trust_5d"] > 0:
        trust_title = "和外資一起買" if cover is None else "有在買，但抵不過外資賣" if cover < 1 else "買得比外資賣得多"
    elif signals["trust_5d"] < 0:
        trust_title = "也在賣" if signals["foreign_5d"] < 0 else "在賣"
    else:
        trust_title = "幾乎沒動作"
    trust_lines = ["{trust_5d}，{trust_today}。"] + (["{trust_vs_foreign}。"] if has("trust_vs_foreign") else [])

    margin_lines = ["{margin_balance}，{margin_change}" + lag + "。", "{margin_peak}。"]
    margin_lines += ["{margin_rank}。"] if has("margin_rank") else []
    margin_lines += {-1: ["槓桿變低，之後的賣壓會減輕；但也代表有資金離場。"],
                     1: ["借錢買股的人變多，行情轉弱時賣壓會比較重。"]}.get(signals["margin"], [])

    short_lines = ["{short_units}，{short_change}" + lag + "。"] + (["{short_vs_20d}。"] if has("short_vs_20d") else [])
    short_lines += {-1: ["放空的人買回，融券減少；剩下的空單之後也要買回。"],
                    1: ["放空的人變多，之後買回時會變成買盤。"]}.get(signals["short"], [])

    position_title = ("短線偏弱" if below20 and below60 else "短線偏強" if below20 is False and below60 is False else "短線多空拉鋸")
    if "below_ma240" in signals:
        position_title += "，已在年線之下" if signals["below_ma240"] else "，還在年線之上"
    sides = "、".join(f"{{ma{n}_side}}" for n in (20, 60, 240) if has(f"ma{n}_side"))
    position_lines = ["今天{close}，{index_change}，{close_in_range}。", "指數{change_20d}。"] + ([f"收盤{sides}。"] if sides else [])

    ratio = signals.get("amount_ratio")
    if ratio is None:
        volume_title, volume_lines = "今天沒有成交金額資料", ["成交金額還沒公布。"]
    else:
        volume_title = "量比平常大" if ratio >= 1.2 else "量比平常小" if ratio <= 0.8 else "量和平常差不多"
        volume_lines = ["{amount}，{amount_ratio}。"] + (["量大代表換手劇烈，要看接下來幾天能不能延續。"] if ratio >= 1.2 else [])

    worry = (["外資期貨空單還在增加。"] if futures_bear else [])
    if selling:
        worry.append("{foreign_streak}，賣壓還沒停。" if signals["foreign_streak"] >= 2 else "外資現貨還在賣。")
    worry.append("收盤跌破{day_low}，短線可能再走弱。")
    relief = (["收盤站回 {ma20_line}。"] if below20 and has("ma20_line") else [])
    relief.append("外資現貨轉為買超，或期貨空單開始減少。" if selling or futures_bear else "外資現貨持續買超、期貨空單沒有增加。")

    return {
        "data_date": bundle["data_dates"]["price_and_foreign"],
        "one_line": one_line,
        "points": [
            {"key": "foreign", "title": "現貨和期貨都偏空" if selling and futures_bear else f"{spot_word}，{futures_word}", "lines": foreign_lines},
            {"key": "trust", "title": trust_title, "lines": trust_lines},
            {"key": "margin", "title": {-1: "散戶借錢買股的錢在減少", 1: "散戶借錢買股的錢在增加"}.get(signals["margin"], "借錢買股的錢變化不大"),
             "lines": margin_lines},
            {"key": "short", "title": {-1: "空單在回補", 1: "空單在增加"}.get(signals["short"], "空單變化不大"), "lines": short_lines},
            {"key": "position", "title": position_title, "lines": position_lines},
            {"key": "volume", "title": volume_title, "lines": volume_lines},
        ],
        "worry": worry[:3],
        "relief": relief[:3],
    }


def _stamp_plain(body: Mapping[str, Any], bundle: Mapping[str, Any], *, source: str, model: str | None,
                 fallback_reason: str | None, clock: Callable[[], datetime]) -> dict[str, Any]:
    """核對過的內容加上資料日、來源、產生時間，成為 plain-data.js 的整份內容。"""
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
    return _stamp_plain(body, bundle, source="template", model=None, fallback_reason=fallback_reason, clock=clock)


# ---- 4. 模型 ----

def build_prompt(bundle: Mapping[str, Any]) -> str:
    facts = {key: {"topic": fact["topic"], "在六點裡代入成": fact["short"], "在結論擔心安心代入成": fact["text"]}
             for key, fact in bundle["facts"].items()}
    topics = "；".join(f"{key}（{SUBJECTS[key]}）只能用 topic 為 {'、'.join(SECTION_TOPICS[key])} 的代號" for key in POINT_KEYS)
    rules = [
        "你是台股盤後籌碼整理員，替一般投資人寫首頁「籌碼與資金」的白話分析。只能根據 FACTS，不可連網、不可使用工具，不可加入 FACTS 沒有的事實（美股、新聞、個股、傳聞都不要寫）。",
        "所有文字一律不得出現阿拉伯數字、全形數字、中文數字（一、兩、十、百、千、萬、億、兆等）、日期、股票代號或百分比。要提到數字或日期時寫 {代號}；程式會換成核對過的句子（六點裡換成「在六點裡代入成」那一句，結論、擔心、安心換成「在結論擔心安心代入成」那一句）。只能用 FACTS 裡有的代號，換進去後要讀得通順，不要在代號前後重複寫主角。",
        f"只有這些含「一」的詞可以用：{'、'.join(YI_WORDS)}；其他像「一天」「一個月」「兩邊」都不可以，請改寫（例如「逐日增加」「近期」「現貨和期貨」）。",
        f"points 剛好六點，key 各一次：{topics}。",
        "title 是一句短判斷，不要寫主角也不要冒號（程式會自動加上「外資：」這類前綴）。lines 一到四句短句。",
        "one_line 是一句話結論。worry 是之後要擔心的情況、relief 是什麼情況下比較安心，各一到三項；跟外資有關的擔心要寫出「外資」二字。",
        "方向不可以和 FACTS 相反（例如外資賣超時不可寫外資買進、融資減少時不可寫融資增加）。分不出原因的事不要說死，例如外資是看壞還是避險、融資減少是被追繳還是停損。",
        "不寫投資建議或保證，不寫模型、提示詞、方法、參考站、會員、反推等字樣。繁體中文、白話、短句。",
        f"data_date 填 {bundle['data_dates']['price_and_foreign']}。",
    ]
    return "\n".join([*rules, "DATA_DATES:", json.dumps(bundle["data_dates"], ensure_ascii=False),
                      "FACTS:", json.dumps(facts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))])


ModelAsker = Callable[[str, Mapping[str, Any]], tuple[str, Any, str]]


def agy_environment(environ: Mapping[str, str], home: Path | str | None) -> dict[str, str]:
    """給 agy 子行程的環境：拿掉所有像金鑰的變數；有隔離的登入 HOME 就換上。"""
    env = {name: value for name, value in environ.items()
           if not any(part in name.upper() for part in SECRET_ENV_PARTS)}
    if home:
        env["HOME"] = str(home)
    return env


def agy_caller(agy_bin: Path | str | None, *, home: Path | str | None, model: str = MODEL_DEFAULT,
               effort: str = EFFORT_DEFAULT, timeout: float = MODEL_TIMEOUT_SECONDS) -> ModelAsker:
    """官方 agy（ledger_core.call_model 的串流模式）。登入檔放在隔離的 HOME，只給 agy 這個子行程用。"""
    def ask(prompt: str, schema: Mapping[str, Any]) -> tuple[str, Any, str]:
        if not agy_bin or not Path(agy_bin).exists():
            return "agy_unavailable", None, "agy 沒有安裝成功"
        return call_agy(agy_bin, prompt, model, timeout, schema, env=agy_environment(os.environ, home), effort=effort)
    return ask


def produce(analysis: Mapping[str, Any], chips: Mapping[str, Any], *, mode: str, ask_model: ModelAsker,
            model: str = MODEL_DEFAULT, effort: str = EFFORT_DEFAULT,
            clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
            ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """回傳（要發布的白話分析或 None＝沿用舊版, 執行紀錄）。永遠不丟例外：不能因為白話分析讓大盤資料包發布不了。"""
    mode = normalize_mode(mode)
    audit: dict[str, Any] = {"mode": mode, "model": model, "effort": effort, "source": None, "failure": None, "rejected": []}
    if mode == "off":
        return None, audit
    try:
        bundle = build_facts(analysis, chips)
    except Exception as error:  # noqa: BLE001
        audit["failure"] = f"facts_failed: {type(error).__name__}: {str(error)[:200]}"
        return None, audit
    try:
        template = template_plain(bundle, clock=clock)
    except Exception as error:  # noqa: BLE001
        audit["failure"] = f"template_failed: {str(error)[:300]}"
        return None, audit
    audit["data_dates"] = bundle["data_dates"]
    try:
        status, structured, detail = ask_model(build_prompt(bundle), OUTPUT_SCHEMA)
    except Exception as error:  # noqa: BLE001
        status, structured, detail = "model_failed", None, f"{type(error).__name__}: {str(error)[:200]}"
    candidate = None
    if status == "ok":
        body, problems = validate(structured, bundle)
        audit["rejected"] = problems
        if body is not None:
            candidate = _stamp_plain(body, bundle, source="ai", model=model, fallback_reason=None, clock=clock)
        else:
            audit["failure"] = "validation_failed"
    else:
        audit["failure"] = status
        audit["detail"] = str(detail)[:300]
    if candidate is None:
        candidate = {**template, "fallback_reason": "模型沒有回來" if status != "ok" else "核對沒過"}
    audit["source"] = candidate["source"]
    if mode == "dry":
        audit["candidate"] = candidate
        audit["template"] = template
        return None, audit
    return candidate, audit


def rewrite_pack(analysis: Mapping[str, Any], chips: Mapping[str, Any], *, day: str, environ: Mapping[str, str],
                 audit_dir: Path, ask_model: ModelAsker | None = None) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """每日更新用的入口：從環境變數讀模式、agy 位置、模型與思考程度，產生後把完整紀錄寫進 audit_dir/<資料日>.json。
    回傳（要發布的白話分析或 None, 印在大盤包報告最後一行的摘要）。永遠不丟例外。"""
    mode = normalize_mode(environ.get("PLAIN_DAILY_MODE"))
    if mode == "off":
        return None, {"mode": "off"}
    model = environ.get("PLAIN_MODEL") or MODEL_DEFAULT
    effort = environ.get("PLAIN_EFFORT") or EFFORT_DEFAULT
    if ask_model is None:
        ask_model = agy_caller(environ.get("PLAIN_AGY_BIN") or None, home=environ.get("PLAIN_AGY_HOME") or None,
                               model=model, effort=effort)
    plain, audit = produce(analysis, chips, mode=mode, ask_model=ask_model, model=model, effort=effort)
    summary = {"mode": mode, "model": model, "effort": effort, "source": audit.get("source"), "failure": audit.get("failure"),
               "rejected": len(audit.get("rejected") or []), "published": plain is not None}
    try:
        audit_dir.mkdir(parents=True, exist_ok=True)
        (audit_dir / f"{day}.json").write_text(json.dumps({"data_date": day, **audit, "published": plain},
                                                          ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as error:
        summary["audit_error"] = f"{type(error).__name__}: {error}"[:200]
    return plain, summary


def write_plain(path: Path, plain: Mapping[str, Any]) -> None:
    path.write_text(PREFIX + json.dumps(plain, ensure_ascii=False, separators=(",", ":")) + ";\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """本機乾跑：讀一個大盤資料包資料夾（analysis-data.js、chips-data.js），把結果寫成檔案；不碰網站、不寫資料庫。"""
    import argparse
    from cloud.market_pack_daily import _read_assignment
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("--market-dir", type=Path, required=True)
    parser.add_argument("--mode", choices=("dry", "on"), default="dry")
    parser.add_argument("--agy-bin", type=Path, help="沒給就不呼叫模型，只看程式套句")
    parser.add_argument("--agy-home", type=Path, help="agy 登入檔所在的 HOME（沒給就用目前的 HOME）")
    parser.add_argument("--model", default=MODEL_DEFAULT)
    parser.add_argument("--effort", default=EFFORT_DEFAULT)
    parser.add_argument("--audit", type=Path, required=True, help="完整結果與核對紀錄（JSON）")
    parser.add_argument("--write-plain", type=Path, help="on 模式下把要發布的那份寫成 plain-data.js")
    args = parser.parse_args(argv)
    analysis = _read_assignment(args.market_dir / "analysis-data.js", "window.MARKET_ANALYSIS = ")
    chips = _read_assignment(args.market_dir / "chips-data.js", "window.MARKET_CHIPS = ")
    ask = agy_caller(args.agy_bin, home=args.agy_home, model=args.model, effort=args.effort)
    plain, audit = produce(analysis, chips, mode=args.mode, ask_model=ask, model=args.model, effort=args.effort)
    args.audit.write_text(json.dumps({**audit, "published": plain}, ensure_ascii=False, indent=2), encoding="utf-8")
    if plain is not None and args.write_plain:
        write_plain(args.write_plain, plain)
    print(json.dumps({"mode": audit["mode"], "source": audit["source"], "failure": audit["failure"],
                      "rejected": audit["rejected"][:5]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
