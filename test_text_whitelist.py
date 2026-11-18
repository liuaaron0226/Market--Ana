"""理由不得有數字，但允許照抄名稱裡本來就有的數字（本人 2026-11-18 晚「放寬檢查（推薦）」）。

白名單從當份輸入取：候選關卡的名字（整個名字，也認去掉括號日期的那一段），以及方法稿原文裡的用詞。
名稱以外的任何數字（阿拉伯、全形、中文）照擋。例子都是固定的，預期結果手判、理由寫在旁邊。
"""
import unittest

from ledger_core import text_problem

# 那一天（11/09 晨間）候選關卡的名字裡有數字的幾個
NAMES = ["五日線", "十日線", "月線", "年線", "前一交易日高點", "前一交易日低點", "近二十日低點（11/06）", "缺口上緣（11/06）"]
# 方法稿原文的一小段（真的方法稿寫的是「兩個頻道」「第一頻道」，不是「兩頻道」）
METHOD = "把兩個頻道並排；第一頻道的五條規則先看量價。跌破三成就降低持股。"
BLOCKED = "自由文字不得包含數字；價位請用候選編號"


def check(text):
    return text_problem(text, names=NAMES, method_text=METHOD)


class AllowedNamesTests(unittest.TestCase):
    def test_the_refused_answer_of_the_dry_run_passes_when_it_copies_the_method_text(self):
        # 11-18 乾跑被擋的原文（晨間 11/09 的 5 日格），只有「兩頻道」改成方法稿原文的「兩個頻道」
        text = ("急跌後五日線與十日線下彎反壓，外資現貨累計大賣、期貨加空，依統計僅解釋當下而不直接當續跌依據。"
                "同時融資縮減、櫃買弱於加權、資金輪動減速多於領先，與基準日出量收腳的止跌候選訊號彼此矛盾。"
                "依方法稿兩個頻道對反彈需檢查均線壓力與量價配合的原則，未站回十日線前先看弱勢整理，預期收在十日線與近期低點之間。")
        self.assertIsNone(check(text))

    def test_the_word_the_model_made_up_is_still_refused(self):
        # 原文寫「兩頻道」：方法稿裡沒有這個詞（方法稿是「兩個頻道」），不算名稱
        self.assertEqual(check("依方法稿兩頻道對反彈需檢查均線壓力"), BLOCKED)

    def test_whole_candidate_names_with_their_dates_and_the_short_form(self):
        self.assertIsNone(check("守在近二十日低點（11/06）之上"))      # 整個名字照抄，括號裡的日期也算名字的一部分
        self.assertIsNone(check("守在近二十日低點之上"))                # 去掉括號那一段也認
        self.assertIsNone(check("站上前一交易日高點與缺口上緣"))
        self.assertIsNone(check("第一頻道的五條規則提醒先看量價"))      # 方法稿原文的用詞

    def test_numbers_next_to_a_name_are_refused(self):
        self.assertEqual(check("五日線 612 點附近有壓"), BLOCKED)        # 名字旁邊的阿拉伯數字
        self.assertEqual(check("跌破十日線三成"), BLOCKED)              # 「三成」貼在名字後面，不是名字
        self.assertEqual(check("五日線上方一千點"), BLOCKED)
        self.assertEqual(check("十日線（11/06）"), BLOCKED)             # 候選名字裡沒有這個括號日期

    def test_a_line_name_not_in_this_input_is_refused(self):
        self.assertEqual(check("跌破二十日線"), BLOCKED)                # 輸入裡沒有「二十日線」，「十日線」也不能借給它
        self.assertEqual(check("站上六十日線"), BLOCKED)

    def test_without_a_whitelist_the_old_rule_holds(self):
        self.assertEqual(text_problem("跌破五日線"), BLOCKED)
        self.assertIsNone(text_problem("量縮整理等待表態"))

    def test_other_problems_are_unchanged(self):
        self.assertEqual(text_problem(" ", names=NAMES, method_text=METHOD), "文字為空")
        self.assertEqual(text_problem("量" * 161, names=NAMES, method_text=METHOD), "文字過長")


if __name__ == "__main__":
    unittest.main()
