"""產業資金流向計算引擎：公式照金融獵場「資金輪動圖」REA 結果，對外名稱與文案本站自訂。

規格唯一來源（本人規則：REA 反推結果就是規格，不自創）：
- FINDINGS：C:/Users/User/Desktop/claude/.claude/worktrees/recovery-ready-20261029/
  .scratch/finhunt-sector-rotation-rea/FINDINGS.md（含 2026-11-08 更正）
- DIGEST：同目錄 DATA-ORIGIN-DIGEST-20261108.md〈資金輪動圖〉

與原站必要的差異（明列，不冒稱原站同值）：
- 族群：原站 49 個概念族群是它人工整理的清單，依本人規則不抄；本版改用官方產業別，
  每檔只屬一個產業，所以不會像原站那樣跨族群重複加總。介面接受任何「族群→成分代號」
  對照，之後可以換成本站自建的概念清單。
- 成交額：原站「當日成交額」的定義是【未知】（FINDINGS §3）；本站用證交所／櫃買公布的
  官方全日成交金額（元），數字會比原站大，但相對位置相近（FINDINGS §5-2）。
- 原站族群數字先取整到 0.1 再算 X（FINDINGS §3）；本站用未取整的原值計算。
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parent
LISTED_DB = ROOT / "data/stock-history/stock-history.sqlite"
OTC_DB = ROOT / "data/otc-history/otc_history.sqlite"

METHOD = "finhunt-sector-rotation-rea-1"
YI = 100_000_000          # 1 億元
SHI_YI = 1_000_000_000    # 1 十億元
WINDOW_LONG = 20
WINDOW_SHORT = 5
TIMELINE_DAYS = 20        # DIGEST〈當前日期與「20 個交易日」〉

# 各產業強勢股（本人 2026-11-20 定案；本站自訂，不是 REA 公式）
STRONG_WINDOW = 3         # 近幾個交易日的累計漲幅
STRONG_LIMIT = 3          # 每個產業最多列幾檔
STRONG_MIN_AVG_TURNOVER = 50_000_000   # 這幾天平均每天成交金額（元）至少要這麼多
LIMIT_UP_RETURN = 0.095   # 當日漲幅達 9.5% 算漲停（和個股訊號 limit_up_momentum 同一個門檻）
STRONG_RULE = "strong-3d-gain-1"

QUADRANT_ORDER = ("leading", "weakening", "improving", "lagging")
# 象限條件與排序：FINDINGS §1〈四個象限〉【前端原碼】。對外名稱、說明與色票本站自訂，不沿用原站用語。
QUADRANT_STYLE = {
    "leading": {"label": "資金領先", "meaning": "成交額高於平均，且近 5 日仍在加速流入", "color": "#059669"},
    "weakening": {"label": "資金減速", "meaning": "成交額高於平均，但近 5 日流入放緩", "color": "#D97706"},
    "improving": {"label": "資金回補", "meaning": "成交額低於平均，但近 5 日開始加速", "color": "#6366F1"},
    "lagging": {"label": "資金落後", "meaning": "成交額低於平均，且近 5 日持續減速", "color": "#EA580C"},
}

OFFICIAL_INDUSTRY_DEFINITION = {
    "kind": "official_industry",
    "note": "族群＝證交所／櫃買官方產業別，與原站人工整理的概念族群不同；每檔只屬一個族群。",
}

UNKNOWNS = (
    "原站「當日成交額」的確切定義未知（FINDINGS §3）；本站改用官方全日成交金額，絕對值會比原站大。",
    "原站概念族群的挑選與成分清單是人工整理，未使用；本站族群改為官方產業別。",
    "原站族群數字先取整到 0.1 再計算 X；本站以未取整原值計算，與原站可能有 ±0.07 的取整差。",
    "原站個股 X、Y 的回傳小數位數未知；本站比照族群（X 兩位、Y 一位）。",
    "原站後端資料上游與重算排程未知（DIGEST〈資料接口〉）。",
)


@dataclass(frozen=True)
class Window:
    """單一標的或族群在某資料日的成交額視窗（單位：元）。"""
    today: int
    sum5: int
    sum20: int


# ---- 族群公式：FINDINGS §1 表格＋11-08 更正 ----

def group_x(amount_20d: float, mean_20d: float) -> float | None:
    """X＝log2(族群近20日成交額 ÷ 全部族群近20日成交額平均)，2 位小數【已驗證】。"""
    if amount_20d is None or mean_20d is None or amount_20d <= 0 or mean_20d <= 0:
        return None
    return round(math.log2(amount_20d / mean_20d), 2)


def group_y(sum5_twd: int, sum20_twd: int) -> float:
    """Y＝近5日日均 − 近20日日均，單位億元，1 位小數（11-08 更正的單位換算已內含）。"""
    return round((sum5_twd / WINDOW_SHORT - sum20_twd / WINDOW_LONG) / YI, 1)


def relative_pct(x: float) -> int:
    """畫面顯示的「相對平均」＝(2^X − 1)×100%，取整數（FINDINGS §1、DIGEST〈橫軸 X〉）。"""
    return round((2 ** x - 1) * 100)


def quadrant(x: float | None, y: float | None) -> str | None:
    """X≥0 且 Y≥0 leading；X≥0 且 Y<0 weakening；X<0 且 Y≥0 improving；其餘 lagging【前端原碼的條件】。"""
    if x is None or y is None:
        return None
    if x >= 0:
        return "leading" if y >= 0 else "weakening"
    return "improving" if y >= 0 else "lagging"


def quadrant_counts(bubbles: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """各象限族群數，用當天全部族群計算（DIGEST〈象限數量與點選篩選〉）。"""
    counts = {name: 0 for name in QUADRANT_ORDER}
    for bubble in bubbles:
        if bubble.get("quadrant") in counts:
            counts[bubble["quadrant"]] += 1
    return counts


def timeline_max_abs_y(snapshots: Sequence[Mapping[str, Any]]) -> float:
    """資金效率排行用的 M（原站 CP 值的 M）：整條時間軸所有族群最大 |Y|，最小為 1（FINDINGS 11-08 更正、DIGEST〈CP 值排行〉）。"""
    largest = 1.0
    for snapshot in snapshots:
        for bubble in snapshot["bubbles"]:
            if bubble.get("y") is not None:
                largest = max(largest, abs(bubble["y"]))
    return largest


def efficiency_ranking(bubbles: Iterable[Mapping[str, Any]], max_abs_y: float, limit: int = 5) -> list[dict[str, Any]]:
    """資金效率排行（公式＝原站 CP 值排行）：只取 X>0；分數＝近20日累計×(1 − max(0,Y)÷M)，取前 5【前端原碼】。"""
    rows = []
    for bubble in bubbles:
        x, y = bubble.get("x"), bubble.get("y")
        if x is None or y is None or x <= 0:
            continue
        score = bubble["size"] * (1 - max(0.0, y) / max_abs_y)
        rows.append({"name": bubble["name"], "score": score, "size": bubble["size"], "x": x, "y": y})
    rows.sort(key=lambda row: (-row["score"], row["name"]))
    return rows[:limit]


def recovery_signals(bubbles: Iterable[Mapping[str, Any]], limit: int = 4) -> list[dict[str, Any]]:
    """低基期回溫訊號（公式＝原站抄底偵測）：improving 象限依 Y 由大到小取前 4【前端原碼】。"""
    rows = [{"name": b["name"], "x": b["x"], "y": b["y"]} for b in bubbles if b.get("quadrant") == "improving"]
    rows.sort(key=lambda row: (-row["y"], row["name"]))
    return rows[:limit]


_SUMMARY_TEXT = {
    ("in", "leading_dominant"): "高於平均的產業成交額占多數，且資金領先的產業不少於資金減速的產業：資金集中且仍在加速。",
    ("in", "decelerating"): "高於平均的產業成交額占多數，但資金減速的產業多於資金領先的產業：熱度正在降溫或轉移。",
    ("out", "selective_recovery"): "低於平均的產業成交額占多數，但資金回補的產業不少於資金落後的產業：部分產業開始回溫。",
    ("out", "broad_outflow"): "低於平均的產業成交額占多數，且資金落後的產業多於資金回補的產業：資金普遍退場。",
}


def market_summary(bubbles: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """資金面摘要：右邊＝X≥0 產業 |當日成交額| 加總、左邊＝X<0；分支條件照 FINDINGS §1〈盤勢總結〉【前端原碼】，文字本站自寫。"""
    known = [b for b in bubbles if b.get("x") is not None]
    right = round(sum(abs(b["amount_today"]) for b in known if b["x"] >= 0), 2)
    left = round(sum(abs(b["amount_today"]) for b in known if b["x"] < 0), 2)
    counts = quadrant_counts(known)
    if right >= left:
        side = "in"
        branch = "leading_dominant" if counts["leading"] >= counts["weakening"] else "decelerating"
    else:
        side = "out"
        branch = "selective_recovery" if counts["lagging"] <= counts["improving"] else "broad_outflow"
    return {"capital_side": side, "branch": branch, "right_amount": right, "left_amount": left,
            "counts": counts, "text": _SUMMARY_TEXT[(side, branch)]}


# ---- 個股（族群下鑽）：FINDINGS §1 表格 ----

def stock_points(windows: Mapping[str, Window]) -> list[dict[str, Any]]:
    """個股 X＝log2(個股20日 ÷ 同族群非零成分股20日平均)，20日為0者 X=0；個股 Y＝(5日/5 − 20日/20)×10（億）。"""
    nonzero = [w.sum20 for w in windows.values() if w.sum20 > 0]
    mean = sum(nonzero) / len(nonzero) if nonzero else None
    points = []
    for code in sorted(windows):
        window = windows[code]
        x = 0.0 if window.sum20 <= 0 or mean is None else round(math.log2(window.sum20 / mean), 2)
        y = round((window.sum5 / WINDOW_SHORT - window.sum20 / WINDOW_LONG) / YI * 10, 1)
        points.append({
            "code": code, "x": x, "y": y, "quadrant": quadrant(x, y),
            "relative_pct": relative_pct(x),
            "amount_today": round(window.today / YI, 2),
            "amount_5d": round(window.sum5 / YI, 2),
            "amount_20d": round(window.sum20 / YI, 2),
        })
    return points


# ---- 族群快照與時間軸 ----

def official_industry_groups(items: Iterable[tuple[str, str, str]]) -> dict[str, list[str]]:
    """(代號, 官方產業別, 市場) → {產業別: [代號…]}；上市與上櫃同一產業併成同一族群。"""
    groups: dict[str, list[str]] = defaultdict(list)
    for code, industry, _market in items:
        if industry:
            groups[industry].append(code)
    return {name: sorted(codes) for name, codes in sorted(groups.items())}


def _window(series: Mapping[str, int | None], dates: Sequence[str], index: int) -> Window | None:
    span = [series.get(day) for day in dates[index - WINDOW_LONG + 1:index + 1]]
    if len(span) < WINDOW_LONG or any(value is None for value in span):
        return None
    return Window(today=span[-1], sum5=sum(span[-WINDOW_SHORT:]), sum20=sum(span))


def group_snapshot(groups: Mapping[str, Sequence[str]], turnover: Mapping[str, Mapping[str, int | None]],
                   dates: Sequence[str], index: int) -> dict[str, Any]:
    """某資料日的族群泡泡；成分股 20 日內有任一天缺值就排除並記錄，不補 0。"""
    if index < WINDOW_LONG - 1:
        raise ValueError("資料日之前不足 20 個交易日")
    windows: dict[str, Window] = {}
    excluded: dict[str, list[str]] = {}
    members: dict[str, dict[str, Window]] = {}
    for name in sorted(groups):
        ok: dict[str, Window] = {}
        missing: list[str] = []
        for code in groups[name]:
            window = _window(turnover.get(code, {}), dates, index)
            if window is None:
                missing.append(code)
            else:
                ok[code] = window
        excluded[name] = missing
        if ok:
            members[name] = ok
            windows[name] = Window(today=sum(w.today for w in ok.values()),
                                   sum5=sum(w.sum5 for w in ok.values()),
                                   sum20=sum(w.sum20 for w in ok.values()))
    mean_20d = sum(w.sum20 for w in windows.values()) / len(windows) if windows else None
    bubbles = []
    for name, window in windows.items():
        x = group_x(window.sum20, mean_20d)
        y = group_y(window.sum5, window.sum20)
        bubbles.append({
            "name": name, "x": x, "y": y, "quadrant": quadrant(x, y),
            "relative_pct": None if x is None else relative_pct(x),
            "size": round(window.sum20 / SHI_YI, 1),           # 泡泡大小＝近20日累計（十億）
            "amount_20d": round(window.sum20 / SHI_YI, 1),     # 族群 20／5 日累計單位：十億
            "amount_5d": round(window.sum5 / SHI_YI, 1),
            "amount_today": round(window.today / YI, 1),       # 族群當日成交額單位：億
            "member_count": len(members[name]),
            "excluded_members": excluded[name],
        })
    order = {name: rank for rank, name in enumerate(QUADRANT_ORDER)}
    bubbles.sort(key=lambda b: (order.get(b["quadrant"], len(order)), -b["size"], b["name"]))
    unavailable = [{"name": name, "excluded_members": excluded[name]} for name in sorted(groups) if name not in windows]
    return {"date": dates[index], "trading_days": WINDOW_LONG, "bubbles": bubbles,
            "unavailable_groups": unavailable, "quadrant_counts": quadrant_counts(bubbles)}


def axis_bounds(snapshots: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    """座標範圍用整條時間軸：X 兩端＝max(0.1, 最大正／負 X)×1.2；Y＝±max(50, 最大|Y|×1.02 取到 50 的倍數)。"""
    xs = [b["x"] for s in snapshots for b in s["bubbles"] if b.get("x") is not None]
    max_pos = max([0.1] + [x for x in xs if x > 0])
    max_neg = max([0.1] + [-x for x in xs if x < 0])
    m = timeline_max_abs_y(snapshots)
    return {"x_min": -max_neg * 1.2, "x_max": max_pos * 1.2,
            "y_half_range": max(50, math.ceil(m * 1.02 / 50) * 50)}


def bubble_radius(size: float, max_size: float) -> float:
    """半徑＝22＋(size÷時間軸最大 size)×30，限制在 22～52 像素（DIGEST〈泡泡大小〉，未含圖寬縮放）。"""
    if not max_size or max_size <= 0:
        return 22
    return min(52, max(22, 22 + max(0.0, size) / max_size * 30))


def compute_timeline(groups: Mapping[str, Sequence[str]], turnover: Mapping[str, Mapping[str, int | None]],
                     dates: Sequence[str], group_definition: Mapping[str, str] = OFFICIAL_INDUSTRY_DEFINITION,
                     days: int = TIMELINE_DAYS) -> dict[str, Any]:
    """最近 20 個交易日的產業快照（舊→新），每天附資金面摘要、資金效率排行與低基期回溫訊號。"""
    dates = sorted(dates)
    if len(dates) < days + WINDOW_LONG - 1:
        raise ValueError(f"時間軸需要至少 {days + WINDOW_LONG - 1} 個交易日的成交額")
    snapshots = [group_snapshot(groups, turnover, dates, index) for index in range(len(dates) - days, len(dates))]
    m = timeline_max_abs_y(snapshots)
    for snapshot in snapshots:
        snapshot["summary"] = market_summary(snapshot["bubbles"])
        snapshot["efficiency_ranking"] = efficiency_ranking(snapshot["bubbles"], m)
        snapshot["recovery_signals"] = recovery_signals(snapshot["bubbles"])
    return {
        "method": METHOD,
        "title": "產業資金流向",
        "quadrants": {key: dict(QUADRANT_STYLE[key]) for key in QUADRANT_ORDER},
        "group_definition": dict(group_definition),
        "units": {"group_amount_20d": "十億元", "group_amount_5d": "十億元", "group_amount_today": "億元",
                  "y": "億元", "stock_amounts": "億元", "stock_y": "億元×10（原站刻度）"},
        "unknowns": list(UNKNOWNS),
        "max_abs_y": m,
        "axis_bounds": axis_bounds(snapshots),
        "snapshots": snapshots,
    }


def rotation_inflow_codes(snapshot: Mapping[str, Any], groups: Mapping[str, Sequence[str]]) -> set[str]:
    """資金加速產業的成分代號：Y≥0 的 leading＋improving（DIGEST〈輪動資料被策略選股重用〉的規則）。"""
    codes: set[str] = set()
    for bubble in snapshot["bubbles"]:
        if bubble.get("y") is not None and bubble["y"] >= 0:
            codes.update(groups.get(bubble["name"], ()))
    return codes


# ---- 各產業強勢股（本站自訂，不是 REA；本人 2026-11-20 定案） ----

def _day_return(quote: Mapping[str, Any]) -> float:
    """當日相對前一日參考價的漲跌幅（比率）＝漲跌 ÷（收盤 − 漲跌），和個股訊號（limit_up_momentum）同一個算式。

    官方沒有公布漲跌的日子（除權息等不比價，漲跌是空的）當 0，也和那邊相同：不把息值算成下跌。"""
    close, change = quote["close"], quote["change"]
    if change is None:
        return 0.0
    return change / (close - change)


def _strong_candidate(code: str, days: Sequence[Mapping[str, Any] | None]) -> tuple[float, int, str, dict[str, Any]] | None:
    """一檔成分股這幾天的排名資料 (−漲幅, −成交金額合計, 代號, 輸出列)；不合格回 None。"""
    # 這幾天要天天有成交：剛上市天數不足、停牌、整天沒成交、缺成交金額的都不列
    if any(day is None or day["suspended"] or day["close"] is None or day["turnover"] is None for day in days):
        return None
    turnover = sum(day["turnover"] for day in days)
    if turnover < STRONG_MIN_AVG_TURNOVER * len(days):
        return None
    returns = [_day_return(day) for day in days]
    gain = round((math.prod(1 + value for value in returns) - 1) * 100, 2)
    if gain <= 0:
        return None
    return (-gain, -turnover, code, {
        "code": code, "name": days[-1]["name"], "gain_pct": gain,
        "today_pct": round(returns[-1] * 100, 2), "limit_up": returns[-1] >= LIMIT_UP_RETURN})


def strong_stocks(groups: Mapping[str, Sequence[str]], quotes: Mapping[str, Mapping[str, Mapping[str, Any]]],
                  dates: Sequence[str]) -> dict[str, Any]:
    """每個產業近 3 個交易日累計漲幅最大的成分股（最多 3 檔，只列有漲的；一檔都沒有的產業是空清單）。

    ``quotes``：{代號: {日期: {name, close, change, turnover, suspended}}}；``dates`` 由舊到新，取最後 3 天。
    漲幅用每天的官方漲跌連乘，不拿兩天的收盤直接相除（除權息、分割那天才不會被算成大跌）。
    """
    window = list(dates)[-STRONG_WINDOW:]
    if len(window) < STRONG_WINDOW:
        raise ValueError(f"強勢股需要 {STRONG_WINDOW} 個交易日")
    result: dict[str, list[dict[str, Any]]] = {}
    for name in sorted(groups):
        found = [_strong_candidate(code, [quotes.get(code, {}).get(day) for day in window]) for code in groups[name]]
        # 漲幅大的在前；同漲幅時成交金額大的在前，再同就照代號，結果才固定
        ranked = sorted((item for item in found if item is not None), key=lambda item: item[:3])
        result[name] = [row for *_, row in ranked[:STRONG_LIMIT]]
    return {"rule_version": STRONG_RULE, "date": window[-1], "window_days": STRONG_WINDOW, "limit": STRONG_LIMIT,
            "min_avg_turnover": STRONG_MIN_AVG_TURNOVER, "groups": result}


# ---- 本機資料（唯讀） ----

def load_official_universe() -> list[tuple[str, str, str]]:
    """上市＋上櫃普通股 (代號, 官方產業別, 市場)。兩份名錄沒有公開列舉 API，沿用已驗 SHA 後的代號集合。"""
    from listed_catalog import load_listed_catalog
    from otc_catalog import load_otc_catalog

    listed_raw = sorted((ROOT / "data/listed-catalog/raw").glob("twse-isin-listed-*.html"))[-1]
    otc_raw = sorted((ROOT / "data/otc-catalog/raw").glob("tpex-isin-otc-*.html"))[-1]
    listed = load_listed_catalog(listed_raw, listed_raw.with_suffix(".meta.json"), LISTED_DB)
    otc = load_otc_catalog(otc_raw, otc_raw.with_suffix(".meta.json"), OTC_DB)
    items = [(code, entry[4], "上市") for code, entry in getattr(listed, "_entries").items()]
    items += [(code, entry[4], "上櫃") for code, entry in getattr(otc, "_entries").items()]
    return items


def load_turnover(codes: Iterable[str], as_of: str, count: int,
                  listed_db: Path = LISTED_DB, otc_db: Path = OTC_DB) -> tuple[list[str], dict[str, dict[str, int | None]]]:
    """以上市交易日曆取 as_of 以前 count 個交易日，讀兩市場每日成交金額（元）；mode=ro。"""
    wanted = set(codes)
    turnover: dict[str, dict[str, int | None]] = defaultdict(dict)
    connection = sqlite3.connect(f"file:{listed_db.resolve().as_posix()}?mode=ro", uri=True)
    try:
        dates = [row[0] for row in connection.execute(
            "SELECT DISTINCT date FROM daily WHERE date <= ? ORDER BY date DESC LIMIT ?", (as_of, count))]
        dates.sort()
        if not dates:
            raise ValueError("上市日 K 沒有可用交易日")
        for db in (listed_db, otc_db):
            conn = connection if db == listed_db else sqlite3.connect(
                f"file:{db.resolve().as_posix()}?mode=ro", uri=True)
            try:
                for day, code, value in conn.execute(
                        "SELECT date, code, turnover FROM daily WHERE date BETWEEN ? AND ?", (dates[0], dates[-1])):
                    if code in wanted:
                        turnover[code][day] = value
            finally:
                if conn is not connection:
                    conn.close()
    finally:
        connection.close()
    return dates, dict(turnover)


def load_quotes(codes: Iterable[str], dates: Sequence[str],
                listed_db: Path = LISTED_DB, otc_db: Path = OTC_DB) -> dict[str, dict[str, dict[str, Any]]]:
    """強勢股要的行情：{代號: {日期: {name, close, change, turnover, suspended}}}，只讀 ``dates`` 的最後 3 天；mode=ro。"""
    wanted = set(codes)
    window = list(dates)[-STRONG_WINDOW:]
    quotes: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for db in (listed_db, otc_db):
        connection = sqlite3.connect(f"file:{Path(db).resolve().as_posix()}?mode=ro", uri=True)
        try:
            for day, code, name, close, change, turnover, suspended in connection.execute(
                    "SELECT date, code, name, close, change, turnover, suspended FROM daily "
                    f"WHERE date IN ({','.join('?' * len(window))})", window):
                if code in wanted:
                    quotes[code][day] = {"name": name, "close": close, "change": change,
                                         "turnover": turnover, "suspended": suspended}
        finally:
            connection.close()
    return dict(quotes)


def main() -> int:
    parser = argparse.ArgumentParser(description="產業資金流向（官方產業別），唯讀本機資料")
    parser.add_argument("--as-of", required=True, help="資料日 YYYY-MM-DD")
    parser.add_argument("--json", action="store_true", help="輸出完整時間軸 JSON 到標準輸出")
    args = parser.parse_args()
    universe = load_official_universe()
    groups = official_industry_groups(universe)
    dates, turnover = load_turnover([code for code, _, _ in universe], args.as_of, TIMELINE_DAYS + WINDOW_LONG - 1)
    timeline = compute_timeline(groups, turnover, dates)
    if args.json:
        print(json.dumps(timeline, ensure_ascii=False, indent=1))
        return 0
    latest = timeline["snapshots"][-1]
    print(json.dumps({
        "date": latest["date"], "groups": len(latest["bubbles"]),
        "unavailable_groups": len(latest["unavailable_groups"]),
        "quadrant_counts": latest["quadrant_counts"], "summary": latest["summary"]["text"],
        "efficiency_ranking": [(r["name"], round(r["score"], 1)) for r in latest["efficiency_ranking"]],
        "recovery_signals": [(r["name"], r["y"]) for r in latest["recovery_signals"]],
        "max_abs_y": timeline["max_abs_y"],
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
