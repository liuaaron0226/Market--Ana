"""關注帳本的公開介面測試（`.scratch/watchlist-watch/spec.md`〈Testing Decisions〉接縫 1）。

假模型是暫存的命令列程式，經真的子程序邊界呼叫（和預測帳本同一支假程式）。
候選關卡、轉變日、贏大盤、「隨便挑」的預期值都是手算的固定數字，算式寫在各測試的註解裡。
"""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import watch_ledger as wl
from test_forecast_ledger import at, bar, calls, clock, envelope, fake_model, weekdays

METHOD_TEXT = "# 大盤分析方法\n\n版本：0.4，2026-11-15。\n\n方法內容。\n"

# 固定日 K：22 根（2026-09-01 起的平日），基準日是第 22 根 2026-09-30，收盤 110。
#   一般的一根：開 = 收 − 0.5、高 = 收 + 1、低 = 收 − 1，沒有成交量。
#   收盤：第 1–14 根 100、101、…、113；第 15 根 120；第 16–19 根 116、112、108、104；第 20–22 根 106、108、110
#   第 5 根（09/07）低點壓到 95；第 15 根（09/21）高 122、低 113.5、成交量 5000（唯一有量的一根）
DATES = weekdays("2026-09-01", 22)
BASE = DATES[-1]
CLOSES = [*range(100, 114), 120, 116, 112, 108, 104, 106, 108, 110]
# 加權指數收盤：第 1–5 根 1000、第 6–16 根 1050、第 17–22 根 1155
INDEX_CLOSES = [1000] * 5 + [1050] * 11 + [1155] * 6


def stock_bars():
    rows = []
    for position, (day, close) in enumerate(zip(DATES, CLOSES), start=1):
        row = bar(day, close - 0.5, close + 1, close - 1, close)
        if position == 5:
            row["low"] = 95
        if position == 15:
            row.update(high=122, low=113.5, volume=5000)
        rows.append(row)
    return rows


def index_bars():
    return [{"date": day, "close": close} for day, close in zip(DATES, INDEX_CLOSES)]


def stock_input(**overrides):
    data = {
        "code": "2330", "name": "台積電", "base_date": BASE,
        "stock_daily": stock_bars(), "index_daily": index_bars(),
        "technical": {"trend_labels": {"short": "偏多", "medium": "盤整"}, "chips": {"foreign_net_lots": 1200}},
        "market_forecast": {"regime": "盤整", "zones": {"5": "between"}},
        "announcements": [{"id": "a1", "source": "twse", "kind": "月營收", "published_at": "2026-09-29T17:30:00+08:00",
                           "title": "公告九月營收", "body": "營收內容。"}],
        "data_cutoffs": {"stock": BASE, "index": BASE},
        "method_text": METHOD_TEXT,
    }
    data.update(overrides)
    return data


def answer(status="接近", trigger="swing_high_20260921", wrong="swing_low_20260925", reason="量縮整理等待表態",
           reason_refs=(), news_refs=(), base_date=BASE, code="2330"):
    return {"base_date": base_date, "code": code, "status": status, "trigger_line": trigger, "wrong_line": wrong,
            "reason": reason, "reason_refs": list(reason_refs), "news_refs": list(news_refs)}


EVENING_OK = at(BASE, 17, 0)


def recording_model(folder: Path, reply):
    """把收到的提示存成 prompt.txt 的假模型。"""
    script = folder / "recording_model.py"
    payload = json.dumps({"event": "result", "result": envelope(reply)}, ensure_ascii=False)
    script.write_text(
        "import json,sys\nfrom pathlib import Path\n"
        "prompt=json.loads(sys.stdin.buffer.readline().decode('utf-8'))['message']['content']\n"
        f"Path({str(folder / 'prompt.txt')!r}).write_text(prompt, encoding='utf-8')\n"
        f"sys.stdout.buffer.write({payload.encode('utf-8')!r})\n", encoding="utf-8")
    return script


class WatchLedgerTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "ledger"
        self.bin = self.tmp / "bin"
        self.bin.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def reset_calls(self):
        """把假模型的呼叫次數歸零（換下一個情境前）。"""
        (self.bin / "calls.txt").unlink(missing_ok=True)

    def write(self, data=None, responses=None, *, edition="evening", now=None, model=None, root=None, **kwargs):
        model = model or fake_model(self.bin, responses or [envelope(answer())])
        return wl.write_judgment(root or self.root, data or stock_input(), edition=edition, model_bin=model,
                                 model="test-model", now=now or clock(EVENING_OK), **kwargs)


class CandidateLevelTests(WatchLedgerTestCase):
    def test_lists_the_named_levels_of_the_existing_support_resistance_calculation(self):
        record = self.write()["record"]
        # 沿用個股頁的支撐壓力計算，22 根只夠算出下面這些（六十日的項目要滿 60 根）：
        #   MA5  = (108+104+106+108+110)/5 = 107.2
        #   MA10 = (112+113+120+116+112+108+104+106+108+110)/10 = 110.9
        #   MA20 = 第 3–22 根 = (102+…+113 = 1290) + (120+116+112+108+104+106+108+110 = 884) = 2174/20 = 108.7
        #   前 20 日盤中高點（第 2–21 根）= 122；近 20 日收盤高點 = 120；近 20 日盤中低點（第 3–22 根）= 95
        #   前波高點：只有第 15 根（09/21）高 122 不低於前後各兩根；波段低點：第 5 根（09/07）95、第 19 根（09/25）103
        #   向下缺口：第 17 根（09/23）高 113 < 第 16 根低 115，之後最高只到 111，沒回補 → 上緣 115、下緣 113
        #     （第 18、19 根的缺口都被之後的高點補上，不列）
        #   大量成交日：只有第 15 根有量 → 收盤 120、高點 122、低點 113.5
        # 收盤 110：大於收盤的在上方（above），其餘在下方或等於收盤（at_or_below）。依數值由高到低、同值依編號。
        self.assertEqual([(c["id"], c["name"], c["value"], c["kind"], c["side"]) for c in record["input"]["candidates"]], [
            ("heavy_high_20260921", "大量成交日高點（09/21）", 122, "heavy_high", "above"),
            ("prior_high_20", "前 20 日盤中高點", 122, "prior_high_20", "above"),
            ("swing_high_20260921", "前波高點（09/21）", 122, "swing_high", "above"),
            ("heavy_volume_20260921", "大量成交日收盤（09/21）", 120, "heavy_volume", "above"),
            ("high_20", "近 20 日收盤高點", 120, "high_20", "above"),
            ("gap_down_upper_20260923", "未回補向下缺口上緣（09/23）", 115, "gap_down_upper", "above"),
            ("heavy_low_20260921", "大量成交日低點（09/21）", 113.5, "heavy_low", "above"),
            ("gap_down_lower_20260923", "未回補向下缺口下緣（09/23）", 113, "gap_down_lower", "above"),
            ("ma10", "MA10", 110.9, "ma10", "above"),
            ("ma20", "MA20", 108.7, "ma20", "at_or_below"),
            ("ma5", "MA5", 107.2, "ma5", "at_or_below"),
            ("swing_low_20260925", "波段低點（09/25）", 103, "swing_low", "at_or_below"),
            ("low_20", "近 20 日盤中低點", 95, "low_20", "at_or_below"),
            ("swing_low_20260907", "波段低點（09/07）", 95, "swing_low", "at_or_below"),
        ])


    def test_levels_are_computed_on_prices_restored_for_a_split(self):
        # 第 11 根（09/15）起一股拆四股：之前 10 根的實際成交價是四倍、成交量是四分之一。
        # 官方參考價 109 ÷ 拆股前收盤 436 = 0.25，還原後和沒拆股的日 K 完全一樣，候選關卡也要一樣。
        plain = self.write()["record"]["input"]["candidates"]
        raw = stock_bars()
        for row in raw[:10]:
            row.update({key: row[key] * 4 for key in ("open", "high", "low", "close")})
        split = {"code": "2330", "date": DATES[10], "type": "變更面額", "close_before": 436, "reference": 109}
        record = self.write(stock_input(stock_daily=raw, capital_events=[split]), root=self.tmp / "split")["record"]
        self.assertEqual(record["input"]["candidates"], plain)


    def test_a_capital_change_that_cannot_be_restored_is_named_in_the_input(self):
        # 官方參考價缺漏：不猜係數，那一段維持實際成交價，判斷照寫，但輸入裡要看得到
        event = {"code": "2330", "date": DATES[10], "type": "減資", "close_before": 109, "reference": None}
        record = self.write(stock_input(capital_events=[event]))["record"]
        self.assertEqual(record["kind"], "judgment")
        self.assertEqual(record["input"]["price_basis"]["unresolved_capital_events"],
                         [{"date": DATES[10], "type": "減資", "reason": "缺官方參考價或現金股利"}])
        self.assertEqual(self.write(root=self.tmp / "plain")["record"]["input"]["price_basis"]["unresolved_capital_events"], [])


class WriteJudgmentTests(WatchLedgerTestCase):
    def test_valid_answer_locks_a_judgment_with_two_named_lines(self):
        result = self.write(responses=[envelope(answer(
            "接近", "swing_high_20260921", "swing_low_20260925", reason="量縮整理等待表態",
            reason_refs=["swing_high_20260921", "ma20"], news_refs=["a1"]))])

        self.assertEqual(result["outcome"], "written")
        record = result["record"]
        self.assertEqual((record["kind"], record["status"]), ("judgment", "接近"))
        self.assertEqual(record["trigger_line"], {"id": "swing_high_20260921", "name": "前波高點（09/21）",
                                                  "value": 122, "kind": "swing_high"})
        self.assertEqual(record["wrong_line"], {"id": "swing_low_20260925", "name": "波段低點（09/25）",
                                                "value": 103, "kind": "swing_low"})
        # 理由：模型的文字不含數字，價位由程式依編號代入；引用的消息只留編號與標題
        self.assertEqual(record["reason"], "量縮整理等待表態【資料：前波高點（09/21） 122 元；MA20 108.7 元】")
        self.assertEqual(record["reason_news"], [{"id": "a1", "title": "公告九月營收", "source": "twse",
                                                  "published_at": "2026-09-29T17:30:00+08:00"}])
        self.assertEqual(record["base_close"], 110)

    def test_judgment_records_identity_versions_and_the_full_input_given_to_the_model(self):
        record = self.write()["record"]
        self.assertEqual((record["code"], record["name"], record["base_date"], record["edition"]),
                         ("2330", "台積電", BASE, "evening"))
        self.assertEqual(record["method_version"], "0.4")
        self.assertRegex(record["input_version"], r"^technical\+market\+news-v1-[0-9a-f]{8}$")
        self.assertEqual(record["judgment_id"], f"2330|{BASE}|evening|0.4|{record['input_version']}")
        self.assertEqual(record["model"], "test-model")
        self.assertEqual(record["locked_at"], "2026-09-30T17:00:00+08:00")
        self.assertEqual(record["run_mode"], "trial")
        self.assertEqual(record["data_cutoffs"], {"stock": BASE, "index": BASE})
        self.assertRegex(record["input_sha256"], r"^[0-9a-f]{64}$")
        given = record["input"]
        self.assertEqual(given["method_text"], METHOD_TEXT)
        self.assertEqual(given["technical"]["chips"], {"foreign_net_lots": 1200})
        self.assertEqual(given["market"], {"regime": "盤整", "zones": {"5": "between"}})
        self.assertEqual(given["news"]["announcements"][0]["title"], "公告九月營收")
        # 算候選關卡與「隨便挑」用的完整日 K：個股與指數的範圍、根數一起鎖定
        self.assertEqual({key: (value["from"], value["to"], value["bars"]) for key, value in given["history"].items()},
                         {"stock": ("2026-09-01", BASE, 22), "index": ("2026-09-01", BASE, 22)})

    def test_judgment_on_or_after_the_official_start_is_official(self):
        self.assertEqual(self.write(official_since=BASE)["record"]["run_mode"], "official")
        later = self.write(official_since="2026-10-01", root=self.tmp / "later")["record"]
        self.assertEqual(later["run_mode"], "trial")

    def test_the_model_is_given_the_input_and_the_rules_as_its_prompt(self):
        record = self.write(model=recording_model(self.bin, answer()))["record"]
        prompt = (self.bin / "prompt.txt").read_text(encoding="utf-8")
        rules, _, sent = prompt.partition("INPUT_JSON:\n")
        self.assertEqual(json.loads(sent), record["input"])
        for phrase in ("還沒到", "接近", "可以留意", "trigger_line", "wrong_line", "不是買賣建議",
                       "只能引用 INPUT_JSON 裡有的資料"):
            self.assertIn(phrase, rules)


def closing_at(close):
    """把基準日那一根的收盤換掉（開高低照一般的一根）。"""
    rows = stock_bars()
    rows[-1] = bar(BASE, close - 0.5, close + 1, close - 1, close)
    return stock_input(stock_daily=rows)


class TwoLineRuleTests(WatchLedgerTestCase):
    def judged(self, reply, data=None):
        self.reset_calls()
        root = self.tmp / f"case{len(list(self.tmp.iterdir()))}"
        return self.write(data, [envelope(reply)], root=root)["record"]

    def test_not_yet_and_near_take_a_trigger_line_above_the_base_close(self):
        # 收盤 110：MA10 110.9 在上方，MA20 108.7、MA5 107.2 在下方
        for status in ("還沒到", "接近"):
            with self.subTest(status):
                ok = self.judged(answer(status, "ma10", "ma20"))
                self.assertEqual((ok["kind"], ok["status"], ok["trigger_line"]["value"]), ("judgment", status, 110.9))
                bad = self.judged(answer(status, "ma20", "ma5"))
                self.assertEqual((bad["kind"], bad["miss_reason"]), ("miss", "answer_invalid"))

    def test_worth_watching_takes_a_line_the_close_already_stands_on(self):
        ok = self.judged(answer("可以留意", "ma20", "swing_low_20260925"))
        self.assertEqual((ok["kind"], ok["status"], ok["trigger_line"]["value"]), ("judgment", "可以留意", 108.7))
        bad = self.judged(answer("可以留意", "ma10", "ma20"))
        self.assertEqual((bad["kind"], bad["miss_reason"]), ("miss", "answer_invalid"))

    def test_a_line_exactly_at_the_close_counts_as_already_stood_on(self):
        # 基準日收盤改成 113（高 114，補不到 115 的缺口上緣）：缺口下緣 113 剛好等於收盤
        ok = self.judged(answer("可以留意", "gap_down_lower_20260923", "swing_low_20260925"), closing_at(113))
        self.assertEqual((ok["kind"], ok["trigger_line"]["value"], ok["base_close"]), ("judgment", 113, 113))
        bad = self.judged(answer("接近", "gap_down_lower_20260923", "swing_low_20260925"), closing_at(113))
        self.assertEqual(bad["kind"], "miss")

    def test_the_wrong_line_must_be_below_the_trigger_line(self):
        above = self.judged(answer("接近", "ma10", "swing_high_20260921"))
        self.assertEqual((above["kind"], above["miss_reason"]), ("miss", "answer_invalid"))
        # 前波高點與前 20 日盤中高點都是 122：同值不算在下方
        level = self.judged(answer("接近", "swing_high_20260921", "prior_high_20"))
        self.assertEqual((level["kind"], level["miss_reason"]), ("miss", "answer_invalid"))


class AnswerValidationTests(WatchLedgerTestCase):
    def assert_miss(self, result, reason, model_calls):
        self.assertEqual(result["outcome"], "written")
        record = result["record"]
        self.assertEqual((record["kind"], record["miss_reason"]), ("miss", reason))
        for field in ("status", "trigger_line", "wrong_line", "reason"):
            self.assertNotIn(field, record)
        self.assertEqual(calls(self.bin), model_calls)

    def test_invalid_answer_is_retried_once_with_the_same_input(self):
        result = self.write(responses=[envelope(answer(trigger="no_such_level")), envelope(answer())])
        self.assertEqual(result["record"]["kind"], "judgment")
        self.assertEqual(calls(self.bin), 2)
        self.assertEqual([a["status"] for a in result["record"]["attempts"]], ["answer_invalid"])

    def test_answers_that_stay_invalid_after_one_retry_become_a_miss(self):
        cases = {
            "狀態不在清單內": answer("買進"),
            "觸發線不是候選關卡": answer(trigger="no_such_level"),
            "看錯線不是候選關卡": answer(wrong="no_such_level"),
            "理由含阿拉伯數字": answer(reason="站上110就表態"),
            "理由含中文數字": answer(reason="連續三天量縮"),
            "理由引用不存在的候選": answer(reason_refs=["no_such_level"]),
            "引用輸入裡沒有的消息編號": answer(news_refs=["a9"]),
            "基準日不符": answer(base_date="2026-09-29"),
            "代號不符": answer(code="2317"),
            "多出欄位": {**answer(), "target_price": "很高"},
            "少了欄位": {key: value for key, value in answer().items() if key != "wrong_line"},
        }
        for name, bad in cases.items():
            with self.subTest(name):
                self.reset_calls()
                result = self.write(responses=[envelope(bad)], root=self.root / name)
                self.assert_miss(result, "answer_invalid", 2)

    def test_news_can_only_be_cited_when_the_layer_has_it(self):
        self.reset_calls()
        result = self.write(stock_input(announcements=None), [envelope(answer(news_refs=["a1"]))])
        self.assert_miss(result, "answer_invalid", 2)

    def test_model_failure_timeout_and_garbled_output_become_a_miss_without_retry(self):
        cases = [
            ("model_failed", dict(exit_code=1)),
            ("model_failed", dict(raw=json.dumps({"event": "result", "result": {"status": "ERROR"}}).encode())),
            ("output_garbled", dict(raw=b"\xff\xfe not json")),
            ("model_timeout", dict(sleep=5)),
        ]
        for index, (reason, options) in enumerate(cases):
            with self.subTest(reason=reason, options=options):
                self.reset_calls()
                model = fake_model(self.bin, [envelope(answer())], **options)
                result = self.write(model=model, root=self.root / f"case{index}", timeout_seconds=1 if "sleep" in options else 60)
                self.assert_miss(result, reason, 1)

    def test_model_failure_keeps_the_tail_of_its_error_output_as_the_reason(self):
        model = fake_model(self.bin, [envelope(answer())], exit_code=3, stderr="noise\nlogin expired\n")
        result = self.write(model=model)
        self.assert_miss(result, "model_failed", 1)
        self.assertIn("結束碼 3", result["record"]["miss_detail"])
        self.assertIn("login expired", result["record"]["miss_detail"])


class LockingTests(WatchLedgerTestCase):
    def judgment_file(self):
        files = list((self.root / "judgments").glob("*.json"))
        self.assertEqual(len(files), 1)
        return files[0]

    def test_rerun_with_the_same_input_keeps_the_first_record_and_does_not_call_the_model(self):
        first = self.write()["record"]
        before = self.judgment_file().read_bytes()
        again = self.write(model=fake_model(self.tmp, [envelope(answer("還沒到"))]), now=clock(at(BASE, 18)))
        self.assertEqual((again["outcome"], again["record"]), ("unchanged", first))
        self.assertEqual(calls(self.tmp), 0)
        self.assertEqual(self.judgment_file().read_bytes(), before)

    def test_rerun_with_different_input_is_refused(self):
        self.write()
        before = self.judgment_file().read_bytes()
        again = self.write(stock_input(technical={"trend_labels": {"short": "偏空"}}))
        self.assertEqual(again["outcome"], "conflict")
        self.assertEqual(self.judgment_file().read_bytes(), before)

    def test_a_miss_cannot_later_be_replaced_by_a_judgment(self):
        self.write(model=fake_model(self.bin, [envelope(answer())], exit_code=1))
        again = self.write(model=fake_model(self.tmp, [envelope(answer())]), now=clock(at(BASE, 20)))
        self.assertEqual((again["outcome"], again["record"]["kind"]), ("unchanged", "miss"))
        self.assertEqual(calls(self.tmp), 0)
        self.assertEqual([r["kind"] for r in wl.read_ledger(self.root)["judgments"]], ["miss"])

    def test_one_stock_has_one_record_per_base_date_and_edition_even_if_the_method_changes(self):
        self.write()
        newer = stock_input(method_text=METHOD_TEXT.replace("版本：0.4", "版本：0.5"))
        again = self.write(newer)
        self.assertEqual(again["outcome"], "conflict")
        self.assertEqual(again["record"]["method_version"], "0.4")
        self.assertEqual(len(wl.read_ledger(self.root)["judgments"]), 1)

    def test_evening_and_morning_and_other_stocks_are_stored_separately(self):
        self.write()
        self.write(edition="morning", session_date="2026-10-01", now=clock(at("2026-10-01", 8)))
        self.write(stock_input(code="2317", name="鴻海"), [envelope(answer(code="2317"))])
        stored = sorted((r["code"], r["edition"]) for r in wl.read_ledger(self.root)["judgments"])
        self.assertEqual(stored, [("2317", "evening"), ("2330", "evening"), ("2330", "morning")])

    def test_rerun_after_an_old_stock_or_index_bar_was_corrected_is_refused(self):
        # 前面多接 48 根，共 70 根：第 1 根不在交給模型的最近 60 根裡，只用在「隨便挑」。它被更正時重跑仍要被擋下。
        older_days = weekdays("2026-06-24", 48)
        self.assertLess(older_days[-1], DATES[0])
        stock = [bar(day, 99.5, 101, 99, 100) for day in older_days] + stock_bars()
        index = [{"date": day, "close": 1000} for day in older_days] + index_bars()
        self.write(stock_input(stock_daily=stock, index_daily=index))
        for name, change in (("個股", {"stock_daily": [dict(stock[0], close=100.5), *stock[1:]], "index_daily": index}),
                             ("指數", {"stock_daily": stock, "index_daily": [dict(index[0], close=1001), *index[1:]]})):
            with self.subTest(name):
                again = self.write(stock_input(**change), now=clock(at(BASE, 18)))
                self.assertEqual(again["outcome"], "conflict")

    def test_tampered_record_is_detected_on_read(self):
        self.write()
        path = self.judgment_file()
        record = json.loads(path.read_text(encoding="utf-8"))
        record["status"] = "可以留意"
        path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(wl.LedgerIntegrityError):
            wl.read_ledger(self.root)

    def test_tampered_stored_input_is_detected_even_if_the_seal_is_recomputed(self):
        self.write()
        path = self.judgment_file()
        record = json.loads(path.read_text(encoding="utf-8"))
        record["input"]["technical"] = {"trend_labels": {"short": "偏空"}}
        body = {k: v for k, v in record.items() if k != "record_sha256"}
        record["record_sha256"] = hashlib.sha256(json.dumps(
            body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(wl.LedgerIntegrityError):
            wl.read_ledger(self.root)


AFTER_EVENING_DEADLINE = at("2026-10-01", 0, 1)


class DeadlineTests(WatchLedgerTestCase):
    NOT_READY = {
        "個股日 K 還沒到基準日": lambda: stock_input(stock_daily=stock_bars()[:-1]),
        "指數日 K 還沒到基準日": lambda: stock_input(index_daily=index_bars()[:-1]),
        "個股日 K 不滿二十根": lambda: stock_input(stock_daily=stock_bars()[-19:]),
        "沒有技術層": lambda: stock_input(technical=None),
    }

    def test_input_not_ready_before_the_deadline_writes_nothing(self):
        for name, build in self.NOT_READY.items():
            with self.subTest(name):
                result = self.write(build())
                self.assertEqual((result["outcome"], result["record"]), ("not_ready", None))
                self.assertTrue(result["detail"])
                self.assertEqual(calls(self.bin), 0)
                self.assertEqual(wl.read_ledger(self.root)["judgments"], [])
        later = self.write(now=clock(at(BASE, 20)))
        self.assertEqual(later["record"]["kind"], "judgment")

    def test_input_still_not_ready_after_the_deadline_is_a_miss(self):
        for index, (name, build) in enumerate(self.NOT_READY.items()):
            with self.subTest(name):
                result = self.write(build(), now=clock(AFTER_EVENING_DEADLINE), root=self.root / str(index))
                self.assertEqual((result["record"]["kind"], result["record"]["miss_reason"]), ("miss", "input_not_ready"))
                self.assertTrue(result["record"]["miss_detail"])
                self.assertEqual(calls(self.bin), 0)

    def test_evening_edition_past_the_end_of_base_date_is_a_miss(self):
        result = self.write(now=clock(AFTER_EVENING_DEADLINE))
        self.assertEqual((result["record"]["kind"], result["record"]["miss_reason"]), ("miss", "deadline_passed"))
        self.assertEqual(calls(self.bin), 0)

    def test_answer_arriving_after_the_deadline_is_a_miss(self):
        result = self.write(now=clock(at(BASE, 23, 50), at("2026-10-01", 0, 5)))
        record = result["record"]
        self.assertEqual((record["kind"], record["miss_reason"]), ("miss", "deadline_passed"))
        self.assertNotIn("status", record)
        self.assertEqual(record["locked_at"], "2026-10-01T00:05:00+08:00")
        self.assertEqual(calls(self.bin), 1)

    def test_morning_edition_must_lock_fifteen_minutes_before_the_open(self):
        ok = self.write(edition="morning", session_date="2026-10-01", now=clock(at("2026-10-01", 8, 44)))
        self.assertEqual(ok["record"]["kind"], "judgment")
        late = self.write(edition="morning", session_date="2026-10-01", now=clock(at("2026-10-01", 8, 45)),
                          root=self.tmp / "late")
        self.assertEqual(late["record"]["miss_reason"], "deadline_passed")


class InputLayerTests(WatchLedgerTestCase):
    def test_a_layer_without_data_is_written_anyway_and_named_as_missing(self):
        full = self.write()["record"]
        self.assertEqual((full["layers"], full["missing_layers"]), (["technical", "market", "news"], []))

        no_market = self.write(stock_input(market_forecast=None), root=self.tmp / "a")["record"]
        self.assertEqual((no_market["kind"], no_market["missing_layers"]), ("judgment", ["market"]))
        self.assertIsNone(no_market["input"]["market"])
        no_news = self.write(stock_input(announcements=None), root=self.tmp / "b")["record"]
        self.assertEqual((no_news["kind"], no_news["missing_layers"]), ("judgment", ["news"]))
        # 抓到了、只是這幾天沒有公告：不算缺漏
        quiet = self.write(stock_input(announcements=[]), root=self.tmp / "c")["record"]
        self.assertEqual(quiet["missing_layers"], [])
        # 某一天缺資料不換系列：輸入版本只看排程打開了哪幾層
        self.assertEqual({r["input_version"] for r in (no_market, no_news, quiet)}, {full["input_version"]})

    def test_input_version_names_the_layers_that_are_switched_on(self):
        only_technical = self.write(layers=("technical",))["record"]
        self.assertRegex(only_technical["input_version"], r"^technical-v1-[0-9a-f]{8}$")
        self.assertEqual((only_technical["layers"], only_technical["missing_layers"]), (["technical"], []))
        # 沒打開的層不放進提示，也不算缺漏
        self.assertIsNone(only_technical["input"]["market"])
        self.assertIsNone(only_technical["input"]["news"]["announcements"])

        lessons = [{"transition_date": "2026-08-03", "trigger_line": "MA20", "beat_index_5": True}]
        with_lessons = self.write(stock_input(lessons=lessons), layers=("lessons", "technical", "market", "news"),
                                  root=self.tmp / "l")["record"]
        self.assertRegex(with_lessons["input_version"], r"^technical\+market\+news\+lessons-v1-[0-9a-f]{8}$")
        self.assertEqual(with_lessons["input"]["lessons"], lessons)
        self.assertEqual(wl.input_version_of(("technical",)), only_technical["input_version"])
        self.assertNotEqual(wl.input_version_of(("technical",)), wl.input_version_of(("technical", "market")))

    def test_news_headlines_and_overseas_peers_keep_their_place_but_never_reach_the_model(self):
        data = stock_input(news_headlines=[{"id": "h1", "source": "cna", "title": "外電報導同業大漲"}],
                           peers=[{"symbol": "NVDA", "change_pct": 5.2}], realtime_quote={"price": 987.65})
        record = self.write(data, model=recording_model(self.bin, answer()))["record"]
        prompt = (self.bin / "prompt.txt").read_text(encoding="utf-8")
        for secret in ("外電報導同業大漲", "NVDA", "987.65", "cna"):
            self.assertNotIn(secret, prompt)
            self.assertNotIn(secret, json.dumps(record, ensure_ascii=False))
        self.assertIsNone(record["input"]["peers"])
        self.assertEqual(record["input"]["news"]["headlines"], None)
        self.assertIn("公告九月營收", prompt)

    def test_layers_that_may_not_be_sent_cannot_be_switched_on(self):
        with self.assertRaises(ValueError):
            self.write(layers=("technical", "peers"))
        with self.assertRaises(ValueError):
            self.write(layers=("technical", "headlines"))
        with self.assertRaises(ValueError):
            self.write(layers=("market", "news"))
        # 消息層這一版只收官方公告：其他來源混進來時拒絕，不送給模型
        mixed = stock_input(announcements=[{"id": "h1", "source": "cna", "title": "外電報導"}])
        with self.assertRaises(ValueError):
            self.write(mixed)
        self.assertEqual(calls(self.bin), 0)
        self.assertEqual(wl.read_ledger(self.root)["judgments"], [])


def three_stocks():
    return [stock_input(code=code, name=name) for code, name in (("2330", "台積電"), ("2317", "鴻海"), ("2454", "聯發科"))]


class BatchTests(WatchLedgerTestCase):
    def batch(self, stocks, responses, *, model=None, **kwargs):
        model = model or fake_model(self.bin, responses)
        return wl.write_judgments(self.root, stocks, edition="evening", model_bin=model, model="test-model",
                                  now=clock(EVENING_OK), **kwargs)

    def kinds(self, results):
        return [(r["code"], r["outcome"], r["record"]["kind"], r["record"].get("miss_reason")) for r in results]

    def test_stocks_are_judged_in_the_given_order_and_one_failure_does_not_stop_the_rest(self):
        # 假模型依呼叫順序回答：第 1 次給 2330；第 2、3 次（2317 與它的重試）都不合格；第 4 次給 2454
        bad = answer(code="2317", trigger="no_such_level")
        results = self.batch(three_stocks(), [envelope(answer(code="2330")), envelope(bad), envelope(bad),
                                              envelope(answer(code="2454"))])
        self.assertEqual(self.kinds(results), [("2330", "written", "judgment", None),
                                               ("2317", "written", "miss", "answer_invalid"),
                                               ("2454", "written", "judgment", None)])
        self.assertEqual(calls(self.bin), 4)

    def test_when_the_call_budget_runs_out_the_later_stocks_are_missed_with_the_reason(self):
        results = self.batch(three_stocks(), [envelope(answer(code="2330")), envelope(answer(code="2317"))],
                             call_budget=2)
        self.assertEqual(self.kinds(results), [("2330", "written", "judgment", None),
                                               ("2317", "written", "judgment", None),
                                               ("2454", "written", "miss", "quota_exhausted")])
        self.assertIn("額度", results[2]["record"]["miss_detail"])
        self.assertEqual(calls(self.bin), 2)
        self.assertEqual(len(wl.read_ledger(self.root)["judgments"]), 3)

    def test_a_retry_also_needs_budget(self):
        bad = answer(trigger="no_such_level")
        results = self.batch(three_stocks()[:1], [envelope(bad), envelope(answer())], call_budget=1)
        self.assertEqual(self.kinds(results), [("2330", "written", "miss", "quota_exhausted")])
        self.assertEqual([a["status"] for a in results[0]["record"]["attempts"]], ["answer_invalid"])
        self.assertEqual(calls(self.bin), 1)

    def test_a_quota_error_from_the_model_stops_calling_and_marks_the_rest(self):
        model = fake_model(self.bin, [envelope(answer())], exit_code=1, stderr="429 RESOURCE_EXHAUSTED: quota exceeded\n")
        results = self.batch(three_stocks(), None, model=model)
        self.assertEqual([r["record"]["miss_reason"] for r in results], ["quota_exhausted"] * 3)
        self.assertIn("RESOURCE_EXHAUSTED", results[0]["record"]["miss_detail"])
        self.assertEqual(calls(self.bin), 1)

    def test_which_model_errors_count_as_quota(self):
        cases = {"HTTP 429 Too Many Requests": True, "You exceeded your current quota": True,
                 "rate limit reached": True, "connection to port 4290 refused": False, "login expired": False}
        for index, (stderr, is_quota) in enumerate(cases.items()):
            with self.subTest(stderr):
                self.reset_calls()
                self.root = self.tmp / f"ledger{index}"
                results = self.batch(three_stocks()[:2], None,
                                     model=fake_model(self.bin, [envelope(answer())], exit_code=1, stderr=stderr + "\n"))
                expected = "quota_exhausted" if is_quota else "model_failed"
                self.assertEqual([r["record"]["miss_reason"] for r in results], [expected] * 2)
                # 不是額度問題時，下一檔照常呼叫模型
                self.assertEqual(calls(self.bin), 1 if is_quota else 2)

    def test_locked_and_not_ready_stocks_do_not_use_budget_or_get_a_quota_miss(self):
        stocks = three_stocks()
        self.batch(stocks[:1], [envelope(answer(code="2330"))])
        self.reset_calls()
        stocks[1]["stock_daily"] = stock_bars()[:-1]
        results = self.batch(stocks, [envelope(answer(code="2454"))], call_budget=0)
        self.assertEqual([(r["code"], r["outcome"]) for r in results],
                         [("2330", "unchanged"), ("2317", "not_ready"), ("2454", "written")])
        self.assertEqual(results[2]["record"]["miss_reason"], "quota_exhausted")
        self.assertEqual(calls(self.bin), 0)

    def test_one_stock_with_unusable_input_is_rejected_without_stopping_the_rest(self):
        stocks = three_stocks()
        stocks[0]["stock_daily"] = list(reversed(stock_bars()))
        results = self.batch(stocks, [envelope(answer(code="2317")), envelope(answer(code="2454"))])
        self.assertEqual([(r["code"], r["outcome"]) for r in results],
                         [("2330", "rejected"), ("2317", "written"), ("2454", "written")])
        self.assertIn("日期遞增", results[0]["detail"])
        self.assertIsNone(results[0]["record"])

    def test_malformed_events_or_announcements_only_reject_that_stock(self):
        stocks = three_stocks()
        stocks[0]["capital_events"] = [{"code": "2330", "type": "減資"}]      # 沒有日期
        stocks[1]["announcements"] = ["不是一則公告"]
        results = self.batch(stocks, [envelope(answer(code="2454"))])
        self.assertEqual([(r["code"], r["outcome"]) for r in results],
                         [("2330", "rejected"), ("2317", "rejected"), ("2454", "written")])
        self.assertTrue(all(r["detail"] for r in results[:2]))
        self.assertEqual(calls(self.bin), 1)


class RandomPickTests(WatchLedgerTestCase):
    def test_share_of_past_days_that_beat_the_index_is_locked_with_its_history_range(self):
        record = self.write()["record"]
        # 五日（第 t 根買、第 t+5 根看）共 17 筆，指數同期：第 1–5 根 1000 → 第 6–10 根 1050 是 +5%；
        # 第 12–16 根 1050 → 第 17–21 根 1155 是 +10%；其餘 0%。
        #   t=1：100→105 = +5%，指數也 +5% → 剛好相同，不算贏
        #   t=2–5：101→106（+4.95%）、102→107、103→108、104→109（+4.81%）都小於 +5% → 輸
        #   t=6–11：105→110、106→111、107→112、108→113、109→120、110→116，指數 0% → 贏 6 筆
        #   t=12：111→112（+0.9%）輸給 +10%；t=13–16 個股下跌 → 輸；t=17：112→110，指數 0% → 輸
        # 二十日共 2 筆：100→108（+8%）、101→110（+8.9%），指數 1000→1155（+15.5%）→ 都輸
        self.assertEqual(record["random_pick"], {
            "5": {"wins": 6, "samples": 17, "from": "2026-09-01", "to": "2026-09-30"},
            "20": {"wins": 0, "samples": 2, "from": "2026-09-01", "to": "2026-09-30"},
        })

    def test_days_without_a_stock_bar_are_left_out(self):
        # 個股第 6 根（09/08）停牌沒有日 K：以它為起點（t=6）或終點（t=1）的五日樣本都不算 → 剩 15 筆、贏 5 筆
        rows = [row for row in stock_bars() if row["date"] != DATES[5]]
        record = self.write(stock_input(stock_daily=rows))["record"]
        self.assertEqual(record["random_pick"]["5"], {"wins": 5, "samples": 15, "from": "2026-09-02", "to": "2026-09-30"})
        self.assertEqual(record["random_pick"]["20"]["samples"], 2)

    def test_a_miss_has_no_random_pick(self):
        record = self.write(model=fake_model(self.bin, [envelope(answer())], exit_code=1))["record"]
        self.assertNotIn("random_pick", record)


# 基準日 09/30 之後的交易日：10/07 休市（沒有資料）。之後第 5 個交易日是 10/08、第 20 個是 10/29。
#   10/01、10/02、10/05、10/06、10/08、10/09（6 天）＋ 10/12–16（11）＋ 10/19–23（16）＋ 10/26–29（20）
LATER_DAYS = [day for day in weekdays("2026-10-01", 22) if day != "2026-10-07"][:20]
# 個股收盤：前 6 天 111、112、113、112、112.2、113，之後都是 114，最後一天（10/29）115.5
LATER_CLOSES = [111, 112, 113, 112, 112.2, 113, *[114] * 13, 115.5]
# 加權指數收盤：前 6 天 1160、1165、1170、1175、1178.1、1180，之後都是 1190，最後一天 1201.2
LATER_INDEX = [1160, 1165, 1170, 1175, 1178.1, 1180, *[1190] * 13, 1201.2]


def stock_through(day, *, changes=None):
    rows = stock_bars() + [bar(d, c - 0.5, c + 1, c - 1, c) for d, c in zip(LATER_DAYS, LATER_CLOSES) if d <= day]
    for row in rows:
        row.update((changes or {}).get(row["date"], {}))
    return rows


def index_through(day, *, changes=None):
    rows = index_bars() + [{"date": d, "close": c} for d, c in zip(LATER_DAYS, LATER_INDEX) if d <= day]
    for row in rows:
        row.update((changes or {}).get(row["date"], {}))
    return rows


# 之後每一天都還在的候選：前波高點 122（收盤一直低於它）、波段低點 103（收盤一直高於它）、波段低點 95
ANSWERS = {
    "還沒到": dict(trigger="swing_high_20260921", wrong="swing_low_20260925"),
    "接近": dict(trigger="swing_high_20260921", wrong="swing_low_20260925"),
    "可以留意": dict(trigger="swing_low_20260925", wrong="swing_low_20260907"),
}


class SeriesTestCase(WatchLedgerTestCase):
    def day(self, base, status, *, edition="evening", code="2330", **extra):
        """替某一檔在某個基準日寫一份；status 給 None 時讓模型失敗，記成漏記。"""
        data = stock_input(code=code, base_date=base, stock_daily=stock_through(base), index_daily=index_through(base),
                           data_cutoffs={"stock": base, "index": base}, **extra.pop("data", {}))
        self.reset_calls()
        if status is None:
            model = fake_model(self.bin, [envelope(answer())], exit_code=1)
        else:
            model = fake_model(self.bin, [envelope(answer(status, base_date=base, code=code, **ANSWERS[status]))])
        options = {"now": clock(at(base, 17))}
        if edition == "morning":
            session = (LATER_DAYS + ["2026-10-30"])[([BASE] + LATER_DAYS).index(base)]
            options = {"now": clock(at(session, 8)), "session_date": session}
        record = self.write(data, edition=edition, model=model, **options, **extra)["record"]
        self.assertEqual(record["kind"], "miss" if status is None else "judgment")
        return record


class TransitionDayTests(SeriesTestCase):
    def test_turning_worth_watching_is_a_transition_and_staying_so_is_not(self):
        near = self.day(BASE, "接近")
        turned = self.day("2026-10-01", "可以留意")
        stayed = self.day("2026-10-02", "可以留意")
        self.assertEqual([r["transition"] for r in (near, turned, stayed)], [False, True, False])
        self.assertEqual(turned["compared_with"], near["judgment_id"])
        self.assertEqual(stayed["compared_with"], turned["judgment_id"])

    def test_a_miss_in_between_is_skipped_when_looking_back(self):
        self.day(BASE, "可以留意")
        self.day("2026-10-01", None)
        after_miss = self.day("2026-10-02", "可以留意")
        self.assertFalse(after_miss["transition"])
        self.day("2026-10-05", "還沒到")
        self.day("2026-10-06", None)
        back = self.day("2026-10-08", "可以留意")
        self.assertTrue(back["transition"])
        self.assertIn("2026-10-05", back["compared_with"])

    def test_the_first_judgment_after_watching_starts_counts_when_it_is_worth_watching(self):
        first = self.day(BASE, "可以留意")
        self.assertEqual((first["transition"], first["compared_with"]), (True, None))
        # 第一份是漏記時，之後第一份有判斷的也一樣
        self.day(BASE, None, code="2317")
        self.assertTrue(self.day("2026-10-01", "可以留意", code="2317")["transition"])

    def test_evening_and_morning_editions_and_each_stock_are_separate_series(self):
        self.assertTrue(self.day(BASE, "可以留意")["transition"])
        self.assertTrue(self.day(BASE, "可以留意", edition="morning")["transition"])
        self.assertFalse(self.day("2026-10-01", "可以留意")["transition"])
        self.assertFalse(self.day("2026-10-01", "可以留意", edition="morning")["transition"])
        self.assertTrue(self.day("2026-10-01", "可以留意", code="2317")["transition"])

    def test_a_new_method_version_does_not_restart_the_series(self):
        # 系列只分「哪一檔、晚間或晨間」：方法改版那一天還是「可以留意」，不是從別的狀態變過來，不算轉變日
        old = self.day(BASE, "可以留意")
        newer = self.day("2026-10-01", "可以留意", data={"method_text": METHOD_TEXT.replace("版本：0.4", "版本：0.5")})
        self.assertEqual((newer["method_version"], newer["transition"], newer["compared_with"]),
                         ("0.5", False, old["judgment_id"]))

    def test_a_tampered_earlier_record_stops_the_write_before_the_model_is_called(self):
        self.day(BASE, "可以留意")
        path = next((self.root / "judgments").glob("*.json"))
        record = json.loads(path.read_text(encoding="utf-8"))
        record["status"] = "接近"
        path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        self.reset_calls()
        base = "2026-10-01"
        data = stock_input(base_date=base, stock_daily=stock_through(base), index_daily=index_through(base))
        with self.assertRaises(wl.LedgerIntegrityError):
            self.write(data, [envelope(answer("可以留意", base_date=base, **ANSWERS["可以留意"]))], now=clock(at(base, 17)))
        self.assertEqual(calls(self.bin), 0)


def market(day, *, stock_changes=None, index_changes=None, capital_events=(), codes=("2330",), drop=()):
    """截至某一天的對帳資料：指數日 K 與各檔個股的實際成交價日 K、股本變動事件。"""
    rows = [row for row in stock_through(day, changes=stock_changes) if row["date"] not in drop]
    return {"index_daily": index_through(day, changes=index_changes),
            "stocks": {code: {"stock_daily": [dict(row) for row in rows], "capital_events": list(capital_events)}
                       for code in codes}}


def cells(rows):
    return sorted((r["horizon"], r["expiry_date"], r["stock_return"], r["index_return"], r["beat_index"]) for r in rows)


class ReconcileTests(SeriesTestCase):
    def reconcile(self, day="2026-10-08", **options):
        data = market(day, **options)
        return wl.reconcile(self.root, data["index_daily"], data["stocks"])

    def test_a_transition_day_is_judged_five_and_twenty_trading_days_later_against_the_index(self):
        record = self.day(BASE, "可以留意")
        result = self.reconcile("2026-10-08")
        # 五日：10/07 休市跳過，第 5 個交易日是 10/08。個股 110→112.2 = +2%；指數 1155→1178.1 = +2% → 剛好相同，不算贏
        self.assertEqual(cells(result["appended"]), [(5, "2026-10-08", 0.02, 0.02, False)])
        self.assertEqual(result["pending"], [{"judgment_id": record["judgment_id"], "horizon": 20}])
        self.assertEqual((result["undeterminable"], result["conflicts"]), ([], []))

        # 二十日：10/29。個股 110→115.5 = +5%；指數 1155→1201.2 = +4% → 贏
        later = self.reconcile("2026-10-29")
        self.assertEqual(cells(later["appended"]), [(20, "2026-10-29", 0.05, 0.04, True)])
        stored = wl.read_ledger(self.root)["reconciliations"]
        self.assertEqual(len(stored), 2)
        twenty = next(r for r in stored if r["horizon"] == 20)
        self.assertEqual((twenty["code"], twenty["edition"], twenty["base_date"], twenty["judgment_sha256"]),
                         ("2330", "evening", BASE, record["record_sha256"]))
        self.assertEqual((twenty["stock_base_close"], twenty["stock_expiry_close"],
                          twenty["index_base_close"], twenty["index_expiry_close"]), (110, 115.5, 1155, 1201.2))

    def test_beating_the_index_by_a_hair_is_a_win_and_falling_short_is_not(self):
        self.day(BASE, "可以留意")
        # 個股 10/08 收 112.21：110→112.21 = +2.0091% > 指數 +2%
        won = self.reconcile(stock_changes={"2026-10-08": {"close": 112.21}})
        self.assertEqual(cells(won["appended"]), [(5, "2026-10-08", 0.020091, 0.02, True)])

    def test_only_transition_days_are_reconciled(self):
        self.day(BASE, "接近")                      # 不是「可以留意」
        self.day("2026-10-01", "可以留意")          # 轉變日
        self.day("2026-10-02", "可以留意")          # 連續，不重複算
        self.day("2026-10-05", None)                # 漏記
        result = self.reconcile("2026-10-29")
        # 轉變日 10/01（個股 111、指數 1160）：之後第 5 個交易日是 10/09（113、1180）
        #   個股 113/111 − 1 = +1.8018%；指數 1180/1160 − 1 = +1.7241% → 贏。二十日要到 10/30，還沒到期
        self.assertEqual(cells(result["appended"]), [(5, "2026-10-09", 0.018018, 0.017241, True)])
        self.assertEqual([p["horizon"] for p in result["pending"]], [20])
        self.assertEqual(result["appended"][0]["base_date"], "2026-10-01")

    def test_not_yet_expired_is_pending_and_late_data_is_reconciled_once(self):
        self.day(BASE, "可以留意")
        first = self.reconcile("2026-10-06")
        self.assertEqual(first["appended"], [])
        self.assertEqual(sorted(p["horizon"] for p in first["pending"]), [5, 20])
        second = self.reconcile("2026-10-09")
        self.assertEqual(cells(second["appended"]), [(5, "2026-10-08", 0.02, 0.02, False)])
        third = self.reconcile("2026-10-09")
        self.assertEqual((third["appended"], third["conflicts"]), ([], []))
        self.assertEqual(len(wl.read_ledger(self.root)["reconciliations"]), 1)

    def test_stock_data_that_has_not_arrived_yet_is_pending(self):
        self.day(BASE, "可以留意")
        # 指數日 K 已經到 10/08，個股日 K 只到 10/06：資料晚到，先等
        data = market("2026-10-08")
        data["stocks"]["2330"]["stock_daily"] = stock_through("2026-10-06")
        result = wl.reconcile(self.root, data["index_daily"], data["stocks"])
        self.assertEqual(result["appended"], [])
        self.assertEqual(sorted(p["horizon"] for p in result["pending"]), [5, 20])
        self.assertEqual(cells(self.reconcile("2026-10-08")["appended"]), [(5, "2026-10-08", 0.02, 0.02, False)])

    def test_missing_data_on_the_expiry_day_is_undeterminable_not_wrong(self):
        record = self.day(BASE, "可以留意")
        ref = {"judgment_id": record["judgment_id"], "horizon": 5, "expiry_date": "2026-10-08"}
        cases = {
            "到期日個股停牌": dict(drop=("2026-10-08",)),
            "到期日指數收盤缺漏": dict(index_changes={"2026-10-08": {"close": None}}),
            "這一檔沒有給日 K": dict(codes=("2317",)),
        }
        for name, options in cases.items():
            with self.subTest(name):
                result = self.reconcile("2026-10-09", **options)
                self.assertEqual(result["appended"], [])
                self.assertIn(ref, result["undeterminable"])
        fixed = self.reconcile("2026-10-09")
        self.assertEqual(cells(fixed["appended"]), [(5, "2026-10-08", 0.02, 0.02, False)])

    def split_on_oct5(self, close_on_oct8):
        # 10/05 起一股拆四股：10/05 之後的實際成交價是四分之一。官方參考價 28 ÷ 拆股前（10/02）收盤 112 = 0.25
        quarter = {day: {key: value / 4 for key, value in bar(day, c - 0.5, c + 1, c - 1, c).items() if key != "date"}
                   for day, c in zip(LATER_DAYS, LATER_CLOSES) if day >= "2026-10-05"}
        quarter["2026-10-08"]["close"] = close_on_oct8
        event = {"code": "2330", "date": "2026-10-05", "type": "變更面額", "close_before": 112, "reference": 28}
        return quarter, event

    def test_a_split_inside_the_period_is_restored_before_comparing(self):
        self.day(BASE, "可以留意")
        # 基準日收盤 110 還原成 110 × 0.25 = 27.5；10/08 實際收 28.1 → 28.1/27.5 − 1 = +2.1818% > 指數 +2% → 贏
        # （不還原的話會是 28.1/110 − 1 = −74%）
        quarter, event = self.split_on_oct5(28.1)
        result = self.reconcile(stock_changes=quarter, capital_events=[event])
        self.assertEqual(cells(result["appended"]), [(5, "2026-10-08", 0.021818, 0.02, True)])
        row = result["appended"][0]
        self.assertEqual((row["stock_base_close"], row["stock_base_close_restored"], row["stock_expiry_close"]),
                         (110, 27.5, 28.1))

    def test_a_capital_reduction_inside_the_period_is_restored_before_comparing(self):
        self.day(BASE, "可以留意")
        # 10/05 減資，兩股併一股：之後的實際成交價是兩倍。官方參考價 224 ÷ 減資前（10/02）收盤 112 = 2
        # 基準日收盤 110 還原成 220；10/08 實際收 224.5 → 224.5/220 − 1 = +2.0455% > 指數 +2% → 贏
        doubled = {day: {key: value * 2 for key, value in bar(day, c - 0.5, c + 1, c - 1, c).items() if key != "date"}
                   for day, c in zip(LATER_DAYS, LATER_CLOSES) if day >= "2026-10-05"}
        doubled["2026-10-08"]["close"] = 224.5
        event = {"code": "2330", "date": "2026-10-05", "type": "減資", "close_before": 112, "reference": 224}
        result = self.reconcile(stock_changes=doubled, capital_events=[event])
        self.assertEqual(cells(result["appended"]), [(5, "2026-10-08", 0.020455, 0.02, True)])
        self.assertEqual(result["appended"][0]["stock_base_close_restored"], 220)

    def test_a_capital_change_without_official_numbers_inside_the_period_is_undeterminable(self):
        record = self.day(BASE, "可以留意")
        quarter, event = self.split_on_oct5(28.1)
        result = self.reconcile(stock_changes=quarter, capital_events=[{**event, "reference": None}])
        self.assertEqual(result["appended"], [])
        self.assertEqual(result["undeterminable"], [
            {"judgment_id": record["judgment_id"], "horizon": 5, "expiry_date": "2026-10-08"}])

    def test_a_split_after_the_expiry_day_does_not_disturb_an_existing_reconciliation(self):
        self.day(BASE, "可以留意")
        self.reconcile("2026-10-08", stock_changes={"2026-10-08": {"close": 112.21}})
        before = wl.read_ledger(self.root)["reconciliations"]
        # 10/09 才拆股：10/08 以前整段的還原價一起變成四分之一，漲跌幅不變，重算要得到同一筆
        late = {"2026-10-09": {key: value / 4 for key, value in bar("x", 112.5, 114, 112, 113).items() if key != "date"}}
        event = {"code": "2330", "date": "2026-10-09", "type": "變更面額", "close_before": 112.21, "reference": 28.05}
        again = self.reconcile("2026-10-09", stock_changes={"2026-10-08": {"close": 112.21}, **late}, capital_events=[event])
        self.assertEqual((again["appended"], again["conflicts"]), ([], []))
        self.assertEqual(wl.read_ledger(self.root)["reconciliations"], before)

    def test_recomputed_different_result_is_refused_and_flagged(self):
        self.day(BASE, "可以留意")
        self.reconcile("2026-10-08")
        before = wl.read_ledger(self.root)["reconciliations"]

        corrected = self.reconcile("2026-10-08", stock_changes={"2026-10-08": {"close": 113}})
        self.assertEqual(corrected["appended"], [])
        self.assertEqual(len(corrected["conflicts"]), 1)
        self.assertEqual((corrected["conflicts"][0]["horizon"], corrected["conflicts"][0]["existing_beat_index"],
                          corrected["conflicts"][0]["recomputed_beat_index"]), (5, False, True))
        ledger = wl.read_ledger(self.root)
        self.assertEqual(ledger["reconciliations"], before)
        self.assertEqual(len(ledger["conflicts"]), 1)

        again = self.reconcile("2026-10-08")
        self.assertEqual((again["appended"], again["conflicts"]), ([], []))

    def test_evening_and_morning_series_are_reconciled_separately(self):
        self.day(BASE, "可以留意")
        self.day(BASE, "可以留意", edition="morning")
        result = self.reconcile("2026-10-08")
        self.assertEqual(sorted((r["edition"], r["horizon"], r["expiry_date"]) for r in result["appended"]),
                         [("evening", 5, "2026-10-08"), ("morning", 5, "2026-10-08")])


class SummaryTests(SeriesTestCase):
    CODES = ("2330", "2317", "2454")

    def summary(self, day="2026-10-29", **options):
        data = market(day, codes=self.CODES, **options)
        wl.reconcile(self.root, data["index_daily"], data["stocks"])
        return wl.summarize(self.root, data["index_daily"], data["stocks"], official_since=BASE)

    def build_ledger(self):
        official = {"official_since": BASE}
        # 2330 晚間正式：09/30 就是「可以留意」→ 轉變日 T1
        self.day(BASE, "可以留意", **official)
        # 2317 晚間正式：09/30 接近、10/01 可以留意 → 轉變日 T2；10/02 漏記
        self.day(BASE, "接近", code="2317", **official)
        self.day("2026-10-01", "可以留意", code="2317", **official)
        self.day("2026-10-02", None, code="2317", **official)
        # 2330 晨間正式：09/30 可以留意 → 轉變日 T3
        self.day(BASE, "可以留意", edition="morning", **official)
        # 2454 晚間試跑：09/30 可以留意 → 轉變日 T4
        self.day(BASE, "可以留意", code="2454")

    def group(self, summary, **key):
        found = [g for g in summary["groups"] if all(g[name] == value for name, value in key.items())]
        self.assertEqual(len(found), 1, key)
        return found[0]

    def assert_group(self, actual, expected):
        for name, value in expected.items():
            if isinstance(value, float):
                self.assertAlmostEqual(actual[name], value, places=9, msg=name)
            else:
                self.assertEqual(actual[name], value, name)

    def test_all_watched_stocks_together_grouped_by_edition_horizon_and_versions(self):
        self.build_ledger()
        summary = self.summary()
        series = dict(run_mode="official", method_version="0.4", input_version=wl.input_version_of())
        # 晚間五日，合計：T1（09/30→10/08）個股 +2% = 指數 +2% → 不算贏；T2（10/01→10/09）+1.8018% > +1.7241% → 贏
        #   隨便挑：T1 鎖定 6/17；T2 的歷史多一根（10/01：個股 111、指數 1160），五日多一筆
        #   第 18 根 108→111（+2.78%）對指數 1155→1160（+0.43%）→ 贏，所以是 7/18。平均 (6/17 + 7/18)/2
        self.assert_group(self.group(summary, scope="all", edition="evening", horizon=5, **series), {
            "code": None, "judgments": 3, "misses": 1, "transitions": 2, "reconciled": 2, "wins": 1, "win_rate": 0.5,
            "random_rate": (6 / 17 + 7 / 18) / 2, "pending": 0, "undeterminable": 0, "stage": "累積中",
        })
        # 晚間二十日，合計：T1（→10/29）+5% > +4% → 贏；T2 要到 10/30，還沒到期。隨便挑只算已對帳的 T1：0/2
        self.assert_group(self.group(summary, scope="all", edition="evening", horizon=20, **series), {
            "transitions": 2, "reconciled": 1, "wins": 1, "win_rate": 1.0, "random_rate": 0.0,
            "pending": 1, "undeterminable": 0, "misses": 1, "stage": "累積中",
        })
        # 晨間自成一組：只有 T3，和 T1 同一天、同樣的結果；沒有漏記
        self.assert_group(self.group(summary, scope="all", edition="morning", horizon=5, **series), {
            "judgments": 1, "misses": 0, "transitions": 1, "reconciled": 1, "wins": 0, "win_rate": 0.0,
            "random_rate": 6 / 17,
        })
        self.assert_group(self.group(summary, scope="all", edition="morning", horizon=20, **series), {
            "reconciled": 1, "wins": 1, "win_rate": 1.0,
        })
        self.assertEqual((summary["as_of"], summary["official_since"], summary["conflicts"]), ("2026-10-29", BASE, 0))

    def test_single_stock_view_counts_only_that_stock(self):
        self.build_ledger()
        summary = self.summary()
        self.assert_group(self.group(summary, scope="stock", code="2330", edition="evening", horizon=5, run_mode="official"), {
            "judgments": 1, "misses": 0, "transitions": 1, "reconciled": 1, "wins": 0, "win_rate": 0.0,
            "random_rate": 6 / 17, "stage": "累積中",
        })
        self.assert_group(self.group(summary, scope="stock", code="2317", edition="evening", horizon=5, run_mode="official"), {
            "judgments": 2, "misses": 1, "transitions": 1, "reconciled": 1, "wins": 1, "win_rate": 1.0,
            "random_rate": 7 / 18,
        })
        # 還沒有已對帳的轉變日：比例是空的，不是零
        self.assert_group(self.group(summary, scope="stock", code="2317", edition="evening", horizon=20, run_mode="official"), {
            "transitions": 1, "reconciled": 0, "wins": 0, "win_rate": None, "random_rate": None, "pending": 1,
        })

    def test_trial_records_are_kept_out_of_official_groups(self):
        self.build_ledger()
        summary = self.summary()
        trial = self.group(summary, scope="all", run_mode="trial", edition="evening", horizon=5)
        self.assertEqual((trial["transitions"], trial["reconciled"], trial["wins"], trial["stage"]), (1, 1, 0, "試跑"))
        self.assertFalse(any(g["code"] == "2454" and g["run_mode"] == "official" for g in summary["groups"]))
        # 正式晚間（合計、2330、2317）＋正式晨間（合計、2330）＋試跑晚間（合計、2454），各有五日與二十日
        self.assertEqual(len(summary["groups"]), 14)

    def test_a_new_method_or_input_version_is_a_separate_group(self):
        self.day(BASE, "可以留意")
        self.day(BASE, "可以留意", code="2317", data={"method_text": METHOD_TEXT.replace("版本：0.4", "版本：0.5")})
        self.day(BASE, "可以留意", code="2454", layers=("technical",))
        summary = self.summary()
        found = sorted((g["method_version"], g["input_version"].split("-v1-")[0], g["transitions"])
                       for g in summary["groups"] if g["scope"] == "all" and g["horizon"] == 5)
        self.assertEqual(found, [("0.4", "technical", 1), ("0.4", "technical+market+news", 1),
                                 ("0.5", "technical+market+news", 1)])

    def test_missing_expiry_data_is_undeterminable_not_pending_and_not_a_loss(self):
        self.day(BASE, "可以留意", official_since=BASE)
        summary = self.summary("2026-10-09", drop=("2026-10-08",))
        five = self.group(summary, scope="all", horizon=5)
        self.assertEqual((five["reconciled"], five["wins"], five["win_rate"], five["pending"], five["undeterminable"]),
                         (0, 0, None, 0, 1))
        twenty = self.group(summary, scope="all", horizon=20)
        self.assertEqual((twenty["pending"], twenty["undeterminable"]), (1, 0))

    def test_a_cell_that_is_due_but_not_reconciled_yet_is_pending_not_undeterminable(self):
        self.day(BASE, "可以留意", official_since=BASE)
        data = market("2026-10-09")
        summary = wl.summarize(self.root, data["index_daily"], data["stocks"])   # 還沒跑對帳
        five = self.group(summary, scope="all", horizon=5)
        self.assertEqual((five["reconciled"], five["pending"], five["undeterminable"]), (0, 1, 0))

    def test_summary_reports_reconciliation_conflicts(self):
        self.day(BASE, "可以留意")
        self.summary("2026-10-08")
        summary = self.summary("2026-10-08", stock_changes={"2026-10-08": {"close": 113}})
        self.assertEqual(summary["conflicts"], 1)
        # 衝突不改成績：帳本裡仍是第一次對的那一筆（剛好相同，不算贏）
        self.assertEqual(self.group(summary, scope="all", horizon=5)["wins"], 0)


class ConclusionStageTests(SeriesTestCase):
    """本人 2026-11-16 定案：正式的那一組，20 日已對帳的轉變日滿 30 次，而且正式起算滿 6 個月，才標「可下結論」。"""
    CODES = tuple(str(code) for code in range(3001, 3031))

    def stages(self, as_of_day=None, *, reconciled_codes=CODES, official_since=BASE):
        data = market("2026-10-29", codes=reconciled_codes)
        wl.reconcile(self.root, data["index_daily"], data["stocks"])
        index = data["index_daily"] + ([{"date": as_of_day, "close": 1200}] if as_of_day else [])
        summary = wl.summarize(self.root, index, data["stocks"], official_since=official_since)
        return {(g["scope"], g["code"], g["horizon"]): g for g in summary["groups"]}

    def test_thirty_reconciled_twenty_day_transitions_and_six_months_are_both_needed(self):
        # 30 檔在 09/30 各寫一份「可以留意」（都是轉變日），正式起算日 09/30 → 滿 6 個月是 2027-03-30
        stocks = [stock_input(code=code, name=code) for code in self.CODES]
        replies = [envelope(answer("可以留意", code=code, **ANSWERS["可以留意"])) for code in self.CODES]
        results = wl.write_judgments(self.root, stocks, edition="evening", model_bin=fake_model(self.bin, replies),
                                     model="test-model", now=clock(EVENING_OK), official_since=BASE)
        self.assertEqual({r["record"]["kind"] for r in results}, {"judgment"})

        # 只對了 29 檔（第 30 檔沒有日 K，無法判定）：時間到了也還是累積中
        short = self.stages("2027-03-30", reconciled_codes=self.CODES[:29])
        self.assertEqual((short[("all", None, 20)]["reconciled"], short[("all", None, 20)]["undeterminable"]), (29, 1))
        self.assertEqual({short[("all", None, h)]["stage"] for h in (5, 20)}, {"累積中"})

        # 30 檔都對了，但時間還沒到
        self.assertEqual(self.stages()[("all", None, 20)]["reconciled"], 30)
        self.assertEqual(self.stages()[("all", None, 20)]["stage"], "累積中")
        self.assertEqual(self.stages("2027-03-29")[("all", None, 20)]["stage"], "累積中")

        # 滿 30 次、滿 6 個月：5 日與 20 日兩格一起標「可下結論」；單一檔各只有 1 次，仍是累積中
        ready = self.stages("2027-03-30")
        self.assertEqual([ready[("all", None, h)]["stage"] for h in (5, 20)], ["可下結論", "可下結論"])
        self.assertEqual({ready[("stock", code, 20)]["stage"] for code in self.CODES}, {"累積中"})

        # 沒有寫下正式起算日，就不會出現結論
        self.assertEqual(self.stages("2027-12-31", official_since=None)[("all", None, 20)]["stage"], "累積中")


if __name__ == "__main__":
    unittest.main()
