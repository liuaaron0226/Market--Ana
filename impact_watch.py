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
    picks：[{condition_key, label, params, note, impact, prepare, source_ref}]（對象都是大盤）；kind 沒給是數字型，
    消息型（impact_watch_news.py，票 05）帶 kind="news"，和數字型算同一個上限。
    - 同一個條件這一班被挑好幾次：留影響力最高的那一次；
    - 已經在盯、或剛有結果首頁還留著的條件：不再建；今天建過又收掉的（讓位、當天就有結果）今天也不再建；
    - 盯著的合計超過 MAX_WATCHING：依影響力高到低、一樣高時新的先，排在後面的收掉（舊的）或不建（新的）。"""
    holidays = td.known_holidays(trade_date) if holidays is None else holidays
    best: dict[str, Mapping[str, Any]] = {}
    for pick in picks:
        if pick["condition_key"] not in best or int(pick["impact"]) > int(best[pick["condition_key"]]["impact"]):
            best[pick["condition_key"]] = pick
    taken = {(row["subject"], row["condition_key"]) for row in active if row["status"] in ("watching", "met", "failed")}
    made_today = {row["id"] for row in active}
    fresh = [pick for key, pick in best.items() if (MARKET, key) not in taken and f"{MARKET}:{key}:{trade_date}" not in made_today]
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
        item_id = f"{MARKET}:{pick['condition_key']}:{trade_date}"
        created.append(item_id)
        values = [item_id, source, trade_date, pick.get("source_ref"), MARKET, pick.get("kind") or "numeric", pick["condition_key"],
                  json.dumps(pick.get("params") or {}, ensure_ascii=False), pick["label"], pick.get("note"), int(pick["impact"]),
                  json.dumps(list(pick["prepare"]), ensure_ascii=False) if pick.get("prepare") else None,
                  trade_date, expires, EXPIRE_TRADING_DAYS, "watching", "all", now]
        # 同一班重跑、或另一支排程剛建了同一個條件：不重複建（條件編號同時只有一筆盯著的）
        lines.append("INSERT OR IGNORE INTO impact_watch_items (id, source_block, source_date, source_ref, subject, kind, condition_key, params, "
                     "label, note, impact, prepare, created_date, expires_on, days_left, status, visibility, created_at) SELECT "
                     + ", ".join(str(value) if isinstance(value, int) else _q(value) for value in values)
                     + f" WHERE NOT EXISTS (SELECT 1 FROM impact_watch_items WHERE subject = {_q(MARKET)} AND condition_key = "
                     f"{_q(pick['condition_key'])} AND (status = 'watching' OR (status IN ('met', 'failed') AND show_until >= {_q(trade_date)})));")
    report = {"picked": len(best), "created": created, "already_watching": len(best) - len(fresh),
              "over_limit": len([entry for entry in over if "new" in entry]),
              "closed_for_room": [entry["old"]["id"] for entry in over if "old" in entry]}
    return ("\n".join(lines) + "\n" if lines else ""), report


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
          dry_run: bool = False, holidays: set[str] | None = None, plain: Any = None) -> dict[str, Any]:
    """每日更新發布之後跑一次：用剛發布的大盤資料判斷每一筆盯著的盯項，寫回結果與當天的數字。
    plain：同一次發布的白話分析；它挑的條件在判完之後建成盯項（今天才建的從下一個交易日開始判）。"""
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
    from datetime import datetime, timezone

    from pathlib import Path

    import watch_news as wn
    parser = argparse.ArgumentParser(description="盯項：每日檢查")
    parser.add_argument("command", choices=["check"])
    parser.add_argument("--analysis", required=True, help="每日更新發布的大盤資料檔（analysis-data.js）")
    parser.add_argument("--plain", help=f"同一次發布的白話分析（沒給就讀 --analysis 旁邊的 {PLAIN_FILE}）")
    parser.add_argument("--dry-run", action="store_true", help="照樣讀、照樣判斷，但不寫網站資料庫")
    args = parser.parse_args(argv)
    analysis = read_assignment(Path(args.analysis))
    try:   # 白話分析讀不到（沒有這個檔、內容壞了）：照樣做每日判斷，只是不建它的條件
        plain = read_assignment(Path(args.plain) if args.plain else Path(args.analysis).with_name(PLAIN_FILE))
    except (OSError, ValueError):
        plain = None
    report = check(analysis, now=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                   query=wn._wrangler_query, execute=wn._wrangler_execute, dry_run=args.dry_run, plain=plain)
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
