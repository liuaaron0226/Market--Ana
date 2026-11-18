"""自選股關注的每日排程（G 票）：晚間版在大盤預測晚間版跑完之後、晨間版在大盤預測晨間版之後，
替每一檔關注股寫一份判斷，鎖定、輸出給網站資料庫的指令檔；晚間另替到期的轉變日對帳。

和大盤預測同一條線（``forecast_run.py``）：輸入是每日更新剛發布的大盤資料檔（加權指數日 K），模型呼叫、鎖定、
期限、漏記都在帳本裡（``watch_ledger`` → ``ledger_core``），原件存回 R2 用 ``forecast_store.py --prefix watch-ledger/``。

交給 Gemini 的只有證交所／櫃買官方資料與由它算出的東西（技術面、籌碼、官方公告、大盤預測），加上 I 票已查證
可放的匯率與美債利率；新聞、國外行情、即時報價都不進提示（H 票、要本人決定付費）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import tempfile
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import forecast_ledger as fl
import gemini_models
import watch_ledger as wl
from forecast_run import load_analysis, previous_trading_day
from level_signals import trading_day_status
from ledger_core import TAIPEI, append, build_prompt, call_model, default_now, read_record, seal, sha

SCHEMA = "watch-run/v1"
# 本人 2026-11-17：每日判斷與提名同類股都用 Flash（名稱只在 gemini_models 改；兩者分開設定）。
JUDGE_MODEL = gemini_models.WATCH_JUDGE
NOMINATE_MODEL = gemini_models.WATCH_NOMINATE
ROOT = Path(__file__).resolve().parent
# 本人 2026-11-16 選「照規格最多」：每次排程最多叫模型 40 次（20 檔各一次，加上不合格時的重試）。
CALL_BUDGET = 40
# 重播乾跑的時鐘：那一天收盤、每日更新與大盤預測晚間版之後；晨間版是開盤日早上、期限（08:45）之前。
REPLAY_CLOCK = "17:30:00"
MORNING_REPLAY_CLOCK = "07:30:00"
# 這一版打開的輸入層＝「輸入到齊」的那一組：官方資料與由它算出的東西，加上帳本自己整理的教訓層（H 票）。
# 國外同類股與新聞標題依來源條款不放（Gemini 付費與否由本人決定；改了就換這一組、換輸入版本、重新起算）。
# wyckoff_lpsy：H 票（本人 2026-11-18 追加）威科夫最後供應點，打開它＝新的輸入版本，和之前的紀錄分開對帳。
# valuation、lending_eps、signals：I 票第二輪（本人 2026-11-18 晚「大盤＋自選股判斷」）估值、借券與季 EPS、個股五塊訊號。
# intraday、headlines、peers：J 票（本人 2026-11-19 00:1x「富果、新聞來源、國外同類股都交給 Gemini 做詳細分析」）。
LAYERS = ("technical", "bollinger", "wyckoff_lpsy", "sector", "margin", "valuation", "lending_eps", "signals",
          "intraday", "environment", "market", "news", "headlines", "peers", "lessons")


class Source(Protocol):
    """一檔關注股的資料：實際成交價日 K、股本變動與各層輸入（只放條款允許交給模型的）；官方公告依代號給。"""

    def stock(self, code: str, base_date: str, edition: str) -> dict[str, Any]: ...

    def announcements(self, codes: Sequence[str], until: str) -> dict[str, list[dict[str, Any]]]: ...


# ---- 本機工作集（每日更新存回 R2、排程還原的那一份）：只讀證交所／櫃買官方資料與由它算出的東西 ----

STOCK_HISTORY_BARS = 750          # 算候選關卡與「隨便挑」用的日 K（約三年）
CHIP_DAYS = 5                     # 法人買賣超：近 5 個交易日
BIG_HOLDER_WEEKS = 4              # 集保大戶持股：近 4 週
REVENUE_MONTHS = 3                # 月營收：近 3 個月
ANNOUNCEMENT_DAYS = 10            # 官方重大訊息：近 10 天


class LocalSource:
    """從工作集讀一檔關注股的輸入。各塊各自讀，讀不到只讓那一塊是 None（帳本標缺漏），不擋這一檔。

    ``root`` 是放 ``data/`` 的那一層（排程裡是 repo 根目錄，``state_sync restore --target .`` 還原的位置）；
    個股頁的計算（``twon_analysis``）固定讀程式旁邊的 ``data/``，所以兩者要是同一個地方。"""

    def __init__(self, root: Path | str = ROOT, *, fugle: Any = None, query: Callable[[str], list[dict]] | None = None,
                 owner_email: str = ""):
        """``fugle``：富果行情（``licensed_inputs.Fugle``，沒有就是那一層缺）；``query``：網站資料庫的唯讀查詢
        （新聞與國外同類股存在那裡，排程給 ``watch_news._wrangler_query``；沒有就是那兩層缺）。"""
        self.root = Path(root)
        self.fugle, self.query, self.owner_email = fugle, query, owner_email
        self.fugle_errors: dict[str, dict[str, str]] = {}
        self.licensed_errors: dict[str, str] = {}
        self._industries: dict[str, tuple[str, str]] | None = None
        self._capital: dict[str, list[dict[str, Any]]] | None = None
        self._members: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
        self._loaded: dict[tuple[str, str, int], tuple[list[Any], dict[str, Any]]] = {}
        self._analyses: dict[tuple[str, str], dict[str, Any]] = {}
        self._fundamentals: Any = None

    def _analysis(self, code: str, base_date: str) -> dict[str, Any]:
        """個股頁同一套計算的結果；技術層與五塊訊號共用，同一檔同一天只算一次。"""
        key = (code, base_date)
        if key not in self._analyses:
            from twon_analysis import analyze_v2
            self._analyses[key] = analyze_v2.analyze_v2(code, base_date, capital_actions=self._capital_events())
        return self._analyses[key]

    def _capital_events(self) -> dict[str, list[dict[str, Any]]]:
        if self._capital is None:
            from twon_analysis.price_adjustment import default_capital_events
            self._capital = default_capital_events(self.root)
        return self._capital

    def _load(self, code: str, base_date: str, limit: int) -> tuple[list[Any], dict[str, Any]]:
        """行情庫的日 K 與當日資訊；同一檔、同一天、同樣根數只讀一次（日 K 與最後供應點共用）。"""
        key = (code, base_date, limit)
        if key not in self._loaded:
            from twon_analysis.data import load_bars
            self._loaded[key] = load_bars(code, base_date, db_path=self.root / "data/stock-history/stock-history.sqlite",
                                          otc_db_path=self.root / "data/otc-history/otc_history.sqlite", limit=limit)
        return self._loaded[key]

    def _bars(self, code: str, base_date: str, limit: int = STOCK_HISTORY_BARS) -> list[dict[str, Any]]:
        bars, _ = self._load(code, base_date, limit)
        return [{"date": b.date, "open": b.open, "high": b.high, "low": b.low, "close": b.close, "volume": b.volume}
                for b in bars]

    def _lpsy(self, code: str, base_date: str) -> dict[str, Any] | None:
        """威科夫最後供應點（H 票）：實際成交價與逐日漲跌換成含息還原價（和研究的歷史統計同一種價格），
        判斷和大盤事前預測共用 ``wyckoff_lpsy``。"""
        import wyckoff_lpsy
        bars, info = self._load(code, base_date, STOCK_HISTORY_BARS)
        return wyckoff_lpsy.stock_layer(bars, info["day_returns"]) if bars else None

    def _technical(self, code: str, base_date: str) -> dict[str, Any] | None:
        """個股頁同一套計算（還原後的價格）：指標、法人買賣超、集保大戶持股、月營收。
        不放個股頁的訊號與策略（固定規則的買賣點，回測沒贏過大盤，規格要求不換個名字端出來）。"""
        from twon_analysis import analyze_v2
        result = self._analysis(code, base_date)
        if result.get("qa", {}).get("blocking"):
            return None
        revenue = None
        try:
            revenue = analyze_v2.revenue_series(code, result["market"], base_date)[-REVENUE_MONTHS:]
        except Exception:  # noqa: BLE001 — 月營收讀不到只讓那一項留空
            revenue = None
        return {"market": result["market"], "indicators": result["indicators"],
                "institutional": result["institutional"][-CHIP_DAYS:],
                "big_holders": result["big_holders"][-BIG_HOLDER_WEEKS:], "monthly_revenue": revenue,
                "source": "臺灣證券交易所／證券櫃檯買賣中心開放資料、集保結算所開放資料（本站計算）"}

    def _signals(self, code: str, base_date: str) -> dict[str, Any] | None:
        """個股頁五塊資料（I 票第二輪）：和技術層同一份計算，去掉畫圖用的陣列。"""
        import watch_inputs
        result = self._analysis(code, base_date)
        return None if result.get("qa", {}).get("blocking") else watch_inputs.signals_input(result.get("signals"))

    def _lending_eps(self, code: str, base_date: str) -> dict[str, Any] | None:
        """借券（個股頁同一份原件）與近 4 季每股盈餘（公開資訊觀測站原件，驗過雜湊才讀；整份只讀一次）。"""
        import fundamentals_history
        import watch_inputs
        from twon_analysis import analyze_v2
        try:
            market = self._analysis(code, base_date).get("market") or "上市"
        except Exception:  # noqa: BLE001 — 個股頁計算失敗不擋借券與 EPS：先當上市讀，上櫃那一檔就只少借券
            market = "上市"
        credit = analyze_v2.credit_series(code, market, base_date, days=watch_inputs.LENDING_DAYS + 5)
        quarterly: list[dict[str, Any]] = []
        folder = self.root / watch_inputs.FUNDAMENTALS_DIR
        if (folder / "meta.json").is_file():
            if self._fundamentals is None:
                self._fundamentals = fundamentals_history.load_history(folder)
            quarterly = self._fundamentals.as_of(code, base_date)["quarterly"]
        return watch_inputs.lending_eps_input(credit, quarterly, base_date)

    def _intraday(self, code: str, base_date: str) -> dict[str, Any] | None:
        """富果的基準日盤中資料（J 票）；回的不是基準日（舊日期、或已經進入新的一天）就是缺。"""
        import licensed_inputs
        layer, errors = licensed_inputs.fetch_stock_intraday(self.fugle, code, base_date)
        if errors:
            self.fugle_errors[code] = errors
        return layer if layer.get("status") == "ok" else None

    def licensed(self, codes: Sequence[str], *, before: str, cutoff: str) -> dict[str, dict[str, Any]]:
        """新聞標題與國外同類股（J 票），從網站資料庫唯讀查。只用 cutoff（寫的那一刻）以前已經抓到的；
        國外收盤只用 before（晚間版是基準日、晨間版是開盤日）以前的。查不到就是空的（那兩層缺）。"""
        import licensed_inputs
        out: dict[str, dict[str, Any]] = {code: {} for code in codes}
        safe = [code for code in codes if re.fullmatch(r"[0-9A-Za-z]{1,10}", code)]
        if self.query is None or not safe:
            return out
        listed = ",".join(f"'{code}'" for code in safe)
        since = (datetime.fromisoformat(cutoff) - timedelta(days=licensed_inputs.HEADLINE_DAYS + 1)).astimezone(
            TAIPEI).date().isoformat()
        try:
            rows = self.query("SELECT code, link, source, title, summary, published_at, fetched_at FROM watch_news "
                              f"WHERE code IN ({listed}) AND published_at >= '{since}'")
            for code in safe:
                out[code]["news_headlines"] = licensed_inputs.headline_items(
                    [row for row in rows if row.get("code") == code], cutoff=cutoff)
        except Exception as error:  # noqa: BLE001 — 查不到只讓新聞那一層缺
            self.licensed_errors["headlines"] = type(error).__name__
        try:
            from watch_news import _EMAIL
            if not _EMAIL.fullmatch(self.owner_email or ""):
                raise ValueError("owner_email")
            peers = self.query("SELECT code, symbol, name, market FROM watch_peers "
                               f"WHERE lower(email) = lower('{self.owner_email}') AND status = 'confirmed' "
                               f"AND code IN ({listed})")
            symbols = sorted({row["symbol"] for row in peers if re.fullmatch(r"[0-9A-Za-z.^_-]{1,20}", row["symbol"])})
            quotes = self.query("SELECT symbol, date, close, fetched_at FROM overseas_quotes WHERE symbol IN ("
                                + ",".join(f"'{symbol}'" for symbol in symbols) + ")") if symbols else []
            for code in safe:
                out[code]["peers"] = licensed_inputs.peers_layer(
                    [row for row in peers if row.get("code") == code and row["symbol"] in symbols], quotes,
                    before=before, cutoff=cutoff)
        except Exception as error:  # noqa: BLE001 — 查不到只讓同類股那一層缺
            self.licensed_errors["peers"] = type(error).__name__
        return out

    def _sector(self, code: str, base_date: str) -> dict[str, Any] | None:
        import watch_inputs
        if self._industries is None:
            self._industries = watch_inputs.catalog_industries(self.root)
        industry = (self._industries.get(code) or (None, None))[1]
        if not industry:
            return None
        key = (industry, base_date)
        if key not in self._members:   # 同產業的關注股共用一份（半導體業一組就上百檔）
            self._members[key] = {other: {"name": name, "industry": kind,
                                          "daily": self._bars(other, base_date, limit=CHIP_DAYS + 2)}
                                  for other, (name, kind) in self._industries.items() if kind == industry}
        return watch_inputs.sector_input(code, base_date, self._members[key])

    def stock(self, code: str, base_date: str, edition: str) -> dict[str, Any]:
        import watch_inputs
        out: dict[str, Any] = {"stock_daily": self._bars(code, base_date),
                               "capital_events": self._capital_events().get(code, [])}
        for key, read in (("technical", lambda: self._technical(code, base_date)),
                          ("wyckoff_lpsy", lambda: self._lpsy(code, base_date)),
                          ("sector", lambda: self._sector(code, base_date)),
                          ("margin", lambda: watch_inputs.margin_input(self.root, code, base_date)),
                          ("valuation", lambda: watch_inputs.valuation_input(self.root, code, base_date)),
                          ("lending_eps", lambda: self._lending_eps(code, base_date)),
                          ("signals", lambda: self._signals(code, base_date)),
                          ("intraday", lambda: self._intraday(code, base_date)),
                          ("environment", lambda: watch_inputs.environment_input(self.root, base_date,
                                                                                 edition=edition))):
            try:
                out[key] = read()
            except Exception as error:  # noqa: BLE001 — 一塊讀不到只標缺漏
                out[key], out.setdefault("errors", {})[key] = None, f"{type(error).__name__}: {error}"
        out["data_cutoffs"] = {"stock": out["stock_daily"][-1]["date"] if out["stock_daily"] else None,
                               "margin": (out.get("margin") or {}).get("data_date"),
                               "sector": (out.get("sector") or {}).get("data_date"),
                               "valuation": (out.get("valuation") or {}).get("data_date"),
                               "lending": ((out.get("lending_eps") or {}).get("lending") or {}).get("data_date"),
                               "signals": (out.get("signals") or {}).get("data_date")}
        return out

    def announcements(self, codes: Sequence[str], until: str) -> dict[str, list[dict[str, Any]]]:
        """官方重大訊息（證交所、櫃買每日快照的累積原件）。每則有編號、出處與發言時間；模型只能引用這些編號。"""
        import hashlib

        import announcements
        rows = announcements.collect(self.root / "data/announcements/raw", as_of=until, codes=set(codes),
                                     days=ANNOUNCEMENT_DAYS)
        out: dict[str, list[dict[str, Any]]] = {code: [] for code in codes}
        for row in rows:
            key = "|".join([row["market"], row["code"], row["date"], row["time"], row["subject"]])
            out[row["code"]].append({
                "id": "ann_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:10],
                "source": "twse" if row["market"] == "上市" else "tpex", "kind": "重大訊息",
                "published_at": f"{row['date']}T{row['time']}+08:00", "title": row["subject"], "body": row["body"]})
        return out


def market_layer(forecast_ledger: Path | str | None, base_date: str, edition: str) -> dict[str, Any] | None:
    """同一基準日、同一版的大盤預測：盤勢類型與三格的落點。沒有那一份或那一份是漏記時是 None（標缺漏）。

    換模型那天同一基準日可能有新舊模型各一份（本人 2026-11-17 預測換 Argon），先取現在這個模型的輸入版本。"""
    if forecast_ledger is None or not Path(forecast_ledger).is_dir():
        return None
    found = [record for record in fl.read_ledger(forecast_ledger)["forecasts"]
             if record["base_date"] == base_date and record["edition"] == edition and record["kind"] == "forecast"]
    if not found:
        return None
    record = min(found, key=lambda record: record["input_version"] != fl.INPUT_VERSION)
    return {"forecast_id": record["forecast_id"], "regime": record["regime"],
            "cells": {key: {"zone": cell["zone"],
                            "strong_line": {k: cell["strong_line"][k] for k in ("name", "value")},
                            "weak_line": {k: cell["weak_line"][k] for k in ("name", "value")}}
                      for key, cell in record["cells"].items()}}


def stock_input(item: Mapping[str, Any], data: Mapping[str, Any], *, base_date: str,
                index_daily: list[dict[str, Any]], method_text: str, market: dict[str, Any] | None,
                announcements: list[dict[str, Any]] | None) -> dict[str, Any]:
    return {
        "code": str(item["code"]), "name": item.get("name"), "base_date": base_date,
        "stock_daily": data.get("stock_daily") or [], "capital_events": data.get("capital_events") or [],
        "index_daily": index_daily, "method_text": method_text,
        "technical": data.get("technical"), "wyckoff_lpsy": data.get("wyckoff_lpsy"), "sector": data.get("sector"),
        "margin": data.get("margin"), "valuation": data.get("valuation"), "lending_eps": data.get("lending_eps"),
        "signals": data.get("signals"), "intraday": data.get("intraday"),
        "news_headlines": data.get("news_headlines"), "peers": data.get("peers"),
        "environment": data.get("environment"), "market_forecast": market, "announcements": announcements,
        "data_cutoffs": {**(data.get("data_cutoffs") or {}), "index": index_daily[-1]["date"] if index_daily else None},
    }


def _index_daily(analysis: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [{"date": row["date"], "close": row.get("close")} for row in analysis.get("daily") or []]


def _load(source: Source, codes: Sequence[str], base_date: str, edition: str) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """每一檔各自讀；讀不到的那一檔是空的（帳本會當成資料沒到齊），原因另外記，不擋其他檔。"""
    loaded, errors = {}, {}
    for code in dict.fromkeys(codes):
        try:
            loaded[code] = dict(source.stock(code, base_date, edition))
        except Exception as error:  # noqa: BLE001 — 一檔讀不到不影響其他檔
            loaded[code], errors[code] = {}, f"{type(error).__name__}: {error}"
    return loaded, errors


def _until(rows: Sequence[Mapping[str, Any]] | None, day: str) -> list[dict[str, Any]]:
    return [dict(row) for row in rows or [] if str(row.get("date") or "") <= day]


def _backfill_misses(ledger: Path | str, watchlist: Sequence[Mapping[str, Any]], loaded: Mapping[str, Mapping[str, Any]],
                     index_daily: list[dict[str, Any]], base_date: str, *, edition: str, method_text: str,
                     model_bin: Path | str, model: str, clock: Callable[[], datetime]) -> list[dict[str, str]]:
    """這一版整個時段沒跑的交易日補記漏記（和大盤預測同一個處理）：每一檔這一版最後一份之後、今天這份之前，
    指數有日 K 卻沒有紀錄的基準日。那些日子都已過期限，帳本不呼叫模型，只鎖一筆漏記並寫下原因。

    只補這一檔「最後一次打開關注」那天以後的日子：關掉的那段不算漏記。這一檔這一版還沒有任何紀錄時不補（起點之前不算）。
    晨間版的開盤日是指數日 K 裡基準日的下一根，所以基準日要有下一根才補。
    """
    records = wl.read_ledger(ledger)["judgments"]
    dates = [row["date"] for row in index_daily]
    filled = []
    for item in watchlist:
        code = str(item["code"])
        earlier = [record["base_date"] for record in records if record["code"] == code and record["edition"] == edition]
        if not earlier:
            continue
        since = str(item.get("watch_since") or "")[:10]
        for position, day in enumerate(dates):
            if not (max(earlier) < day < base_date and day >= since):
                continue
            session = None
            if edition == "morning":
                if position + 1 >= len(dates):
                    continue
                session = dates[position + 1]
            data = stock_input(item, {key: _until(value, day) if key == "stock_daily" else value
                                      for key, value in loaded[code].items()},
                               base_date=day, index_daily=_until(index_daily, day), method_text=method_text,
                               market=None, announcements=None)
            wl.write_judgment(ledger, data, edition=edition, session_date=session, model_bin=model_bin, model=model,
                              now=clock, layers=LAYERS)
            filled.append({"code": code, "base_date": day})
    return filled


def _summary(result: Mapping[str, Any], load_error: str | None = None) -> dict[str, Any]:
    """執行紀錄裡每一檔的結果；乾跑時給本人看的就是這一份（狀態、兩條線、理由），不放完整輸入。"""
    record = result.get("record") or {}
    return {"code": result["code"], "outcome": result["outcome"], "kind": record.get("kind"),
            "status": record.get("status"), "trigger_line": record.get("trigger_line"),
            "wrong_line": record.get("wrong_line"), "reason": record.get("reason"),
            "reason_news": record.get("reason_news"), "transition": record.get("transition"),
            "base_close": record.get("base_close"), "miss_reason": record.get("miss_reason"),
            "miss_detail": record.get("miss_detail"), "missing_layers": record.get("missing_layers"),
            "attempts": record.get("attempts"), "input_version": record.get("input_version"),
            **({"detail": "；".join(filter(None, [load_error, result.get("detail")]))}
               if load_error or result.get("detail") else {})}


def _frozen(day: str, clock_text: str) -> Callable[[], datetime]:
    moment = datetime.fromisoformat(f"{day}T{clock_text}").replace(tzinfo=TAIPEI)
    return lambda: moment


def _finish(log: dict[str, Any], clock: Callable[[], datetime], log_output: Path | str | None) -> dict[str, Any]:
    log["finished_at"] = clock().astimezone(TAIPEI).isoformat()
    if log_output:
        Path(log_output).parent.mkdir(parents=True, exist_ok=True)
        Path(log_output).write_text(json.dumps(log, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return log


def run_evening(
    analysis_path: Path | str,
    *,
    watchlist: Sequence[Mapping[str, Any]],
    ledger: Path | str,
    forecast_ledger: Path | str | None,
    method_path: Path | str,
    model_bin: Path | str,
    source: Source,
    model: str = JUDGE_MODEL,
    now: Callable[[], datetime] | None = None,
    dry_run: bool = False,
    as_of: str | None = None,
    sql_output: Path | str | None = None,
    log_output: Path | str | None = None,
    call_budget: int = CALL_BUDGET,
    timeout_seconds: float = 360,
) -> dict[str, Any]:
    """寫晚間版：先對帳、補記整晚沒跑的漏記，再依打開關注的先後一檔一檔寫。

    乾跑（``dry_run``）只在暫存的帳本副本上寫，不碰真的帳本、不輸出資料庫指令檔；``as_of`` 重播已發布過的某一天
    （資料截到那天、時鐘停在那天收盤後），只能在乾跑使用。
    """
    if as_of and not dry_run:
        raise ValueError("重播某一天（as_of）只能在乾跑使用，正式帳本不補寫過去的判斷")
    clock = _frozen(as_of, REPLAY_CLOCK) if as_of else (now or default_now)
    index_daily = _index_daily(load_analysis(analysis_path))
    if as_of:
        index_daily = _until(index_daily, as_of)
        if not index_daily or index_daily[-1]["date"] != as_of:
            raise ValueError(f"已發布的日 K 裡沒有 {as_of}，不能重播")
    base_date = index_daily[-1]["date"]
    return _in_ledger(ledger, dry_run, lambda target: _run(
        target, "evening", watchlist, index_daily, base_date, session_date=None, forecast_ledger=forecast_ledger,
        method_path=method_path, model_bin=model_bin, source=source, model=model, clock=clock, dry_run=dry_run,
        sql_output=None if dry_run else sql_output, log_output=log_output, call_budget=call_budget,
        timeout_seconds=timeout_seconds))


def run_morning(
    analysis_path: Path | str,
    *,
    watchlist: Sequence[Mapping[str, Any]],
    ledger: Path | str,
    forecast_ledger: Path | str | None,
    method_path: Path | str,
    model_bin: Path | str,
    source: Source,
    session_date: str,
    holidays: set[str],
    model: str = JUDGE_MODEL,
    now: Callable[[], datetime] | None = None,
    dry_run: bool = False,
    replay: bool = False,
    sql_output: Path | str | None = None,
    log_output: Path | str | None = None,
    call_budget: int = CALL_BUDGET,
    timeout_seconds: float = 360,
) -> dict[str, Any]:
    """寫晨間版：開盤日 ``session_date`` 早上、開盤前 15 分鐘之前鎖定；基準日是前一個台股交易日（和大盤預測晨間版同一套）。

    休市日不寫。日 K 還沒有基準日時不寫、不記漏記（之後的晨間執行補記）。不對帳（對帳只接在每日更新發布之後）。
    和同一基準日的晚間版互不相干：交給模型的輸入裡沒有任何帳本內容，轉變日只和前一份晨間版比。
    ``replay``（只能乾跑）：重播過去某個開盤日，時鐘停在那天 07:30。
    """
    if replay and not dry_run:
        raise ValueError("重播某個開盤日只能在乾跑使用，正式帳本不補寫過去的判斷")
    clock = _frozen(session_date, MORNING_REPLAY_CLOCK) if replay else (now or default_now)
    if not trading_day_status(session_date, holidays):
        log = {"schema": SCHEMA, "edition": "morning", "dry_run": dry_run, "session_date": session_date,
               "base_date": None, "outcome": "holiday", "started_at": clock().astimezone(TAIPEI).isoformat(),
               "detail": f"{session_date} 不是台股交易日，不寫晨間版"}
        return _finish(log, clock, log_output)
    base_date = previous_trading_day(session_date, holidays)
    # 開盤日之後才發布的資料（例如當天傍晚重跑）不能讓晨間版看到。
    index_daily = _until(_index_daily(load_analysis(analysis_path)), base_date)
    if not index_daily or index_daily[-1]["date"] != base_date:
        log = {"schema": SCHEMA, "edition": "morning", "dry_run": dry_run, "session_date": session_date,
               "base_date": base_date, "outcome": "not_ready", "started_at": clock().astimezone(TAIPEI).isoformat(),
               "detail": f"日 K 還沒有基準日 {base_date}（資料沒發布或臨時休市）"}
        return _finish(log, clock, log_output)
    return _in_ledger(ledger, dry_run, lambda target: _run(
        target, "morning", watchlist, index_daily, base_date, session_date=session_date,
        forecast_ledger=forecast_ledger, method_path=method_path, model_bin=model_bin, source=source, model=model,
        clock=clock, dry_run=dry_run, sql_output=None if dry_run else sql_output, log_output=log_output,
        call_budget=call_budget, timeout_seconds=timeout_seconds))


def _in_ledger(ledger: Path | str, dry_run: bool, work: Callable[[Path], dict[str, Any]]) -> dict[str, Any]:
    """乾跑時在暫存的帳本副本上做，真的帳本一個檔案都不動。"""
    if not dry_run:
        return work(Path(ledger))
    with tempfile.TemporaryDirectory() as scratch:
        copy = Path(scratch) / "ledger"
        if Path(ledger).is_dir():
            shutil.copytree(ledger, copy)
        return work(copy)


def _official_since(ledger: Path, edition: str, base_date: str, model: str = JUDGE_MODEL) -> str | None:
    """這一組輸入（``LAYERS``，連同這次用的模型）的正式起算日。還沒寫下時，晚間執行看帳本：之前有一天早晚兩版都用這一組跑過，
    就把今天寫成起算日（寫了不改）；今天起的判斷是正式，之前的是試跑。晨間只讀不寫。"""
    version = wl.input_version_of(LAYERS, model)
    since = wl.official_starts(ledger).get(version)
    if since is None and edition == "evening":
        started = wl.start_official(ledger, since=base_date, input_version=version)
        since = started["record"]["since"] if started["record"] else None
    return since


def _run(ledger: Path, edition: str, watchlist: Sequence[Mapping[str, Any]], index_daily: list[dict[str, Any]],
         base_date: str, *, session_date: str | None, forecast_ledger: Path | str | None, method_path: Path | str,
         model_bin: Path | str, source: Source, model: str, clock: Callable[[], datetime], dry_run: bool,
         sql_output: Path | str | None, log_output: Path | str | None, call_budget: int,
         timeout_seconds: float) -> dict[str, Any]:
    method_text = Path(method_path).read_text(encoding="utf-8")
    codes = [str(item["code"]) for item in watchlist]
    market = market_layer(forecast_ledger, base_date, edition)
    # 晨間版看得到開盤日早上（開盤前）才發的公告；排程在寫之前另抓一次當天的官方快照。
    news = source.announcements(codes, session_date or base_date)
    log: dict[str, Any] = {"schema": SCHEMA, "edition": edition, "dry_run": dry_run, "base_date": base_date,
                           **({"session_date": session_date} if session_date else {}),
                           "model": model, "started_at": clock().astimezone(TAIPEI).isoformat(), "watched": codes}
    # 帳本裡有轉變日的每一檔（含已經關掉關注的）都要對帳，所以晚間版的行情要讀關注清單與帳本兩邊的代號。
    ledger_codes = sorted({record["code"] for record in wl.read_ledger(ledger)["judgments"]})
    loaded, load_errors = _load(source, [*codes, *ledger_codes], base_date, edition)
    # J 票：新聞標題與國外同類股（只用寫的那一刻以前已經有的；國外收盤只用開盤日——晚間版是基準日——以前的）。
    licensed = getattr(source, "licensed", None)
    if licensed is not None:
        extra = licensed(codes, before=session_date or base_date, cutoff=clock().astimezone(TAIPEI).isoformat())
        for code in codes:
            loaded.setdefault(code, {}).update(extra.get(code) or {})
        log["licensed_errors"] = dict(getattr(source, "licensed_errors", {}) or {})
        log["fugle_errors"] = dict(getattr(source, "fugle_errors", {}) or {})
    if edition == "evening":
        # 先對帳再寫：今天到期的格子先對完；今天這份最早的到期日在明天，寫完不必再對。
        log["reconciliation"] = {key: len(value) for key, value in wl.reconcile(ledger, index_daily, loaded).items()}
    log["backfilled_misses"] = _backfill_misses(ledger, watchlist, loaded, index_daily, base_date, edition=edition,
                                                method_text=method_text, model_bin=model_bin, model=model, clock=clock)
    log["official_since"] = _official_since(ledger, edition, base_date, model)
    stocks = [stock_input(item, loaded[str(item["code"])], base_date=base_date, index_daily=index_daily,
                          method_text=method_text, market=market, announcements=news.get(str(item["code"])))
              for item in watchlist]
    results = wl.write_judgments(ledger, stocks, edition=edition, session_date=session_date, model_bin=model_bin,
                                 model=model, now=clock, layers=LAYERS, timeout_seconds=timeout_seconds,
                                 call_budget=call_budget, official_since=log["official_since"])
    log["stocks"] = [_summary(result, load_errors.get(result["code"])) for result in results]
    if sql_output:
        published_at = clock().astimezone(TAIPEI).isoformat()
        Path(sql_output).parent.mkdir(parents=True, exist_ok=True)
        Path(sql_output).write_text(wl.d1_sql(ledger, index_daily, loaded, published_at=published_at,
                                              official_since=log["official_since"]), encoding="utf-8")
    return _finish(log, clock, log_output)


# ---- 第 16 片：Gemini 提名國外同類股，站主確認後才使用 ----

NOMINATION_SCHEMA = "watch-nomination/v1"
NOMINATION_BUDGET = 20            # 每次排程最多叫幾次模型來提名（一檔一次、不合格重試也算）
# 查出確定結果的排除原因；其他（EODHD 這一次的檢查額度、網路、金鑰、額度）是這一次沒查成，下一次排程再查。
SETTLED_EXCLUSIONS = {"not_found", "no_source", "unreadable_symbol"}
CHECK_PATIENCE_DAYS = 3
NOMINEES_MIN, NOMINEES_MAX = 3, 5
REASON_LIMIT = 60
NOMINATION_OUTPUT: dict[str, Any] = {
    "type": "object", "additionalProperties": False, "required": ["code", "peers"],
    "properties": {
        "code": {"type": "string"},
        "peers": {"type": "array", "minItems": NOMINEES_MIN, "maxItems": NOMINEES_MAX, "items": {
            "type": "object", "additionalProperties": False, "required": ["symbol", "name", "reason"],
            "properties": {"symbol": {"type": "string"}, "name": {"type": "string"},
                           "reason": {"type": "string", "minLength": 1, "maxLength": REASON_LIMIT}}}},
    },
}
NOMINATION_RULES = (
    "你是產業研究員。只根據你對公開資訊的一般認識回答，不可連網、不可使用工具。",
    f"替 INPUT_JSON 這一家台灣上市櫃公司提名 {NOMINEES_MIN} 到 {NOMINEES_MAX} 家在國外上市、業務最相關的公司"
    "（客戶、供應商、主要競爭者或同一條產業鏈），讓站主對照它們的股價漲跌。",
    "優先挑美國上市的公司（symbol 寫美股代號，例如 NVDA）；韓國上市的寫六位數代號加 .KS 或 .KQ。"
    "日本上市的公司目前拿不到行情，不要提名。不要提名台灣上市櫃公司。",
    f"每一家附一句繁體中文理由（{REASON_LIMIT} 字以內），說明和這家台灣公司的關係；不寫股價、目標價或任何買賣建議。",
    "code 照抄 INPUT_JSON。",
)
_SYMBOL_PATTERNS: tuple[tuple[str, Callable[[re.Match[str]], tuple[str, str]]], ...] = (
    (r"\^([A-Z0-9]{2,10})", lambda m: (f"{m[1]}.INDX", "INDEX")),
    (r"([A-Z0-9]{2,10})\.INDX", lambda m: (f"{m[1]}.INDX", "INDEX")),
    (r"(\d{6})\.(KO|KS|KQ)", lambda m: (f"{m[1]}.{'KQ' if m[2] == 'KQ' else 'KO'}", "KR")),
    (r"([0-9][0-9A-Z]{3})\.(T|TSE|JP)", lambda m: (f"{m[1]}.TSE", "JP")),
    (r"([A-Z]{1,5}(?:\.[A-Z])?)(?:\.US)?", lambda m: (f"{m[1]}.US", "US")),
)


def peer_symbol(text: Any) -> dict[str, str] | None:
    """代號 → 正規化寫法與市場（和網站 cloud/lib/watch-peers.js 的 parsePeerSymbol 同一套）；看不懂就是 None。"""
    value = text.strip().upper() if isinstance(text, str) else ""
    if not value or len(value) > 16:
        return None
    for pattern, build in _SYMBOL_PATTERNS:
        match = re.fullmatch(pattern, value)
        if match:
            symbol, market = build(match)
            return {"symbol": symbol, "market": market}
    return None


# EODHD 免費方案每天 20 次，和水位、大盤預測的海外指數、每日國外行情、站主自己加代號時的檢查共用；
# 提名的檢查每次排程最多用 2 次，多的那幾檔這一次不列（站主可以自己加）。
EODHD_CHECKS_PER_RUN = 2
CHECK_DAYS = 14
_SOURCE_OF_MARKET = {"US": "massive", "KR": "eodhd", "INDEX": "eodhd"}


def source_check(sources: Mapping[str, Any], *, today: str, eodhd_limit: int = EODHD_CHECKS_PER_RUN,
                 sleep: Callable[[float], None] | None = None) -> Callable[[str, str], str]:
    """「來源拿得到行情」的檢查（和網站加代號時同一個標準：最近兩週有收盤才算）。

    ``sources`` 是 ``overseas_peers`` 的 ``{"massive": MassiveSource | None, "eodhd": EodhdSource | None}``。
    回 ``ok``／``not_found``／``quota``／``no_key``／``eodhd_limit``／其他來源錯誤的代碼。"""
    import time
    from datetime import date as day_type, timedelta

    import overseas_peers as op
    pause = sleep or time.sleep
    used = {"eodhd": 0}
    called: set[str] = set()
    end = day_type.fromisoformat(today)

    def check(symbol: str, market: str) -> str:
        name = _SOURCE_OF_MARKET.get(market)
        source = sources.get(name) if name else None
        if source is None:
            return "no_key" if name else "no_source"
        if name == "eodhd":
            if used["eodhd"] >= eodhd_limit:
                return "eodhd_limit"
            used["eodhd"] += 1
        if name in called and source.pause:
            pause(source.pause)   # Massive 免費方案每分鐘 5 次
        called.add(name)
        try:
            bars = source.daily(symbol, end - timedelta(days=CHECK_DAYS), end)
        except op.SourceError as error:
            return error.problem
        except Exception:  # noqa: BLE001 — 檢查失敗就不列，不擋其他檔
            return "network"
        return "ok" if any(bar.get("close") for bar in bars or []) else "not_found"

    return check


def _nomination_problem(structured: Any, code: str) -> str | None:
    if not isinstance(structured, dict) or set(structured) != {"code", "peers"}:
        return "回答缺欄位或含未允許欄位"
    if structured["code"] != code:
        return "回答的代號不符"
    peers = structured["peers"]
    if not isinstance(peers, list) or not NOMINEES_MIN <= len(peers) <= NOMINEES_MAX:
        return f"提名要 {NOMINEES_MIN} 到 {NOMINEES_MAX} 家"
    for peer in peers:
        if not isinstance(peer, dict) or set(peer) != {"symbol", "name", "reason"}:
            return "提名的欄位不對"
        if not all(isinstance(peer[key], str) and peer[key].strip() for key in ("symbol", "name", "reason")):
            return "提名的欄位是空的"
        if len(peer["reason"]) > REASON_LIMIT:
            return "理由過長"
    return None


# 韓股與指數（EODHD）全部關注股合計的上限；和 cloud/lib/watch-peers.js 的 EODHD_PEER_LIMIT、
# overseas_peers.py 的 EODHD_SYMBOL_LIMIT 是同一個數字，要調整得三處一起改。
LIMITED_PEER_MARKETS = ("KR", "INDEX")
LIMITED_PEER_LIMIT = 12


def _nomination_sql(record: Mapping[str, Any], owner_email: str, at: str) -> list[str]:
    """名單是站主的資料（0016）：每一檔一列、改動紀錄一筆。重發不多一筆，也不動站主已有（含移除）的列。

    本人 2026-11-18「國外同類股直接預設都幫我加入，不用我再確認，反正我事後都可以加入或移除」：提名直接進名單
    （confirmed）。韓股與指數照網站加一檔時同一個上限（已確認、關注還開著的，不同代號合計 12）：滿了那一檔才留成
    「提名・還沒確認」（nominated），站主騰出位置後可以在個股頁確認。"""
    from ledger_core import sql_literal as q
    out = []
    columns = "INSERT INTO watch_peers (email, code, symbol, name, market, status, reason, added_by, updated_at) "
    for peer in record["listed"]:
        email, code, symbol = q(owner_email), q(record["code"]), q(peer["symbol"])
        values = (email, code, symbol, q(peer["name"]), q(peer["market"]))
        tail = f"{q(peer['reason'])}, 'gemini', {q(at)}"
        if peer["market"] in LIMITED_PEER_MARKETS:
            markets = ", ".join(q(market) for market in LIMITED_PEER_MARKETS)
            out.append(columns + f"SELECT {', '.join(values)}, 'confirmed', {tail} "
                       "WHERE (SELECT COUNT(DISTINCT p.symbol) FROM watch_peers p JOIN user_watchlist w "
                       "ON w.email = p.email AND w.code = p.code "
                       f"WHERE p.email = {email} AND p.status = 'confirmed' AND w.watching = 1 AND p.market IN ({markets}) "
                       f"AND p.symbol != {symbol}) < {LIMITED_PEER_LIMIT} "
                       "ON CONFLICT (email, code, symbol) DO NOTHING;")
            status = "nominated"   # 上面因為額度滿了沒寫進去，才會落到這一句
        else:
            status = "confirmed"
        out.append(columns + f"VALUES ({', '.join(values)}, '{status}', {tail}) ON CONFLICT (email, code, symbol) DO NOTHING;")
        out.append("INSERT INTO watch_peer_events (email, code, symbol, name, action, at) "
                   f"SELECT {email}, {code}, {symbol}, {q(peer['name'])}, 'nominate', {q(at)} "
                   "WHERE NOT EXISTS (SELECT 1 FROM watch_peer_events "
                   f"WHERE email = {email} AND code = {code} AND symbol = {symbol} AND action = 'nominate');")
    return out


def _ask_for_nominees(model_input: Mapping[str, Any], *, model_bin: Path | str, model: str, timeout_seconds: float,
                      budget: list[int]) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    """問模型一次（不合格同一份輸入重試一次）；budget 是這一批剩下的呼叫次數（會被扣）。"""
    prompt = build_prompt(NOMINATION_RULES, model_input)
    attempts: list[dict[str, str]] = []
    for _ in range(2):
        if budget[0] <= 0:
            break
        budget[0] -= 1
        status, structured, detail = call_model(model_bin, prompt, model, timeout_seconds, NOMINATION_OUTPUT,
                                                effort=gemini_models.WATCH_NOMINATE_EFFORT)
        if status != "ok":
            attempts.append({"status": status, "detail": detail})
            break
        problem = _nomination_problem(structured, str(model_input["code"]))
        if problem is None:
            return structured, attempts
        attempts.append({"status": "answer_invalid", "detail": problem})
    return None, attempts


def _checked(answer: Mapping[str, Any], check: Callable[[str, str], str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """每一檔提名過一次「來源拿得到行情」：（列出的, 不列的連同原因）。日股沒有來源、代號看不懂的不查。"""
    listed: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for peer in answer["peers"]:
        parsed = peer_symbol(peer["symbol"])
        item = {"symbol": parsed["symbol"] if parsed else peer["symbol"], "market": parsed["market"] if parsed else None,
                "name": peer["name"].strip()[:40], "reason": peer["reason"].strip()}
        if parsed is None:
            excluded.append({**item, "excluded": "unreadable_symbol"})
        elif parsed["market"] == "JP":
            excluded.append({**item, "excluded": "no_source"})
        elif any(other["symbol"] == item["symbol"] for other in listed + excluded):
            continue
        else:
            verdict = check(item["symbol"], item["market"])
            (listed if verdict == "ok" else excluded).append(item if verdict == "ok" else {**item, "excluded": verdict})
    return listed, excluded


def nominate_peers(
    ledger: Path | str,
    stocks: Sequence[Mapping[str, Any]],
    *,
    owner_email: str,
    model_bin: Path | str,
    check: Callable[[str, str], str],
    model: str = NOMINATE_MODEL,
    now: Callable[[], datetime] | None = None,
    call_budget: int = NOMINATION_BUDGET,
    timeout_seconds: float = 360,
) -> dict[str, Any]:
    """替還沒有國外同類股名單的關注股各提名一次，回傳每一檔的結果與給網站資料庫的指令檔。

    ``stocks`` 是 ``{"code", "name", "industry"}``——提示裡只有這三樣公開資訊。``check(symbol, market)`` 是
    「來源拿得到行情」的檢查，回 ``ok``／``not_found``／其他原因；只有 ``ok`` 的才列出（日股沒有來源，不查直接不列）。

    兩段鎖：模型的回答一拿到就鎖在 ``nominations/answers/<代號>.json``（之後不再問模型）；每一檔都查出確定的結果
    （拿得到或查無代號）才鎖名單 ``nominations/<代號>.json`` 並發布。這一次沒查成的（EODHD 這一次的檢查額度、網路、
    金鑰）下一次排程再查；回答鎖下來滿 ``CHECK_PATIENCE_DAYS`` 天還查不成，就照能查的鎖名單、沒查成的寫明原因不列。
    模型失敗或兩次都不合格什麼都不鎖，下一次排程再問。站主確認、取消、自己加在網站上做（``cloud/lib/watch-peers.js``）。
    """
    clock = now or default_now
    folder = Path(ledger) / "nominations"
    budget = [call_budget]
    results: list[dict[str, Any]] = []
    statements: list[str] = []
    for stock in stocks:
        code = str(stock["code"])
        if not re.fullmatch(r"[0-9A-Za-z]{1,12}", code):
            results.append({"code": code, "outcome": "rejected", "detail": "代號不合法"})
            continue
        path, answer_path = folder / f"{code}.json", folder / "answers" / f"{code}.json"
        if path.exists():
            record = read_record(path)
            results.append({"code": code, "outcome": "unchanged", "listed": record["listed"],
                            "excluded": record["excluded"]})
            statements += _nomination_sql(record, owner_email, record["locked_at"])
            continue
        if answer_path.exists():
            answered = read_record(answer_path)
        else:
            model_input = {"code": code, "name": stock.get("name"), "industry": stock.get("industry")}
            accepted, attempts = _ask_for_nominees(model_input, model_bin=model_bin, model=model,
                                                   timeout_seconds=timeout_seconds, budget=budget)
            if accepted is None:
                results.append({"code": code, "outcome": "not_nominated" if attempts else "budget_spent",
                                "attempts": attempts})
                continue
            append(answer_path, seal({"schema": NOMINATION_SCHEMA, "kind": "nomination_answer", "code": code,
                                      "input": model_input, "input_sha256": sha(model_input), "model": model,
                                      "answer": accepted, "attempts": attempts,
                                      "locked_at": clock().astimezone(TAIPEI).isoformat()}))
            answered = read_record(answer_path)
        listed, excluded = _checked(answered["answer"], check)
        unsettled = [item for item in excluded if item["excluded"] not in SETTLED_EXCLUSIONS]
        waited = clock() - datetime.fromisoformat(answered["locked_at"])
        if unsettled and waited < timedelta(days=CHECK_PATIENCE_DAYS):
            results.append({"code": code, "outcome": "check_pending", "listed": listed, "excluded": excluded})
            continue
        record = seal({"schema": NOMINATION_SCHEMA, "kind": "nomination", "code": code,
                       "answer_sha256": answered["record_sha256"], "listed": listed, "excluded": excluded,
                       "locked_at": clock().astimezone(TAIPEI).isoformat()})
        outcome = append(path, record)
        record = read_record(path)
        results.append({"code": code, "outcome": outcome, "listed": record["listed"], "excluded": record["excluded"]})
        statements += _nomination_sql(record, owner_email, record["locked_at"])
    return {"stocks": results, "sql": "\n".join(statements) + ("\n" if statements else "")}


# ---- 命令列（排程用） ----

def authorize(evidence: Path | str, edition: str) -> dict[str, Any]:
    """自動執行的前提：大盤預測同一版剛真的跑完（不是乾跑），看它的執行紀錄。

    預測寫成、沒變、資料沒到齊或記了漏記都算跑完——大盤層缺了在判斷裡標缺漏，不擋個股。
    沒有紀錄（預測整支沒跑或跑到一半掛掉）就不寫：那幾天由之後的執行補記漏記。
    """
    try:
        log = json.loads(Path(evidence).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return {"allowed": False, "reason": f"讀不到大盤預測的執行紀錄：{type(error).__name__}"}
    if not isinstance(log, dict) or log.get("schema") != "forecast-run/v1":
        return {"allowed": False, "reason": "不是大盤預測的執行紀錄"}
    if log.get("dry_run") is not False:
        return {"allowed": False, "reason": "大盤預測那一次是乾跑"}
    if log.get("edition") != edition:
        return {"allowed": False, "reason": f"大盤預測那一次是 {log.get('edition')}，不是 {edition}"}
    return {"allowed": True, "forecast_base_date": log.get("base_date"), "forecast_outcome": log.get("outcome")}


def _owner_literal(owner_email: str) -> str:
    """站主 email 的 SQL 字面值：先用 watch_news 同一個格式檢查，再交給共用的跳脫。"""
    from ledger_core import sql_literal
    from watch_news import _EMAIL
    if not _EMAIL.fullmatch(owner_email or ""):
        raise ValueError("站主 email 格式不對")
    return sql_literal(owner_email)


def watchlist_sql(owner_email: str) -> str:
    """站主現在正在關注的股票，依最後一次打開關注的先後（被額度擠掉的固定是排在後面的同一批）。"""
    return ("SELECT code, name, market, watch_since FROM user_watchlist "
            f"WHERE lower(email) = lower({_owner_literal(owner_email)}) AND watching = 1 ORDER BY watch_since, code")


def nomination_candidates_sql(owner_email: str) -> str:
    """正在關注、而且還沒有任何國外同類股紀錄（提名中、已確認、已移除都算有）的股票，依打開關注的先後。"""
    return ("SELECT w.code, w.name FROM user_watchlist w "
            f"WHERE lower(w.email) = lower({_owner_literal(owner_email)}) AND w.watching = 1 AND NOT EXISTS "
            "(SELECT 1 FROM watch_peers p WHERE p.email = w.email AND p.code = w.code) ORDER BY w.watch_since, w.code")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="自選股關注：每日排程（判斷、對帳、同類股提名）")
    commands = parser.add_subparsers(dest="command", required=True)
    gate = commands.add_parser("authorize", help="看大盤預測的執行紀錄，決定這一次自動執行要不要寫")
    gate.add_argument("--evidence", type=Path, required=True)
    gate.add_argument("--edition", choices=["evening", "morning"], required=True)
    gate.add_argument("--github-output", type=Path)
    listing = commands.add_parser("watchlist", help="從網站資料庫讀站主的關注清單，寫成 JSON")
    listing.add_argument("--out", type=Path, required=True)
    judge = commands.add_parser("judge", help="寫一版判斷（晚間版並對帳；或晨間版）")
    judge.add_argument("--edition", choices=["evening", "morning"], default="evening")
    judge.add_argument("--analysis", type=Path, required=True, help="已發布的 analysis-data.js")
    judge.add_argument("--watchlist", type=Path, required=True, help="watchlist 子指令寫出的 JSON")
    judge.add_argument("--ledger", type=Path, required=True)
    judge.add_argument("--forecast-ledger", type=Path, help="大盤預測帳本（同一基準日、同一版那一份當大盤層）")
    judge.add_argument("--method", type=Path, required=True, help="方法稿（MARKET_METHOD.md）")
    judge.add_argument("--agy-bin", type=Path, required=True)
    judge.add_argument("--model", default=JUDGE_MODEL)
    judge.add_argument("--session-date", help="晨間版：開盤日（台北今天）")
    judge.add_argument("--dry-run", action="store_true", help="只在暫存副本上寫，不碰帳本、不輸出資料庫指令檔")
    judge.add_argument("--as-of", help="乾跑時重播：晚間版是已發布的某一天，晨間版是開盤日（須等於 --session-date）")
    judge.add_argument("--call-budget", type=int, default=CALL_BUDGET)
    judge.add_argument("--sql-output", type=Path)
    judge.add_argument("--log-output", type=Path)
    nominate = commands.add_parser("nominate", help="替還沒有國外同類股名單的關注股請 Gemini 提名（站主確認後才使用）")
    nominate.add_argument("--ledger", type=Path, required=True)
    nominate.add_argument("--agy-bin", type=Path, required=True)
    nominate.add_argument("--model", default=NOMINATE_MODEL)
    nominate.add_argument("--dry-run", action="store_true", help="只在暫存副本上寫、不輸出資料庫指令檔")
    nominate.add_argument("--sql-output", type=Path)
    nominate.add_argument("--log-output", type=Path)
    nominate.add_argument("--shared-cache", action="store_true",
                          help="韓股與指數的檢查走 R2 的 EODHD 共用快取（eodhd-cache/），和大盤預測、每日同類股同一份")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "nominate":
        import overseas_peers as op
        import watch_inputs
        from watch_news import _wrangler_query
        owner = os.environ.get("OWNER_EMAIL", "")
        industries = watch_inputs.catalog_industries(ROOT)
        stocks = [{"code": row["code"], "name": row["name"], "industry": (industries.get(row["code"]) or (None, None))[1]}
                  for row in _wrangler_query(nomination_candidates_sql(owner))]
        keys = {name: os.environ.get(env, "").strip() for name, env in (("massive", "MASSIVE_API_KEY"),
                                                                       ("eodhd", "EODHD_API_KEY"))}
        sources = {"massive": op.MassiveSource(keys["massive"]) if keys["massive"] else None,
                   "eodhd": op.EodhdSource(keys["eodhd"]) if keys["eodhd"] else None}
        from eodhd_cache import SharedEodhd
        shared = SharedEodhd.from_env(keys["eodhd"]) if args.shared_cache else None
        if shared is not None:
            sources["eodhd"] = op.CachedEodhdSource(shared)
        today = datetime.now(TAIPEI).date().isoformat()
        result = _in_ledger(args.ledger, args.dry_run, lambda target: nominate_peers(
            target, stocks, owner_email=owner.lower(), model_bin=args.agy_bin, model=args.model,
            check=source_check(sources, today=today)))
        if args.sql_output and not args.dry_run:
            args.sql_output.parent.mkdir(parents=True, exist_ok=True)
            args.sql_output.write_text(result["sql"], encoding="utf-8")
        log = {"schema": "watch-nominate/v1", "dry_run": args.dry_run, "candidates": [s["code"] for s in stocks],
               "stocks": result["stocks"]}
        if args.log_output:
            args.log_output.parent.mkdir(parents=True, exist_ok=True)
            args.log_output.write_text(json.dumps(log, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"candidates": log["candidates"],
                          "outcomes": {item["code"]: item["outcome"] for item in result["stocks"]}}, ensure_ascii=False))
        return 0
    if args.command == "authorize":
        verdict = authorize(args.evidence, args.edition)
        if args.github_output:
            with args.github_output.open("a", encoding="utf-8") as out:
                out.write("allowed=" + ("true" if verdict["allowed"] else "false") + "\n")
        print(json.dumps(verdict, ensure_ascii=False))
        return 0
    if args.command == "watchlist":
        from watch_news import _wrangler_query
        rows = _wrangler_query(watchlist_sql(os.environ.get("OWNER_EMAIL", "")))
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"watched": len(rows)}, ensure_ascii=False))
        return 0

    import licensed_inputs
    from watch_news import _wrangler_query
    watchlist = json.loads(args.watchlist.read_text(encoding="utf-8"))
    common = dict(watchlist=watchlist, ledger=args.ledger, forecast_ledger=args.forecast_ledger,
                  method_path=args.method, model_bin=args.agy_bin, model=args.model,
                  # J 票：富果金鑰 FUGLE_API_KEY；新聞與國外同類股從網站資料庫唯讀查（和關注清單同一個連線）。
                  source=LocalSource(fugle=licensed_inputs.fugle_from_env(), query=_wrangler_query,
                                     owner_email=os.environ.get("OWNER_EMAIL", "")),
                  dry_run=args.dry_run, sql_output=args.sql_output, log_output=args.log_output,
                  call_budget=args.call_budget)
    if args.edition == "morning":
        if not args.session_date:
            parser.error("晨間版要給 --session-date")
        if args.as_of and args.as_of != args.session_date:
            parser.error("晨間版的 --as-of 就是要重播的開盤日，必須等於 --session-date")
        from level_signals import ensure_holiday_calendar  # 證交所官方休市日曆（工作集裡有快取）
        log = run_morning(args.analysis, session_date=args.session_date, replay=bool(args.as_of),
                          holidays=ensure_holiday_calendar(args.session_date), **common)
    else:
        log = run_evening(args.analysis, as_of=args.as_of, **common)
    summary = {key: log.get(key) for key in ("edition", "base_date", "session_date", "dry_run", "outcome",
                                             "reconciliation", "backfilled_misses", "official_since")}
    summary["stocks"] = [{key: item.get(key) for key in ("code", "outcome", "kind", "status", "miss_reason")}
                         for item in log.get("stocks", [])]
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
