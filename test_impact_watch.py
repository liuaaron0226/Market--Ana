"""盯項（本人 2026-11-20「接下來要看的條件由系統盯」，.scratch/impact-watch 票 03）：大盤的數字型，從記下來到每天查。

假的模型、本機 SQLite 套同一份變更檔；不呼叫任何模型、不連網路。"""
import json
import sqlite3
import unittest
from datetime import date, timedelta
from pathlib import Path

import daily_ingest
import impact_watch as iw
import market_news as mn
import test_market_news as tmn
import trading_days as td

ROOT = Path(__file__).resolve().parent
SCHEMA_0034 = (ROOT / "cloud/migrations/0034_impact_watch.sql").read_text(encoding="utf-8")
HOLIDAYS = daily_ingest.parse_holiday_schedule(daily_ingest.HOLIDAY_SEED.read_bytes())   # 證交所 2026 年休市日曆（專案帶的種子）
NOW = "2026-11-19T06:05:00Z"


def weekdays(end: str, count: int) -> list[str]:
    days, current = [], date.fromisoformat(end)
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current.isoformat())
        current -= timedelta(days=1)
    return days[::-1]


def daily(closes, end="2026-11-18"):
    return [{"date": day, "open": close, "high": close + 50, "low": close - 50, "close": close}
            for day, close in zip(weekdays(end, len(closes)), closes)]


def flows(nets, end="2026-11-18"):
    """nets：外資買賣超（億），最後一個是 end 那一天。"""
    return [{"date": day, "foreign": {"net": net * 100_000_000}} for day, net in zip(weekdays(end, len(nets)), nets)]


RISING = [30000 + 40 * index for index in range(260)]          # 一路漲：收盤在月、季、年線之上
FALLING = [45000 - 40 * index for index in range(260)]         # 一路跌：收盤在三條線之下


def facts(closes=RISING, nets=(120, -80, -60), end="2026-11-18"):
    return iw.market_facts(daily(closes, end), flows(nets, end))


class Db:
    def __init__(self, schema=SCHEMA_0034):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript(schema)

    def query(self, sql):
        return [dict(row) for row in self.db.execute(sql)]

    def execute(self, script):
        self.db.executescript(script)

    def items(self, where="1 = 1"):
        return self.query(f"SELECT * FROM impact_watch_items WHERE {where} ORDER BY id")

    def create(self, picks, trade_date="2026-11-19"):
        script, report = iw.creation_sql(picks, iw.active_items(self.query, trade_date), trade_date=trade_date, now=NOW, holidays=HOLIDAYS)
        self.execute(script)
        return report

    def check(self, day_facts):
        script, report = iw.check_sql(self.query("SELECT * FROM impact_watch_items WHERE status = 'watching'"), day_facts,
                                      now=NOW, holidays=HOLIDAYS)
        self.execute(script)
        return report


def pick(key, base=None, impact=3, **over):
    offered = {row["id"]: row for row in iw.candidates(base or facts())}
    return {"condition_key": key, "label": offered[key]["name"], "params": offered[key]["params"], "note": "一句說明",
            "impact": impact, "prepare": ["如果跌破月線，就先檢查持股比重"], "source_ref": "2026-11-19-close#n1", **over}


class TradingDayTests(unittest.TestCase):
    def test_counting_forward_skips_weekends(self):
        self.assertEqual(td.add_trading_days("2026-11-20", 1, HOLIDAYS), "2026-11-23")
        self.assertEqual(td.add_trading_days("2026-11-20", 0, HOLIDAYS), "2026-11-20")
        self.assertEqual(td.add_trading_days("2026-11-21", 1, HOLIDAYS), "2026-11-23", "從週六起算，下一個交易日是週一")

    def test_counting_forward_skips_the_lunar_new_year_break(self):
        # 2026 春節：2/11（三）是節前最後交易日，2/12～2/20 休市，2/23（一）開紅盤
        self.assertEqual(td.add_trading_days("2026-02-11", 1, HOLIDAYS), "2026-02-23")
        self.assertEqual(td.add_trading_days("2026-02-06", 5, HOLIDAYS), "2026-02-24")
        self.assertEqual(td.add_trading_days("2026-01-30", 20, HOLIDAYS), "2026-03-11", "跨春節與 228 補假的第 20 個交易日")

    def test_counting_the_trading_days_between_two_dates(self):
        self.assertEqual(td.trading_days_between("2026-02-11", "2026-02-23", HOLIDAYS), 1)
        self.assertEqual(td.trading_days_between("2026-11-16", "2026-11-20", HOLIDAYS), 4)
        self.assertEqual(td.trading_days_between("2026-11-20", "2026-11-20", HOLIDAYS), 0)
        self.assertEqual(td.trading_days_between("2026-11-20", "2026-11-18", HOLIDAYS), 0)

    def test_agrees_with_counting_forward(self):
        for start in ("2026-02-03", "2026-09-22", "2026-11-19"):
            self.assertEqual(td.trading_days_between(start, td.add_trading_days(start, 20, HOLIDAYS), HOLIDAYS), 20)


class CandidateTests(unittest.TestCase):
    def test_the_days_numbers_come_from_the_published_market_data(self):
        made = facts()
        self.assertEqual(made["date"], "2026-11-18")
        self.assertEqual(made["close"], 40360.0)
        self.assertEqual(made["ma"], {"20": 39980.0, "60": 39180.0, "240": 35580.0})
        self.assertEqual((made["high"], made["low"]), (40410.0, 39550.0))
        self.assertEqual(made["flow"], {"date": "2026-11-18", "net": -60.0, "side": "sell", "days": 2})

    def test_too_little_history_gives_no_numbers_and_a_short_one_skips_the_long_averages(self):
        self.assertIsNone(iw.market_facts(daily(RISING[:10]), []))
        short = iw.market_facts(daily(RISING[:30]), [])
        self.assertEqual(sorted(short["ma"]), ["20"])
        self.assertNotIn("flow", short)
        self.assertEqual([row["id"] for row in iw.candidates(short)], ["below_ma20", "above_high", "below_low"])

    def test_the_list_is_small_and_only_offers_what_has_not_happened_yet(self):
        offered = iw.candidates(facts())
        self.assertEqual([(row["id"], row["name"]) for row in offered], [
            ("below_ma20", "加權指數收盤跌破月線"), ("below_ma60", "加權指數收盤跌破季線"), ("below_ma240", "加權指數收盤跌破年線"),
            ("above_high", "加權指數收盤突破近 20 日高點（40,410）"), ("below_low", "加權指數收盤跌破近 20 日低點（39,550）"),
            ("foreign_sell_streak", "外資連續賣超達 4 天"), ("foreign_turn_buy", "外資由賣超轉為買超")])
        self.assertTrue(all(row["now"] and row["params"] for row in offered))
        below = iw.candidates(facts(FALLING, nets=(-50, 30)))
        self.assertEqual([row["id"] for row in below][:3], ["above_ma20", "above_ma60", "above_ma240"])
        self.assertEqual([(row["id"], row["name"]) for row in below][-2:],
                         [("foreign_buy_streak", "外資連續買超達 3 天"), ("foreign_turn_sell", "外資由買超轉為賣超")])
        self.assertEqual(iw.candidates(None), [])


class EvaluateTests(unittest.TestCase):
    def test_a_close_back_above_the_average_is_met_with_the_actual_numbers(self):
        base = facts(FALLING)
        item = pick("above_ma20", base)
        self.assertEqual(iw.evaluate(item, base), ("watching", None), "建立時依據的那一天不算")
        still = facts([*FALLING, 34000], end="2026-11-19")
        self.assertEqual(iw.evaluate(item, still), ("watching", None))
        back = facts([*FALLING, 36000], end="2026-11-19")
        self.assertEqual(iw.evaluate(item, back), ("met", "成立：11/19 收 36,000，站回月線（35,050）。"))

    def test_a_close_below_the_average_or_the_recent_low(self):
        base = facts()
        drop = facts([*RISING, 39900], end="2026-11-19")
        self.assertEqual(iw.evaluate(pick("below_ma20", base), drop), ("met", "成立：11/19 收 39,900，跌破月線（39,995）。"))
        self.assertEqual(iw.evaluate(pick("below_ma60", base), drop), ("watching", None))
        crash = facts([*RISING, 39500], end="2026-11-19")
        self.assertEqual(iw.evaluate(pick("below_low", base), crash), ("met", "成立：11/19 收 39,500，跌破近 20 日低點 39,550。"))
        jump = facts([*RISING, 40500], end="2026-11-19")
        self.assertEqual(iw.evaluate(pick("above_high", base), jump), ("met", "成立：11/19 收 40,500，突破近 20 日高點 40,410。"))
        self.assertEqual(iw.evaluate(pick("above_high", base), facts([*RISING, 40410], end="2026-11-19")), ("watching", None),
                         "剛好碰到高點不算突破")

    def test_a_selling_streak_is_met_broken_or_still_running(self):
        base = facts()                                   # 已連續賣超 2 天，盯到 4 天
        item = pick("foreign_sell_streak", base)
        third = facts([*RISING, 40400], nets=(120, -80, -60, -90), end="2026-11-19")
        self.assertEqual(iw.evaluate(item, third), ("watching", None))
        fourth = facts([*RISING, 40400, 40440], nets=(120, -80, -60, -90, -912), end="2026-11-20")
        self.assertEqual(iw.evaluate(item, fourth), ("met", "成立：外資連續賣超 4 天，11/20 賣超 912 億。"))
        broken = facts([*RISING, 40400], nets=(120, -80, -60, 52), end="2026-11-19")
        self.assertEqual(iw.evaluate(item, broken), ("failed", "否定：11/19 外資買超 52 億，連續賣超沒有延續到 4 天。"))
        self.assertEqual(iw.evaluate(pick("foreign_turn_buy", base), broken), ("met", "成立：11/19 外資轉為買超 52 億。"))
        self.assertEqual(iw.evaluate(pick("foreign_turn_buy", base), third), ("watching", None))

    def test_flow_conditions_wait_for_flow_data_newer_than_the_day_they_were_made(self):
        base = facts()
        late = iw.market_facts(daily([*RISING, 40400], "2026-11-19"), flows((120, -80, -60)))   # 指數有新的一天、法人還沒有
        self.assertEqual(iw.evaluate(pick("foreign_turn_buy", base), late), ("watching", None))
        self.assertEqual(iw.evaluate(pick("foreign_sell_streak", base), late), ("watching", None))


class StoreTests(unittest.TestCase):
    def test_a_picked_condition_becomes_an_item_that_expires_on_the_20th_trading_day(self):
        db = Db()
        report = db.create([pick("below_ma20")])
        self.assertEqual(report["created"], ["market:below_ma20:2026-11-19"])
        row = db.items()[0]
        self.assertEqual({key: row[key] for key in ("source_block", "source_date", "source_ref", "subject", "kind", "condition_key", "label",
                                                    "note", "impact", "created_date", "expires_on", "days_left", "status", "visibility",
                                                    "push_sent_at", "result_date")},
                         {"source_block": "market_news", "source_date": "2026-11-19", "source_ref": "2026-11-19-close#n1", "subject": "market",
                          "kind": "numeric", "condition_key": "below_ma20", "label": "加權指數收盤跌破月線", "note": "一句說明", "impact": 3,
                          "created_date": "2026-11-19", "expires_on": "2026-12-17", "days_left": 20, "status": "watching",
                          "visibility": "all", "push_sent_at": None, "result_date": None})
        self.assertEqual(json.loads(row["params"]), {"ma": 20, "as_of": "2026-11-18"})
        self.assertEqual(json.loads(row["prepare"]), ["如果跌破月線，就先檢查持股比重"])

    def test_the_same_condition_is_only_watched_once(self):
        db = Db()
        db.create([pick("below_ma20", impact=2), pick("below_ma20", impact=3, note="影響大的那一則"), pick("above_high")])
        self.assertEqual([(row["condition_key"], row["impact"], row["note"]) for row in db.items()],
                         [("above_high", 3, "一句說明"), ("below_ma20", 3, "影響大的那一則")], "同一班挑了兩次：留影響力高的")
        report = db.create([pick("below_ma20")], trade_date="2026-11-20")
        self.assertEqual((report["created"], report["already_watching"]), ([], 1))
        self.assertEqual(len(db.items()), 2)

    def test_running_the_same_script_twice_does_not_duplicate(self):
        db = Db()
        script, _ = iw.creation_sql([pick("below_ma20")], [], trade_date="2026-11-19", now=NOW, holidays=HOLIDAYS)
        db.execute(script)
        db.execute(script)
        self.assertEqual(len(db.items()), 1)

    def test_a_condition_with_a_fresh_result_is_not_rebuilt_until_it_leaves_the_home_page(self):
        db = Db()
        db.create([pick("below_ma20")])
        db.check(facts([*RISING, 39900], end="2026-11-19"))
        self.assertEqual(db.create([pick("below_ma20")], trade_date="2026-11-20")["created"], [])
        self.assertEqual(db.create([pick("below_ma20")], trade_date="2026-11-25")["created"], ["market:below_ma20:2026-11-25"],
                         "首頁收掉之後可以再盯一次")

    def test_at_most_ten_are_watched_and_the_higher_impact_stays(self):
        db = Db()
        keys = ["below_ma20", "below_ma60", "below_ma240", "above_high", "below_low", "foreign_sell_streak", "foreign_turn_buy"]
        other = [row["id"] for row in iw.candidates(facts(FALLING, nets=(-50, 30)))]
        both = [pick(key, impact=2) for key in keys] + [pick(key, facts(FALLING, nets=(-50, 30)), impact=2) for key in other[:3]]
        db.create(both, trade_date="2026-11-18")
        self.assertEqual(len(db.items("status = 'watching'")), 10)
        base = facts(FALLING, nets=(-50, 30))
        report = db.create([pick("foreign_buy_streak", base, impact=3), pick("foreign_turn_sell", base, impact=1)])
        self.assertEqual(report["created"], ["market:foreign_buy_streak:2026-11-19"])
        self.assertEqual(report["over_limit"], 1, "影響力低的新條件排不進去")
        self.assertEqual(len(report["closed_for_room"]), 1)
        self.assertEqual(len(db.items("status = 'watching'")), 10)
        closed = db.items("status = 'expired'")[0]
        self.assertEqual((closed["impact"], closed["result_note"], closed["show_until"]), (2, iw.FULL_NOTE, None), "讓位的安靜收掉，首頁不留")
        self.assertEqual(db.items("condition_key = 'foreign_buy_streak'")[0]["impact"], 3)

    def test_same_impact_keeps_the_newer_ones(self):
        db = Db()
        base = facts(FALLING, nets=(-50, 30))
        old = ["below_ma20", "below_ma60", "below_ma240", "above_high", "below_low", "foreign_sell_streak", "foreign_turn_buy"]
        db.create([pick(key, impact=2) for key in old], trade_date="2026-11-16")
        db.create([pick(key, base, impact=2) for key in ("above_ma20", "above_ma60", "above_ma240")], trade_date="2026-11-17")
        report = db.create([pick("foreign_buy_streak", base, impact=2)], trade_date="2026-11-19")
        self.assertEqual(report["created"], ["market:foreign_buy_streak:2026-11-19"])
        self.assertEqual(len(report["closed_for_room"]), 1)
        self.assertTrue(report["closed_for_room"][0].endswith(":2026-11-16"), report)


    def test_a_condition_closed_for_room_today_is_not_rebuilt_the_same_day(self):
        db = Db()
        db.create([pick("below_ma20", impact=2)])
        db.execute("UPDATE impact_watch_items SET status = 'expired', result_date = '2026-11-19'")   # 同一天稍後讓位被收掉
        report = db.create([pick("below_ma20", impact=3), pick("above_high")])
        self.assertEqual(report["created"], ["market:above_high:2026-11-19"], "同一個識別碼不再寫一次")
        self.assertEqual([(row["condition_key"], row["status"]) for row in db.items()], [("above_high", "watching"), ("below_ma20", "expired")])
        self.assertEqual(db.create([pick("below_ma20")], trade_date="2026-11-20")["created"], ["market:below_ma20:2026-11-20"], "隔天可以再盯")


class DailyCheckTests(unittest.TestCase):
    def test_one_broken_row_does_not_stop_the_others(self):
        db = Db()
        db.create([pick("below_ma20"), pick("below_low")])
        db.execute("UPDATE impact_watch_items SET params = '{}' WHERE condition_key = 'below_low'")   # 少了價位
        report = db.check(facts([*RISING, 39500], end="2026-11-19"))
        rows = {row["condition_key"]: row["status"] for row in db.items()}
        self.assertEqual(rows, {"below_ma20": "met", "below_low": "watching"})
        self.assertEqual(report["errors"], [{"id": "market:below_low:2026-11-19", "error": "KeyError"}])
        self.assertEqual(db.query("SELECT data_date FROM impact_watch_facts")[0]["data_date"], "2026-11-19", "當天的數字照樣存")

    def test_rerunning_an_older_day_does_not_overwrite_newer_numbers(self):
        db = Db()
        db.check(facts([*RISING, 39900], end="2026-11-19"))
        db.check(facts())                                                  # 重跑 11-18
        self.assertEqual(db.query("SELECT data_date FROM impact_watch_facts")[0]["data_date"], "2026-11-19")
        db.check(facts([*RISING, 39900, 39950], end="2026-11-20"))
        self.assertEqual(db.query("SELECT data_date FROM impact_watch_facts")[0]["data_date"], "2026-11-20")

    def test_results_are_written_with_numbers_and_stay_three_more_trading_days(self):
        db = Db()
        db.create([pick("below_ma20"), pick("foreign_sell_streak"), pick("below_ma60")])
        report = db.check(facts([*RISING, 39900], nets=(120, -80, -60, 52), end="2026-11-19"))
        self.assertEqual((report["checked"], len(report["met"]), len(report["failed"]), report["watching"]), (3, 1, 1, 1))
        rows = {row["condition_key"]: row for row in db.items()}
        self.assertEqual({key: rows["below_ma20"][key] for key in ("status", "result_date", "result_note", "show_until", "days_left")},
                         {"status": "met", "result_date": "2026-11-19", "result_note": "成立：11/19 收 39,900，跌破月線（39,995）。",
                          "show_until": "2026-11-24", "days_left": None})
        self.assertEqual((rows["foreign_sell_streak"]["status"], rows["foreign_sell_streak"]["result_note"]),
                         ("failed", "否定：11/19 外資買超 52 億，連續賣超沒有延續到 4 天。"))
        self.assertEqual((rows["below_ma60"]["status"], rows["below_ma60"]["days_left"]), ("watching", 20))
        stored = db.query("SELECT data_date, payload FROM impact_watch_facts WHERE subject = 'market'")[0]
        self.assertEqual((stored["data_date"], json.loads(stored["payload"])["close"]), ("2026-11-19", 39900.0))

    def test_days_left_counts_down_and_the_20th_trading_day_closes_it_quietly(self):
        db = Db()
        db.create([pick("below_ma240")])                                   # 11-19 建立，12-17 到期
        closes = list(RISING)
        for offset, day in enumerate(weekdays("2026-12-16", 19), 1):       # 11-20 … 12-16
            closes.append(40400 + offset)
            db.check(facts(closes, end=day))
        row = db.items()[0]
        self.assertEqual((row["status"], row["days_left"]), ("watching", 1))
        closes.append(40500)
        report = db.check(facts(closes, end="2026-12-17"))
        row = db.items()[0]
        self.assertEqual(report["expired"], ["market:below_ma240:2026-11-19"])
        self.assertEqual((row["status"], row["result_date"], row["result_note"], row["show_until"]), ("expired", "2026-12-17", None, None))

    def test_expiry_counts_trading_days_across_a_long_break(self):
        db = Db()
        base = facts(end="2026-01-29")
        db.create([pick("below_ma240", base)], trade_date="2026-01-30")
        self.assertEqual(db.items()[0]["expires_on"], "2026-03-11")
        closes = [*RISING, 40400]
        db.check(facts(closes, end="2026-02-11"))                          # 春節前最後一個交易日
        self.assertEqual(db.items()[0]["days_left"], td.trading_days_between("2026-02-11", "2026-03-11", HOLIDAYS))
        db.check(facts([*closes, 40420], end="2026-03-10"))
        self.assertEqual((db.items()[0]["status"], db.items()[0]["days_left"]), ("watching", 1))
        db.check(facts([*closes, 40420, 40430], end="2026-03-11"))
        self.assertEqual(db.items()[0]["status"], "expired")

    def test_met_on_the_last_day_counts_as_met(self):
        db = Db()
        db.create([pick("below_ma20")])
        db.check(facts([*RISING, 39000], end="2026-12-17"))
        self.assertEqual(db.items()[0]["status"], "met")

    def test_the_daily_job_reads_the_published_pack_and_skips_quietly_without_the_table(self):
        db = Db()
        db.create([pick("below_ma20")])
        analysis = {"daily": daily([*RISING, 39900], "2026-11-19"), "institutional": flows((120, -80, -60, 52), "2026-11-19")}
        dry = iw.check(analysis, now=NOW, query=db.query, execute=db.execute, dry_run=True, holidays=HOLIDAYS)
        self.assertEqual((dry["status"], len(dry["met"]), db.items()[0]["status"]), ("ok", 1, "watching"), "乾跑不寫")
        report = iw.check(analysis, now=NOW, query=db.query, execute=db.execute, holidays=HOLIDAYS)
        self.assertEqual((report["status"], report["data_date"], db.items()[0]["status"]), ("ok", "2026-11-19", "met"))
        empty = Db(schema="CREATE TABLE other (x)")
        self.assertEqual(iw.check(analysis, now=NOW, query=empty.query, execute=empty.execute)["status"], "skipped")
        self.assertEqual(iw.check({"daily": []}, now=NOW, query=db.query, execute=db.execute)["status"], "skipped")

    def test_items_closed_long_ago_are_cleared(self):
        db = Db()
        db.create([pick("below_ma20"), pick("below_ma60")])
        db.check(facts([*RISING, 39900], end="2026-11-19"))
        later = [*RISING, 39900, *([41000] * 120)]
        db.check(facts(later, end="2027-05-06"))
        self.assertEqual([(row["condition_key"], row["status"]) for row in db.items()], [("below_ma60", "expired")],
                         "收了很久的那一筆清掉；今天才到期的那一筆還留著")


def with_track(picks_for):
    """假的 Flash：照原本的回答，再讓它挑要盯的條件。picks_for(kind, 這一則或總結) → track 的內容。"""
    def change(kind, output, _asked):
        if kind == "items":
            for entry in output["items"]:
                entry["track"] = picks_for("items", entry)
        elif kind == "summary":
            output["track"] = picks_for("summary", output)
    return tmn.Reply(change)


class NewsD1(tmn.D1):
    def __init__(self, day_facts=None, schema=tmn.SCHEMA + SCHEMA_0034):
        super().__init__(schema)
        if day_facts:
            self.db.execute("INSERT INTO impact_watch_facts VALUES ('market', ?, ?, ?)",
                            (day_facts["date"], json.dumps(day_facts, ensure_ascii=False), NOW))

    def watch_items(self):
        return self.query("SELECT * FROM impact_watch_items ORDER BY id")


def payloads(ask):
    return [(kind, json.JSONDecoder().raw_decode(text[text.index("INPUT:") + 6:])[0]) for kind, text in zip(ask.kinds, ask.prompts)]


class NewsRunTests(unittest.TestCase):
    """消息排程：模型從清單用編號挑，挑到的寫成盯項。"""

    def test_the_model_is_given_the_list_and_what_it_picks_becomes_items(self):
        d1 = NewsD1(facts())
        ask = with_track(lambda kind, entry: [{"id": "below_ma20", "why": "外資續賣時指數容易先破月線"}] if kind == "summary"
                         else [{"id": "foreign_sell_streak", "why": "賣超延續才是真的轉弱"}] if entry["tone"] == "down" else [])
        report, _, _ = tmn.run(d1, ask=ask)
        offered = [row["id"] for row in iw.candidates(facts())]
        for kind, payload in payloads(ask):
            if kind in ("items", "summary"):
                self.assertEqual([row["id"] for row in payload["watchable"]], offered, kind)
                self.assertEqual(sorted(payload["watchable"][0]), ["id", "name", "now"], "參數不給模型")
            else:
                self.assertNotIn("watchable", payload, "個股新聞的判讀不挑大盤條件")
        rows = {row["condition_key"]: row for row in d1.watch_items()}
        self.assertEqual(sorted(rows), ["below_ma20", "foreign_sell_streak"])
        streak = rows["foreign_sell_streak"]
        self.assertEqual((streak["label"], streak["note"], streak["impact"], streak["source_date"], streak["status"], streak["visibility"]),
                         ("外資連續賣超達 4 天", "賣超延續才是真的轉弱", 3, "2026-11-19", "watching", "all"))
        self.assertEqual(json.loads(streak["prepare"]), ["如果外資連續賣超，就先檢查權值股佔多少"], "原本寫的準備跟著存")
        self.assertRegex(streak["source_ref"], r"^2026-11-19-close#n\d+$")
        self.assertEqual((rows["below_ma20"]["source_ref"], rows["below_ma20"]["impact"]), ("2026-11-19-close#summary", 3))
        self.assertEqual(json.loads(rows["below_ma20"]["prepare"]), ["如果加權跌破月線，就先檢查持股比重"])
        watch = report["diagnosis"]["watch"]
        self.assertEqual((watch["offered"], watch["picked"], sorted(watch["created"]), watch["unknown_ids"]),
                         (7, 2, ["market:below_ma20:2026-11-19", "market:foreign_sell_streak:2026-11-19"], []))
        self.assertEqual(d1.query("SELECT conditions FROM market_news_runs")[0]["conditions"], '["外資是否連續賣超"]', "人話的條件照舊存")

    def test_an_id_that_is_not_on_the_list_is_dropped_and_the_rest_kept(self):
        d1 = NewsD1(facts())
        ask = with_track(lambda kind, entry: [{"id": "taiex_above_50000", "why": "自己編的"}, {"id": "below_ma60", "why": "季線是中期多空分界"},
                                               "not-an-object"] if kind == "summary" else [])
        report, _, _ = tmn.run(d1, ask=ask)
        self.assertEqual([row["condition_key"] for row in d1.watch_items()], ["below_ma60"])
        self.assertEqual(report["diagnosis"]["watch"]["unknown_ids"], ["taiex_above_50000", "not-an-object"])
        self.assertEqual(d1.query("SELECT summary FROM market_news_runs")[0]["summary"], "台股今天下跌，主要是外資賣超。", "總結照樣在")

    def test_a_later_shift_picking_the_same_condition_does_not_build_it_again(self):
        d1 = NewsD1(facts())
        picks = lambda kind, entry: [{"id": "below_ma20", "why": "月線"}] if kind == "summary" else []  # noqa: E731
        tmn.run(d1, ask=with_track(picks), slot="midday")
        report, _, _ = tmn.run(d1, ask=with_track(picks), slot="close")
        self.assertEqual(len(d1.watch_items()), 1)
        self.assertEqual((report["diagnosis"]["watch"]["created"], report["diagnosis"]["watch"]["already_watching"]), ([], 1))
        self.assertEqual(d1.watch_items()[0]["source_ref"], "2026-11-19-midday#summary", "記的是第一次寫的那一班")

    def test_minor_news_does_not_start_a_watch_and_two_picks_is_the_most(self):
        d1 = NewsD1(facts())
        every = [{"id": key, "why": "說明"} for key in ("below_ma20", "below_ma60", "below_ma240")]
        ask = with_track(lambda kind, entry: every if kind == "items" else [])

        def minor(kind, output, asked):   # 全部判成影響最小
            if kind == "items":
                for entry in output["items"]:
                    entry["impact"] = "1"
        low = tmn.Reply(lambda kind, output, asked: (ask.change(kind, output, asked), minor(kind, output, asked)))
        tmn.run(d1, ask=low)
        self.assertEqual(d1.watch_items(), [])
        tmn.run(NewsD1(facts()), ask=ask)
        d2 = NewsD1(facts())
        tmn.run(d2, ask=ask)
        self.assertEqual([row["condition_key"] for row in d2.watch_items()], ["below_ma20", "below_ma60"])

    def test_a_note_that_reads_like_an_order_is_dropped_but_the_item_is_kept(self):
        d1 = NewsD1(facts())
        ask = with_track(lambda kind, entry: [{"id": "below_ma20", "why": "跌破就建議賣出持股"}] if kind == "summary" else [])
        report, _, _ = tmn.run(d1, ask=ask)
        self.assertEqual([(row["condition_key"], row["note"]) for row in d1.watch_items()], [("below_ma20", None)])
        self.assertEqual(report["diagnosis"]["points"]["text_rejected"], {"track": 1})

    def test_without_the_days_numbers_nothing_is_offered_and_the_news_is_unchanged(self):
        for d1 in (NewsD1(None), NewsD1(None, schema=tmn.SCHEMA), NewsD1(facts(end="2026-11-10"))):   # 沒檢查過／0034 沒套／數字太舊
            ask = with_track(lambda kind, entry: [{"id": "below_ma20", "why": "月線"}])
            report, _, _ = tmn.run(d1, ask=ask)
            self.assertTrue(all("watchable" not in payload for _, payload in payloads(ask)))
            self.assertEqual(report["diagnosis"]["watch"], {"offered": 0, "unknown_ids": []})
            self.assertEqual(report["run_status"], "ok")
            self.assertEqual(len(d1.query("SELECT 1 FROM market_news_items")), 4)
        self.assertEqual(NewsD1(None).watch_items(), [])

    def test_a_failed_watch_write_does_not_lose_the_news(self):
        d1 = NewsD1(facts())
        original = d1.execute

        def execute(script):
            if "impact_watch_items" in script:
                raise RuntimeError("D1 壞了")
            original(script)
        d1.execute = execute
        ask = with_track(lambda kind, entry: [{"id": "below_ma20", "why": "月線"}] if kind == "summary" else [])
        report, _, _ = tmn.run(d1, ask=ask)
        self.assertEqual(report["diagnosis"]["watch"]["error"], "write: RuntimeError")
        self.assertEqual(len(d1.query("SELECT 1 FROM market_news_items")), 4, "消息照樣寫進去")
        self.assertEqual(d1.watch_items(), [])

    def test_broken_stored_numbers_offer_nothing(self):
        d1 = NewsD1(None)
        d1.db.execute("INSERT INTO impact_watch_facts VALUES ('market', 'x', ?, ?)", (json.dumps({"date": "not-a-date", "close": 1}), NOW))
        report, _, _ = tmn.run(d1, ask=with_track(lambda kind, entry: [{"id": "below_ma20", "why": "月線"}]))
        self.assertEqual((report["run_status"], report["diagnosis"]["watch"]["offered"]), ("ok", 0))

    def test_a_dry_run_reports_what_it_would_build_without_writing(self):
        d1 = NewsD1(facts())
        get, _ = tmn.web()
        ask = with_track(lambda kind, entry: [{"id": "below_ma20", "why": "月線"}] if kind == "summary" else [])
        report = mn.run("close", trade_date="2026-11-19", now=NOW, query=d1.query, execute=d1.execute, get=get, sleep=lambda s: None,
                        ask_owner=ask, owner_email=tmn.OWNER, friend_keys=[], ask_friend=None,
                        taiex=lambda: None, dry_run=True)
        self.assertEqual(report["diagnosis"]["watch"]["created"], ["market:below_ma20:2026-11-19"])
        self.assertEqual(d1.watch_items(), [])


if __name__ == "__main__":
    unittest.main()
