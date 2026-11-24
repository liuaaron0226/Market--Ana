"""AI 產業鏈資金版圖（.scratch/ai-chain-map 票 01）：**一次性**把樣品的 stock_flows.json 轉成寫進 D1 的一列 SQL。

這不是更新的做法。本人 2026-11-24 定案：這一頁之後要每天自動更新、不靠助手也不靠這台電腦——那會是另外一張票的
GitHub Actions 排程（台股從每日更新的工作集重算、美股來源未定），用 cloud/lib/d1-http.js 以綁定參數寫進同一張表
（payload 會隨月份變大，超過 D1 單句 100 KB 的上限後就不能再用 SQL 檔）。這支只在本人核准之後、排程上線之前，
讓正式站先有樣品那一份資料可以看；跑一次就好。

只產生檔案，不連任何遠端：

    python chain_map_bootstrap.py --flows <stock_flows.json> --sql-output <檔>
    npx wrangler@4 d1 execute market-ana-accounts --remote --file <檔>      ← 本人核准後由 owner 手動執行一次

D1 寫不進去時（2026-11-24 晚免費方案當天寫入額度用完）改走 R2：

    python chain_map_bootstrap.py --flows <stock_flows.json> --json-output <檔>
    npx wrangler@4 r2 object put market-ana-state/chain-map/current/flows.json --file <檔> --content-type application/json --remote

只寫資金數字那一張（chain_map_flows）；產業鏈定義不用寫，chain_map_definitions 是空的時候網站用入庫的 seeds/ai-chain-map.json。
寫進的那一列：version＝bootstrap-<最後一個月>、as_of＝最後一個月、built_at＝現在（UTC）。表裡已經有排程寫的列
（version 不是 bootstrap- 開頭）就什麼都不寫，不會蓋過每日建置。payload 的約定（chain-map-flows/v1）見 cloud/lib/chain-map.js 開頭；
格式不符就停下、不產生檔案。印出來的只有月份範圍與檔數，不印資料本身（Yahoo 來源的數字不能出現在公開的紀錄裡）。
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import chain_map_flows as cm
from ledger_core import sql_literal

TABLE = "chain_map_flows"
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
KEYS = ("months", "inst0", "notes", "tw", "us")
# 樣品的 stock_flows.json 另有 owner（同一家公司的主要環節）：那是定義、不是資料，已搬進 seeds/ai-chain-map.json 的 primary，這裡不寫進去。
D1_STATEMENT_LIMIT = 100_000   # D1 單一 SQL 句子的長度上限（位元組）


class ContractError(ValueError):
    pass


def _numbers(values: Any, length: int) -> bool:
    return isinstance(values, list) and len(values) == length and all(
        v is None or (isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)) for v in values)


def validate(payload: Any) -> dict[str, Any]:
    """照約定核對一份 payload；不符就丟 ContractError。回傳只留約定的五個鍵（順序固定）；owner 有的話照樣核對格式、但不留。"""
    if not isinstance(payload, dict):
        raise ContractError("最上層不是物件")
    missing = [key for key in KEYS if key not in payload]
    if missing:
        raise ContractError(f"缺 {', '.join(missing)}")
    months = payload["months"]
    if not isinstance(months, list) or len(months) < 2 or not all(isinstance(m, str) and MONTH_RE.match(m) for m in months):
        raise ContractError("months 要是兩個以上的 YYYY-MM")
    for before, after in zip(months, months[1:]):
        y, m = map(int, before.split("-"))
        if after != (f"{y + 1}-01" if m == 12 else f"{y}-{m + 1:02d}"):
            raise ContractError(f"months 不連續：{before} 之後是 {after}")
    n, inst0 = len(months), payload["inst0"]
    if not isinstance(inst0, int) or isinstance(inst0, bool) or not 0 <= inst0 <= n:
        raise ContractError("inst0 要是 0～月份數之間的整數")
    if not isinstance(payload["notes"], list) or not all(isinstance(line, str) for line in payload["notes"]):
        raise ContractError("notes 要是字串清單")
    owner = payload.get("owner", {})
    if not isinstance(owner, dict) or not all(re.match(r"^(TW|US) \S+$", key) and isinstance(value, str) for key, value in owner.items()):
        raise ContractError("owner 的鍵要是「TW 代號」或「US 代號」、值是環節 id")
    for market in ("tw", "us"):
        table = payload[market]
        if not isinstance(table, dict) or not table:
            raise ContractError(f"{market} 要是有內容的物件")
        for code, row in table.items():
            if not isinstance(row, dict) or not _numbers(row.get("t"), n):
                raise ContractError(f"{market} {code} 的 t 要是 {n} 個數字")
            if market == "tw" and "i" in row and not _numbers(row["i"], n - inst0):
                raise ContractError(f"tw {code} 的 i 要是 {n - inst0} 個數字")
            if market == "us" and "i" in row:
                raise ContractError(f"us {code} 不該有 i")
    return {key: payload[key] for key in KEYS}


def bootstrap_sql(payload: dict[str, Any], *, built_at: str) -> str:
    """一句 INSERT：表裡還沒有排程寫的列才寫；同一個 bootstrap 版本重跑就蓋掉。不寫 BEGIN／COMMIT（D1 不收明確的交易）。"""
    flows = validate(payload)
    as_of = flows["months"][-1]
    q = sql_literal
    text = json.dumps(flows, ensure_ascii=False, separators=(",", ":"))
    sql = (f"INSERT INTO {TABLE} (version, as_of, payload, built_at) "
           f"SELECT {q('bootstrap-' + as_of)}, {q(as_of)}, {q(text)}, {q(built_at)} "
           f"WHERE NOT EXISTS (SELECT 1 FROM {TABLE} WHERE version NOT LIKE 'bootstrap-%') "
           "ON CONFLICT (version) DO UPDATE SET as_of = excluded.as_of, payload = excluded.payload, built_at = excluded.built_at;\n")
    size = len(sql.encode("utf-8"))
    if size > D1_STATEMENT_LIMIT:
        raise ContractError(f"這一句有 {size} 位元組，超過 D1 單句上限 {D1_STATEMENT_LIMIT}；改用 d1-http 綁定參數寫入")
    return sql


def r2_object(payload: dict[str, Any], *, built_at: str) -> dict[str, Any]:
    """R2 chain-map/current/flows.json 的內容（2026-11-24 晚 D1 寫入額度用完時的備援，網站讀法見 cloud/lib/chain-map.js）：
    約定的五個鍵（不留 owner）、舊代號改成新代號（PSTG → P，和每日建置一樣），外面包 schema／version／as_of／built_at。"""
    flows = validate(payload)
    us = dict(flows["us"])
    for old, new, _ in cm.RENAMES:
        if old in us and new not in us:
            us[new] = us.pop(old)
    flows["us"] = us
    as_of = flows["months"][-1]
    return cm.current_object(flows, version=f"bootstrap-{as_of}", as_of=as_of, built_at=built_at)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="一次性：樣品 stock_flows.json → 寫進 chain_map_flows 的一列 SQL、或 R2 的那一份 JSON（不連遠端）")
    parser.add_argument("--flows", required=True, help="stock_flows.json 的路徑")
    parser.add_argument("--sql-output", help="寫出的 SQL 檔（D1）")
    parser.add_argument("--json-output", help="寫出的 JSON 檔（上傳到 R2 market-ana-state/chain-map/current/flows.json）")
    parser.add_argument("--built-at", help="建置時間（ISO 8601，UTC）；不給就用現在")
    args = parser.parse_args(argv)
    if not (args.sql_output or args.json_output):
        parser.error("要給 --sql-output 或 --json-output")
    built_at = args.built_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        payload = json.loads(Path(args.flows).read_text(encoding="utf-8"))
        sql = bootstrap_sql(payload, built_at=built_at) if args.sql_output else None
        obj = r2_object(payload, built_at=built_at) if args.json_output else None
    except (OSError, ValueError) as error:
        print(f"沒有產生：{error}", file=sys.stderr)
        return 1
    months = payload["months"]
    summary = (f"{months[0]}～{months[-1]}，法人自 {months[payload['inst0']] if payload['inst0'] < len(months) else '—'}，"
               f"台股 {len(payload['tw'])} 檔、美股 {len(payload['us'])} 檔")
    if sql is not None:
        Path(args.sql_output).write_text(sql, encoding="utf-8", newline="\n")
        print(f"chain_map_flows 一列（bootstrap-{months[-1]}）：{summary}，{len(sql.encode('utf-8'))} 位元組")
    if obj is not None:
        text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        Path(args.json_output).write_text(text, encoding="utf-8", newline="\n")
        print(f"R2 {cm.CURRENT_KEY}（bootstrap-{months[-1]}，built_at {built_at}）：{summary}，{len(text.encode('utf-8'))} 位元組")
    return 0


if __name__ == "__main__":
    sys.exit(main())
