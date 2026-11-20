"""站主自選股與庫存的盯項（本人 2026-11-20，.scratch/impact-watch 票 06）：只有站主看得到。

從清單、建立、每天判斷到移除後收掉；假的模型、本機 SQLite 套同一份變更檔，不呼叫任何模型、不連網路、不讀行情庫
（每一檔當天的數字用和個股分析同一個形狀的假資料）。"""
import json
import unittest

import impact_watch as iw
import market_news as mn
import test_impact_watch as tiw
import test_market_news as tmn
from test_impact_watch import HOLIDAYS, NOW, weekdays

OWNER = tmn.OWNER
HOLD = ("INSERT INTO user_holdings (email, code, name, market, shares, average_cost, in_universe, created_at, updated_at) "
        "VALUES (?, ?, ?, '上市', 1000, 200, 1, '2026-11-01T00:00:00Z', '2026-11-01T00:00:00Z')")
WATCH = ("INSERT INTO user_watchlist (email, code, name, market, in_universe, created_at, updated_at, watching) "
         "VALUES (?, ?, ?, '上市', 1, '2026-11-01T00:00:00Z', '2026-11-01T00:00:00Z', 0)")


def analysis(closes, *, foreign=(), trust=(), resistance=(), support=(), end="2026-11-18", name="台積電"):
    """和個股分析（analyze_v2）同一個形狀的假結果。foreign／trust：最近幾天的買賣超（張），最後一個是 end 那一天；
    None 代表那一天沒有資料。"""
    days = weekdays(end, len(closes))
    bars = [{"date": day, "open": close, "high": close + 5, "low": close - 5, "close": close, "volume": 1000} for day, close in zip(days, closes)]
    flows: dict[str, dict] = {}
    for key, nets in (("foreign", foreign), ("trust", trust)):
        for day, net in zip(days[len(days) - len(nets):], nets):
            if net is not None:
                flows.setdefault(day, {"date": day, "foreign": None, "trust": None, "dealer": 0, "total": 0})[key] = net * 1000
    mean = lambda count: sum(closes[-count:]) / count if len(closes) >= count else None  # noqa: E731
    return {"qa": {"blocking": False}, "name": name, "as_of": days[-1], "bars": bars[-120:],
            "indicators": {"ma20": mean(20), "ma60": mean(60)},
            "levels": {"resistance": [{"tick": tick} for tick in resistance], "support": [{"tick": tick} for tick in support]},
            "institutional": [flows[day] for day in sorted(flows)]}


RISING = [500 + 2 * index for index in range(80)]      # 一路漲：收 658，在月線、季線之上
FALLING = [900 - 2 * index for index in range(80)]     # 一路跌：收 742，在兩條線之下


def stock(closes=RISING, code="2330", name="台積電", **kwargs):
    return iw.stock_facts(code, name, analysis(closes, **kwargs))


def ids(rows):
    return [row["id"] for row in rows]


class StockListTests(unittest.TestCase):
    """個股的可盯條件清單：由程式依那一檔當天的資料產生。"""

    def test_the_days_numbers_come_from_the_same_analysis_as_the_stock_page(self):
        facts = stock(foreign=(300, -1200, -800.4), trust=(50, 60), resistance=(700, 680), support=(600, 640, 660))
        self.assertEqual((facts["code"], facts["name"], facts["date"], facts["close"]), ("2330", "台積電", "2026-11-18", 658))
        self.assertEqual(facts["ma"], {"20": 639.0, "60": 599.0})
        self.assertEqual((facts["high"], facts["low"]), (663, 615), "近 20 日的盤中高低")
        self.assertEqual((facts["resistance"], facts["support"]), (680, 640), "最近的壓力在收盤之上、最近的支撐在收盤之下")
        self.assertEqual(facts["flows"]["foreign"], {"date": "2026-11-18", "net": -800.4, "side": "sell", "days": 2}, "單位是張")
        self.assertEqual(facts["flows"]["trust"], {"date": "2026-11-18", "net": 60, "side": "buy", "days": 2})

    def test_a_missing_day_breaks_a_streak_and_no_flow_rows_means_no_flow_conditions(self):
        self.assertEqual(stock(foreign=(-5, None, -7, -9))["flows"]["foreign"]["days"], 2)
        self.assertEqual(stock(foreign=(-5, 0, -9))["flows"]["foreign"]["days"], 1)
        plain = stock()
        self.assertNotIn("flows", plain)
        self.assertEqual(ids(iw.stock_candidates(plain)), ["below_ma20", "below_ma60", "above_high", "below_low"])

    def test_a_stock_that_cannot_be_analysed_has_no_numbers(self):
        self.assertIsNone(iw.stock_facts("0000", "查無", {"qa": {"blocking": True, "reason": "查不到這個代號的有效日 K"}}))
        self.assertIsNone(stock(RISING[:10]))
        self.assertEqual(iw.stock_candidates(None), [])

    def test_the_list_is_small_and_only_offers_what_has_not_happened_yet(self):
        rows = iw.stock_candidates(stock(foreign=(300, -1200, -800), trust=(50, 60), resistance=(680,), support=(640,)))
        self.assertEqual(ids(rows), ["below_ma20", "below_ma60", "above_high", "below_low", "above_resist", "below_support",
                                     "foreign_sell_streak", "foreign_turn_buy", "trust_buy_streak", "trust_turn_sell"])
        names = {row["id"]: row["name"] for row in rows}
        self.assertEqual(names["below_ma20"], "收盤跌破月線")
        self.assertEqual(names["above_high"], "收盤突破近 20 日高點（663）")
        self.assertEqual(names["above_resist"], "收盤站上壓力（680）")
        self.assertEqual(names["below_support"], "收盤跌破支撐（640）")
        self.assertEqual(names["foreign_sell_streak"], "外資連續賣超達 4 天")
        self.assertEqual(names["trust_turn_sell"], "投信由買超轉為賣超")
        self.assertTrue(all("台積電" not in row["name"] for row in rows), "條件名稱不含股票名稱：畫面與推播另外帶對象")
        self.assertTrue(all(row["params"]["name"] == "台積電" for row in rows), "名稱記在參數裡，網站顯示用")
        self.assertEqual(ids(iw.stock_candidates(stock(FALLING)))[:2], ["above_ma20", "above_ma60"])

    def test_a_level_that_is_the_recent_high_or_low_is_not_listed_twice(self):
        rows = iw.stock_candidates(stock(resistance=(663,), support=(615,)))
        self.assertEqual(ids(rows), ["below_ma20", "below_ma60", "above_high", "below_low"])

    def test_prices_keep_their_decimals(self):
        rows = iw.stock_candidates(stock([20 + 0.05 * index for index in range(80)], resistance=(24.35,)))
        self.assertIn("收盤站上壓力（24.35）", [row["name"] for row in rows])
        self.assertEqual(iw._price(1050), "1,050")
        self.assertEqual(iw._price(23.5), "23.5")


class StockEvaluateTests(unittest.TestCase):
    def item(self, key, before, **kwargs):
        row = next(row for row in iw.stock_candidates(before) if row["id"] == key)
        return {"condition_key": key, "params": row["params"], **kwargs}

    def test_price_conditions_are_met_with_the_actual_numbers(self):
        before = stock(resistance=(680,), support=(640,))
        drop = stock([*RISING[1:], 600], end="2026-11-19", resistance=(680,), support=(560,))
        self.assertEqual(iw.evaluate_stock(self.item("below_ma20", before), drop), ("met", "成立：11/19 收 600，跌破月線（638）。"))
        self.assertEqual(iw.evaluate_stock(self.item("below_support", before), drop), ("met", "成立：11/19 收 600，跌破支撐 640。"))
        self.assertEqual(iw.evaluate_stock(self.item("below_low", before), drop), ("met", "成立：11/19 收 600，跌破近 20 日低點 615。"))
        self.assertEqual(iw.evaluate_stock(self.item("above_resist", before), drop), ("watching", None))
        jump = stock([*RISING[1:], 690], end="2026-11-19")
        self.assertEqual(iw.evaluate_stock(self.item("above_resist", before), jump), ("met", "成立：11/19 收 690，站上壓力 680。"))
        self.assertEqual(iw.evaluate_stock(self.item("above_high", before), jump), ("met", "成立：11/19 收 690，突破近 20 日高點 663。"))

    def test_nothing_is_judged_on_the_day_it_was_made(self):
        before = stock(FALLING)
        self.assertEqual(iw.evaluate_stock(self.item("above_ma20", before), {**before, "close": 9999}), ("watching", None))

    def test_a_streak_is_met_broken_or_still_running_in_lots(self):
        before = stock(foreign=(300, -1200, -800), trust=(50, 60))
        streak, turn = self.item("foreign_sell_streak", before), self.item("foreign_turn_buy", before)
        more = lambda nets: stock([*RISING[1:], 660], end="2026-11-19", foreign=nets)  # noqa: E731
        self.assertEqual(iw.evaluate_stock(streak, more((300, -1200, -800, -500))), ("watching", None), "才 3 天")
        day4 = stock([*RISING[2:], 660, 661], end="2026-11-20", foreign=(-1200, -800, -500, -3200))
        self.assertEqual(iw.evaluate_stock(streak, day4), ("met", "成立：外資連續賣超 4 天，11/20 賣超 3,200 張。"))
        broken = more((-1200, -800, 520))
        self.assertEqual(iw.evaluate_stock(streak, broken), ("failed", "否定：11/19 外資買超 520 張，連續賣超沒有延續到 4 天。"))
        self.assertEqual(iw.evaluate_stock(turn, broken), ("met", "成立：11/19 外資轉為買超 520 張。"))
        self.assertEqual(iw.evaluate_stock(streak, before), ("watching", None), "法人資料還是建立時那一天：不判")
        trust = self.item("trust_turn_sell", before)
        self.assertEqual(iw.evaluate_stock(trust, stock([*RISING[1:], 660], end="2026-11-19", trust=(60, -15))),
                         ("met", "成立：11/19 投信轉為賣超 15 張。"))


def pick(code, key, facts, impact=2, **over):
    row = next(row for row in iw.stock_candidates(facts) if row["id"] == key)
    return {"subject": code, "condition_key": key, "label": row["name"], "params": row["params"], "note": "說明", "impact": impact,
            "prepare": ["如果跌破月線，就先確認停損的價位想好了沒"], "source_ref": "2026-11-19-close#n1", **over}


def market_pick(key, impact=2):
    row = next(row for row in iw.candidates(tiw.facts()) if row["id"] == key)
    return {"condition_key": key, "label": row["name"], "params": row["params"], "note": None, "impact": impact, "prepare": None,
            "source_ref": "2026-11-19-close#n2"}


class StockStoreTests(unittest.TestCase):
    def test_a_stock_pick_becomes_an_item_only_the_owner_can_see(self):
        db = tiw.Db()
        report = db.create([pick("2330", "below_ma20", stock()), market_pick("below_ma20")])
        self.assertEqual(sorted(report["created"]), ["2330:below_ma20:2026-11-19", "market:below_ma20:2026-11-19"], "同一個編號，對象不同各盯各的")
        row = db.items("subject = '2330'")[0]
        self.assertEqual((row["subject"], row["visibility"], row["kind"], row["label"], row["status"], row["days_left"]),
                         ("2330", "owner", "numeric", "收盤跌破月線", "watching", 20))
        self.assertEqual(json.loads(row["params"]), {"name": "台積電", "as_of": "2026-11-18", "ma": 20})
        self.assertEqual(db.items("subject = 'market'")[0]["visibility"], "all")

    def test_a_stock_item_is_owner_only_whatever_the_pick_says(self):
        db = tiw.Db()
        db.create([pick("2330", "below_ma20", stock(), visibility="all")])
        self.assertEqual([row["visibility"] for row in db.items()], ["owner"])

    def test_the_same_stock_condition_is_only_watched_once(self):
        db = tiw.Db()
        db.create([pick("2330", "below_ma20", stock())])
        report = db.create([pick("2330", "below_ma20", stock()), pick("2454", "below_ma20", stock(code="2454", name="聯發科"))], "2026-11-20")
        self.assertEqual((report["created"], report["already_watching"]), (["2454:below_ma20:2026-11-20"], 1))

    def test_stocks_and_the_market_share_the_limit_of_ten(self):
        db = tiw.Db()
        market = [market_pick(row["id"], impact=2) for row in iw.candidates(tiw.facts())]          # 7 件
        stocks = [pick("2330", key, stock(resistance=(680,), support=(640,)), impact=3)
                  for key in ("below_ma20", "below_ma60", "above_high", "below_low", "above_resist")]  # 5 件，影響力高
        report = db.create([*market, *stocks])
        self.assertEqual(len(db.items("status = 'watching'")), iw.MAX_WATCHING)
        self.assertEqual(report["over_limit"], 2)
        self.assertEqual(len(db.items("status = 'watching' AND subject = '2330'")), 5, "影響力高的個股盯項留著")


class StockD1(tiw.Db):
    def __init__(self, watch=(("2330", "台積電"),), hold=()):
        super().__init__(tmn.SCHEMA + tiw.SCHEMA_0034)
        self.db.execute("DELETE FROM user_watchlist")
        for code, name in watch:
            self.db.execute(WATCH, (OWNER, code, name))
        for code, name in hold:
            self.db.execute(HOLD, (OWNER, code, name))
        self.db.execute(WATCH, (tmn.FRIEND, "2454", "聯發科"))

    def stock_check(self, day_facts, day="2026-11-20", **kwargs):
        return iw.check_stocks(day=day, now=NOW, owner_email=OWNER, query=self.query, execute=self.execute,
                               load=lambda names, as_of: {code: day_facts[code] for code in names if code in day_facts},
                               holidays=HOLIDAYS, **kwargs)

    def facts(self):
        return {row["subject"]: row for row in self.query("SELECT * FROM impact_watch_facts")}


class StockDailyCheckTests(unittest.TestCase):
    def setUp(self):
        self.before = stock(foreign=(300, -1200, -800), resistance=(680,), support=(640,))
        self.db = StockD1()
        self.db.create([pick("2330", key, self.before) for key in ("below_ma20", "above_resist", "foreign_sell_streak")])

    def test_results_carry_the_numbers_and_stay_three_more_trading_days(self):
        day = stock([*RISING[2:], 650, 600], end="2026-11-20", foreign=(-800, -500, 520))
        report = self.db.stock_check({"2330": day})
        self.assertEqual((report["checked"], report["watching"], report["stocks"], report["removed"]), (3, 1, 1, []))
        rows = {row["condition_key"]: row for row in self.db.items()}
        self.assertEqual((rows["below_ma20"]["status"], rows["below_ma20"]["result_note"], rows["below_ma20"]["result_date"],
                          rows["below_ma20"]["show_until"]), ("met", "成立：11/20 收 600，跌破月線（639.4）。", "2026-11-20", "2026-11-25"))
        self.assertEqual((rows["foreign_sell_streak"]["status"], rows["foreign_sell_streak"]["result_note"]),
                         ("failed", "否定：11/20 外資買超 520 張，連續賣超沒有延續到 4 天。"))
        self.assertEqual((rows["above_resist"]["status"], rows["above_resist"]["days_left"]), ("watching", 19))
        self.assertTrue(all(row["visibility"] == "owner" for row in rows.values()))

    def test_the_days_numbers_are_stored_per_stock_for_the_news_job(self):
        day = stock([*RISING[1:], 660], end="2026-11-19")
        self.db.stock_check({"2330": day}, day="2026-11-19")
        self.assertEqual(json.loads(self.db.facts()["2330"]["payload"])["close"], 660)
        self.db.stock_check({"2330": self.before}, day="2026-11-18")     # 重跑舊的一天
        self.assertEqual(self.db.facts()["2330"]["data_date"], "2026-11-19", "不把新的數字蓋回去")
        offered = iw.stock_offered(self.db.query, "2026-11-20", {"2330": "台積電", "2454": "聯發科"}, HOLIDAYS)
        self.assertEqual(list(offered), ["2330"])
        self.assertEqual((offered["2330"]["name"], offered["2330"]["now"]), ("台積電", "收盤 660；月線 641；季線 601"))
        self.assertIn("below_ma20", ids(offered["2330"]["conditions"]))
        self.assertEqual(iw.stock_offered(self.db.query, "2026-11-30", {"2330": "台積電"}, HOLIDAYS), {}, "數字太舊不出清單")
        self.assertEqual(iw.stock_offered(tiw.Db(tmn.SCHEMA).query, "2026-11-20", {"2330": "台積電"}, HOLIDAYS), {}, "表還沒建")

    def test_a_stock_removed_from_both_lists_is_closed_quietly(self):
        self.db.db.execute("INSERT INTO impact_watch_facts VALUES ('2330', '2026-11-18', ?, ?)", (json.dumps(self.before), NOW))
        self.db.db.execute("INSERT INTO impact_watch_facts VALUES ('market', '2026-11-18', '{}', ?)", (NOW,))
        self.db.db.execute(WATCH, (OWNER, "2317", "鴻海"))     # 名單上還有別檔（整份空的會被當成站主設錯，不收）
        self.db.db.execute("DELETE FROM user_watchlist WHERE code = '2330'")
        report = self.db.stock_check({"2330": stock([*RISING[1:], 600], end="2026-11-19")}, day="2026-11-19")
        self.assertEqual(len(report["removed"]), 3)
        for row in self.db.items():
            self.assertEqual((row["status"], row["result_note"], row["show_until"], row["push_sent_at"], row["days_left"]),
                             ("expired", "已不在自選與庫存", None, None, None), "到期那一種收法：不推播、首頁不留")
        self.assertEqual(list(self.db.facts()), ["market"], "那一檔的數字也清掉，大盤的留著")

    def test_a_stock_still_held_is_not_closed_when_it_leaves_the_watchlist(self):
        self.db.db.execute("DELETE FROM user_watchlist WHERE code = '2330'")
        self.db.db.execute(HOLD, (OWNER, "2330", "台積電"))
        report = self.db.stock_check({})
        self.assertEqual((report["removed"], report["watching"]), ([], 3))

    def test_a_stock_without_numbers_today_still_counts_down_and_expires(self):
        self.assertEqual(self.db.stock_check({})["watching"], 3)
        self.assertEqual({row["days_left"] for row in self.db.items()}, {19})
        report = self.db.stock_check({}, day=self.db.items()[0]["expires_on"])
        self.assertEqual(len(report["expired"]), 3)
        self.assertEqual({(row["status"], row["result_note"]) for row in self.db.items()}, {("expired", None)})

    def test_when_the_owners_lists_cannot_be_read_nothing_is_closed(self):
        report = iw.check(tiw_analysis(), now=NOW, query=broken_watchlist(self.db.query), execute=self.db.execute, holidays=HOLIDAYS,
                          owner_email=OWNER, load_stocks=lambda names, as_of: {})
        self.assertEqual((report["status"], report["stocks"]), ("ok", {"error": "RuntimeError"}))
        self.assertEqual({row["status"] for row in self.db.items()}, {"watching"})
        self.assertEqual(self.db.facts()["market"]["data_date"], "2026-11-18", "大盤那一段照樣寫進去")
        bad = iw.check(tiw_analysis(), now=NOW, query=self.db.query, execute=self.db.execute, holidays=HOLIDAYS, owner_email="not-an-email")
        self.assertEqual(bad["stocks"], {"error": "ValueError"})

    def test_a_holdings_read_that_fails_does_not_close_the_stocks_that_are_only_held(self):
        self.db.db.execute("DELETE FROM user_watchlist WHERE code = '2330'")
        self.db.db.execute(HOLD, (OWNER, "2330", "台積電"))

        def query(sql):
            if "user_holdings" in sql:
                raise RuntimeError("wrangler 逾時")
            return self.db.query(sql)
        with self.assertRaises(RuntimeError):
            iw.check_stocks(day="2026-11-19", now=NOW, owner_email=OWNER, query=query, execute=self.db.execute, load=lambda n, d: {},
                            holidays=HOLIDAYS)
        self.assertEqual({row["status"] for row in self.db.items()}, {"watching"})

    def test_empty_lists_look_like_a_wrong_owner_and_close_nothing(self):
        self.db.db.execute("INSERT INTO impact_watch_facts VALUES ('2330', '2026-11-18', '{}', ?)", (NOW,))
        report = iw.check_stocks(day="2026-11-19", now=NOW, owner_email="someone-else@example.com", query=self.db.query,
                                 execute=self.db.execute, load=lambda n, d: {}, holidays=HOLIDAYS)
        self.assertEqual(report["stocks"], 0)
        self.assertEqual({row["status"] for row in self.db.items()}, {"watching"})
        self.assertIn("2330", self.db.facts())

    def test_the_daily_job_judges_stocks_after_the_market_and_skips_them_without_an_owner(self):
        self.db.create([market_pick("below_ma20")])
        seen = {}

        def load(names, as_of):
            seen.update(names=dict(names), as_of=as_of)
            return {"2330": stock([*RISING[1:], 600], end="2026-11-19")}
        without = iw.check(tiw_analysis(), now=NOW, query=self.db.query, execute=self.db.execute, holidays=HOLIDAYS, load_stocks=load)
        self.assertNotIn("stocks", without)
        self.assertEqual(seen, {}, "沒給站主就不讀行情庫")
        report = iw.check(tiw_analysis(), now=NOW, query=self.db.query, execute=self.db.execute, holidays=HOLIDAYS,
                          owner_email=OWNER, load_stocks=load)
        self.assertEqual(seen, {"names": {"2330": "台積電"}, "as_of": "2026-11-18"}, "只算站主自己的那幾檔，日期跟著大盤資料日")
        self.assertEqual([row["id"] for row in report["stocks"]["met"]], ["2330:below_ma20:2026-11-19"])
        self.assertEqual(report["checked"], 1, "大盤那一段只算大盤的")

    def test_a_dry_run_writes_nothing(self):
        report = self.db.stock_check({"2330": stock([*RISING[1:], 600], end="2026-11-19")}, day="2026-11-19", dry_run=True)
        self.assertEqual(len(report["met"]), 1)
        self.assertEqual({row["status"] for row in self.db.items()}, {"watching"})
        self.assertEqual(self.db.facts(), {})


def tiw_analysis():
    return {"daily": tiw.daily(tiw.RISING), "institutional": tiw.flows((120, -80, -60))}


def broken_watchlist(query):
    def run(sql):
        if "user_watchlist" in sql:
            raise RuntimeError("D1 壞了")
        return query(sql)
    return run


class StockLoadTests(unittest.TestCase):
    def test_one_stock_that_fails_only_loses_itself(self):
        import sys
        import types
        calls = []

        def analyze(code, as_of, capital_actions=None):
            calls.append((code, as_of))
            if code == "9999":
                raise RuntimeError("行情庫壞了")
            return {"qa": {"blocking": True}} if code == "FUND0001" else analysis(RISING)
        fake = types.SimpleNamespace(analyze_v2=types.SimpleNamespace(analyze_v2=analyze),
                                     price_adjustment=types.SimpleNamespace(default_capital_events=lambda: {}))
        saved = {name: sys.modules.get(name) for name in ("twon_analysis", "twon_analysis.analyze_v2", "twon_analysis.price_adjustment")}
        sys.modules.update({"twon_analysis": fake, "twon_analysis.analyze_v2": fake.analyze_v2,
                            "twon_analysis.price_adjustment": fake.price_adjustment})
        try:
            out = iw.load_stock_facts({"2330": "台積電", "9999": "壞掉", "FUND0001": "基金", "bad code": "x"}, "2026-11-18")
        finally:
            for name, module in saved.items():
                sys.modules.pop(name, None) if module is None else sys.modules.__setitem__(name, module)
        self.assertEqual(list(out), ["2330"])
        self.assertEqual(calls, [("2330", "2026-11-18"), ("9999", "2026-11-18"), ("FUND0001", "2026-11-18")], "代號格式不對的不查")


# ---- 消息排程：站主自己的呼叫裡替他的股票挑 ----

WHY = "法說後賣壓還沒消化完"


class StockNewsD1(tiw.NewsD1):
    def __init__(self, stocks=None, market=True):
        super().__init__(tiw.facts() if market else None)
        for code, day_facts in ({"2330": stock(foreign=(300, -1200, -800))} if stocks is None else stocks).items():
            self.db.execute("INSERT INTO impact_watch_facts VALUES (?, ?, ?, ?)", (code, day_facts["date"], json.dumps(day_facts, ensure_ascii=False), NOW))

    def everything_shared(self):
        """大家共用、朋友讀得到的三張表的全部內容。"""
        return json.dumps([self.query(f"SELECT * FROM {table}") for table in
                           ("market_news_runs", "market_news_items", "stock_news_marks", "market_news_user_items")], ensure_ascii=False)


def picking(*, watch=None, items=None, summary=None):
    """假的 Flash：照原本的回答，再替站主的股票挑。watch(entry)→track；items(entry)／summary(output)→my_track。"""
    def change(kind, output, _asked):
        if kind == "watch" and watch:
            for entry in output["watch"]:
                entry["track"] = watch(entry)
        elif kind == "items" and items:
            for entry in output["items"]:
                entry["my_track"] = items(entry)
        elif kind == "summary" and summary:
            output["my_track"] = summary(output)
    return tmn.Reply(change)


class StockNewsRunTests(unittest.TestCase):
    def test_the_owners_stock_news_can_pick_a_condition_for_that_stock(self):
        d1 = StockNewsD1()
        ask = picking(watch=lambda entry: [{"id": "below_ma20", "why": WHY}, {"id": "no_such", "why": "自己編的"}])
        report, _, _ = tmn.run(d1, ask=ask)
        watch = next(payload for kind, payload in tiw.payloads(ask) if kind == "watch")
        self.assertEqual([(row["code"], row["now"]) for row in watch["stock_watchable"]], [("2330", "收盤 658；月線 639；季線 599；外資連續賣超 2 天")])
        self.assertEqual(sorted(watch["stock_watchable"][0]["conditions"][0]), ["id", "name"], "參數不給模型")
        self.assertIn("stock_watchable", ask.prompts[ask.kinds.index("watch")].split("INPUT:")[0], "規則有講這個欄位")
        rows = d1.query("SELECT * FROM impact_watch_items WHERE subject <> 'market'")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row["id"], row["subject"], row["visibility"], row["label"], row["note"], row["impact"], row["source_block"],
                          row["source_ref"]),
                         ("2330:below_ma20:2026-11-19", "2330", "owner", "收盤跌破月線", WHY, 2, "market_news", "2026-11-19-close#stock:2330"))
        self.assertEqual(json.loads(row["prepare"]), ["如果費半續跌，就先想好能承受的跌幅"], "那一則的準備跟著存")
        self.assertEqual(json.loads(row["params"])["name"], "台積電")
        self.assertEqual((report["diagnosis"]["watch"]["stocks_offered"], report["diagnosis"]["watch"]["unknown_ids"]), (1, ["2330:no_such"]))
        self.assertIn("2330:below_ma20:2026-11-19", report["diagnosis"]["watch"]["created"])

    def test_what_the_owner_watches_never_reaches_the_shared_tables(self):
        d1 = StockNewsD1()
        ask = picking(watch=lambda entry: [{"id": "below_ma20", "why": WHY}],
                      items=lambda entry: [{"code": "2330", "id": "below_ma60", "why": WHY}],
                      summary=lambda output: [{"code": "2330", "id": "above_high", "why": WHY}])
        tmn.run(d1, ask=ask)
        self.assertEqual(len(d1.query("SELECT 1 FROM impact_watch_items WHERE subject = '2330' AND visibility = 'owner'")), 3)
        self.assertEqual(d1.query("SELECT 1 FROM impact_watch_items WHERE subject <> 'market' AND visibility <> 'owner'"), [])
        shared = d1.everything_shared()
        for secret in (WHY, "below_ma20", "below_ma60", "above_high", "my_track", "track"):
            self.assertNotIn(secret, shared, secret)
        self.assertEqual(d1.query("SELECT effect FROM stock_news_marks WHERE code = '2330'")[0]["effect"], "短線股價承壓", "共用的判讀照原樣")

    def test_market_news_picks_only_for_a_stock_its_owner_line_names(self):
        d1 = StockNewsD1({"2330": stock(), "2317": stock(code="2317", name="鴻海")})
        d1.db.execute(HOLD, (OWNER, "2317", "鴻海"))
        ask = picking(items=lambda entry: [{"code": "2330", "id": "below_ma20", "why": WHY}, {"code": "2317", "id": "below_ma20", "why": WHY}])
        report, _, _ = tmn.run(d1, ask=ask)
        items = next(payload for kind, payload in tiw.payloads(ask) if kind == "items")
        self.assertEqual([row["code"] for row in items["my_watchable"]], ["2317", "2330"], "自選與庫存都給")
        rows = d1.query("SELECT subject, impact, source_ref, visibility FROM impact_watch_items WHERE subject <> 'market'")
        self.assertEqual([(row["subject"], row["impact"], row["visibility"]) for row in rows], [("2330", 3, "owner")],
                         "只有點名台積電的那一則建得成；沒被點名的鴻海不建")
        self.assertRegex(rows[0]["source_ref"], r"^2026-11-19-close#n\d+$")

    def test_the_summary_can_pick_for_a_stock_it_names(self):
        d1 = StockNewsD1()
        ask = picking(summary=lambda output: [{"code": "2330", "id": "foreign_sell_streak", "why": "建議賣出"}])
        report, _, _ = tmn.run(d1, ask=ask)
        row = d1.query("SELECT * FROM impact_watch_items WHERE subject = '2330'")[0]
        self.assertEqual((row["label"], row["impact"], row["source_ref"], row["note"]),
                         ("外資連續賣超達 4 天", 3, "2026-11-19-close#summary", None), "那一句像買賣指令：只丟那一句，盯項照建")
        self.assertEqual(report["diagnosis"]["points"]["text_rejected"], {"track": 1})

    def test_one_shift_builds_market_stock_and_news_type_items_together(self):
        """票 05 併進來之後：同一班裡大盤的數字型、等消息的（kind='news'）、站主個股的三種一起建，各自的可見範圍不變。"""
        d1 = StockNewsD1()

        def change(kind, output, _asked):
            if kind == "summary":
                output.update(track=[{"id": "below_ma20", "why": "月線"}], my_track=[{"code": "2330", "id": "below_ma60", "why": WHY}],
                              wait={"what": "金管會公布正式名單", "keywords": ["金管會", "正式名單"]})
            elif kind == "watch":
                for entry in output["watch"]:
                    entry["track"] = [{"id": "below_ma20", "why": WHY}]
        report, _, _ = tmn.run(d1, ask=tmn.Reply(change))
        rows = {(row["subject"], row["kind"], row["condition_key"] if row["kind"] == "numeric" else "wait"): row["visibility"]
                for row in d1.watch_items()}
        self.assertEqual(rows, {("market", "numeric", "below_ma20"): "all", ("market", "news", "wait"): "all",
                                ("2330", "numeric", "below_ma60"): "owner", ("2330", "numeric", "below_ma20"): "owner"})
        self.assertEqual(report["diagnosis"]["watch"]["news"], {"written": 1, "same_event": 0})
        self.assertNotIn(WHY, d1.everything_shared())

    def test_minor_or_unrelated_stock_news_starts_nothing(self):
        d1 = StockNewsD1()

        def change(kind, output, _asked):
            for entry in output.get("watch", []):
                entry.update(impact="1", track=[{"id": "below_ma20", "why": WHY}])
        tmn.run(d1, ask=tmn.Reply(change))
        self.assertEqual(d1.query("SELECT 1 FROM impact_watch_items WHERE subject <> 'market'"), [])

    def test_friends_keys_are_never_given_the_list_and_cannot_pick(self):
        d1 = StockNewsD1({"2330": stock(), "2454": stock(code="2454", name="聯發科")})
        prompts = []

        def friend_ask(key_row, prompt, schema):
            prompts.append(prompt)
            payload, _ = json.JSONDecoder().raw_decode(prompt[prompt.index("INPUT:") + 6:])
            reply = tmn.model_reply([], payload["watch"])["watch"]
            for entry in reply:
                entry["track"] = [{"id": "below_ma20", "why": WHY}]
            return "ok", {"watch": reply}, ""
        tmn.run(d1, friend_keys=[{"email": tmn.FRIEND}], friend_ask=friend_ask)
        self.assertEqual(len(prompts), 1)
        self.assertNotIn("stock_watchable", prompts[0])
        self.assertNotIn("my_watchable", prompts[0])
        self.assertEqual(d1.query("SELECT 1 FROM impact_watch_items WHERE subject <> 'market'"), [], "朋友的自選不產生盯項")

    def test_without_stock_numbers_the_prompts_are_exactly_as_before(self):
        with_stocks, without = StockNewsD1({}), tiw.NewsD1(tiw.facts())
        _, _, ask_a = tmn.run(with_stocks)
        _, _, ask_b = tmn.run(without)
        self.assertEqual(ask_a.prompts, ask_b.prompts)
        self.assertNotIn("my_watchable", "".join(ask_a.prompts))
        report, _, _ = tmn.run(StockNewsD1({}, market=False))
        self.assertEqual(report["diagnosis"]["watch"], {"offered": 0, "unknown_ids": []})

    def test_stock_items_work_without_the_market_list(self):
        d1 = StockNewsD1(market=False)
        report, _, _ = tmn.run(d1, ask=picking(watch=lambda entry: [{"id": "below_ma20", "why": WHY}]))
        self.assertEqual(report["diagnosis"]["watch"]["created"], ["2330:below_ma20:2026-11-19"])

    def test_a_stock_no_longer_on_the_owners_lists_is_not_offered(self):
        d1 = StockNewsD1({"2330": stock(), "3008": stock(code="3008", name="大立光")})
        _, _, ask = tmn.run(d1)
        self.assertNotIn("3008", "".join(ask.prompts))

    def test_validation_keeps_picks_out_of_the_rows_it_returns(self):
        offers = {"2330": {"name": "台積電", "now": "", "conditions": iw.stock_candidates(stock())}}
        watch = [{"id": "w1", "code": "2330", "name": "台積電", "title": "台積電法說", "link": "https://x/1", "links": ["https://x/1", "https://x/2"]}]
        entry = {**tmn.model_reply([], watch)["watch"][0], "track": [{"id": "below_ma20", "why": WHY}, {"id": "below_ma20", "why": "重複"}]}
        picked: list = []
        rows = mn.validate_watch({"watch": [entry]}, watch, stock_offers=offers, picked=picked)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all("track" not in row and WHY not in json.dumps(row, ensure_ascii=False) for row in rows))
        self.assertEqual([(row["subject"], row["condition_key"], row["impact"]) for row in picked], [("2330", "below_ma20", 2)])
        self.assertTrue(all("track" not in sql and WHY not in sql for sql in mn.marks_sql(rows, judged_by="owner", now=NOW)))


if __name__ == "__main__":
    unittest.main()
