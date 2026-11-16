"""把每日已算好的 Market--Ana 資料整理成 agy 可讀的精簡、可稽核 JSON。

本程式不連網、不抓資料、不改寫任何來源數字。輸入 SHA-256 以不含
``input_sha256`` 欄位的 canonical JSON 計算，供 D1 與稽核紀錄交叉核對。
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent
INPUT_SCHEMA = "ai-digest-input/v1"
PREFERRED_SCREENS = (
    "screen-foreign_net_rank-1d-buy",
    "screen-foreign_net_rank-1d-sell",
    "screen-trust_net_rank-1d-buy",
    "screen-trust_net_rank-1d-sell",
    "screen-foreign_streak-1d-buy",
    "screen-foreign_streak-1d-sell",
    "screen-trust_streak-1d-buy",
    "screen-trust_streak-1d-sell",
    "screen-rotation_relative_value-1d-buy",
)
STOCK_INDICATORS = (
    "atr14", "atr_pct", "d9", "high20_close", "k9", "low20_close",
    "low20_intraday", "ma5", "ma10", "ma20", "ma60", "ma120", "ma240",
    "macd_dea", "macd_dif",
)


def _read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _read_js_assignment(path: Path) -> Any:
    text = Path(path).read_text(encoding="utf-8").strip()
    marker = text.find("=")
    if marker < 0:
        raise ValueError(f"{path} 不是 window.* = JSON 格式")
    return json.loads(text[marker + 1 :].strip().removesuffix(";").strip())


def _tail(value: Any, count: int) -> list[Any]:
    return list(value[-count:]) if isinstance(value, list) else []


def _decoded(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


def _compact_compare(value: Any) -> Any:
    value = _decoded(value)
    if not isinstance(value, dict):
        return value
    # 圖表全序列很大；AI 解讀只需要已算好的期間數字與狀態。
    return {key: item for key, item in value.items() if key not in {"chart", "series"}}


def _portfolio_payload(path: Path, owner_email: str) -> dict[str, list[dict[str, Any]]]:
    raw = _read_json(path)
    if isinstance(raw, dict) and ("watchlist" in raw or "holdings" in raw):
        watchlist = list(raw.get("watchlist") or [])
        holdings = list(raw.get("holdings") or [])
    else:
        containers = raw if isinstance(raw, list) else [raw]
        rows: list[dict[str, Any]] = []
        for container in containers:
            if isinstance(container, dict) and isinstance(container.get("results"), list):
                rows.extend(container["results"])
        watchlist = [row for row in rows if row.get("kind") == "watchlist"]
        holdings = [row for row in rows if row.get("kind") == "holding"]

    owner = owner_email.strip().lower()
    watch_fields = ("code", "name", "market", "horizon")
    holding_fields = ("code", "name", "market", "shares", "average_cost", "last_price", "price_as_of")
    watchlist = [
        {key: row.get(key) for key in watch_fields if key in row}
        for row in watchlist if str(row.get("email", "")).lower() == owner
    ]
    holdings = [
        {key: row.get(key) for key in holding_fields if key in row}
        for row in holdings if str(row.get("email", "")).lower() == owner
    ]
    return {"watchlist": watchlist, "holdings": holdings}


def _stock_summary(payload: dict[str, Any]) -> dict[str, Any]:
    indicators = payload.get("indicators") or {}
    return {
        "name": payload.get("name"),
        "market": payload.get("market"),
        "kind": payload.get("kind"),
        "as_of": payload.get("as_of"),
        "quote": payload.get("quote"),
        "trend_classification": payload.get("trend_classification"),
        "levels": payload.get("levels"),
        "conditions": payload.get("conditions"),
        "defense": payload.get("defense"),
        "indicators": {key: indicators[key] for key in STOCK_INDICATORS if key in indicators},
        "reference": payload.get("reference"),
        "qa": payload.get("qa"),
    }


def _screen_summary(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    payload = _read_json(path)
    result = payload.get("result") or {}
    return {
        "kind": payload.get("kind") or path.parent.name,
        "display_name": result.get("display_name"),
        "data_dates": result.get("data_dates"),
        "state": result.get("state"),
        "items": list(result.get("items") or [])[:5],
    }


def _rotation_summary(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    payload = _read_json(path)
    snapshots = (payload.get("result") or {}).get("snapshots") or []
    if not snapshots:
        return None
    latest = snapshots[-1]
    bubbles = [
        {key: bubble.get(key) for key in ("name", "quadrant", "relative_pct", "x", "y", "amount_today", "amount_5d", "amount_20d")}
        for bubble in sorted(latest.get("bubbles") or [], key=lambda row: abs(float(row.get("y") or 0)), reverse=True)[:8]
    ]
    return {
        "date": latest.get("date"),
        "summary": latest.get("summary"),
        "quadrant_counts": latest.get("quadrant_counts"),
        "efficiency_ranking": list(latest.get("efficiency_ranking") or [])[:5],
        "recovery_signals": list(latest.get("recovery_signals") or [])[:5],
        "notable_bubbles": bubbles,
    }


def _holding_metrics(holdings: Iterable[dict[str, Any]], stocks: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for source in holdings:
        item = dict(source)
        code = str(item.get("code") or "")
        quote = (stocks.get(code) or {}).get("quote") or {}
        latest = quote.get("close", item.get("last_price"))
        try:
            shares = float(item["shares"])
            average = float(item["average_cost"])
            price = float(latest)
            market_value = shares * price
            cost_basis = shares * average
            item.update({
                "latest_price": price,
                "market_value": round(market_value, 2),
                "cost_basis": round(cost_basis, 2),
                "unrealized_pnl": round(market_value - cost_basis, 2),
                "return_pct": round((market_value - cost_basis) / cost_basis * 100, 2),
            })
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            item.update({"latest_price": latest, "market_value": None, "cost_basis": None,
                         "unrealized_pnl": None, "return_pct": None})
        result.append(item)
    return result


def canonical_sha(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def infer_data_date(stock_pack_path: Path, screens_root: Path) -> str:
    stock_date = str(_read_json(stock_pack_path).get("as_of") or "")
    screen_dates = {
        path.stem for path in Path(screens_root).glob("screen-*/*.json")
        if len(path.stem) == 10
    }
    if stock_date and stock_date in screen_dates:
        return stock_date
    raise ValueError("找不到個股分析與籌碼快照共同的資料日")


def _exchange_sections(data_date: str, market_dir: Path, screens_root: Path) -> dict[str, Any]:
    """大盤與籌碼兩段：站主與朋友共用同一份整理，朋友那份再拿掉非證交所／櫃買的欄位。"""
    market_dir = Path(market_dir)
    screens_root = Path(screens_root)
    analysis = _read_js_assignment(market_dir / "analysis-data.js")
    dashboard = _read_js_assignment(market_dir / "dashboard-data.js")
    chips = _read_js_assignment(market_dir / "chips-data.js")
    screen_summaries = []
    for kind in PREFERRED_SCREENS:
        item = _screen_summary(screens_root / kind / f"{data_date}.json")
        if item is not None:
            screen_summaries.append(item)
    rotation = _rotation_summary(screens_root / "rotation" / f"{data_date}.json")
    screen_codes = {
        str(item.get("code"))
        for screen in screen_summaries
        for item in screen.get("items", [])
        if item.get("code")
    }
    indicators = analysis.get("indicators") or {}
    return {
        "market_date": str((analysis.get("meta") or {}).get("cutoff_date") or dashboard.get("market_date") or ""),
        "screens": screen_summaries,
        "rotation": rotation,
        "screen_codes": screen_codes,
        "market": {
            "latest": dashboard.get("latest"),
            "thresholds": dashboard.get("thresholds"),
            "daily": _tail(analysis.get("daily"), 10),
            "institutional": _tail(analysis.get("institutional"), 5),
            "futures": _tail(analysis.get("futures"), 5),
            "sectors": _tail(analysis.get("sectors"), 5),
            "indicators": {"daily": _tail(indicators.get("daily"), 3)},
            "margin": _tail(chips.get("margin"), 5),
            "foreign": _tail(chips.get("foreign"), 5),
            "futures_chips": _tail(chips.get("futures"), 5),
        },
    }


# 朋友那份只放證交所、櫃買的官方開放資料（DECISIONS 第 5 題）：期貨部位來自期交所，不放。
FRIEND_INPUT_SCHEMA = "ai-digest-friend-input/v1"
NON_EXCHANGE_MARKET_FIELDS = ("futures", "futures_chips")
# dashboard-data.js 是人寫的劇本檔，以後可能多放國外指數：朋友那份只放列出的欄位（全是證交所資料算出來的）。
FRIEND_LATEST_FIELDS = (
    "close", "change", "change_percent", "low", "high", "previous_low", "turnover", "turnover_vs_20d",
    "avg_turnover_20d", "foreign_net", "investment_net", "electronic_percent", "financial_percent",
    "below_ma20", "ma20", "ma5",
)
FRIEND_THRESHOLD_FIELDS = ("upper", "lower")


def _only(value: Any, fields: tuple[str, ...]) -> Any:
    return {key: value[key] for key in fields if key in value} if isinstance(value, dict) else None


def build_friend_input(*, data_date: str, market_dir: Path, screens_root: Path) -> dict[str, Any]:
    """朋友的大盤、籌碼解讀輸入：所有朋友共用同一份，不含任何人的自選股、庫存、email，也不含新聞與國外行情。"""
    exchange = _exchange_sections(data_date, market_dir, screens_root)
    market = {key: value for key, value in exchange["market"].items() if key not in NON_EXCHANGE_MARKET_FIELDS}
    market["latest"] = _only(market.get("latest"), FRIEND_LATEST_FIELDS)
    market["thresholds"] = _only(market.get("thresholds"), FRIEND_THRESHOLD_FIELDS)
    payload: dict[str, Any] = {
        "schema": FRIEND_INPUT_SCHEMA,
        "data_date": data_date,
        "availability": {
            "market": exchange["market_date"] == data_date,
            "flow": bool(exchange["screens"]) and exchange["rotation"] is not None,
        },
        "source_dates": {
            "market": exchange["market_date"] or None,
            "screens": data_date if exchange["screens"] else None,
        },
        "allowed_codes": sorted(exchange["screen_codes"]),
        "portfolio_codes": [],
        "market": market,
        "flows": {"screens": exchange["screens"], "rotation": exchange["rotation"]},
    }
    payload["input_sha256"] = canonical_sha(payload)
    return payload


def load_stock_packs(stock_pack_path: Path, compare_pack_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """個股分析與 0050 對照資料包：都是本站用證交所、櫃買盤後資料算的，不含即時行情。"""
    stock_pack = _read_json(stock_pack_path)
    if stock_pack.get("analysis_schema") != "stock-analysis/v2":
        raise ValueError("個股資料包不是 stock-analysis/v2")
    compare_pack = _read_json(compare_pack_path)
    if compare_pack.get("schema") != "compare-0050-pack/v1":
        raise ValueError("0050 對照資料包格式不正確")
    return stock_pack, compare_pack


def _portfolio_section(portfolio: dict[str, list[dict[str, Any]]], stock_pack: dict[str, Any],
                       compare_pack: dict[str, Any]) -> dict[str, Any]:
    """一個人的持股段落：站主與朋友共用，送出的欄位逐欄一樣（開放給朋友 03 票，DECISIONS 第 8 題）。"""
    portfolio_codes = sorted({str(row.get("code")) for row in [*portfolio["watchlist"], *portfolio["holdings"]] if row.get("code")})
    stock_results = stock_pack.get("results") or {}
    stocks = {code: _stock_summary(stock_results[code]) for code in portfolio_codes if code in stock_results}
    compare_items = compare_pack.get("items") or {}
    return {
        "portfolio_codes": portfolio_codes,
        "portfolio": {"watchlist": portfolio["watchlist"], "holdings": _holding_metrics(portfolio["holdings"], stocks)},
        "stocks": stocks,
        "compare_0050": {
            "benchmark": _compact_compare(compare_pack.get("benchmark")),
            "stocks": {code: _compact_compare(compare_items.get(code)) for code in portfolio_codes if code in compare_items},
        },
    }


FRIEND_STOCK_INPUT_SCHEMA = "ai-digest-friend-stock-input/v1"


def with_friend_portfolio(base: dict[str, Any], *, email: str, portfolio_path: Path, stock_pack: dict[str, Any],
                          compare_pack: dict[str, Any]) -> dict[str, Any]:
    """朋友的完整輸入：大盤、籌碼沿用 ``build_friend_input`` 那份，再加上「他自己」的自選股與庫存。

    只取 ``email`` 這個人的列，其他朋友與站主的持股一列都不帶。自選股和庫存都空的朋友原樣回傳共用那份
    （排程看到沒有 portfolio_codes 就不叫個股判斷）。
    """
    section = _portfolio_section(_portfolio_payload(portfolio_path, email), stock_pack, compare_pack)
    if not section["portfolio_codes"]:
        return base
    payload = {key: value for key, value in base.items() if key != "input_sha256"}
    payload.update({
        "schema": FRIEND_STOCK_INPUT_SCHEMA,
        "availability": {**base["availability"], "stocks": stock_pack.get("as_of") == base["data_date"]},
        "source_dates": {**base["source_dates"], "stock_analysis": stock_pack.get("as_of"),
                         "compare_0050": compare_pack.get("data_date")},
        "allowed_codes": sorted(set(base["allowed_codes"]) | set(section["portfolio_codes"])),
        **section,
    })
    payload["input_sha256"] = canonical_sha(payload)
    return payload


def build_digest_input(
    *,
    data_date: str,
    market_dir: Path,
    screens_root: Path,
    stock_pack_path: Path,
    compare_pack_path: Path,
    portfolio_path: Path,
    owner_email: str,
) -> dict[str, Any]:
    exchange = _exchange_sections(data_date, market_dir, screens_root)
    screen_summaries, rotation = exchange["screens"], exchange["rotation"]
    stock_pack, compare_pack = load_stock_packs(stock_pack_path, compare_pack_path)
    section = _portfolio_section(_portfolio_payload(portfolio_path, owner_email), stock_pack, compare_pack)
    portfolio_codes = section["portfolio_codes"]

    allowed_codes = sorted(set(portfolio_codes) | exchange["screen_codes"])
    market_date = exchange["market_date"]
    payload: dict[str, Any] = {
        "schema": INPUT_SCHEMA,
        "data_date": data_date,
        "availability": {
            "market": market_date == data_date,
            "flow": bool(screen_summaries) and rotation is not None,
            "stocks": stock_pack.get("as_of") == data_date,
        },
        "source_dates": {
            "market": market_date or None,
            "screens": data_date if screen_summaries else None,
            "stock_analysis": stock_pack.get("as_of"),
            "compare_0050": compare_pack.get("data_date"),
        },
        "allowed_codes": allowed_codes,
        "portfolio_codes": portfolio_codes,
        "market": exchange["market"],
        "flows": {"screens": screen_summaries, "rotation": rotation},
        "portfolio": section["portfolio"],
        "stocks": section["stocks"],
        "compare_0050": section["compare_0050"],
    }
    payload["input_sha256"] = canonical_sha(payload)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", dest="data_date")
    parser.add_argument("--market-dir", type=Path, default=ROOT / "data/daily-update-state/last-market-pack")
    parser.add_argument("--screens-root", type=Path, default=ROOT / "data/market-screen-snapshots")
    parser.add_argument("--stock-pack", type=Path, default=ROOT / "cloud/.stock-pack.json")
    parser.add_argument("--compare-pack", type=Path, default=ROOT / "cloud/.compare-pack.json")
    parser.add_argument("--portfolio-json", type=Path, required=True)
    parser.add_argument("--owner-email", required=True)
    parser.add_argument("--output", type=Path, default=Path("ai-digest-input.json"))
    parser.add_argument("--sha-file", type=Path)
    parser.add_argument("--print-latest-date", action="store_true")
    args = parser.parse_args(argv)

    if args.print_latest_date:
        print(infer_data_date(args.stock_pack, args.screens_root))
        return 0
    data_date = args.data_date or infer_data_date(args.stock_pack, args.screens_root)
    payload = build_digest_input(
        data_date=data_date,
        market_dir=args.market_dir,
        screens_root=args.screens_root,
        stock_pack_path=args.stock_pack,
        compare_pack_path=args.compare_pack,
        portfolio_path=args.portfolio_json,
        owner_email=args.owner_email,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    if args.sha_file:
        args.sha_file.parent.mkdir(parents=True, exist_ok=True)
        args.sha_file.write_text(payload["input_sha256"] + "\n", encoding="ascii")
    print(json.dumps({"data_date": data_date, "input_sha256": payload["input_sha256"],
                      "codes": len(payload["allowed_codes"]), "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
