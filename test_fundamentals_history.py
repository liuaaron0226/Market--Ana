from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from fundamentals_history import (
    FundamentalsHistory,
    HttpResponse,
    _monthly_available_on,
    _quarter_available_on,
    fetch_history,
    load_history,
    parse_monthly_revenue,
    parse_quarterly_financials,
)


DATA = Path(__file__).resolve().parent / "data" / "fundamentals-history"


class MonthlyRevenueParserTest(unittest.TestCase):
    def test_known_2330_january_2024_revenue_fixture(self) -> None:
        raw = """
        <html><body><table>
          <tr><th>公司代號</th><th>公司名稱</th><th>當月營收</th></tr>
          <tr><td>2330</td><td>台積電</td><td>215,785,127</td></tr>
        </table></body></html>
        """.encode("big5")

        records = parse_monthly_revenue(
            raw,
            market="listed",
            period="2024-01",
            source_file="raw/monthly/listed/2024-01.html",
            fetched_at_utc="2026-11-08T00:00:00+00:00",
            sha256="fixture-sha256",
        )

        record = next(row for row in records if row["code"] == "2330")
        self.assertEqual(record["revenue_thousand_twd"], 215_785_127)
        self.assertEqual(record["unit"], "TWD_thousand")
        self.assertEqual(record["period"], "2024-01")
        self.assertEqual(record["available_on"], "2024-02-12")
        self.assertEqual(record["available_on_basis"], "statutory_deadline")
        self.assertEqual(record["source_file"], "raw/monthly/listed/2024-01.html")
        self.assertEqual(record["fetched_at_utc"], "2026-11-08T00:00:00+00:00")
        self.assertEqual(record["source_sha256"], "fixture-sha256")

    def test_known_2330_january_2024_revenue_matches_saved_official_original(self) -> None:
        manifest = json.loads((DATA / "meta.json").read_text(encoding="utf-8"))
        item = next(
            row
            for row in manifest["files"]
            if row["dataset"] == "monthly_revenue"
            and row["market"] == "listed"
            and row["period"] == "2024-01"
        )
        content = (DATA / item["file"]).read_bytes()
        self.assertEqual(hashlib.sha256(content).hexdigest(), item["sha256"])

        records = parse_monthly_revenue(
            content,
            market=item["market"],
            period=item["period"],
            source_file=item["file"],
            fetched_at_utc=item["fetched_at_utc"],
            sha256=item["sha256"],
        )

        record = next(row for row in records if row["code"] == "2330")
        self.assertEqual(record["revenue_thousand_twd"], 215_785_127)
        self.assertEqual(record["unit"], "TWD_thousand")

    def test_invalid_big5_byte_in_company_name_does_not_discard_numeric_original(self) -> None:
        raw = (
            b"<table><tr><th>\xa4\xbd\xa5\x71\xa5N\xb8\xb9</th><th>\xa4\xbd\xa5\x71\xa6W\xba\xd9</th>"
            b"<th>\xb7\xed\xa4\xeb\xc0\xe7\xa6\xac</th></tr>"
            b"<tr><td>2330</td><td>\x81</td><td>215,785,127</td></tr></table>"
        )

        records = parse_monthly_revenue(
            raw,
            market="listed",
            period="2024-01",
            source_file="raw/monthly/listed/2024-01.html",
            fetched_at_utc="2026-11-08T00:00:00+00:00",
            sha256="fixture-sha256",
        )

        self.assertEqual(records[0]["revenue_thousand_twd"], 215_785_127)


class QuarterlyFinancialParserTest(unittest.TestCase):
    def test_missing_values_stay_none_and_general_industry_debt_ratio_is_traceable(self) -> None:
        income = """
        <table><tr><th>公司代號</th><th>公司名稱</th><th>營業收入</th><th>營業毛利（毛損）</th>
        <th>營業利益（損失）</th><th>本期淨利（淨損）</th><th>基本每股盈餘（元）</th></tr>
        <tr><td>2330</td><td>台積電</td><td>1,000</td><td>400</td><td>200</td><td>150</td><td>--</td></tr></table>
        """.encode("utf-8")
        balance = """
        <table><tr><th>公司代號</th><th>公司名稱</th><th>流動資產</th><th>非流動資產</th>
        <th>資產總計</th><th>負債總計</th></tr>
        <tr><td>2330</td><td>台積電</td><td>1,200</td><td>800</td><td>2,000</td><td>1,000</td></tr></table>
        """.encode("utf-8")
        cashflow = """
        <table><tr><th>公司代號</th><th>公司名稱</th><th>營業活動之淨現金流入（流出）</th></tr>
        <tr><td>2330</td><td>台積電</td><td>--</td></tr></table>
        """.encode("utf-8")
        sources = {
            kind: {
                "source_file": f"raw/quarterly/listed/2024-Q1/{kind}.html",
                "fetched_at_utc": "2026-11-08T00:00:00+00:00",
                "sha256": f"{kind}-sha",
            }
            for kind in ("income", "balance", "cashflow")
        }

        records = parse_quarterly_financials(
            income,
            balance,
            cashflow,
            market="listed",
            period="2024-Q1",
            sources=sources,
        )

        record = next(row for row in records if row["code"] == "2330")
        self.assertEqual(record["revenue_thousand_twd"], 1_000)
        self.assertEqual(record["gross_profit_thousand_twd"], 400)
        self.assertEqual(record["operating_income_thousand_twd"], 200)
        self.assertEqual(record["net_income_thousand_twd"], 150)
        self.assertIsNone(record["eps_twd"])
        self.assertIsNone(record["operating_cash_flow_thousand_twd"])
        self.assertEqual(record["debt_ratio_pct"], 50.0)
        self.assertEqual(record["available_on"], "2024-05-15")
        self.assertEqual(record["available_on_basis"], "statutory_deadline")
        self.assertEqual(record["value_basis"], "fiscal_year_to_date")
        self.assertEqual(record["units"]["revenue_thousand_twd"], "TWD_thousand")
        self.assertEqual(record["sources"]["cashflow"]["sha256"], "cashflow-sha")
        self.assertTrue(record["applicability"]["debt_ratio"]["applicable"])

    def test_financial_sector_form_is_marked_not_applicable_instead_of_forcing_ratios(self) -> None:
        income = """
        <table><tr><th>公司代號</th><th>公司名稱</th><th>利息淨收益</th><th>本期淨利（淨損）</th>
        <th>基本每股盈餘（元）</th></tr>
        <tr><td>2881</td><td>富邦金</td><td>9,000</td><td>2,000</td><td>1.20</td></tr></table>
        """.encode("utf-8")
        balance = """
        <table><tr><th>公司代號</th><th>公司名稱</th><th>現金及約當現金</th><th>資產總計</th><th>負債總計</th></tr>
        <tr><td>2881</td><td>富邦金</td><td>500</td><td>10,000</td><td>9,000</td></tr></table>
        """.encode("utf-8")
        cashflow = """
        <table><tr><th>公司代號</th><th>公司名稱</th><th>營業活動之淨現金流入（流出）</th></tr>
        <tr><td>2881</td><td>富邦金</td><td>1,000</td></tr></table>
        """.encode("utf-8")
        sources = {
            kind: {"source_file": f"{kind}.html", "fetched_at_utc": "2026-11-08T00:00:00+00:00", "sha256": kind}
            for kind in ("income", "balance", "cashflow")
        }

        record = parse_quarterly_financials(
            income, balance, cashflow, market="listed", period="2024-Q1", sources=sources
        )[0]

        self.assertEqual(record["statement_form"], "sector_specific")
        self.assertIsNone(record["revenue_thousand_twd"])
        self.assertIsNone(record["gross_profit_thousand_twd"])
        self.assertIsNone(record["operating_income_thousand_twd"])
        self.assertIsNone(record["debt_ratio_pct"])
        self.assertFalse(record["applicability"]["standard_income_statement"]["applicable"])
        self.assertFalse(record["applicability"]["operating_cash_flow_quality"]["applicable"])
        self.assertFalse(record["applicability"]["debt_ratio"]["applicable"])


class FetchPolicyTest(unittest.TestCase):
    def test_existing_original_is_not_refetched_or_overwritten_and_requests_are_five_seconds_apart(self) -> None:
        raw = """
        <table><tr><th>公司代號</th><th>公司名稱</th><th>當月營收</th></tr>
        <tr><td>2330</td><td>台積電</td><td>1</td></tr></table>
        """.encode("big5")
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            existing = out / "raw/monthly/listed/2024-01.html"
            existing_otc = out / "raw/monthly/otc/2024-01.html"
            existing.parent.mkdir(parents=True)
            existing_otc.parent.mkdir(parents=True)
            existing.write_bytes(b"do-not-overwrite")
            existing_otc.write_bytes(b"do-not-overwrite-otc")
            (out / "meta.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "files": [
                            {
                                "dataset": "monthly_revenue",
                                "market": market,
                                "period": "2024-01",
                                "file": f"raw/monthly/{market}/2024-01.html",
                                "fetched_at_utc": "2026-11-08T00:00:00+00:00",
                                "sha256": hashlib.sha256(content).hexdigest(),
                            }
                            for market, content in (
                                ("listed", b"do-not-overwrite"),
                                ("otc", b"do-not-overwrite-otc"),
                            )
                        ],
                    }
                ),
                encoding="utf-8",
            )
            requested: list[str] = []
            slept: list[float] = []

            def get(url: str) -> HttpResponse:
                requested.append(url)
                return HttpResponse(200, "text/html", raw, url)

            summary = fetch_history(
                out,
                monthly_periods=["2024-01", "2024-02", "2024-03"],
                quarterly_periods=[],
                get=get,
                post=lambda *_: self.fail("不應 POST"),
                sleep=slept.append,
                now=lambda: "2026-11-08T00:00:00+00:00",
            )

            self.assertEqual(existing.read_bytes(), b"do-not-overwrite")
            self.assertEqual(len(requested), 4)  # 兩個新月份 × 上市／上櫃；既有兩市場都跳過
            self.assertEqual(slept, [5.0, 5.0, 5.0])
            self.assertEqual(summary["existing"], 2)
            manifest = json.loads((out / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual(len(manifest["files"]), 6)
            self.assertTrue(all(row["sha256"] for row in manifest["files"]))

    def test_existing_original_without_meta_stops_instead_of_silently_skipping(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            orphan = out / "raw/monthly/listed/2024-01.html"
            orphan.parent.mkdir(parents=True)
            orphan.write_bytes(b"orphan")

            summary = fetch_history(
                out,
                monthly_periods=["2024-01"],
                quarterly_periods=[],
                get=lambda _: self.fail("孤兒原件不得觸發網路"),
                post=lambda *_: self.fail("不應 POST"),
                sleep=lambda _: None,
            )

            self.assertIn("meta", summary["stopped"])
            self.assertEqual(orphan.read_bytes(), b"orphan")

    def test_existing_quarterly_original_without_meta_stops_instead_of_silently_skipping(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            orphan = out / "raw/quarterly/listed/2024-Q1/income.html"
            orphan.parent.mkdir(parents=True)
            orphan.write_bytes(b"orphan-income")

            summary = fetch_history(
                out,
                monthly_periods=[],
                quarterly_periods=["2024-Q1"],
                get=lambda _: self.fail("不應 GET"),
                post=lambda *_: self.fail("孤兒原件不得觸發網路"),
                sleep=lambda _: None,
            )

            self.assertIsNotNone(summary["stopped"])
            self.assertIn("meta", summary["stopped"])
            self.assertEqual(summary["existing"], 0)
            self.assertEqual(orphan.read_bytes(), b"orphan-income")

    def test_wrong_quarterly_statement_kind_stops_before_saving_that_file(self) -> None:
        income = """
        <table><tr><th>公司代號</th><th>公司名稱</th><th>營業收入</th><th>營業利益（損失）</th>
        <th>本期淨利（淨損）</th></tr>
        <tr><td>2330</td><td>台積電</td><td>1</td><td>1</td><td>1</td></tr></table>
        """.encode("utf-8")
        calls = 0

        def post(url: str, _params: dict) -> HttpResponse:
            nonlocal calls
            calls += 1
            return HttpResponse(200, "text/html", income, url)

        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            summary = fetch_history(
                out,
                monthly_periods=[],
                quarterly_periods=["2024-Q1"],
                get=lambda _: self.fail("不應 GET"),
                post=post,
                sleep=lambda _: None,
            )

            self.assertEqual(calls, 2)
            self.assertIsNotNone(summary["stopped"])
            self.assertTrue((out / "raw/quarterly/listed/2024-Q1/income.html").exists())
            self.assertFalse((out / "raw/quarterly/listed/2024-Q1/balance.html").exists())

    def test_429_or_unexpected_html_stops_immediately_without_saving_original(self) -> None:
        cases = [
            HttpResponse(429, "text/html", b"rate limited", "https://mopsov.twse.com.tw/limited"),
            HttpResponse(200, "text/html", b"<html>verification page</html>", "https://mopsov.twse.com.tw/check"),
        ]
        for response in cases:
            with self.subTest(status=response.status, url=response.url), tempfile.TemporaryDirectory() as directory:
                out = Path(directory)
                calls = 0

                def get(_url: str) -> HttpResponse:
                    nonlocal calls
                    calls += 1
                    return response

                summary = fetch_history(
                    out,
                    monthly_periods=["2024-01"],
                    quarterly_periods=[],
                    get=get,
                    post=lambda *_: self.fail("不應 POST"),
                    sleep=lambda _: None,
                )

                self.assertEqual(calls, 1)
                self.assertIsNotNone(summary["stopped"])
                self.assertFalse((out / "raw/monthly/listed/2024-01.html").exists())


class RawRepositoryTest(unittest.TestCase):
    def test_loader_rejects_an_original_whose_sha_no_longer_matches_meta(self) -> None:
        raw = """
        <table><tr><th>公司代號</th><th>公司名稱</th><th>當月營收</th></tr>
        <tr><td>2330</td><td>台積電</td><td>1</td></tr></table>
        """.encode("big5")
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            target = out / "raw/monthly/listed/2024-01.html"
            target.parent.mkdir(parents=True)
            target.write_bytes(raw)
            (out / "meta.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "files": [
                            {
                                "dataset": "monthly_revenue",
                                "market": "listed",
                                "period": "2024-01",
                                "file": "raw/monthly/listed/2024-01.html",
                                "fetched_at_utc": "2026-11-08T00:00:00+00:00",
                                "sha256": "wrong-sha",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "SHA-256"):
                load_history(out)

    def test_loader_rejects_raw_original_or_partial_file_missing_from_meta(self) -> None:
        raw = """
        <table><tr><th>公司代號</th><th>公司名稱</th><th>當月營收</th></tr>
        <tr><td>2330</td><td>台積電</td><td>1</td></tr></table>
        """.encode("big5")
        for stray in ("raw/monthly/listed/2024-02.html", "raw/monthly/listed/2024-02.html.partial"):
            with self.subTest(stray=stray), tempfile.TemporaryDirectory() as directory:
                out = Path(directory)
                target = out / "raw/monthly/listed/2024-01.html"
                target.parent.mkdir(parents=True)
                target.write_bytes(raw)
                (out / stray).write_bytes(raw)
                (out / "meta.json").write_text(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "files": [
                                {
                                    "dataset": "monthly_revenue",
                                    "market": "listed",
                                    "period": "2024-01",
                                    "file": "raw/monthly/listed/2024-01.html",
                                    "fetched_at_utc": "2026-11-08T00:00:00+00:00",
                                    "sha256": hashlib.sha256(raw).hexdigest(),
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )

                with self.assertRaisesRegex(ValueError, "meta"):
                    load_history(out)

    def test_loader_rejects_meta_entry_whose_original_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            (out / "raw").mkdir()
            (out / "meta.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "files": [
                            {
                                "dataset": "monthly_revenue",
                                "market": "listed",
                                "period": "2024-01",
                                "file": "raw/monthly/listed/2024-01.html",
                                "fetched_at_utc": "2026-11-08T00:00:00+00:00",
                                "sha256": "0" * 64,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "原件不存在"):
                load_history(out)


class StatementKindTest(unittest.TestCase):
    INCOME = """
    <table><tr><th>公司代號</th><th>公司名稱</th><th>營業收入</th><th>營業利益（損失）</th>
    <th>本期淨利（淨損）</th><th>基本每股盈餘（元）</th></tr>
    <tr><td>2330</td><td>台積電</td><td>1,000</td><td>200</td><td>150</td><td>1.00</td></tr></table>
    """.encode("utf-8")
    BALANCE = """
    <table><tr><th>公司代號</th><th>公司名稱</th><th>流動資產</th><th>非流動資產</th>
    <th>資產總計</th><th>負債總計</th></tr>
    <tr><td>2330</td><td>台積電</td><td>1,200</td><td>800</td><td>2,000</td><td>1,000</td></tr></table>
    """.encode("utf-8")
    CASHFLOW = """
    <table><tr><th>公司代號</th><th>公司名稱</th><th>營業活動之淨現金流入（流出）</th></tr>
    <tr><td>2330</td><td>台積電</td><td>300</td></tr></table>
    """.encode("utf-8")
    SOURCES = {
        kind: {"source_file": f"{kind}.html", "fetched_at_utc": "2026-11-08T00:00:00+00:00", "sha256": kind}
        for kind in ("income", "balance", "cashflow")
    }

    def test_parser_rejects_statement_contents_passed_in_the_wrong_slot(self) -> None:
        swapped = {
            "income": (self.BALANCE, self.BALANCE, self.CASHFLOW),
            "balance": (self.INCOME, self.INCOME, self.CASHFLOW),
            "cashflow": (self.INCOME, self.BALANCE, self.BALANCE),
        }
        for wrong_slot, contents in swapped.items():
            with self.subTest(wrong_slot=wrong_slot):
                with self.assertRaisesRegex(ValueError, wrong_slot):
                    parse_quarterly_financials(
                        *contents, market="listed", period="2024-Q1", sources=self.SOURCES
                    )

    def test_loader_rejects_meta_statement_label_that_does_not_match_original(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            files = []
            # meta 標 balance 的檔其實是損益表內容（例如寫入時錯置）。
            for statement, content in (("income", self.INCOME), ("balance", self.INCOME), ("cashflow", self.CASHFLOW)):
                relative = f"raw/quarterly/listed/2024-Q1/{statement}.html"
                (out / relative).parent.mkdir(parents=True, exist_ok=True)
                (out / relative).write_bytes(content)
                files.append(
                    {
                        "dataset": "quarterly_financials",
                        "statement": statement,
                        "market": "listed",
                        "period": "2024-Q1",
                        "file": relative,
                        "fetched_at_utc": "2026-11-08T00:00:00+00:00",
                        "sha256": hashlib.sha256(content).hexdigest(),
                    }
                )
            (out / "meta.json").write_text(json.dumps({"schema_version": 1, "files": files}), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "balance"):
                load_history(out)


def _monthly_html(rows: list[tuple[str, str, str]]) -> bytes:
    body = "".join(f"<tr><td>{code}</td><td>{name}</td><td>{value}</td></tr>" for code, name, value in rows)
    return f"<table><tr><th>公司代號</th><th>公司名稱</th><th>當月營收</th></tr>{body}</table>".encode("big5")


def _write_monthly_repo(out: Path, entries: list[tuple[str, str, str, bytes]]) -> None:
    """entries: (scope, market, period, content)；依正式路徑與 meta 欄位建立小型原件庫。"""
    files = []
    for scope, market, period, content in entries:
        folder = "monthly" if scope == "domestic" else "monthly-foreign"
        relative = f"raw/{folder}/{market}/{period}.html"
        (out / relative).parent.mkdir(parents=True, exist_ok=True)
        (out / relative).write_bytes(content)
        files.append(
            {
                "dataset": "monthly_revenue",
                "source_scope": scope,
                "market": market,
                "period": period,
                "file": relative,
                "fetched_at_utc": "2026-11-08T00:00:00+00:00",
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    (out / "meta.json").write_text(json.dumps({"schema_version": 1, "files": files}), encoding="utf-8")


class ForeignMonthlyRevenueTest(unittest.TestCase):
    def test_saved_official_foreign_original_matches_ky_row_field_by_field(self) -> None:
        manifest = json.loads((DATA / "meta.json").read_text(encoding="utf-8"))
        item = next(
            row
            for row in manifest["files"]
            if row["dataset"] == "monthly_revenue"
            and row.get("source_scope") == "foreign"
            and row["market"] == "listed"
            and row["period"] == "2026-09"
        )
        content = (DATA / item["file"]).read_bytes()
        self.assertEqual(hashlib.sha256(content).hexdigest(), item["sha256"])
        self.assertTrue(item["source_url"].endswith("/nas/t21/sii/t21sc03_115_9_1.html"))

        records = parse_monthly_revenue(
            content,
            market=item["market"],
            period=item["period"],
            source_file=item["file"],
            fetched_at_utc=item["fetched_at_utc"],
            sha256=item["sha256"],
            source_scope="foreign",
        )

        # 原件 2026-09 上市外國公司表：1256 鮮活果汁-KY 當月營收 561,575（單位：千元）。
        record = next(row for row in records if row["code"] == "1256")
        self.assertEqual(record["revenue_thousand_twd"], 561_575)
        self.assertIn("KY", record["name"])
        self.assertEqual(record["unit"], "TWD_thousand")
        self.assertEqual(record["source_scope"], "foreign")
        self.assertEqual(record["available_on"], "2026-10-15")  # 2026 起沿用最晚適用截止日（15 日）

    def test_fetch_foreign_scope_requests_suffix_1_table_and_records_scope_in_meta(self) -> None:
        raw = _monthly_html([("1256", "鮮活果汁-KY", "561,575")])
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            requested: list[str] = []

            def get(url: str) -> HttpResponse:
                requested.append(url)
                return HttpResponse(200, "text/html", raw, url)

            summary = fetch_history(
                out,
                monthly_periods=["2026-09"],
                quarterly_periods=[],
                monthly_scopes=("foreign",),
                get=get,
                post=lambda *_: self.fail("不應 POST"),
                sleep=lambda _: None,
                now=lambda: "2026-11-08T00:00:00+00:00",
            )

            self.assertIsNone(summary["stopped"])
            self.assertEqual(
                requested,
                [
                    "https://mopsov.twse.com.tw/nas/t21/sii/t21sc03_115_9_1.html",
                    "https://mopsov.twse.com.tw/nas/t21/otc/t21sc03_115_9_1.html",
                ],
            )
            self.assertTrue((out / "raw/monthly-foreign/listed/2026-09.html").exists())
            self.assertFalse((out / "raw/monthly/listed/2026-09.html").exists())
            manifest = json.loads((out / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual({row["source_scope"] for row in manifest["files"]}, {"foreign"})

    def test_loader_keeps_domestic_and_foreign_rows_with_their_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            _write_monthly_repo(
                out,
                [
                    ("domestic", "listed", "2026-09", _monthly_html([("2330", "台積電", "1")])),
                    ("foreign", "listed", "2026-09", _monthly_html([("1256", "鮮活果汁-KY", "2")])),
                ],
            )

            history = load_history(out)

            self.assertEqual(history.as_of("1256", "2026-11-01")["monthly"][0]["source_scope"], "foreign")
            self.assertEqual(history.as_of("2330", "2026-11-01")["monthly"][0]["source_scope"], "domestic")

    def test_loader_rejects_same_company_month_in_both_domestic_and_foreign_tables(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            _write_monthly_repo(
                out,
                [
                    ("domestic", "listed", "2026-09", _monthly_html([("1256", "鮮活果汁-KY", "1")])),
                    ("foreign", "listed", "2026-09", _monthly_html([("1256", "鮮活果汁-KY", "1")])),
                ],
            )

            with self.assertRaisesRegex(ValueError, "重複"):
                load_history(out)

    def test_as_of_does_not_expose_foreign_month_before_its_statutory_deadline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            _write_monthly_repo(
                out,
                [
                    ("foreign", "listed", "2026-08", _monthly_html([("1256", "鮮活果汁-KY", "1")])),
                    ("foreign", "listed", "2026-09", _monthly_html([("1256", "鮮活果汁-KY", "2")])),
                ],
            )

            history = load_history(out)

            # 2026-09 月營收的保守截止日為 10/15（週四）；10/14 只能看到 8 月，外國表與國內表同一規則。
            self.assertEqual([row["period"] for row in history.as_of("1256", "2026-10-14")["monthly"]], ["2026-08"])
            self.assertEqual(
                [row["period"] for row in history.as_of("1256", "2026-10-15")["monthly"]], ["2026-08", "2026-09"]
            )


class AvailabilityDeadlineTest(unittest.TestCase):
    def test_monthly_deadline_on_saturday_or_sunday_rolls_to_monday(self) -> None:
        self.assertEqual(_monthly_available_on("2024-01"), ("2024-02-12", "statutory_deadline"))  # 2/10 週六
        self.assertEqual(_monthly_available_on("2024-02"), ("2024-03-11", "statutory_deadline"))  # 3/10 週日
        self.assertEqual(_monthly_available_on("2024-03"), ("2024-04-10", "statutory_deadline"))  # 週三不動

    def test_quarterly_deadline_on_weekend_rolls_to_monday(self) -> None:
        self.assertEqual(_quarter_available_on("2023-Q4"), ("2024-04-01", "statutory_deadline"))  # 3/31 週日
        self.assertEqual(_quarter_available_on("2024-Q4"), ("2025-03-31", "statutory_deadline"))  # 週一不動


class PointInTimeQueryTest(unittest.TestCase):
    def test_as_of_query_excludes_records_that_were_not_yet_available(self) -> None:
        monthly = [
            {"code": "2330", "period": "2024-01", "available_on": "2024-02-10"},
            {"code": "2330", "period": "2024-02", "available_on": "2024-03-10"},
        ]
        quarterly = [
            {"code": "2330", "period": "2023-Q4", "available_on": "2024-04-01"},
        ]
        history = FundamentalsHistory(monthly=monthly, quarterly=quarterly)

        result = history.as_of("2330", "2024-02-29")

        self.assertEqual([row["period"] for row in result["monthly"]], ["2024-01"])
        self.assertEqual(result["quarterly"], [])


if __name__ == "__main__":
    unittest.main()
