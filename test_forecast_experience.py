"""過去的經驗（E 票）：相似盤勢層與教訓層，走預測帳本的公開介面（規格〈Testing Decisions〉接縫 1）。

給固定的歷史日 K 與固定的對帳紀錄，檢查交給模型的輸入裡出現的統計與教訓是手算得出來的數字。
相似盤勢的定義是本人 2026-11-16 看過白話版後選「照這樣」的那一份（見 forecast_experience 模組說明）。
"""
import json
import unittest

import forecast_ledger as fl
from test_forecast_ledger import (
    LedgerTestCase,
    answer_a,
    answer_oct1,
    at,
    cell,
    clock,
    day_input,
    envelope,
    fake_model,
    through,
    weekdays,
)

PULLBACK, WEAK = "多頭中的回檔", "高檔轉弱"


# ---- 相似盤勢的固定日 K（e）：336 根，編號 0–335；只看收盤，高低各 ±5 ----
#   0–259   收盤 1000 + 10×i（一路創新高；259 收 3590）
#   260–264 收 3400：比近 60 日最高 3590 低 5.29%（≥ 5%），仍在季線之上 → 回檔（第一段起點 260）
#           例：264 的季線 = (3050…3590 共 55 根和 182600 + 5×3400) / 60 = 3326.7 < 3400
#   265–269 收 3500：只低 2.5% → 不歸類
#   270–271 收 3400：又是回檔；270 − 264 = 6 < 20 → 併進第一段（起點仍是 260）
#   272–299 收 3500 + 20×(i−272)（277 收 3600 創新高；299 收 4040）
#   300–302 收 3800：比 4040 低 5.94%；季線 (241…300) = 217160/60 = 3619.3 < 3800 → 回檔（第二段；300 − 271 = 29）
#   303–330 收 3500 − 10×(i−303)：跌破季線。月線下彎＝近 5 根收盤和 < 20 根前那 5 根的和：
#           303：4040+3×3800+3500 = 18940 > 3640…3720 的 18400 → 還沒下彎，不歸類
#           304：3×3800+3500+3490 = 18390 < 3660…3740 的 18500 → 高檔轉弱（起點 304），一路到 330
#   331–334 收 4100、4150、4200、4250（創新高，不歸類）
#   335     收 4000：比 4250 低 5.88%；季線 = 217760/60 = 3629.3 < 4000 → 回檔（現在這一段，不算樣本）
def closes_e():
    out = []
    for i in range(336):
        if i <= 259:
            out.append(1000 + 10 * i)
        elif i <= 264 or 270 <= i <= 271:
            out.append(3400)
        elif i <= 269:
            out.append(3500)
        elif i <= 299:
            out.append(3500 + 20 * (i - 272))
        elif i <= 302:
            out.append(3800)
        elif i <= 330:
            out.append(3500 - 10 * (i - 303))
        elif i <= 334:
            out.append(4100 + 50 * (i - 331))
        else:
            out.append(4000)
    return out


D = weekdays("2025-01-01", 336)


def series_e(last=335):
    return [{"date": D[i], "open": c, "high": c + 5, "low": c - 5, "close": c}
            for i, c in enumerate(closes_e()[:last + 1])]


def answer_e(base, weak="ma60"):
    return {"base_date": base, "regime": PULLBACK,
            "cells": {key: cell("high20", weak, "between") for key in ("1", "5", "20")}}


class SimilarRegimeTestCase(LedgerTestCase):
    def layer(self, last=335, weak="ma60"):
        base = D[last]
        result = self.write(day_input(series_e(last), base_date=base),
                            model=fake_model(self.tmp, [envelope(answer_e(base, weak))]),
                            now=clock(at(base, 17)))
        self.assertEqual(result["record"]["kind"], "forecast", result["record"].get("miss_detail"))
        return result["record"]["input"]["similar_regimes"], result["record"]


class SimilarRegimeTests(SimilarRegimeTestCase):
    def test_today_and_past_episodes_with_their_later_moves(self):
        layer, _ = self.layer()
        self.assertEqual(layer["today"], {"regime": PULLBACK, "since": D[335]})
        # 第一段起點 260 收 3400：1 日 261 收 3400 → 0%；5 日 265 收 3500 → +2.94%；20 日 280 收 3660 → +7.65%
        # 第二段起點 300 收 3800：301 收 3800 → 0%；305 收 3480 → −8.42%；320 收 3330 → −12.37%
        self.assertEqual(layer["classes"][PULLBACK]["episodes"], [
            {"start": D[260], "returns_pct": {"1": 0.0, "5": 2.94, "20": 7.65}},
            {"start": D[300], "returns_pct": {"1": 0.0, "5": -8.42, "20": -12.37}},
        ])
        # 高檔轉弱起點 304 收 3490：305 收 3480 → −0.29%；309 收 3440 → −1.43%；324 收 3290 → −5.73%
        self.assertEqual(layer["classes"][WEAK]["episodes"], [
            {"start": D[304], "returns_pct": {"1": -0.29, "5": -1.43, "20": -5.73}},
        ])

    def test_distribution_and_sample_count_per_horizon_flag_small_samples(self):
        layer, _ = self.layer()
        pullback = layer["classes"][PULLBACK]["horizons"]
        # 五日：+2.94% 與 −8.42%，中位數 = 兩者平均 (0.0294118 − 0.0842105)/2 = −2.74%
        self.assertEqual(pullback["5"], {"samples": 2, "up": 1, "down": 1, "median_pct": -2.74,
                                         "min_pct": -8.42, "max_pct": 2.94})
        self.assertEqual(pullback["1"], {"samples": 2, "up": 0, "down": 0, "median_pct": 0.0,
                                         "min_pct": 0.0, "max_pct": 0.0})
        # 二十日：(0.0764706 − 0.1236842)/2 = −2.36%
        self.assertEqual(pullback["20"]["median_pct"], -2.36)
        self.assertEqual(layer["classes"][WEAK]["horizons"]["20"],
                         {"samples": 1, "up": 0, "down": 1, "median_pct": -5.73,
                          "min_pct": -5.73, "max_pct": -5.73})
        # 少於 10 段標樣本少：兩段、一段都是
        self.assertEqual((layer["classes"][PULLBACK]["low_sample"], layer["classes"][WEAK]["low_sample"]), (True, True))
        self.assertEqual(layer["min_episodes"], 10)

    def test_all_trading_days_are_given_as_the_comparison(self):
        layer, _ = self.layer()
        # 336 根：1 日 335 筆、5 日 331 筆、20 日 316 筆
        self.assertEqual({key: value["samples"] for key, value in layer["all_days"].items()},
                         {"1": 335, "5": 331, "20": 316})
        self.assertEqual(layer["history"], {"from": D[0], "to": D[335], "bars": 336})

    def test_classification_only_uses_what_was_known_that_day(self):
        # 基準日 304：今天剛進入高檔轉弱（現在這一段不算樣本），之前沒有其他高檔轉弱。
        layer, _ = self.layer(304, weak="low60")
        self.assertEqual(layer["today"], {"regime": WEAK, "since": D[304]})
        self.assertEqual(layer["classes"][WEAK]["episodes"], [])
        self.assertEqual(layer["classes"][WEAK]["horizons"]["1"],
                         {"samples": 0, "up": 0, "down": 0, "median_pct": None,
                          "min_pct": None, "max_pct": None})
        # 第二段回檔起點 300：基準日 304 時只知道 1 日（301）；5 日（305）、20 日（320）還沒發生，不算
        self.assertEqual(layer["classes"][PULLBACK]["episodes"][1],
                         {"start": D[300], "returns_pct": {"1": 0.0, "5": None, "20": None}})
        self.assertEqual({key: value["samples"] for key, value in layer["classes"][PULLBACK]["horizons"].items()},
                         {"1": 2, "5": 1, "20": 1})

    def test_ongoing_episode_is_today_not_a_sample(self):
        # 基準日 302：第二段回檔還在進行（從 300 開始），歷史樣本只有第一段
        layer, _ = self.layer(302)
        self.assertEqual(layer["today"], {"regime": PULLBACK, "since": D[300]})
        self.assertEqual([e["start"] for e in layer["classes"][PULLBACK]["episodes"]], [D[260]])

    def test_too_short_history_classifies_nothing_but_the_forecast_is_still_written(self):
        result = self.write()
        layer = result["record"]["input"]["similar_regimes"]
        self.assertEqual(result["record"]["kind"], "forecast")
        self.assertEqual(layer["today"], {"regime": None, "since": None})
        self.assertEqual(layer["classes"][PULLBACK]["episodes"], [])

    def test_notes_say_similar_is_not_a_guarantee_and_name_the_trend_line(self):
        layer, record = self.layer()
        notes = "".join(layer["notes"])
        self.assertIn("不保證", notes)
        self.assertIn("19 次大跌", notes)
        self.assertIn("跌破才算趨勢轉壞", notes)
        # 網站點開要畫統計圖：鎖定的紀錄帶著同一份統計（發布到資料庫的公開欄位）
        self.assertEqual(record["similar_regimes"], layer)
        self.assertIn('"similar_regimes"', fl.d1_sql(self.root, series_e(), published_at="2026-11-16T18:00:00+08:00"))

    def test_similar_and_lesson_layers_name_a_new_input_version(self):
        _, record = self.layer()
        # D 票的海外層與 E 票同時合進來，輸入版本照四層一起算（owner 11-16 整合）。
        self.assertRegex(record["input_version"], r"^base\+overseas\+similar\+lessons-v1-[0-9a-f]{8}$")


# ---- 教訓層：沿用帳本測試的日 K（a）與之後的 FUTURE ----
#   F1 晚間 09/30（answer_a）：1 日線上、5 日兩線之間 → 10/01 收 97.6 線上（中）、10/08 收 93 兩線之間（中）
#   F3 晨間 09/30：1 日改猜線下（轉強 五日線 97.6、轉弱 前一交易日低點 95），盤勢「盤整」→ 1 日沒中；5 日中
#   F2 晚間 10/01（answer_oct1）：1 日猜線下（轉弱 前一交易日低點 96.5）→ 10/02 收 96 < 96.5 線下（中）
def answer_oct6():
    # 基準日 10/06 收 94：五日線 (97+97.6+96+95+94)/5 = 95.92 在上方；前一交易日低點 93.5、近二十日低點 93 在下方
    return {"base_date": "2026-10-06", "regime": "盤整", "cells": {
        "1": cell("ma5", "prev_low", "between"),
        "5": cell("high20", "low20", "between"),
        "20": cell("high20", "low20", "between"),
    }}


class LessonTests(LedgerTestCase):
    def build_ledger(self, reconcile_through="2026-10-08"):
        self.write()
        self.write(edition="morning", session_date="2026-10-01", now=clock(at("2026-10-01", 8)),
                   model=fake_model(self.tmp, [envelope(answer_a(regime="盤整", **{"1": cell("ma5", "prev_low", "below")}))]))
        self.write(day_input(through("2026-10-01"), base_date="2026-10-01"),
                   model=fake_model(self.tmp, [envelope(answer_oct1())]), now=clock(at("2026-10-01", 17)))
        fl.reconcile(self.root, through(reconcile_through))

    def write_oct6(self, edition="evening", **kwargs):
        options = {"now": clock(at("2026-10-06", 17))}
        if edition == "morning":
            options = {"session_date": "2026-10-08", "now": clock(at("2026-10-08", 8))}
        options.update(kwargs)
        return self.write(day_input(through("2026-10-06"), base_date="2026-10-06"), edition=edition,
                          model=fake_model(self.tmp, [envelope(answer_oct6())]), **options)

    def test_empty_ledger_gives_an_empty_lesson_layer_and_the_forecast_is_written(self):
        record = self.write()["record"]
        self.assertEqual(record["kind"], "forecast")
        self.assertEqual(record["input"]["lessons"], {
            "counts": {"1": {"hits": 0, "misses": 0}, "5": {"hits": 0, "misses": 0}, "20": {"hits": 0, "misses": 0}},
            "recent_misses": []})

    def test_counts_and_recent_misses_are_the_reconciled_facts(self):
        self.build_ledger()
        lessons = self.write_oct6()["record"]["input"]["lessons"]
        # 截至 10/06 對完帳的只有三格一日：F1 中、F2 中、F3 沒中；五日（10/08 到期）還不知道
        self.assertEqual(lessons["counts"], {"1": {"hits": 2, "misses": 1}, "5": {"hits": 0, "misses": 0},
                                             "20": {"hits": 0, "misses": 0}})
        self.assertEqual(lessons["recent_misses"], [{
            "base_date": "2026-09-30", "edition": "morning", "horizon": 1, "regime": "盤整", "zone": "below",
            "strong_line": {"id": "ma5", "name": "五日線", "value": 97.6},
            "weak_line": {"id": "prev_low", "name": "前一交易日低點", "value": 95},
            "invalidation": "跌破前低就代表看錯",
            "expiry_date": "2026-10-01", "expiry_close": 97.6, "actual_zone": "above",
        }])

    def test_later_base_date_sees_the_five_day_results(self):
        self.build_ledger()
        rows = through("2026-10-08")
        answer = {"base_date": "2026-10-08", "regime": "盤整",
                  "cells": {key: cell("high20", "low20", "between") for key in ("1", "5", "20")}}
        record = self.write(day_input(rows, base_date="2026-10-08"), model=fake_model(self.tmp, [envelope(answer)]),
                            now=clock(at("2026-10-08", 17)))["record"]
        self.assertEqual(record["input"]["lessons"]["counts"]["5"], {"hits": 2, "misses": 0})

    def test_rerun_after_more_reconciliations_keeps_the_first_record(self):
        self.build_ledger(reconcile_through="2026-10-06")
        first = self.write_oct6()
        self.assertEqual(first["outcome"], "written")
        fl.reconcile(self.root, through("2026-10-08"))  # 五日格 10/08 到期，晚於基準日 10/06
        self.assertEqual(self.write_oct6()["outcome"], "unchanged")

    def test_morning_edition_does_not_see_the_evening_of_the_same_base_date(self):
        self.build_ledger()
        evening = self.write_oct6()["record"]
        fl.reconcile(self.root, through("2026-10-08"))  # 晚間 10/06 的一日格 10/08 到期
        morning = self.write_oct6("morning")["record"]
        self.assertEqual(morning["input"]["lessons"], evening["input"]["lessons"])
        self.assertNotIn(evening["forecast_id"], json.dumps(morning["input"], ensure_ascii=False))

    def test_a_growing_lesson_layer_does_not_change_the_input_version(self):
        empty = self.write()["record"]
        self.build_ledger()
        grown = self.write_oct6()["record"]
        self.assertNotEqual(grown["input"]["lessons"], empty["input"]["lessons"])
        self.assertEqual(grown["input_version"], empty["input_version"])


if __name__ == "__main__":
    unittest.main()
