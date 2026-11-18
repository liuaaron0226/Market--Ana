"""基金當成一般個股 06 票：個股頁「配息」分類的資料（分析契約 kind fund 的基金專屬欄位 ``distributions``）。

規格 .scratch/fund-as-stock/spec.md 第 28 條與 06 票：最近一次除息日與每單位配息（美元）、各年配息次數與合計、配息來源的本金註記，
由真配息原件（seeds/funds/BLKTENAU 的集保「基金資訊觀測站」配息分頁回補原件）算出，和集保配息表一致。
預期值是照集保配息表逐筆抄下來手算的（不是用程式同一個算法重算）：
- 2022：12-30 一次 0.057，本金 100%。
- 2023：12 次，合計 0.057＋0.0615×3＋0.0635×3＋0.098×3＋0.0655×2＝0.857；10-31 那次本金 78.56%，其他 11 次本金 100%。
- 2024：12 次，0.0655＋0.111×3＋0.1175×3＋0.123×4＋0.129＝1.372；全部 0% 本金（100% 可分配淨利益）。
- 2025：12 次，0.129×2＋0.1235×4＋0.13×3＋0.137×3＝1.553；12-31 那次集保還沒公布來源（N/A），其他 0% 本金。
- 2026（到 10-30）：10 次，0.144×3＋0.1375×3＋0.153×3＋0.162＝1.4655；10-30 那次來源未公布。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from functools import partial
from pathlib import Path

import fund_compare
import fund_stock
from cloud import build_stock_pack as build

DATA_DATE = "2026-11-18"
EMPTY_RAW = Path(tempfile.mkdtemp(prefix="fund-raw-empty-"))
# 只讀回補原件（不讀本機或 R2 的每日抽取檔），預期值才固定。
REAL = partial(fund_compare.load_history, raw_dir=EMPTY_RAW)


def fund_payload(as_of: str = DATA_DATE, loader=REAL) -> dict:
    _, analyses, _ = fund_stock.fund_rows(as_of, loader=loader)
    return analyses[0]["payload"]


class FundDistributionsFromRealDataTests(unittest.TestCase):
    def test_latest_distribution_is_the_last_ex_date_with_amount_in_usd(self) -> None:
        dist = fund_payload()["distributions"]
        self.assertEqual(dist["currency"], "USD")
        self.assertEqual(dist["count"], 47)
        self.assertEqual(dist["latest"], {"ex_date": "2026-10-30", "record_date": "2026-10-29", "pay_date": "2026-11-06",
                                          "amount": 0.162, "capital_pct": None, "income_pct": None})

    def test_each_year_count_total_and_source_facts_match_the_tdcc_table(self) -> None:
        years = fund_payload()["distributions"]["years"]
        self.assertEqual([(y["year"], y["count"], y["total"]) for y in years],
                         [("2026", 10, 1.4655), ("2025", 12, 1.553), ("2024", 12, 1.372), ("2023", 12, 0.857), ("2022", 1, 0.057)],
                         "新的年份在前")
        self.assertEqual([(y["year"], y["all_capital"], y["part_capital"], y["income_only"], y["unknown"]) for y in years],
                         [("2026", 0, 0, 9, 1), ("2025", 0, 0, 11, 1), ("2024", 0, 0, 12, 0), ("2023", 11, 1, 0, 0), ("2022", 1, 0, 0, 0)])
        self.assertEqual([y["source"] for y in years], [
            "9 次來自可分配淨利益、1 次來源未公布",
            "11 次來自可分配淨利益、1 次來源未公布",
            "全部來自可分配淨利益",
            "11 次全部來自本金、1 次部分來自本金",
            "全部來自本金",
        ])

    def test_capital_note_states_the_facts_in_one_sentence(self) -> None:
        self.assertEqual(fund_payload()["distributions"]["capital_note"],
                         "這檔配息可能來自本金：2022 年 1 次配息全部來自本金；2023 年 12 次配息中 11 次全部來自本金；"
                         "2024 年 1 月起公布的配息都來自可分配淨利益。")

    def test_distributions_after_the_data_date_are_not_used(self) -> None:
        dist = fund_payload("2026-10-29")["distributions"]
        self.assertEqual((dist["count"], dist["latest"]["ex_date"], dist["latest"]["amount"]), (46, "2026-09-30", 0.153))
        self.assertEqual((dist["years"][0]["year"], dist["years"][0]["count"], dist["years"][0]["total"]), ("2026", 9, 1.3035))

    def test_payload_with_distributions_still_passes_the_build_check(self) -> None:
        build._check_payload("BLKTENAU", fund_payload())


class FundDistributionsFromDailyFetchTests(unittest.TestCase):
    """每日抓取（集保開放資料 3-5 的抽取檔，fund_compare._extract_dist 存的格式）接在回補原件後面：
    新的除息出現在最近一次，原本來源未公布（N/A）的那次被補上比例。"""

    def test_new_open_data_distribution_shows_up_and_fills_the_missing_source(self) -> None:
        def row(ex: str, record: str, pay: str, amount: str, income: str, capital: str) -> dict:
            return {"﻿基金代碼": "BLKTENAU", "資料日期": "2026/12/01", "除息日": ex, "配息基準日": record, "配息發放日": pay,
                    "單位數": amount, "可分配淨利益/配息": income, "本金/配息": capital}

        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "BLKTENAU-dist-2026-12-01.json").write_text(json.dumps({"rows": [
                row("2026/10/30", "2026/10/29", "2026/11/06", "0.162", "100.00%", "0.00%"),
                row("2026/11/28", "2026/11/27", "2026/12/05", "0.162", "N/A", "N/A"),
            ]}, ensure_ascii=False), encoding="utf-8")
            loader = partial(fund_compare.load_history, raw_dir=Path(tmp))
            dist = fund_payload("2026-12-01", loader=loader)["distributions"]
        self.assertEqual((dist["count"], dist["latest"]["ex_date"], dist["latest"]["pay_date"]), (48, "2026-11-28", "2026-12-05"))
        self.assertEqual((dist["years"][0]["count"], dist["years"][0]["total"], dist["years"][0]["source"]),
                         (11, 1.6275, "10 次來自可分配淨利益、1 次來源未公布"))


class FundDistributionsEdgeTests(unittest.TestCase):
    @staticmethod
    def loader(dists: list[dict]):
        navs = [(f"2026-10-{day:02d}", 18.0) for day in range(1, 29)]

        def load(code: str, *, as_of: str) -> dict:
            return {"navs": navs, "distributions": dists, "fx": [], "issues": []}
        return load

    def test_fund_without_any_distribution_says_so_instead_of_inventing_one(self) -> None:
        dist = fund_payload(loader=self.loader([]))["distributions"]
        self.assertEqual((dist["count"], dist["latest"], dist["years"], dist["capital_note"]), (0, None, [], None))

    def test_only_unpublished_sources_write_not_published(self) -> None:
        row = {"ex_date": "2026-10-15", "record_date": "2026-10-14", "pay_date": "2026-10-22", "amount": 0.1,
               "income_pct": None, "capital_pct": None}
        dist = fund_payload(loader=self.loader([row]))["distributions"]
        self.assertEqual(dist["years"][0]["source"], "來源未公布")
        self.assertEqual(dist["capital_note"], "這檔配息可能來自本金。")


if __name__ == "__main__":
    unittest.main()
