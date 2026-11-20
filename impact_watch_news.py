"""消息型盯項：條件是「某件事有沒有被報導出來」（本人 2026-11-20，.scratch/impact-watch/spec.md，票 05）。

數字型（impact_watch.py）由程式比數字；這一型由模型讀之後抓到的新聞判斷，可能漏看或看錯，畫面與推播都標明。

建立（消息排程 market_news.py 每一班）：
- 模型在影響夠大的消息或總結寫 0～1 個 ``wait``：一句「在等什麼被報導」＋最多 KEYWORDS_MAX 個關鍵詞 → ``news_pick``。
- ``fresh`` 去重後和數字型一起交給 ``impact_watch.creation_sql``：同一個上限（10 件）、同一個到期日（第 20 個交易日）。
- 關鍵詞至少要 KEYWORDS_MIN 個（只有一個詞會對到太多不相干的新聞），最多 KEYWORDS_MAX 個。
- **去重規則**：同一對象，關鍵詞（去空白、不分大小寫、不管順序）完全相同、或共有至少 SAME_EVENT_SHARED 個
  → 視為同一件，不再建（已經在盯、或剛有結果首頁還留著的都算）。同一班寫了好幾個同一件的，留影響力最高的。
- 白話分析（plain_daily.py）不寫這一型：它的輸入只有籌碼數字、沒有新聞，寫「等什麼被報導」等於憑空想事件。

每日檢查（``python impact_watch_news.py check``，daily-update.yml 接在數字型的檢查後面、推播前面）：
- 對每一筆盯著的消息型，找「建立（或上次判過）之後才第一次抓到」而且對得上關鍵詞的新聞（market_news_items）：
  標題＋一句理由（站上存的只有這兩樣，沒有內文）裡至少出現 MATCH_MIN 個關鍵詞，取對到最多、最新的 NEWS_PER_ITEM 則；
  超過的那幾則留到下一個交易日（params.seen 記已經給模型看過的連結，不會重複給）。
- 全部沒有對得上的新聞：不呼叫模型。有的話**所有盯項合成一次呼叫**（Flash、思考高，沿用首頁消息的模型設定），
  模型逐件回 met（明確報導已發生）／failed（明確報導確定不會發生）／waiting（還看不出來）；met、failed 要指出依據哪一則。
- 回答不合格（漏回、依據的新聞不在給它的那幾則裡、沒寫理由、寫成買賣建議）：那一件當成「還沒」，記在報告的 invalid，
  而且不往前推「判到哪裡」，下一個交易日連同新的新聞再判一次。呼叫沒成功再試一次（CALL_ATTEMPTS）。
- 成立／否定：結果說明＝「成立：」或「否定：」＋模型那一句；依據的新聞（標題、連結、來源）存在 params.evidence，
  首頁清單附連結。到期（第 20 個交易日還沒結果）安靜收掉，和數字型一樣。
- 報告的 model_calls 是這一次實際呼叫幾次（agy 沒準備好、根本沒叫到的不算）：``python impact_watch_news.py usage`` 照這個
  數字一次呼叫寫一筆 Gemini 用量（任務 impact-watch，設定頁「盯項讀新聞」）；沒有呼叫就不寫。
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import date, timedelta
from typing import Any

import impact_text
import impact_watch
import trading_days as td

KIND = "news"
TASK = "impact-watch"           # Gemini 用量裡的任務名稱（gemini_usage.TASK_MODELS、web/settings.js）
KEYWORDS_MAX = 3
KEYWORDS_MIN = 2
KEYWORD_MIN_CHARS = 2
KEYWORD_MAX_CHARS = 12
# 提示裡要模型寫得比這裡的上限短（那一句 30 字、關鍵詞 2～6 字、理由 40 字）：稍微超過的照收，不為了幾個字丟掉
WAIT_MAX = 50                   # 「在等什麼被報導」那一句最多幾個字；超過的不收（不硬切成半句）
SAME_EVENT_SHARED = 2           # 共有幾個關鍵詞就當成同一件事
MATCH_MIN = 2                   # 一則新聞至少要出現幾個關鍵詞才算對得上
NEWS_PER_ITEM = 6               # 一筆盯項一次最多交給模型幾則新聞
REASON_MAX = 60                 # 模型說「新聞報了什麼」那一句
CALL_ATTEMPTS = 2               # 呼叫沒成功再試一次
LOOKBACK_DAYS = 5               # 一則新聞最多連續出現在幾天的輪次裡（market_news.MAX_WINDOW_HOURS 是 4 天）
NO_MODEL = "agy_unavailable"    # market_news._owner_asker：agy 沒裝好，沒有呼叫到模型（不算一次用量）
VERDICTS = ("met", "failed", "waiting")
_PREFIX = {"met": "成立", "failed": "否定"}

_q = impact_watch._q
Asker = Callable[[str, Mapping[str, Any]], tuple[str, Any, str]]
Problem = tuple[str, str, str]

WAIT_SCHEMA = {"type": "object", "properties": {"what": {"type": "string"}, "keywords": {"type": "array", "items": {"type": "string"}}},
               "required": ["what", "keywords"]}
WAIT_RULE = """- wait：這件事接下來要等「某件事被報導出來」才知道怎麼發展時（例如正式清單公布、法說會給出展望、主管機關宣布結果），
  可以寫一個：what 一句（30 字內）寫在等什麼被報導（例如「美方公布正式的出口管制清單」）；keywords 給 2～3 個報導這件事的
  新聞標題裡一定會出現的詞（至少兩個、每個 2～6 個字，用台灣媒體的寫法，例如「出口管制」「清單」），系統每天用這些詞找後續新聞、
  再判斷這件事成立了沒。能用 watchable 的條件表達的用 track，不要寫 wait；impact 是 "1" 的消息、或沒有在等的事，
  就不要寫這個欄位，不要硬寫。
"""

VERDICT_SCHEMA = {"type": "object", "properties": {"verdicts": {"type": "array", "items": {"type": "object", "properties": {
    "id": {"type": "string"}, "verdict": {"type": "string", "enum": list(VERDICTS)},
    "news": {"type": "string"}, "reason": {"type": "string"}}, "required": ["id", "verdict"]}}}, "required": ["verdicts"]}
JUDGE_RULES = """你是台股投資人的新聞助理。INPUT 的 waiting 是讀者在等的事：wait 是在等什麼被報導、since 是從哪一天開始等，
news 是之後抓到、可能有關的新聞（title 標題、reason 一句摘要、source 來源、published_at 發布時間）。
逐件判斷這件事被報導出來了沒，用繁體中文回答，只回 JSON。verdicts：waiting 的每一件都要回（id 照抄）：
- verdict：met＝有新聞明確報導這件事已經發生；failed＝有新聞明確報導這件事確定不會發生（取消、否決、官方否認）；
  waiting＝還看不出來（只是預期、傳聞、分析師看法、還在討論，或新聞講的是別件事）。拿不準就填 waiting。
- news：verdict 是 met 或 failed 時，填依據的那一則新聞的 id（只能是這一件自己的 news 裡的 id）；waiting 不用寫。
- reason：verdict 是 met 或 failed 時，一句話（40 字內）說那則新聞報了什麼；只能根據標題與摘要，不要編數字或內容，
  也不要寫買賣指令或投資建議。waiting 不用寫。
不要使用任何工具、不要上網查，只根據 INPUT 直接回答。"""


def _norm(word: str) -> str:
    return re.sub(r"\s+", "", word).lower()


# ---- 建立 ----

def keywords(value: Any) -> list[str]:
    """模型給的關鍵詞 → 合格的那幾個：字串、KEYWORD_MIN_CHARS～KEYWORD_MAX_CHARS 個字、不重複，最多 KEYWORDS_MAX 個。"""
    out: list[str] = []
    for word in value if isinstance(value, (list, tuple)) else []:
        if not isinstance(word, str):
            continue
        word = re.sub(r"\s+", " ", word).strip().strip("「」『』\"'")
        if KEYWORD_MIN_CHARS <= len(word) <= KEYWORD_MAX_CHARS and _norm(word) not in {_norm(kept) for kept in out}:
            out.append(word)
    return out[:KEYWORDS_MAX]


def condition_key(words: Sequence[str]) -> str:
    """條件編號：關鍵詞（去空白、不分大小寫、不管順序）一樣就是同一個。"""
    return "news:" + hashlib.sha1("|".join(sorted(_norm(word) for word in words)).encode("utf-8")).hexdigest()[:12]


def same_event(first: Sequence[str], second: Sequence[str]) -> bool:
    """兩組關鍵詞是不是在等同一件事（規則見檔頭）。"""
    a, b = {_norm(word) for word in first}, {_norm(word) for word in second}
    return bool(a) and (a == b or len(a & b) >= SAME_EVENT_SHARED)


def news_pick(entry: Mapping[str, Any], problems: list[Problem] | None = None,
              check: Callable[[str], str | None] | None = None) -> dict[str, Any] | None:
    """一則消息或總結裡模型寫的 wait → creation_sql 吃的 pick（還沒有 impact／prepare／source_ref，由消息排程補）。
    沒寫、寫得不成形（不是物件、沒有那一句、合格的關鍵詞不到 KEYWORDS_MIN 個、那一句太長）就沒有；那一句被文字檢查退回記進 problems。"""
    value = entry.get("wait")
    if not isinstance(value, Mapping) or not isinstance(value.get("what"), str):
        return None
    what = re.sub(r"\s+", " ", value["what"]).strip().rstrip("。")
    words = keywords(value.get("keywords"))
    if not what or len(what) > WAIT_MAX or len(words) < KEYWORDS_MIN:
        return None
    reason = (check or impact_text.order_problem)(what)
    if reason:
        if problems is not None:
            problems.append(("wait", reason, what))
        return None
    return {"kind": KIND, "condition_key": condition_key(words), "label": what, "params": {"keywords": words}, "note": None}


def _params(row: Mapping[str, Any]) -> dict[str, Any]:
    value = row.get("params")
    try:
        value = json.loads(value) if isinstance(value, str) else value
    except json.JSONDecodeError:
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


def existing(query: Callable[[str], list[dict]], trade_date: str) -> list[dict[str, Any]]:
    """還算數的消息型盯項（盯著的，和有結果、首頁還留著的）：去重用。"""
    return query("SELECT id, subject, params FROM impact_watch_items WHERE kind = 'news' AND (status = 'watching' "
                 f"OR (status IN ('met', 'failed') AND show_until >= {_q(trade_date)}))")


def fresh(picks: Sequence[Mapping[str, Any]], active: Sequence[Mapping[str, Any]], *, now: str,
          subject: str = impact_watch.MARKET) -> tuple[list[dict[str, Any]], int]:
    """這一班寫的消息型條件 → (要建的, 因為是同一件事而不建的有幾個)。影響力高的先留；要建的記下從什麼時候開始等（since）。"""
    known = [_params(row).get("keywords") or [] for row in active if row.get("subject", subject) == subject]
    kept: list[dict[str, Any]] = []
    for pick in sorted(picks, key=lambda row: -int(row["impact"])):
        words = pick["params"]["keywords"]
        if any(same_event(words, other) for other in known):
            continue
        known.append(words)
        kept.append({**pick, "params": {**pick["params"], "since": now}})
    return kept, len(picks) - len(kept)


# ---- 每日檢查 ----

def recent_news(query: Callable[[str], list[dict]], since: str) -> list[dict[str, Any]]:
    """since 前後抓到的市場新聞，一個連結一列：內容用最新一輪的（外文標題後來才翻成中文的用中文那一份），
    first_seen 是它第一次出現的那一輪（同一則會連續出現在好幾輪裡）。"""
    cutoff = (date.fromisoformat(since[:10]) - timedelta(days=LOOKBACK_DAYS)).isoformat()
    rows = query("SELECT i.link AS link, i.title AS title, i.reason AS reason, i.source AS source, i.published_at AS published_at, "
                 "r.created_at AS fetched_at FROM market_news_items i JOIN market_news_runs r ON r.run_id = i.run_id "
                 f"WHERE r.trade_date >= {_q(cutoff)} ORDER BY r.created_at")
    by_link: dict[str, dict[str, Any]] = {}
    for row in rows:
        seen = by_link.get(row["link"])
        by_link[row["link"]] = {**row, "first_seen": seen["first_seen"] if seen else row["fetched_at"]}
    return list(by_link.values())


def matching(params: Mapping[str, Any], news: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    """這一筆盯項還沒判過、對得上關鍵詞的新聞 → (這一次給模型的, 是不是還有放不下的)。
    對到最多個詞的先、一樣多時新的先，最多 NEWS_PER_ITEM 則；已經給模型看過的連結（params.seen）不再給。"""
    words = [_norm(word) for word in params.get("keywords") or [] if isinstance(word, str)]
    after = str(params.get("checked_through") or params.get("since") or "")
    seen = set(params.get("seen") or [])
    if not words:
        return [], False
    hits = []
    for row in news:
        link = str(row.get("link") or "")
        if not link.startswith(("https://", "http://")) or str(row["first_seen"]) <= after or link in seen:
            continue
        text = _norm(f"{row.get('title') or ''} {row.get('reason') or ''}")
        count = sum(1 for word in words if word in text)
        if count >= MATCH_MIN:
            hits.append((count, dict(row)))
    newest = sorted(hits, key=lambda hit: str(hit[1].get("published_at") or ""), reverse=True)
    return [row for _, row in sorted(newest, key=lambda hit: -hit[0])[:NEWS_PER_ITEM]], len(hits) > NEWS_PER_ITEM


def judge_prompt(day: str, asked: Sequence[Mapping[str, Any]]) -> str:
    """asked：[{id, item, news:[{id, …}]}]。"""
    payload = {"today": day, "waiting": [
        {"id": entry["id"], "wait": entry["item"]["label"], "since": entry["item"]["created_date"],
         "news": [{"id": row["id"], **{key: row[key] for key in ("title", "reason", "source", "published_at") if row.get(key)}}
                  for row in entry["news"]]} for entry in asked]}
    return JUDGE_RULES + "\nINPUT:" + json.dumps(payload, ensure_ascii=False)


def verdicts(output: Any, asked: Sequence[Mapping[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
    """模型的回答 → ({盯項 id: {status, note, evidence}}, 不合格的清單)。回答裡沒有、或不合格的那幾件不在第一個裡
    （當成「還沒」而且下次再判）；判 waiting 的在裡面、status 是 watching。"""
    if isinstance(output, str):
        try:
            output = json.loads(output)
        except json.JSONDecodeError:
            output = None
    listed = output.get("verdicts") if isinstance(output, Mapping) else None
    answers = {entry["id"]: entry for entry in (listed if isinstance(listed, (list, tuple)) else [])
               if isinstance(entry, Mapping) and isinstance(entry.get("id"), str)}
    judged: dict[str, dict[str, Any]] = {}
    invalid: list[dict[str, str]] = []
    for entry in asked:
        item_id, answer = entry["item"]["id"], answers.get(entry["id"])
        problem = None
        if answer is None:
            problem = "missing"
        elif not isinstance(answer.get("verdict"), str) or answer["verdict"] not in VERDICTS:
            problem = "bad_verdict"
        elif answer["verdict"] == "waiting":
            judged[item_id] = {"status": "watching"}
        else:
            source = next((row for row in entry["news"] if row["id"] == answer.get("news")), None)
            reason = re.sub(r"\s+", " ", answer["reason"]).strip() if isinstance(answer.get("reason"), str) else ""
            if not source:
                problem = "unknown_news"
            elif not reason or len(reason) > REASON_MAX:
                problem = "no_reason" if not reason else "reason_too_long"
            elif impact_text.order_problem(reason):
                problem = "trade_advice"
            else:
                judged[item_id] = {"status": answer["verdict"],
                                   "note": f"{_PREFIX[answer['verdict']]}：{reason.rstrip('。')}。",
                                   "evidence": {key: source[key] for key in ("title", "link", "source") if source.get(key)}}
        if problem:
            invalid.append({"id": item_id, "problem": problem})
    return judged, invalid


def check(day: str, *, now: str, query: Callable[[str], list[dict]], execute: Callable[[str], None], ask: Asker,
          holidays: set[str] | None = None, dry_run: bool = False) -> dict[str, Any]:
    """每個交易日盤後跑一次（day：這次發布的資料日）。見檔頭。"""
    try:
        items = query("SELECT id, label, params, created_date, expires_on FROM impact_watch_items "
                      "WHERE status = 'watching' AND kind = 'news' ORDER BY id")
    except Exception as error:  # noqa: BLE001 — 0034 還沒套：不擋每日更新
        return {"status": "skipped", "reason": f"讀不到盯項（0034 還沒套？）：{type(error).__name__}"}
    report: dict[str, Any] = {"status": "ok", "data_date": day, "checked": len(items), "with_news": 0, "model_calls": 0, "calls": [],
                              "met": [], "failed": [], "expired": [], "watching": 0, "invalid": [], "dry_run": dry_run}
    if not items:
        return report
    holidays = td.known_holidays(day) if holidays is None else holidays
    parsed = [{**item, "params": _params(item)} for item in items]
    judged: dict[str, dict[str, Any]] = {}
    try:
        starts = [str(item["params"].get("since") or "") for item in parsed if item["params"].get("since")]
        news = recent_news(query, min(starts)) if starts else []
    except Exception as error:  # noqa: BLE001 — 讀不到新聞：今天都當成還沒，到期照算
        news, report["news_error"] = [], type(error).__name__
    asked = []
    for item in parsed:
        rows, more = matching(item["params"], news)
        if rows:
            name = f"w{len(asked) + 1}"
            asked.append({"id": name, "item": item, "more": more,
                          "news": [{**row, "id": f"{name}n{index + 1}"} for index, row in enumerate(rows)]})
    report["with_news"] = len(asked)
    if asked:   # 沒有對得上的新聞就不呼叫模型
        prompt, output = judge_prompt(day, asked), None
        for _ in range(CALL_ATTEMPTS):
            try:
                status, output, note = ask(prompt, VERDICT_SCHEMA)
            except Exception as error:  # noqa: BLE001
                status, output, note = type(error).__name__, None, str(error)[:200]
            report["calls"].append({"status": status, **({"note": str(note)[:300]} if note and status != "ok" else {})})
            if status in ("ok", NO_MODEL):   # 模型根本叫不到（agy 沒準備好）：再試也一樣
                break
        output = output if report["calls"][-1]["status"] == "ok" else None
        report["model_calls"] = sum(1 for call in report["calls"] if call["status"] != NO_MODEL)
        if output is not None:
            try:
                judged, report["invalid"] = verdicts(output, asked)
            except Exception:  # noqa: BLE001 — 回答的形狀怪到讀不了：全部當成還沒，到期與剩幾天照樣要寫
                judged, report["invalid"] = {}, [{"id": entry["item"]["id"], "problem": "bad_answer"} for entry in asked]
        else:
            report["invalid"] = [{"id": entry["item"]["id"], "problem": "no_answer"} for entry in asked]
    lines: list[str] = []
    for item in parsed:
        result = judged.get(item["id"])
        where = f"WHERE id = {_q(item['id'])} AND status = 'watching';"
        if result and result["status"] in _PREFIX:
            keep = td.add_trading_days(day, impact_watch.KEEP_RESULT_TRADING_DAYS, holidays)
            evidence = json.dumps(result["evidence"], ensure_ascii=False)
            lines.append(f"UPDATE impact_watch_items SET status = {_q(result['status'])}, result_date = {_q(day)}, "
                         f"result_note = {_q(result['note'])}, show_until = {_q(keep)}, days_left = NULL, "
                         f"params = json_set(params, '$.evidence', json({_q(evidence)})) {where}")
            report[result["status"]].append({"id": item["id"], "note": result["note"], "evidence": result["evidence"]})
            continue
        if day >= item["expires_on"]:
            lines.append(f"UPDATE impact_watch_items SET status = 'expired', result_date = {_q(day)}, days_left = NULL {where}")
            report["expired"].append(item["id"])
            continue
        left = td.trading_days_between(day, item["expires_on"], holidays)
        # 模型看過、判成還沒的：記下判到哪裡，同一批新聞不再問第二次。回答不合格的不記，下次連同新的再判。
        # 這一次放不下、還有沒給的：不往前推，只記已經給過的連結，剩下的下一個交易日再給
        seen = ""
        if result:
            entry = next(entry for entry in asked if entry["item"]["id"] == item["id"])
            if entry["more"]:
                links = [*(item["params"].get("seen") or []), *(row["link"] for row in entry["news"])]
                seen = f", params = json_set(params, '$.seen', json({_q(json.dumps(links, ensure_ascii=False))}))"
            else:
                seen = f", params = json_remove(json_set(params, '$.checked_through', {_q(now)}), '$.seen')"
        lines.append(f"UPDATE impact_watch_items SET days_left = {left}{seen} {where}")
        report["watching"] += 1
    if lines and not dry_run:
        execute("\n".join(lines) + "\n")
    return report


def usage_sql(report: Any, buckets: list[dict[str, Any]] | None, *, run_id: str, taken_at: str) -> str:
    """這一次每日檢查的 Gemini 用量：實際呼叫幾次就幾筆（設定頁一筆算一次）；沒有呼叫、乾跑、報告讀不懂都是空的。"""
    import gemini_usage
    if not isinstance(report, Mapping) or report.get("dry_run") or not isinstance(report.get("model_calls"), int):
        return ""
    return "".join(gemini_usage.snapshot_sql(buckets, task=TASK, run_id=f"{run_id}-{index + 1}", taken_at=taken_at,
                                             model=report.get("model") or gemini_usage.model_for_task(TASK))
                   for index in range(report["model_calls"]))


def _usage(args: Any) -> int:
    """排程的下一步：讀每日檢查印出的報告，寫出要記進 gemini_usage_snapshots 的 SQL。一律以 0 結束（用量只是附帶資訊）。"""
    from datetime import datetime, timezone
    from pathlib import Path

    import gemini_usage
    try:
        # 檔案是每日檢查那一步的輸出：報告是最後一行（前面可能有別的訊息）
        lines = [line for line in Path(args.report).read_text(encoding="utf-8").splitlines() if line.strip().startswith("{")]
        report = json.loads(lines[-1])
    except (OSError, ValueError, IndexError):
        report = None
    buckets = None
    if isinstance(report, Mapping) and report.get("model_calls"):
        try:
            buckets = gemini_usage.read_usage(args.agy_bin) if args.agy_bin else None
        except gemini_usage.UsageUnavailable as error:
            print(f"::warning::讀不到 Gemini 用量（{error}）；只記下這次任務")
    script = usage_sql(report, buckets, run_id=args.run_id, taken_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    Path(args.sql_output).write_text(script, encoding="utf-8")
    print(f"消息型盯項這一次記 {script.count('INSERT INTO')} 筆用量")
    return 0


def main(argv: Sequence[str]) -> int:
    import argparse
    import os
    from datetime import datetime, timezone
    from pathlib import Path

    import gemini_models
    import market_news
    import watch_news as wn
    parser = argparse.ArgumentParser(description="消息型盯項：每日檢查")
    parser.add_argument("command", choices=["check", "usage"])
    parser.add_argument("--analysis", help="check：每日更新發布的大盤資料檔（analysis-data.js），只用來知道資料日")
    parser.add_argument("--agy-bin")
    parser.add_argument("--agy-home")
    parser.add_argument("--report", help="check：把報告另外寫成一個檔（執行紀錄用）；usage：要讀的那份報告")
    parser.add_argument("--dry-run", action="store_true", help="照樣讀、照樣叫模型判斷，但不寫網站資料庫")
    parser.add_argument("--run-id", help="usage：這一次排程的編號")
    parser.add_argument("--sql-output", help="usage：寫用量 SQL 的檔")
    args = parser.parse_args(argv)
    if args.command == "usage":
        if not (args.report and args.run_id and args.sql_output):
            parser.error("usage 要 --report、--run-id、--sql-output")
        return _usage(args)
    if not args.analysis:
        parser.error("check 要 --analysis")
    analysis = impact_watch.read_assignment(Path(args.analysis))
    facts = impact_watch.market_facts(analysis.get("daily") or [], analysis.get("institutional") or [])
    model = os.environ.get("AI_NEWS_MODEL") or gemini_models.NEWS
    effort = os.environ.get("AI_NEWS_EFFORT") or gemini_models.NEWS_EFFORT
    if not facts:
        report: dict[str, Any] = {"status": "skipped", "reason": "大盤日 K 不足"}
    else:
        report = check(facts["date"], now=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), query=wn._wrangler_query,
                       execute=wn._wrangler_execute, ask=market_news._owner_asker(args.agy_bin, args.agy_home, model, effort),
                       dry_run=args.dry_run)
    report = {**report, "model": model, "effort": effort}
    text = json.dumps(report, ensure_ascii=False)
    if args.report:
        try:
            Path(args.report).parent.mkdir(parents=True, exist_ok=True)
            Path(args.report).write_text(text + "\n", encoding="utf-8")
        except OSError:
            pass
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
