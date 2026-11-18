"""關注股每日排程的執行程式（G 票第 14–16 片；規格〈Testing Decisions〉接縫 1：關注帳本的公開介面）。

給一份「每日更新已發布」的大盤資料檔、一份關注清單、一個假的資料來源（個股日 K 與各層輸入），加上假的模型
命令列程式（經真的子程序邊界，和預測帳本、關注帳本的測試同一支），檢查帳本、寫給資料庫的指令檔與執行紀錄。
"""
import json
import tempfile
import unittest
from pathlib import Path

import forecast_ledger as fl
import watch_ledger as wl
import watch_run as wr
from test_forecast_ledger import EVENING_OK as FORECAST_OK
from test_forecast_ledger import answer_a, at, bar, calls, clock, day_input, envelope, fake_model, weekdays
from test_watch_ledger import BASE, METHOD_TEXT, answer, index_bars, recording_model, stock_bars

# 關注帳本測試的固定日 K（22 根，到 09/30）再往後接 5 個交易日，好讓轉變日有機會到期：
#   個股收盤 10/01–10/07：111、112、113、114、115（開 = 收 − 0.5、高 = 收 + 1、低 = 收 − 1）
#   加權指數收盤 10/01–10/07：1160、1165、1170、1175、1180
LONG = weekdays("2026-09-01", 27)
EXTRA_STOCK = [111, 112, 113, 114, 115]
EXTRA_INDEX = [1160, 1165, 1170, 1175, 1180]


def long_stock_bars():
    return stock_bars() + [bar(day, c - 0.5, c + 1, c - 1, c) for day, c in zip(LONG[22:], EXTRA_STOCK)]


def long_index_bars():
    return index_bars() + [{"date": day, "close": c} for day, c in zip(LONG[22:], EXTRA_INDEX)]

EVENING_OK = at(BASE, 17, 30)
WATCHLIST = [{"code": "2330", "name": "台積電", "watch_since": "2026-09-01T09:00:00+08:00"},
             {"code": "2317", "name": "鴻海", "watch_since": "2026-09-02T09:00:00+08:00"}]


class FakeSource:
    """假的資料來源：每一檔都用同一份固定日 K（基準日之後的根數切掉），公告依代號給。"""

    def __init__(self, announcements=None, missing=(), broken=()):
        self.announcement_map = announcements or {}
        self.missing = set(missing)
        self.broken = set(broken)
        self.asked = []

    def stock(self, code, base_date, edition):
        self.asked.append((code, base_date, edition))
        if code in self.broken:
            raise OSError("讀不到行情庫")
        if code in self.missing:
            return {"stock_daily": [row for row in long_stock_bars() if row["date"] < base_date]}
        return {
            "stock_daily": [row for row in long_stock_bars() if row["date"] <= base_date],
            "capital_events": [],
            "technical": {"indicators": {"rsi14": 55}},
            "sector": {"data_date": base_date, "average_day_pct": 0.5},
            "margin": {"data_date": base_date, "margin_lots": 1000},
            "environment": {"usdtwd": {"date": base_date, "close": 32.1}},
            "wyckoff_lpsy": {"reading": {"status": "no", "date": base_date}},
            "valuation": {"data_date": base_date, "pe": {"value": 20.0}},
            "lending_eps": {"lending": {"data_date": base_date, "balance_lots": 10}, "eps": None},
            "signals": {"data_date": base_date, "blocks": ["ma_alignment"], "ma_alignment": {"summary": "多頭排列"}},
            "intraday": {"status": "ok", "data_date": base_date, "summary": {"close": 100}},
            "news_headlines": [{"id": "h_1", "source": "中央社", "title": "同業擴產", "summary": "",
                                "published_at": f"{base_date}T15:00:00+08:00"}],
            "peers": {"peers": [{"symbol": "NVDA", "close": 120.0}], "no_data": []},
            "data_cutoffs": {"stock": base_date},
        }

    def announcements(self, codes, base_date):
        return {code: self.announcement_map.get(code, []) for code in codes}


def analysis_file(folder: Path, until=BASE) -> Path:
    rows = [{**row, "open": row["close"], "high": row["close"], "low": row["close"]} for row in long_index_bars()
            if row["date"] <= until]
    path = folder / "analysis-data.js"
    path.write_text("window.MARKET_ANALYSIS = " + json.dumps({"daily": rows}) + ";\n", encoding="utf-8")
    return path


class WatchRunTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.ledger = self.tmp / "watch-ledger"
        self.forecasts = self.tmp / "forecast-ledger"
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.method = self.tmp / "method.md"
        self.method.write_text(METHOD_TEXT, encoding="utf-8")
        self.source = FakeSource()

    def tearDown(self):
        self._tmp.cleanup()

    def write_forecast(self, edition="evening", session_date=None, moment=EVENING_OK):
        folder = self.tmp / f"forecast-bin-{edition}"
        folder.mkdir(exist_ok=True)
        return fl.write_forecast(self.forecasts, day_input(), edition=edition, session_date=session_date,
                                 model_bin=fake_model(folder, [envelope(answer_a())]), model="test-model",
                                 now=clock(moment))

    def model(self, *replies):
        return fake_model(self.bin, [envelope(reply) for reply in replies] or [envelope(answer())])

    def earlier_judgment(self, day, status="可以留意", code="2330", edition="evening", session_date=None):
        """在某個較早的基準日替一檔寫一份合格的判斷（兩條線從那天的候選關卡挑：觸發線照狀態挑對的一側最近的一條、
        看錯線挑觸發線下方最近的一條；觸發線不挑最低的那一條，好讓下方還有看錯線可挑）。"""
        data = {"code": code, "name": "台積電", "base_date": day,
                "stock_daily": [row for row in long_stock_bars() if row["date"] <= day],
                "index_daily": [row for row in long_index_bars() if row["date"] <= day],
                "technical": {"indicators": {}}, "method_text": METHOD_TEXT}
        folder = self.tmp / f"earlier-{day}-{code}-{edition}"
        folder.mkdir()
        moment = at(day, 17, 30) if edition == "evening" else at(session_date, 7, 30)
        probe = wl.write_judgment(folder / "probe", data, edition=edition, session_date=session_date,
                                  model_bin=fake_model(folder, [envelope({})]), model="m", now=clock(moment))
        candidates = probe["record"]["input"]["candidates"]
        side = "at_or_below" if status == "可以留意" else "above"
        lowest = min(c["value"] for c in candidates)
        trigger = min((c for c in candidates if c["side"] == side and c["value"] > lowest),
                      key=lambda c: abs(c["value"] - probe["record"]["base_close"]))
        wrong = max((c for c in candidates if c["value"] < trigger["value"]), key=lambda c: c["value"])
        reply = answer(status=status, trigger=trigger["id"], wrong=wrong["id"], base_date=day, code=code)
        (folder / "calls.txt").unlink(missing_ok=True)
        return wl.write_judgment(self.ledger, data, edition=edition, session_date=session_date,
                                 model_bin=fake_model(folder, [envelope(reply)]), model="m", now=clock(moment))

    def evening(self, *, watchlist=WATCHLIST, model=None, day=BASE, now=None, **kwargs):
        return wr.run_evening(analysis_file(self.tmp, day), watchlist=watchlist, ledger=self.ledger,
                              forecast_ledger=self.forecasts, method_path=self.method,
                              model_bin=model or self.model(), model="test-model", now=clock(now or at(day, 17, 30)),
                              source=self.source, **kwargs)


class EveningTests(WatchRunTestCase):
    def test_writes_one_locked_judgment_per_watched_stock_with_the_same_day_market_forecast(self):
        self.write_forecast()
        model = fake_model(self.bin, [envelope(answer(code="2330")), envelope(answer(code="2317"))])

        log = self.evening(model=model, sql_output=self.tmp / "watch.sql")

        self.assertEqual([(item["code"], item["outcome"], item["kind"]) for item in log["stocks"]],
                         [("2330", "written", "judgment"), ("2317", "written", "judgment")])
        judgments = wl.read_ledger(self.ledger)["judgments"]
        self.assertEqual(sorted(record["code"] for record in judgments), ["2317", "2330"])
        market = judgments[0]["input"]["market"]
        # 預測帳本同一基準日、同一版那一份：盤勢類型與三格的落點（answer_a：線上、區間內、線下）。
        self.assertEqual(market["regime"], "多頭中的回檔")
        self.assertEqual({key: cell["zone"] for key, cell in market["cells"].items()},
                         {"1": "above", "5": "between", "20": "below"})
        self.assertEqual(judgments[0]["missing_layers"], [])
        # I 票第二輪三層照原樣交給模型
        self.assertEqual(judgments[0]["input"]["valuation"]["pe"]["value"], 20.0)
        self.assertEqual(judgments[0]["input"]["lending_eps"]["lending"]["balance_lots"], 10)
        self.assertEqual(judgments[0]["input"]["signals"]["ma_alignment"]["summary"], "多頭排列")
        sql = (self.tmp / "watch.sql").read_text(encoding="utf-8")
        self.assertEqual(sql.count("INSERT INTO watch_judgments"), 2)

    def test_reconciles_due_transition_days_before_writing_and_publishes_the_result(self):
        # 09/30 變成「可以留意」；之後第 5 個交易日是 10/07＝今天的基準日，所以這一次執行要對帳。
        # 個股 09/30 收 110 → 10/07 收 115（+4.5%）；指數 09/30 1155 → 10/07 1180（+2.2%）→ 贏大盤。
        self.assertTrue(self.earlier_judgment(BASE)["record"]["transition"])

        log = self.evening(day=LONG[26], watchlist=WATCHLIST[:1], sql_output=self.tmp / "watch.sql")

        self.assertEqual(log["reconciliation"]["appended"], 1)
        [row] = wl.read_ledger(self.ledger)["reconciliations"]
        self.assertEqual((row["base_date"], row["horizon"], row["beat_index"]), (BASE, 5, True))
        sql = (self.tmp / "watch.sql").read_text(encoding="utf-8")
        self.assertEqual(sql.count("INSERT INTO watch_reconciliations"), 1)

    def test_locks_a_miss_for_each_day_the_schedule_never_ran_since_the_watch_was_last_switched_on(self):
        # 09/30 有一份；10/01、10/02 整晚沒跑；10/05 這次執行。關注在 10/02 早上關掉又打開（watch_since），
        # 所以只有 10/02 補記漏記（10/01 那天沒在關注，不算漏記）；補記不呼叫模型（沒有任何一次嘗試）。
        self.earlier_judgment(BASE)
        watchlist = [{"code": "2330", "name": "台積電", "watch_since": "2026-10-02T08:00:00+08:00"}]

        log = self.evening(day=LONG[24], watchlist=watchlist)

        self.assertEqual(log["backfilled_misses"], [{"code": "2330", "base_date": LONG[23]}])
        by_day = {record["base_date"]: record for record in wl.read_ledger(self.ledger)["judgments"]}
        self.assertEqual(sorted(by_day), [BASE, LONG[23], LONG[24]])
        missed = by_day[LONG[23]]
        self.assertEqual((missed["kind"], missed["miss_reason"], missed["attempts"]), ("miss", "deadline_passed", []))

    def test_dry_run_replays_a_published_day_on_a_scratch_copy_and_reports_each_judgment(self):
        # 已發布的資料到 10/07，重播 09/30：資料截到 09/30、時鐘停在那天收盤後；真的帳本一個檔案都不多，不輸出指令檔。
        self.earlier_judgment(BASE, code="2317")
        before = sorted(path.name for path in self.ledger.rglob("*.json"))

        log = wr.run_evening(analysis_file(self.tmp, LONG[26]), watchlist=WATCHLIST[:1], ledger=self.ledger,
                             forecast_ledger=self.forecasts, method_path=self.method, model_bin=self.model(),
                             model="test-model", source=self.source, dry_run=True, as_of=BASE,
                             sql_output=self.tmp / "watch.sql")

        self.assertEqual(sorted(path.name for path in self.ledger.rglob("*.json")), before)
        self.assertFalse((self.tmp / "watch.sql").exists())
        [item] = log["stocks"]
        self.assertEqual((log["base_date"], log["dry_run"], item["outcome"], item["status"]), (BASE, True, "written", "接近"))
        self.assertEqual((item["trigger_line"]["id"], item["wrong_line"]["id"]),
                         ("swing_high_20260921", "swing_low_20260925"))
        self.assertEqual(item["reason"], "量縮整理等待表態")
        # J 票：重播舊日期沒有富果盤中、新聞與同類股當時的樣子，三層一律缺（不用今天的資料冒充）
        self.assertEqual(item["missing_layers"][:1], ["intraday"])
        self.assertTrue({"intraday", "headlines", "peers"} <= set(item["missing_layers"]))
        self.assertIn("重播", log["licensed_skipped"])

    def test_a_dry_run_can_rewrite_a_day_that_already_has_judgments_without_touching_the_ledger(self):
        # J 票（owner 11-19）：Actions 乾跑的那天已經有正式判斷，照帳本規矩 unchanged／conflict、不會呼叫模型；
        # rewrite_today 只在乾跑的暫存副本裡先拿掉那一天那一版的判斷，模型就會真的寫，真的帳本一個檔案都不動。
        self.earlier_judgment(BASE, code="2330")
        before = sorted(path.name for path in self.ledger.rglob("*.json"))
        model = self.model()
        plain = self.evening(watchlist=WATCHLIST[:1], dry_run=True, model=model)
        self.assertNotEqual(plain["stocks"][0]["outcome"], "written")
        fresh = self.evening(watchlist=WATCHLIST[:1], dry_run=True, rewrite_today=True)
        self.assertEqual(fresh["stocks"][0]["outcome"], "written")
        self.assertEqual(fresh["rewrite_today"], 1)
        self.assertEqual(sorted(path.name for path in self.ledger.rglob("*.json")), before)

    def test_rewriting_is_only_for_dry_runs(self):
        with self.assertRaises(ValueError):
            self.evening(rewrite_today=True)

    def test_a_stock_whose_data_cannot_be_read_waits_without_stopping_the_others(self):
        # 2330 的行情讀不到：期限前不寫（之後可以重跑），2317 照寫。
        self.source = FakeSource(broken={"2330"})
        model = fake_model(self.bin, [envelope(answer(code="2317"))])

        log = self.evening(model=model)

        self.assertEqual([(item["code"], item["outcome"]) for item in log["stocks"]],
                         [("2330", "not_ready"), ("2317", "written")])
        self.assertIn("OSError", log["stocks"][0]["detail"])

    def test_after_the_deadline_a_stock_still_not_ready_is_locked_as_a_miss_with_the_reason(self):
        # 2317 的日 K 到期限（基準日結束）都沒有基準日那一根：記漏記，原因寫明是資料沒到齊。
        self.source = FakeSource(missing={"2317"})
        log = self.evening(watchlist=WATCHLIST[1:], now=at(LONG[22], 0, 5), day=BASE)

        [item] = log["stocks"]
        self.assertEqual((item["kind"], item["miss_reason"]), ("miss", "input_not_ready"))


class MarketLayerModelTests(WatchRunTestCase):
    def test_the_market_layer_prefers_the_current_forecast_model_when_both_models_wrote_that_day(self):
        # 本人 2026-11-17 預測換 Argon：換模型那天同一基準日可能有 Flash 與 Argon 各一份，取現在這個模型的。
        # 舊模型挑一個輸入版本排在現在這個之前的名字，帳本讀出來時它在前面，才測得出「不是碰巧排第一」。
        older = next(name for name in (f"older-model-{n}" for n in range(100))
                     if fl.input_version(name) < fl.INPUT_VERSION)
        for name, regime in ((older, "盤整"), (fl.MODEL, "多頭中的回檔")):
            folder = self.tmp / f"forecast-bin-{name}"
            folder.mkdir()
            fl.write_forecast(self.forecasts, day_input(), edition="evening", model=name,
                              model_bin=fake_model(folder, [envelope(answer_a(regime=regime))]), now=clock(FORECAST_OK))
        layer = wr.market_layer(self.forecasts, day_input()["base_date"], "evening")
        self.assertEqual(layer["regime"], "多頭中的回檔")
        self.assertTrue(layer["forecast_id"].endswith(fl.INPUT_VERSION))

    def test_an_older_models_forecast_is_still_used_when_it_is_the_only_one(self):
        folder = self.tmp / "forecast-bin-flash"
        folder.mkdir()
        fl.write_forecast(self.forecasts, day_input(), edition="evening", model="gemini-3.8-flash",
                          model_bin=fake_model(folder, [envelope(answer_a(regime="盤整"))]), now=clock(FORECAST_OK))
        self.assertEqual(wr.market_layer(self.forecasts, day_input()["base_date"], "evening")["regime"], "盤整")


class MorningTests(WatchRunTestCase):
    def morning(self, session_date, *, published_until=BASE, holidays=frozenset(), now=None, **kwargs):
        return wr.run_morning(analysis_file(self.tmp, published_until), watchlist=WATCHLIST[:1], ledger=self.ledger,
                              forecast_ledger=self.forecasts, method_path=self.method, model_bin=self.model(),
                              model="test-model", source=self.source, session_date=session_date,
                              holidays=set(holidays), now=clock(now or at(session_date, 7, 30)), **kwargs)

    def test_writes_a_morning_judgment_on_the_last_trading_day_with_the_morning_market_forecast(self):
        # 開盤日 10/01 → 基準日 09/30。晚間版與晨間版的大盤預測都在，晨間的判斷用晨間那一份；
        # 晨間的大盤預測換一個盤勢類型，才分得出用了哪一份。不對帳。
        self.write_forecast()
        folder = self.tmp / "forecast-bin-morning"
        folder.mkdir()
        fl.write_forecast(self.forecasts, day_input(), edition="morning", session_date=LONG[22],
                          model_bin=fake_model(folder, [envelope(answer_a(regime="盤整"))]), model="m",
                          now=clock(at(LONG[22], 7, 25)))

        log = self.morning(LONG[22], sql_output=self.tmp / "watch.sql")

        self.assertEqual((log["edition"], log["session_date"], log["base_date"]), ("morning", LONG[22], BASE))
        self.assertNotIn("reconciliation", log)
        [record] = wl.read_ledger(self.ledger)["judgments"]
        self.assertEqual((record["edition"], record["base_date"], record["kind"]), ("morning", BASE, "judgment"))
        self.assertEqual(record["input"]["market"]["regime"], "盤整")
        self.assertIn("INSERT INTO watch_judgments", (self.tmp / "watch.sql").read_text(encoding="utf-8"))

    def test_writes_nothing_on_a_holiday(self):
        log = self.morning(LONG[22], holidays={LONG[22]})

        self.assertEqual(log["outcome"], "holiday")
        self.assertEqual(wl.read_ledger(self.ledger)["judgments"], [])


class OfficialStartTests(WatchRunTestCase):
    """H 票：輸入到齊（排程打開的那一組層）、早晚兩版都跑過一天之後，晚間執行自動寫下正式起算日。"""

    def judge(self, edition, day, session_date=None, **options):
        folder = self.tmp / f"bin-{edition}-{day}"
        folder.mkdir()
        model = fake_model(folder, [envelope(answer(base_date=day))])
        common = dict(watchlist=WATCHLIST[:1], ledger=self.ledger, forecast_ledger=self.forecasts,
                      method_path=self.method, model_bin=model, model="test-model", source=self.source,
                      sql_output=self.tmp / f"{edition}-{day}.sql", **options)
        if edition == "evening":
            return wr.run_evening(analysis_file(self.tmp, day), now=clock(at(day, 17, 30)), **common)
        return wr.run_morning(analysis_file(self.tmp, day), session_date=session_date, holidays=set(),
                              now=clock(at(session_date, 7, 30)), **common)

    def records(self):
        return {(r["base_date"], r["edition"]): r for r in wl.read_ledger(self.ledger)["judgments"]}

    def test_the_evening_after_a_full_day_of_both_editions_becomes_the_official_start(self):
        first = self.judge("evening", BASE)
        self.judge("morning", BASE, session_date=LONG[22])
        self.assertIsNone(first["official_since"])

        started = self.judge("evening", LONG[22])
        self.judge("morning", LONG[22], session_date=LONG[23])

        self.assertEqual(started["official_since"], LONG[22])
        records = self.records()
        self.assertEqual({key: r["run_mode"] for key, r in records.items()}, {
            (BASE, "evening"): "trial", (BASE, "morning"): "trial",
            (LONG[22], "evening"): "official", (LONG[22], "morning"): "official"})
        # 這一組輸入：官方資料算出的各層＋教訓層（H 票加最後供應點）；J 票（本人 2026-11-19 00:1x「富果、新聞來源、
        # 國外同類股都交給 Gemini 做詳細分析」）起富果、新聞標題、國外同類股也交，沒有被條款擋下的項目
        self.assertEqual(records[(LONG[22], "evening")]["layers"],
                         ["technical", "bollinger", "wyckoff_lpsy", "sector", "margin", "valuation", "lending_eps",
                          "signals", "intraday", "environment", "market", "news", "headlines", "peers", "lessons"])
        self.assertEqual(records[(LONG[22], "evening")]["withheld_inputs"], {})
        sql = (self.tmp / f"evening-{LONG[22]}.sql").read_text(encoding="utf-8")
        self.assertIn(f'"official_since":"{LONG[22]}"', sql)

    def test_a_dry_run_never_writes_the_official_start_into_the_real_ledger(self):
        self.judge("evening", BASE)
        self.judge("morning", BASE, session_date=LONG[22])
        log = self.judge("evening", LONG[22], dry_run=True)
        self.assertEqual(log["official_since"], LONG[22])         # 乾跑看得到「今天會起算」
        self.assertEqual(wl.official_starts(self.ledger), {})    # 真的帳本沒有寫


class AuthorizeTests(unittest.TestCase):
    """自動執行要看到大盤預測同一版真的跑完的紀錄（它的執行紀錄），才接著寫判斷。"""

    def evidence(self, **fields):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: [path.unlink() for path in folder.iterdir()] and folder.rmdir())
        path = folder / "run.json"
        path.write_text(json.dumps({"schema": "forecast-run/v1", "edition": "evening", "dry_run": False,
                                    "base_date": BASE, "outcome": "written", **fields}), encoding="utf-8")
        return path

    def test_allows_after_a_real_forecast_run_of_the_same_edition_whatever_its_outcome(self):
        for outcome in ("written", "unchanged", "not_ready"):
            self.assertEqual(wr.authorize(self.evidence(outcome=outcome), "evening"),
                             {"allowed": True, "forecast_base_date": BASE, "forecast_outcome": outcome})

    def test_refuses_without_a_record_after_a_dry_run_or_for_the_other_edition(self):
        self.assertFalse(wr.authorize(Path(tempfile.gettempdir()) / "no-such-run.json", "evening")["allowed"])
        self.assertFalse(wr.authorize(self.evidence(dry_run=True), "evening")["allowed"])
        self.assertFalse(wr.authorize(self.evidence(), "morning")["allowed"])
        self.assertFalse(wr.authorize(self.evidence(schema="something-else"), "evening")["allowed"])


def nomination(code="2330", peers=None):
    return {"code": code, "peers": peers if peers is not None else [
        {"symbol": "NVDA", "name": "NVIDIA", "reason": "先進製程的大客戶"},
        {"symbol": "8035.T", "name": "Tokyo Electron", "reason": "半導體設備供應商"},
        {"symbol": "INTC", "name": "Intel", "reason": "晶圓代工的競爭者"},
        {"symbol": "ZZZZ", "name": "Unknown", "reason": "名字對不上的代號"},
        {"symbol": "005930.KS", "name": "Samsung", "reason": "記憶體與代工的競爭者"},
    ]}


class NominationTests(unittest.TestCase):
    """第 16 片：站主打開關注之後的下一次排程，Gemini 替還沒有名單的關注股提名國外同類股，各附一句理由。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.ledger = self.tmp / "watch-ledger"
        self.checked = []

    def tearDown(self):
        self._tmp.cleanup()

    def check(self, symbol, market):
        # 假的「來源拿得到行情」：ZZZZ 查無代號，其他拿得到。
        self.checked.append(symbol)
        return "not_found" if symbol == "ZZZZ.US" else "ok"

    def nominate(self, model, stocks=None, **kwargs):
        return wr.nominate_peers(self.ledger, stocks or [{"code": "2330", "name": "台積電", "industry": "半導體業"}],
                                 owner_email="owner@example.com", model_bin=model, model="test-model",
                                 now=clock(at(BASE, 18, 0)), check=self.check, **kwargs)

    def test_nominees_go_straight_into_the_list_unless_korea_and_index_room_is_used_up(self):
        # 本人 11-18「國外同類股直接預設都幫我加入，不用我再確認」：提名直接成為名單（confirmed）。
        # 韓股與指數另有全部關注股合計 12 檔的上限（每日額度）：滿了那一檔才留在「提名・還沒確認」。
        import sqlite3
        result = self.nominate(fake_model(self.tmp, [envelope(nomination())]))
        db = sqlite3.connect(":memory:")
        db.executescript(
            "CREATE TABLE user_watchlist (email TEXT, code TEXT, watching INTEGER);"
            "CREATE TABLE watch_peers (email TEXT, code TEXT, symbol TEXT, name TEXT, market TEXT, status TEXT, reason TEXT,"
            " added_by TEXT, updated_at TEXT, PRIMARY KEY (email, code, symbol));"
            "CREATE TABLE watch_peer_events (email TEXT, code TEXT, symbol TEXT, name TEXT, action TEXT, at TEXT);"
            "INSERT INTO user_watchlist VALUES ('owner@example.com', '2330', 1), ('owner@example.com', '3661', 1);")
        db.executescript(result["sql"])
        rows = dict(db.execute("SELECT symbol, status FROM watch_peers WHERE code = '2330'").fetchall())
        self.assertEqual(rows, {"NVDA.US": "confirmed", "INTC.US": "confirmed", "005930.KO": "confirmed"})

        full = sqlite3.connect(":memory:")
        full.executescript(db.execute("SELECT group_concat(sql, ';') FROM sqlite_master WHERE type = 'table'").fetchone()[0] + ";")
        full.execute("INSERT INTO user_watchlist VALUES ('owner@example.com', '2330', 1), ('owner@example.com', '3661', 1)")
        full.executemany("INSERT INTO watch_peers VALUES ('owner@example.com', '3661', ?, 'x', 'KR', 'confirmed', NULL, 'owner', 'x')",
                         [(f"{n:06d}.KO",) for n in range(12)])
        full.executescript(result["sql"])
        rows = dict(full.execute("SELECT symbol, status FROM watch_peers WHERE code = '2330'").fetchall())
        self.assertEqual(rows, {"NVDA.US": "confirmed", "INTC.US": "confirmed", "005930.KO": "nominated"})

    def test_lists_only_nominees_whose_quotes_the_source_can_fetch_and_records_them_as_nominated(self):
        result = self.nominate(fake_model(self.tmp, [envelope(nomination())]))

        # 日股沒有來源、ZZZZ 查無代號：都不列。查過的只有美股與韓股。
        self.assertEqual(self.checked, ["NVDA.US", "INTC.US", "ZZZZ.US", "005930.KO"])
        [item] = result["stocks"]
        self.assertEqual([peer["symbol"] for peer in item["listed"]], ["NVDA.US", "INTC.US", "005930.KO"])
        self.assertEqual({peer["symbol"]: peer["excluded"] for peer in item["excluded"]},
                         {"8035.TSE": "no_source", "ZZZZ.US": "not_found"})
        sql = result["sql"]
        self.assertEqual(sql.count("INSERT INTO watch_peers"), 4)  # 韓股那一檔多一句「額度滿了才留提名」
        self.assertIn("'confirmed'", sql)
        self.assertIn("'gemini'", sql)
        self.assertIn("先進製程的大客戶", sql)
        self.assertEqual(sql.count("INSERT INTO watch_peer_events"), 3)
        self.assertTrue((self.ledger / "nominations" / "2330.json").exists())

    def test_the_prompt_only_carries_public_company_facts(self):
        model = recording_model(self.tmp, nomination())
        self.nominate(model)

        prompt = (self.tmp / "prompt.txt").read_text(encoding="utf-8")
        given = json.loads(prompt.split("INPUT_JSON:", 1)[1])
        self.assertEqual(given, {"code": "2330", "name": "台積電", "industry": "半導體業"})

    def test_a_stock_already_nominated_is_not_asked_again_and_publishing_again_adds_nothing_new(self):
        self.nominate(fake_model(self.tmp, [envelope(nomination())]))
        (self.tmp / "calls.txt").unlink()

        result = self.nominate(fake_model(self.tmp, [envelope(nomination())]))

        self.assertEqual(calls(self.tmp), 0)
        self.assertEqual(result["stocks"][0]["outcome"], "unchanged")
        # 同一份提名再發布一次：名單與改動紀錄都靠資料庫的條件擋下重複，不多一筆。
        self.assertIn("ON CONFLICT (email, code, symbol) DO NOTHING", result["sql"])
        self.assertIn("WHERE NOT EXISTS", result["sql"])

    def test_a_failed_or_invalid_answer_locks_nothing_so_the_next_schedule_asks_again(self):
        result = self.nominate(fake_model(self.tmp, [envelope({"code": "2330", "peers": []})]))

        self.assertEqual(result["stocks"][0]["outcome"], "not_nominated")
        self.assertEqual(calls(self.tmp), 2)  # 不合格重試一次
        self.assertFalse((self.ledger / "nominations").exists())
        self.assertNotIn("INSERT INTO watch_peers", result["sql"])

    def test_a_nominee_the_source_could_not_check_this_time_is_checked_again_without_asking_the_model_again(self):
        # 第一次：韓股那一檔因為 EODHD 這一次的檢查額度用完沒查成——不鎖名單、不發布，模型的回答先鎖下來。
        verdicts = {"005930.KO": "eodhd_limit"}
        self.check = lambda symbol, market: verdicts.get(symbol, "not_found" if symbol == "ZZZZ.US" else "ok")
        first = self.nominate(fake_model(self.tmp, [envelope(nomination())]))
        self.assertEqual(first["stocks"][0]["outcome"], "check_pending")
        self.assertNotIn("INSERT INTO watch_peers", first["sql"])
        (self.tmp / "calls.txt").unlink()

        # 下一次排程：額度回來了，再查一次就鎖名單，不再問模型。
        verdicts.clear()
        second = self.nominate(fake_model(self.tmp, [envelope(nomination())]))

        self.assertEqual(calls(self.tmp), 0)
        self.assertEqual([peer["symbol"] for peer in second["stocks"][0]["listed"]], ["NVDA.US", "INTC.US", "005930.KO"])
        self.assertEqual(second["sql"].count("INSERT INTO watch_peers"), 4)  # 三檔；韓股那一檔另有額度滿時的提名句

    def test_after_three_days_of_failed_checks_the_list_is_locked_with_what_could_be_checked(self):
        self.check = lambda symbol, market: "network" if market == "KR" else ("not_found" if symbol == "ZZZZ.US" else "ok")
        self.nominate(fake_model(self.tmp, [envelope(nomination())]))
        result = wr.nominate_peers(self.ledger, [{"code": "2330", "name": "台積電", "industry": "半導體業"}],
                                   owner_email="owner@example.com", model_bin=fake_model(self.tmp, [envelope({})]),
                                   model="test-model", now=clock(at(LONG[25], 18, 0)), check=self.check)

        [item] = result["stocks"]
        self.assertEqual(item["outcome"], "written")
        self.assertEqual([peer["symbol"] for peer in item["listed"]], ["NVDA.US", "INTC.US"])
        self.assertEqual({peer["symbol"]: peer["excluded"] for peer in item["excluded"]}["005930.KO"], "network")

    def test_stops_asking_once_the_call_budget_is_spent(self):
        stocks = [{"code": code, "name": name, "industry": "半導體業"} for code, name in (("2330", "台積電"), ("2303", "聯電"))]
        result = self.nominate(fake_model(self.tmp, [envelope(nomination())]), stocks=stocks, call_budget=1)

        self.assertEqual([item["outcome"] for item in result["stocks"]], ["written", "budget_spent"])
        self.assertEqual(calls(self.tmp), 1)


class PeerSymbolTests(unittest.TestCase):
    def test_reads_symbols_the_same_way_as_the_site(self):
        # 對照表和網站的測試（cloud/test/watch-peers-symbols.test.js）共用，兩邊改一邊另一邊會紅。
        table = json.loads((Path(__file__).parent / "cloud/test/fixtures/peer-symbols.json").read_text(encoding="utf-8"))
        for text, expected in table["cases"]:
            self.assertEqual(wr.peer_symbol(text), expected, text)


class SourceCheckTests(unittest.TestCase):
    """「來源拿得到行情」：美股問 Massive、韓股與指數問 EODHD（每天 20 次和其他排程共用，每次排程最多查 2 檔）。"""

    def test_maps_source_answers_and_caps_the_shared_eodhd_quota(self):
        import overseas_peers as op

        class Fake:
            def __init__(self, name, answers):
                self.name, self.pause, self.answers, self.asked = name, 0.0, answers, []

            def daily(self, symbol, start, end):
                self.asked.append(symbol)
                outcome = self.answers[symbol]
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome

        massive = Fake("massive", {"NVDA.US": [{"date": "2026-09-29", "close": 1.0}], "ZZZZ.US": op.NotFound(),
                                   "AMD.US": op.QuotaExceeded()})
        eodhd = Fake("eodhd", {"005930.KO": [{"date": "2026-09-29", "close": 1.0}], "000660.KO": [], "035420.KO": []})
        check = wr.source_check({"massive": massive, "eodhd": eodhd}, today=BASE, sleep=lambda _: None)

        self.assertEqual([check(symbol, market) for symbol, market in (
            ("NVDA.US", "US"), ("ZZZZ.US", "US"), ("AMD.US", "US"),
            ("005930.KO", "KR"), ("000660.KO", "KR"), ("035420.KO", "KR"))],
            ["ok", "not_found", "quota", "ok", "not_found", "eodhd_limit"])
        self.assertEqual(eodhd.asked, ["005930.KO", "000660.KO"])
        self.assertEqual(wr.source_check({"massive": None, "eodhd": None}, today=BASE)("NVDA.US", "US"), "no_key")


class LicensedSourceTests(unittest.TestCase):
    """J 票：新聞標題與國外同類股從網站資料庫唯讀查；只用寫的那一刻以前已經抓到的。"""

    def query(self, sql):
        self.asked.append(sql)
        if "FROM watch_news" in sql:
            return [{"code": "2330", "link": "https://www.cna.com.tw/a", "source": "cna-finance", "title": "同業擴產",
                     "summary": "摘要", "published_at": "2026-11-06T06:00:00Z", "fetched_at": "2026-11-06T09:00:00Z"},
                    {"code": "2330", "link": "https://www.cna.com.tw/b", "source": "cna-finance", "title": "之後才抓到",
                     "summary": "", "published_at": "2026-11-06T10:00:00Z", "fetched_at": "2026-11-06T13:00:00Z"}]
        if "FROM watch_peers" in sql:
            return [{"code": "2330", "symbol": "NVDA", "name": "NVIDIA", "market": "US"}]
        if "FROM overseas_quotes" in sql:
            return [{"symbol": "NVDA", "date": "2026-11-04", "close": 100.0, "fetched_at": "2026-11-05T00:00:00Z"},
                    {"symbol": "NVDA", "date": "2026-11-05", "close": 110.0, "fetched_at": "2026-11-06T00:00:00Z"},
                    {"symbol": "NVDA", "date": "2026-11-06", "close": 999.0, "fetched_at": "2026-11-06T22:00:00Z"}]
        return []

    def test_headlines_and_peers_up_to_the_moment_of_writing(self):
        self.asked = []
        source = wr.LocalSource(query=self.query, owner_email="owner@example.com")
        # 晚間版 2026-11-06 20:00（台北）＝ 12:00Z
        found = source.licensed(["2330"], base_date="2026-11-06", before="2026-11-06",
                                cutoff="2026-11-06T20:00:00+08:00")["2330"]
        self.assertEqual([item["title"] for item in found["news_headlines"]], ["同業擴產"])
        # 美國 11-05 收 110（11-06 那一筆在基準日當天、而且 22:00Z 才抓到，看不到）；1 日 110 ÷ 100 − 1 ＝ 10%
        self.assertEqual(found["peers"]["peers"][0]["close"], 110.0)
        self.assertEqual(found["peers"]["peers"][0]["change_1d_pct"], 10.0)
        self.assertTrue(all("DELETE" not in sql and "INSERT" not in sql for sql in self.asked))   # 只查不寫

    def test_without_a_connection_both_layers_are_missing(self):
        found = wr.LocalSource().licensed(["2330"], base_date="2026-11-06", before="2026-11-06",
                                          cutoff="2026-11-06T20:00:00+08:00")
        self.assertEqual(found, {"2330": {"intraday": None}})   # 沒有富果金鑰也沒有連線：三層都缺

    def test_without_a_fugle_key_the_intraday_layer_is_missing(self):
        source = wr.LocalSource()
        self.assertIsNone(source._intraday("2330", "2026-11-06"))
        self.assertEqual(source.fugle_errors["2330"], {"fugle": "no_api_key"})
        self.assertEqual(source.fugle_status["2330"]["status"], "missing")

    def test_the_fugle_status_says_which_day_and_which_parts(self):
        import test_licensed_inputs as tl

        class Fake:
            def candles(self, code):
                return tl.candles(tl.ROWS)

            def quote(self, code):
                return tl.quote()

            def hourly(self, code, start, end):
                raise TimeoutError("timed out")

        source = wr.LocalSource(fugle=Fake())
        self.assertEqual(source._intraday("2330", tl.BASE)["data_date"], tl.BASE)
        self.assertEqual(source.fugle_status["2330"], {"status": "ok", "data_date": tl.BASE,
                                                       "missing": ["hourly_history"]})

    def test_the_schedule_opens_the_three_layers(self):
        for layer in ("intraday", "headlines", "peers"):
            self.assertIn(layer, wr.LAYERS)


class LocalSourceLpsyTests(unittest.TestCase):
    """H 票：關注股的最後供應點讀數從行情庫（實際成交價＋逐日漲跌）算，和大盤共用 wyckoff_lpsy。"""

    def setUp(self):
        import sqlite3
        import test_wyckoff_lpsy as lpsy
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        db = self.root / "data/stock-history/stock-history.sqlite"
        db.parent.mkdir(parents=True)
        # test_wyckoff_lpsy 的固定日線：第 80 天收 90 跌破 95，第 81 天收 92、量 70 → 是。
        self.series = lpsy.basic_break().add(close=92, high=94, low=91, amount=70)
        con = sqlite3.connect(db)
        try:
            con.execute("CREATE TABLE daily (date TEXT, code TEXT, name TEXT, kind TEXT, open REAL, high REAL, low REAL,"
                        " close REAL, change_sign TEXT, change REAL, volume_shares INTEGER, turnover INTEGER)")
            previous = None
            for row in self.series.index_rows():
                change = 0.0 if previous is None else row["close"] - previous
                con.execute("INSERT INTO daily VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                            (row["date"], "2330", "台積電", "common", row["open"], row["high"], row["low"], row["close"],
                             "-" if change < 0 else "+", change, int(row["amount"]), 0))
                previous = row["close"]
            con.commit()
        finally:
            con.close()

    def tearDown(self):
        self._tmp.cleanup()

    def test_the_reading_is_computed_from_the_stock_history_database(self):
        base = self.series.date(81)
        layer = wr.LocalSource(self.root)._lpsy("2330", base)
        self.assertEqual(layer["reading"], {"status": "yes", "date": base, "break_date": self.series.date(80),
                                            "range_low": 95.0, "volume_ratio": 0.7})
        # 前一天（跌破日）不是
        self.assertEqual(wr.LocalSource(self.root)._lpsy("2330", self.series.date(80))["reading"]["status"], "no")

    def test_the_schedule_opens_the_layer(self):
        self.assertIn("wyckoff_lpsy", wr.LAYERS)

    def test_the_schedule_opens_the_three_new_layers(self):
        for layer in ("valuation", "lending_eps", "signals"):
            self.assertIn(layer, wr.LAYERS)

    def test_valuation_is_read_from_the_working_set(self):
        from test_watch_inputs import valuation_db
        base = self.series.date(81)
        valuation_db(self.root, [(base, 15.0, 2.0, 3.5)])
        found = wr.LocalSource(self.root).stock("2330", base, "evening")
        self.assertEqual(found["valuation"]["pe"]["value"], 15.0)
        self.assertEqual(found["data_cutoffs"]["valuation"], base)


if __name__ == "__main__":
    unittest.main()
