from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, time as clock_time, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
HOST = "127.0.0.1"
DEFAULT_PORT = 8876
TAIPEI = ZoneInfo("Asia/Taipei")

QUOTE_URL = (
    "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
    "?ex_ch=tse_t00.tw&json=1&delay=0"
)
CHART_URL = (
    "https://mis.twse.com.tw/stock/api/getChartOhlcStatis.jsp"
    "?ex=tse&ch=t00.tw"
)
BOOTSTRAP_URL = "https://mis.twse.com.tw/stock/"

QUOTE_INTERVAL_SECONDS = 5.0
CHART_INTERVAL_SECONDS = 15.0
REQUEST_TIMEOUT_SECONDS = 12.0
MAX_SERIES_POINTS = 4000


def iso_now() -> str:
    return datetime.now(TAIPEI).isoformat(timespec="seconds")


def parse_number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text in {"-", "--", "---", "null", "None"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_integer(value: Any) -> int | None:
    number = parse_number(value)
    return None if number is None else int(number)


def parse_data_time(fields: dict[str, Any]) -> datetime | None:
    millis = parse_integer(fields.get("tlong"))
    if millis is not None:
        try:
            return datetime.fromtimestamp(millis / 1000, TAIPEI)
        except (OverflowError, OSError, ValueError):
            pass

    day_text = str(fields.get("d") or "")
    time_text = str(fields.get("t") or fields.get("%") or "")
    if len(day_text) == 8 and time_text:
        try:
            return datetime.strptime(
                f"{day_text} {time_text}", "%Y%m%d %H:%M:%S"
            ).replace(tzinfo=TAIPEI)
        except ValueError:
            return None
    return None


def market_phase(now: datetime, data_day: date | None) -> tuple[str, bool]:
    if data_day != now.date() or now.weekday() >= 5:
        return "non_trading_day", False
    current = now.timetz().replace(tzinfo=None)
    if current < clock_time(9, 0):
        return "preopen", False
    if current <= clock_time(13, 30):
        return "open", True
    return "closed", False


class UpstreamState:
    def __init__(self, minimum_interval: float) -> None:
        self.minimum_interval = minimum_interval
        self.last_attempt_monotonic = 0.0
        self.last_success_monotonic = 0.0
        self.last_success_at: str | None = None
        self.value: Any = None
        self.error: str | None = None
        self.failures = 0
        self.retry_at_monotonic = 0.0

    def due(self, now_monotonic: float) -> bool:
        if now_monotonic < self.retry_at_monotonic:
            return False
        if self.value is None:
            return True
        return now_monotonic - self.last_success_monotonic >= self.minimum_interval

    def retry_after(self, now_monotonic: float) -> int:
        return max(0, int(round(self.retry_at_monotonic - now_monotonic)))


class LiveQuoteService:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookies)
        )
        self.bootstrapped = False
        self.quote = UpstreamState(QUOTE_INTERVAL_SECONDS)
        self.chart = UpstreamState(CHART_INTERVAL_SECONDS)
        self.series_day: date | None = None
        self.series: list[dict[str, Any]] = []

    @staticmethod
    def _headers() -> dict[str, str]:
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/131 Safari/537.36"
            ),
            "Accept": "application/json,text/plain,*/*",
            "Referer": "https://mis.twse.com.tw/stock/",
            "Cache-Control": "no-cache",
        }

    def _bootstrap(self) -> None:
        if self.bootstrapped:
            return
        self.bootstrapped = True
        try:
            request = urllib.request.Request(
                BOOTSTRAP_URL,
                headers={**self._headers(), "Accept": "text/html,*/*"},
            )
            with self.opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                response.read(512)
        except (OSError, urllib.error.URLError, urllib.error.HTTPError):
            # The quote endpoint currently works without a cookie. A failed bootstrap
            # must not hide a quote that can still be fetched directly.
            pass

    def _request_json(self, url: str) -> dict[str, Any]:
        self._bootstrap()
        separator = "&" if "?" in url else "?"
        cache_busted = f"{url}{separator}_={int(time.time() * 1000)}"
        request = urllib.request.Request(cache_busted, headers=self._headers())
        with self.opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            raw = response.read()
        return json.loads(raw.decode("utf-8-sig"))

    @staticmethod
    def _failure_delay(error: Exception, failures: int) -> float:
        if isinstance(error, urllib.error.HTTPError):
            retry_after = error.headers.get("Retry-After") if error.headers else None
            if retry_after and retry_after.isdigit():
                return max(30.0, float(retry_after))
            if error.code == 429:
                return 60.0
            if 400 <= error.code < 500:
                return 30.0
        return min(120.0, 5.0 * (2 ** min(failures, 5)))

    def _attempt(self, state: UpstreamState, fetcher) -> bool:
        now_monotonic = time.monotonic()
        if not state.due(now_monotonic):
            return state.error is None
        state.last_attempt_monotonic = now_monotonic
        try:
            value = fetcher()
        except Exception as error:  # converted to a bounded, visible API error below
            state.failures += 1
            delay = self._failure_delay(error, state.failures)
            state.retry_at_monotonic = time.monotonic() + delay
            if isinstance(error, urllib.error.HTTPError):
                state.error = f"證交所回應 HTTP {error.code}，已暫停重試 {int(delay)} 秒"
            elif isinstance(error, urllib.error.URLError):
                state.error = f"無法連線證交所：{error.reason}，已暫停重試 {int(delay)} 秒"
            else:
                state.error = f"證交所資料解析失敗：{error}，已暫停重試 {int(delay)} 秒"
            return False

        state.value = value
        state.failures = 0
        state.error = None
        state.retry_at_monotonic = 0.0
        state.last_success_monotonic = time.monotonic()
        state.last_success_at = iso_now()
        return True

    def _fetch_quote(self) -> dict[str, Any]:
        payload = self._request_json(QUOTE_URL)
        if str(payload.get("rtcode")) != "0000":
            raise ValueError(payload.get("rtmessage") or "MIS rtcode 不是 0000")
        rows = payload.get("msgArray")
        if not isinstance(rows, list) or not rows:
            raise ValueError("MIS msgArray 沒有加權指數資料")
        fields = rows[0]
        if not isinstance(fields, dict):
            raise ValueError("MIS 加權指數資料格式錯誤")

        price = parse_number(fields.get("z"))
        previous_close = parse_number(fields.get("y"))
        data_time = parse_data_time(fields)
        if price is None or previous_close is None or data_time is None:
            raise ValueError("MIS 缺少 z、y 或 tlong/d/t")

        change = price - previous_close
        change_percent = change / previous_close * 100 if previous_close else None
        return {
            "price": price,
            "previous_close": previous_close,
            "change": change,
            "change_percent": change_percent,
            "open": parse_number(fields.get("o")),
            "high": parse_number(fields.get("h")),
            "low": parse_number(fields.get("l")),
            "data_time": data_time,
            "data_date": data_time.date(),
            "source_fields": {
                "z": fields.get("z"),
                "y": fields.get("y"),
                "h": fields.get("h"),
                "l": fields.get("l"),
                "o": fields.get("o"),
                "tlong": fields.get("tlong"),
                "d": fields.get("d"),
                "t": fields.get("t") or fields.get("%"),
                "m": fields.get("m"),
                "r": fields.get("r"),
            },
            "upstream_user_delay_ms": parse_integer(payload.get("userDelay")),
        }

    def _fetch_chart(self) -> dict[str, Any]:
        payload = self._request_json(CHART_URL)
        if str(payload.get("rtcode")) != "0000":
            raise ValueError(payload.get("rtmessage") or "MIS chart rtcode 不是 0000")
        static = payload.get("staticObj") or {}
        rows = payload.get("ohlcArray") or []
        if not isinstance(static, dict) or not isinstance(rows, list):
            raise ValueError("MIS 分時資料格式錯誤")

        points: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            price = parse_number(row.get("c"))
            millis = parse_integer(row.get("t"))
            if price is None or millis is None:
                continue
            try:
                stamp = datetime.fromtimestamp(millis / 1000, TAIPEI)
            except (OverflowError, OSError, ValueError):
                continue
            points.append(
                {
                    "timestamp": stamp.isoformat(timespec="seconds"),
                    "time": stamp.strftime("%H:%M"),
                    "price": price,
                    "volume": parse_integer(row.get("s")),
                }
            )

        chart_day = None
        if points:
            chart_day = date.fromisoformat(points[-1]["timestamp"][:10])
        else:
            key_text = str(static.get("key") or "")
            if len(key_text) >= 8 and key_text[-8:].isdigit():
                chart_day = datetime.strptime(key_text[-8:], "%Y%m%d").date()

        return {
            "data_date": chart_day,
            "turnover_value": parse_integer(static.get("tz")),
            "trade_volume": parse_integer(static.get("tv")),
            "transactions": parse_integer(static.get("tr")),
            "points": points,
            "upstream_user_delay_ms": parse_integer(payload.get("userDelay")),
            "source_fields": {
                "tz": static.get("tz"),
                "tv": static.get("tv"),
                "tr": static.get("tr"),
            },
        }

    def _merge_series(
        self, data_day: date, points: list[dict[str, Any]], quote: dict[str, Any]
    ) -> None:
        if self.series_day != data_day:
            self.series_day = data_day
            self.series = []

        merged = {point["timestamp"]: point for point in self.series}
        for point in points:
            stamp = str(point.get("timestamp") or "")
            if stamp:
                merged[stamp] = point

        quote_time = quote["data_time"].isoformat(timespec="seconds")
        merged[quote_time] = {
            "timestamp": quote_time,
            "time": quote["data_time"].strftime("%H:%M"),
            "price": quote["price"],
            "volume": None,
        }
        self.series = sorted(merged.values(), key=lambda item: item["timestamp"])[
            -MAX_SERIES_POINTS:
        ]

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            quote_ok = self._attempt(self.quote, self._fetch_quote)
            quote = self.quote.value

            # The chart endpoint declares a 15-second delay. It is independent of
            # the 5-second quote cache so a page poll cannot make us over-request it.
            chart_ok = self._attempt(self.chart, self._fetch_chart)
            chart = self.chart.value

            now = datetime.now(TAIPEI)
            now_monotonic = time.monotonic()
            if quote is None:
                return {
                    "ok": False,
                    "source": "臺灣證券交易所基本市況報導（MIS）",
                    "source_urls": {"quote": QUOTE_URL, "chart": CHART_URL},
                    "served_at": now.isoformat(timespec="seconds"),
                    "fetched_at": self.quote.last_success_at,
                    "data_time": None,
                    "market_open": False,
                    "market_phase": "unknown",
                    "delay": (
                        "MIS 主報價宣告 5 秒更新；本機主報價至少間隔 5 秒、"
                        "分時與成交金額至少間隔 15 秒。"
                    ),
                    "delay_seconds": {"quote": 5, "chart": 15},
                    "error": self.quote.error or "尚未取得證交所資料",
                    "warning": self.chart.error,
                    "last_success_at": self.quote.last_success_at,
                    "retry_after_seconds": max(
                        self.quote.retry_after(now_monotonic),
                        self.chart.retry_after(now_monotonic),
                    ),
                    "data": None,
                    "series": [],
                }

            chart_same_day = bool(
                isinstance(chart, dict) and chart.get("data_date") == quote["data_date"]
            )
            chart_points = chart.get("points", []) if chart_same_day else []
            self._merge_series(quote["data_date"], chart_points, quote)
            phase, is_open = market_phase(now, quote["data_date"])
            turnover_value = chart.get("turnover_value") if chart_same_day else None
            turnover_billion = (
                turnover_value / 100_000_000 if turnover_value is not None else None
            )
            last_success = self.quote.last_success_at
            quote_age = (
                max(0.0, time.monotonic() - self.quote.last_success_monotonic)
                if self.quote.last_success_monotonic
                else None
            )

            error = None if quote_ok else self.quote.error
            warning = None
            if not chart_ok and self.chart.error:
                warning = self.chart.error
            elif chart is None:
                warning = "尚未取得分時與成交金額資料"

            return {
                "ok": quote_ok,
                "source": "臺灣證券交易所基本市況報導（MIS）",
                "source_urls": {"quote": QUOTE_URL, "chart": CHART_URL},
                "served_at": now.isoformat(timespec="seconds"),
                "fetched_at": last_success,
                "data_time": quote["data_time"].isoformat(timespec="seconds"),
                "market_open": is_open,
                "market_phase": phase,
                "delay": (
                    "MIS 主報價 userDelay=5000ms；本機主報價至少間隔 5 秒，"
                    "官方分時與成交金額端點 userDelay=15000ms，至少間隔 15 秒。"
                ),
                "delay_seconds": {"quote": 5, "chart": 15},
                "cache_age_seconds": round(quote_age, 3) if quote_age is not None else None,
                "error": error,
                "warning": warning,
                "last_success_at": last_success,
                "retry_after_seconds": max(
                    self.quote.retry_after(now_monotonic),
                    self.chart.retry_after(now_monotonic),
                ),
                "data": {
                    "price": quote["price"],
                    "previous_close": quote["previous_close"],
                    "change": quote["change"],
                    "change_percent": quote["change_percent"],
                    "open": quote["open"],
                    "high": quote["high"],
                    "low": quote["low"],
                    "turnover_value": turnover_value,
                    "turnover_billion": turnover_billion,
                    "trade_volume": chart.get("trade_volume") if chart_same_day else None,
                    "transactions": chart.get("transactions") if chart_same_day else None,
                    "source_fields": {
                        "quote": quote["source_fields"],
                        "chart": chart.get("source_fields") if chart_same_day else None,
                    },
                },
                "series": self.series,
            }


SERVICE = LiveQuoteService()


class MarketHTTPServer(ThreadingHTTPServer):
    # On Windows, SO_REUSEADDR can let two unrelated local servers bind the same
    # port and randomly receive each other's requests. Fail instead of sharing.
    allow_reuse_address = False
    daemon_threads = True


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _send_stock_analysis(self, path: str, query: str) -> None:
        """GET /api/stocks/{code}/analysis?as_of=YYYY-MM-DD：只回結果投影，公式與權重留在後端。"""
        code = path[len("/api/stocks/"):-len("/analysis")]
        as_of = (urllib.parse.parse_qs(query).get("as_of") or ["9999-12-31"])[0]
        if not re.fullmatch(r"[0-9A-Z]{4,6}", code) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", as_of):
            self._send_json({"error": "bad-request", "message": "代號或日期格式不正確"}, 400)
            return
        try:
            from twon_analysis.analyze import analyze

            result = analyze(code, as_of)
        except Exception as error:  # 不把 stack trace 送到瀏覽器
            print(f"stock analysis failed for {code}: {type(error).__name__}: {error}")
            self._send_json({"error": "analysis-failed", "message": "分析暫時無法完成"}, 500)
            return
        if not result.get("name") and result.get("qa", {}).get("blocking"):
            self._send_json({**result, "error": "insufficient-data"}, 404 if result["qa"].get("not_found") else 200)
            return
        self._send_json(result)

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        path = urllib.parse.urlsplit(self.path).path
        if path == "/api/live":
            self._send_json(SERVICE.snapshot())
            return
        if path.startswith("/api/stocks/") and path.endswith("/analysis"):
            self._send_stock_analysis(path, urllib.parse.urlsplit(self.path).query)
            return
        if path == "/health":
            self._send_json(
                {
                    "ok": True,
                    "host": HOST,
                    "port": self.server.server_port,
                    "web_root": str(WEB_DIR),
                }
            )
            return
        super().do_GET()

    def end_headers(self) -> None:
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Serve Market--Ana locally and proxy the TWSE MIS live index quote."
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("MARKET_ANA_PORT", DEFAULT_PORT)),
    )
    args = parser.parse_args()
    if not WEB_DIR.is_dir():
        raise SystemExit(f"Missing web directory: {WEB_DIR}")

    server = MarketHTTPServer((HOST, args.port), Handler)
    url = f"http://{HOST}:{args.port}/visual-preview.html"
    print(f"Market--Ana live service: {url}")
    print("Bound to 127.0.0.1 only. Press Ctrl+C to stop.")
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
