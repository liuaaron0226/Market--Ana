"""AI 產業鏈資金版圖：每月資金資料的雲端每日建置（本人 2026-11-24：要每天自己更新、不靠助手）。

三種數字（本人 11-24 定案），2021-01 起：
- 台股成交金額（億元）：工作集的上市（stock-history）、上櫃（otc-history）日行情 turnover 每月加總。
- 台股法人（億元）：每天（外資含外資自營商＋投信）淨買賣股數 × 當天收盤，每月加總；
  來源 listed-institutional、otc-history/otc_market.sqlite。有幾天就用幾天，涵蓋不到的月份是 null。
- 美股成交金額（十億美元）：Massive（原 Polygon）grouped daily，每個美股交易日一次呼叫拿全部代號，
  只留產業鏈定義裡約 100 檔，每天一個抽取檔存 R2 的 chain-map/raw/（只增不改、記原始回應的 SHA-256）；
  當天成交金額 = 成交量 × 成交量加權均價（vw），沒有 vw 才用 (高＋低＋收)/3，抽取檔記下用了哪一種。

公司名單：D1 chain_map_definitions（0039）created_at 最新、讀得懂的那一份；沒有就用入庫的 seeds/ai-chain-map.json。

歷史：R2（market-ana-state）chain-map/bootstrap/ 底下最新的樣品原檔是起始資料（owner 不用、PSTG 改成 P），
美股從它最後一個月的下一個月起改用 Massive。每次建置以 D1 chain_map_flows（0039）裡「上一次排程寫的那一列」為底
（雲端工作集的行情只留最近約 1,270 個交易日，算過的月份靠它留住），沒有就用起始資料；所以第一次排程就寫出完整的一份。合併規則：某公司某月，雲端資料涵蓋整個月就用雲端算的，否則用底的；不把半個月的
加總混進過去的月份。還沒過完的這個月放在 ``partial``，不放進 ``months``（網頁目前不讀它）。
寫 D1 用 Cloudflare API 的 D1 /query 綁定參數（payload 會超過 D1 單句 100 KB，不能用 SQL 檔）；
version＝台北建置日，同一天重跑蓋掉，排程寫的列只留最近 KEEP_ROWS 列（樣品那一列不刪）。

Massive 資料限站主私用：原始資料不進 git、不印到執行紀錄；執行紀錄只印數量與月份範圍。

  python chain_map_flows.py build [--d1] [--r2] [--dry-run] [--output F] [--max-us-calls N]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "chain-map/raw"
CHAIN_PATH = ROOT / "seeds/ai-chain-map.json"   # 入庫的基準版（網站同一份）
R2_PREFIX = "chain-map/raw/"
BOOTSTRAP_PREFIX = "chain-map/bootstrap/"   # 樣品原檔（stock_flows-<月>.json），取檔名最後的那一份
# 網站讀的另一份（2026-11-24 晚 D1 寫入額度用完時加的 R2 備援）：每次建置先寫它、再寫 D1；網站取 built_at 較晚的那一份。
CURRENT_KEY = "chain-map/current/flows.json"
CURRENT_SCHEMA = "chain-map-flows-r2/v1"
TABLE = "chain_map_flows"
DEFINITIONS = "chain_map_definitions"
KEEP_ROWS = 14   # 排程寫的列留幾列（每天一列）
SCHEMA = "chain-map-us-day/v1"
TAIPEI = timezone(timedelta(hours=8))

# 每次最多打幾次 Massive（免費方案每分鐘 5 次 → 每次間隔 12.5 秒；20 次約 4 分鐘）。
# 停擺後從最舊的缺日往後補，一次補不完下一次接著補。
MAX_US_CALLS = 20
# 免費方案約只回得到兩年內的日子；超過的不去要。
US_HISTORY_DAYS = 700

PRICE_DBS = ("stock-history/stock-history.sqlite", "otc-history/otc_history.sqlite")
INST_PAIRS = (("listed-institutional/listed_institutional.sqlite", PRICE_DBS[0]),
              ("otc-history/otc_market.sqlite", PRICE_DBS[1]))

# 代號變更（舊, 新, 新代號生效日）：定義裡寫哪一個都行，payload 用定義裡的代號當鍵。
# Pure Storage 2026-04-17 起改名 Everpure、代號 P；MOD 改名 Modexus Solutions 但代號沒變，不用列。
RENAMES = (("PSTG", "P", "2026-04-17"),)


class ChainMapError(Exception):
    status = "failed"


class MissingBootstrap(ChainMapError):
    status = "no_bootstrap"


class MissingTable(ChainMapError):
    status = "skipped_no_table"   # 0039 還沒套：不算失敗


class D1WriteLimit(ChainMapError):
    status = "d1_write_limit"     # 免費方案當天寫入額度用完：這一班算失敗，R2 的日檔照樣留著


# ---- D1（Cloudflare API 的 /query，參數由 Cloudflare 那一端代入；同 cloud/lib/d1-http.js） ----

class D1Http:
    def __init__(self, account_id: str, token: str, database_id: str, http=None):
        if not (account_id and token and database_id):
            raise ChainMapError("缺少 Cloudflare 帳號、資料庫或權杖設定")
        self.url = (f"https://api.cloudflare.com/client/v4/accounts/{account_id}/d1/database/{database_id}/query")
        self.token, self.http = token, http or self._post

    @classmethod
    def from_env(cls) -> "D1Http":
        text = (ROOT / "cloud/wrangler.jsonc").read_text(encoding="utf-8")
        match = re.search(r'"database_name":\s*"market-ana-accounts",\s*"database_id":\s*"([^"]+)"', text)
        return cls(os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip(), os.environ.get("CLOUDFLARE_API_TOKEN", "").strip(),
                   match.group(1) if match else "")

    def _post(self, url: str, body: bytes, headers: dict) -> tuple[int, bytes]:
        request = urllib.request.Request(url, data=body, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read() or b""

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        status, raw = self.http(self.url, json.dumps({"sql": sql, "params": list(params)}).encode("utf-8"),
                                {"Authorization": f"Bearer {self.token}", "content-type": "application/json"})
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = {}
        result = payload.get("result")[0] if isinstance(payload.get("result"), list) and payload["result"] else None
        if status != 200 or not payload.get("success") or not result or result.get("success") is False:
            message = "；".join(str(e.get("message")) for e in payload.get("errors") or [] if isinstance(e, dict)) or f"HTTP {status}"
            if re.search(r"no such table", message, re.I):
                raise MissingTable("資料表還沒建立：到 Actions 按 d1-migrate")
            if re.search(r"daily row write limit|exceeded D1'?s free tier", message, re.I):
                raise D1WriteLimit("D1 今天的免費寫入額度已用完，這一班沒寫進去；美股日檔已存進 R2，明天的排程會照常補上")
            raise ChainMapError(f"資料庫查詢失敗（HTTP {status}）")
        return result.get("results") or []


def load_definition(d1, seed_path: Path = CHAIN_PATH) -> tuple[dict[str, Any], str]:
    """目前的公司名單：D1 最新、讀得懂的那一份；沒有、表還沒建、那一份壞了 → 入庫的基準版。"""
    if d1 is not None:
        try:
            rows = d1.query(f"SELECT version, payload FROM {DEFINITIONS} ORDER BY created_at DESC, version DESC LIMIT 1")
        except MissingTable:
            rows = []
        if rows:
            try:
                chain = json.loads(rows[0]["payload"])
                definition(chain)
                return chain, str(rows[0]["version"])
            except (TypeError, KeyError, ValueError):
                pass
    return json.loads(seed_path.read_text(encoding="utf-8")), "file"


def load_latest(d1) -> tuple[dict[str, Any] | None, str | None]:
    """排程最近寫的那一列的（payload, built_at）（樣品那一列不算）；沒有就是 (None, None)。表還沒建 → MissingTable。"""
    rows = d1.query(f"SELECT payload, built_at FROM {TABLE} WHERE version NOT LIKE 'bootstrap-%' "
                    "ORDER BY built_at DESC, version DESC LIMIT 1")
    return (json.loads(rows[0]["payload"]), str(rows[0].get("built_at") or "")) if rows else (None, None)


def normalize_bootstrap(payload: Mapping[str, Any]) -> dict[str, Any]:
    """樣品原檔 → 約定的形狀：不留 owner（主要環節在定義裡），舊代號改成新代號（PSTG → P）。"""
    out = {k: v for k, v in payload.items() if k != "owner"}
    us = dict(out.get("us") or {})
    for old, new, _ in RENAMES:
        if old in us and new not in us:
            us[new] = us.pop(old)
    out["us"] = us
    return validate_bootstrap(out)


def load_bootstrap(store) -> tuple[dict[str, Any] | None, str | None]:
    """R2 chain-map/bootstrap/ 底下最新（檔名排序最後）的那一份樣品；沒有就是 (None, None)。"""
    keys = sorted(k for k in store.keys(BOOTSTRAP_PREFIX) if k.endswith(".json"))
    if not keys:
        return None, None
    data = store.get(keys[-1])
    return (normalize_bootstrap(json.loads(data.decode("utf-8"))) if data else None), keys[-1]


def current_object(payload: Mapping[str, Any], *, version: str, as_of: str, built_at: str) -> dict[str, Any]:
    """R2 chain-map/current/flows.json 的內容（網站在 D1 沒有、或這份較新時讀它；見 cloud/lib/chain-map.js）。"""
    return {"schema": CURRENT_SCHEMA, "version": version, "as_of": as_of, "built_at": built_at, "payload": dict(payload)}


def load_current(store) -> tuple[dict[str, Any] | None, str | None]:
    """R2 上一次寫的 current（payload, built_at）；沒有、讀不懂 → (None, None)。"""
    try:
        data = store.get(CURRENT_KEY)
        wrapper = json.loads(data.decode("utf-8")) if data else None
    except (OSError, UnicodeDecodeError, ValueError):
        return None, None
    if not isinstance(wrapper, dict) or wrapper.get("schema") != CURRENT_SCHEMA or not isinstance(wrapper.get("payload"), dict):
        return None, None
    return wrapper["payload"], str(wrapper.get("built_at") or "")


def write_flows(d1, payload_text: str, *, version: str, as_of: str, built_at: str, keep: int = KEEP_ROWS) -> None:
    """一列帶版本的資料（同一天重跑蓋掉），再清掉太舊的排程列；樣品那一列不動。"""
    d1.query(f"INSERT INTO {TABLE} (version, as_of, payload, built_at) VALUES (?1, ?2, ?3, ?4) "
             "ON CONFLICT (version) DO UPDATE SET as_of = excluded.as_of, payload = excluded.payload, built_at = excluded.built_at",
             [version, as_of, payload_text, built_at])
    d1.query(f"DELETE FROM {TABLE} WHERE version NOT LIKE 'bootstrap-%' AND version NOT IN "
             f"(SELECT version FROM {TABLE} WHERE version NOT LIKE 'bootstrap-%' ORDER BY built_at DESC, version DESC LIMIT ?1)",
             [keep])


# ---- 日期 ----

def next_month(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{y + (m == 12):04d}-{m % 12 + 1:02d}"


def month_range(first: str, last: str) -> list[str]:
    out, ym = [], first
    while ym <= last:
        out.append(ym)
        ym = next_month(ym)
    return out


def weekdays(start: date, end: date) -> list[str]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def month_weekdays(ym: str) -> list[str]:
    first = date.fromisoformat(f"{ym}-01")
    return weekdays(first, date.fromisoformat(f"{next_month(ym)}-01") - timedelta(days=1))


def last_us_day(now_utc: datetime) -> date:
    """最晚能要的美股交易日：美東午夜過後才有前一天的資料。now−11 小時一定早於美東時間 6 小時以上，
    再往前一天，就不會在資料還沒出來時把空回應誤記成休市。"""
    return (now_utc - timedelta(hours=11)).date() - timedelta(days=1)


# ---- 定義 ----

def definition(chain: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    tw = sorted({row[0] for node in chain["nodes"] for row in node.get("tw") or []})
    us = sorted({row[0] for node in chain["nodes"] for row in node.get("us") or []})
    if not tw or not us:
        raise ValueError("產業鏈定義沒有台股或美股公司")
    return tw, us


def massive_ticker(code: str, day: str) -> str:
    for old, new, since in RENAMES:
        if code in (old, new):
            return new if day >= since else old
    return code


def r(value: float | None, big: float = 100) -> float | int | None:
    """和樣品同一套位數：大的取整數、中間一位小數、小的兩位。"""
    if value is None:
        return None
    a = abs(value)
    return int(round(value)) if a >= big else round(value, 1) if a >= 10 else round(value, 2)


# ---- 起始資料 ----

def validate_bootstrap(payload: Mapping[str, Any]) -> dict[str, Any]:
    months = payload.get("months")
    if not isinstance(months, list) or not months or months != month_range(months[0], months[-1]):
        raise ChainMapError("起始資料的 months 不是連續月份")
    n, i0 = len(months), payload.get("inst0")
    if not isinstance(i0, int) or not 0 <= i0 <= n:
        raise ChainMapError("起始資料的 inst0 不對")
    for code, series in (payload.get("tw") or {}).items():
        if len(series.get("t", [])) != n or len(series.get("i", [])) != n - i0:
            raise ChainMapError(f"起始資料台股 {code} 長度不對")
    for code, series in (payload.get("us") or {}).items():
        if len(series.get("t", [])) != n:
            raise ChainMapError(f"起始資料美股 {code} 長度不對")
    return dict(payload)


def _boot_value(boot: Mapping[str, Any], market: str, code: str, metric: str, ym: str) -> float | None:
    months = boot["months"]
    if ym not in months:
        return None
    k = months.index(ym)
    series = (boot.get(market) or {}).get(code)
    if not series or metric not in series:
        return None
    if metric == "i":
        k -= boot["inst0"]
        if k < 0:
            return None
    return series[metric][k]


# ---- 台股（工作集） ----

def _ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def load_tw(data_dir: Path, codes: list[str]) -> dict[str, Any]:
    """每月加總（元）＋涵蓋範圍：每檔行情最早一天、法人最早一天（上市、上櫃兩邊都有的那天起）、行情最後一天。"""
    marks = ",".join("?" * len(codes))
    t: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    inst: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    first: dict[str, str] = {}
    ends, inst_firsts = [], []
    for db in PRICE_DBS:
        path = data_dir / db
        if not path.exists():
            raise ChainMapError(f"工作集缺 {db}")
        con = _ro(path)
        try:
            for ym, code, v in con.execute(f"SELECT substr(date,1,7), code, sum(turnover) FROM daily WHERE code IN ({marks}) "
                                           "GROUP BY 1, 2", codes):
                t[code][ym] += v or 0
            for code, d in con.execute(f"SELECT code, min(date) FROM daily WHERE code IN ({marks}) GROUP BY code", codes):
                first[code] = min(first.get(code, d), d)
            ends.append(con.execute("SELECT max(date) FROM daily").fetchone()[0])
        finally:
            con.close()
    for inst_db, price_db in INST_PAIRS:
        path = data_dir / inst_db
        if not path.exists():
            raise ChainMapError(f"工作集缺 {inst_db}")
        con = _ro(path)
        try:
            con.execute("ATTACH DATABASE ? AS px", (f"file:{(data_dir / price_db).as_posix()}?mode=ro",))
            for ym, code, v in con.execute(
                    "SELECT substr(i.date,1,7), i.code, sum((coalesce(i.foreign_net,0)+coalesce(i.trust_net,0)) * p.close) "
                    f"FROM institutional i JOIN px.daily p ON p.date = i.date AND p.code = i.code "
                    f"WHERE i.code IN ({marks}) AND p.close IS NOT NULL GROUP BY 1, 2", codes):
                inst[code][ym] += v or 0
            inst_firsts.append(con.execute("SELECT min(date) FROM institutional").fetchone()[0])
        finally:
            con.close()
    return {"t": t, "i": inst, "first": first,
            "inst_first": max(inst_firsts) if all(inst_firsts) else None,
            "through": min(ends) if all(ends) else None}


# ---- 美股（Massive 抽取檔） ----

def day_turnover(row: Mapping[str, Any]) -> tuple[float, str] | None:
    """一檔一天的成交金額（美元）與算法：vw 優先，沒有才 (高＋低＋收)/3；成交量不對就沒有。"""
    def num(x):
        return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) and x == x else None
    v, vw = num(row.get("v")), num(row.get("vw"))
    if v is None or v < 0:
        return None
    if vw and vw > 0:
        return v * vw, "vw"
    h, l, c = num(row.get("h")), num(row.get("l")), num(row.get("c"))
    if None in (h, l, c):
        return None
    return v * (h + l + c) / 3, "hlc3"


def make_extract(day: str, results: list[dict], body: bytes, us_codes: list[str], fetched_at: str) -> dict[str, Any]:
    wanted = {massive_ticker(code, day) for code in us_codes}
    rows = {item["T"]: item for item in results if item.get("T") in wanted}
    if results and len(rows) < len(wanted) // 2:
        # 有回應卻找不到一半以上的公司：多半是回應不完整，不存（存了就永遠不重抓）。
        from overseas_peers import FormatChanged
        raise FormatChanged(f"grouped daily 只找到 {len(rows)} 檔")
    tickers = {}
    for ticker, item in sorted(rows.items()):
        got = day_turnover(item)
        tickers[ticker] = {k: item.get(k) for k in ("v", "vw", "h", "l", "c", "n")}
        tickers[ticker].update({"turnover": got[0], "basis": got[1]} if got else {"turnover": None, "basis": None})
    return {"schema": SCHEMA, "source": "Massive grouped daily（美股，adjusted=false）",
            "endpoint": f"/v2/aggs/grouped/locale/us/market/stocks/{day}", "date": day, "fetched_at": fetched_at,
            "response_sha256": hashlib.sha256(body).hexdigest(), "response_bytes": len(body),
            "results_count": len(results), "holiday": not results, "requested": sorted(wanted), "tickers": tickers}


def load_us_days(raw_dir: Path) -> dict[str, dict[str, Any]]:
    days = {}
    for path in sorted(raw_dir.glob("us-*.json")) if raw_dir.exists() else []:
        entry = json.loads(path.read_text(encoding="utf-8"))
        if entry.get("schema") == SCHEMA:
            days[entry["date"]] = entry
    return days


def us_sum(days: Mapping[str, Mapping[str, Any]], code: str, ym: str, until: str | None = None) -> float:
    total = 0.0
    for day in month_weekdays(ym):
        if until and day > until:
            break
        entry = days.get(day)
        row = (entry or {}).get("tickers", {}).get(massive_ticker(code, day))
        if row and row.get("turnover"):
            total += row["turnover"]
    return total


def us_covered(days: Mapping[str, Any], ym: str) -> bool:
    return all(day in days for day in month_weekdays(ym))


def us_through(days: Mapping[str, Any], ym: str) -> str | None:
    """這個月從月初連續存到哪一天（休市也算存了）；月初就缺就是 None。"""
    last = None
    for day in month_weekdays(ym):
        if day not in days:
            break
        last = day
    return last


# ---- 補抓 ----

def catch_up(source, raw_dir: Path, us_codes: list[str], *, start: str, now_utc: datetime,
             max_calls: int = MAX_US_CALLS, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """把 start 之後、最晚可要的那天之前所有缺的平日補上（最舊的先），每次最多 max_calls 次。
    已存的日子（含休市）絕不重抓；休市日 Massive 回空，存成 holiday 檔，下次不再問。"""
    from overseas_peers import SourceError
    cutoff = last_us_day(now_utc)
    floor = max(date.fromisoformat(start), cutoff - timedelta(days=US_HISTORY_DAYS))
    raw_dir.mkdir(parents=True, exist_ok=True)
    missing = [d for d in weekdays(floor, cutoff) if not (raw_dir / f"us-{d}.json").exists()]
    report = {"missing": len(missing), "fetched": 0, "holidays": 0, "hlc3": 0, "error": None}
    fetched_at = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    for n, day in enumerate(missing[:max_calls]):
        if n:
            sleep(source.pause)
        try:
            results, body = source.grouped(date.fromisoformat(day))
            extract = make_extract(day, results, body, us_codes, fetched_at)
        except SourceError as error:
            report["error"] = error.problem
            break
        except OSError:   # 連線失敗（urllib.error.URLError、逾時）
            report["error"] = "network"
            break
        target = raw_dir / f"us-{day}.json"
        target.write_text(json.dumps(extract, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        report["fetched"] += 1
        report["holidays"] += extract["holiday"]
        report["hlc3"] += sum(1 for row in extract["tickers"].values() if row["basis"] == "hlc3")
    report["left"] = report["missing"] - report["fetched"]
    return report


def sync_down(store, raw_dir: Path = RAW_DIR) -> int:
    raw_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for key in store.keys(R2_PREFIX):
        name = key[len(R2_PREFIX):]
        target = raw_dir / name
        if "/" in name or target.exists():
            continue
        data = store.get(key)
        if data is not None:
            target.write_bytes(data)
            count += 1
    return count


def sync_up(store, raw_dir: Path = RAW_DIR) -> int:
    existing = set(store.keys(R2_PREFIX))
    count = 0
    for path in sorted(raw_dir.glob("us-*.json")) if raw_dir.exists() else []:
        if R2_PREFIX + path.name not in existing:
            store.put(R2_PREFIX + path.name, path.read_bytes())
            count += 1
    return count


# ---- 合併成 payload ----

def compose(boot: Mapping[str, Any], tw: Mapping[str, Any], us_days: Mapping[str, Any],
            tw_codes: list[str], us_codes: list[str]) -> dict[str, Any]:
    """起始資料＋雲端資料 → 網頁讀的 payload（與樣品 stock_flows.json 同結構，多一個 partial）。"""
    tw_through, inst_first = tw["through"], tw["inst_first"]

    def tw_complete(ym):
        return bool(tw_through) and tw_through >= f"{next_month(ym)}-01"

    months = list(boot["months"])
    ym = next_month(months[-1])
    while tw_complete(ym) and us_covered(us_days, ym):
        months.append(ym)
        ym = next_month(ym)
    pending = ym
    in_boot = set(boot["months"])

    def tw_cov(code, ym, metric):
        start = f"{ym}-01"
        price = tw["first"].get(code)
        ok = bool(price) and price < start
        return ok and (metric == "t" or (bool(inst_first) and inst_first < start))

    def tw_value(code, ym, metric):
        if ym in in_boot and not tw_cov(code, ym, metric):
            return _boot_value(boot, "tw", code, metric, ym)
        if metric == "i" and not (inst_first and inst_first < f"{ym}-01"):
            return None
        return r(tw[metric].get(code, {}).get(ym, 0.0) / 1e8)

    def us_value(code, ym):
        if us_covered(us_days, ym):
            return r(us_sum(us_days, code, ym) / 1e9, 1000)
        return _boot_value(boot, "us", code, "t", ym)

    t_rows = {code: [tw_value(code, m, "t") for m in months] for code in tw_codes}
    i_rows = {code: [tw_value(code, m, "i") for m in months] for code in tw_codes}

    def inst_month_ok(k):
        # 那個月有成交的每一檔都算得出法人才算（只有少數幾檔算得出時，網頁會把其他檔當 0，不如整月留空）
        traded = [c for c in tw_codes if t_rows[c][k]]
        return bool(traded) and all(i_rows[c][k] is not None for c in traded)

    inst0 = next((k for k in range(len(months)) if inst_month_ok(k)), len(months))
    us_rows = {code: [us_value(code, m) for m in months] for code in us_codes}
    massive_from = None
    for k in range(len(months) - 1, -1, -1):
        if not us_covered(us_days, months[k]):
            break
        massive_from = months[k]

    partial = None
    us_thr = us_through(us_days, pending)
    tw_has = bool(tw_through) and tw_through >= f"{pending}-01"
    if tw_has or us_thr:
        month_end = (date.fromisoformat(f"{next_month(pending)}-01") - timedelta(days=1)).isoformat()
        inst_ok = bool(inst_first) and inst_first < f"{pending}-01"
        partial = {
            "month": pending,
            "through": {"tw": min(tw_through, month_end) if tw_has else None, "us": us_thr},
            "tw": {code: {"t": r(tw["t"].get(code, {}).get(pending, 0.0) / 1e8),
                          "i": r(tw["i"].get(code, {}).get(pending, 0.0) / 1e8) if inst_ok else None}
                   for code in tw_codes} if tw_has else {},
            "us": {code: {"t": r(us_sum(us_days, code, pending, us_thr) / 1e9, 1000)} for code in us_codes} if us_thr else {},
        }

    inst_note = (f"法人為外資（含外資自營商）加投信的淨買賣股數乘當日收盤價，{months[inst0]} 起。"
                 if inst0 < len(months) else "法人資料尚未涵蓋任何完整月份。")
    if inst0 < len(months) and inst_first and inst_first < f"{months[inst0]}-01" and inst0 > 0:
        inst_note += "更早的月份缺逐日收盤價，無法換算金額，暫無數字。"
    if massive_from and massive_from != months[0]:
        before = months[months.index(massive_from) - 1]
        us_note = (f"美股成交金額 {massive_from} 起為 Massive 每日成交量乘成交量加權均價的月加總；"
                   f"{before} 以前以月成交量乘當月高、低、收平均估算，兩段算法不同。上市較晚的公司之前沒有數字。")
    elif massive_from:
        us_note = "美股成交金額為 Massive 每日成交量乘成交量加權均價的月加總。上市較晚的公司之前沒有數字。"
    else:
        us_note = "美股成交金額以月成交量乘當月高、低、收平均估算；上市較晚的公司之前沒有數字。"
    return {
        "months": months,
        "inst0": inst0,
        "notes": ["台股成交金額來自證交所、櫃買中心每日行情；" + inst_note, us_note,
                  "占比的分母是地圖上所有公司的合計。同一家公司出現在好幾個環節時，只算進一個主要環節。"],
        "tw": {code: {"t": t_rows[code], "i": i_rows[code][inst0:]} for code in tw_codes},
        "us": {code: {"t": us_rows[code]} for code in us_codes},
        "partial": partial,
        "us_massive_from": massive_from,
    }


# ---- 一次建置 ----

def build(*, d1=None, chain: Mapping[str, Any] | None = None, bootstrap: Mapping[str, Any] | None = None,
          data_dir: Path = DATA_DIR, raw_dir: Path = RAW_DIR, store=None, source=None, now_utc: datetime | None = None,
          dry_run: bool = False, output: Path | None = None,
          max_calls: int = MAX_US_CALLS, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """讀名單（D1／基準版）、樣品（R2）與上一次的結果（D1）→ 補齊 R2 日檔 → 補抓缺的美股日子 → 上傳新的日檔
    → 合併 → 寫 D1 一列。回傳一行狀態用的摘要（只有數量與月份）。
    乾跑：照樣讀 D1、R2、呼叫 Massive，但不上傳 R2、不寫 D1、不寫檔。"""
    now_utc = now_utc or datetime.now(timezone.utc)
    summary: dict[str, Any] = {"status": "ok", "as_of": None, "months": 0, "us_days_fetched": 0, "dry_run": dry_run}
    if chain is None:
        chain, summary["definition"] = load_definition(d1)
    if bootstrap is None and store is not None:
        bootstrap, summary["bootstrap"] = load_bootstrap(store)
    if bootstrap is None:
        raise MissingBootstrap(f"R2 {BOOTSTRAP_PREFIX} 找不到樣品資料，組不出歷史月份")
    boot = normalize_bootstrap(bootstrap)
    table_missing = False
    latest = latest_at = None
    if d1 is not None:
        try:
            latest, latest_at = load_latest(d1)
        except MissingTable:
            table_missing = True   # 照樣補美股日檔、組出資料，只是不寫
    current, current_at = load_current(store) if store is not None else (None, None)
    start = f"{next_month(boot['months'][-1])}-01"    # 美股從樣品之後的那個月起用 Massive
    summary["base"] = "bootstrap"
    base = boot
    # 底：D1 排程列與 R2 current 取 built_at 較晚的那一份（D1 寫不進去的那幾天靠 R2 留住算過的月份）
    for candidate, at, name in sorted(((latest, latest_at or "", "latest"), (current, current_at or "", "r2_current")),
                                      key=lambda item: item[1], reverse=True):
        if candidate is None:
            continue
        try:
            base = validate_bootstrap(candidate)
        except ChainMapError:
            continue
        summary["base"] = name
        break
    tw_codes, us_codes = definition(chain)
    if store is not None:
        summary["r2_downloaded"] = sync_down(store, raw_dir)
    if source is None:
        fetch = {"missing": None, "fetched": 0, "holidays": 0, "hlc3": 0, "left": None, "error": "no_key"}
    else:
        fetch = catch_up(source, raw_dir, us_codes, start=start, now_utc=now_utc, max_calls=max_calls, sleep=sleep)
    if store is not None and not dry_run:
        summary["r2_uploaded"] = sync_up(store, raw_dir)
    tw = load_tw(data_dir, tw_codes)
    from chain_roster_health import market_health
    us_days = load_us_days(raw_dir)
    payload = compose(base, tw, us_days, tw_codes, us_codes)
    seed_us = definition(json.loads(CHAIN_PATH.read_text(encoding="utf-8")))[1]
    payload["roster_health"] = market_health(chain, data_dir, PRICE_DBS, us_days, massive_ticker, set(seed_us))
    if fetch["error"] or fetch["left"]:
        payload["roster_health"]["us_incomplete"] = True
    massive_from = payload.pop("us_massive_from")
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    months = payload["months"]
    partial = payload["partial"] or {}
    summary.update({
        "as_of": months[-1], "months": len(months), "first_month": months[0], "last_month": months[-1],
        "tw_through": tw["through"],
        "inst_from": months[payload["inst0"]] if payload["inst0"] < len(months) else None,
        "us_massive_from": massive_from,
        "partial_month": partial.get("month"), "partial_through": partial.get("through"),
        "tw_codes": len(tw_codes), "us_codes": len(us_codes),
        "us_days_fetched": fetch["fetched"], "us_holidays_recorded": fetch["holidays"],
        "us_hlc3_rows": fetch["hlc3"], "us_days_left": fetch["left"], "us_error": fetch["error"],
        "payload_bytes": len(text.encode("utf-8")),
    })
    if fetch["error"] == "no_key":
        summary["status"] = "no_key"
    elif fetch["error"]:
        summary["status"] = "fetch_failed"
    elif fetch["left"]:
        summary["status"] = "catching_up"
    if not dry_run:
        if output:
            output.write_text(text, encoding="utf-8")
        version = now_utc.astimezone(TAIPEI).date().isoformat()
        built_at = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        # 先寫 R2（網站的備援），再寫 D1：D1 寫不進去（額度用完、表還沒建）時網站照樣有今天的資料
        r2_ok = False
        if store is not None:
            body = json.dumps(current_object(payload, version=version, as_of=months[-1], built_at=built_at),
                              ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            try:
                store.put(CURRENT_KEY, body)
                r2_ok = True
            except OSError:
                summary["r2_current_error"] = True   # 只記有沒有失敗：錯誤內容可能帶網址，執行紀錄是公開的
            summary["r2_current"] = r2_ok
        d1_status = None
        if d1 is not None and table_missing:
            d1_status = MissingTable.status
        elif d1 is not None:
            try:
                write_flows(d1, text, version=version, as_of=months[-1], built_at=built_at)
                summary["version"] = version
            except (MissingTable, D1WriteLimit) as error:
                d1_status = error.status
        if d1_status:
            summary["d1_status"] = d1_status
            if not r2_ok:
                summary["status"] = d1_status
            elif summary["status"] in ("ok", "catching_up"):
                summary["status"] = d1_status + "_r2"   # 只寫進 R2：網站讀得到，算警告不算失敗
    elif table_missing:
        summary["status"] = MissingTable.status
    return summary


def exit_code(summary: Mapping[str, Any]) -> int:
    return 0 if summary.get("status") in ("ok", "catching_up", "skipped_no_table", "skipped_no_table_r2",
                                          "d1_write_limit_r2") else 1


MESSAGES = {
    "ok": "已更新：{first_month}～{last_month}（美股這一班補 {us_days_fetched} 天）",
    "catching_up": "已更新：{first_month}～{last_month}；美股還有 {us_days_left} 天待補，下一班接著補",
    "skipped_no_table": "資料表還沒建立：到 Actions 按 d1-migrate",
    "d1_write_limit": "D1 今天的免費寫入額度已用完，這一班沒寫進去；美股日檔已存進 R2，明天的排程會照常補上",
    "d1_write_limit_r2": "D1 今天的免費寫入額度已用完，這一班只寫進 R2；網站照樣讀得到 {first_month}～{last_month}，明天的排程會照常寫回 D1",
    "skipped_no_table_r2": "D1 資料表還沒建立，這一班只寫進 R2；網站照樣讀得到 {first_month}～{last_month}。之後到 Actions 按 d1-migrate",
    "no_bootstrap": "R2 chain-map/bootstrap/ 找不到樣品資料，組不出歷史月份",
    "no_key": "沒有 MASSIVE_API_KEY，美股這一班沒有補",
    "fetch_failed": "Massive 回應異常（{us_error}），已存的日檔保留，下一班重試；資料照已有的日子建好",
    "failed": "建置失敗（{error}）",
}


def plain_message(summary: Mapping[str, Any]) -> str:
    template = MESSAGES.get(str(summary.get("status")), "狀態 {status}")
    try:
        return template.format_map(defaultdict(str, summary))
    except (KeyError, ValueError):
        return str(summary.get("status"))


def write_step_summary(summary: Mapping[str, Any]) -> None:
    """GitHub 步驟摘要：一行白話（只有月份範圍與天數，沒有任何數字資料）。"""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"### AI 產業鏈資金版圖\n- {plain_message(summary)}\n")


def _status_line(summary: Mapping[str, Any]) -> str:
    return json.dumps(summary, ensure_ascii=False, separators=(",", ":"))


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AI 產業鏈資金版圖：每月資金資料")
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build")
    b.add_argument("--d1", action="store_true", help="從 D1 讀名單與歷史；正式建置寫回一列（要 CLOUDFLARE_ACCOUNT_ID／API_TOKEN）")
    b.add_argument("--r2", action="store_true", help="先從 R2 補齊美股日檔，抓完上傳新的")
    b.add_argument("--dry-run", action="store_true", help="不上傳 R2、不寫 D1、不寫檔，只印摘要")
    b.add_argument("--output", type=Path, help="另存一份 payload（本機檢查用；排程不用）")
    b.add_argument("--max-us-calls", type=int, default=MAX_US_CALLS)
    args = parser.parse_args(list(argv) if argv is not None else None)

    summary: dict[str, Any]
    try:
        d1 = D1Http.from_env() if args.d1 else None
        store = None
        if args.r2:
            import state_sync
            store = state_sync.R2Store.from_env()
        key = os.environ.get("MASSIVE_API_KEY", "").strip()
        source = None
        if key:
            from overseas_peers import MassiveSource
            source = MassiveSource(key)
        summary = build(d1=d1, store=store, source=source, dry_run=args.dry_run, output=args.output,
                        max_calls=args.max_us_calls)
    except ChainMapError as error:
        summary = {"status": error.status, "as_of": None, "months": 0, "us_days_fetched": 0}
        if error.status == "failed":
            summary["error"] = str(error)   # 這幾種訊息都是程式自己寫的白話（沒有網址、金鑰或資料）
    except Exception as error:  # noqa: BLE001 —— 執行紀錄是公開的：只印錯誤種類
        summary = {"status": "failed", "as_of": None, "months": 0, "us_days_fetched": 0, "error": type(error).__name__}
    if summary.get("status") not in ("ok", "catching_up"):
        print(("::warning::" if exit_code(summary) == 0 else "::error::") + plain_message(summary), file=sys.stderr)
    write_step_summary(summary)
    print(_status_line(summary))
    return exit_code(summary)


if __name__ == "__main__":
    raise SystemExit(main())
