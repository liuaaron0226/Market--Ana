"""大盤預測的執行程式：晚間版（C 票第 6、7 片）在每日更新成功發布之後跑；晨間版（D 票第 10 片）在下一個
開盤日的早上、開盤前跑。

輸入是每日更新剛發布的大盤資料檔（``data/daily-update-state/last-market-pack/analysis-data.js``，就是網站畫圖用的
那一份）與方法稿；晚間版做的事依序是：
1. 從大盤資料整理一份當天輸入（加權指數日 K、法人與期貨、現行短中期走勢標籤、各項資料截止日、方法稿全文）；
2. 替帳本裡所有還有未到期格的預測對帳一次（先對帳，今天到期的結果才進得了今天這份的教訓層）；
3. 交給預測帳本寫一份晚間版（呼叫模型、驗證、鎖定或記漏記；相似盤勢與教訓兩層也在帳本裡算）；
4. 輸出給網站資料庫的指令檔與一份執行紀錄。

晨間版：基準日是開盤日之前的最後一個台股交易日（證交所休市日曆推算，且日 K 要真的有那一天；沒有就不寫、
不記漏記，那天之後若有日 K 出現，下一次晨間執行補記漏記）；不對帳（對帳只接在每日更新發布之後）；過去整個早上沒跑的晨間版補記漏記。兩版都可帶海外層
（``overseas_inputs``：先由排程抓好原件，這裡依版本的截止規則整理）；原件在紀錄寫成之後才放進帳本 ``sources/``。

乾跑（``dry_run``）只在暫存的帳本副本上寫，不碰真的帳本、不輸出資料庫指令檔，把模型挑的線、落點與理由寫進
執行紀錄給本人看。乾跑可以用 ``as_of`` 重播已發布過的某一天（資料截到那天、時鐘停在那天收盤後）。
"""
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import forecast_ledger as fl
import foreign_flow
import foreign_futures
import gemini_models
import market_layers
import market_trend
import overseas_inputs as oi
from level_signals import trading_day_status

SCHEMA = "forecast-run/v1"
# 本人 2026-11-17：大盤預測交給 Argon（模型名稱只在 gemini_models 改；本人已訂閱的 Google AI Pro，不新增付費服務）。
MODEL_DEFAULT = gemini_models.FORECAST
# 重播乾跑的時鐘：那一天收盤、每日更新發布之後。
REPLAY_CLOCK = "17:30:00"
# 重播晨間版乾跑的時鐘：開盤日早上、期限（08:45）之前。
MORNING_REPLAY_CLOCK = "07:30:00"
CHIP_DAYS = 5
ROOT = Path(__file__).resolve().parent
# 外資層（G 票）讀的資料：工作集裡的上市法人資料庫、個股日線與外資台指期部位，加上 seeds/ 的起始資料。
DATA_ROOT = ROOT / "data"
SEEDS_ROOT = ROOT / "seeds"


def load_analysis(path: Path | str) -> dict[str, Any]:
    """讀 ``window.MARKET_ANALYSIS = {...};``。"""
    text = Path(path).read_text(encoding="utf-8").strip()
    marker = text.find("=")
    if marker < 0:
        raise ValueError(f"{path} 不是 window.* = JSON 格式")
    return json.loads(text[marker + 1:].strip().removesuffix(";").strip())


def _until(rows: Any, day: str) -> list[dict[str, Any]]:
    return [row for row in rows or [] if str(row.get("date") or "") <= day]


def _billion(value: Any) -> float | None:
    return None if value is None else round(float(value) / 100_000_000, 2)


def _trend(daily: list[dict[str, Any]], institutional: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """現行短中長期走勢（網站同一套 market_trend）：標籤給固定規則，指標數值給模型看。"""
    periods = market_trend.assess_market_trends(daily, institutional)["periods"]
    labels = {period["key"]: (None if period["state"] == "insufficient" else period["conclusion"]) for period in periods}
    indicators = [{"key": period["key"], "state": period["state"], "conclusion": period["conclusion"],
                   "metrics": period["metrics"]} for period in periods]
    return labels, indicators


def _error(error: Exception) -> dict[str, str]:
    """``kind`` 進模型輸入（各機器一樣）；``detail`` 可能有本機路徑，只進執行紀錄。"""
    return {"kind": type(error).__name__, "detail": str(error)}


def load_foreign_sources(data_root: Path | str | None, seeds_root: Path | str = SEEDS_ROOT) -> dict[str, Any]:
    """外資層要的兩份序列（現貨每日彙總、期貨淨部位）。讀不到或原件不符時那一份是空的，原因記在 errors。

    ``data_root`` 是 None 時什麼都不讀（外資讀數全部標 missing），測試用。"""
    # 那斯達克（nasdaq）、美元指數（dollar_index）、美元兌台幣（usdtwd）由 D 票的海外資料接進這裡，
    # 每個都是 [{"date", "close"}]；沒接上之前是空的，讀數 1、4 標 missing。
    # nasdaq_in_prompt：本人 2026-11-16 決定 EODHD 的那斯達克也交給模型（D 票第 8 片）；整合時接
    # overseas_inputs.LICENSES["eodhd"]["in_prompt"]，改成 False 就只存進紀錄、不交。
    sources: dict[str, Any] = {"daily_flow": [], "futures": [], "proxy_daily": [], "nasdaq": [], "dollar_index": [],
                               "usdtwd": [], "nasdaq_in_prompt": oi.LICENSES["eodhd"]["in_prompt"], "errors": {}}
    if data_root is None:
        sources["errors"] = {name: {"kind": "no_data_root", "detail": "沒有指定資料位置"}
                             for name in ("daily_flow", "futures")}
        return sources
    root = Path(data_root)
    stock_db = root / "stock-history/stock-history.sqlite"
    try:
        sources["daily_flow"] = foreign_flow.load_daily_flow(
            root / "listed-institutional/listed_institutional.sqlite", stock_db,
            seed=Path(seeds_root) / foreign_flow.FLOW_SEED.name)
    except Exception as error:  # noqa: BLE001 — 讀不到就讓讀數標 missing，預測照寫
        sources["errors"]["daily_flow"] = _error(error)
    try:
        if stock_db.exists():
            sources["proxy_daily"] = foreign_flow.proxy_daily_from_stock_db(stock_db)
    except Exception as error:  # noqa: BLE001
        sources["errors"]["proxy_daily"] = _error(error)
    try:
        sources["futures"] = foreign_futures.positions(root / "foreign-futures",
                                                       Path(seeds_root) / foreign_futures.SEED.name)
    except Exception as error:  # noqa: BLE001
        sources["errors"]["futures"] = _error(error)
    return sources


def _foreign_layer(daily: list[dict[str, Any]], base_date: str, sources: Mapping[str, Any], *,
                   edition: str = "evening", us_before: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """回傳（交給模型的外資層, 只存進紀錄的部分）。"""
    flow = [row for row in sources.get("daily_flow") or [] if row["date"] <= base_date]
    futures = [row for row in sources.get("futures") or [] if row["date"] <= base_date]
    layer = foreign_flow.build_layer(
        base_date=base_date, index_daily=daily, daily_flow=flow, futures_positions=futures,
        proxy_daily=sources.get("proxy_daily") or [], nasdaq=sources.get("nasdaq") or [],
        dollar_index=sources.get("dollar_index") or [], usdtwd=sources.get("usdtwd") or [], edition=edition,
        us_before=us_before,
        nasdaq_in_prompt=bool(sources.get("nasdaq_in_prompt")),
        sources={"daily_flow_to": flow[-1]["date"] if flow else None,
                 "futures_to": futures[-1]["date"] if futures else None,
                 "errors": {name: error["kind"] for name, error in (sources.get("errors") or {}).items()}})
    return layer, layer.pop("record_only")


def _with_overseas_series(sources: Mapping[str, Any], overseas: Mapping[str, Any] | None) -> dict[str, Any]:
    """D 票的海外序列接進外資讀數 1、4（兩票 2026-11-16 對齊）：那斯達克、美元指數、美元兌台幣。

    那斯達克算出來的讀數交不交給模型，照海外層的判斷（條款允許而且當天抓得到），沒有海外層時照 EODHD 的條款。"""
    merged = dict(sources)
    merged.setdefault("nasdaq_in_prompt", oi.LICENSES["eodhd"]["in_prompt"])
    if not overseas:
        return merged
    series = overseas.get("series") or {}
    for key in ("nasdaq", "dollar_index", "usdtwd"):
        if series.get(key):
            merged[key] = series[key]
    in_prompt = overseas.get("series_in_prompt") or {}
    if "nasdaq" in in_prompt:
        merged["nasdaq_in_prompt"] = bool(in_prompt["nasdaq"])
    return merged


def build_day_input(analysis: Mapping[str, Any], method_text: str,
                    overseas: Mapping[str, Any] | None = None,
                    foreign_sources: Mapping[str, Any] | None = None, *,
                    edition: str = "evening", session_date: str | None = None,
                    market_sources: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """把已發布的大盤資料整理成帳本要的一份當天輸入；基準日是日 K 的最後一天。

    ``overseas`` 是 ``overseas_inputs.build_layer`` 的結果：交給模型的部分進輸入，狀態、缺漏、條款不允許交給模型的
    數字與原件清單只進紀錄。沒給就是海外各項都缺（照樣寫，標明缺漏）。
    外資層（G 票）一定附上；沒給 ``foreign_sources`` 時現貨與期貨讀數標 missing。海外層的那斯達克、美元指數、
    美元兌台幣序列接進外資讀數 1、4；晨間版的那斯達克只看開盤日之前收盤的那一晚。
    I 票五層（大盤融資融券、櫃買指數、產業成交比重、選股快照與資金輪動、OFR 金融壓力）由 ``market_sources``
    （``market_layers.load_sources`` 的結果）算；沒給就五層都缺，缺的層另記進 missing_inputs（不擋整份）。
    """
    daily = list(analysis.get("daily") or [])
    institutional = list(analysis.get("institutional") or [])
    futures = list(analysis.get("futures") or [])
    base_date = daily[-1]["date"] if daily else None
    labels, indicators = _trend(daily, institutional) if daily else ({}, [])
    sources = _with_overseas_series(foreign_sources or {}, overseas)
    layer, record_only = (_foreign_layer(daily, base_date, sources, edition=edition, us_before=session_date)
                          if base_date else (None, {}))
    extra = (market_layers.build_layers(market_sources or {}, base_date=base_date, index_daily=daily, edition=edition,
                                        session_date=session_date) if base_date else {})
    fields = _overseas_fields(overseas, foreign_record_only=record_only)
    fields["missing_inputs"] = list(fields.get("missing_inputs") or []) + [
        {"key": name, "name": market_layers.TITLES[name], "status": "missing", "reason": value.get("reason")}
        for name, value in extra.items() if value.get("status") == "missing"]
    return {
        "base_date": base_date,
        "index_daily": [{key: row.get(key) for key in ("date", "open", "high", "low", "close", "amount")} for row in daily],
        "trend_labels": labels,
        "indicators": indicators,
        "chips": {
            "institutional_billion": [
                {"date": row["date"], "foreign": _billion((row.get("foreign") or {}).get("net")),
                 "trust": _billion((row.get("trust") or {}).get("net")),
                 "dealer": _billion((row.get("dealer") or {}).get("net"))}
                for row in institutional[-CHIP_DAYS:]],
            "futures_foreign_net_contracts": [
                {"date": row["date"], "net": row.get("foreign_oi_net_contracts")} for row in futures[-CHIP_DAYS:]],
        },
        "data_cutoffs": {
            "index": base_date,
            "institutional": institutional[-1]["date"] if institutional else None,
            "futures": futures[-1]["date"] if futures else None,
        },
        "foreign_flow": layer,
        **extra,
        "method_text": method_text,
        # 只存進紀錄、不交給模型的內容：外資讀數放 foreign_flow、海外放 overseas（2026-11-16 兩票對齊）。
        **fields,
    }


def _overseas_fields(layer: Mapping[str, Any] | None, *,
                     foreign_record_only: Mapping[str, Any] | None = None) -> dict[str, Any]:
    record_only: dict[str, Any] = {"foreign_flow": dict(foreign_record_only)} if foreign_record_only else {}
    if layer is None:
        return {"overseas": None,
                "missing_inputs": [{"key": "overseas", "status": "missing", "reason": "這一份沒有海外資料"}],
                **({"record_only": record_only} if record_only else {})}
    fields = {"overseas": layer["prompt"], "overseas_cutoffs": layer["cutoffs"], "overseas_status": layer["status"],
              "missing_inputs": layer["missing"], "sources": layer["sources"]}
    if layer.get("withheld"):
        # 和 G 票共用 record_only：海外放 overseas、外資讀數放 foreign_flow（2026-11-16 兩票對齊）。
        record_only["overseas"] = layer["withheld"]
    if record_only:
        fields["record_only"] = record_only
    return fields


def _layer(overseas_dir: Path | str | None, *, edition: str, base_date: str | None,
           session_date: str | None) -> dict[str, Any] | None:
    if overseas_dir is None or not base_date:
        return None
    return oi.build_layer(overseas_dir, edition=edition, base_date=base_date, session_date=session_date)


def _with_archive_names(data: dict[str, Any], overseas_dir: Path | str | None, ledger: Path) -> dict[str, Any]:
    """紀錄裡的原件清單加上放進帳本後的檔名（先算名字，紀錄寫成了才真的寫原件）。"""
    if overseas_dir is None or "sources" not in data:
        return data
    return {**data, "sources": oi.archive_sources(overseas_dir, ledger, write=False)}


def trim_to_day(analysis: Mapping[str, Any], day: str) -> dict[str, Any]:
    """已發布的大盤資料截到某一天（日 K、法人、期貨都只留那天以前），那天必須有日 K。乾跑重播與歷史重播共用。"""
    trimmed = {key: _until(analysis.get(key), day) for key in ("daily", "institutional", "futures")}
    if not trimmed["daily"] or trimmed["daily"][-1]["date"] != day:
        raise ValueError(f"已發布的日 K 裡沒有 {day}，不能重播")
    return {**analysis, **trimmed}


def _backfill_misses(analysis: Mapping[str, Any], method_text: str, ledger: Path, base_date: str, *,
                     model_bin: Path | str, model: str, clock: Callable[[], datetime],
                     edition: str = "evening", foreign_sources: Mapping[str, Any] | None = None,
                     official_since: str | None = None) -> list[str]:
    """帳本這一版最後一份之後、今天這一份之前，有日 K 卻沒有任何紀錄的基準日（整個時段沒跑）補記漏記。

    那些日子都已過期限，帳本不會呼叫模型，只鎖一筆漏記並寫下原因。帳本還沒有任何這一版的紀錄時不補（起點之前不算）。
    晨間版的開盤日是日 K 裡基準日的下一根，所以基準日要有下一根才補。晚間版只由晚間的執行補、晨間版只由晨間的
    執行補：兩支排程各自寫各自的檔案，不會同時寫到同一筆。
    """
    version = fl.input_version(model)  # 換模型＝新系列：舊模型的紀錄不算這一版的起點
    earlier = [record["base_date"] for record in fl.read_ledger(ledger)["forecasts"]
               if record["edition"] == edition and record["input_version"] == version]
    if not earlier:
        return []
    last = max(earlier)
    dates = [row["date"] for row in analysis.get("daily") or []]
    days = []
    for index, day in enumerate(dates):
        if not last < day < base_date:
            continue
        session = None
        if edition == "morning":
            if index + 1 >= len(dates):
                continue
            session = dates[index + 1]
        fl.write_forecast(ledger, build_day_input(trim_to_day(analysis, day), method_text, None, foreign_sources,
                                          edition=edition, session_date=session), edition=edition,
                          model_bin=model_bin, model=model, now=clock, session_date=session,
                          official_since=official_since)
        days.append(day)
    return days


def _market_log(data: Mapping[str, Any], sources: Mapping[str, Any]) -> dict[str, Any]:
    """執行紀錄：I 票五層哪幾層缺、讀資料時的錯誤種類。"""
    return {"market_layers_missing": [name for name in market_layers.LAYER_NAMES
                                      if (data.get(name) or {}).get("status") != "ok"],
            "market_layers_errors": sources.get("errors")}


def _report(record: Mapping[str, Any]) -> dict[str, Any]:
    """執行紀錄裡放這一筆的內容，但不放完整輸入（方法稿與日 K 很大，原件已存在帳本）。"""
    return {key: value for key, value in record.items() if key != "input"}


def _log_result(log: dict[str, Any], result: Mapping[str, Any], *, overseas_dir: Path | str | None, ledger: Path,
                dry_run: bool) -> None:
    record = result.get("record")
    log["outcome"] = result["outcome"]
    if result.get("detail"):
        log["detail"] = result["detail"]
    if record:
        log.update(kind=record["kind"], forecast_id=record["forecast_id"], locked_at=record.get("locked_at"),
                   miss_reason=record.get("miss_reason"), miss_detail=record.get("miss_detail"),
                   attempts=record.get("attempts", []), missing_inputs=record.get("missing_inputs"),
                   record=_report(record))
        model_input = record.get("input") or {}
        log["experience"] = {"similar_today": (model_input.get("similar_regimes") or {}).get("today"),
                             "lessons_counts": (model_input.get("lessons") or {}).get("counts")}
    if result["outcome"] == "written" and overseas_dir is not None and not dry_run:
        oi.archive_sources(overseas_dir, ledger)  # 紀錄寫成了，原件才進帳本（只新增）


def _write_sql(ledger: Path | str, daily: list[dict[str, Any]], clock: Callable[[], datetime],
               sql_output: Path | str, official_since: str | None) -> None:
    published_at = clock().astimezone(fl.TAIPEI).isoformat()
    Path(sql_output).parent.mkdir(parents=True, exist_ok=True)
    Path(sql_output).write_text(fl.d1_sql(ledger, daily, published_at=published_at, official_since=official_since),
                                encoding="utf-8")


def _finish_log(log: dict[str, Any], clock: Callable[[], datetime], log_output: Path | str | None) -> dict[str, Any]:
    log["finished_at"] = clock().astimezone(fl.TAIPEI).isoformat()
    if log_output:
        Path(log_output).parent.mkdir(parents=True, exist_ok=True)
        Path(log_output).write_text(json.dumps(log, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return log


def _write(ledger: Path | str, data: Mapping[str, Any], *, dry_run: bool, **kwargs: Any) -> dict[str, Any]:
    """寫進帳本；乾跑時寫在暫存的副本上。"""
    with tempfile.TemporaryDirectory() as scratch:
        target = Path(ledger)
        if dry_run:
            target = Path(scratch) / "ledger"
            if Path(ledger).is_dir():
                shutil.copytree(ledger, target)
            fl.reconcile(target, data["index_daily"])  # 乾跑也先對帳（E 票），但只對暫存副本
        return fl.write_forecast(target, data, **kwargs)


def run_evening(
    analysis_path: Path | str,
    *,
    ledger: Path | str,
    method_path: Path | str,
    model_bin: Path | str,
    model: str = MODEL_DEFAULT,
    now: Callable[[], datetime] | None = None,
    date: str | None = None,
    dry_run: bool = False,
    as_of: str | None = None,
    sql_output: Path | str | None = None,
    log_output: Path | str | None = None,
    timeout_seconds: float = 360,
    overseas_dir: Path | str | None = None,
    data_root: Path | str | None = None,
    seeds_root: Path | str = SEEDS_ROOT,
    official_since: str | None | object = fl.OFFICIAL_SINCE_DEFAULT,
) -> dict[str, Any]:
    if as_of and not dry_run:
        raise ValueError("重播某一天（as_of）只能在乾跑使用，正式帳本不補寫過去的預測")
    official_since = fl.official_since(official_since)
    analysis = load_analysis(analysis_path)
    clock = now
    if as_of:
        analysis = trim_to_day(analysis, as_of)
        frozen = datetime.fromisoformat(f"{as_of}T{REPLAY_CLOCK}").replace(tzinfo=fl.TAIPEI)
        clock = lambda: frozen  # noqa: E731
    clock = clock or (lambda: datetime.now(fl.TAIPEI))
    method_text = Path(method_path).read_text(encoding="utf-8")
    daily = analysis.get("daily") or []
    layer = _layer(overseas_dir, edition="evening", base_date=daily[-1]["date"] if daily else None, session_date=None)
    foreign_sources = load_foreign_sources(data_root, seeds_root)
    market_sources = market_layers.load_sources(data_root, analysis_path,
                                                base_date=daily[-1]["date"] if daily else None)
    data = _with_archive_names(build_day_input(analysis, method_text, layer, foreign_sources,
                                               market_sources=market_sources), overseas_dir, Path(ledger))
    base_date = data["base_date"]
    log: dict[str, Any] = {
        "schema": SCHEMA, "edition": "evening", "dry_run": dry_run, "base_date": base_date, "model": model,
        "started_at": clock().astimezone(fl.TAIPEI).isoformat(), "backfilled_misses": [],
        "foreign_flow_missing": (data.get("foreign_flow") or {}).get("missing"),
        "foreign_flow_errors": foreign_sources.get("errors"),
        **_market_log(data, market_sources),
    }

    if date and date != base_date:
        # 手動指定的日期不是剛發布的資料日：不交給帳本（帳本會把過了期限的日子鎖成漏記）。
        log.update(outcome="not_ready", detail=f"要求的日期 {date} 不是已發布的資料日 {base_date}")
    else:
        if not dry_run:
            # 先對帳再寫（E 票）：今天到期的格子要先對完，今天這份的教訓層才看得到。
            # 今天這份最早的到期日在明天，寫完之後不必再對一次。
            log["reconciliation"] = {key: len(value) for key, value in fl.reconcile(ledger, data["index_daily"]).items()}
            log["backfilled_misses"] = _backfill_misses(analysis, method_text, Path(ledger), base_date,
                                                        model_bin=model_bin, model=model, clock=clock,
                                                        foreign_sources=foreign_sources, official_since=official_since)
        result = _write(ledger, data, dry_run=dry_run, edition="evening", model_bin=model_bin, model=model,
                        now=clock, timeout_seconds=timeout_seconds, official_since=official_since)
        _log_result(log, result, overseas_dir=overseas_dir, ledger=Path(ledger), dry_run=dry_run)

    if not dry_run:
        if "reconciliation" not in log:  # 要求的日期不對、沒寫預測時，照樣替舊預測對帳
            log["reconciliation"] = {key: len(value) for key, value in fl.reconcile(ledger, data["index_daily"]).items()}
        if sql_output:
            _write_sql(ledger, data["index_daily"], clock, sql_output, official_since)
    return _finish_log(log, clock, log_output)


def previous_trading_day(day: str, holidays: set[str]) -> str:
    """休市日曆只涵蓋開盤日那一年；年初往回推到去年時，去年的休市日不在日曆裡（已知限制，年底最後一天通常開市）。"""
    current = datetime.fromisoformat(day).date()
    while True:
        current -= timedelta(days=1)
        if trading_day_status(current.isoformat(), holidays):
            return current.isoformat()


def run_morning(
    analysis_path: Path | str,
    *,
    ledger: Path | str,
    method_path: Path | str,
    model_bin: Path | str,
    session_date: str,
    holidays: set[str],
    model: str = MODEL_DEFAULT,
    now: Callable[[], datetime] | None = None,
    dry_run: bool = False,
    sql_output: Path | str | None = None,
    log_output: Path | str | None = None,
    timeout_seconds: float = 360,
    overseas_dir: Path | str | None = None,
    replay: bool = False,
    data_root: Path | str | None = None,
    seeds_root: Path | str = SEEDS_ROOT,
    official_since: str | None | object = fl.OFFICIAL_SINCE_DEFAULT,
) -> dict[str, Any]:
    """寫一份晨間版：開盤日 session_date 的早上、開盤前 15 分鐘之前鎖定；基準日是前一個台股交易日。

    休市日不寫。日 K 還沒有基準日（資料沒發布或臨時休市）時不寫、不記漏記；之後補記由下一次晨間執行做。
    不對帳。和同一基準日的晚間版互不相干：輸入裡沒有任何帳本內容。
    ``replay``（只能乾跑）：重播過去某個開盤日，時鐘停在那天 07:30。
    """
    if replay and not dry_run:
        raise ValueError("重播某個開盤日只能在乾跑使用，正式帳本不補寫過去的預測")
    official_since = fl.official_since(official_since)
    clock = now or (lambda: datetime.now(fl.TAIPEI))
    if replay:
        frozen = datetime.fromisoformat(f"{session_date}T{MORNING_REPLAY_CLOCK}").replace(tzinfo=fl.TAIPEI)
        clock = lambda: frozen  # noqa: E731
    log: dict[str, Any] = {
        "schema": SCHEMA, "edition": "morning", "dry_run": dry_run, "session_date": session_date, "model": model,
        "started_at": clock().astimezone(fl.TAIPEI).isoformat(), "backfilled_misses": [],
    }
    if not trading_day_status(session_date, holidays):
        log.update(outcome="holiday", base_date=None, detail=f"{session_date} 不是台股交易日，不寫晨間版")
        return _finish_log(log, clock, log_output)

    base_date = previous_trading_day(session_date, holidays)
    analysis = load_analysis(analysis_path)
    # 開盤日之後才發布的資料（例如當天傍晚重跑）不能讓晨間版看到。
    analysis = {**analysis, **{key: _until(analysis.get(key), base_date) for key in ("daily", "institutional", "futures")}}
    method_text = Path(method_path).read_text(encoding="utf-8")
    layer = _layer(overseas_dir, edition="morning", base_date=base_date, session_date=session_date)
    # 外資讀數（G 票）：晨間版的那斯達克只看開盤日之前收盤的那一晚（us_before＝開盤日）。
    foreign_sources = load_foreign_sources(data_root, seeds_root)
    market_sources = market_layers.load_sources(data_root, analysis_path, base_date=base_date, edition="morning",
                                                session_date=session_date)
    data = _with_archive_names(build_day_input(analysis, method_text, layer, foreign_sources, edition="morning",
                                               session_date=session_date, market_sources=market_sources),
                               overseas_dir, Path(ledger))
    log["foreign_flow_missing"] = (data.get("foreign_flow") or {}).get("missing")
    log["foreign_flow_errors"] = foreign_sources.get("errors")
    log.update(_market_log(data, market_sources))
    log["base_date"] = base_date

    if not dry_run:
        log["backfilled_misses"] = _backfill_misses(analysis, method_text, Path(ledger), base_date, model_bin=model_bin,
                                                    model=model, clock=clock, edition="morning",
                                                    foreign_sources=foreign_sources, official_since=official_since)
    if data["base_date"] != base_date:
        # 日 K 停在更早的日子：基準日的資料還沒發布，或那天臨時休市（颱風假不在休市日曆裡）。交易日以日 K 實際
        # 有資料為準，所以這裡不寫也不記漏記；之後那天的日 K 出現了，下一次晨間執行會把它補記成漏記。
        log.update(outcome="not_ready", detail=f"日 K 還沒有基準日 {base_date}（資料沒發布或臨時休市）")
        return _finish_log(log, clock, log_output)
    result = _write(ledger, data, dry_run=dry_run, edition="morning", session_date=session_date, model_bin=model_bin,
                    model=model, now=clock, timeout_seconds=timeout_seconds, official_since=official_since)
    _log_result(log, result, overseas_dir=overseas_dir, ledger=Path(ledger), dry_run=dry_run)
    if not dry_run and sql_output:
        _write_sql(ledger, data["index_daily"], clock, sql_output, official_since)
    return _finish_log(log, clock, log_output)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="寫一份大盤預測（晚間版並對帳；或晨間版）")
    parser.add_argument("--edition", choices=["evening", "morning"], default="evening")
    parser.add_argument("--analysis", type=Path, required=True, help="已發布的 analysis-data.js")
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--method", type=Path, required=True, help="方法稿（MARKET_METHOD.md）")
    parser.add_argument("--agy-bin", type=Path, required=True)
    parser.add_argument("--model", default=MODEL_DEFAULT)
    parser.add_argument("--date", help="晚間版：要求的資料日；必須等於已發布資料的最後一天")
    parser.add_argument("--session-date", help="晨間版：開盤日（台北今天）")
    parser.add_argument("--dry-run", action="store_true", help="只在暫存副本上寫，不碰帳本、不輸出資料庫指令檔")
    parser.add_argument("--as-of", help="乾跑時重播已發布過的某一天（只有晚間版）")
    parser.add_argument("--overseas-dir", type=Path, help="overseas_inputs.py fetch 抓好的原件資料夾")
    parser.add_argument("--sql-output", type=Path)
    parser.add_argument("--log-output", type=Path)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT,
                        help="外資層讀的工作集資料夾（上市法人、個股日線、外資台指期部位）")
    args = parser.parse_args(argv)
    common = dict(ledger=args.ledger, method_path=args.method, model_bin=args.agy_bin, model=args.model,
                  dry_run=args.dry_run, sql_output=args.sql_output, log_output=args.log_output,
                  overseas_dir=args.overseas_dir, data_root=args.data_root)
    if args.edition == "morning":
        if not args.session_date or args.date:
            parser.error("晨間版要給 --session-date，不接受 --date")
        if args.as_of and args.as_of != args.session_date:
            parser.error("晨間版的 --as-of 就是要重播的開盤日，必須等於 --session-date")
        from level_signals import ensure_holiday_calendar  # 證交所官方休市日曆（工作集裡有快取）
        log = run_morning(args.analysis, session_date=args.session_date, replay=bool(args.as_of),
                          holidays=ensure_holiday_calendar(args.session_date), **common)
    else:
        log = run_evening(args.analysis, date=args.date, as_of=args.as_of, **common)
    summary = {key: log.get(key) for key in ("edition", "base_date", "session_date", "dry_run", "outcome", "kind",
                                             "miss_reason", "detail", "reconciliation", "missing_inputs")}
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
