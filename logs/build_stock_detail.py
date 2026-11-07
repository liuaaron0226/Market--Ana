"""把個股明細整理成每日前 20 名，輸出 web/detail-data.js（給籌碼圖點某一天時顯示）。
金額換算：法人用「買賣超股數 × 當日收盤價」估算金額（億元），並標明是估算；融資融券用張數變化。"""
import glob
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path("C:/Users/User/Desktop/Market--Ana")
sys.path.insert(0, str(ROOT))
from chip_streaks import current_top, rank_streak_top  # noqa: E402
SRC = ROOT / "data/market-snapshots/stock-detail"
TOP = 20


def num(value):
    text = str(value).replace(",", "").strip()
    if text in ("", "--", "X", "-"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def load(name, day):
    path = SRC / name / f"{name}-{day}.json"
    return json.loads(path.read_text(encoding="utf-8"))["data"] if path.exists() else None


def is_stock(code):
    return len(code) == 4 and code.isdigit() and not code.startswith("00")


def ranked(items, key, top=TOP):
    clean = [item for item in items if item[key] is not None]
    buy = sorted([i for i in clean if i[key] > 0], key=lambda i: -i[key])[:top]
    sell = sorted([i for i in clean if i[key] < 0], key=lambda i: i[key])[:top]
    return buy, sell


days = sorted({Path(p).stem.split("-")[-1] for p in glob.glob(str(SRC / "*" / "*.json"))})
out = {}
for day in days:
    iso = f"{day[:4]}-{day[4:6]}-{day[6:]}"
    entry = {}
    quotes = load("quotes", day)
    price = {}
    if quotes:
        table = next((t for t in quotes.get("tables", []) if "每日收盤行情" in str(t.get("title"))), None)
        if table:
            fields = table["fields"]
            ci, ni, ai, pi, si, di = (fields.index("證券代號"), fields.index("證券名稱"), fields.index("成交金額"), fields.index("收盤價"), fields.index("漲跌(+/-)"), fields.index("漲跌價差"))
            rows = []
            for row in table["data"]:
                code = row[ci].strip()
                close = num(row[pi])
                if close is not None:
                    price[code] = close
                if not is_stock(code):
                    continue
                sign = -1 if "-" in str(row[si]) or "green" in str(row[si]) else 1
                diff = num(row[di])
                prev = close - sign * diff if close is not None and diff is not None else None
                rows.append({"code": code, "name": row[ni].strip(), "amount_billion": round((num(row[ai]) or 0) / 1e8, 2), "close": close,
                             "change_pct": round(sign * diff / prev * 100, 2) if prev else None})
            entry["turnover_top"] = sorted(rows, key=lambda r: -r["amount_billion"])[:TOP]
            entry["turnover_all"] = rows
    t86 = load("t86", day)
    if t86 and t86.get("fields"):
        fields = t86["fields"]
        ci, ni = fields.index("證券代號"), fields.index("證券名稱")
        fi = fields.index("外陸資買賣超股數(不含外資自營商)")
        ti = fields.index("投信買賣超股數")
        items = []
        for row in t86["data"]:
            code = row[ci].strip()
            if not is_stock(code):
                continue
            close = price.get(code)
            to_billion = lambda shares: round(shares * close / 1e8, 2) if shares is not None and close else None
            fs, ts = num(row[fi]), num(row[ti])
            items.append({"code": code, "name": row[ni].strip(), "close": close,
                          "foreign_lots": round(fs / 1000) if fs is not None else None, "foreign_billion": to_billion(fs),
                          "trust_lots": round(ts / 1000) if ts is not None else None, "trust_billion": to_billion(ts)})
        entry["foreign_buy"], entry["foreign_sell"] = ranked(items, "foreign_billion")
        entry["trust_buy"], entry["trust_sell"] = ranked(items, "trust_billion")
        entry["foreign_all"] = entry["trust_all"] = items
    margin = load("margin", day)
    if margin:
        table = next((t for t in margin.get("tables", []) if "融資融券彙總" in str(t.get("title"))), None)
        if table:
            items = []
            for row in table["data"]:
                code = row[0].strip()
                if not is_stock(code):
                    continue
                mp, mt, sp, st = num(row[5]), num(row[6]), num(row[11]), num(row[12])
                items.append({"code": code, "name": row[1].strip(),
                              "margin_change": int(mt - mp) if mp is not None and mt is not None else None, "margin_balance": int(mt) if mt is not None else None,
                              "short_change": int(st - sp) if sp is not None and st is not None else None, "short_balance": int(st) if st is not None else None})
            entry["margin_up"], entry["margin_down"] = ranked(items, "margin_change")
            entry["short_up"], entry["short_down"] = ranked(items, "short_change")
            entry["margin_all"] = items
    out[iso] = entry

# 本人 11-07：每張卡另加「目前前 10＋連續天數」（依最新一天排序；連續＝往回數同方向幾天）。
ISO = sorted(out)
TOP_NOW = 10
series = {"foreign": {}, "trust": {}, "margin": {}, "short": {}, "amount": {}}
names = {}
for d in ISO:
    e = out[d]
    for key, table, field in (("foreign", "foreign_all", "foreign_billion"), ("trust", "trust_all", "trust_billion"),
                              ("margin", "margin_all", "margin_change"), ("short", "margin_all", "short_change"), ("amount", "turnover_all", "amount_billion")):
        for row in e.get(table, []):
            names[row["code"]] = row["name"]
            if row.get(field) is not None:
                series[key].setdefault(row["code"], {})[d] = row[field]
for e in out.values():
    for k in ("foreign_all", "trust_all", "margin_all", "turnover_all"):
        e.pop(k, None)


def named(items):
    return [{"code": r["code"], "name": names.get(r["code"], ""), "value": r["value"], "streak": r["streak"], "capped": r["capped"]} for r in items]


current = {}
for key, (buy_name, sell_name) in {"foreign": ("foreign_buy", "foreign_sell"), "trust": ("trust_buy", "trust_sell"),
                                   "margin": ("margin_up", "margin_down"), "short": ("short_up", "short_down")}.items():
    b, s = current_top(series[key], ISO, TOP_NOW)
    current[buy_name], current[sell_name] = named(b), named(s)
current["turnover_top"] = named(rank_streak_top(series["amount"], ISO, TOP_NOW, TOP))
# 期貨卡：外資台指期淨部位連續變得更空幾天（chips-data 的期貨序列，淨口數變小＝空單增加）。
chips_text = (ROOT / "web/chips-data.js").read_text(encoding="utf-8").strip()
chips = json.loads(chips_text[chips_text.index("=") + 1:].strip().rstrip(";"))
fut = chips.get("futures", [])
grow = 0
for i in range(len(fut) - 1, 0, -1):
    if fut[i]["net_contracts"] < fut[i - 1]["net_contracts"]:
        grow += 1
    else:
        break
current["futures_short_growing_days"] = grow
current["futures_days_available"] = max(0, len(fut) - 1)

data = {
    "generated_at": datetime.now().astimezone().isoformat(),
    "days": sorted(out),
    "notes": {
        "money": "法人金額＝買賣超股數×當日收盤價，是估算值；張數為官方股數÷1000。",
        "scope": "上市個股（四碼代號），不含 ETF（00 開頭）、權證與上櫃。",
        "sources": ["https://www.twse.com.tw/rwd/zh/fund/T86", "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN", "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"],
    },
    "by_date": out,
    "current": {"as_of": ISO[-1] if ISO else None, "days_available": len(ISO), **current},
}
target = ROOT / "web/detail-data.js"
target.write_text("window.MARKET_DETAIL = " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n", encoding="utf-8")
coverage = {k: [d for d in out if out[d].get(k)] for k in ("trust_buy", "foreign_buy", "margin_up", "turnover_top")}
print(json.dumps({"days": len(out), "bytes": target.stat().st_size, "coverage": {k: len(v) for k, v in coverage.items()}}, ensure_ascii=False))
