"""大盤事前預測多的五層輸入（I 票；本人 2026-11-18 晚「更多整合所有網站可以獲取的資料」→ 先補「大盤＋自選股判斷」）。

每一層都由程式算好、附資料日與統計期間和一段說明，Gemini 只讀不算；缺資料時那一層是
``{"status": "missing", "reason": …}``，不擋整份預測。資料全部是網站每天已經抓好、存在工作集裡的官方（或已核准）來源，
這裡不連網：

- ``market_margin``：大盤融資融券（證交所信用交易總表，首頁籌碼卡同一份 chips-data.js）。大盤維持率沒有來源，明寫不給。
- ``otc_index``：櫃買指數日線近 60 根（櫃買中心，otc_market.sqlite）與近 5／20／60 日和加權指數比強弱。
- ``sector_share``：各產業占當日成交金額的比重與近 5 日變化（每日更新封存的產業資金流向快照）。
- ``screens``：資金輪動四象限與籌碼選股排行前五（每日更新封存的選股快照；只放法人排行，分點那幾頁本來就沒有資料）。
- ``ofr_stress``：OFR 金融壓力指數的信用分項（水位訊號每日存的 OFR 原件；值、近 20 日變化、近三年分位）。

富果、新聞來源、國外同類股不在這裡（授權照舊不進 Gemini）。
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

SCHEMA = "market-layers/v1"
VERSION = "market-layers-v1"
LAYER_NAMES = ("market_margin", "otc_index", "sector_share", "screens", "ofr_stress")
# 網站「缺漏」那一列顯示的中文名（missing_inputs 的 name）。
TITLES = {"market_margin": "大盤融資融券", "otc_index": "櫃買指數", "sector_share": "產業成交比重",
          "screens": "選股快照與資金輪動", "ofr_stress": "OFR 金融壓力指數"}
OTC_RECENT_BARS = 60
VERSUS_DAYS = (5, 20, 60)
SHARE_COMPARE_DAYS = 5
MARGIN_COMPARE_DAYS = 5
SCREEN_TOP = 5
# 交給模型的選股快照（各取前五）：外資、投信的當日與近 5 日買超、賣超排行，外資、投信連續買超、賣超，
# 資金流入族群裡位階低於大盤的個股（另給總檔數）。分點與人工名單頁本來就沒有資料，不放。
SCREEN_KINDS = (tuple(f"{who}_net_rank-{days}d-{side}" for who in ("foreign", "trust") for days in (1, 5)
                      for side in ("buy", "sell"))
                + tuple(f"{who}_streak-1d-{side}" for who in ("foreign", "trust") for side in ("buy", "sell"))
                + ("rotation_relative_value-1d-buy",))
QUADRANT_ORDER = ("leading", "weakening", "improving", "lagging")
OFR_MAX_AGE_DAYS = 7      # OFR 最後一筆比截止日早超過這麼多天（原件沒更新）就當作缺

NOTES = {
    "market_margin": "大盤融資融券餘額（上市，證交所信用交易總表）。融資餘額是散戶用槓桿的程度、融券是放空的張數，"
                     "只描述籌碼狀態；融資增加或減少都不代表之後會漲或跌，不能單獨決定方向。"
                     "交易所不公布大盤融資維持率，這一層也沒有算，maintenance_ratio 一律是 null，不得自己推估或寫出維持率。",
    "otc_index": "櫃買指數（上櫃股票）日線與近 5／20／60 個交易日和加權指數的漲跌比較（gap_pp＝櫃買減加權，百分點）。"
                 "只描述中小型股相對大型權值股強或弱，不代表加權指數之後的方向。",
    "sector_share": "上市＋上櫃各官方產業別占當天全部產業成交金額合計的比重（%），和 5 個交易日前比的變化（百分點）。"
                    "成交比重上升只代表資金往那裡集中，不代表那個產業或大盤會漲。",
    "screens": "資金輪動是各產業成交額相對近 20 日平均（高或低）與近 5 日加速或減速分成的四象限；排行是外資、投信當日與"
               "近 5 日買賣超張數的前五名（常有 ETF，因為申購贖回也算進買賣超）。這些是籌碼與資金流向的描述，"
               "不是加權指數方向的理由，不得把個股名單或象限直接當成之後漲跌的依據。",
    "ofr_stress": "美國財政部金融研究辦公室（OFR）金融壓力指數的信用分項：0 是長期平均，正數代表信用市場壓力高於平均；"
                  "change_20d 是近 20 筆的變化，percentile_3y 是這個變化在近三年的分位（歷史不足時是 null）。"
                  "美國資料，資料日比台股早一到兩天；它是外部環境的背景，不能單獨決定方向。",
}

# 參數與說明整組算進預測帳本的輸入版本（forecast_ledger.PARAMETERS["market_layers"]）：說明改一個字也換版本。
PARAMETERS: dict[str, Any] = {
    "version": VERSION, "layers": list(LAYER_NAMES), "otc_recent_bars": OTC_RECENT_BARS,
    "versus_days": list(VERSUS_DAYS), "share_compare_days": SHARE_COMPARE_DAYS,
    "margin_compare_days": MARGIN_COMPARE_DAYS, "screen_top": SCREEN_TOP, "screen_kinds": list(SCREEN_KINDS),
    "ofr": {"series": "OFR FSI Credit", "change_days": 20, "percentile_years": 3, "max_age_days": OFR_MAX_AGE_DAYS},
    "notes": NOTES,
}


def _missing(reason: str) -> dict[str, Any]:
    return {"status": "missing", "reason": reason}


def _round(value: float | None, places: int = 2) -> float | None:
    return None if value is None else round(value + 0.0, places)


def _pct(now: float, then: float) -> float:
    return (now / then - 1) * 100


# ---- 大盤融資融券 ----

def margin_layer(rows: Sequence[Mapping[str, Any]], base_date: str) -> dict[str, Any]:
    """``rows``：chips-data.js 的 margin（date、margin_balance_billion 億元、margin_units 張、short_units 張）。
    只看基準日（含）以前；融資晚上才公布，資料日可能比基準日早一天（same_day_as_base 標出來）。"""
    seen = sorted((row for row in rows if str(row.get("date") or "") <= base_date and
                   row.get("margin_balance_billion") is not None), key=lambda row: row["date"])
    if not seen:
        return _missing("沒有大盤融資融券資料")
    today = seen[-1]
    previous = seen[-2] if len(seen) >= 2 else None
    earlier = seen[-1 - MARGIN_COMPARE_DAYS] if len(seen) > MARGIN_COMPARE_DAYS else None
    balance = float(today["margin_balance_billion"])
    margin_units, short_units = today.get("margin_units"), today.get("short_units")
    return {
        "status": "ok", "data_date": today["date"], "same_day_as_base": today["date"] == base_date,
        "unit": {"balance": "億元", "units": "張"},
        "margin_balance_billion": balance,
        "change_1d_billion": _round(balance - previous["margin_balance_billion"]) if previous else None,
        "change_5d_billion": _round(balance - earlier["margin_balance_billion"]) if earlier else None,
        "change_5d_pct": _round(_pct(balance, earlier["margin_balance_billion"])) if earlier else None,
        "compared_with": earlier["date"] if earlier else None,
        "margin_units": margin_units, "short_units": short_units,
        "short_change_5d_units": (short_units - earlier["short_units"])
        if earlier and short_units is not None and earlier.get("short_units") is not None else None,
        "short_to_margin_pct": _round(short_units / margin_units * 100) if short_units is not None and margin_units else None,
        "maintenance_ratio": None,
        "source": "臺灣證券交易所 信用交易統計（MI_MARGN）",
        "note": NOTES["market_margin"],
    }


# ---- 櫃買指數 ----

def otc_layer(otc_rows: Sequence[Mapping[str, Any]], taiex_rows: Sequence[Mapping[str, Any]],
              base_date: str) -> dict[str, Any]:
    rows = sorted((row for row in otc_rows if str(row.get("date") or "") <= base_date and row.get("close") is not None),
                  key=lambda row: row["date"])
    if not rows or rows[-1]["date"] != base_date:
        return _missing("櫃買指數沒有基準日的收盤")
    taiex = {row["date"]: row["close"] for row in taiex_rows if row.get("close")}
    close, versus = rows[-1]["close"], []
    for days in VERSUS_DAYS:
        if len(rows) <= days:
            continue
        since = rows[-1 - days]["date"]
        if not taiex.get(since) or not taiex.get(base_date) or not rows[-1 - days]["close"]:
            continue
        otc_pct, taiex_pct = _pct(close, rows[-1 - days]["close"]), _pct(taiex[base_date], taiex[since])
        versus.append({"days": days, "since": since, "otc_pct": _round(otc_pct), "taiex_pct": _round(taiex_pct),
                       "gap_pp": _round(otc_pct - taiex_pct)})
    return {
        "status": "ok", "data_date": base_date, "unit": "點",
        "recent": [{key: row.get(key) for key in ("date", "open", "high", "low", "close")}
                   for row in rows[-OTC_RECENT_BARS:]],
        "versus_taiex": versus,
        "period": {"from": rows[-OTC_RECENT_BARS:][0]["date"], "to": base_date},
        "source": "證券櫃檯買賣中心 櫃買指數",
        "note": NOTES["otc_index"],
    }


# ---- 產業成交比重 ----

def _shares(entry: Mapping[str, Any]) -> tuple[dict[str, float], dict[str, float], float]:
    amounts = {bubble["name"]: float(bubble["amount_today"]) for bubble in entry.get("bubbles") or []
               if bubble.get("amount_today") is not None}
    total = sum(amounts.values())
    return amounts, ({name: amount / total * 100 for name, amount in amounts.items()} if total else {}), total


def _rotation_entry(batch: Mapping[str, Any] | None, base_date: str) -> tuple[list[dict[str, Any]], int] | None:
    if not batch or batch.get("data_date") != base_date:
        return None
    snapshots = list((batch.get("result") or {}).get("snapshots") or [])
    dates = [entry.get("date") for entry in snapshots]
    if base_date not in dates:
        return None
    return snapshots, dates.index(base_date)


def sector_share_layer(rotation: Mapping[str, Any] | None, base_date: str) -> dict[str, Any]:
    """``rotation``：每日更新封存的產業資金流向那一批（market_screens_publish，kind＝rotation），資料日要是基準日。"""
    found = _rotation_entry(rotation, base_date)
    if found is None:
        return _missing("沒有基準日的產業資金流向快照")
    snapshots, at = found
    amounts, shares, total = _shares(snapshots[at])
    if not total:
        return _missing("產業成交金額合計是零")
    earlier = snapshots[at - SHARE_COMPARE_DAYS] if at >= SHARE_COMPARE_DAYS else None
    old_shares = _shares(earlier)[1] if earlier else {}
    industries = [{"name": name, "share_pct": _round(share),
                   "share_5d_ago_pct": _round(old_shares.get(name)) if name in old_shares else None,
                   "change_pp": _round(share - old_shares[name]) if name in old_shares else None,
                   "amount_100m": _round(amounts[name], 1)}
                  for name, share in sorted(shares.items(), key=lambda item: (-item[1], item[0]))]
    return {"status": "ok", "data_date": base_date, "compared_with": earlier.get("date") if earlier else None,
            "total_amount_100m": _round(total, 1), "unit": {"share": "%", "change": "百分點", "amount": "億元"},
            "industries": industries, "source": "證交所／櫃買官方產業別與全日成交金額（本站計算）",
            "note": NOTES["sector_share"]}


# ---- 選股快照與資金輪動 ----

def screens_layer(rotation: Mapping[str, Any] | None, screens: Mapping[str, Mapping[str, Any]],
                  base_date: str) -> dict[str, Any]:
    """``screens``：{排行種類: 那一批}（kind 去掉 ``screen-`` 前綴）；資料日不是基準日的排行不放。"""
    found = _rotation_entry(rotation, base_date)
    rotation_part = None
    if found is not None:
        snapshots, at = found
        entry = snapshots[at]
        labels = {key: (value or {}).get("label", key)
                  for key, value in ((rotation or {}).get("result") or {}).get("quadrants", {}).items()}
        quadrants = {labels.get(key, key): [] for key in QUADRANT_ORDER}
        for bubble in entry.get("bubbles") or []:
            if bubble.get("quadrant") in QUADRANT_ORDER:
                quadrants[labels.get(bubble["quadrant"], bubble["quadrant"])].append(bubble["name"])
        rotation_part = {"quadrants": quadrants, "summary": (entry.get("summary") or {}).get("text"),
                         "recovery_signals": [item["name"] for item in entry.get("recovery_signals") or []]}
    ranks = []
    for kind in SCREEN_KINDS:
        batch = screens.get(kind)
        if not batch or batch.get("data_date") != base_date:
            continue
        result = batch.get("result") or {}
        if result.get("state") not in (None, "available"):
            continue
        top = []
        for item in (result.get("items") or [])[:SCREEN_TOP]:
            row = {"code": item.get("code"), "name": item.get("name"), "security_type": item.get("security_type")}
            if "metric_lots" in item:
                row["lots"] = None if item["metric_lots"] is None else int(round(item["metric_lots"]))
            if "streak_days" in item:
                row["streak_days"] = item["streak_days"]
            if "change_pct" in item:
                row["change_pct"] = _round(item.get("change_pct"))
            if "group" in item:
                row["group"] = item["group"]
            top.append(row)
        ranks.append({"kind": kind, "name": result.get("display_name"), "total": result.get("total"), "top": top})
    if rotation_part is None and not ranks:
        return _missing("沒有基準日的選股快照與資金輪動")
    return {"status": "ok", "data_date": base_date, "rotation": rotation_part, "ranks": ranks,
            "units": {"lots": "張（N 日買賣超加總）", "change_pct": "%（當日漲跌）"},
            "source": "證交所／櫃買三大法人與行情（本站計算）", "note": NOTES["screens"]}


# ---- OFR 金融壓力指數 ----

def ofr_layer(credit_rows: Sequence[Mapping[str, Any]], cutoff: str) -> dict[str, Any]:
    """``credit_rows``：``level_signals.parse_ofr_fsi_csv`` 的結果（date、credit）；只用 cutoff（含）以前的。
    值、20 筆變化與三年分位都照水位訊號同一個算法（``level_signals.build_international_features``）。"""
    from level_signals import build_international_features
    credit = build_international_features(cutoff, [], [], list(credit_rows))["credit"]
    if not credit:
        return _missing("OFR 金融壓力指數不足 21 筆")
    if credit["observed_at"] < (date.fromisoformat(cutoff) - timedelta(days=OFR_MAX_AGE_DAYS)).isoformat():
        return _missing("OFR 金融壓力指數太舊（原件沒有更新）")
    return {"status": "ok", "series": "OFR FSI Credit", "observed_at": credit["observed_at"],
            "value": _round(credit["value"], 3), "change_20d": _round(credit["change_20d"], 3),
            "percentile_3y": _round(credit["percentile"], 3) if credit.get("percentile") is not None else None,
            "source": "Office of Financial Research, OFR Financial Stress Index", "note": NOTES["ofr_stress"]}


def ofr_cutoff(base_date: str, edition: str, session_date: str | None) -> str:
    """晚間版只看基準日前一天（含）以前的美國資料；晨間版看開盤日前一天（含）以前。"""
    anchor = session_date if edition == "morning" and session_date else base_date
    return (date.fromisoformat(anchor) - timedelta(days=1)).isoformat()


# ---- 整合 ----

def build_layers(sources: Mapping[str, Any], *, base_date: str, index_daily: Sequence[Mapping[str, Any]],
                 edition: str, session_date: str | None = None) -> dict[str, dict[str, Any]]:
    """五層都在；``sources`` 缺哪一樣那一層就是 missing（原因寫在層裡）。"""
    errors = sources.get("errors") or {}

    def guarded(name: str, build) -> dict[str, Any]:
        if name in errors:
            return _missing(f"讀不到資料（{errors[name]}）")
        try:
            return build()
        except Exception as error:  # noqa: BLE001 — 一層算錯只讓那一層缺，預測照寫
            return _missing(f"計算失敗（{type(error).__name__}）")

    return {
        "market_margin": guarded("market_margin", lambda: margin_layer(sources.get("chips_margin") or [], base_date)),
        "otc_index": guarded("otc_index", lambda: otc_layer(sources.get("otc_index") or [], index_daily, base_date)),
        "sector_share": guarded("sector_share", lambda: sector_share_layer(sources.get("rotation"), base_date)),
        "screens": guarded("screens", lambda: screens_layer(sources.get("rotation"), sources.get("screens") or {},
                                                            base_date)),
        "ofr_stress": guarded("ofr_stress", lambda: ofr_layer(sources.get("ofr_credit") or [],
                                                              ofr_cutoff(base_date, edition, session_date))),
    }


# ---- 從工作集讀（排程用；測試直接給 sources） ----

CHIPS_FILE = "chips-data.js"
OTC_MARKET_DB = "otc-history/otc_market.sqlite"
SNAPSHOT_DIR = "market-screen-snapshots"
LEVEL_SIGNALS_DIR = "level-signals"
OFR_RAW = "ofr-fsi-credit.raw"


def _window_json(path: Path) -> Any:
    text = path.read_text(encoding="utf-8").strip()
    return json.loads(text[text.find("=") + 1:].strip().removesuffix(";").strip())


def _snapshot(root: Path, kind: str, day: str) -> dict[str, Any] | None:
    """選股快照：資料日那一批（market_screens_publish 的 <root>/<kind>/<資料日>.json）。"""
    path = root / kind / f"{day}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def load_sources(data_root: Path | str | None, analysis_path: Path | str | None, *,
                 base_date: str | None, edition: str = "evening", session_date: str | None = None) -> dict[str, Any]:
    """排程用：從工作集讀五層要的資料（不連網）。每一樣各自讀，讀不到的記進 errors（只記錯誤種類，不記路徑）。

    融資讀 analysis-data.js 旁邊的 chips-data.js。OFR 讀水位訊號（每天早上 07:30）存的原件，只用寫預測那時已經存在的
    那一份：晚間版是資料夾日期不晚於基準日的最新一份，晨間版（07:20 前後跑）是開盤日前一天（含）以前的。"""
    sources: dict[str, Any] = {"errors": {}}
    if data_root is None or base_date is None:
        sources["errors"] = {name: "no_data_root" for name in LAYER_NAMES}
        return sources
    root = Path(data_root)

    def read(name: str, fn) -> None:
        try:
            fn()
        except Exception as error:  # noqa: BLE001
            sources["errors"][name] = type(error).__name__

    def chips() -> None:
        path = Path(analysis_path).with_name(CHIPS_FILE) if analysis_path else None
        if path is None or not path.is_file():
            raise FileNotFoundError(CHIPS_FILE)
        sources["chips_margin"] = list(_window_json(path).get("margin") or [])

    def otc() -> None:
        import otc_market_data
        db = root / OTC_MARKET_DB
        if not db.is_file():
            raise FileNotFoundError(OTC_MARKET_DB)
        start = (date.fromisoformat(base_date) - timedelta(days=200)).isoformat()
        sources["otc_index"] = otc_market_data.index_range(db, start, base_date)

    def snapshots() -> None:
        store = root / SNAPSHOT_DIR
        sources["rotation"] = _snapshot(store, "rotation", base_date)
        sources["screens"] = {kind: batch for kind in SCREEN_KINDS
                              if (batch := _snapshot(store, f"screen-{kind}", base_date)) is not None}

    def ofr() -> None:
        from level_signals import parse_ofr_fsi_csv
        folder = root / LEVEL_SIGNALS_DIR
        limit = ((date.fromisoformat(session_date) - timedelta(days=1)).isoformat()
                 if edition == "morning" and session_date else base_date)
        days = sorted(path.parent.name for path in folder.glob(f"*/{OFR_RAW}")
                      if re.fullmatch(r"\d{4}-\d{2}-\d{2}", path.parent.name) and path.parent.name <= limit) \
            if folder.is_dir() else []
        if not days:
            raise FileNotFoundError(OFR_RAW)
        sources["ofr_credit"] = parse_ofr_fsi_csv((folder / days[-1] / OFR_RAW).read_bytes(), "9999-12-31")

    for name, fn in (("market_margin", chips), ("otc_index", otc), ("screens", snapshots), ("ofr_stress", ofr)):
        read(name, fn)
    if "screens" in sources["errors"]:
        sources["errors"]["sector_share"] = sources["errors"]["screens"]
    return sources
