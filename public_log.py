"""公開的執行紀錄只留數量與狀態（倉庫公開之後，GitHub Actions 的輸出、步驟摘要與 artifact 誰都看得到）。

排程裡的程式照舊組出完整的報告，完整的那一份只寫進不公開的地方（跟著工作集或帳本存到 R2 的資料夾）；
印到標準輸出的是這裡遮過的那一份：
- 朋友的 email → 依 email 排序的代稱 friend-01、friend-02（和 friend_ai_digest.try_lines 同一個寫法）；
- 站主的自選股與庫存：代號、帶代號的盯項編號（「代號:條件:日期」）、逐檔的判斷 → 只留幾筆、各是什麼狀態；
- 可能帶著模型回答的文字（note 之類）→ 只留字數。
大盤的東西（對象是 market 的盯項、指數、新聞標題、數量、狀態、模型名稱）照留。

這裡都是不碰檔案、不碰網路的純函式（write_private 除外），測試在 test_public_log.py。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

MARKET = "market"   # impact_watch.MARKET：盯項的對象是大盤


def friend_labels(emails: Iterable[Any]) -> dict[str, str]:
    """email → friend-01、friend-02…（依 email 排序，同一批人每次都是同一個代稱）。"""
    return {email: f"friend-{index:02d}" for index, email in enumerate(sorted({str(email) for email in emails}), start=1)}


def is_market_item(entry: Any) -> bool:
    """盯項（編號字串，或帶 id 的那一筆）是不是大盤的：編號是「market:條件:日期」。其餘一律當成站主個股的。"""
    item_id = entry.get("id") if isinstance(entry, Mapping) else entry
    return isinstance(item_id, str) and item_id.startswith(MARKET + ":")


def text_to_length(entry: Mapping[str, Any], keys: Iterable[str] = ("note",)) -> dict[str, Any]:
    """把可能帶著模型回答的欄位換成字數（note → note_chars）；空的直接拿掉。"""
    keys = set(keys)
    out: dict[str, Any] = {}
    for key, value in entry.items():
        if key not in keys:
            out[key] = value
        elif value:
            out[f"{key}_chars"] = len(str(value))
    return out


def split_items(entries: Any, text_keys: Iterable[str] = ()) -> dict[str, Any]:
    """一串盯項 → {"market": [大盤的照列], "stocks": 站主個股的有幾筆}。text_keys：大盤那幾筆裡要換成字數的欄位。"""
    listed = list(entries) if isinstance(entries, (list, tuple)) else []
    text_keys = tuple(text_keys)
    market = [text_to_length(entry, text_keys) if isinstance(entry, Mapping) and text_keys else entry
              for entry in listed if is_market_item(entry)]
    return {"market": market, "stocks": len(listed) - len(market)}


def count_by(rows: Any, key: str) -> dict[str, int]:
    """一串逐檔的結果 → 每一種 key 的值各幾筆（沒有值的算在 "none"）。"""
    counts: dict[str, int] = {}
    for row in rows if isinstance(rows, (list, tuple)) else []:
        value = row.get(key) if isinstance(row, Mapping) else None
        label = "none" if value is None else str(value)
        counts[label] = counts.get(label, 0) + 1
    return counts


def _size(value: Any) -> int:
    return len(value) if isinstance(value, (list, tuple, dict, str)) else int(bool(value))


def _creation(report: Mapping[str, Any]) -> dict[str, Any]:
    """impact_watch.creation_sql 的報告（market_news 的 diagnosis.watch、每日檢查的 plain）：
    新建與讓位收掉的編號分成大盤與個股；模型挑了清單外的編號（unknown_ids，內容是模型寫的）只留幾個。"""
    out = dict(report)
    for key in ("created", "closed_for_room"):
        if key in out:
            out[key] = split_items(out[key])
    if "unknown_ids" in out:
        out["unknown_ids"] = _size(out["unknown_ids"])
    return out


def _call(record: Any) -> Any:
    """一次模型呼叫的紀錄：沒成功時附的 note 可能是模型回的字，只留字數；text_retry 只留是哪一個欄位被退回。"""
    if not isinstance(record, Mapping):
        return record
    out = text_to_length(record, ("note",))
    if isinstance(out.get("text_retry"), str):
        out["text_retry"] = out["text_retry"].split("：", 1)[0]
    return out


def _friend_status(status: Any) -> str:
    """朋友那一段的結果：只留狀態（ok、nothing_new，或「成功幾批/共幾批 失敗原因的第一個詞」），後面附的說明不留。"""
    words = str(status).split()
    if not words:
        return ""
    return words[0] if words[0] in ("ok", "nothing_new") else " ".join(words[:2])


def market_news(report: Mapping[str, Any]) -> dict[str, Any]:
    """market_news.run 的報告 → 可以公開印出來的那一份。"""
    out = text_to_length(report, ("model_note",))
    friends = report.get("friends")
    if isinstance(friends, Mapping):
        labels = friend_labels(friends)
        out["friends"] = {labels[str(email)]: _friend_status(status) for email, status in friends.items()}
    diagnosis = report.get("diagnosis")
    if isinstance(diagnosis, Mapping):
        shown = dict(diagnosis)
        if isinstance(shown.get("calls"), Mapping):
            shown["calls"] = {name: _call(record) for name, record in shown["calls"].items()}
        if isinstance(shown.get("watch"), Mapping):
            shown["watch"] = _creation(shown["watch"])
        out["diagnosis"] = shown
    return out


def impact_watch(report: Mapping[str, Any]) -> dict[str, Any]:
    """impact_watch.check 的報告 → 大盤的盯項照列（結果說明是程式依數字寫的）；站主個股那一段只留數量。"""
    out = dict(report)
    for key in ("met", "failed", "expired", "errors"):
        if key in out:
            out[key] = split_items(out[key])
    stocks = report.get("stocks")
    if isinstance(stocks, Mapping):
        out["stocks"] = {key: (_size(value) if isinstance(value, (list, tuple, dict)) else value) for key, value in stocks.items()}
    if isinstance(report.get("plain"), Mapping):
        out["plain"] = _creation(report["plain"])
    return out


def impact_watch_news(report: Mapping[str, Any]) -> dict[str, Any]:
    """impact_watch_news.check 的報告 → 模型寫的理由只留字數；對象不是大盤的只留數量。
    model_calls、dry_run、model 照留（下一步記用量要讀）。"""
    out = dict(report)
    if isinstance(out.get("calls"), (list, tuple)):
        out["calls"] = [_call(record) for record in out["calls"]]
    for key in ("met", "failed"):
        if key in out:
            out[key] = split_items(out[key], ("note",))
    for key in ("expired", "invalid"):
        if key in out:
            out[key] = split_items(out[key])
    return out


def watch_judge(summary: Mapping[str, Any]) -> dict[str, Any]:
    """watch_run judge 印的摘要 → 逐檔的那一串換成幾檔、每一種結果與狀態各幾檔；補記的漏記只留幾筆。"""
    out = dict(summary)
    stocks = summary.get("stocks")
    rows = list(stocks) if isinstance(stocks, (list, tuple)) else []
    out["stocks"] = {"count": len(rows), "outcomes": count_by(rows, "outcome"), "kinds": count_by(rows, "kind"),
                     "statuses": count_by(rows, "status"),
                     "missed": sum(1 for row in rows if isinstance(row, Mapping) and row.get("miss_reason"))}
    if isinstance(out.get("backfilled_misses"), (list, tuple)):
        out["backfilled_misses"] = len(out["backfilled_misses"])
    return out


def watch_nominate(candidates: Any, stocks: Any) -> dict[str, Any]:
    """watch_run nominate 印的摘要 → 幾檔候選、每一種結果各幾檔。"""
    return {"candidates": _size(candidates), "outcomes": count_by(stocks, "outcome")}


def watch_news_daily(report: Mapping[str, Any]) -> dict[str, Any]:
    """watch_news.daily 的報告 → 來源原本一檔一格（gnews:代號），合成每個來源成功、失敗各幾檔。"""
    out = dict(report)
    sources = report.get("sources")
    if isinstance(sources, Mapping):
        merged: dict[str, dict[str, int]] = {}
        for name, result in sources.items():
            status = result.get("status") if isinstance(result, Mapping) else None
            bucket = merged.setdefault(str(name).split(":", 1)[0], {"ok": 0, "error": 0})
            bucket["ok" if status == "ok" else "error"] += 1
        out["sources"] = merged
    return out


def write_private(path: Any, report: Any) -> bool:
    """完整的報告寫進不公開的地方（跟著工作集或帳本存到 R2 的資料夾）。寫不成不影響排程，回 False。"""
    if not path:
        return False
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False) + "\n", encoding="utf-8")
        return True
    except OSError:
        return False
