"""產生 web/chips-data.js：外資現貨、外資期貨淨部位、融資、融券的日序列（給籌碼卡與白話分析用）。"""
import glob
import json
from datetime import datetime
from pathlib import Path

ROOT = Path("C:/Users/User/Desktop/Market--Ana")
text = (ROOT / "web/analysis-data.js").read_text(encoding="utf-8").strip()
A = json.loads(text[len("window.MARKET_ANALYSIS = "):].rstrip(";"))
num = lambda s: int(str(s).replace(",", ""))

margin = []
for path in sorted(glob.glob(str(ROOT / "data/market-snapshots/margin/mi-margn-*.json"))):
    payload = json.load(open(path, encoding="utf-8"))["data"]
    rows = {row[0]: row for row in payload["tables"][0]["data"]}
    stamp = Path(path).stem[-8:]
    margin.append({
        "date": f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:]}",
        "margin_balance_billion": round(num(rows["融資金額(仟元)"][5]) / 1e5, 2),
        "margin_units": num(rows["融資(交易單位)"][5]),
        "short_units": num(rows["融券(交易單位)"][5]),
    })
foreign = [{"date": r["date"], "net_billion": round(r["foreign"]["net"] / 1e8, 2), "trust_billion": round(r["trust"]["net"] / 1e8, 2)} for r in A["institutional"][-60:]]
futures = [{"date": r["date"], "net_contracts": r["foreign_oi_net_contracts"]} for r in A["futures"][-60:]]
close_by_date = {r["date"]: r["close"] for r in A["daily"]}
for row in margin:
    row["index_close"] = close_by_date.get(row["date"])

data = {
    "generated_at": datetime.now().astimezone().isoformat(),
    "analysis_cutoff": A["meta"]["cutoff_date"],
    "margin_latest_date": margin[-1]["date"] if margin else None,
    "sources": {
        "margin": "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN（信用交易統計，上市）",
        "foreign": "https://www.twse.com.tw/fund/BFI82U",
        "futures": "https://www.taifex.com.tw/cht/3/futContractsDateDown（臺股期貨，外資）",
    },
    "margin": margin,
    "foreign": foreign,
    "futures": futures,
}
(ROOT / "web/chips-data.js").write_text("window.MARKET_CHIPS = " + json.dumps(data, ensure_ascii=False) + ";\n", encoding="utf-8")
print(json.dumps({"margin": len(margin), "margin_latest": data["margin_latest_date"], "foreign": len(foreign), "futures": len(futures)}, ensure_ascii=False))
