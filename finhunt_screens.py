"""G1：籌碼選股 10 分頁計算引擎；演算法依金融獵場 REA 結果，對外名稱為本站自訂。

規格主來源（唯讀）：
- recovery-ready-20261029/.scratch/finhunt-sector-rotation-rea/DATA-ORIGIN-DIGEST-20261108.md〈策略選股〉
- .scratch/finhunt-private-rebuild/strategy-source-readable.txt（分頁設定、條件、天數、每頁 50 列）

REA 查不到的條件（ETF 成分、券商分點 Top15）不自創定義，輸出 unknown／unavailable。
法人與行情來源以 ``MarketSource`` 介面注入；上櫃由 ``OtcSqliteSource`` 讀既有資料庫（唯讀）。

R1（2026-11-09）回填：
- 相對低位階：會員研究 127 檔全對（finhunt-sector-rotation-rea/member-20261108/FINDINGS-MEMBER.md
  〈甜價位階股規則〉）——輪動族群成分股（不加象限條件）且個股 20 日乖離 < 所屬市場指數 20 日乖離。
- N 日加總與連續天數中斷口徑：會員第二輪驗證（reverse-engineering-round2.md §3，14/14＋12/12、180/180）。
- 經營者口述的操作原則以 ``TAB_USAGE_NOTES`` 附在各頁（B 級，非網站計算訊號）。
"""
from __future__ import annotations

import math
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Protocol, Sequence

ROOT = Path(__file__).resolve().parent
ENGINE_VERSION = "finhunt-screens-g1"

PAGE_SIZE = 50  # 【前端原碼】pageSize:50
RANK_LIMIT = 50  # 【前端原碼】continuous-rank limit=50；排行每頁 50 筆，後端總筆數未知，保守取 50


@dataclass(frozen=True)
class Security:
    code: str
    name: str
    market: str  # 上市／上櫃
    kind: str = "common"  # common／etf（V2：排行母體含大量股 ETF）


SECURITY_TYPE_LABEL = {"common": "普通股", "etf": "ETF"}


@dataclass(frozen=True)
class Tab:
    key: str
    display_name: str
    conditions: tuple[str, ...]
    kind: str  # rank／streak／rotation_unknown／unavailable／not_applicable
    investor: str | None = None  # foreign／trust
    day_options: tuple[int, ...] = (1,)
    rotation_checkbox: bool = True
    method_evidence: str = "推論"


TABS: tuple[Tab, ...] = (
    Tab("curated_list", "研究精選名單", ("研究人員人工挑選的名單",), "not_applicable",
        rotation_checkbox=False, method_evidence="人工名單"),
    Tab("rotation_relative_value", "資金流入族群・相對低位階",
        ("資金加速流入（輪動 Y≥0）族群的成分股", "股價位階低於大盤"), "relative_value",
        rotation_checkbox=False, method_evidence="原站重現"),
    Tab("rotation_etf_constituent", "資金流入族群・ETF成分股",
        ("資金加速流入（輪動 Y≥0）族群的成分股", "主動式或被動式 ETF 成分股"), "rotation_unknown",
        rotation_checkbox=False, method_evidence="未知"),
    Tab("foreign_net_rank", "外資淨買超排行", ("外資及陸資買賣超排行",), "rank", "foreign", (1, 3, 5, 10, 20),
        method_evidence="會員驗證"),
    Tab("trust_net_rank", "投信淨買超排行", ("投信買賣超排行",), "rank", "trust", (1, 3, 5, 10, 20),
        method_evidence="會員驗證"),
    Tab("broker_top15_net", "主要券商淨買超", ("前 15 大券商分點合計買賣超排行",), "unavailable",
        day_options=(1, 3, 5, 10, 20), method_evidence="未知"),
    Tab("broker_concentration", "券商籌碼集中度", ("前 15 大券商分點籌碼集中度排行",), "unavailable",
        day_options=(1, 5, 20), method_evidence="未知"),
    Tab("foreign_streak", "外資連續淨買超", ("外資連續買超／賣超天數排行",), "streak", "foreign", method_evidence="會員驗證"),
    Tab("trust_streak", "投信連續淨買超", ("投信連續買超／賣超天數排行",), "streak", "trust", method_evidence="會員驗證"),
    Tab("broker_streak", "主要券商連續淨買超", ("前 15 大券商分點連續買超／賣超天數排行",), "unavailable",
        method_evidence="未知"),
)
TAB_BY_KEY = {tab.key: tab for tab in TABS}

UNKNOWN_NOTES = {
    "rotation_etf_constituent": [
        "條件 2 涵蓋哪些 ETF、用哪天的成分、有無權重門檻仍查不到；會員第二輪只看出被排除者偏向中小型冷門股（近似），"
        "需官方 ETF 成分清單才能驗證，本站不自創定義。",
        "條件 1 的族群成分清單由原站自建，本站尚無對應的合法來源。",
    ],
}
UNAVAILABLE_NOTES = [
    "需要逐日券商分點進出資料；會員第二輪比對三大法人各欄都不吻合（0/36），無法由法人資料重算。",
    "前 15 大分點的選法、加總方式、集中度分子分母仍查不到，本站不自創定義。",
]
RANK_NOTES = [
    "N 日＝最近 N 個交易日逐日買賣超的加總；會員第二輪驗證外資 14/14、投信 12/12（合計 26/26，差 ≤1 張）。",
    "原站顯示單位為張（股÷1000）；本站同時輸出股數與張數，不換算成金額。",
    "任一天原件缺該檔或欄位空白，該檔 N 日累計視為不可得，不當成 0。",
]
# V2（2026-11-09）：本人定案「整個網站都只做大量股」；原站排行母體含 ETF（REA），協調窗裁決排行納入 ETF，
# 但 ETF 也要通過同一條大量股規則。普通股與 ETF 共用這段說明。
UNIVERSE_NOTE = ("排行母體＝大量股：上市＋上櫃普通股依 20 日平均成交金額排名前 300（跌出前 400 才移出），"
                 "加上同口徑名次也在前 300 的 ETF（原站排行含 ETF）；每列標示普通股或 ETF。")
RANK_NOTES.append(UNIVERSE_NOTE)
STREAK_NOTES = [
    "從最新交易日往回數嚴格同號（買超或賣超）的交易日數；會員第二輪可驗證的 180 筆全部吻合（180/180）。",
    "中斷口徑：買賣超為 0、停牌或當日原件沒有該檔都算中斷（0 跳過版 165/180、0 計入版 156/180 皆不符）。",
    "連續天數達到本站可得的全部交易日時，標為下限（實際可能更長）。",
    UNIVERSE_NOTE,
]
RELATIVE_VALUE_NOTES = [
    "規則（會員研究 127 檔全對）：屬於任一輪動族群的成分股（不加象限條件），且個股 20 日乖離 < 所屬市場指數 20 日乖離。",
    "20 日乖離＝收盤 ÷ 含當日的 20 日簡單均線 − 1；上市比加權指數、上櫃比櫃買指數。",
    "族群定義：本站以官方產業別作為族群，與原站自建的 49 個概念族群不同，入選名單會因此不同。",
    "原站條件文字寫「第一二象限」，但會員研究顯示象限沒有篩選作用；`<` 與 `≤` 尚未分辨。",
]

# 經營者口述的操作原則（B 級）：出處見 reference-rules-external-research.md §2.2、§2.3，以及 member-rules-audit.md §3.3。
_B = "經營者說明，非網站計算訊號"
TAB_USAGE_NOTES = {
    "foreign_net_rank": [
        {"grade": "B", "label": _B, "text": "核心持股看的是外資有沒有連續好幾天大賣，而不是單日漲跌。",
         "source": "YouTube lN_Mskg1lco 08:02–08:10"},
        {"grade": "B", "label": _B, "text": "訊號要連續兩次才算方向，一次可能只是巧合。", "source": "會員文章 article?id=33（11/06）"},
    ],
    "trust_net_rank": [
        {"grade": "B", "label": _B, "text": "訊號要連續兩次才算方向，一次可能只是巧合。", "source": "會員文章 article?id=33（11/06）"},
        {"grade": "B", "label": _B, "text": "殺盤日的名單先拿來觀察，等止穩之後看誰先站起來，不急著出手。", "source": "會員文章（11/06）"},
    ],
    "foreign_streak": [
        {"grade": "B", "label": _B, "text": "核心持股不用急著全賣，分批調整；要看外資是否連續多日賣超。",
         "source": "YouTube lN_Mskg1lco 08:02–08:10"},
    ],
    "trust_streak": [
        {"grade": "B", "label": _B, "text": "訊號要連續兩次才算方向，一次可能只是巧合。", "source": "會員文章 article?id=33（11/06）"},
    ],
    "rotation_relative_value": [
        {"grade": "B", "label": _B, "text": "錢先到、價後到：只漲價、資金沒跟上的不追。", "source": "YouTube wrPwnRRy_8g"},
        {"grade": "B", "label": _B, "text": "比較穩健的做法是等量縮價穩，或拉回沒有跌破前低時再找買點。",
         "source": "YouTube b7gHMxXZ44Q 06:55–07:05"},
        {"grade": "B", "label": _B, "text": "產業資金流向看的是成交金額，不是法人買賣超；加速度本身是落後指標。",
         "source": "YouTube yvIWz_QhIjU 08:05–08:10；5gDLYubDHP0 05:30–05:45"},
    ],
}


class MarketSource(Protocol):
    market: str

    def trading_days(self) -> list[str]: ...

    def institutional(self, day: str) -> Mapping[str, Mapping[str, int | None]]: ...

    def quotes(self, day: str) -> Mapping[str, Mapping[str, float | bool | None]]: ...


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _rotation_members(rotation: Sequence[Mapping] | None) -> tuple[str | None, set[str] | None]:
    """【前端原碼】取 latest_date 最新的一天，Y≥0 族群（第一二象限）的成分股集合。"""
    if not rotation:
        return None, None
    latest = max(rotation, key=lambda snapshot: str(snapshot.get("date") or ""))
    members: set[str] = set()
    for group in latest.get("groups") or []:
        y = group.get("y")
        if _finite(y) and y >= 0:
            members.update(str(code) for code in group.get("codes") or [])
    return latest.get("date"), members


def _quote_columns(source: MarketSource, day: str, code: str) -> dict:
    quote = source.quotes(day).get(code) or {}
    close = quote.get("close") if _finite(quote.get("close")) else None
    change = quote.get("change") if _finite(quote.get("change")) else None
    previous = close - change if close is not None and change is not None else None
    change_pct = round(change / previous * 100, 2) if previous not in (None, 0) else None
    return {"close": close, "change": change, "change_pct": change_pct}


def _rank_metric(source: MarketSource, window: list[str], code: str, investor: str) -> int | None:
    total = 0
    for day in window:
        value = (source.institutional(day).get(code) or {}).get(investor)
        if not _finite(value):
            return None
        total += int(value)
    return total


def _streak(source: MarketSource, days: list[str], code: str, investor: str, sign: int) -> tuple[int, bool]:
    count = 0
    for day in reversed(days):
        value = (source.institutional(day).get(code) or {}).get(investor)
        if not _finite(value) or value * sign <= 0:
            return count, False
        count += 1
    return count, True


def _base(tab: Tab) -> dict:
    return {
        "engine_version": ENGINE_VERSION,
        "key": tab.key,
        "display_name": tab.display_name,
        "conditions": list(tab.conditions),
        "evidence": {"source": "金融獵場 REA（DATA-ORIGIN-DIGEST-20261108）", "method": tab.method_evidence, "notes": []},
        "items": [],
        "total": 0,
        "missing_count": 0,
        "page": 1,
        "pages": 1,
        "page_size": PAGE_SIZE,
        "only_rotation_applied": False,
        "rotation_date": None,
        "data_dates": {},
        "uncovered_markets": [],
        "unknown": [],
        "usage_notes": list(TAB_USAGE_NOTES.get(tab.key, [])),
    }


def _relative_value(result: dict, universe: Iterable[Security], groups: Mapping[str, Sequence[str]] | None,
                    bias: Mapping | None, page: int) -> dict:
    """相對低位階（會員研究 127 檔全對）：族群成分股且 20 日乖離 < 所屬市場指數 20 日乖離。"""
    result["evidence"]["notes"] = list(RELATIVE_VALUE_NOTES)
    if not groups or not bias or not bias.get("indexes"):
        # 規則已確認，但呼叫端沒注入族群成分與 20 日乖離（例如發布流程尚未接線）：不輸出名單，也不捏造數字。
        result.update(state="unknown", total=None,
                      unknown=["計算規則已確認（會員研究 127 檔全對），但這次沒有提供族群成分與 20 日乖離資料，待發布流程接線。"])
        return result
    member_group: dict[str, str] = {}
    for name, codes in groups.items():
        for code in codes:
            member_group.setdefault(str(code), name)
    stocks, indexes = bias.get("stocks") or {}, bias["indexes"]
    sources = bias.get("index_sources") or {}
    rows, missing, uncovered = [], 0, set()
    for security in universe:
        if security.kind != "common":  # 規則對象是族群成分股；ETF 不屬於任何產業族群
            continue
        group = member_group.get(security.code)
        if group is None:
            continue
        index_bias = indexes.get(security.market)
        if not _finite(index_bias):
            uncovered.add(security.market)
            continue
        value = stocks.get(security.code)
        if not _finite(value):
            missing += 1
            continue
        if value < index_bias:
            rows.append({"code": security.code, "name": security.name, "market": security.market, "group": group,
                         "bias20": value, "index_bias20": index_bias, "index_name": sources.get(security.market)})
    rows.sort(key=lambda row: (row["bias20"] - row["index_bias20"], row["code"]))
    pages = max(1, math.ceil(len(rows) / PAGE_SIZE))
    page = min(max(1, page), pages)
    result["uncovered_markets"] = sorted(uncovered, reverse=True)
    result["data_dates"] = {"bias": bias.get("data_date")}
    result.update(state="partial" if (missing or uncovered) else "available", total=len(rows), missing_count=missing,
                  page=page, pages=pages, items=rows[(page - 1) * PAGE_SIZE: page * PAGE_SIZE])
    return result


def run_screen(key: str, sources: Iterable[MarketSource], universe: Iterable[Security], *,
               direction: str = "buy", days: int = 1, rotation: Sequence[Mapping] | None = None,
               only_rotation: bool = False, page: int = 1, groups: Mapping[str, Sequence[str]] | None = None,
               bias: Mapping | None = None) -> dict:
    """依分頁 key 回傳排行結果；``universe`` 是上市＋上櫃普通股母體（ETF 不在其中）。

    ``groups``／``bias`` 只給相對低位階頁：族群成分（{族群: [代號]}）與 20 日乖離
    （{"stocks": {代號: 乖離}, "indexes": {市場: 乖離}, "index_sources": {市場: 指數名}, "data_date": ...}）。
    """
    tab = TAB_BY_KEY.get(key)
    if tab is None:
        raise ValueError(f"未知的選股分頁：{key}")
    if direction not in ("buy", "sell"):
        raise ValueError("direction 只能是 buy 或 sell")
    if days not in tab.day_options:
        raise ValueError(f"{tab.display_name} 不支援 {days} 日")
    result = _base(tab)
    rotation_date, members = _rotation_members(rotation)
    result["rotation_date"] = rotation_date

    if tab.kind == "relative_value":
        return _relative_value(result, universe, groups, bias, page)
    if tab.kind == "not_applicable":
        result.update(state="not_applicable", total=None)
        result["evidence"]["notes"] = ["原站為分析師人工挑選，前端看不到任何篩選公式，無法以演算法重建。"]
        return result
    if tab.kind == "unavailable":
        result.update(state="unavailable", total=None)
        result["evidence"]["notes"] = list(UNAVAILABLE_NOTES)
        return result
    if tab.kind == "rotation_unknown":
        result.update(state="unknown", total=None, unknown=list(UNKNOWN_NOTES[key]))
        result["rotation_member_count"] = len(members) if members is not None else None
        result["evidence"]["notes"] = ["條件 1 的輪動判斷（Y≥0）為【已驗證】，但條件 2 未知，因此整頁不輸出名單。"]
        return result

    universe = list(universe)
    sources = list(sources)
    by_market = {source.market: source for source in sources}
    result["uncovered_markets"] = sorted({s.market for s in universe} - set(by_market), reverse=True)
    sign = 1 if direction == "buy" else -1
    rows: list[dict] = []
    missing = 0
    market_missing: list[str] = []
    for market, source in by_market.items():
        trading_days = sorted(source.trading_days())
        if not trading_days:
            market_missing.append(market)
            continue
        result["data_dates"][market] = trading_days[-1]
        if tab.kind == "rank":
            if len(trading_days) < days:
                market_missing.append(market)
                continue
            window = trading_days[-days:]
            result["window_days"] = window
        for security in universe:
            if security.market != market:
                continue
            if tab.kind == "rank":
                metric = _rank_metric(source, window, security.code, tab.investor)
                if metric is None:
                    missing += 1
                    continue
                if metric * sign <= 0:
                    continue
                row = {"metric_shares": metric, "metric_lots": round(metric / 1000, 3), "_order": metric * sign}
            else:
                streak, lower_bound = _streak(source, trading_days, security.code, tab.investor, sign)
                if streak == 0:
                    continue
                row = {"streak_days": streak, "streak_is_lower_bound": lower_bound, "_order": streak}
            row.update(code=security.code, name=security.name, market=market,
                       security_type=SECURITY_TYPE_LABEL.get(security.kind, security.kind),
                       **_quote_columns(source, trading_days[-1], security.code))
            rows.append(row)

    rows.sort(key=lambda row: (-row["_order"], row["code"]))
    rows = rows[:RANK_LIMIT]
    for row in rows:
        del row["_order"]
        row["rotation_star"] = members is not None and row["code"] in members
    if only_rotation and tab.rotation_checkbox:
        result["only_rotation_applied"] = True
        if members is None:
            result.update(state="missing", total=0)
            result["evidence"]["notes"] = ["勾選只顯示資金輪動，但沒有輪動快照；不回傳未過濾名單。"]
            return result
        rows = [row for row in rows if row["rotation_star"]]

    result["evidence"]["notes"] = list(RANK_NOTES if tab.kind == "rank" else STREAK_NOTES)
    result["missing_count"] = missing
    if not by_market or len(market_missing) == len(by_market):
        state = "missing"
        rows = []
    elif missing or market_missing or result["uncovered_markets"]:
        state = "partial"
    else:
        state = "available"
    pages = max(1, math.ceil(len(rows) / PAGE_SIZE))
    page = min(max(1, page), pages)
    result.update(state=state, total=len(rows), page=page, pages=pages,
                  items=rows[(page - 1) * PAGE_SIZE: page * PAGE_SIZE])
    if market_missing:
        result["evidence"]["notes"].append(f"{'、'.join(market_missing)} 交易日不足，未納入。")
    return result


class OtcSqliteSource:
    """上櫃法人（otc_market.sqlite）與日行情（otc_history.sqlite）的唯讀轉接。"""

    market = "上櫃"

    def __init__(self, market_db: Path | None = None, history_db: Path | None = None) -> None:
        self._market_db = market_db or ROOT / "data/otc-history/otc_market.sqlite"
        self._history_db = history_db or ROOT / "data/otc-history/otc_history.sqlite"
        self._inst_cache: dict[str, dict] = {}

    @staticmethod
    def _connect(path: Path) -> closing:
        return closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True))

    def trading_days(self) -> list[str]:
        with self._connect(self._market_db) as connection:
            return [row[0] for row in connection.execute("SELECT DISTINCT date FROM institutional ORDER BY date")]

    def institutional(self, day: str) -> dict[str, dict]:
        if day not in self._inst_cache:
            with self._connect(self._market_db) as connection:
                self._inst_cache[day] = {
                    code: {"foreign": foreign, "trust": trust}
                    for code, foreign, trust in connection.execute(
                        "SELECT code, foreign_net, trust_net FROM institutional WHERE date = ?", (day,))
                }
        return self._inst_cache[day]

    def quotes(self, day: str) -> dict[str, dict]:
        with self._connect(self._history_db) as connection:
            return {
                code: {"close": close, "change": change, "suspended": bool(suspended)}
                for code, close, change, suspended in connection.execute(
                    "SELECT code, close, change, suspended FROM daily WHERE date = ?", (day,))
            }


def _bias20(closes: Sequence[float]) -> float | None:
    """20 日乖離＝收盤 ÷ 含當日的 20 日簡單均線 − 1。"""
    if len(closes) < 20:
        return None
    mean = sum(closes[-20:]) / 20
    return round(closes[-1] / mean - 1, 5) if mean else None


def load_bias20(codes: Iterable[str], as_of: str) -> dict:
    """唯讀本機資料計算 20 日乖離：個股讀兩市場日 K；加權指數讀既有 MI_5MINS_HIST 月檔，櫃買指數讀 otc_market。"""
    import glob
    import json

    wanted = {str(code) for code in codes}
    stocks: dict[str, float | None] = {}
    for path in (ROOT / "data/stock-history/stock-history.sqlite", ROOT / "data/otc-history/otc_history.sqlite"):
        with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as connection:
            for code in wanted - set(stocks):
                rows = connection.execute(
                    "SELECT close FROM daily WHERE code = ? AND date <= ? AND close IS NOT NULL ORDER BY date DESC LIMIT 20",
                    (code, as_of)).fetchall()
                if rows:
                    stocks[code] = _bias20([r[0] for r in reversed(rows)])
    taiex: dict[str, float] = {}
    for path in sorted(glob.glob(str(ROOT / "data/market-snapshots/*/index-monthly/taiex-*.json"))):
        for row in json.loads(Path(path).read_text(encoding="utf-8"))["data"]["data"]:
            year, month, day = row[0].split("/")
            taiex[f"{int(year) + 1911}-{month}-{day}"] = float(row[4].replace(",", ""))
    taiex_closes = [taiex[d] for d in sorted(taiex) if d <= as_of]
    with closing(sqlite3.connect(f"file:{(ROOT / 'data/otc-history/otc_market.sqlite').as_posix()}?mode=ro",
                                 uri=True)) as connection:
        tpex = [r[0] for r in connection.execute(
            "SELECT close FROM tpex_index WHERE date <= ? AND close IS NOT NULL ORDER BY date DESC LIMIT 20", (as_of,))]
    return {
        "stocks": stocks,
        "indexes": {"上市": _bias20(taiex_closes), "上櫃": _bias20(list(reversed(tpex)))},
        "index_sources": {"上市": "加權指數", "上櫃": "櫃買指數"},
        "data_date": as_of,
    }


def liquid_universe_securities(universe: Mapping, commons: Iterable[Security]) -> list[Security]:
    """把大量股母體（liquid_universe 輸出）轉成排行母體：名單內的普通股，加名單內的 ETF。"""
    members = {str(member["code"]) for member in universe.get("members", ())}
    picked = [security for security in commons if security.code in members]
    seen = {security.code for security in picked}
    for member in universe.get("etf_members", ()):
        code = str(member["code"])
        if code not in seen:
            picked.append(Security(code, member.get("name") or code, member["market"], "etf"))
            seen.add(code)
    return picked


def otc_universe() -> list[Security]:
    """上櫃普通股母體：otc_history 只收 otc_catalog 判定的普通股（kind='common'）。"""
    path = ROOT / "data/otc-history/otc_history.sqlite"
    with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as connection:
        rows = connection.execute(
            "SELECT code, name FROM daily WHERE kind = 'common' AND date = (SELECT MAX(date) FROM daily)").fetchall()
    return [Security(str(code), name, "上櫃") for code, name in rows]
