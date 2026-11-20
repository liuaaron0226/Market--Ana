"""盯項：AI 文字裡「接下來要看的條件」由系統替本人盯（本人 2026-11-20，.scratch/impact-watch/spec.md，票 03）。

這一張票只有大盤的數字型，一條完整的路：
- ``market_facts``：從每日更新發布的大盤資料整理當天的數字（收盤、月季年線、近 20 日高低、外資買賣超與連續天數）。
- ``candidates``：依這些數字產生一份小而明確的「可盯條件清單」（每個有編號與人話名稱）。消息排程把清單交給模型，
  模型只能用編號挑（market_news.py；沿用預測帳本「用候選編號、數字由程式代入」的做法）。
- ``creation_sql``：把挑到的條件寫成盯項。同一對象同一條件只建一次；同時盯著的最多 MAX_WATCHING 件，
  超過時留影響力高的、一樣高留新的。
- ``evaluate``／``check_sql``：每個交易日盤後由程式判斷成立、否定或還沒，結果說明帶實際數字；建立後第
  EXPIRE_TRADING_DAYS 個交易日還沒結果就安靜收掉；有結果的在首頁多留 KEEP_RESULT_TRADING_DAYS 個交易日。
- ``python impact_watch.py check``：每日更新發布成功之後跑（daily-update.yml），順手把當天的數字存進網站資料庫
  （impact_watch_facts），下一班消息用它產生清單。
- ``plain_picks``（票 07）：首頁白話分析（plain_daily.py）也從同一份清單用編號挑條件，寫在剛發布的 plain-data.js 的
  ``track``；每日檢查判完之後把它們建成盯項（來源 SOURCE_PLAIN）。和消息建的走同一個 ``creation_sql``，所以自動去重、
  同一個上限。模型沒寫成、改用程式套句（source=template）的那一天不建。

資料表在 cloud/migrations/0034_impact_watch.sql；網站只讀（cloud/lib/impact-watch.js）。這裡不叫任何模型。
"""
from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import date, timedelta
from typing import Any

import trading_days as td

MAX_WATCHING = 10               # 同時盯著的最多幾件（大盤與之後的個股合計）
EXPIRE_TRADING_DAYS = 20        # 建立後第幾個交易日到期
KEEP_RESULT_TRADING_DAYS = 3    # 有結果後首頁多留幾個交易日
FACTS_FRESH_TRADING_DAYS = 2    # 數字比消息那一天舊超過幾個交易日就不出清單（每日檢查連續沒跑成）
KEEP_DAYS = 120                 # 收掉的盯項留多久（之後對帳用）
MARKET = "market"
SOURCE_NEWS = "market_news"
SOURCE_PLAIN = "plain_daily"    # 首頁白話分析挑的條件
PLAIN_IMPACT = 2                # 白話分析挑的條件算影響力「中」：不把消息裡影響大的擠掉
PLAIN_FILE = "plain-data.js"    # 和 --analysis 同一個資料夾裡的白話分析
MA_NAMES = {20: "月線", 60: "季線", 240: "年線"}
LEVEL_DAYS = 20
STREAK_TARGET = 3               # 連續買賣超至少盯到幾天
STREAK_AHEAD = 2                # 已經連續好幾天時，往後再盯幾天
FULL_NOTE = "名額讓給影響更大或更新的"
OWNER_ONLY = "owner"            # 個股盯項的可見範圍：只有站主（票 06）
STOCK_MA_NAMES = {20: "月線", 60: "季線"}   # 個股只盯月線與季線
FLOW_WHO = {"foreign": "外資", "trust": "投信"}
LOT = 1000                      # 個股法人買賣超的單位：股 → 張
REMOVED_NOTE = "已不在自選與庫存"

_CODE = re.compile(r"[0-9A-Za-z]{1,12}", re.ASCII)      # 股票代號（和 market_news.pending_stock_rows 同一個範圍）
_EMAIL = re.compile(r"[^@\s'\"\\;]+@[^@\s'\"\\;]+")       # 和 watch_news._EMAIL 同一個（email 會進 SQL 字面值）
_q = lambda value: "NULL" if value is None else "'" + str(value).replace("'", "''") + "'"  # noqa: E731


def _number(value: float) -> str:
    return f"{value:,.0f}"


def _month_day(day: str) -> str:
    return f"{int(day[5:7])}/{int(day[8:10])}"


def _hundred_million(value: float) -> str:
    return f"{abs(value):,.0f} 億"


# ---- 當天的數字 ----

def market_facts(daily: Sequence[Mapping[str, Any]], institutional: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """大盤當天的數字。daily：加權指數日 K（date、high、low、close）；institutional：三大法人（date、foreign.net，元）。
    日 K 不到 LEVEL_DAYS 根就回 None；某一條均線的根數不夠就沒有那一條；沒有法人資料就沒有 flow。"""
    rows = sorted((row for row in daily if row.get("date") and row.get("close")), key=lambda row: row["date"])
    if len(rows) < LEVEL_DAYS:
        return None
    closes = [float(row["close"]) for row in rows]
    recent = rows[-LEVEL_DAYS:]
    facts: dict[str, Any] = {
        "date": rows[-1]["date"], "close": round(closes[-1], 2),
        "ma": {str(days): round(sum(closes[-days:]) / days, 2) for days in MA_NAMES if len(closes) >= days},
        "high": round(max(float(row.get("high") or row["close"]) for row in recent), 2),
        "low": round(min(float(row.get("low") or row["close"]) for row in recent), 2),
    }
    flows = sorted((row for row in institutional if row.get("date") and isinstance(row.get("foreign"), Mapping)
                    and row["foreign"].get("net") is not None and row["date"] <= facts["date"]), key=lambda row: row["date"])
    if flows:
        nets = [float(row["foreign"]["net"]) / 100_000_000 for row in flows]
        side = "buy" if nets[-1] > 0 else "sell" if nets[-1] < 0 else "flat"
        days = 0
        for net in reversed(nets):
            if (side == "buy" and net > 0) or (side == "sell" and net < 0):
                days += 1
            else:
                break
        facts["flow"] = {"date": flows[-1]["date"], "net": round(nets[-1], 1), "side": side, "days": days}
    return facts


# ---- 可盯條件清單 ----

_SIDE_TEXT = {"buy": "買超", "sell": "賣超"}


def candidates(facts: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """這一天可以盯的大盤條件：[{id, name, now, params}]。只列現在還沒發生的；id 是條件編號（同一個編號同時只盯一筆）。
    now 是給模型看的現況（哪裡、差多少），不存進盯項。"""
    if not facts:
        return []
    close, as_of = float(facts["close"]), facts["date"]
    out: list[dict[str, Any]] = []
    for days, name in MA_NAMES.items():
        line = (facts.get("ma") or {}).get(str(days))
        if line is None:
            continue
        below = close < float(line)
        out.append({"id": f"{'above' if below else 'below'}_ma{days}",
                    "name": f"加權指數收盤{'站回' if below else '跌破'}{name}",
                    "now": f"收盤 {_number(close)}，在{name} {_number(float(line))} 之{'下' if below else '上'}",
                    "params": {"ma": days, "as_of": as_of}})
    high, low = float(facts["high"]), float(facts["low"])
    out.append({"id": "above_high", "name": f"加權指數收盤突破近 {LEVEL_DAYS} 日高點（{_number(high)}）",
                "now": f"收盤 {_number(close)}", "params": {"level": high, "as_of": as_of}})
    out.append({"id": "below_low", "name": f"加權指數收盤跌破近 {LEVEL_DAYS} 日低點（{_number(low)}）",
                "now": f"收盤 {_number(close)}", "params": {"level": low, "as_of": as_of}})
    flow = facts.get("flow")
    if flow and flow.get("side") in _SIDE_TEXT:
        side, other = flow["side"], "sell" if flow["side"] == "buy" else "buy"
        target = max(STREAK_TARGET, int(flow["days"]) + STREAK_AHEAD)
        now = f"外資已連續{_SIDE_TEXT[side]} {flow['days']} 天，最近一天{_SIDE_TEXT[side]} {_hundred_million(flow['net'])}"
        out.append({"id": f"foreign_{side}_streak", "name": f"外資連續{_SIDE_TEXT[side]}達 {target} 天", "now": now,
                    "params": {"days": target, "flow_as_of": flow["date"]}})
        out.append({"id": f"foreign_turn_{other}", "name": f"外資由{_SIDE_TEXT[side]}轉為{_SIDE_TEXT[other]}", "now": now,
                    "params": {"flow_as_of": flow["date"]}})
    return out


def offered(query: Callable[[str], list[dict]], trade_date: str, holidays: set[str] | None = None) -> list[dict[str, Any]]:
    """消息排程這一班可以交給模型的清單：讀每日檢查存下的數字。0034 還沒套、還沒有任何一次檢查、或數字太舊就回空清單
    （那一班照樣有消息，只是不建盯項）。"""
    try:
        rows = query(f"SELECT data_date, payload FROM impact_watch_facts WHERE subject = {_q(MARKET)}")
        facts = json.loads(rows[0]["payload"]) if rows else None
        if not isinstance(facts, Mapping) or not facts.get("date"):
            return []
        holidays = td.known_holidays(trade_date) if holidays is None else holidays
        if td.trading_days_between(facts["date"], trade_date, holidays) > FACTS_FRESH_TRADING_DAYS:
            return []
        return candidates(facts)
    except Exception:  # noqa: BLE001 — 表還沒建或內容壞了（缺欄位、日期格式不對）：不出清單，不能賠掉這一班的消息
        return []


# ---- 每日判斷 ----

def evaluate(item: Mapping[str, Any], facts: Mapping[str, Any]) -> tuple[str, str | None]:
    """一筆數字型盯項今天的狀態：("met"｜"failed"｜"watching", 結果說明)。只看建立之後才出現的資料
    （params 的 as_of／flow_as_of 是建立時依據的那一天）。均線用當天的值（均線每天在動）；價位用建立時定下的那一個。
    否定只有連續買賣超會出現（中途斷了）；站回、跌破、突破這類沒有「確定不會發生」，沒發生就等到期。"""
    key, params = str(item.get("condition_key") or ""), item.get("params") or {}
    close, day = float(facts["close"]), facts["date"]
    when = _month_day(day)
    if key.startswith(("above_", "below_")):
        if day <= str(params.get("as_of") or ""):
            return "watching", None
        above = key.startswith("above_")
        if "ma" in params:
            line, name = (facts.get("ma") or {}).get(str(params["ma"])), MA_NAMES.get(int(params["ma"]), "均線")
            if line is None:
                return "watching", None
            if (close >= float(line)) if above else (close < float(line)):
                return "met", f"成立：{when} 收 {_number(close)}，{'站回' if above else '跌破'}{name}（{_number(float(line))}）。"
            return "watching", None
        level = float(params["level"])
        if (close > level) if above else (close < level):
            return "met", (f"成立：{when} 收 {_number(close)}，{'突破' if above else '跌破'}近 {LEVEL_DAYS} 日"
                           f"{'高' if above else '低'}點 {_number(level)}。")
        return "watching", None
    flow = facts.get("flow")
    if not flow or flow["date"] <= str(params.get("flow_as_of") or ""):
        return "watching", None
    when, side = _month_day(flow["date"]), flow.get("side")
    today = "買賣超持平" if side not in _SIDE_TEXT else f"{_SIDE_TEXT[side]} {_hundred_million(flow['net'])}"
    for wanted in _SIDE_TEXT:
        if key == f"foreign_{wanted}_streak":
            target = int(params["days"])
            if side == wanted and int(flow["days"]) >= target:
                return "met", f"成立：外資連續{_SIDE_TEXT[wanted]} {flow['days']} 天，{when} {today}。"
            if side != wanted:
                return "failed", f"否定：{when} 外資{today}，連續{_SIDE_TEXT[wanted]}沒有延續到 {target} 天。"
            return "watching", None
        if key == f"foreign_turn_{wanted}":
            return ("met", f"成立：{when} 外資轉為{today}。") if side == wanted else ("watching", None)
    return "watching", None


def check_sql(items: Sequence[Mapping[str, Any]], facts: Mapping[str, Any], *, now: str,
              holidays: set[str] | None = None) -> tuple[str, dict[str, Any]]:
    """盯著的每一筆判一次 → 一份 SQL 與報告。成立、否定：記結果日、結果說明與首頁留到哪一天；到期日當天還沒結果：
    安靜收掉（到期日當天成立的算成立）；其餘更新還剩幾個交易日。順手存下當天的數字、清掉收了很久的。"""
    day = facts["date"]
    holidays = td.known_holidays(day) if holidays is None else holidays
    lines: list[str] = []
    report: dict[str, Any] = {"data_date": day, "checked": 0, "met": [], "failed": [], "expired": [], "watching": 0, "errors": []}
    for item in items:
        if item.get("status") != "watching" or item.get("kind") != "numeric" or item.get("subject") != MARKET:
            continue
        report["checked"] += 1
        try:
            parsed = {**item, "params": json.loads(item["params"]) if isinstance(item.get("params"), str) else item.get("params") or {}}
            status, note = evaluate(parsed, facts)
        except Exception as error:  # noqa: BLE001 — 一筆的參數壞了只跳過那一筆（照樣會到期），其餘照判
            report["errors"].append({"id": item["id"], "error": type(error).__name__})
            status, note = "watching", None
        where = f"WHERE id = {_q(item['id'])} AND status = 'watching';"
        if status in ("met", "failed"):
            keep = td.add_trading_days(day, KEEP_RESULT_TRADING_DAYS, holidays)
            lines.append(f"UPDATE impact_watch_items SET status = {_q(status)}, result_date = {_q(day)}, result_note = {_q(note)}, "
                         f"show_until = {_q(keep)}, days_left = NULL {where}")
            report[status].append({"id": item["id"], "note": note})
        elif day >= item["expires_on"]:
            lines.append(f"UPDATE impact_watch_items SET status = 'expired', result_date = {_q(day)}, days_left = NULL {where}")
            report["expired"].append(item["id"])
        else:
            left = td.trading_days_between(day, item["expires_on"], holidays)
            lines.append(f"UPDATE impact_watch_items SET days_left = {left} {where}")
            report["watching"] += 1
    # 重跑舊的一天不把已經存下的新數字蓋回去
    lines.append("INSERT OR REPLACE INTO impact_watch_facts (subject, data_date, payload, updated_at) SELECT "
                 f"{_q(MARKET)}, {_q(day)}, {_q(json.dumps(facts, ensure_ascii=False))}, {_q(now)} WHERE NOT EXISTS "
                 f"(SELECT 1 FROM impact_watch_facts WHERE subject = {_q(MARKET)} AND data_date > {_q(day)});")
    cutoff = (date.fromisoformat(day) - timedelta(days=KEEP_DAYS)).isoformat()
    lines.append(f"DELETE FROM impact_watch_items WHERE status <> 'watching' AND result_date < {_q(cutoff)};")
    return "\n".join(lines) + "\n", report


# ---- 建立 ----

def active_items(query: Callable[[str], list[dict]], trade_date: str) -> list[dict[str, Any]]:
    """還算數的盯項：盯著的，和有結果、首頁還留著的；另外帶上今天建過的（不論狀態，建立時用來避開同一個識別碼）。"""
    return query("SELECT id, subject, condition_key, impact, created_date, status FROM impact_watch_items "
                 f"WHERE status = 'watching' OR (status IN ('met', 'failed') AND show_until >= {_q(trade_date)}) "
                 f"OR created_date = {_q(trade_date)}")


def creation_sql(picks: Sequence[Mapping[str, Any]], active: Sequence[Mapping[str, Any]], *, trade_date: str, now: str,
                 holidays: set[str] | None = None, source: str = SOURCE_NEWS) -> tuple[str, dict[str, Any]]:
    """模型挑到的條件 → 新盯項的 SQL 與報告。source：哪一塊 AI 文字挑的（消息或白話分析）。
    picks：[{condition_key, label, params, note, impact, prepare, source_ref}]；對象預設是大盤，站主個股的另外帶
    subject＝股票代號（票 06）。個股盯項一律只給站主看（visibility 由這裡依對象決定，不看 pick 怎麼寫）。
    - 同一個條件這一班被挑好幾次：留影響力最高的那一次；
    - 已經在盯、或剛有結果首頁還留著的條件：不再建；今天建過又收掉的（讓位、當天就有結果）今天也不再建；
    - 盯著的合計超過 MAX_WATCHING：依影響力高到低、一樣高時新的先，排在後面的收掉（舊的）或不建（新的）。"""
    holidays = td.known_holidays(trade_date) if holidays is None else holidays
    best: dict[tuple[str, str], Mapping[str, Any]] = {}
    for pick in picks:
        slot = (str(pick.get("subject") or MARKET), pick["condition_key"])
        if slot not in best or int(pick["impact"]) > int(best[slot]["impact"]):
            best[slot] = pick
    taken = {(row["subject"], row["condition_key"]) for row in active if row["status"] in ("watching", "met", "failed")}
    made_today = {row["id"] for row in active}
    fresh = [{**pick, "subject": subject} for (subject, key), pick in best.items()
             if (subject, key) not in taken and f"{subject}:{key}:{trade_date}" not in made_today]
    watching = [row for row in active if row["status"] == "watching"]
    ranked = sorted([*({"new": pick, "impact": int(pick["impact"]), "created_date": trade_date} for pick in fresh),
                     *({"old": row, "impact": int(row["impact"]), "created_date": row["created_date"]} for row in watching)],
                    key=lambda entry: (-entry["impact"], -date.fromisoformat(entry["created_date"]).toordinal(), "old" in entry))
    kept, over = ranked[:MAX_WATCHING], ranked[MAX_WATCHING:]
    expires = td.add_trading_days(trade_date, EXPIRE_TRADING_DAYS, holidays)
    lines = [f"UPDATE impact_watch_items SET status = 'expired', result_date = {_q(trade_date)}, result_note = {_q(FULL_NOTE)}, "
             f"days_left = NULL WHERE id = {_q(entry['old']['id'])} AND status = 'watching';" for entry in over if "old" in entry]
    created = []
    for entry in kept:
        pick = entry.get("new")
        if not pick:
            continue
        subject = pick["subject"]
        item_id = f"{subject}:{pick['condition_key']}:{trade_date}"
        created.append(item_id)
        values = [item_id, source, trade_date, pick.get("source_ref"), subject, "numeric", pick["condition_key"],
                  json.dumps(pick.get("params") or {}, ensure_ascii=False), pick["label"], pick.get("note"), int(pick["impact"]),
                  json.dumps(list(pick["prepare"]), ensure_ascii=False) if pick.get("prepare") else None,
                  trade_date, expires, EXPIRE_TRADING_DAYS, "watching", "all" if subject == MARKET else OWNER_ONLY, now]
        # 同一班重跑、或另一支排程剛建了同一個條件：不重複建（條件編號同時只有一筆盯著的）
        lines.append("INSERT OR IGNORE INTO impact_watch_items (id, source_block, source_date, source_ref, subject, kind, condition_key, params, "
                     "label, note, impact, prepare, created_date, expires_on, days_left, status, visibility, created_at) SELECT "
                     + ", ".join(str(value) if isinstance(value, int) else _q(value) for value in values)
                     + f" WHERE NOT EXISTS (SELECT 1 FROM impact_watch_items WHERE subject = {_q(subject)} AND condition_key = "
                     f"{_q(pick['condition_key'])} AND (status = 'watching' OR (status IN ('met', 'failed') AND show_until >= {_q(trade_date)})));")
    report = {"picked": len(best), "created": created, "already_watching": len(best) - len(fresh),
              "over_limit": len([entry for entry in over if "new" in entry]),
              "closed_for_room": [entry["old"]["id"] for entry in over if "old" in entry]}
    return ("\n".join(lines) + "\n" if lines else ""), report


# ---- 站主自選股與庫存的盯項（票 06）：只有站主看得到 ----
# 一條路：每日檢查用個股頁同一套分析（twon_analysis.analyze_v2，只讀本機行情庫）整理站主每一檔當天的數字，存進
# impact_watch_facts（subject＝股票代號）；消息排程讀它產生每一檔的可盯條件清單（stock_offered），模型只在站主自己的
# 那幾次呼叫裡用編號挑（market_news.stock_picks）；挑到的經 creation_sql 寫成 subject＝代號、visibility='owner' 的盯項，
# 和大盤的合計同一個上限；隔天起由 stock_check_sql 判。這些數字與盯項網站只回給站主（cloud/lib/impact-watch.js）。

def _price(value: float) -> str:
    """股價：千分位，最多兩位小數，不留尾巴的 0。"""
    text = f"{float(value):,.2f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _lots(value: float) -> str:
    return f"{abs(value):,.0f} 張"


def _flow(rows: Sequence[Mapping[str, Any]], key: str, dates: Sequence[str]) -> dict[str, Any] | None:
    """某一種法人最近一天的買賣超（張）與連續天數。dates：日 K 的日期（舊到新）；中間缺一天或那天是 0 就算斷了。"""
    by_day = {row["date"]: float(row[key]) / LOT for row in rows if row.get("date") and row.get(key) is not None}
    known = [day for day in dates if day in by_day]
    if not known:
        return None
    latest = known[-1]
    net = by_day[latest]
    side = "buy" if net > 0 else "sell" if net < 0 else "flat"
    days = 0
    for day in reversed(dates[:dates.index(latest) + 1]):
        value = by_day.get(day)
        if value is not None and ((side == "buy" and value > 0) or (side == "sell" and value < 0)):
            days += 1
        else:
            break
    return {"date": latest, "net": round(net, 1), "side": side, "days": days}


def stock_facts(code: str, name: str, analysis: Mapping[str, Any]) -> dict[str, Any] | None:
    """一檔股票當天的數字，取自個股頁同一份分析結果（analyze_v2）：收盤、月線季線、近 20 日高低、最近的壓力與支撐、
    外資與投信的買賣超（張）與連續天數。分析不成立（查不到、日 K 不足）或日 K 不到 LEVEL_DAYS 根就回 None。"""
    bars = [bar for bar in analysis.get("bars") or [] if bar.get("date") and bar.get("close")]
    if (analysis.get("qa") or {}).get("blocking") or len(bars) < LEVEL_DAYS:
        return None
    close = float(bars[-1]["close"])
    recent, dates = bars[-LEVEL_DAYS:], [bar["date"] for bar in bars]
    indicators, levels = analysis.get("indicators") or {}, analysis.get("levels") or {}

    def nearest(side: str, above: bool) -> float | None:
        ticks = [float(row["tick"]) for row in levels.get(side) or [] if isinstance(row, Mapping) and row.get("tick")]
        ticks = [tick for tick in ticks if (tick > close if above else tick < close)]
        return (min(ticks) if above else max(ticks)) if ticks else None

    facts: dict[str, Any] = {
        "code": code, "name": name or str(analysis.get("name") or code), "date": bars[-1]["date"], "close": round(close, 2),
        "ma": {str(days): round(float(indicators[f"ma{days}"]), 2) for days in STOCK_MA_NAMES if indicators.get(f"ma{days}")},
        "high": round(max(float(bar.get("high") or bar["close"]) for bar in recent), 2),
        "low": round(min(float(bar.get("low") or bar["close"]) for bar in recent), 2),
        "resistance": nearest("resistance", True), "support": nearest("support", False),
    }
    flows = {who: flow for who in FLOW_WHO if (flow := _flow(analysis.get("institutional") or [], who, dates))}
    if flows:
        facts["flows"] = flows
    return facts


def stock_candidates(facts: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """這一檔可以盯的條件：[{id, name, now, params}]。名稱不含股票名稱（畫面與推播另外帶對象）；
    params 記股票名稱（網站顯示用）與依據的那一天。只列現在還沒發生的。"""
    if not facts:
        return []
    close, as_of, name = float(facts["close"]), facts["date"], str(facts.get("name") or facts.get("code") or "")
    base = {"name": name, "as_of": as_of}
    out: list[dict[str, Any]] = []
    for days, label in STOCK_MA_NAMES.items():
        line = (facts.get("ma") or {}).get(str(days))
        if line is None:
            continue
        below = close < float(line)
        out.append({"id": f"{'above' if below else 'below'}_ma{days}", "name": f"收盤{'站回' if below else '跌破'}{label}",
                    "now": f"收盤 {_price(close)}，在{label} {_price(float(line))} 之{'下' if below else '上'}",
                    "params": {**base, "ma": days}})
    high, low = float(facts["high"]), float(facts["low"])
    out.append({"id": "above_high", "name": f"收盤突破近 {LEVEL_DAYS} 日高點（{_price(high)}）", "now": f"收盤 {_price(close)}",
                "params": {**base, "level": high}})
    out.append({"id": "below_low", "name": f"收盤跌破近 {LEVEL_DAYS} 日低點（{_price(low)}）", "now": f"收盤 {_price(close)}",
                "params": {**base, "level": low}})
    # 最近的壓力與支撐（個股頁價位表的第一階）；和近 20 日高低點同一個價位時不重複列
    resistance, support = facts.get("resistance"), facts.get("support")
    if resistance and float(resistance) != high:
        out.append({"id": "above_resist", "name": f"收盤站上壓力（{_price(resistance)}）", "now": f"收盤 {_price(close)}",
                    "params": {**base, "level": float(resistance)}})
    if support and float(support) != low:
        out.append({"id": "below_support", "name": f"收盤跌破支撐（{_price(support)}）", "now": f"收盤 {_price(close)}",
                    "params": {**base, "level": float(support)}})
    for who, title in FLOW_WHO.items():
        flow = (facts.get("flows") or {}).get(who)
        if not flow or flow.get("side") not in _SIDE_TEXT:
            continue
        side, other = flow["side"], "sell" if flow["side"] == "buy" else "buy"
        target = max(STREAK_TARGET, int(flow["days"]) + STREAK_AHEAD)
        now = f"{title}已連續{_SIDE_TEXT[side]} {flow['days']} 天，最近一天{_SIDE_TEXT[side]} {_lots(flow['net'])}"
        out.append({"id": f"{who}_{side}_streak", "name": f"{title}連續{_SIDE_TEXT[side]}達 {target} 天", "now": now,
                    "params": {"name": name, "days": target, "flow_as_of": flow["date"]}})
        out.append({"id": f"{who}_turn_{other}", "name": f"{title}由{_SIDE_TEXT[side]}轉為{_SIDE_TEXT[other]}", "now": now,
                    "params": {"name": name, "flow_as_of": flow["date"]}})
    return out


def stock_now(facts: Mapping[str, Any]) -> str:
    """給模型看的這一檔現況，一行。"""
    parts = [f"收盤 {_price(facts['close'])}"]
    parts += [f"{label} {_price(line)}" for days, label in STOCK_MA_NAMES.items()
              if (line := (facts.get("ma") or {}).get(str(days))) is not None]
    for who, title in FLOW_WHO.items():
        flow = (facts.get("flows") or {}).get(who)
        if flow and flow.get("side") in _SIDE_TEXT:
            parts.append(f"{title}連續{_SIDE_TEXT[flow['side']]} {flow['days']} 天")
    return "；".join(parts)


def stock_offered(query: Callable[[str], list[dict]], trade_date: str, names: Mapping[str, str],
                  holidays: set[str] | None = None) -> dict[str, dict[str, Any]]:
    """消息排程這一班、站主每一檔可以交給模型挑的清單：{代號: {name, now, conditions}}。
    只讀站主現在自選＋庫存裡的代號；沒有每日檢查存下的數字、數字太舊、表還沒建都回空的（那一班照樣有消息）。"""
    codes = [code for code in names if _CODE.fullmatch(str(code))]
    if not codes:
        return {}
    out: dict[str, dict[str, Any]] = {}
    try:
        rows = query(f"SELECT subject, payload FROM impact_watch_facts WHERE subject IN ({', '.join(_q(code) for code in codes)})")
        holidays = td.known_holidays(trade_date) if holidays is None else holidays
    except Exception:  # noqa: BLE001 — 和 offered 一樣：讀不到就不出清單
        return {}
    for row in rows:
        try:
            name = names.get(row["subject"]) or row["subject"]
            facts = {**json.loads(row["payload"]), "name": name}
            if td.trading_days_between(facts["date"], trade_date, holidays) > FACTS_FRESH_TRADING_DAYS:
                continue
            conditions = stock_candidates(facts)
            if conditions:
                out[row["subject"]] = {"name": name, "now": stock_now(facts), "conditions": conditions}
        except Exception:  # noqa: BLE001 — 一檔的數字壞了只少那一檔
            continue
    return out


_LEVEL_WORDS = {"above_high": f"突破近 {LEVEL_DAYS} 日高點", "below_low": f"跌破近 {LEVEL_DAYS} 日低點",
                "above_resist": "站上壓力", "below_support": "跌破支撐"}


def evaluate_stock(item: Mapping[str, Any], facts: Mapping[str, Any]) -> tuple[str, str | None]:
    """個股的一筆數字型盯項今天的狀態，規則和大盤的 evaluate 一樣：只看建立之後才出現的資料；均線用當天的值、
    價位用建立時定下的那一個；否定只有連續買賣超中途斷掉。說明帶實際數字（股價、張數）。"""
    key, params = str(item.get("condition_key") or ""), item.get("params") or {}
    close, day = float(facts["close"]), facts["date"]
    when = _month_day(day)
    if key.startswith(("above_", "below_")):
        if day <= str(params.get("as_of") or ""):
            return "watching", None
        above = key.startswith("above_")
        if "ma" in params:
            line, label = (facts.get("ma") or {}).get(str(params["ma"])), STOCK_MA_NAMES.get(int(params["ma"]), "均線")
            if line is None:
                return "watching", None
            if (close >= float(line)) if above else (close < float(line)):
                return "met", f"成立：{when} 收 {_price(close)}，{'站回' if above else '跌破'}{label}（{_price(float(line))}）。"
            return "watching", None
        level = float(params["level"])
        if key in _LEVEL_WORDS and ((close > level) if above else (close < level)):
            return "met", f"成立：{when} 收 {_price(close)}，{_LEVEL_WORDS[key]} {_price(level)}。"
        return "watching", None
    who = key.split("_", 1)[0]
    flow = (facts.get("flows") or {}).get(who)
    if who not in FLOW_WHO or not flow or flow["date"] <= str(params.get("flow_as_of") or ""):
        return "watching", None
    title, when, side = FLOW_WHO[who], _month_day(flow["date"]), flow.get("side")
    today = "買賣超持平" if side not in _SIDE_TEXT else f"{_SIDE_TEXT[side]} {_lots(flow['net'])}"
    for wanted in _SIDE_TEXT:
        if key == f"{who}_{wanted}_streak":
            target = int(params["days"])
            if side == wanted and int(flow["days"]) >= target:
                return "met", f"成立：{title}連續{_SIDE_TEXT[wanted]} {flow['days']} 天，{when} {today}。"
            if side != wanted:
                return "failed", f"否定：{when} {title}{today}，連續{_SIDE_TEXT[wanted]}沒有延續到 {target} 天。"
            return "watching", None
        if key == f"{who}_turn_{wanted}":
            return ("met", f"成立：{when} {title}轉為{today}。") if side == wanted else ("watching", None)
    return "watching", None


def stock_check_sql(items: Sequence[Mapping[str, Any]], facts_by_code: Mapping[str, Mapping[str, Any]], owned: Sequence[str],
                    *, day: str, now: str, holidays: set[str] | None = None) -> tuple[str, dict[str, Any]]:
    """站主個股的盯項每天判一次 → SQL 與報告。day：這次檢查的大盤資料日（到期與剩幾天照它算）。
    - 股票已經不在站主的自選與庫存：還在盯的安靜收掉（狀態 expired，不會推播、首頁不留）；
    - 其餘和大盤一樣：成立、否定記結果與首頁留到哪一天；到期日當天還沒結果就收掉；否則更新剩幾個交易日
      （那一檔今天沒有數字也照樣倒數、照樣到期）。
    順手存下每一檔當天的數字、清掉已經不在名單上的股票的數字。"""
    holidays = td.known_holidays(day) if holidays is None else holidays
    owned = [code for code in owned if _CODE.fullmatch(str(code))]
    lines: list[str] = []
    report: dict[str, Any] = {"checked": 0, "met": [], "failed": [], "expired": [], "removed": [], "watching": 0, "errors": [],
                              "facts": len(facts_by_code)}
    for item in items:
        if item.get("status") != "watching" or item.get("kind") != "numeric" or item.get("subject") == MARKET:
            continue
        report["checked"] += 1
        where = f"WHERE id = {_q(item['id'])} AND status = 'watching';"
        if item["subject"] not in owned:
            lines.append(f"UPDATE impact_watch_items SET status = 'expired', result_date = {_q(day)}, result_note = {_q(REMOVED_NOTE)}, "
                         f"days_left = NULL {where}")
            report["removed"].append(item["id"])
            continue
        status, note, facts = "watching", None, facts_by_code.get(item["subject"])
        if facts:
            try:
                parsed = {**item, "params": json.loads(item["params"]) if isinstance(item.get("params"), str) else item.get("params") or {}}
                status, note = evaluate_stock(parsed, facts)
            except Exception as error:  # noqa: BLE001 — 一筆壞了只跳過那一筆（照樣會到期）
                report["errors"].append({"id": item["id"], "error": type(error).__name__})
        if status in ("met", "failed"):
            keep = td.add_trading_days(day, KEEP_RESULT_TRADING_DAYS, holidays)
            lines.append(f"UPDATE impact_watch_items SET status = {_q(status)}, result_date = {_q(day)}, result_note = {_q(note)}, "
                         f"show_until = {_q(keep)}, days_left = NULL {where}")
            report[status].append({"id": item["id"], "note": note})
        elif day >= item["expires_on"]:
            lines.append(f"UPDATE impact_watch_items SET status = 'expired', result_date = {_q(day)}, days_left = NULL {where}")
            report["expired"].append(item["id"])
        else:
            lines.append(f"UPDATE impact_watch_items SET days_left = {td.trading_days_between(day, item['expires_on'], holidays)} {where}")
            report["watching"] += 1
    for code, facts in facts_by_code.items():
        if code in owned:   # 重跑舊的一天不把已經存下的新數字蓋回去
            lines.append("INSERT OR REPLACE INTO impact_watch_facts (subject, data_date, payload, updated_at) SELECT "
                         f"{_q(code)}, {_q(facts['date'])}, {_q(json.dumps(facts, ensure_ascii=False))}, {_q(now)} WHERE NOT EXISTS "
                         f"(SELECT 1 FROM impact_watch_facts WHERE subject = {_q(code)} AND data_date > {_q(facts['date'])});")
    still_listed = ", ".join(_q(code) for code in [MARKET, *owned])
    lines.append(f"DELETE FROM impact_watch_facts WHERE subject NOT IN ({still_listed});")
    return "\n".join(lines) + "\n", report


def owner_codes(query: Callable[[str], list[dict]], email: str) -> dict[str, str]:
    """站主現在的自選＋庫存：{代號: 名稱}。任何一邊讀不到就丟例外（整段個股檢查跳過）：少讀一邊會把只在那一邊的股票
    當成「已經移除」，盯項就被收掉了。只有庫存那張表根本不存在時才只用自選。"""
    if not _EMAIL.fullmatch(email or ""):
        raise ValueError("站主 email 格式不對")
    who = f"lower(email) = lower({_q(email)})"
    names = {row["code"]: row["name"] for row in query(f"SELECT code, max(name) AS name FROM user_watchlist WHERE {who} GROUP BY code")}
    try:
        held = query(f"SELECT code, max(name) AS name FROM user_holdings WHERE {who} GROUP BY code")
    except Exception as error:  # noqa: BLE001
        if "no such table" not in str(error).lower():
            raise
        held = []
    return {**{row["code"]: row["name"] for row in held}, **names}


def load_stock_facts(names: Mapping[str, str], as_of: str) -> dict[str, dict[str, Any]]:
    """站主每一檔當天的數字：用個股頁同一套分析（只讀本機行情庫，不連網路）。查不到、資料不足、或那一檔算壞了都只少那一檔
    （基金、槓桿與債券 ETF 沒有日 K，本來就不會有）。"""
    from twon_analysis import analyze_v2
    from twon_analysis.price_adjustment import default_capital_events
    capital = default_capital_events()
    out: dict[str, dict[str, Any]] = {}
    for code, name in names.items():
        if not _CODE.fullmatch(str(code)):
            continue
        try:
            facts = stock_facts(code, name, analyze_v2.analyze_v2(code, as_of, capital_actions=capital))
        except Exception:  # noqa: BLE001
            facts = None
        if facts:
            out[code] = facts
    return out


def check_stocks(*, day: str, now: str, owner_email: str, query: Callable[[str], list[dict]], execute: Callable[[str], None],
                 load: Callable[[Mapping[str, str], str], Mapping[str, Mapping[str, Any]]] | None = None,
                 dry_run: bool = False, holidays: set[str] | None = None) -> dict[str, Any]:
    """每日檢查裡站主個股的那一段（接在大盤之後）：讀站主現在的自選＋庫存 → 整理每一檔當天的數字 → 判每一筆個股盯項。"""
    names = owner_codes(query, owner_email)
    if not names:
        # 一檔都沒有：多半是 OWNER_EMAIL 設錯而不是站主真的清空了。不收任何盯項、不刪數字，留給到期日處理。
        return {"stocks": 0, "skipped": "站主的自選與庫存是空的"}
    facts = dict((load or load_stock_facts)(names, day))
    items = query("SELECT id, subject, kind, condition_key, params, expires_on, status FROM impact_watch_items "
                  f"WHERE status = 'watching' AND subject <> {_q(MARKET)}")
    script, report = stock_check_sql(items, facts, list(names), day=day, now=now, holidays=holidays)
    if not dry_run:
        execute(script)
    return {**report, "stocks": len(names)}


# ---- 首頁白話分析挑的條件（票 07） ----

def plain_picks(plain: Any, facts: Mapping[str, Any]) -> list[dict[str, Any]]:
    """剛發布的白話分析挑了哪些條件 → creation_sql 吃的 picks。
    只收模型寫的（source=ai）、而且和這次檢查同一個資料日的那一份；條件的名稱與參數一律用這裡依當天數字重算的清單，
    檔案裡只認編號與模型附的那一句（檔案不是這支程式寫的，不拿它的參數）。"""
    if (not isinstance(plain, Mapping) or plain.get("source") != "ai" or not isinstance(plain.get("data_dates"), Mapping)
            or plain["data_dates"].get("price_and_foreign") != facts["date"] or not isinstance(plain.get("track"), list)):
        return []
    offered = {row["id"]: row for row in candidates(facts)}
    prepare = [line for line in plain.get("prepare") or [] if isinstance(line, str)] if isinstance(plain.get("prepare"), list) else []
    picks = []
    for pick in plain["track"]:
        row = offered.get(pick.get("id")) if isinstance(pick, Mapping) and isinstance(pick.get("id"), str) else None
        if row:
            picks.append({"condition_key": row["id"], "label": row["name"], "params": row["params"],
                          "note": pick["note"] if isinstance(pick.get("note"), str) else None, "impact": PLAIN_IMPACT,
                          "prepare": prepare, "source_ref": str(plain.get("id") or "")[:80] or None})
    return picks


# ---- 每日檢查（daily-update.yml） ----

def check(analysis: Mapping[str, Any], *, now: str, query: Callable[[str], list[dict]], execute: Callable[[str], None],
          dry_run: bool = False, holidays: set[str] | None = None, plain: Any = None, owner_email: str | None = None,
          load_stocks: Callable[[Mapping[str, str], str], Mapping[str, Mapping[str, Any]]] | None = None) -> dict[str, Any]:
    """每日更新發布之後跑一次：用剛發布的大盤資料判斷每一筆盯著的盯項，寫回結果與當天的數字。
    plain：同一次發布的白話分析；它挑的條件在判完之後建成盯項（今天才建的從下一個交易日開始判）。
    owner_email：給了就接著判站主個股的盯項（票 06）；沒給就只做大盤的。"""
    facts = market_facts(analysis.get("daily") or [], analysis.get("institutional") or [])
    if not facts:
        return {"status": "skipped", "reason": "大盤日 K 不足"}
    try:
        items = query("SELECT id, subject, kind, condition_key, params, expires_on, status FROM impact_watch_items WHERE status = 'watching'")
    except Exception as error:  # noqa: BLE001 — 0034 還沒套：不擋每日更新
        return {"status": "skipped", "reason": f"讀不到盯項（0034 還沒套？）：{type(error).__name__}"}
    script, report = check_sql(items, facts, now=now, holidays=holidays)
    if not dry_run:
        execute(script)
    # 站主個股的盯項：和大盤的分開寫，個股這一段出錯（讀不到自選、行情庫壞了）不能賠掉大盤的結果
    if owner_email:
        try:
            report["stocks"] = check_stocks(day=facts["date"], now=now, owner_email=owner_email, query=query, execute=execute,
                                            load=load_stocks, dry_run=dry_run, holidays=holidays)
        except Exception as error:  # noqa: BLE001
            report["stocks"] = {"error": type(error).__name__}
    # 和每日判斷分開：白話分析的檔讀壞了、條件建不成，都不能賠掉今天的判斷結果
    try:
        picks = plain_picks(plain, facts)
        if picks:
            creation, created = creation_sql(picks, active_items(query, facts["date"]), trade_date=facts["date"], now=now,
                                             holidays=holidays, source=SOURCE_PLAIN)
            if creation and not dry_run:
                execute(creation)
            report["plain"] = created
    except Exception as error:  # noqa: BLE001
        report["plain"] = {"error": type(error).__name__}
    return {"status": "ok", **report, "dry_run": dry_run}


def read_assignment(path: "Path") -> Any:
    """讀資料包裡的一個檔（window.X = {...};）。"""
    text = path.read_text(encoding="utf-8").strip()
    return json.loads(text[text.index("=") + 1:].strip().removesuffix(";").strip())


def main(argv: Sequence[str]) -> int:
    import argparse
    import os
    from datetime import datetime, timezone

    from pathlib import Path

    import watch_news as wn
    parser = argparse.ArgumentParser(description="盯項：每日檢查")
    parser.add_argument("command", choices=["check"])
    parser.add_argument("--analysis", required=True, help="每日更新發布的大盤資料檔（analysis-data.js）")
    parser.add_argument("--plain", help=f"同一次發布的白話分析（沒給就讀 --analysis 旁邊的 {PLAIN_FILE}）")
    parser.add_argument("--owner-email", default=os.environ.get("OWNER_EMAIL") or None,
                        help="站主的 email（預設讀環境變數 OWNER_EMAIL）：給了才判站主自選股與庫存的盯項")
    parser.add_argument("--dry-run", action="store_true", help="照樣讀、照樣判斷，但不寫網站資料庫")
    args = parser.parse_args(argv)
    analysis = read_assignment(Path(args.analysis))
    try:   # 白話分析讀不到（沒有這個檔、內容壞了）：照樣做每日判斷，只是不建它的條件
        plain = read_assignment(Path(args.plain) if args.plain else Path(args.analysis).with_name(PLAIN_FILE))
    except (OSError, ValueError):
        plain = None
    report = check(analysis, now=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                   query=wn._wrangler_query, execute=wn._wrangler_execute, dry_run=args.dry_run, plain=plain,
                   owner_email=args.owner_email)
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
