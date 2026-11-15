"""晚間版預測的執行程式（C 票第 6、7 片）：每日更新成功發布之後，在雲端排程裡跑一次。

輸入是每日更新剛發布的大盤資料檔（``data/daily-update-state/last-market-pack/analysis-data.js``，就是網站畫圖用的
那一份）與方法稿；做的事依序是：
1. 從大盤資料整理一份當天輸入（加權指數日 K、法人與期貨、現行短中期走勢標籤、各項資料截止日、方法稿全文）；
2. 交給預測帳本寫一份晚間版（呼叫模型、驗證、鎖定或記漏記，都在帳本裡）；
3. 替帳本裡所有還有未到期格的預測對帳一次；
4. 輸出給網站資料庫的指令檔與一份執行紀錄。

乾跑（``dry_run``）只在暫存的帳本副本上寫，不碰真的帳本、不輸出資料庫指令檔，把模型挑的線、落點與理由寫進
執行紀錄給本人看。乾跑可以用 ``as_of`` 重播已發布過的某一天（資料截到那天、時鐘停在那天收盤後）。
"""
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

import forecast_ledger as fl
import market_trend

SCHEMA = "forecast-run/v1"
# 沿用每日 AI 解讀用的模型（本人已訂閱的 Google AI Pro，不新增付費服務）。
MODEL_DEFAULT = "gemini-3.8-flash"
# 重播乾跑的時鐘：那一天收盤、每日更新發布之後。
REPLAY_CLOCK = "17:30:00"
CHIP_DAYS = 5


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


def _trend_labels(daily: list[dict[str, Any]], institutional: list[dict[str, Any]]) -> dict[str, str | None]:
    periods = {period["key"]: period for period in market_trend.assess_market_trends(daily, institutional)["periods"]}
    return {key: (periods[key]["conclusion"] if periods.get(key, {}).get("state") != "insufficient" else None)
            for key in ("short", "medium")}


def build_day_input(analysis: Mapping[str, Any], method_text: str) -> dict[str, Any]:
    """把已發布的大盤資料整理成帳本要的一份當天輸入；基準日是日 K 的最後一天。"""
    daily = list(analysis.get("daily") or [])
    institutional = list(analysis.get("institutional") or [])
    futures = list(analysis.get("futures") or [])
    base_date = daily[-1]["date"] if daily else None
    return {
        "base_date": base_date,
        "index_daily": [{key: row.get(key) for key in ("date", "open", "high", "low", "close")} for row in daily],
        "trend_labels": _trend_labels(daily, institutional) if daily else {},
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
        "method_text": method_text,
    }


def _replay(analysis: Mapping[str, Any], day: str) -> dict[str, Any]:
    trimmed = {key: _until(analysis.get(key), day) for key in ("daily", "institutional", "futures")}
    if not trimmed["daily"] or trimmed["daily"][-1]["date"] != day:
        raise ValueError(f"已發布的日 K 裡沒有 {day}，不能重播")
    return {**analysis, **trimmed}


def _report(record: Mapping[str, Any]) -> dict[str, Any]:
    """執行紀錄裡放這一筆的內容，但不放完整輸入（方法稿與日 K 很大，原件已存在帳本）。"""
    return {key: value for key, value in record.items() if key != "input"}


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
) -> dict[str, Any]:
    if as_of and not dry_run:
        raise ValueError("重播某一天（as_of）只能在乾跑使用，正式帳本不補寫過去的預測")
    analysis = load_analysis(analysis_path)
    clock = now
    if as_of:
        analysis = _replay(analysis, as_of)
        frozen = datetime.fromisoformat(f"{as_of}T{REPLAY_CLOCK}").replace(tzinfo=fl.TAIPEI)
        clock = lambda: frozen  # noqa: E731
    clock = clock or (lambda: datetime.now(fl.TAIPEI))
    data = build_day_input(analysis, Path(method_path).read_text(encoding="utf-8"))
    base_date = data["base_date"]
    log: dict[str, Any] = {
        "schema": SCHEMA, "edition": "evening", "dry_run": dry_run, "base_date": base_date, "model": model,
        "started_at": clock().astimezone(fl.TAIPEI).isoformat(),
    }

    if date and date != base_date:
        # 手動指定的日期不是剛發布的資料日：不交給帳本（帳本會把過了期限的日子鎖成漏記）。
        log.update(outcome="not_ready", detail=f"要求的日期 {date} 不是已發布的資料日 {base_date}")
    else:
        with tempfile.TemporaryDirectory() as scratch:
            target = Path(ledger)
            if dry_run:
                target = Path(scratch) / "ledger"
                if Path(ledger).is_dir():
                    shutil.copytree(ledger, target)
            result = fl.write_forecast(target, data, edition="evening", model_bin=model_bin, model=model,
                                       now=clock, timeout_seconds=timeout_seconds)
        record = result.get("record")
        log["outcome"] = result["outcome"]
        if result.get("detail"):
            log["detail"] = result["detail"]
        if record:
            log.update(kind=record["kind"], forecast_id=record["forecast_id"], locked_at=record.get("locked_at"),
                       miss_reason=record.get("miss_reason"), miss_detail=record.get("miss_detail"),
                       attempts=record.get("attempts", []), record=_report(record))

    if not dry_run:
        reconciliation = fl.reconcile(ledger, data["index_daily"])
        log["reconciliation"] = {key: len(value) for key, value in reconciliation.items()}
        if sql_output:
            published_at = clock().astimezone(fl.TAIPEI).isoformat()
            Path(sql_output).parent.mkdir(parents=True, exist_ok=True)
            Path(sql_output).write_text(fl.d1_sql(ledger, data["index_daily"], published_at=published_at),
                                        encoding="utf-8")
    log["finished_at"] = clock().astimezone(fl.TAIPEI).isoformat()
    if log_output:
        Path(log_output).parent.mkdir(parents=True, exist_ok=True)
        Path(log_output).write_text(json.dumps(log, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return log


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="寫一份晚間版大盤預測並對帳（每日更新成功發布之後）")
    parser.add_argument("--analysis", type=Path, required=True, help="已發布的 analysis-data.js")
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--method", type=Path, required=True, help="方法稿（MARKET_METHOD.md）")
    parser.add_argument("--agy-bin", type=Path, required=True)
    parser.add_argument("--model", default=MODEL_DEFAULT)
    parser.add_argument("--date", help="要求的資料日；必須等於已發布資料的最後一天")
    parser.add_argument("--dry-run", action="store_true", help="只在暫存副本上寫，不碰帳本、不輸出資料庫指令檔")
    parser.add_argument("--as-of", help="乾跑時重播已發布過的某一天")
    parser.add_argument("--sql-output", type=Path)
    parser.add_argument("--log-output", type=Path)
    args = parser.parse_args(argv)
    log = run_evening(args.analysis, ledger=args.ledger, method_path=args.method, model_bin=args.agy_bin,
                      model=args.model, date=args.date, dry_run=args.dry_run, as_of=args.as_of,
                      sql_output=args.sql_output, log_output=args.log_output)
    summary = {key: log.get(key) for key in ("base_date", "dry_run", "outcome", "kind", "miss_reason", "detail",
                                             "reconciliation")}
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
