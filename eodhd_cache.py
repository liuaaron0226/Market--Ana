"""EODHD 的共用快取（G 票延伸，本人 2026-11-17「可以共用」）：大盤預測的海外層（``overseas_inputs``，D 票）與
關注的國外同類股（``overseas_peers``，B 票）同一個代號、同一個收盤之後只打一次 EODHD，兩邊讀同一份。

EODHD 免費方案每天 20 次，要和水位（S&P 500）、站主自己加代號時的檢查、同類股提名的檢查共用。

- 放在 R2 的 ``eodhd-cache/`` 前綴：``<代號>.raw``（EODHD 回應原樣）與 ``<代號>.meta.json``（抓取時間、範圍、雜湊；
  網址裡的金鑰不存）。不放工作集：工作集是三支排程共用一把鎖、整包換 manifest，這份兩條線都要隨時讀寫。
- 什麼時候重抓：這個代號的市場在上一次抓取之後又收盤了一次才重抓。美國的代號看美國收盤（UTC 22:00，冬令時間收盤後
  約一小時、夏令兩小時，EODHD 已更新），亞洲的看亞洲收盤（UTC 08:00＝台北 16:00，日韓收盤後約一個半小時）；週末不算。
  不認得市場的代號兩邊都算。照現在的排程，美國的指數每天由晨間版（台北 07:20）抓一次、晚間版讀同一份；日韓的指數與
  韓股由每日更新裡的同類股抓取或晚間版（誰先）抓一次、隔天晨間版讀同一份。
- 每次都取最近約一年（免費方案最多就這麼多），兩邊各自切自己要的日子：預測帳本的海外層本來就依版本截到該看的那天。
- 重抓失敗（額度用完、網路、格式）時有舊的一份就用舊的，並在 meta 標 ``refresh_error``；沒有就丟 ``FetchError``。
- 兩支排程剛好同時要同一檔時兩邊都會抓（最後寫的那一份留下），不會壞資料，只是那一次多用一次額度。
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
from collections.abc import Callable
from datetime import datetime, time, timedelta, timezone
from typing import Any

PREFIX = "eodhd-cache/"
ENDPOINT = "https://eodhd.com/api/eod/{symbol}"
HISTORY_DAYS = 400
UTC = timezone.utc
CLOSES = {"us": time(22, 0), "asia": time(8, 0)}
# 收盤之後抓到的那一份還沒有那一天（EODHD 還沒更新）：下一次要再抓；過了這麼久還是沒有，就當那天休市、不再一直重抓。
SETTLE = timedelta(hours=6)
# 美國的指數（其他 .INDX 不認得就兩邊都算）。韓股 .KO／.KQ 與日韓指數看亞洲收盤。
US_INDICES = {"GSPC", "IXIC", "DJI", "NDX", "RUT", "SOX", "VIX", "NYA", "XAX"}
ASIA_INDICES = {"N225", "KS11", "KQ11", "HSI", "SSEC", "TWII", "STI", "AXJO"}
_SYMBOL = re.compile(r"^[0-9A-Z.^]{2,20}$")


class FetchError(Exception):
    """抓不到，也沒有舊的一份可用。``kind``：missing_key／invalid_symbol／http／network／format；
    ``status`` 是 EODHD 回的 HTTP 狀態（不是 HTTP 錯誤時是 None）。"""

    def __init__(self, message: str, kind: str, status: int | None = None):
        super().__init__(message)
        self.kind, self.status = kind, status


def market_zone(symbol: str) -> str:
    stem, _, suffix = symbol.upper().rpartition(".")
    if suffix in ("KO", "KQ", "KS", "TSE", "T"):
        return "asia"
    if suffix == "US":
        return "us"
    if suffix == "INDX":
        return "us" if stem in US_INDICES else "asia" if stem in ASIA_INDICES else "any"
    return "any"


def last_close(zone: str, now: datetime) -> datetime:
    """這個市場在 ``now`` 之前最近一次收盤（之後 EODHD 才有新的一天）；週六、週日沒有收盤。"""
    if zone == "any":
        return max(last_close("us", now), last_close("asia", now))
    moment = now.astimezone(UTC)
    day = moment.date()
    while True:
        point = datetime.combine(day, CLOSES[zone], tzinfo=UTC)
        if point <= moment and day.weekday() < 5:
            return point
        day -= timedelta(days=1)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _last_date(body: bytes) -> str | None:
    try:
        days = [str(row.get("date") or "") for row in json.loads(body) if isinstance(row, dict)]
    except (ValueError, UnicodeDecodeError, TypeError):
        return None
    return max(days) if days else None


def _fresh(meta: dict[str, Any], close: datetime) -> bool:
    """共用的那一份能不能直接用：是在最近一次收盤之後抓的，而且有那一天的收盤（或抓的時候已經是收盤後好幾小時，
    那天大概休市）。"""
    fetched = meta.get("fetched_at", "")
    if fetched < _iso(close):
        return False
    return (meta.get("last_date") or "") >= close.date().isoformat() or fetched >= _iso(close + SETTLE)


def _default_http(url: str) -> tuple[int, bytes]:
    import urllib.error
    import urllib.request
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "market-ana-eodhd-cache"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read() or b""


class SharedEodhd:
    """兩條線共用的 EODHD：``raw(symbol)`` 回（EODHD 回應原樣, meta）；``calls`` 是這一次真的打了幾次 EODHD。"""

    def __init__(self, store: Any, token: str, *, http: Callable[[str], tuple[int, bytes]] | None = None,
                 now: Callable[[], datetime] | None = None):
        self.store, self.token = store, token or ""
        self.http = http or _default_http
        self.now = now or (lambda: datetime.now(UTC))
        self.calls = 0

    @classmethod
    def from_env(cls, token: str) -> "SharedEodhd | None":
        """排程用：連 R2（state_sync 的設定）。R2 的設定不齊時回 None，呼叫的人照原本的做法直接打 EODHD。"""
        from state_sync import R2Store
        try:
            return cls(R2Store.from_env(), token)
        except (SystemExit, Exception):  # noqa: BLE001 — 沒有共用快取不擋抓取
            return None

    def _get(self, key: str) -> bytes | None:
        try:
            return self.store.get(key)
        except Exception:  # noqa: BLE001 — R2 讀不到就當作沒有共用的那一份
            return None

    def _cached(self, symbol: str) -> tuple[bytes | None, dict[str, Any] | None]:
        meta_bytes, raw = self._get(f"{PREFIX}{symbol}.meta.json"), self._get(f"{PREFIX}{symbol}.raw")
        if not meta_bytes or raw is None:
            return None, None
        try:
            meta = json.loads(meta_bytes)
        except (ValueError, UnicodeDecodeError):
            return None, None
        # 雜湊對不上（寫到一半、被改動）就當作沒有。
        return (raw, meta) if meta.get("sha256") == hashlib.sha256(raw).hexdigest() else (None, None)

    def raw(self, symbol: str) -> tuple[bytes, dict[str, Any]]:
        if not _SYMBOL.fullmatch(symbol or ""):
            raise FetchError(f"代號不合法：{symbol!r}", "invalid_symbol")
        now = self.now()
        raw, meta = self._cached(symbol)
        if raw is not None and _fresh(meta, last_close(market_zone(symbol), now)):
            return raw, {**meta, "shared": True}
        if not self.token:
            if raw is not None:
                return raw, {**meta, "shared": True, "refresh_error": "missing_key", "refresh_kind": "missing_key"}
            raise FetchError("沒有 EODHD 金鑰，也沒有共用的那一份", "missing_key")
        start = (now.astimezone(UTC).date() - timedelta(days=HISTORY_DAYS)).isoformat()
        end = now.astimezone(UTC).date().isoformat()
        query = {"fmt": "json", "period": "d", "from": start, "to": end}
        url = ENDPOINT.format(symbol=symbol) + "?" + urllib.parse.urlencode({"api_token": self.token, **query})
        self.calls += 1
        try:
            status, body = self.http(url)
        except Exception as error:  # noqa: BLE001 — 連線失敗：有舊的就用舊的
            status, body, problem, kind = None, b"", type(error).__name__, "network"
        else:
            problem, kind = (None, None) if status == 200 and body else (f"HTTP {status}", "http")
        if problem is None:
            try:
                if not isinstance(json.loads(body), list):
                    problem, kind = "format", "format"
            except (ValueError, UnicodeDecodeError):
                problem, kind = "format", "format"
        if problem is not None:
            if raw is not None:
                return raw, {**meta, "shared": True, "refresh_error": problem, "refresh_kind": kind,
                             "refresh_status": status if kind == "http" else None}
            raise FetchError(f"EODHD {symbol}：{problem}", kind, status if kind == "http" else None)
        meta = {"symbol": symbol, "fetched_at": _iso(now), "from": start, "to": end, "last_date": _last_date(body),
                "bytes": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
                "url": ENDPOINT.format(symbol=symbol) + "?" + urllib.parse.urlencode(
                    {"api_token": "REDACTED", **query})}
        # 先寫原件再寫 meta：讀的人以 meta 的雜湊核對，寫到一半的不會被當成新的一份。
        # R2 暫時寫不進去：這一次拿到的照樣用（額度已經花了），只是沒有共用到。
        try:
            self.store.put(f"{PREFIX}{symbol}.raw", body)
            self.store.put(f"{PREFIX}{symbol}.meta.json", json.dumps(meta, ensure_ascii=False).encode("utf-8"))
        except Exception as error:  # noqa: BLE001
            return body, {**meta, "shared": False, "store_error": type(error).__name__}
        return body, {**meta, "shared": False}
