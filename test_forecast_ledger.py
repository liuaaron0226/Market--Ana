"""預測帳本的公開介面測試（規格〈Testing Decisions〉接縫 1）。

假模型是暫存的命令列程式，經真的子程序邊界呼叫（同 test_ai_digest）。
關卡、比例、落點的預期值都是手算的固定數字，算式寫在各測試的註解裡。
"""
import hashlib
import json
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import forecast_ledger as fl

TAIPEI = timezone(timedelta(hours=8))
METHOD_TEXT = "# 大盤分析方法\n\n版本：0.2，2026-11-15（0.1 為 2026-11-05）。\n\n方法內容。\n"


def weekdays(start: str, count: int) -> list[str]:
    day = date.fromisoformat(start)
    out = []
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


def bar(day, o, h, l, c):
    return {"date": day, "open": o, "high": h, "low": l, "close": c}


# 固定日 K（a）：22 根，基準日是第 22 根 2026-09-30。
#   第 1–15、17–19 根：開 100 高 101 低 99 收 100
#   第 16 根：高 108（近 20 日高點）
#   第 20 根：向下跳空（前一根低 99，這根高 97）開 96 高 97 低 94 收 95
#   第 21 根：開 95 高 98 低 93 收 96（部分回補，缺口剩 98–99；近 20 日低點 93）
#   第 22 根：開 96 高 97.2 低 95 收 97
DATES_A = weekdays("2026-09-01", 22)


def series_a():
    rows = []
    for index, day in enumerate(DATES_A, start=1):
        if index == 16:
            rows.append(bar(day, 100, 108, 99, 100))
        elif index == 20:
            rows.append(bar(day, 96, 97, 94, 95))
        elif index == 21:
            rows.append(bar(day, 95, 98, 93, 96))
        elif index == 22:
            rows.append(bar(day, 96, 97.2, 95, 97))
        else:
            rows.append(bar(day, 100, 101, 99, 100))
    return rows


BASE_A = DATES_A[-1]


def day_input(index_daily=None, *, base_date=BASE_A, short="偏多", medium="偏空", cutoffs=None):
    return {
        "base_date": base_date,
        "index_daily": index_daily if index_daily is not None else series_a(),
        "trend_labels": {"short": short, "medium": medium},
        "chips": {"foreign_net_billion": 12.5, "foreign_futures_net_contracts": -3000},
        "data_cutoffs": cutoffs or {"index": base_date, "institutional": base_date, "futures": base_date},
        "method_text": METHOD_TEXT,
    }


def cell(strong, weak, zone, reason="站回均線之上偏強", invalidation="跌破前低就代表看錯",
         reason_refs=(), invalidation_refs=()):
    return {"strong_line": strong, "weak_line": weak, "zone": zone,
            "reason": reason, "reason_refs": list(reason_refs),
            "invalidation": invalidation, "invalidation_refs": list(invalidation_refs)}


def answer_a(base_date=BASE_A, regime="多頭中的回檔", **overrides):
    cells = {
        "1": cell("ma5", "prev_low", "above", reason_refs=["ma5"], invalidation_refs=["prev_low"]),
        "5": cell("ma20", "low20", "between"),
        "20": cell("high20", "low20", "below"),
    }
    cells.update(overrides)
    return {"base_date": base_date, "regime": regime, "cells": cells}


def envelope(structured):
    return {"status": "SUCCESS", "structured_output": structured}


def fake_model(folder: Path, responses, *, exit_code=0, sleep=0, raw=None, stderr=""):
    """仿 agy 的串流模式：從 stdin 讀一行 {"event":"user","message":{"content":提示}}，
    輸出 init 與 result 兩行事件。依序回傳 responses（重試時取下一個）；每次被呼叫都在 calls.txt 加一行。"""
    script = folder / "fake_model.py"
    state = folder / "calls.txt"
    payloads = [json.dumps({"event": "result", "result": item}, ensure_ascii=False) for item in responses]
    script.write_text(
        "import json,sys,time\n"
        "from pathlib import Path\n"
        "args=sys.argv[1:]\n"
        "assert '--json-schema' in args and args[args.index('--input-format')+1]=='stream-json'\n"
        "assert args[args.index('--output-format')+1]=='stream-json'\n"
        "message=json.loads(sys.stdin.buffer.readline().decode('utf-8'))\n"
        "assert message['event']=='user' and message['message']['content']\n"
        f"state=Path({str(state)!r})\n"
        "calls=len(state.read_text().splitlines()) if state.exists() else 0\n"
        "state.write_text('x\\n'*(calls+1))\n"
        f"time.sleep({sleep})\n"
        f"sys.stderr.write({stderr!r})\n"
        f"raw={raw!r}\n"
        "if raw is not None:\n"
        "    sys.stdout.buffer.write(raw)\n"
        "else:\n"
        f"    payloads={payloads!r}\n"
        "    sys.stdout.buffer.write(b'{\"event\":\"init\"}\\n'+payloads[min(calls,len(payloads)-1)].encode('utf-8')+b'\\n')\n"
        f"raise SystemExit({exit_code})\n",
        encoding="utf-8",
    )
    return script


def calls(folder: Path) -> int:
    state = folder / "calls.txt"
    return len(state.read_text().splitlines()) if state.exists() else 0


def clock(*moments):
    """每呼叫一次往後取一個時間，取完停在最後一個。"""
    values = list(moments)

    def now():
        return values.pop(0) if len(values) > 1 else values[0]
    return now


def at(day: str, hour: int, minute: int = 0):
    return datetime.fromisoformat(day).replace(hour=hour, minute=minute, tzinfo=TAIPEI)


EVENING_OK = at(BASE_A, 17, 0)
AFTER_EVENING_DEADLINE = at("2026-10-01", 0, 1)
STALE_FUTURES = {"index": BASE_A, "institutional": BASE_A, "futures": DATES_A[-2]}


class LedgerTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "ledger"
        self.bin = self.tmp / "bin"
        self.bin.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, data=None, responses=None, *, edition="evening", now=None, official_since=None,
              model=None, root=None, model_name="test-model", **kwargs):
        model = model or fake_model(self.bin, responses or [envelope(answer_a())])
        return fl.write_forecast(
            root or self.root, data or day_input(), edition=edition, model_bin=model, model=model_name,
            now=now or clock(EVENING_OK), official_since=official_since, **kwargs,
        )


class WriteForecastTests(LedgerTestCase):
    def test_valid_answer_locks_a_forecast_with_named_lines_from_candidates(self):
        result = self.write()

        self.assertEqual(result["outcome"], "written")
        record = result["record"]
        self.assertEqual(record["kind"], "forecast")
        self.assertEqual(record["regime"], "多頭中的回檔")
        one = record["cells"]["1"]
        # 五日線 = 第 18–22 根收盤 (100+100+95+96+97)/5 = 97.6；前一交易日低點 = 95
        self.assertEqual(one["strong_line"], {"id": "ma5", "name": "五日線", "value": 97.6})
        self.assertEqual(one["weak_line"], {"id": "prev_low", "name": "前一交易日低點", "value": 95})
        self.assertEqual(one["zone"], "above")
        # 理由與看錯條件：模型的文字不含數字，數值由程式依編號代入
        self.assertEqual(one["reason"], "站回均線之上偏強【資料：五日線 97.6 點】")
        self.assertEqual(one["invalidation"], "跌破前低就代表看錯【資料：前一交易日低點 95 點】")
        self.assertEqual(record["cells"]["20"]["strong_line"]["name"], "近二十日高點（09/22）")
        self.assertEqual(set(record["cells"]), {"1", "5", "20"})


def flat_series(closes, start="2026-09-01"):
    """開高低收都一樣的日 K，方便手算漲跌幅分布。"""
    return [bar(day, c, c, c, c) for day, c in zip(weekdays(start, len(closes)), closes)]


# （b）創新高：最後一根 206.04 = 202×1.02，比之前的最高 204 還高。
#   一日漲跌幅十筆由小到大：−1.96% ×2、−0.99% ×2、+1% ×3、+2% ×3
#     八成位 = 第 ceil(0.8×10)=8 小 = +2% → 206.04 × 1.02 = 210.1608 → 210.16（中位數會是 +1%，可區分）
#   五日漲跌幅六筆：−1.96% ×2、+1% ×3、+2% ×1；八成位 = 第 5 小 = +1% → 206.04 × 1.01 = 208.1004 → 208.1
#   二十日：只有 11 根，沒有樣本，不產生距離關卡。
CLOSES_NEW_HIGH = [200, 204, 200, 204, 200, 202, 200, 202, 200, 202, 206.04]
# （c）創新低：最後一根 194.04 = 198×0.98，比之前的最低 196 還低。
#   一日：−2% ×3、−1% ×3、+1.01% ×2、+2.04% ×2；兩成位 = 第 2 小 = −2% → 194.04 × 0.98 = 190.1592 → 190.16
#   五日：−2% ×1、−1% ×3、+2.04% ×2；兩成位 = 第 2 小 = −1% → 194.04 × 0.99 = 192.0996 → 192.1
CLOSES_NEW_LOW = [200, 196, 200, 196, 200, 198, 200, 198, 200, 198, 194.04]


class CandidateLevelTests(LedgerTestCase):
    def candidates(self, rows):
        data = day_input(rows, base_date=rows[-1]["date"])
        record = self.write(data, now=clock(at(rows[-1]["date"], 17)))["record"]
        return [(c["id"], c["value"], c["side"]) for c in record["input"]["candidates"]]

    def test_lists_named_structural_levels_in_index_points(self):
        # 收盤 97。近二十日 = 第 3–22 根。
        #   月線 = (17×100 + 95 + 96 + 97)/20 = 99.4；十日線 = (7×100 + 95 + 96 + 97)/10 = 98.8
        #   缺口：第 20 根（09/28）高 97 < 第 19 根低 99；第 21 根高 98 部分回補 → 剩 98–99
        #   前一交易日高點 97.2 離收盤 0.21%，小於門檻 0.3%，不列入
        #   布林（20 日、2 倍母體標準差）：中線 99.4；離差平方和 17×0.36 + 4.4² + 3.4² + 2.4² = 42.8，
        #   σ = √(42.8/20) = 1.46287；上緣 99.4 + 2.92575 = 102.33、下緣 96.47
        self.assertEqual(self.candidates(series_a()), [
            ("high20", 108, "above"),
            ("boll_upper", 102.33, "above"),
            ("ma20", 99.4, "above"),
            ("gap20260928_upper", 99, "above"),
            ("ma10", 98.8, "above"),
            ("gap20260928_lower", 98, "above"),
            ("ma5", 97.6, "above"),
            ("boll_lower", 96.47, "below"),
            ("prev_low", 95, "below"),
            ("low20", 93, "below"),
        ])

    def test_bollinger_position_is_described_for_the_model(self):
        record = self.write()["record"]
        names = {c["id"]: c["name"] for c in record["input"]["candidates"]}
        self.assertEqual((names["boll_upper"], names["boll_lower"]), ("布林上緣", "布林下緣"))
        band = record["input"]["bollinger"]
        # 收盤 97 在下緣 96.47 與中線 99.4 之間；寬度 (102.33−96.47)/99.4 = 5.89%；不到 139 根算不出近 120 根的窄度
        self.assertEqual({k: band[k] for k in ("middle", "upper", "lower", "zone", "width_pct", "squeezed")},
                         {"middle": 99.4, "upper": 102.33, "lower": 96.47, "zone": "下緣到中線", "width_pct": 5.89,
                          "squeezed": None})
        self.assertIn("沒有通過", band["note"])

    def test_bollinger_squeeze_means_narrowest_fifth_of_the_last_120_bars(self):
        # 前 120 根在 100 與 110 之間跳（通道很寬），最後 20 根只在 100 與 101 之間跳：最後一根的寬度是近 120 根最窄
        wide_then_narrow = [100 if i % 2 == 0 else 110 for i in range(120)] + [100 if i % 2 == 0 else 101 for i in range(20)]
        # 反過來：前 120 根窄、最後 20 根寬，最後一根是近 120 根最寬，不算變窄
        narrow_then_wide = [100 if i % 2 == 0 else 101 for i in range(120)] + [100 if i % 2 == 0 else 110 for i in range(20)]
        for closes, expected in ((wide_then_narrow, True), (narrow_then_wide, False)):
            rows = flat_series(closes, start="2026-03-02")
            base = rows[-1]["date"]
            data = day_input(rows, base_date=base)
            record = self.write(data, [envelope(answer_a(base_date=base))], now=clock(at(base, 17)),
                                root=self.tmp / f"ledger-{expected}")["record"]
            self.assertIs(record["input"]["bollinger"]["squeezed"], expected)

    def test_new_high_adds_distance_levels_from_historical_gains(self):
        levels = self.candidates(flat_series(CLOSES_NEW_HIGH))
        self.assertEqual([c for c in levels if c[2] == "above"],
                         [("dist_up_1", 210.16, "above"), ("dist_up_5", 208.1, "above")])
        self.assertFalse(any(c[0].startswith("dist_down") for c in levels))

    def test_bollinger_band_does_not_replace_distance_levels_at_a_new_high(self):
        # 22 根：前 21 根在 200 與 204 之間跳，最後一根 205 創新高（布林上緣約 206.4，離收盤夠遠）；布林上緣在收盤上方，但它不是圖上的結構關卡，
        # 上方仍然沒有結構關卡，所以照樣補距離關卡。
        closes = [200 if i % 2 == 0 else 204 for i in range(21)] + [205]
        ids = [c[0] for c in self.candidates(flat_series(closes)) if c[2] == "above"]
        self.assertIn("boll_upper", ids)
        self.assertIn("dist_up_1", ids)

    def test_new_low_adds_distance_levels_from_historical_declines(self):
        levels = self.candidates(flat_series(CLOSES_NEW_LOW))
        self.assertEqual([c for c in levels if c[2] == "below"],
                         [("dist_down_5", 192.1, "below"), ("dist_down_1", 190.16, "below")])

    def test_level_dates_from_an_earlier_year_carry_the_year(self):
        # 同形狀的日 K 改成 2025-12-08 起 22 個平日：第 16 根是 2025-12-29，基準日是 2026-01-06
        shifted = [dict(row, date=day) for row, day in zip(series_a(), weekdays("2025-12-08", 22))]
        base = shifted[-1]["date"]
        record = self.write(day_input(shifted, base_date=base),
                            [envelope(answer_a(base_date=base))], now=clock(at(base, 17)))["record"]
        names = {c["id"]: c["name"] for c in record["input"]["candidates"]}
        self.assertEqual(names["high20"], "近二十日高點（2025/12/29）")
        self.assertEqual(names["low20"], "近二十日低點（01/05）")

    def test_distance_level_can_only_be_used_in_its_own_horizon(self):
        rows = flat_series(CLOSES_NEW_HIGH)
        base = rows[-1]["date"]
        bad = answer_a(base_date=base, **{
            "1": cell("dist_up_5", "ma5", "between"),
            "5": cell("dist_up_5", "ma5", "between"),
            "20": cell("dist_up_5", "ma5", "between"),
        })
        result = self.write(day_input(rows, base_date=base), [envelope(bad)], now=clock(at(base, 17)))
        self.assertEqual(result["record"]["kind"], "miss")
        self.assertEqual(result["record"]["miss_reason"], "answer_invalid")


class AnswerValidationTests(LedgerTestCase):
    def assert_miss(self, result, reason, model_calls):
        self.assertEqual(result["outcome"], "written")
        self.assertEqual(result["record"]["kind"], "miss")
        self.assertEqual(result["record"]["miss_reason"], reason)
        self.assertNotIn("cells", result["record"])
        self.assertEqual(calls(self.bin), model_calls)

    def test_invalid_answer_is_retried_once_with_the_same_input(self):
        bad = answer_a(**{"1": cell("no_such_level", "prev_low", "above")})
        result = self.write(responses=[envelope(bad), envelope(answer_a())])
        self.assertEqual(result["record"]["kind"], "forecast")
        self.assertEqual(calls(self.bin), 2)
        self.assertEqual(len(result["record"]["attempts"]), 1)

    def test_answers_that_stay_invalid_after_one_retry_become_a_miss(self):
        cases = {
            "不存在的關卡": answer_a(**{"5": cell("ma20", "no_such_level", "between")}),
            "轉強線放在下方": answer_a(**{"1": cell("low20", "prev_low", "above")}),
            "轉弱線放在上方": answer_a(**{"20": cell("high20", "ma5", "below")}),
            "少一格": {"base_date": BASE_A, "regime": "盤整",
                     "cells": {k: v for k, v in answer_a()["cells"].items() if k != "20"}},
            "落點不在清單": answer_a(**{"5": cell("ma20", "low20", "sideways")}),
            "盤勢類型不在清單": answer_a(regime="大多頭"),
            "理由含阿拉伯數字": answer_a(**{"1": cell("ma5", "prev_low", "above", reason="站上97點")}),
            "看錯條件含中文數字": answer_a(**{"1": cell("ma5", "prev_low", "above", invalidation="連跌三天")}),
            "基準日不符": answer_a(base_date="2026-09-29"),
        }
        for name, bad in cases.items():
            with self.subTest(name):
                (self.bin / "calls.txt").unlink(missing_ok=True)
                root = self.root / name
                model = fake_model(self.bin, [envelope(bad)])
                result = fl.write_forecast(root, day_input(), edition="evening", model_bin=model,
                                           model="test-model", now=clock(EVENING_OK))
                self.assert_miss(result, "answer_invalid", 2)

    def test_model_failure_timeout_and_garbled_output_become_a_miss_without_retry(self):
        cases = [
            ("model_failed", dict(exit_code=1)),
            ("model_failed", dict(raw=json.dumps({"event": "result", "result": {"status": "ERROR"}}).encode())),
            ("output_garbled", dict(raw=b"\xff\xfe not json")),
        ]
        for index, (reason, options) in enumerate(cases):
            with self.subTest(reason=reason, options=options):
                (self.bin / "calls.txt").unlink(missing_ok=True)
                model = fake_model(self.bin, [envelope(answer_a())], **options)
                result = self.write(model=model, root=self.root / f"case{index}")
                self.assert_miss(result, reason, 1)

    def test_model_failure_keeps_the_tail_of_its_error_output_as_the_reason(self):
        model = fake_model(self.bin, [envelope(answer_a())], exit_code=3, stderr="noise\nquota exceeded for this week\n")
        result = self.write(model=model)
        self.assert_miss(result, "model_failed", 1)
        self.assertIn("結束碼 3", result["record"]["miss_detail"])
        self.assertIn("quota exceeded for this week", result["record"]["miss_detail"])

    def test_a_prompt_longer_than_a_windows_command_line_still_reaches_the_model(self):
        # Windows 單一指令列上限約 32,767 字元；提示改從 stdin 交給模型，長度不再受限。
        data = day_input()
        data["method_text"] = METHOD_TEXT + "方法細節。" * 8000
        result = self.write(data)
        self.assertEqual(result["record"]["kind"], "forecast")

    def test_model_timeout_becomes_a_miss(self):
        model = fake_model(self.bin, [envelope(answer_a())], sleep=5)
        result = self.write(model=model, timeout_seconds=1)
        self.assert_miss(result, "model_timeout", 1)

    def test_input_not_ready_before_the_deadline_writes_nothing(self):
        result = self.write(day_input(cutoffs=STALE_FUTURES))
        self.assertEqual(result["outcome"], "not_ready")
        self.assertIn("futures", result["detail"])
        self.assertIsNone(result["record"])
        self.assertEqual(calls(self.bin), 0)
        self.assertEqual(fl.read_ledger(self.root)["forecasts"], [])

        later = self.write(day_input(), now=clock(at(BASE_A, 20)))
        self.assertEqual(later["record"]["kind"], "forecast")

    def test_input_still_not_ready_after_the_deadline_is_a_miss(self):
        result = self.write(day_input(cutoffs=STALE_FUTURES), now=clock(at("2026-10-01", 0, 1)))
        self.assert_miss(result, "input_not_ready", 0)
        self.assertIn("futures", result["record"]["miss_detail"])

        result = self.write(day_input(series_a()[:-1]), edition="morning", session_date="2026-10-01",
                            now=clock(at("2026-10-01", 8, 50)), root=self.root / "morning")
        self.assert_miss(result, "input_not_ready", 0)

    def test_evening_edition_past_the_end_of_base_date_is_a_miss(self):
        result = self.write(now=clock(at("2026-10-01", 0, 1)))
        self.assert_miss(result, "deadline_passed", 0)

    def test_answer_arriving_after_the_deadline_is_a_miss(self):
        result = self.write(now=clock(at(BASE_A, 23, 50), at("2026-10-01", 0, 5)))
        self.assert_miss(result, "deadline_passed", 1)
        self.assertEqual(result["record"]["locked_at"], "2026-10-01T00:05:00+08:00")

    def test_morning_edition_must_lock_fifteen_minutes_before_the_open(self):
        ok = self.write(edition="morning", session_date="2026-10-01", now=clock(at("2026-10-01", 8, 44)))
        self.assertEqual(ok["record"]["kind"], "forecast")
        late = fl.write_forecast(self.root / "late", day_input(), edition="morning", session_date="2026-10-01",
                                 model_bin=fake_model(self.bin, [envelope(answer_a())]), model="m",
                                 now=clock(at("2026-10-01", 8, 45)))
        self.assertEqual(late["record"]["miss_reason"], "deadline_passed")


OVERSEAS = {
    "overseas": {"treasury": {"date": "2026-09-29", "yield_2y": 4.1}, "not_provided": ["日本日經 225"]},
    "overseas_cutoffs": {"treasury": "2026-09-29"},
    "overseas_status": {"japan_nikkei": {"name": "日本日經 225", "status": "ok", "latest_date": BASE_A,
                                         "in_prompt": False}},
    "missing_inputs": [{"key": "korea_kospi", "name": "韓國綜合", "status": "stale", "reason": "休市"}],
    "record_only": {"overseas": {"markets": {"japan_nikkei": {"latest_close": 1030.2}}}},
    "sources": [{"name": "eodhd-N225.INDX", "fetched_at": "2026-09-30T09:00:00Z", "sha256": "a" * 64}],
}


class OverseasLayerTests(LedgerTestCase):
    def test_overseas_layer_reaches_the_model_and_the_record_keeps_status_sources_and_withheld_numbers(self):
        record = self.write({**day_input(), **OVERSEAS})["record"]
        self.assertEqual(record["kind"], "forecast")
        self.assertEqual(record["input"]["overseas"], OVERSEAS["overseas"])
        self.assertEqual(record["data_cutoffs"]["treasury"], "2026-09-29")
        self.assertEqual(record["input"]["data_cutoffs"]["treasury"], "2026-09-29")
        # 條款不允許交給模型的數字只在紀錄裡，不在輸入裡
        self.assertNotIn("1030.2", json.dumps(record["input"], ensure_ascii=False))
        self.assertEqual(record["record_only"], OVERSEAS["record_only"])
        self.assertEqual(record["overseas_status"], OVERSEAS["overseas_status"])
        self.assertEqual(record["missing_inputs"], OVERSEAS["missing_inputs"])
        self.assertEqual(record["sources"], OVERSEAS["sources"])

    def test_rerun_with_a_new_fetch_time_but_the_same_numbers_is_unchanged(self):
        self.write({**day_input(), **OVERSEAS})
        again = {**day_input(), **OVERSEAS,
                 "sources": [{"name": "eodhd-N225.INDX", "fetched_at": "2026-09-30T09:30:00Z", "sha256": "b" * 64}]}
        result = self.write(again)
        self.assertEqual(result["outcome"], "unchanged")
        self.assertEqual(result["record"]["sources"], OVERSEAS["sources"])

    def test_a_missing_overseas_item_does_not_block_the_forecast(self):
        data = {**day_input(), "overseas": None, "missing_inputs": [{"key": "us_sp500", "status": "missing"}]}
        record = self.write(data)["record"]
        self.assertEqual(record["kind"], "forecast")
        self.assertEqual(record["missing_inputs"], [{"key": "us_sp500", "status": "missing"}])


class RecordMetadataTests(LedgerTestCase):
    def test_forecast_records_identity_versions_hashes_and_cutoffs(self):
        record = self.write()["record"]
        self.assertEqual(record["base_date"], "2026-09-30")
        self.assertEqual(record["edition"], "evening")
        self.assertEqual(record["method_version"], "0.2")
        self.assertRegex(record["input_version"], r"^base\+overseas\+foreign_flow\+wyckoff_lpsy\+similar\+lessons-v1-[0-9a-f]{8}$")
        self.assertEqual(record["model"], "test-model")
        self.assertRegex(record["input_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(record["locked_at"], "2026-09-30T17:00:00+08:00")
        self.assertEqual(record["data_cutoffs"], {"index": BASE_A, "institutional": BASE_A, "futures": BASE_A})
        self.assertEqual(record["run_mode"], "trial")
        self.assertEqual(record["input"]["method_text"], METHOD_TEXT)
        self.assertEqual(record["input"]["chips"], {"foreign_net_billion": 12.5, "foreign_futures_net_contracts": -3000})
        # 算候選關卡與照歷史亂猜用的完整日 K：範圍與根數一起鎖定
        history = record["input"]["history"]
        self.assertEqual((history["from"], history["to"], history["bars"]), ("2026-09-01", "2026-09-30", 22))
        self.assertRegex(history["sha256"], r"^[0-9a-f]{64}$")

    def test_forecast_on_or_after_the_official_start_is_official(self):
        record = self.write(official_since=BASE_A)["record"]
        self.assertEqual(record["run_mode"], "official")
        other = fl.write_forecast(self.root / "x", day_input(), edition="evening", model="m",
                                  model_bin=fake_model(self.bin, [envelope(answer_a())]),
                                  now=clock(EVENING_OK), official_since="2026-10-01")
        self.assertEqual(other["record"]["run_mode"], "trial")

    def test_method_version_comes_from_the_method_text(self):
        self.assertEqual(fl.method_version_of(METHOD_TEXT), "0.2")
        self.assertEqual(fl.method_version_of("# 標題\n版本：0.1，2026-11-05。\n"), "0.1")
        with self.assertRaises(ValueError):
            fl.method_version_of("# 沒有版本行\n")


class ModelSeriesTests(LedgerTestCase):
    """本人 2026-11-17：預測換 Argon。換模型＝新的輸入版本，舊模型的紀錄另成一個系列、不混算。"""
    FLASH_ERA = "base+overseas+foreign_flow+similar+lessons-v1-1fbcaa45"  # 換模型前（Flash 試跑與 40 份重播）

    def test_the_input_version_follows_the_model(self):
        self.assertEqual(fl.INPUT_VERSION, fl.input_version(fl.MODEL))
        self.assertNotEqual(fl.input_version("gemini-4-argon"), fl.input_version("gemini-3.8-flash"))
        self.assertNotIn(self.FLASH_ERA, {fl.input_version("gemini-4-argon"), fl.input_version("gemini-3.8-flash")})
        self.assertRegex(fl.input_version("gemini-4-argon"), r"^base\+overseas\+foreign_flow\+wyckoff_lpsy\+similar\+lessons-v1-[0-9a-f]{8}$")

    def test_a_record_carries_the_input_version_of_the_model_that_wrote_it(self):
        record = self.write(model_name="gemini-4-argon")["record"]
        self.assertEqual(record["model"], "gemini-4-argon")
        self.assertEqual(record["input_version"], fl.input_version("gemini-4-argon"))
        self.assertEqual(record["input"]["input_version"], record["input_version"])
        self.assertTrue(record["forecast_id"].endswith("|" + fl.input_version("gemini-4-argon")))

    def test_the_same_day_by_another_model_is_a_separate_record_not_a_conflict(self):
        flash = self.write(model_name="gemini-3.8-flash")
        argon = self.write(model_name="gemini-4-argon")
        self.assertEqual((flash["outcome"], argon["outcome"]), ("written", "written"))
        self.assertNotEqual(flash["record"]["forecast_id"], argon["record"]["forecast_id"])
        self.assertEqual(len(fl.read_ledger(self.root)["forecasts"]), 2)

    def test_summary_keeps_each_model_in_its_own_group_and_names_the_model(self):
        self.write(model_name="gemini-3.8-flash")
        self.write(model_name="gemini-4-argon")
        fl.reconcile(self.root, through("2026-10-08"))
        groups = [g for g in fl.summarize(self.root, through("2026-10-08"))["groups"] if g["horizon"] == 1]
        self.assertEqual(sorted((g["model"], g["input_version"], g["reconciled"]) for g in groups), [
            ("gemini-3.8-flash", fl.input_version("gemini-3.8-flash"), 1),
            ("gemini-4-argon", fl.input_version("gemini-4-argon"), 1),
        ])


class LockingTests(LedgerTestCase):
    def forecast_file(self):
        files = list((self.root / "forecasts").glob("*.json"))
        self.assertEqual(len(files), 1)
        return files[0]

    def test_rerun_with_the_same_input_keeps_the_first_record_and_does_not_call_the_model(self):
        first = self.write()["record"]
        before = self.forecast_file().read_bytes()
        other_answer = answer_a(regime="盤整")
        again = fl.write_forecast(self.root, day_input(), edition="evening", model="test-model",
                                  model_bin=fake_model(self.tmp, [envelope(other_answer)]),
                                  now=clock(at(BASE_A, 18)))
        self.assertEqual(again["outcome"], "unchanged")
        self.assertEqual(again["record"], first)
        self.assertEqual(calls(self.tmp), 0)
        self.assertEqual(self.forecast_file().read_bytes(), before)

    def test_rerun_with_different_input_is_refused(self):
        self.write()
        before = self.forecast_file().read_bytes()
        changed = day_input()
        changed["chips"] = {"foreign_net_billion": -1}
        again = self.write(changed)
        self.assertEqual(again["outcome"], "conflict")
        self.assertEqual(self.forecast_file().read_bytes(), before)

    def test_a_miss_cannot_later_be_replaced_by_a_forecast(self):
        self.write(day_input(cutoffs=STALE_FUTURES), now=clock(AFTER_EVENING_DEADLINE))
        again = self.write(day_input(), now=clock(at(BASE_A, 20)))
        self.assertEqual(again["outcome"], "conflict")
        self.assertEqual(again["record"]["kind"], "miss")
        self.assertEqual(fl.read_ledger(self.root)["forecasts"][0]["kind"], "miss")

    def test_evening_and_morning_editions_are_stored_separately(self):
        self.write()
        self.write(edition="morning", session_date="2026-10-01", now=clock(at("2026-10-01", 8)))
        editions = [r["edition"] for r in fl.read_ledger(self.root)["forecasts"]]
        self.assertEqual(sorted(editions), ["evening", "morning"])

    def test_rerun_after_an_old_bar_was_corrected_is_refused(self):
        # 前面多接 48 根平盤，共 70 根：第 1 根不在交給模型的最近 60 根裡，也不進任何均線或高低點，
        # 只用在照歷史亂猜。它被更正時，重跑仍要被擋下。
        older = [bar(day, 100, 101, 99, 100) for day in weekdays("2026-06-24", 48)]
        self.assertLess(older[-1]["date"], DATES_A[0])
        self.write(day_input(older + series_a()))
        corrected = older + series_a()
        corrected[0] = dict(corrected[0], close=100.5)
        again = self.write(day_input(corrected), now=clock(at(BASE_A, 18)))
        self.assertEqual(again["outcome"], "conflict")

    def test_tampered_record_is_detected_on_read(self):
        self.write()
        path = self.forecast_file()
        record = json.loads(path.read_text(encoding="utf-8"))
        record["cells"]["1"]["zone"] = "below"
        path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(fl.LedgerIntegrityError):
            fl.read_ledger(self.root)

    def test_tampered_stored_input_is_detected_even_if_the_seal_is_recomputed(self):
        self.write()
        path = self.forecast_file()
        record = json.loads(path.read_text(encoding="utf-8"))
        record["input"]["chips"] = {"foreign_net_billion": 999}
        body = {k: v for k, v in record.items() if k != "record_sha256"}
        record["record_sha256"] = hashlib.sha256(json.dumps(
            body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(fl.LedgerIntegrityError):
            fl.read_ledger(self.root)


class BaselineAtWriteTests(LedgerTestCase):
    def test_historical_shares_and_fixed_rule_are_locked_with_the_forecast(self):
        cells = self.write()["record"]["cells"]
        # 一日：轉強線 97.6（+0.6186%）、轉弱線 95（−2.0619%）。日 K（a）的一日漲跌幅 21 筆：
        #   0% ×18、−5%（100→95）、+1.0526%（95→96）、+1.0417%（96→97）→ 線上 2、中間 18、線下 1
        self.assertEqual(cells["1"]["baseline"],
                         {"above": 2, "between": 18, "below": 1, "samples": 21,
                          "from": "2026-09-01", "to": "2026-09-30"})
        # 五日：99.4（+2.474%）／93（−4.124%）。五日漲跌幅 17 筆：0% ×14、−5%、−4%、−3% → 線下只有 −5%
        self.assertEqual(cells["5"]["baseline"],
                         {"above": 0, "between": 16, "below": 1, "samples": 17,
                          "from": "2026-09-01", "to": "2026-09-30"})
        # 二十日：108／93。兩筆 −4%、−3%，都在中間
        self.assertEqual(cells["20"]["baseline"],
                         {"above": 0, "between": 2, "below": 0, "samples": 2,
                          "from": "2026-09-01", "to": "2026-09-30"})
        # 固定規則：短期偏多 → 一日、五日選線上；中期偏空 → 二十日選線下
        self.assertEqual([cells[k]["fixed_rule_zone"] for k in ("1", "5", "20")], ["above", "above", "below"])

    def test_neutral_label_picks_between(self):
        cells = self.write(day_input(short="盤整", medium="偏多"))["record"]["cells"]
        self.assertEqual([cells[k]["fixed_rule_zone"] for k in ("1", "5", "20")], ["between", "between", "above"])


# 基準日 09/30 之後的日 K。10/07 休市（沒有資料），所以基準日之後第 5 個交易日是 10/08。
FUTURE = [
    bar("2026-10-01", 97, 98, 96.5, 97.6),    # 剛好等於一日轉強線 97.6
    bar("2026-10-02", 97, 97.5, 95.5, 96),
    bar("2026-10-05", 96, 96, 94.5, 95),
    bar("2026-10-06", 95, 95, 93.5, 94),
    bar("2026-10-08", 94, 94, 92.5, 93),      # 剛好等於五日轉弱線 93
]


def through(day, *, changes=None):
    rows = series_a() + [dict(row) for row in FUTURE if row["date"] <= day]
    for row in rows:
        row.update((changes or {}).get(row["date"], {}))
    return rows


def summary_of(result):
    return sorted((r["horizon"], r["expiry_date"], r["expiry_close"], r["actual_zone"]) for r in result)


class ReconcileTests(LedgerTestCase):
    def test_expired_cells_are_judged_on_trading_days_with_boundaries_on_the_lines(self):
        record = self.write()["record"]
        result = fl.reconcile(self.root, through("2026-10-08"))

        # 一日：10/01 收 97.6 = 轉強線 → 線上；五日：10/08 收 93 = 轉弱線 → 兩線之間；二十日還沒到期
        self.assertEqual(summary_of(result["appended"]),
                         [(1, "2026-10-01", 97.6, "above"), (5, "2026-10-08", 93, "between")])
        self.assertEqual(result["pending"], [{"forecast_id": record["forecast_id"], "horizon": 20}])
        self.assertEqual(result["conflicts"], [])
        self.assertEqual(len(fl.read_ledger(self.root)["reconciliations"]), 2)

    def test_close_below_the_weak_line_is_below(self):
        self.write()
        result = fl.reconcile(self.root, through("2026-10-08", changes={"2026-10-08": {"close": 92.99}}))
        self.assertIn((5, "2026-10-08", 92.99, "below"), summary_of(result["appended"]))

    def test_close_just_under_the_strong_line_is_between(self):
        self.write()
        result = fl.reconcile(self.root, through("2026-10-01", changes={"2026-10-01": {"close": 97.59}}))
        self.assertEqual(summary_of(result["appended"]), [(1, "2026-10-01", 97.59, "between")])

    def test_not_yet_expired_is_pending_and_late_data_is_reconciled_later(self):
        self.write()
        first = fl.reconcile(self.root, through("2026-09-30"))
        self.assertEqual(first["appended"], [])
        self.assertEqual(sorted(p["horizon"] for p in first["pending"]), [1, 5, 20])

        second = fl.reconcile(self.root, through("2026-10-02"))
        self.assertEqual(summary_of(second["appended"]), [(1, "2026-10-01", 97.6, "above")])

        third = fl.reconcile(self.root, through("2026-10-08"))
        self.assertEqual(summary_of(third["appended"]), [(5, "2026-10-08", 93, "between")])
        self.assertEqual(len(fl.read_ledger(self.root)["reconciliations"]), 2)

    def test_missing_close_on_the_expiry_day_is_undeterminable_not_wrong(self):
        record = self.write()["record"]
        result = fl.reconcile(self.root, through("2026-10-02", changes={"2026-10-01": {"close": None}}))
        self.assertEqual(result["appended"], [])
        self.assertEqual(result["undeterminable"], [{"forecast_id": record["forecast_id"], "horizon": 1,
                                                     "expiry_date": "2026-10-01"}])
        fixed = fl.reconcile(self.root, through("2026-10-02"))
        self.assertEqual(summary_of(fixed["appended"]), [(1, "2026-10-01", 97.6, "above")])

    def test_recomputed_different_result_is_refused_and_flagged(self):
        self.write()
        fl.reconcile(self.root, through("2026-10-01"))
        before = fl.read_ledger(self.root)["reconciliations"]

        corrected = fl.reconcile(self.root, through("2026-10-01", changes={"2026-10-01": {"close": 97.0}}))
        self.assertEqual(corrected["appended"], [])
        self.assertEqual(len(corrected["conflicts"]), 1)
        self.assertEqual(corrected["conflicts"][0]["horizon"], 1)
        ledger = fl.read_ledger(self.root)
        self.assertEqual(ledger["reconciliations"], before)
        self.assertEqual(len(ledger["conflicts"]), 1)

        again = fl.reconcile(self.root, through("2026-10-01"))
        self.assertEqual((again["appended"], again["conflicts"]), ([], []))

    def test_evening_and_morning_of_the_same_base_date_share_expiry_dates(self):
        self.write()
        self.write(edition="morning", session_date="2026-10-01", now=clock(at("2026-10-01", 8)))
        result = fl.reconcile(self.root, through("2026-10-08"))
        by_edition = {}
        for row in result["appended"]:
            by_edition.setdefault(row["edition"], []).append((row["horizon"], row["expiry_date"]))
        self.assertEqual(sorted(by_edition["evening"]), [(1, "2026-10-01"), (5, "2026-10-08")])
        self.assertEqual(sorted(by_edition["morning"]), [(1, "2026-10-01"), (5, "2026-10-08")])

    def test_misses_have_nothing_to_reconcile(self):
        self.write(day_input(cutoffs=STALE_FUTURES), now=clock(AFTER_EVENING_DEADLINE))
        result = fl.reconcile(self.root, through("2026-10-08"))
        self.assertEqual((result["appended"], result["pending"]), ([], []))


def answer_oct1():
    # 基準日 10/01（收 97.6）的候選：月線 (16×100+95+96+97+97.6)/20 = 99.28、前一交易日低點 96.5、
    # 近二十日高點 108、近二十日低點 93。
    return {"base_date": "2026-10-01", "regime": "盤整", "cells": {
        "1": cell("ma20", "prev_low", "below"),
        "5": cell("high20", "low20", "between"),
        "20": cell("high20", "low20", "between"),
    }}


class SummaryTests(LedgerTestCase):
    def build_ledger(self):
        # F1 晚間 09/30 正式（多頭中的回檔）；F3 晨間 09/30 正式（盤整）；F2 晚間 10/01 正式；
        # M1 晚間 10/02 正式漏記；T1 晚間 09/30 試跑（方法 0.1，另一個系列）
        self.write(official_since=BASE_A)
        self.write(edition="morning", session_date="2026-10-01", now=clock(at("2026-10-01", 8)),
                   official_since=BASE_A,
                   model=fake_model(self.tmp, [envelope(answer_a(regime="盤整"))]))
        self.write(day_input(through("2026-10-01"), base_date="2026-10-01", short="偏空"),
                   model=fake_model(self.tmp, [envelope(answer_oct1())]),
                   now=clock(at("2026-10-01", 17)), official_since=BASE_A)
        stale = {"index": "2026-10-02", "institutional": "2026-10-02", "futures": "2026-10-01"}
        self.write(day_input(through("2026-10-02"), base_date="2026-10-02", cutoffs=stale),
                   now=clock(at("2026-10-03", 0, 1)), official_since=BASE_A)
        trial = day_input()
        trial["method_text"] = "版本：0.1\n"
        self.write(trial)
        fl.reconcile(self.root, through("2026-10-08"))

    def group(self, summary, **key):
        found = [g for g in summary["groups"]
                 if all(g[name] == value for name, value in key.items())]
        self.assertEqual(len(found), 1, key)
        return found[0]

    def assert_group(self, actual, expected):
        for name, value in expected.items():
            if isinstance(value, dict):
                self.assert_group(actual[name], value)
            elif isinstance(value, float):
                self.assertAlmostEqual(actual[name], value, places=9, msg=name)
            else:
                self.assertEqual(actual[name], value, name)

    def test_groups_by_edition_horizon_method_and_input_version(self):
        self.build_ledger()
        summary = fl.summarize(self.root, through("2026-10-08"))
        series = dict(run_mode="official", method_version="0.2", input_version=fl.input_version("test-model"))

        # 晚間一日：F1 10/01 收 97.6 ≥ 97.6 線上（猜線上，中）；F2 10/02 收 96 < 96.5 線下（猜線下，中）
        #   照歷史亂猜：F1 線上 2/21；F2 線下 1/22（一日漲跌幅 22 筆只有 −5% 低於 −1.127%）
        #   固定規則：F1 短期偏多→線上（中）；F2 短期偏空→線下（中）
        self.assert_group(self.group(summary, edition="evening", horizon=1, **series), {
            "reconciled": 2, "hit_rate": 1.0, "baseline_rate": (2 / 21 + 1 / 22) / 2, "fixed_rule_rate": 1.0,
            "pending": 0, "misses": 1,
            "pullback": {"reconciled": 1, "hit_rate": 1.0, "baseline_rate": 2 / 21, "fixed_rule_rate": 1.0},
        })
        # 晚間五日：F1 10/08 收 93 = 轉弱線 → 兩線之間（猜中間，中；固定規則猜線上，沒中）；F2 還沒到期
        self.assert_group(self.group(summary, edition="evening", horizon=5, **series), {
            "reconciled": 1, "hit_rate": 1.0, "baseline_rate": 16 / 17, "fixed_rule_rate": 0.0,
            "pending": 1, "misses": 1,
            "pullback": {"reconciled": 1, "hit_rate": 1.0, "baseline_rate": 16 / 17, "fixed_rule_rate": 0.0},
        })
        self.assert_group(self.group(summary, edition="evening", horizon=20, **series), {
            "reconciled": 0, "hit_rate": None, "baseline_rate": None, "fixed_rule_rate": None,
            "pending": 2, "misses": 1, "pullback": {"reconciled": 0, "hit_rate": None},
        })
        # 晨間一日：F3 和 F1 同一組線、同一個到期日；盤勢類型是盤整，不進回檔統計；晨間沒有漏記
        self.assert_group(self.group(summary, edition="morning", horizon=1, **series), {
            "reconciled": 1, "hit_rate": 1.0, "baseline_rate": 2 / 21, "fixed_rule_rate": 1.0,
            "pending": 0, "misses": 0, "pullback": {"reconciled": 0, "hit_rate": None},
        })

    def test_trial_records_are_kept_out_of_official_groups(self):
        self.build_ledger()
        summary = fl.summarize(self.root, through("2026-10-08"))
        trial = self.group(summary, run_mode="trial", edition="evening", horizon=1, method_version="0.1")
        self.assertEqual((trial["reconciled"], trial["hit_rate"], trial["misses"]), (1, 1.0, 0))
        self.assertFalse(any(g["method_version"] == "0.1" and g["run_mode"] == "official" for g in summary["groups"]))
        self.assertEqual(len(summary["groups"]), 9)  # 正式晚間、正式晨間、試跑晚間 × 三個期間

    def test_wrong_calls_lower_the_hit_rate(self):
        self.write(official_since=BASE_A)
        # 10/01 收 95.5：介於 95 與 97.6 之間，猜線上 → 沒中；固定規則也猜線上 → 沒中
        fl.reconcile(self.root, through("2026-10-01", changes={"2026-10-01": {"close": 95.5}}))
        group = self.group(fl.summarize(self.root, through("2026-10-08")), edition="evening", horizon=1, run_mode="official")
        self.assertEqual((group["reconciled"], group["hit_rate"], group["fixed_rule_rate"]), (1, 0.0, 0.0))
        self.assertAlmostEqual(group["baseline_rate"], 2 / 21)

    def test_missing_expiry_data_is_undeterminable_not_pending(self):
        self.write(official_since=BASE_A)
        data = through("2026-10-02", changes={"2026-10-01": {"close": None}})
        fl.reconcile(self.root, data)
        summary = fl.summarize(self.root, data)
        one = self.group(summary, edition="evening", horizon=1, run_mode="official")
        self.assertEqual((one["reconciled"], one["pending"], one["undeterminable"]), (0, 0, 1))
        five = self.group(summary, edition="evening", horizon=5, run_mode="official")
        self.assertEqual((five["pending"], five["undeterminable"]), (1, 0))

    def test_summary_reports_reconciliation_conflicts(self):
        self.write(official_since=BASE_A)
        fl.reconcile(self.root, through("2026-10-01"))
        fl.reconcile(self.root, through("2026-10-01", changes={"2026-10-01": {"close": 97.0}}))
        self.assertEqual(fl.summarize(self.root, through("2026-10-08"))["conflicts"], 1)


class WyckoffLayerTests(LedgerTestCase):
    """H 票：今天加權指數是否處在最後供應點，由程式從同一份日 K 算好交給模型（本人 2026-11-18 定案）。"""

    def test_the_reading_and_fixed_history_go_into_the_model_input(self):
        import test_wyckoff_lpsy as lpsy
        # 前 80 天整理（高 105 低 95）；第 80 天收 90 跌破 95；第 81 天收 92、最高 94、量 70 ÷ 100 ＝ 0.7 → 是
        series = lpsy.basic_break().add(close=92, high=94, low=91, amount=70)
        rows = series.index_rows()
        base = rows[-1]["date"]
        record = self.write(day_input(rows, base_date=base), now=clock(at(base, 17, 0)))["record"]
        layer = record["input"]["wyckoff_lpsy"]
        self.assertEqual(layer["reading"], {"status": "yes", "date": base, "break_date": series.date(80),
                                            "range_low": 95.0, "volume_ratio": 0.7})
        self.assertEqual((layer["history"]["count"], layer["history"]["t"]), (14, -1.2))
        self.assertEqual(record["input"]["parameters"]["wyckoff_lpsy_layer"], layer["version"])

    def test_a_short_history_is_undeterminable_and_the_forecast_is_still_written(self):
        record = self.write()["record"]  # 固定日 K（a）只有 22 根
        self.assertEqual(record["kind"], "forecast")
        self.assertEqual(record["input"]["wyckoff_lpsy"]["reading"],
                         {"status": "undeterminable", "date": BASE_A, "reason": "history_short"})

    def test_the_layer_is_a_new_input_version_and_the_prompt_says_how_to_use_it(self):
        self.assertIn("wyckoff_lpsy", fl.PARAMETERS["layers"])
        self.assertNotEqual(fl.input_version("gemini-4-argon"), "base+overseas+foreign_flow+similar+lessons-v1-1b88159c")
        rules = "".join(fl._PROMPT_RULES)
        for words in ("wyckoff_lpsy", "不顯著", "避開記號", "不是放空訊號", "不能單獨"):
            self.assertIn(words, rules)


class PromptRulesTests(LedgerTestCase):
    def test_model_is_told_to_use_only_given_facts_and_that_it_will_be_compared_with_history(self):
        # 本人 2026-11-15 看過乾跑後選「先改提示再乾跑」：理由只能用輸入裡的資料；事先說明會和照歷史亂猜並排比。
        seen = self.tmp / "prompt.txt"
        script = self.bin / "recording_model.py"
        payload = json.dumps({"event": "result", "result": envelope(answer_a())}, ensure_ascii=False)
        script.write_text(
            "import json,sys\nfrom pathlib import Path\n"
            "prompt=json.loads(sys.stdin.buffer.readline().decode('utf-8'))['message']['content']\n"
            f"Path({str(seen)!r}).write_text(prompt, encoding='utf-8')\n"
            f"sys.stdout.buffer.write({payload.encode('utf-8')!r})\n", encoding="utf-8")
        self.write(model=script)
        prompt = seen.read_text(encoding="utf-8")
        self.assertIn("只能引用 INPUT_JSON 裡有的資料", prompt)
        self.assertIn("照歷史亂猜", prompt)


class VerdictStatusTests(LedgerTestCase):
    def test_groups_say_accumulating_until_each_horizon_has_run_long_enough(self):
        self.write(official_since=BASE_A)
        trial = day_input()
        trial["method_text"] = METHOD_TEXT.replace("版本：0.2", "版本：0.1")
        self.write(trial)

        def statuses(as_of_rows):
            summary = fl.summarize(self.root, as_of_rows, official_since=BASE_A)
            return {(g["run_mode"], g["horizon"]): g["verdict"] for g in summary["groups"]}

        # 正式起算 09/30：一日滿三個月是 12/30、五日滿六個月是 2027-03-30、二十日滿一年是 2027-09-30
        early = statuses(through("2026-10-08"))
        self.assertEqual(early[("official", 1)], "累積中")
        self.assertEqual(early[("trial", 1)], "試跑")

        just_before = statuses(through("2026-10-08") + [bar("2026-12-29", 93, 94, 92, 93)])
        self.assertEqual(just_before[("official", 1)], "累積中")
        on_the_day = statuses(through("2026-10-08") + [bar("2026-12-30", 93, 94, 92, 93)])
        self.assertEqual([on_the_day[("official", h)] for h in (1, 5, 20)], ["可下結論", "累積中", "累積中"])
        later = statuses(through("2026-10-08") + [bar("2027-09-30", 93, 94, 92, 93)])
        self.assertEqual([later[("official", h)] for h in (1, 5, 20)], ["可下結論", "可下結論", "可下結論"])
        self.assertEqual(later[("trial", 20)], "試跑")

    def test_a_new_method_version_after_the_official_start_counts_its_time_from_its_own_first_record(self):
        # 正式起算 09/30；10/01 起換成方法 0.3（新的一組）。12/30 時 0.2 的一日已滿三個月，0.3 的要到 2027-01-01
        self.write(official_since=BASE_A)
        newer = day_input(through("2026-10-01"), base_date="2026-10-01")
        newer["method_text"] = METHOD_TEXT.replace("版本：0.2", "版本：0.3")
        self.write(newer, model=fake_model(self.tmp, [envelope(answer_oct1())]),
                   now=clock(at("2026-10-01", 17)), official_since=BASE_A)

        def one_day(rows):
            summary = fl.summarize(self.root, rows, official_since=BASE_A)
            return {g["method_version"]: g["verdict"] for g in summary["groups"] if g["horizon"] == 1}

        self.assertEqual(one_day(through("2026-10-08") + [bar("2026-12-30", 93, 94, 92, 93)]),
                         {"0.2": "可下結論", "0.3": "累積中"})
        self.assertEqual(one_day(through("2026-10-08") + [bar("2027-01-01", 93, 94, 92, 93)]),
                         {"0.2": "可下結論", "0.3": "可下結論"})

    def test_without_an_official_start_every_official_group_is_accumulating(self):
        self.write(official_since=BASE_A)
        # 一年多以後也一樣：沒有寫下正式起算日，就不會出現結論
        summary = fl.summarize(self.root, through("2026-10-08") + [bar("2027-12-31", 93, 94, 92, 93)])
        self.assertEqual({g["verdict"] for g in summary["groups"]}, {"累積中"})


REPLAY = {"model_knowledge_cutoff": "2026-03", "fetched_at": "2026-11-17T12:00:00+08:00"}


class ReplayRecordTests(LedgerTestCase):
    """本人 2026-11-17「要，重播當參考」：重播紀錄和正式、試跑分開存，標明是歷史重播，不計入正式結論。"""

    def test_a_replayed_forecast_is_marked_replay_and_cannot_be_mistaken_for_an_official_one(self):
        result = self.write(replay=REPLAY, official_since=BASE_A)
        record = result["record"]
        self.assertEqual((result["outcome"], record["kind"], record["run_mode"]), ("written", "forecast", "replay"))
        self.assertTrue(record["forecast_id"].startswith("replay|"))
        self.assertEqual(record["replay"], REPLAY)

    def test_a_failed_model_call_is_not_locked_so_the_day_can_be_replayed_later(self):
        # 正式帳本把模型失敗鎖成漏記；重播時的失敗多半是額度用完，鎖起來會讓那天永遠補不回來
        result = self.write(replay=REPLAY, model=fake_model(self.bin, [envelope(answer_a())], exit_code=1))
        self.assertEqual(result["outcome"], "model_failed")
        self.assertIsNone(result["record"])
        self.assertEqual(fl.read_ledger(self.root)["forecasts"], [])
        again = self.write(replay=REPLAY, model=fake_model(self.tmp, [envelope(answer_a())]))
        self.assertEqual(again["record"]["kind"], "forecast")

    def test_an_invalid_answer_twice_is_still_locked_as_a_miss_like_the_real_ledger(self):
        bad = answer_a(**{"1": cell("prev_low", "ma5", "above")})  # 兩條線放反
        result = self.write(replay=REPLAY, responses=[envelope(bad), envelope(bad)])
        self.assertEqual((result["record"]["kind"], result["record"]["miss_reason"], result["record"]["run_mode"]),
                         ("miss", "answer_invalid", "replay"))

    def test_replay_groups_are_labelled_as_reference_and_never_judged(self):
        self.write(replay=REPLAY)
        rows = through("2026-10-08") + [bar("2027-12-31", 93, 94, 92, 93)]
        fl.reconcile(self.root, rows)
        groups = fl.summarize(self.root, rows, official_since=BASE_A)["groups"]
        self.assertEqual({(g["run_mode"], g["verdict"]) for g in groups}, {("replay", "歷史重播（參考）")})
        self.assertEqual({g["judgement"] is None for g in groups}, {True})


class JudgementTests(LedgerTestCase):
    """F 票：「明顯比照歷史亂猜準」的判定，在第一筆正式紀錄之前寫定（白話版在 ANALYSIS_PLAN）。

    z ＝（猜中次數 − Σp）÷ √（Σp(1−p) × 期間天數）；p 是寫下當時鎖定的那一區歷史比例。
    漏記算猜錯（p 用同組有對帳那些格的平均）；漏記超過一成不下結論。門檻 z ≥ 2.326（單邊 1%）。
    """

    def test_sixty_one_day_calls_at_even_odds_with_forty_hits_is_clearly_better(self):
        # Σp = 30、Σp(1−p) = 15；z = 10 ÷ √15 = 2.582 ≥ 2.326
        result = fl.judge([(index < 40, 0.5) for index in range(60)], horizon=1)
        self.assertEqual(result["conclusion"], "明顯比亂猜準")
        self.assertAlmostEqual(result["z"], 10 / 15 ** 0.5, places=3)
        self.assertEqual((result["hits"], result["expected_hits"], result["effective_samples"]), (40, 30.0, 60.0))

    def test_overlapping_five_day_records_count_as_a_fifth_of_the_samples(self):
        # 同樣的成績放在五日：樣本數 60 ÷ 5 = 12，z = 2.582 ÷ √5 = 1.155，不夠明顯
        result = fl.judge([(index < 40, 0.5) for index in range(60)], horizon=5)
        self.assertEqual(result["conclusion"], "沒有明顯差別")
        self.assertAlmostEqual(result["z"], 10 / 75 ** 0.5, places=3)
        self.assertEqual(result["effective_samples"], 12.0)

    def test_equally_bad_results_are_called_clearly_worse(self):
        result = fl.judge([(index < 20, 0.5) for index in range(60)], horizon=1)
        self.assertEqual(result["conclusion"], "明顯比亂猜差")

    def test_misses_count_as_wrong_calls_at_the_groups_average_odds(self):
        # 60 格猜中 40（p 都是 0.5）＋ 6 次漏記（9.1%，未超過一成）：66 格、Σp = 33、Σp(1−p) = 16.5
        # z = (40 − 33) ÷ √16.5 = 1.723 → 不夠明顯（沒有漏記時是 2.582）
        result = fl.judge([(index < 40, 0.5) for index in range(60)], horizon=1, misses=6)
        self.assertEqual(result["conclusion"], "沒有明顯差別")
        self.assertAlmostEqual(result["z"], 7 / 16.5 ** 0.5, places=3)
        self.assertEqual(result["misses"], 6)

    def test_more_than_one_in_ten_missed_means_no_conclusion(self):
        # 7 ÷ 67 = 10.4%
        result = fl.judge([(True, 0.5) for _ in range(60)], horizon=1, misses=7)
        self.assertEqual(result["conclusion"], "漏記太多")
        self.assertIsNone(result["z"])

    def test_summary_judges_only_groups_that_have_run_long_enough(self):
        SummaryTests.build_ledger(self)
        rows = through("2026-10-08") + [bar("2026-12-30", 93, 94, 92, 93)]
        summary = fl.summarize(self.root, rows, official_since=BASE_A)

        def group(edition, horizon):
            return next(g for g in summary["groups"] if g["run_mode"] == "official" and g["edition"] == edition
                        and g["horizon"] == horizon)

        # 晚間一日已滿三個月：F1、F2 都中，但 10/02 那份漏記（一日到期日 10/05 已過）→ 1 ÷ 3 超過一成
        self.assertEqual(group("evening", 1)["judgement"]["conclusion"], "漏記太多")
        self.assertEqual(group("evening", 1)["judgement"]["misses"], 1)
        # 晨間一日：只有一格、猜中、p = 2/21 → z = (1 − 2/21) ÷ √(2/21 × 19/21) = 3.08
        self.assertEqual(group("morning", 1)["judgement"]["conclusion"], "明顯比亂猜準")
        # 五日、二十日還在累積：沒有判定
        self.assertIsNone(group("evening", 5)["judgement"])
        trial = next(g for g in summary["groups"] if g["run_mode"] == "trial")
        self.assertIsNone(trial["judgement"])


MIGRATION = Path(__file__).resolve().parent / "cloud" / "migrations" / "0010_forecast_ledger.sql"


class PublishToDatabaseTests(LedgerTestCase):
    def database(self):
        connection = sqlite3.connect(":memory:")
        connection.executescript(MIGRATION.read_text(encoding="utf-8"))
        return connection

    def published(self):
        self.write(official_since=BASE_A)
        stale = {"index": "2026-10-01", "institutional": "2026-10-01", "futures": "2026-09-30"}
        self.write(day_input(through("2026-10-01"), base_date="2026-10-01", cutoffs=stale),
                   now=clock(at("2026-10-02", 0, 1)), official_since=BASE_A)
        fl.reconcile(self.root, through("2026-10-08"))
        return fl.d1_sql(self.root, through("2026-10-08"), published_at="2026-10-08T18:00:00+08:00")

    def test_publishing_twice_keeps_one_row_per_record_with_the_original_hash(self):
        sql = self.published()
        self.assertNotRegex(sql, r"(?im)^\s*(BEGIN|COMMIT)\s*;")  # 部署工具自己包交易
        db = self.database()
        db.executescript(sql)
        first = db.execute("SELECT forecast_id, kind, record_sha256, record_json FROM forecast_records ORDER BY forecast_id").fetchall()
        db.executescript(sql)
        again = db.execute("SELECT forecast_id, kind, record_sha256, record_json FROM forecast_records ORDER BY forecast_id").fetchall()
        self.assertEqual(first, again)
        self.assertEqual([row[1] for row in first], ["forecast", "miss"])
        ledger = {r["forecast_id"]: r for r in fl.read_ledger(self.root)["forecasts"]}
        for forecast_id, _, sha, _ in first:
            self.assertEqual(sha, ledger[forecast_id]["record_sha256"])
        self.assertEqual(db.execute("SELECT COUNT(*) FROM forecast_reconciliations").fetchone(), (2,))
        self.assertEqual(db.execute("SELECT COUNT(*) FROM forecast_summaries").fetchone(), (1,))

    def test_published_rows_carry_display_fields_but_not_the_method_text_or_model_input(self):
        db = self.database()
        db.executescript(self.published())
        record = json.loads(db.execute("SELECT record_json FROM forecast_records WHERE kind = 'forecast'").fetchone()[0])
        self.assertNotIn("input", record)
        self.assertNotIn("方法內容", json.dumps(record, ensure_ascii=False))
        self.assertEqual(record["cells"]["1"]["strong_line"], {"id": "ma5", "name": "五日線", "value": 97.6})
        self.assertEqual(record["cells"]["1"]["reason"], "站回均線之上偏強【資料：五日線 97.6 點】")
        self.assertEqual((record["run_mode"], record["regime"], record["data_cutoffs"]["futures"]),
                         ("official", "多頭中的回檔", BASE_A))
        summary = json.loads(db.execute("SELECT summary_json FROM forecast_summaries").fetchone()[0])
        self.assertEqual(summary["as_of"], "2026-10-08")

    def test_database_refuses_updates_and_deletes(self):
        db = self.database()
        db.executescript(self.published())
        for statement in ("UPDATE forecast_records SET kind = 'miss'",
                          "DELETE FROM forecast_records",
                          "UPDATE forecast_reconciliations SET actual_zone = 'below'",
                          "DELETE FROM forecast_reconciliations",
                          "UPDATE forecast_summaries SET summary_json = '{}'",
                          "DELETE FROM forecast_summaries"):
            with self.subTest(statement):
                with self.assertRaises(sqlite3.DatabaseError):
                    db.execute(statement)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM forecast_records").fetchone(), (2,))

    def test_database_refuses_replacing_a_row_with_different_content(self):
        db = self.database()
        db.executescript(self.published())
        forecast_id, sha = db.execute("SELECT forecast_id, record_sha256 FROM forecast_records WHERE kind = 'forecast'").fetchone()
        replace = ("INSERT OR REPLACE INTO forecast_records (forecast_id, base_date, edition, method_version, input_version, "
                   "run_mode, kind, record_json, record_sha256, published_at) VALUES (?, ?, 'evening', '0.2', 'v', 'trial', "
                   "'forecast', '{}', ?, 'now')")
        with self.assertRaises(sqlite3.DatabaseError):
            db.execute(replace, (forecast_id, BASE_A, "0" * 64))
        with self.assertRaises(sqlite3.DatabaseError):
            db.execute("INSERT OR REPLACE INTO forecast_reconciliations (forecast_id, horizon, expiry_date, actual_zone, "
                       "record_json, record_sha256, published_at) VALUES (?, 1, '2026-10-01', 'below', '{}', ?, 'now')",
                       (forecast_id, "0" * 64))
        self.assertEqual(db.execute("SELECT record_sha256 FROM forecast_records WHERE forecast_id = ?", (forecast_id,)).fetchone(), (sha,))
        self.assertEqual(db.execute("SELECT actual_zone FROM forecast_reconciliations WHERE horizon = 1").fetchone(), ("above",))

    def test_publishing_a_different_record_under_the_same_id_fails_loudly(self):
        # 兩本帳各有一筆同識別（同基準日、版本、方法、輸入版本）但內容不同的預測
        self.write()
        other = self.tmp / "other-ledger"
        self.write(responses=[envelope(answer_a(regime="盤整"))], root=other,
                   model=fake_model(self.tmp, [envelope(answer_a(regime="盤整"))]))
        db = self.database()
        db.executescript(fl.d1_sql(self.root, series_a(), published_at="2026-09-30T18:00:00+08:00"))
        with self.assertRaises(sqlite3.DatabaseError):
            db.executescript(fl.d1_sql(other, series_a(), published_at="2026-09-30T19:00:00+08:00"))
        record = json.loads(db.execute("SELECT record_json FROM forecast_records").fetchone()[0])
        self.assertEqual(record["regime"], "多頭中的回檔")

    def test_text_with_quotes_survives_publishing(self):
        quoted = answer_a(**{"1": cell("ma5", "prev_low", "above", reason="站回'均線'之上")})
        self.write(responses=[envelope(quoted)])
        db = self.database()
        db.executescript(fl.d1_sql(self.root, series_a(), published_at="2026-09-30T18:00:00+08:00"))
        record = json.loads(db.execute("SELECT record_json FROM forecast_records").fetchone()[0])
        self.assertEqual(record["cells"]["1"]["reason"], "站回'均線'之上")


REPLAY_MIGRATION = MIGRATION.parent / "0023_forecast_replay.sql"


class PublishReplayTests(LedgerTestCase):
    def database(self):
        connection = sqlite3.connect(":memory:")
        connection.executescript(MIGRATION.read_text(encoding="utf-8"))
        connection.executescript(REPLAY_MIGRATION.read_text(encoding="utf-8"))
        return connection

    def test_replay_rows_go_only_to_the_replay_tables(self):
        self.write(replay=REPLAY)
        fl.reconcile(self.root, through("2026-10-08"))
        db = self.database()
        db.executescript(fl.d1_sql(self.root, through("2026-10-08"), published_at="2026-11-17T12:00:00+08:00",
                                   replay=True))
        db.executescript(fl.d1_sql(self.root, through("2026-10-08"), published_at="2026-11-17T13:00:00+08:00",
                                   replay=True))  # 再發布一次：不多一筆
        self.assertEqual(db.execute("SELECT run_mode, kind FROM forecast_replay_records").fetchall(),
                         [("replay", "forecast")])
        self.assertEqual(db.execute("SELECT COUNT(*) FROM forecast_replay_reconciliations").fetchone(), (2,))
        summary = json.loads(db.execute("SELECT summary_json FROM forecast_replay_summaries").fetchone()[0])
        self.assertEqual({g["verdict"] for g in summary["groups"]}, {"歷史重播（參考）"})
        for table in ("forecast_records", "forecast_reconciliations", "forecast_summaries"):
            self.assertEqual(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone(), (0,), table)

    def test_the_replay_tables_refuse_official_rows_updates_and_deletes(self):
        self.write(replay=REPLAY)
        db = self.database()
        db.executescript(fl.d1_sql(self.root, series_a(), published_at="2026-11-17T12:00:00+08:00", replay=True))
        with self.assertRaises(sqlite3.DatabaseError):
            db.execute("INSERT INTO forecast_replay_records (forecast_id, base_date, edition, method_version, "
                       "input_version, run_mode, kind, record_json, record_sha256, published_at) VALUES "
                       "('x', '2026-09-30', 'evening', '0.2', 'v', 'official', 'forecast', '{}', ?, 'now')", ("0" * 64,))
        for statement in ("UPDATE forecast_replay_records SET kind = 'miss'", "DELETE FROM forecast_replay_records",
                          "DELETE FROM forecast_replay_summaries"):
            with self.subTest(statement), self.assertRaises(sqlite3.DatabaseError):
                db.execute(statement)


if __name__ == "__main__":
    unittest.main()
