"""盤勢解讀：一班一份、分類別的解讀，存進 D1 ``market_brief_runs``（本人 2026-11-23「共識達成」，.scratch/market-brief）。

資料約定在 .scratch/market-brief/contract.md（D1 表、payload ``market-brief/v1``、命令列）。
- 時段四個：morning／midday／close 接在那一班首頁消息成功之後（market-news.yml），evening 在每日更新裡（daily-update.yml）。
- 類別四個，payload 裡的順序固定：overseas（國際股市）、news（市場消息）、chips（籌碼面）、tech（技術面）。
  tracking（追蹤事項）不經過這裡（前端自己讀站主的 /api/impact-watch）。要加類別：在 ``CATEGORIES`` 加一列。
- 籌碼面、技術面的事實沿用 ``plain_daily.build_facts``（最近一次大盤資料包；白天是前一交易日）。資料日不是這一班的
  交易日時，事實裡的「今天」換成日期（M/D），那兩類的句子（與引用那些數字的結論）不准寫「今天／今日／本日」。
- 國際股市、市場消息的輸入是 D1 讀出來的一個檔（``--inputs``：排程前一步用 ``inputs-sql`` 印的查詢交給 wrangler --json）。
  國際股市六個指數的漲跌幅由程式用 ``overseas_quotes`` 最後兩根日線算（一位小數），做成事實（代號 ov_…）；
  台股這一班的漲跌：evening 用資料包的收盤，白天用那一班消息存的加權指數（``market_news_runs.indices``）。
  市場消息只用所有人看得到的欄位（總結、影響、影響大的幾則的標題／理由／影響）；不讀 owner_effect、不讀任何人的自選。
- 模型只寫句子：籌碼面、技術面、國際股市與結論的數字一律寫 ``{代號}``，程式核對後代入；市場消息那一類可以照抄
  消息裡出現過的數字與外文名稱，消息裡沒有的不准寫。文字檢查沿用 ``plain_daily._text_problem`` 與
  ``market_news.text_problem``（禁買賣指令、因應要「如果…就…」、不對著讀者說他的股票）。
- 某一類這一班沒有東西（國際股市資料過期或沒抓到、沒有影響大盤的消息）：那一格只有一句（``quiet: true``），不問模型。
- 回答不合格：帶著原因重問一次（``ledger_core.retry_note``）；還是不合格、或模型沒回來 → 程式套句（source ``template``）。

國際股市「過期」的判斷（``_fresh``）：應該有的最近一天＝交易日之前的最後一個平日（週一 → 上週五）。
指數的資料日不早於那一天就算新；比它早時，只有「最後一次抓取成功、而且是在那一天收盤之後抓的」才當成那個市場自己休市
（感恩節、日本連假），沿用最近一天（最多往回 HOLIDAY_GAP_DAYS 天）；否則就是沒抓到，不放進去。

用法：
  python market_brief.py inputs-sql --slot <時段> [--date YYYY-MM-DD] [--analysis <analysis-data.js>]    # 印一句查詢
  python market_brief.py run --slot <時段> --analysis <last-market-pack>/analysis-data.js --sql-output <檔> \
      [--inputs <wrangler --json 的輸出>] [--chips <chips-data.js>] [--date YYYY-MM-DD] [--dry-run] \
      [--agy-bin … --agy-home …] [--report <不公開的 JSON>]
  python market_brief.py usage --report <run 印出來的那一份> --run-id <排程編號> [--agy-bin …] --sql-output <檔>
``--chips`` 不給就讀 ``--analysis`` 同一個資料夾的 chips-data.js。``--inputs`` 不給或讀不懂：國際股市、市場消息各只有一句。
``--dry-run`` 照樣呼叫模型與核對，但 SQL 檔是空的。
印到 stdout 的是遮過的報告（狀態、時段、來源、問了幾次、類別與哪幾類只有一句、模型；沒有模型寫的任何一句），完整的只寫到 ``--report``。
三個子命令一律以 0 結束：這一份寫不成，網站沿用上一班（或原本的解讀面板），不能讓排程失敗。
模型與思考程度沿用設定頁「AI 模型」的 plain 那一項（排程帶 ``AI_PLAIN_MODEL``／``AI_PLAIN_EFFORT``；空的照 gemini_models.PLAIN）。
"""
from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, NamedTuple

import ai_digest
import gemini_models
import ledger_core
import market_news
import overseas_peers
import plain_daily
import public_log

SCHEMA = "market-brief/v1"
TABLE = "market_brief_runs"
TASK = "market-brief"          # Gemini 用量裡的任務名稱；設定頁（web/settings.js 的 TASKS）要對成「盤勢解讀」
SLOTS = ("morning", "midday", "close", "evening")
KEEP_DAYS = 35                 # 比照首頁消息（market_news.KEEP_DAYS）
TAIPEI = timezone(timedelta(hours=8))
MODEL_DEFAULT = gemini_models.PLAIN
EFFORT_DEFAULT = gemini_models.PLAIN_EFFORT
CALL_ATTEMPTS = 2              # 回答被核對退回時，帶著原因再問一次
POINTS_MAX = 2                 # 觀察、因應各最多幾句；多寫的不看
LIMITS = {"one_line": 80, "say": 80, "effect": 100, "conditions": 60, "prepare": 90}
NO_MODEL = "agy_unavailable"   # plain_daily.agy_caller：agy 沒裝好，沒有呼叫到模型（不算一次用量）

# 國際股市：六個指數（overseas_peers.HOME_INDICES 抓的那六個；key 與 cloud/lib/market-indices.js 相同）。
OVERSEAS = (("dji", "DJI.INDX", "道瓊", "us"), ("ixic", "IXIC.INDX", "那斯達克", "us"), ("gspc", "GSPC.INDX", "標普 500", "us"),
            ("sox", "SOX.INDX", "費城半導體", "us"), ("n225", "N225.INDX", "日經 225", "asia"), ("ks11", "KS11.INDX", "韓國綜合", "asia"))
assert {symbol for _, symbol, _, _ in OVERSEAS} == set(overseas_peers.HOME_INDICES)
HOME_NAME = "加權指數"
NEWS_HOME_NAME = "加權"        # market_news._taiex_from_fugle 存在 market_news_runs.indices 裡的名稱
QUOTE_DAYS = 20                # 查詢往回幾天的日線（算漲跌只要最後兩根；連假也夠）
HOLIDAY_GAP_DAYS = 4           # 那個市場自己休市時，最多沿用到幾天前的那一根
NEWS_MIN_IMPACT = 2            # 影響力到這個才算「影響大盤的消息」（market_news.WATCH_MIN_IMPACT 同一條線）
NEWS_TOP = 6                   # 給模型看幾則（影響大的先）
NEWS_ITEM_FIELDS = ("group", "kind", "tone", "impact", "title", "reason", "effect")   # 所有人看得到的欄位；沒有 owner_effect
NEWS_SLOT_ORDER = {"morning": 0, "midday": 1, "close": 2}
QUIET_SAY = {"overseas": "國際股市資料尚未更新", "news": "本時段無影響大盤的新消息"}
TODAY_WORDS = re.compile("今天|今日|本日")
_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_NUMERIC = "0-9０-９" + "".join(sorted(ai_digest.CHINESE_NUMERAL_CHARS))
_NUMBER_RUN = re.compile(f"[{_NUMERIC}][{_NUMERIC}.,%％]*")
_LATIN_WORD = re.compile(r"[A-Za-z][A-Za-z&.\-]+")
GENERIC_LATIN = frozenset({"ai", "etf", "fed", "fomc", "cpi", "gdp", "eps", "pmi"})   # 通用縮寫，不算點名

Problem = tuple[str, str]      # (退回的原因, 被退回的那一句)
ModelAsker = plain_daily.ModelAsker


# ---- 類別 ----

class Category(NamedTuple):
    topics: tuple[str, ...]       # 這一類的句子可以代入哪些主題的事實（事實表的 topic）
    directions: tuple[str, ...]   # 判斷那一句要過哪幾段的方向核對（plain_daily 的段落名稱；overseas 用自己的）
    prompt: str                   # 提示裡這一類的說明
    template: Callable[[Mapping[str, Any]], dict[str, Any]]   # 程式套句：{say, effect, conditions, prepare, tone, phrase}
    dated: bool = False           # 數字來自大盤資料包：資料包不是這一班交易日的時，句子不准寫「今天／今日／本日」
    free_text: bool = False       # 不用代號，數字與外文名稱要在這一班的消息裡出現過（市場消息）


def _overseas_template(bundle: Mapping[str, Any]) -> dict[str, Any]:
    markets = sorted(bundle["markets"], key=lambda row: -row["change_pct"])
    ups, downs = sum(row["change_pct"] > 0 for row in markets), sum(row["change_pct"] < 0 for row in markets)
    tone = (ups > downs) - (ups < downs)
    phrase = "國際股市" + ("全面收高" if ups == len(markets) else "全面收低" if downs == len(markets)
                       else "變動有限" if not ups and not downs else {1: "多數收高", -1: "多數收低"}.get(tone, "漲跌互見"))
    shown = [markets[0], *([markets[-1]] if len(markets) > 1 else [])]
    return {
        "say": f"{phrase}：" + "，".join(f"{{ov_{row['key']}}}" for row in shown) + "。",
        "effect": {1: "國際股市收高，有利台股權值股的開盤氣氛。",
                   -1: "國際股市收低，台股權值股開盤易承壓。"}.get(tone, "國際股市方向分歧，對台股的指引有限。"),
        "conditions": ["美股主要指數是否延續方向", "台股權值股是否跟隨國際股市方向"],
        "prepare": ["如果美股主要指數方向反轉，就先確認台股權值股是否同步反應。"], "tone": tone, "phrase": phrase,
    }


def _news_template(bundle: Mapping[str, Any]) -> dict[str, Any]:
    news = bundle["news"]
    ups, downs = (sum(row.get("tone") == tone for row in news["items"]) for tone in ("up", "down"))
    tone = (ups > downs) - (ups < downs)
    phrase = "市場消息" + {1: "偏多", -1: "偏空"}.get(tone, "多空交錯")

    def quoted(field: str, fallback: str) -> str:
        """消息那一班自己寫的總結／影響（所有人看得到的那一份）：整句放得下、過得了檢查才用，不然用程式的句子。"""
        text = market_news.whole_sentences(news.get(field), LIMITS["say" if field == "summary" else "effect"])
        usable = text and text[-1] in "。！？" and not _free_text_problem(text, field, bundle, 10_000, prepare=False)
        return text if usable else fallback

    return {
        "say": quoted("summary", {1: "市場消息偏多：利多消息多於利空。", -1: "市場消息偏空：利空消息多於利多。"}
                      .get(tone, "市場消息多空交錯，方向尚未明朗。")),
        "effect": quoted("effect", {1: "消息面偏多，有助權值股買氣。", -1: "消息面偏空，權值股易受賣壓影響。"}
                         .get(tone, "消息面多空交錯，對大盤方向的影響有限。")),
        "conditions": ["後續消息是否改變市場方向"],
        "prepare": ["如果後續出現重大政策或財報消息，就先確認對權值股的影響。"], "tone": tone, "phrase": phrase,
    }


def _chips_template(bundle: Mapping[str, Any]) -> dict[str, Any]:
    facts, signals = bundle["facts"], bundle["signals"]
    selling, buying = signals["foreign"] < 0, signals["foreign"] > 0
    net_short, adding = signals["futures_short"], signals["futures_short"] and signals["futures_growing"]
    score = (buying - selling) + (-1 if adding else 0 if net_short else 1)
    tone = (score > 0) - (score < 0)
    phrase = "籌碼面" + {-1: "偏空", 1: "偏多"}.get(tone, "多空分歧")
    spot = "foreign_streak" if "foreign_streak" in facts else "foreign_today"
    turn = "轉為買超" if selling else "轉為賣超" if buying else "出現明顯買賣超"
    prepare = [f"如果外資現貨{turn}，就先確認台指期部位是否同步轉向。"]
    if signals["margin"]:
        prepare.append("如果融資餘額持續增加，就先評估行情轉弱時的槓桿風險。" if signals["margin"] > 0
                       else "如果融資餘額持續減少，就先檢視融資部位的比重。")
    return {
        "say": f"{phrase}{_as_of(bundle, 'chips')}：{{{spot}}}，{{futures_net}}。",
        "effect": {-1: "外資現貨與期貨部位同步偏空，權值股賣壓未解。",
                   1: "外資站在買方，權值股具支撐。"}.get(tone, "外資現貨與期貨部位方向分歧，權值股易隨之震盪。"),
        "conditions": [f"外資現貨是否{turn}",
                       "外資台指期淨空單是否持續增加" if net_short else "外資台指期淨多單是否減少"],
        "prepare": prepare, "tone": tone, "phrase": phrase,
    }


def _tech_template(bundle: Mapping[str, Any]) -> dict[str, Any]:
    facts, signals = bundle["facts"], bundle["signals"]
    below20, below60 = signals.get("below_ma20"), signals.get("below_ma60")
    tone = -1 if below20 and below60 else 1 if below20 is False and below60 is False else 0
    phrase = "技術面" + {-1: "偏弱", 1: "偏強"}.get(tone, "區間整理")
    ratio = signals.get("amount_ratio")
    conditions, prepare = [], []
    if "ma20_line" in facts:
        conditions.append(f"收盤是否{'站回' if below20 else '守住'} {{ma20_line}}")
        prepare.append("如果收盤站回 {ma20_line}，就先確認外資現貨是否同步轉買。" if below20
                       else "如果收盤跌破 {ma20_line}，就先檢視持股的支撐位置。")
    else:
        conditions.append("指數能否延續當日方向")
        prepare.append("如果指數方向反轉，就先檢視持股的支撐位置。")
    if ratio is not None:
        conditions.append("成交量能否維持在均量之上" if ratio >= 1 else "成交量能否回到均量之上")
    return {
        "say": f"{phrase}{_as_of(bundle, 'tech')}：{{{'ma20_side' if 'ma20_side' in facts else 'close'}}}。",
        "effect": {-1: "指數位於均線之下，反彈易遇賣壓。",
                   1: "指數位於均線之上，回檔具支撐。"}.get(tone, "指數位於均線之間，方向尚未表態。"),
        "conditions": conditions, "prepare": prepare, "tone": tone, "phrase": phrase,
    }


def _as_of(bundle: Mapping[str, Any], key: str) -> str:
    """資料日不是這一班的交易日時，套句的判斷帶上日期（「籌碼面偏空（11/23）：…」）。"""
    return "（{price_date}）" if key in bundle.get("stale", ()) else ""


# 順序就是 payload 裡 cats 的順序（contract：國際股市 → 市場消息 → 籌碼面 → 技術面）。
CATEGORIES: dict[str, Category] = {
    "overseas": Category(
        topics=("overseas",), directions=("overseas",),
        prompt="overseas（國際股市）：美股主要指數與日、韓股市最近一個交易日的漲跌（代號 ov_ 開頭，代入後自帶名稱與日期），"
               "ov_home 是加權指數這一班的漲跌（有才用）。判斷國際股市對台股是助力、壓力或中性，點出領漲或領跌的市場。",
        template=_overseas_template),
    "news": Category(
        topics=(), directions=(), free_text=True,
        prompt="news（市場消息）：NEWS 是這一班首頁消息的總結與影響最大的幾則（tone：up 利多、down 利空、flat 中性；impact 越大越重要）。"
               "判斷消息面偏多、偏空或中性，點出最關鍵的事件與它影響的族群。",
        template=_news_template),
    "chips": Category(
        topics=("foreign", "futures", "trust", "margin", "short", "date"), directions=("foreign",), dated=True,
        prompt="chips（籌碼面）：外資現貨買賣超、外資台指期淨部位、投信、融資與融券。判斷籌碼面偏多、偏空或分歧，點出主導的力量。",
        template=_chips_template),
    "tech": Category(
        topics=("position", "volume", "date", "intraday"), directions=("position",), dated=True,
        prompt="tech（技術面）：加權指數收盤與漲跌、相對均線的位置、成交金額。判斷技術面偏強、偏弱或整理，點出關鍵均線。",
        template=_tech_template),
}
SLOT_CATEGORIES: dict[str, tuple[str, ...]] = {slot: tuple(CATEGORIES) for slot in SLOTS}
SLOT_TEXT = {"morning": "盤前（台北 08:00）", "midday": "盤中（台北 11:30）", "close": "收盤後（台北 14:00）",
             "evening": "盤後定稿（台北 18:00，當日籌碼到齊）"}


# ---- D1 讀出來的輸入（國際股市、這一班的消息） ----

def inputs_sql(trade_date: str) -> str:
    """一句查詢（排程用 wrangler --json --command 跑，輸出存成 ``--inputs``）：每一列是 {row_type, data: JSON}。
    只讀大家共用的表與欄位：不碰 market_news_user_items、不選 owner_effect。"""
    q = ledger_core.sql_literal
    symbols = ", ".join(q(symbol) for _, symbol, _, _ in OVERSEAS)
    since = (date.fromisoformat(trade_date) - timedelta(days=QUOTE_DAYS)).isoformat()
    day = q(trade_date)
    return " UNION ALL ".join([
        "SELECT 'quote' AS row_type, json_object('symbol', symbol, 'date', date, 'close', close) AS data FROM overseas_quotes "
        f"WHERE symbol IN ({symbols}) AND date >= {q(since)} AND date <= {day}",
        "SELECT 'status', json_object('symbol', symbol, 'ok', ok, 'succeeded_at', succeeded_at) FROM overseas_fetch_status "
        f"WHERE symbol IN ({symbols})",
        "SELECT 'run', json_object('run_id', run_id, 'trade_date', trade_date, 'slot', slot, 'status', status, 'summary', summary, "
        f"'effect', effect, 'indices', indices) FROM market_news_runs WHERE trade_date = {day}",
        "SELECT 'item', data FROM (SELECT json_object('run_id', run_id, 'group', grp, 'kind', kind, 'tone', tone, 'impact', impact, "
        f"'title', title, 'reason', reason, 'effect', effect) AS data FROM market_news_items WHERE impact >= {NEWS_MIN_IMPACT} "
        f"AND run_id IN (SELECT run_id FROM market_news_runs WHERE trade_date = {day}) ORDER BY impact DESC, published_at DESC LIMIT 90)",
    ]) + ";"


def read_inputs(path: Path | str) -> dict[str, list[dict[str, Any]]] | None:
    """wrangler d1 execute --json 的輸出（[{results: [{row_type, data}]}]）→ {quote, status, run, item: [列]}；讀不懂就是 None。"""
    try:
        text = Path(path).read_text(encoding="utf-8")
        payload = json.loads(text[text.index("["):])
    except (OSError, ValueError):
        return None
    out: dict[str, list[dict[str, Any]]] = {"quote": [], "status": [], "run": [], "item": []}
    for container in payload if isinstance(payload, list) else []:
        for row in (container.get("results") or []) if isinstance(container, Mapping) else []:
            data = row.get("data") if isinstance(row, Mapping) else None
            try:
                data = json.loads(data) if isinstance(data, str) else data
            except ValueError:
                continue
            if isinstance(data, Mapping) and row.get("row_type") in out:
                out[row["row_type"]].append(dict(data))
    return out


def _is_day(value: Any) -> bool:
    if not isinstance(value, str) or not _ISO_DAY.match(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or abs(value) == float("inf"):
        return None
    return float(value)


def _expected_day(trade_date: str) -> date:
    """這一班應該拿得到的最近一個海外交易日：交易日之前的最後一個平日（美股前一晚收盤；日韓當天還沒收）。"""
    day = date.fromisoformat(trade_date) - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def _fresh(day: str, zone: str, status: Mapping[str, Any] | None, expected: date) -> bool:
    """這個指數的最後一根（day）夠不夠新。規則在檔頭。"""
    if day >= expected.isoformat():
        return True
    if date.fromisoformat(day) < expected - timedelta(days=HOLIDAY_GAP_DAYS) or not status or not status.get("ok"):
        return False
    try:
        fetched = datetime.fromisoformat(str(status.get("succeeded_at")).replace("Z", "+00:00"))
    except ValueError:
        return False
    fetched = fetched if fetched.tzinfo else fetched.replace(tzinfo=timezone.utc)
    # 「應該有的那一天」的資料什麼時候抓得到：美股收盤是台北隔天清晨（留到 05:30），日韓是台北當天下午（留到 16:00）
    closed = datetime(expected.year, expected.month, expected.day, tzinfo=TAIPEI) + (
        timedelta(days=1, hours=5, minutes=30) if zone == "us" else timedelta(hours=16))
    return fetched >= closed


def overseas_markets(inputs: Mapping[str, Any] | None, trade_date: str) -> list[dict[str, Any]]:
    """六個指數各自最後一根日線對前一根的漲跌（%，一位小數）與那一根的日期；過期或不到兩根的不列。順序照 OVERSEAS。"""
    if not inputs:
        return []
    expected = _expected_day(trade_date)
    statuses = {row.get("symbol"): row for row in inputs.get("status") or []}
    out = []
    for key, symbol, name, zone in OVERSEAS:
        closes = {row["date"]: _number(row.get("close")) for row in inputs.get("quote") or []
                  if row.get("symbol") == symbol and _is_day(row.get("date")) and row["date"] <= trade_date}
        series = sorted((day, close) for day, close in closes.items() if close and close > 0)
        if len(series) < 2 or not _fresh(series[-1][0], zone, statuses.get(symbol), expected):
            continue
        out.append({"key": key, "name": name, "change_pct": round((series[-1][1] / series[-2][1] - 1) * 100, 1),
                    "date": series[-1][0]})
    return out


def _news_run(inputs: Mapping[str, Any] | None, slot: str, trade_date: str) -> dict[str, Any] | None:
    """同一交易日、不晚於這一班的最近一班首頁消息（evening：當天最後一班）。"""
    limit = NEWS_SLOT_ORDER.get(slot, max(NEWS_SLOT_ORDER.values()))
    runs = [row for row in (inputs or {}).get("run") or []
            if row.get("trade_date") == trade_date and NEWS_SLOT_ORDER.get(row.get("slot"), 99) <= limit]
    return max(runs, key=lambda row: NEWS_SLOT_ORDER[row["slot"]]) if runs else None


def _clean(value: Any, limit: int = 400) -> str | None:
    text = re.sub(r"\s+", " ", value).strip()[:limit] if isinstance(value, str) else ""
    return text or None


def _news(inputs: Mapping[str, Any] | None, run: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """那一班的總結與影響最大的幾則（只留 NEWS_ITEM_FIELDS）；那一班沒寫成、或沒有影響大盤的消息 → None。"""
    if not run or run.get("status") != "ok":
        return None
    rows = [row for row in (inputs or {}).get("item") or []
            if row.get("run_id") == run.get("run_id") and isinstance(row.get("impact"), int) and row["impact"] >= NEWS_MIN_IMPACT
            and _clean(row.get("title"))]
    rows = sorted(rows, key=lambda row: -row["impact"])[:NEWS_TOP]   # 穩定排序：同樣大的照查詢的順序（新的先）
    if not rows:
        return None
    items = [{field: value for field in NEWS_ITEM_FIELDS
              if (value := row["impact"] if field == "impact" else _clean(row.get(field)))} for row in rows]
    return {"slot": run.get("slot"), "summary": _clean(run.get("summary"), 2000), "effect": _clean(run.get("effect"), 2000), "items": items}


def _home(slot: str, trade_date: str, analysis: Mapping[str, Any], run: Mapping[str, Any] | None, pack_day: str) -> dict[str, Any] | None:
    """台股這一班的漲跌：evening 用資料包最後兩根收盤；白天用那一班消息存的加權指數（盤前只收今天以前的那一根）。"""
    if slot == "evening":
        closes = [float(row["close"]) for row in analysis["daily"] if isinstance(row.get("close"), (int, float))]
        return {"name": HOME_NAME, "change_pct": round((closes[-1] / closes[-2] - 1) * 100, 1), "date": pack_day}
    try:
        rows = json.loads(run.get("indices") or "[]") if isinstance((run or {}).get("indices"), str) else (run or {}).get("indices")
    except ValueError:
        rows = None
    for row in rows if isinstance(rows, list) else []:
        change = _number(row.get("change_pct")) if isinstance(row, Mapping) else None
        if change is None or row.get("name") != NEWS_HOME_NAME or not _is_day(row.get("date")):
            continue
        if pack_day <= row["date"] <= trade_date and not (slot == "morning" and row["date"] == trade_date):
            return {"name": HOME_NAME, "change_pct": round(change, 1), "date": row["date"]}
    return None


def _move(change: float) -> str:
    return f"上漲 {abs(change):.1f}%" if change > 0 else f"下跌 {abs(change):.1f}%" if change < 0 else "持平"


def _fact(topic: str, text: str) -> dict[str, str]:
    return {"topic": topic, "short": text, "text": text}


def extend(base: Mapping[str, Any], slot: str, trade_date: str, analysis: Mapping[str, Any],
           inputs: Mapping[str, Any] | None) -> dict[str, Any]:
    """plain_daily 的事實表 → 這一班四類共用的那一份：補上國際股市的事實、這一班的消息、每一類的資料日與哪幾類不是今天的資料。"""
    dates = base["data_dates"]
    pack_day = dates["price_and_foreign"]
    facts = {key: dict(fact) for key, fact in base["facts"].items()}
    signals = dict(base["signals"])
    if pack_day != trade_date:   # 白天的班：資料包是前一交易日的，事實裡的「今天」換成日期；指數今天的方向不拿那一天的核對
        md = plain_daily._md(pack_day)
        for fact in facts.values():
            for form in ("short", "text"):
                fact[form] = re.sub(r"\s+", " ", fact[form].replace("今天", f" {md} ")).strip()
        signals["index"] = 0
    run = _news_run(inputs, slot, trade_date)
    news = _news(inputs, run)
    markets = overseas_markets(inputs, trade_date)
    home = _home(slot, trade_date, analysis, run, pack_day)
    intraday = home if home and slot != "evening" and home["date"] == trade_date else None
    for row in markets:
        gap = " " if row["name"][-1].isdigit() else ""
        facts[f"ov_{row['key']}"] = _fact("overseas", f"{row['name']}{gap}{_move(row['change_pct'])}（{plain_daily._md(row['date'])}）")
    if home:
        text = f"{HOME_NAME} {plain_daily._md(home['date'])} {'盤中' if intraday and slot == 'midday' else ''}{_move(home['change_pct'])}"
        if markets:
            facts["ov_home"] = _fact("overseas", text)
        if intraday:
            facts["intraday"] = _fact("intraday", text)
    cat_dates = {"chips": dates["foreign"], "tech": pack_day, "news": trade_date,
                 "overseas": max((row["date"] for row in markets), default=None)}
    signs = {(row["change_pct"] > 0) - (row["change_pct"] < 0) for row in markets}
    banned = {"overseas": {frozenset({1}): ("美股收低", "美股收黑", "美股下跌", "美股走低", "國際股市收低", "國際股市下跌", "全面收低", "全面下跌"),
                           frozenset({-1}): ("美股收高", "美股收紅", "美股上漲", "美股走高", "國際股市收高", "國際股市上漲", "全面收高", "全面上漲"),
                           }.get(frozenset(signs), ())}
    corpus = "".join(str(text) for text in ([news["summary"], news["effect"]] + [value for item in news["items"] for value in item.values()]
                                            if news else []) if text)
    return {"data_dates": dates, "facts": facts, "signals": signals, "markets": markets, "home": home, "intraday": intraday,
            "news": news, "news_run": run.get("slot") if run else None, "corpus": re.sub(r"\s+", "", corpus), "banned": banned,
            "cat_dates": cat_dates, "pack_md": plain_daily._md(pack_day),
            # 資料包不是這一班交易日的（白天的班）：籌碼面、技術面都算「不是今天的資料」
            "stale": {key for key, spec in CATEGORIES.items() if spec.dated} if pack_day != trade_date else set(),
            "quiet": {"overseas": not markets, "news": news is None}}


# ---- 核對 ----

def _free_text_problem(text: Any, label: str, bundle: Mapping[str, Any], limit: int, *, prepare: bool) -> str | None:
    """市場消息那一類的一句：不用代號；買賣指令、「如果…就…」、不對著讀者說話照首頁消息的規則；
    數字（阿拉伯與中文）與外文名稱要在這一班給模型看的消息裡出現過。"""
    if not isinstance(text, str) or not text.strip():
        return f"{label}：文字為空"
    if len(text) > limit:
        return f"{label}：文字太長"
    if "{" in text or "}" in text:
        return f"{label}：市場消息這一類不用代號"
    reason = market_news.text_problem("prepare" if prepare else "effect", text)
    if reason:
        return f"{label}：{reason}"
    corpus = bundle.get("corpus") or ""
    hit = next((term for term in ai_digest.BANNED_TEXT if term.lower() in text.lower() and term.lower() not in corpus.lower()), None)
    if hit:
        return f"{label}：包含正式頁禁止字樣"
    bare = text
    for word in plain_daily.YI_WORDS:
        bare = bare.replace(word, "")
    for token in _NUMBER_RUN.findall(re.sub(r"\s+", "", bare)):
        if token.rstrip(".,") not in corpus:
            return f"{label}：數字「{token}」不在這一班的消息裡"
    for word in _LATIN_WORD.findall(text):
        if word.lower() not in GENERIC_LATIN and word.lower() not in corpus.lower():
            return f"{label}：「{word}」不在這一班的消息裡"
    return None


def _sentence_problem(text: Any, label: str, bundle: Mapping[str, Any], limit: int, *, key: str | None = None,
                      prepare: bool = False, directions: Sequence[str] = ()) -> str | None:
    """模型（或程式套句）寫的一句能不能上站。label 是寫進原因的位置（例如 chips.say）；key 是哪一類（結論是 None）。"""
    spec = CATEGORIES[key] if key else None
    if spec and spec.free_text:
        return _free_text_problem(text, label, bundle, limit, prepare=prepare)
    reason = plain_daily._text_problem(text, label, bundle, limit, prepare=prepare)
    if reason:
        return reason
    used = [bundle["facts"][name]["topic"] for name in plain_daily.PLACEHOLDER.findall(text)]
    if spec and any(topic not in spec.topics for topic in used):
        return f"{label}：代號不屬於這一類的主題"
    prose = plain_daily.PLACEHOLDER.sub("", text)
    for sections, terms in plain_daily._contradictions(bundle["signals"]) if directions else ():
        if sections.intersection(directions):
            hit = next((term for term in terms if term in prose), None)
            if hit:
                return f"{label}：「{hit}」和當天數字的方向矛盾"
    hit = next((term for term in bundle["banned"].get(key, ()) if term in prose), None) if directions else None
    if hit:
        return f"{label}：「{hit}」和國際股市的漲跌方向矛盾"
    # 白天的班：籌碼與位置是前一交易日的。那兩類的句子、引用那些數字的結論不准說成今天
    stale_topics = {topic for name in bundle["stale"] for topic in CATEGORIES[name].topics} - {"date", "intraday"}
    if (key in bundle["stale"] or (key is None and stale_topics.intersection(used))) \
            and TODAY_WORDS.search(plain_daily._render(text, bundle, form="text")):
        return f"{label}：這些數字是 {bundle['pack_md']} 的資料，不能寫「今天」「今日」「本日」，要交代日期請寫 {{price_date}}"
    return None


def validate(output: Any, bundle: Mapping[str, Any], keys: Sequence[str]) -> tuple[dict[str, Any] | None, list[Problem]]:
    """模型的回答 → ({one_line, lead, cats:{key:{say, effect, conditions, prepare}}}（數字已代入）, 退回的原因)。
    任何一句不合格就整份退回（重問或改用程式套句）；觀察、因應多寫的只取前 POINTS_MAX 句；lead 不在這一班的類別裡就用第一個。"""
    if isinstance(output, str):
        try:
            output = json.loads(output)
        except json.JSONDecodeError:
            output = None
    if not isinstance(output, Mapping):
        return None, [("回答不是物件", "")]
    problems: list[Problem] = []

    def passed(text: Any, label: str, field: str, **check: Any) -> str:
        reason = _sentence_problem(text, label, bundle, LIMITS[field], **check)
        if reason:
            problems.append((reason, text[:200] if isinstance(text, str) else ""))
            return ""
        return plain_daily._render(text, bundle, form="text")

    def listed(value: Any, label: str, field: str, **check: Any) -> list[str]:
        items = [value] if isinstance(value, str) else value if isinstance(value, (list, tuple)) else []
        return [passed(item, label, field, **check) for item in [item for item in items if item not in (None, "")][:POINTS_MAX]]

    body: dict[str, Any] = {"one_line": passed(output.get("one_line"), "one_line", "one_line", directions=("one_line",)), "cats": {}}
    rows = output.get("cats")
    by_key = {row.get("key"): row for row in (rows if isinstance(rows, (list, tuple)) else []) if isinstance(row, Mapping)}
    for key in keys:
        row, spec = by_key.get(key), CATEGORIES[key]
        if row is None:
            problems.append((f"cats 少了 {key}", ""))
            continue
        body["cats"][key] = {
            "say": passed(row.get("say"), f"{key}.say", "say", key=key, directions=spec.directions),
            "effect": passed(row.get("effect"), f"{key}.effect", "effect", key=key),
            "conditions": listed(row.get("conditions"), f"{key}.conditions", "conditions", key=key),
            "prepare": listed(row.get("prepare"), f"{key}.prepare", "prepare", key=key, prepare=True),
        }
    if problems:
        return None, problems
    body["lead"] = output.get("lead") if output.get("lead") in keys else keys[0]
    return body, []


# ---- 程式套句 ----

def template_body(bundle: Mapping[str, Any], keys: Sequence[str]) -> dict[str, Any]:
    """模型沒寫成時用的那一份：同一張事實表、同一個核對器。套句自己沒過核對＝程式錯，丟例外。"""
    drafts = {key: CATEGORIES[key].template(bundle) for key in keys}
    total = sum(draft["tone"] for draft in drafts.values())
    head = "盤勢偏空" if total < 0 else "盤勢偏多" if total > 0 else "多空分歧"
    raw = {"one_line": f"{head}：" + "、".join(drafts[key]["phrase"] for key in keys) + "。",
           "lead": max(keys, key=lambda key: abs(drafts[key]["tone"])),   # 一樣大時取排在前面的
           "cats": [{"key": key, **{field: drafts[key][field] for field in ("say", "effect", "conditions", "prepare")}}
                    for key in keys]}
    body, problems = validate(raw, bundle, keys)
    if body is None:
        raise RuntimeError("程式套句沒通過核對：" + "；".join(reason for reason, _ in problems))
    return body


# ---- 模型 ----

def output_schema(keys: Sequence[str]) -> dict[str, Any]:
    line = lambda field: {"type": "string", "maxLength": LIMITS[field]}   # noqa: E731
    points = lambda field: {"type": "array", "maxItems": POINTS_MAX, "items": line(field)}   # noqa: E731
    return {"type": "object", "required": ["one_line", "lead", "cats"], "properties": {
        "one_line": line("one_line"), "lead": {"type": "string", "enum": list(keys)},
        "cats": {"type": "array", "minItems": len(keys), "maxItems": len(keys), "items": {
            "type": "object", "required": ["key", "say", "effect", "conditions", "prepare"], "properties": {
                "key": {"type": "string", "enum": list(keys)}, "say": line("say"), "effect": line("effect"),
                "conditions": points("conditions"), "prepare": points("prepare")}}}}}


def build_prompt(slot: str, bundle: Mapping[str, Any], keys: Sequence[str]) -> str:
    topics = {topic for key in keys for topic in CATEGORIES[key].topics}
    facts = {key: {"topic": fact["topic"], "代入成": fact["text"]} for key, fact in bundle["facts"].items() if fact["topic"] in topics}
    coded = [key for key in keys if not CATEGORIES[key].free_text]
    allowed = "；".join(f"{key} 只能用 topic 為 {'、'.join(CATEGORIES[key].topics)} 的代號" for key in coded)
    news = bundle.get("news") if "news" in keys else None
    stale = [key for key in keys if key in bundle["stale"]]
    count = "兩" if POINTS_MAX == 2 else POINTS_MAX
    rules = [
        f"你是台股研究部的盤勢分析師，撰寫「盤勢解讀」{SLOT_TEXT[slot]}這一班。只能根據 FACTS{'與 NEWS' if news else ''}，"
        "不可連網、不可使用工具，不可加入其中沒有的事實（沒列出的國際股市、新聞、個股、傳聞都不寫）。",
        "用字專業、精簡，採研究報告語氣（國際股市、市場消息、籌碼面、技術面、外資、淨空單、均線、量能、權值股），繁體中文，不寫贅詞與口語。",
        f"{'除了 news 那一類，' if news else ''}所有文字一律不得出現阿拉伯數字、全形數字、中文數字（一、兩、十、百、千、萬、億、兆等）、"
        "日期、股票代號或百分比。要提到數字或日期時寫 {代號}，程式會換成核對過的句子（FACTS 的「代入成」）；代入後的句子自帶主詞"
        "（外資、加權指數、各國指數…），代號前後不要重複主詞，換進去後要讀得通順。只能用 FACTS 裡有的代號。",
        f"只有這些含「一」的詞可以用：{'、'.join(plain_daily.YI_WORDS)}；其他含中文數字的詞（例如「不一」「兩者」「十分」）請改寫。",
        "cats 每一類各一筆（key 照抄）：" + " ".join(CATEGORIES[key].prompt for key in keys),
        f"各類可用的代號：{allowed}。",
    ]
    if news:
        rules.append("news 那一類不用代號：只根據 NEWS 改寫。NEWS 裡出現過的數字、公司與機構名稱可以照抄，NEWS 沒有的數字與名稱一律不寫；"
                     "不寫「你」「您」「自選」這類對著讀者說的話。one_line 要帶到消息時只寫事件，不寫數字。")
    if stale:
        rules.append(f"FACTS 裡 {'、'.join(stale)} 的數字是 {bundle['pack_md']} 收盤後的資料，不是今天的：這幾類的句子，以及引用這些數字的 one_line，"
                     "不可寫「今天」「今日」「本日」；要交代是哪一天時寫 {price_date}（代入成日期，例：{price_date} 外資現貨賣超）。")
    if "intraday" in facts:
        rules.append("{intraday} 是這一班抓到的加權指數即時漲跌（自帶日期），tech 可以引用，並和前一交易日的位置分開講。")
    rules += [
        "每一類四個欄位：say 一句判斷（方向與關鍵數字）；effect 一句影響（影響到誰、多大）；"
        f"conditions 觀察——接下來要觀察的條件，一到{count}句，寫「什麼是否發生」；"
        f"prepare 因應，一到{count}句，每句用「如果…就…」寫情境與要先確認、檢視或評估的事。",
        "one_line 是整體一句結論，綜合各類的方向。lead 填這一班影響最大的那一類的 key。",
        "整份不得有買賣指令或個人化投資建議：不要出現「建議買進」「可以加碼」「應該減碼」「逢低布局」「停損出場」這類字眼；"
        "prepare 的「就」後面只能是確認、檢視、評估，不能是買賣動作。",
        "方向不可和 FACTS 相反（例如外資賣超時不可寫外資買超、淨空單增加時不可寫空單減少、各國指數全面收高時不可寫美股收低）。"
        "原因分不出來的不要說死。",
        "不寫模型、提示詞、方法、參考站、會員等字樣。",
    ]
    lines = [*rules, "DATA_DATES:", json.dumps({key: bundle["cat_dates"][key] for key in keys}, ensure_ascii=False),
             "FACTS:", json.dumps(facts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))]
    if news:
        shown = {"summary": news["summary"], "effect": news["effect"], "items": news["items"]}
        lines += ["NEWS:", json.dumps({key: value for key, value in shown.items() if value}, ensure_ascii=False, separators=(",", ":"))]
    return "\n".join(lines)


def _retry_note(problem: Problem) -> str:
    reason, text = problem
    return (ledger_core.retry_note(reason, text or None)
            + "要提數字一律寫 {代號}（news 那一類只能照抄 NEWS 裡有的）；不得有買賣指令；prepare 每一句用「如果…就…」。")


# ---- 一班 ----

def run(slot: str, analysis: Mapping[str, Any], chips: Mapping[str, Any], *, ask_model: ModelAsker,
        model: str = MODEL_DEFAULT, effort: str = EFFORT_DEFAULT, now: datetime | None = None,
        trade_date: str | None = None, dry_run: bool = False, inputs: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """產生一班。回傳完整的報告：status ok 時帶 payload 與 sql（乾跑時 sql 是空字串）。不丟例外。
    inputs：read_inputs 讀到的 D1 資料（國際股市日線、這一班的消息）；None 時那兩類各只有一句。"""
    now = now or datetime.now(timezone.utc)
    order = SLOT_CATEGORIES[slot]
    report: dict[str, Any] = {"status": "failed", "slot": slot, "trade_date": trade_date, "source": None, "attempts": 0,
                              "categories": list(order), "model": model, "effort": effort, "model_called": False,
                              "failure": None, "rejected": [], "dry_run": dry_run}
    try:
        base = plain_daily.build_facts(analysis, chips)
        dates = base["data_dates"]
        # 18:00 那一班的交易日就是剛發布的資料日；白天的班是當天（台北）。
        trade_date = trade_date or (dates["price_and_foreign"] if slot == "evening" else now.astimezone(TAIPEI).date().isoformat())
        date.fromisoformat(trade_date)
        try:
            bundle = extend(base, slot, trade_date, analysis, inputs)
        except Exception as error:  # noqa: BLE001 — D1 讀到的東西不對：國際股市、消息各只留一句，籌碼與技術照寫
            report["inputs_failure"] = type(error).__name__
            bundle = extend(base, slot, trade_date, analysis, None)
        keys = tuple(key for key in order if not bundle["quiet"].get(key))
        template = template_body(bundle, keys)
    except Exception as error:  # noqa: BLE001 — 事實算不出來或套句有錯：這一班不寫，網站沿用上一班
        report["failure"] = f"facts_failed: {type(error).__name__}: {str(error)[:200]}"
        return report
    report.update({"trade_date": trade_date, "data_dates": dates, "quiet": {key: key not in keys for key in order},
                   "inputs": {"read": inputs is not None, "markets": [row["key"] for row in bundle["markets"]],
                              "home": bundle["home"] is not None, "news_run": bundle["news_run"],
                              "news_items": len(bundle["news"]["items"]) if bundle["news"] else 0}})
    prompt, schema, suffix, body = build_prompt(slot, bundle, keys), output_schema(keys), "", None
    for attempt in range(1, CALL_ATTEMPTS + 1):
        report["attempts"] = attempt
        try:
            status, structured, detail = ask_model(prompt + suffix, schema)
        except Exception as error:  # noqa: BLE001
            status, structured, detail = "model_failed", None, f"{type(error).__name__}: {str(error)[:200]}"
        report["model_called"] = report["model_called"] or status != NO_MODEL
        if status != "ok":   # 模型沒回來不重試（額度與時間）
            report.update({"failure": status, "detail": str(detail)[:300]})
            break
        body, problems = validate(structured, bundle, keys)
        if body is not None:
            report["failure"] = None
            break
        report["failure"] = "validation_failed"
        report["rejected"].append({"attempt": attempt, "problems": [{"reason": reason, "text": text} for reason, text in problems]})
        suffix = _retry_note(problems[0])
    source = "ai" if body is not None else "template"
    body = body or template
    for key in keys:   # 模型沒寫觀察或因應的那一類，用程式套句的補上（畫面三段都有內容）
        for field in ("conditions", "prepare"):
            body["cats"][key][field] = body["cats"][key][field] or template["cats"][key][field]
    payload = {
        "schema": SCHEMA, "trade_date": trade_date, "slot": slot,
        "generated_at": now.astimezone(TAIPEI).isoformat(timespec="seconds"), "source": source,
        "one_line": body["one_line"], "lead": body["lead"],
        "cats": [_cat(key, body["cats"].get(key), bundle) for key in order],
    }
    report.update({"status": "ok", "source": source, "lead": payload["lead"], "payload": payload,
                   "sql": "" if dry_run else run_sql(payload, model=model if source == "ai" else None)})
    return report


def _cat(key: str, written: Mapping[str, Any] | None, bundle: Mapping[str, Any]) -> dict[str, Any]:
    """payload 裡的一類。沒有東西的那一類只有一句；圖用的數字（國際股市的 markets／home、技術面的 intraday）由程式放。"""
    if written is None:
        return {"key": key, "say": QUIET_SAY[key], "quiet": True}
    cat = {"key": key, **written, "data_date": bundle["cat_dates"][key], "quiet": False}
    if key == "overseas":
        cat["markets"] = [dict(row) for row in bundle["markets"]]
        if bundle["home"]:
            cat["home"] = dict(bundle["home"])
    if key == "tech" and bundle["intraday"]:
        cat["intraday"] = {"change_pct": bundle["intraday"]["change_pct"], "date": bundle["intraday"]["date"]}
    return cat


def run_sql(payload: Mapping[str, Any], *, model: str | None) -> str:
    """同一個（交易日, 時段）重跑就蓋掉；超過 KEEP_DAYS 天的清掉。不寫 BEGIN／COMMIT（D1 不收明確的交易）。"""
    q = ledger_core.sql_literal
    cutoff = (date.fromisoformat(payload["trade_date"]) - timedelta(days=KEEP_DAYS)).isoformat()
    values = ", ".join(q(value) for value in (payload["trade_date"], payload["slot"], payload["generated_at"], payload["source"],
                                              model, json.dumps(payload, ensure_ascii=False, separators=(",", ":"))))
    return (f"INSERT INTO {TABLE} (trade_date, slot, generated_at, source, model, payload) VALUES ({values}) "
            "ON CONFLICT (trade_date, slot) DO UPDATE SET generated_at = excluded.generated_at, source = excluded.source, "
            "model = excluded.model, payload = excluded.payload;\n"
            f"DELETE FROM {TABLE} WHERE trade_date < {q(cutoff)};\n")


def public_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """可以印到公開的排程輸出的那一份：沒有 payload、SQL、被退回的句子；模型失敗的說明只留字數。"""
    out = {key: value for key, value in report.items() if key not in ("payload", "sql", "rejected")}
    out["rejected"] = sum(len(entry.get("problems") or []) for entry in report.get("rejected") or [])
    return public_log.text_to_length(out, ("detail",))


# ---- 命令列 ----

def usage_sql(report: Any, buckets: list[dict[str, Any]] | None, *, run_id: str, taken_at: str) -> str:
    """這一班的 Gemini 用量：有呼叫到模型就一筆（重問那一次不另外記）；沒呼叫、乾跑、報告讀不懂都是空的。
    成功＝模型寫的那一份通過核對；改用程式套句算失敗。"""
    import gemini_usage
    if not isinstance(report, Mapping) or report.get("dry_run") or not report.get("model_called"):
        return ""
    return gemini_usage.snapshot_sql(buckets, task=TASK, run_id=run_id, taken_at=taken_at, model=report.get("model"),
                                     call={"effort": report.get("effort"), "dry_run": False,
                                           "outcome": "ok" if report.get("source") == "ai" else "failed"})


def _usage(args: Any) -> int:
    import gemini_usage
    try:
        lines = [line for line in Path(args.report).read_text(encoding="utf-8").splitlines() if line.strip().startswith("{")]
        report = json.loads(lines[-1])   # 報告是那一步輸出的最後一行
    except (OSError, ValueError, IndexError):
        report = None
    buckets = None
    if isinstance(report, Mapping) and report.get("model_called") and not report.get("dry_run"):
        try:
            buckets = gemini_usage.read_usage(args.agy_bin) if args.agy_bin else None
        except gemini_usage.UsageUnavailable as error:
            print(f"::warning::讀不到 Gemini 用量（{error}）；只記下這次任務")
    script = usage_sql(report, buckets, run_id=args.run_id, taken_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    Path(args.sql_output).write_text(script, encoding="utf-8", newline="\n")
    print(f"盤勢解讀這一班記 {script.count('INSERT INTO')} 筆用量")
    return 0


def _inputs_sql(args: Any, now: datetime | None) -> int:
    """印出讀 D1 的那一句查詢。交易日和 run 同一個算法：給了 --date 用它；evening 用資料包的資料日；其他是台北今天。"""
    import impact_watch
    day = args.date
    if not day and args.slot == "evening":
        daily = impact_watch.read_assignment(Path(args.analysis))["daily"]
        day = [row for row in daily if isinstance(row.get("close"), (int, float))][-1]["date"]
    day = day or (now or datetime.now(timezone.utc)).astimezone(TAIPEI).date().isoformat()
    if not _is_day(day):
        raise ValueError("交易日不是 YYYY-MM-DD")
    print(inputs_sql(day))
    return 0


def _run(args: Any, ask_model: ModelAsker | None, now: datetime | None) -> dict[str, Any]:
    import os

    import impact_watch
    model = os.environ.get("AI_PLAIN_MODEL") or MODEL_DEFAULT
    effort = os.environ.get("AI_PLAIN_EFFORT") or EFFORT_DEFAULT
    base = {"status": "failed", "slot": args.slot, "model": model, "effort": effort, "model_called": False, "dry_run": args.dry_run}
    try:
        analysis_path = Path(args.analysis)
        analysis = impact_watch.read_assignment(analysis_path)
        chips = impact_watch.read_assignment(Path(args.chips) if args.chips else analysis_path.with_name("chips-data.js"))
    except Exception as error:  # noqa: BLE001 — 資料包讀不到：這一班不寫
        return {**base, "failure": f"input_failed: {type(error).__name__}"}
    ask = ask_model or plain_daily.agy_caller(args.agy_bin or None, home=args.agy_home or None, model=model, effort=effort)
    return run(args.slot, analysis, chips, ask_model=ask, model=model, effort=effort, now=now, trade_date=args.date,
               dry_run=args.dry_run, inputs=read_inputs(args.inputs) if args.inputs else None)


def main(argv: Sequence[str] | None = None, *, ask_model: ModelAsker | None = None, now: datetime | None = None) -> int:
    """ask_model、now 只給測試換掉模型與時間。"""
    import argparse
    parser = argparse.ArgumentParser(description="盤勢解讀：一班")
    parser.add_argument("command", choices=["run", "usage", "inputs-sql"])
    parser.add_argument("--slot", choices=SLOTS, help="run／inputs-sql：哪一班")
    parser.add_argument("--analysis", help="run：大盤資料包的 analysis-data.js（inputs-sql 的 evening 用它決定交易日）")
    parser.add_argument("--chips", help="run：chips-data.js；不給就讀 --analysis 同一個資料夾的")
    parser.add_argument("--inputs", help="run：wrangler --json 跑 inputs-sql 那一句的輸出（國際股市日線、這一班的消息）")
    parser.add_argument("--date", help="run／inputs-sql：台北交易日；不給時 evening 用資料日、其他時段用今天")
    parser.add_argument("--sql-output", help="寫進 D1 的 SQL（run：這一份；usage：用量）")
    parser.add_argument("--dry-run", action="store_true", help="run：照樣呼叫模型與核對，但 SQL 檔是空的")
    parser.add_argument("--agy-bin")
    parser.add_argument("--agy-home")
    parser.add_argument("--report", help="run：完整的報告（不公開的地方）；usage：run 印出來的那一份")
    parser.add_argument("--run-id", help="usage：這一次排程的編號")
    args = parser.parse_args(argv)
    if args.command == "inputs-sql":
        if not args.slot or (args.slot == "evening" and not (args.date or args.analysis)):
            parser.error("inputs-sql 要 --slot；evening 還要 --date 或 --analysis")
        try:
            return _inputs_sql(args, now)
        except Exception as error:  # noqa: BLE001 — 印不出查詢：下一步讀不到，那兩類各只有一句
            print(f"::warning::盤勢解讀的查詢組不出來（{type(error).__name__}）", file=sys.stderr)
            return 0
    if args.command == "usage":
        if not (args.report and args.run_id and args.sql_output):
            parser.error("usage 要 --report、--run-id、--sql-output")
        try:
            return _usage(args)
        except Exception as error:  # noqa: BLE001 — 用量只是附帶資訊
            print(f"::warning::盤勢解讀的用量沒記（{type(error).__name__}）")
            return 0
    if not (args.slot and args.analysis and args.sql_output):
        parser.error("run 要 --slot、--analysis、--sql-output")
    try:
        report = _run(args, ask_model, now)
        Path(args.sql_output).write_text(report.get("sql") or "", encoding="utf-8", newline="\n")
    except Exception as error:  # noqa: BLE001 — 這一份寫不成不能讓排程失敗
        report = {"status": "failed", "slot": args.slot, "failure": f"{type(error).__name__}", "model_called": False,
                  "dry_run": args.dry_run}
    public_log.write_private(args.report, {key: value for key, value in report.items() if key != "sql"})
    # 倉庫公開之後排程的輸出誰都看得到：印的是遮過的那一份，完整的（含被退回的句子）只在 --report
    print(json.dumps(public_report(report), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
