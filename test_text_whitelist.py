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

    def test_a_lone_numeral_in_an_ordinary_word_passes_but_a_quantity_does_not(self):
        # 2026-11-20 改（本人「要，重問一次」那一輪）：正式晚間連兩晚被擋的原文沒有任何數值（兩個頻道、前一日、第二隻腳）。
        # 單獨一個中文數字、後面不是量詞、不貼著名字、不是線名開頭 → 不是數值，放行；原本「兩頻道」這種方法稿沒有的寫法會擋。
        self.assertIsNone(check("依方法稿兩頻道對反彈需檢查均線壓力"))
        self.assertIsNone(check("前一日融資與指數同降，第二隻腳還沒出現"))
        self.assertEqual(check("量能放大兩倍"), BLOCKED)                # 量詞
        self.assertEqual(check("跌破三萬八"), BLOCKED)                  # 連著兩個以上的數字
        self.assertEqual(check("站上三日高點"), BLOCKED)                # 輸入裡沒有的線名

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


# ---- 第二輪（owner 11-19 轉本人「放寬檢查」）：白名單擴大到當份完整提示、常見詞放行、量詞照擋 ----

# 那一天（11/19 晨間）的候選名字與提示裡的一段（「兩線之間」是提示自己的說法）
DAY_NAMES = ["五日線", "十日線", "月線", "年線", "前一交易日高點", "前一交易日低點", "布林下緣"]
PROMPT = ("zone 只能是 above、between、below；照歷史亂猜：兩條線拉得越寬，「兩線之間」在歷史上本來就越常中。"
          "candidates：前一交易日低點、前一交易日高點。新聞：營收年增三成、法人買超五張。" + METHOD)


def check_day(text):
    return text_problem(text, names=DAY_NAMES, method_text=PROMPT)


class WiderWhitelistTests(unittest.TestCase):
    def test_the_refused_sentence_of_the_actions_dry_run_passes(self):
        # Actions 晨間乾跑 44359597670 第一次被擋的原文（owner 11-19 轉來）：沒有任何價位或數值
        text = ("依第一頻道位置與量價一起看，基準日早盤開高後盤中回測前一交易日低點，尾盤收紅守在年線之上，但受制於五日線反壓。"
                "外資連賣、投信轉買與前一交易日融資減少方向分歧，日韓股市收紅，新聞提到輝達財測帶動相關類股走揚僅作背景，"
                "預期收盤在兩線之間。")
        self.assertIsNone(check_day(text))

    def test_common_words_with_a_numeral_pass(self):
        for word in ("一起", "一致", "一些", "一般", "一路", "一度", "一旦", "一再", "單一", "統一", "第一", "第二"):
            self.assertIsNone(check_day(f"量價{word}轉弱"), word)

    def test_quantities_are_still_refused_even_if_the_prompt_has_them(self):
        for text in ("營收年增三成", "法人買超五張", "反彈十點", "站上兩線之間 12 點", "跌破二十日線"):
            self.assertEqual(check_day(text), BLOCKED, text)


if __name__ == "__main__":
    unittest.main()


class FormalNumeralTests(unittest.TestCase):
    """2026-11-20 07:20 正式晨間（run 44419887586）第一次被擋：理由裡的「僅作參考」——「參」是大寫的「三」。
    大寫數字字（參、陸、零、拾、伍…）在一般文章裡幾乎都不是數字（參考、大陸、零組件、收拾、隊伍），只有前後接著
    別的數字或量詞時才是（零點五、參成、陸佰）。"""

    def test_the_refused_sentence_of_the_official_morning_passes(self):
        text = ("採用第二頻道長週期定風險框架並對照第一頻道量價確認：指數位於季線與月線之下、年線之上，布林處於下緣到中線，"
                "中期偏空而長期盤整。歷史相似盤勢為高檔轉弱僅作參考；美國信用壓力低於平均、美元走強，上檔受月線與季線壓制、"
                "下檔有年線與布林下緣緩衝，預期收在月線與布林下緣區間內。")
        self.assertIsNone(text_problem(text, names=["月線", "季線", "年線", "布林下緣"], method_text=METHOD))

    def test_ordinary_words_with_a_formal_numeral_pass(self):
        for text in ("僅作參考", "參照前段走勢", "大陸需求轉弱", "陸續出貨", "零組件拉貨", "零售疲弱", "收拾失土", "萬一跌破"):
            self.assertIsNone(text_problem(text, names=NAMES, method_text=METHOD), text)

    def test_formal_numerals_next_to_numbers_or_measure_words_are_still_refused(self):
        for text in ("零點五", "參成", "陸佰張", "跌了拾點", "二〇二六年"):
            self.assertEqual(text_problem(text, names=NAMES, method_text=METHOD), BLOCKED, text)


# ---- 2026-11-19 晚間正式預測（run 44504695955）兩次都被擋的原文 ----
REFUSED_1119 = (
    "中期偏空、長期盤整，指數跌破季線與年線。本格採第二頻道月線空頭背離之風險框架，反彈遇壓宜調節；第一頻道小時線正背離反彈說法列反對證據。"
    "近期美元強台幣貶伴隨外資賣超，美國信用壓力仍低但微升，新聞提到外資上修台灣經濟預估與中東能源威脅。"
    "上有月線反壓，下測缺口下緣（04/15）支撐，估落於兩線間。",
    "長線盤整而中線偏空，指數處於高檔轉弱且跌破季線與月線。依第二頻道月線背離風險背景與第一頻道看量價確認原則，"
    "美元走強台幣走貶伴隨外資累賣，產業熱度降溫且集中半導體業。上方月線與季線反壓重，未打底前難直接翻多，"
    "但下方缺口下緣（04/15）具支撐且歷史相似期多有反彈，估落於兩線間。",
)
PROMPT_1119 = "zone 只能是 above、between、below；「兩線之間」在歷史上本來就越常中。" + METHOD


class Refused1119Tests(unittest.TestCase):
    def test_a_name_written_without_its_prefix_passes(self):
        # owner 推測候選名是「未回補向上缺口下緣（04/15）」，模型省掉前綴：名字的尾段（含結尾括號日期）放行
        names = ["月線", "季線", "年線", "未回補向上缺口下緣（04/15）"]
        for text in REFUSED_1119:
            self.assertIsNone(text_problem(text, names=names, method_text=PROMPT_1119), text[-30:])

    def test_a_name_that_only_appears_elsewhere_in_the_input_passes(self):
        # 名字不在當天的候選裡、而是在教訓層（過去選過的線）：整份輸入裡的 name 都算名字
        import forecast_ledger as fl
        model_input = {"candidates": [{"id": "ma20", "name": "月線"}],
                       "lessons": {"recent_misses": [{"weak_line": {"id": "gap20260415_lower", "name": "缺口下緣（04/15）"}}]}}
        self.assertEqual(sorted(fl._input_names(model_input)), ["月線", "缺口下緣（04/15）"])

    def test_numbers_beside_a_short_tail_are_still_refused(self):
        names = ["未回補向上缺口下緣（04/15）"]
        for text in ("下緣（04/15）支撐", "缺口下緣 04/15", "缺口下緣（04/16）"):
            self.assertEqual(text_problem(text, names=names, method_text=PROMPT_1119), BLOCKED, text)
