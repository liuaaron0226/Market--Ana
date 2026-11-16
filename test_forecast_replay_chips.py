"""歷史重播補抓的法人與期貨（本人 2026-11-17 選 A2：從 04-01 起，先補資料包 120 天以前的法人與期貨）。

假網路依網址回應、記下每次請求；預期值都是手寫的官方格式樣本。
"""
import json
import tempfile
import unittest
from pathlib import Path

import forecast_replay_chips as chips
from test_forecast_run import analysis, weekdays


def bfi82u(foreign_net, trust_net=100, self_net=10, hedge_net=-5):
    def row(name, net):
        return [name, f"{abs(net) * 3:,}", f"{abs(net) * 3 - net:,}", f"{net:,}"]
    return json.dumps({"stat": "OK", "data": [
        row("自營商(自行買賣)", self_net), row("自營商(避險)", hedge_net), row("投信", trust_net),
        row("外資及陸資(不含外資自營商)", foreign_net), row("外資自營商", 0), row("合計", 0)]},
        ensure_ascii=False).encode("utf-8")


HEADER = ("日期,商品名稱,身份別,多方交易口數,多方交易契約金額(千元),空方交易口數,空方交易契約金額(千元),"
          "多空交易口數淨額,多空交易契約金額淨額(千元),多方未平倉口數,多方未平倉契約金額(千元),空方未平倉口數,"
          "空方未平倉契約金額(千元),多空未平倉口數淨額,多空未平倉契約金額淨額(千元)")


def taifex(days):
    lines = [HEADER]
    for index, day in enumerate(days):
        slash = day.replace("-", "/")
        lines.append(f"{slash},臺股期貨,自營商,1,1,1,1,0,0,10,10,20,20,-10,-10")
        lines.append(f"{slash},臺股期貨,外資,1,1,1,1,0,0,{1000 + index},5,{3000},7,{index - 2000},-2")
        lines.append(f"{slash},小型臺指,外資,1,1,1,1,0,0,9,9,9,9,0,0")
    return ("\n".join(lines) + "\n").encode("cp950")


class FakeHttp:
    def __init__(self, routes):
        self.routes = routes
        self.requests = []

    def __call__(self, method, url, data=None):
        self.requests.append((method, url, data))
        for prefix, reply in self.routes.items():
            if url.startswith(prefix):
                return reply(url, data) if callable(reply) else reply
        raise RuntimeError("沒有這個網址")


# 2026-02-02 起 60 個平日（最後 04-24）；資料包的法人與期貨只從 04-01 起。
DATES = weekdays("2026-02-02", 60)


def pack():
    payload = analysis(DATES)
    payload["institutional"] = [row for row in payload["institutional"] if row["date"] >= "2026-04-01"]
    payload["futures"] = [row for row in payload["futures"] if row["date"] >= "2026-04-01"]
    return payload


class ChipsTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.out = Path(self._tmp.name) / "chips-backfill"

    def tearDown(self):
        self._tmp.cleanup()

    def http(self):
        def twse(url, _):
            day = url.split("dayDate=")[1][:8]
            return bfi82u(-int(day[-2:]) * 100_000_000)
        return FakeHttp({"https://www.twse.com.tw/fund/BFI82U": twse,
                         "https://www.taifex.com.tw/": taifex([d for d in DATES if d < "2026-04-01"])})


class NeededDatesTests(ChipsTestCase):
    def test_backfills_the_twenty_five_trading_days_before_the_start_up_to_where_the_pack_begins(self):
        # 走勢標籤要往前二十日的法人、籌碼表要五日：起日之前多補二十五個交易日。
        needed = chips.needed_dates(pack(), start="2026-03-10")
        first = DATES[DATES.index("2026-03-10") - 25]
        self.assertEqual((needed[0], needed[-1]), (first, "2026-03-31"))
        self.assertEqual(needed, [d for d in DATES if first <= d <= "2026-03-31"])

    def test_nothing_is_needed_when_the_pack_already_covers_the_start(self):
        self.assertEqual(chips.needed_dates(analysis(DATES), start="2026-03-10"), [])


class FetchTests(ChipsTestCase):
    def test_fetches_each_day_once_from_twse_and_the_futures_range_in_one_request(self):
        needed = ["2026-03-27", "2026-03-30", "2026-03-31"]
        http = self.http()
        manifest = chips.fetch(self.out, needed, http=http, now=lambda: "2026-11-17T03:00:00+08:00", pause=0)
        twse = [url for method, url, _ in http.requests if "twse" in url]
        self.assertEqual(len(twse), 3)
        self.assertTrue(all("dayDate=" in url and "type=day" in url for url in twse))
        futures = [(method, data) for method, url, data in http.requests if "taifex" in url]
        self.assertEqual(futures, [("POST", {"queryStartDate": "2026/03/27", "queryEndDate": "2026/03/31",
                                             "commodityId": "TXF"})])
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), [
            "bfi82u-20260327.json", "bfi82u-20260330.json", "bfi82u-20260331.json", "manifest.json",
            "taifex-txf-20260327-20260331.csv"])
        self.assertEqual(manifest["fetched_at"], "2026-11-17T03:00:00+08:00")
        # 再跑一次：原件都在，就不再請求
        again = self.http()
        chips.fetch(self.out, needed, http=again, now=lambda: "x", pause=0)
        self.assertEqual(again.requests, [])

    def test_a_day_twse_does_not_answer_ok_stops_without_saving_it(self):
        http = FakeHttp({"https://www.twse.com.tw/": json.dumps({"stat": "很抱歉，沒有符合條件的資料!"}).encode(),
                         "https://www.taifex.com.tw/": taifex(["2026-03-31"])})
        with self.assertRaisesRegex(chips.BackfillStopped, "2026-03-31"):
            chips.fetch(self.out, ["2026-03-31"], http=http, now=lambda: "x", pause=0)
        self.assertFalse((self.out / "bfi82u-20260331.json").exists())


class LoadTests(ChipsTestCase):
    def test_saved_originals_become_the_same_rows_as_the_published_pack(self):
        chips.fetch(self.out, ["2026-03-30", "2026-03-31"], http=self.http(), now=lambda: "x", pause=0)
        rows = chips.load(self.out)
        # 外資淨額 = −日 × 1 億；自營商 = 自行買賣 10 ＋ 避險 −5
        self.assertEqual(rows["institutional"][0]["date"], "2026-03-30")
        self.assertEqual(rows["institutional"][0]["foreign"]["net"], -3_000_000_000)
        self.assertEqual(rows["institutional"][1]["dealer"]["net"], 5)
        self.assertEqual(rows["institutional"][1]["trust"]["net"], 100)
        # 期貨只取臺股期貨的外資那一列；淨額 = 第幾天 − 2000
        index = [d for d in DATES if d < "2026-04-01"].index("2026-03-31")
        self.assertEqual([(r["date"], r["foreign_oi_net_contracts"]) for r in rows["futures"]][-1],
                         ("2026-03-31", index - 2000))
        self.assertEqual(rows["futures"][0]["date"], DATES[0])

    def test_merging_keeps_the_published_rows_and_only_adds_missing_days(self):
        chips.fetch(self.out, ["2026-03-31"], http=self.http(), now=lambda: "x", pause=0)
        merged = chips.merge(pack(), chips.load(self.out))
        days = [row["date"] for row in merged["institutional"]]
        self.assertEqual(days[0], "2026-03-31")
        self.assertEqual(days, sorted(days))
        published = {row["date"]: row for row in pack()["institutional"]}
        self.assertEqual(merged["institutional"][1], published["2026-04-01"])
        futures_days = [row["date"] for row in merged["futures"]]
        self.assertEqual(len(futures_days), len(set(futures_days)))
        self.assertEqual(futures_days[-1], DATES[-1])


if __name__ == "__main__":
    unittest.main()
