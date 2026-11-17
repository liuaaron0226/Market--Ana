"""基金配息摘要（基金當成一般個股 06 票，本人 2026-11-18；規格 .scratch/fund-as-stock/spec.md 第 28 條）。

個股頁「配息」分類的資料：最近一次除息與每單位配息、各年配息次數與合計、每年配息來源（本金／可分配淨利益）的事實，
以及一句本金註記。輸入是 ``fund_compare.load_history`` 合併好的配息列（集保回補原件＋開放資料 3-5），只寫集保公布的事實：
來源比例集保還沒公布（N/A）的那幾次寫「來源未公布」，不猜。
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

ALL_CAPITAL = 99.995   # 集保比例到小數兩位：100.00 才算「全部來自本金」


def _kind(row: Mapping[str, Any]) -> str:
    capital = row.get("capital_pct")
    if capital is None:
        return "unknown"
    if capital >= ALL_CAPITAL:
        return "all_capital"
    return "part_capital" if capital > 0 else "income_only"


_PHRASES = (("all_capital", "全部來自本金"), ("part_capital", "部分來自本金"),
            ("income_only", "來自可分配淨利益"), ("unknown", "來源未公布"))


def _source(year: Mapping[str, Any]) -> str:
    """一年的來源事實，例如「全部來自本金」「11 次全部來自本金、1 次部分來自本金」「9 次來自可分配淨利益、1 次來源未公布」。"""
    present = [(key, phrase) for key, phrase in _PHRASES if year[key]]
    if len(present) > 1:
        return "、".join(f"{year[key]} 次{phrase}" for key, phrase in present)
    key, phrase = present[0]
    return "全部來自可分配淨利益" if key == "income_only" else phrase


def capital_note(dists: Sequence[Mapping[str, Any]]) -> str | None:
    """一句本金註記：哪幾年有配息來自本金，以及從哪個月起公布的配息都來自可分配淨利益；沒有任何配息回 None。"""
    if not dists:
        return None
    parts = []
    for year in sorted({row["ex_date"][:4] for row in dists}):
        rows = [row for row in dists if row["ex_date"][:4] == year]
        kinds = [_kind(row) for row in rows]
        whole = kinds.count("all_capital")
        if whole + kinds.count("part_capital"):
            parts.append(f"{year} 年 {len(rows)} 次配息全部來自本金" if whole == len(rows)
                         else f"{year} 年 {len(rows)} 次配息中 {whole} 次全部來自本金")
    known = [row for row in dists if row.get("capital_pct") is not None]
    since = None
    for row in reversed(known):
        if row["capital_pct"] > 0:
            break
        since = row["ex_date"]
    if since and since != (known[0]["ex_date"] if known else None):
        parts.append(f"{int(since[:4])} 年 {int(since[5:7])} 月起公布的配息都來自可分配淨利益")
    return "這檔配息可能來自本金" + (f"：{'；'.join(parts)}。" if parts else "。")


def summary(dists: Sequence[Mapping[str, Any]], *, as_of: str, currency: str) -> dict[str, Any]:
    """分析契約 kind fund 的 ``distributions`` 欄。只用除息日在資料日（含）以前的配息；年份新的在前。

    ``as_of`` 是建置的資料日（和淨值同一個截止日）。淨值約晚 3 個營業日公布、配息是事先公告的，
    所以最近一次除息可能比淨值線最後一天還新——那是集保已公布的事實，照寫。"""
    rows = sorted((row for row in dists if row["ex_date"] <= as_of), key=lambda row: row["ex_date"])
    years: dict[str, dict[str, Any]] = {}
    for row in rows:
        year = years.setdefault(row["ex_date"][:4], {"year": row["ex_date"][:4], "count": 0, "total": 0.0,
                                                     **{key: 0 for key, _ in _PHRASES}})
        year["count"] += 1
        year["total"] += float(row["amount"])
        year[_kind(row)] += 1
    by_year = [{**year, "total": round(year["total"], 4), "source": _source(year)}
               for year in (years[key] for key in sorted(years, reverse=True))]
    last = rows[-1] if rows else None
    return {
        "currency": currency, "count": len(rows),
        "latest": {key: last.get(key) for key in ("ex_date", "record_date", "pay_date", "amount", "capital_pct", "income_pct")}
        if last else None,
        "years": by_year, "capital_note": capital_note(rows),
    }
