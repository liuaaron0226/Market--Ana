"""個股缺行情只按已取得的市場交易日計算；資料缺口隨既有版圖快照一起發布。"""

from datetime import date, timedelta
import sqlite3


def _trailing(code, days, since, status, last_good=None):
    missing = []
    for day in reversed(days):
        if since and day <= since:
            break
        state = status(day, code)
        if state == "closed":
            continue
        if state != "missing":
            if state == "received":
                last_good = day
            break
        missing.append(day)
    if len(missing) >= 3:
        return {"days": missing[::-1], "lastGood": last_good}
    return None


def market_health(chain, data_dir, price_dbs, us_days, ticker, prior_us_codes):
    gaps, through = {}, {}
    since = chain.get("roster_since", {})
    otc = set(chain.get("otc", []))
    tw_codes = {row[0] for node in chain["nodes"] for row in node.get("tw", [])}
    coverage = []
    for i, filename in enumerate(price_dbs):
        db = sqlite3.connect(f"file:{(data_dir / filename).as_posix()}?mode=ro", uri=True)
        try:
            codes = [c for c in tw_codes if (c in otc) == bool(i)]
            # 早期工作集未保存全市場來源日帳時，不從單檔或月合計猜交易日。
            if not db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_days'"
            ).fetchone():
                coverage.append((set(), {}))
                continue
            days = {d for d, in db.execute("SELECT date FROM source_days WHERE rows>0")}
            last = {}
            for code in codes:
                row = db.execute(
                    "SELECT date FROM daily WHERE code=? AND turnover IS NOT NULL "
                    "AND date IN (SELECT date FROM source_days WHERE rows>0) ORDER BY date DESC LIMIT 1",
                    (code,),
                ).fetchone()
                last[code] = row[0] if row else None
            coverage.append((days, last))
        finally:
            db.close()
    sessions = sorted(coverage[0][0] | coverage[1][0])
    for name, (dates, latest) in zip(("listed", "otc"), coverage):
        through[name] = max(dates, default=None)
        if not dates:
            continue
        observed = [d for d in sessions if d <= through[name]]
        for code, good in latest.items():

            def tw_status(day, _):
                if day not in dates:
                    return "unknown"
                return "received" if day == good else "missing"

            item = _trailing(code, observed, since.get("TW " + code), tw_status, good)
            if item:
                gaps["tw:" + code] = item
    through["us"] = max((d for d, e in us_days.items() if not e["holiday"]), default=None)
    if us_days:
        # 未取得的平日無法證明休市或單股缺報，不能把兩段缺口串在一起。
        first, last = date.fromisoformat(min(us_days)), date.fromisoformat(max(us_days))
        calendar = [
            (first + timedelta(days=i)).isoformat()
            for i in range((last - first).days + 1)
            if (first + timedelta(days=i)).weekday() < 5
        ]

        def us_status(day, code):
            entry = us_days.get(day)
            if not entry:
                return "unknown"
            if entry["holiday"]:
                return "closed"
            symbol = ticker(code, day)
            if entry["tickers"].get(symbol, {}).get("turnover") is not None:
                return "received"
            tracked = entry.get("requested")
            if (tracked is None and code in prior_us_codes) or (tracked is not None and symbol in tracked):
                return "missing"
            return "unknown"

        for code in {r[0] for n in chain["nodes"] for r in n.get("us", [])}:
            item = _trailing(code, calendar, since.get("US " + code), us_status)
            if item:
                if not item["lastGood"]:
                    item["lastGood"] = next(
                        (d for d in reversed(calendar) if us_status(d, code) == "received"), None
                    )
                gaps["us:" + code] = item
    return {"through": through, "gaps": gaps}
