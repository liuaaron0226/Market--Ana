"""雲端狀態同步（U1）：把每日更新需要的最小工作集打包、上傳到 R2，或從 R2 下載還原。

- 每個工作集項目存成一個 gzip 物件（SQLite 單檔或目錄 tar），附 ``state/manifest.json``：SHA-256、大小、資料日。
- 上傳以 SHA-256 內容定址的 immutable key 寫入新物件；全部成功才切換 manifest，中途失敗時舊 manifest 與其物件保持可還原。
- 舊 manifest 既有的固定 object key 仍可還原。
- 清理（本人 2026-11-14 同意）：資料日前進時把上一份 manifest 另存為 ``state/manifest.previous.json``；
  upload 成功後刪掉 ``state/`` 底下沒被目前或上一份 manifest 引用的物件（含失敗留下的暫存物件）。
  判斷不出哪些在用就一個都不刪；清理失敗不影響上傳結果。
- 還原逐一驗 SHA，全部通過才把暫存目錄換到目標位置；任一不符就丟出 StateIntegrityError，目標不動。
- R2 用 S3 相容 API（SigV4，標準庫實作，不新增依賴）；本機測試用 DirectoryStore 以目錄模擬。
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import hmac
import io
import json
import os
import re
import shutil
import sqlite3
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from xml.etree import ElementTree
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PREFIX = "state/"
MANIFEST = PREFIX + "manifest.json"
PREVIOUS_MANIFEST = PREFIX + "manifest.previous.json"  # 上一個資料日的 manifest：出問題時可退回，也是清理時要保留的一代
# B1（11-09，協調窗追加）：個股與 0050 的 3Y／5Y 含息對照要 5 年日K（60 個月約 1,214 個交易日，留 1,270 含緩衝）。
DAILY_KEEP_DAYS = 1270
LISTED_KEEP_DAYS = DAILY_KEEP_DAYS  # 舊名保留
BOOKKEEPING = frozenset({"meta.json", "gaps.json", "fetch-log.jsonl"})
CORE_CODES = ("0050", "2330")  # 長期核心持有：含息報酬從 2016 起算，上市日 K 不裁
# 大量股母體 rolling 60 日 warmup＋20 日窗會回讀 ETF 行情原件；留 100 個交易日（含緩衝）。
UNIVERSE_RAW_DAYS = 100
# U3：大盤頁資料包（cloud/market_pack_daily.py → logs/build_analysis_data.py）的原件快照目錄。
MARKET_SNAPSHOT = "data/market-snapshots/analysis-20261106-close"
# 人寫的劇本與白話分析：每日不重寫（本人 11-06），但 build 要它們組大盤頁；web/ 這兩檔不入 git，只在 R2 保存。
HUMAN_AUTHORED = ("web/dashboard-data.js", "web/plain-data.js")
# 產生物但要帶到隔天：build_stock_pack 以上一版 Functions 個股包的代號決定內建快取收哪些股票（不入 git）。
GENERATED_CARRY = ("cloud/.stock-pack.json",)


class StateIntegrityError(Exception):
    """遠端狀態、日期或物件完整性不符合契約。"""


STRICT_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


def require_iso_date(value, field: str) -> str:
    """只接受真實且完整的 YYYY-MM-DD；拒絕 compact ISO 與錯型。"""
    if not isinstance(value, str) or not STRICT_DATE.fullmatch(value):
        raise StateIntegrityError(f"{field} 必須是有效的 YYYY-MM-DD 日期")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise StateIntegrityError(f"{field} 必須是有效的 YYYY-MM-DD 日期") from error
    if parsed.isoformat() != value:
        raise StateIntegrityError(f"{field} 必須是有效的 YYYY-MM-DD 日期")
    return value


@dataclass(frozen=True)
class Item:
    path: str               # 相對 ROOT
    kind: str = "tree"      # sqlite／tree
    recent_files: int = 0   # tree 只留最近 N 個檔（依檔名排序；0＝全部）
    trim_days: int = 0      # sqlite 只留最近 N 個交易日（daily＋source_days）

    @property
    def key(self) -> str:
        return PREFIX + self.path.replace("/", "__") + (".gz" if self.kind == "sqlite" else ".tar.gz")


# 第 1 節工作集：每日增量只需這些；6 GB 影片與直播研究資料不在內。
WORKING_SET: tuple[Item, ...] = (
    Item("data/stock-history/stock-history.sqlite", kind="sqlite", trim_days=DAILY_KEEP_DAYS),
    Item("data/stock-history/raw", recent_files=UNIVERSE_RAW_DAYS),  # ETF 行情從原件讀（RawEtfSource 驗 SHA）
    Item("data/stock-history/gaps.json"),
    # 加權指數月檔：同時是交易日曆（stock_history）與大盤頁日K（build_analysis_data 要 2016 起全部月份，不得裁）。
    Item(f"{MARKET_SNAPSHOT}/index-monthly"),
    # 大盤頁其餘原件；窗口依 logs/build_analysis_data.py 的 require（多留會讓建置腳本因檔數不符而停）。
    Item(f"{MARKET_SNAPSHOT}/turnover-monthly"),              # 成交金額：2016 起全部月份
    Item(f"{MARKET_SNAPSHOT}/intraday-5s", recent_files=30),  # 每 5 秒指數 30 日（60 分K）
    Item(f"{MARKET_SNAPSHOT}/institutional", recent_files=120),  # 三大法人總表 120 日
    Item(f"{MARKET_SNAPSHOT}/sectors", recent_files=60),      # 類股指數 60 日
    Item(f"{MARKET_SNAPSHOT}/futures"),                       # 期交所 TXF CSV＋meta（建置只認 1 份）
    Item("data/market-snapshots/margin", recent_files=60),    # 信用交易總表（chips-data.js）
    *(Item(path) for path in HUMAN_AUTHORED),
    Item("data/otc-history/otc_history.sqlite", kind="sqlite", trim_days=DAILY_KEEP_DAYS),
    Item("data/otc-history/raw", recent_files=UNIVERSE_RAW_DAYS),    # 原件＋sidecar（ETF 行情、增量 build 驗 SHA）
    Item("data/otc-history/meta.json"),
    Item("data/otc-history/otc_market.sqlite", kind="sqlite"),
    Item("data/otc-history/raw-institutional", recent_files=60),
    Item("data/otc-history/raw-index"),
    Item("data/listed-institutional/listed_institutional.sqlite", kind="sqlite"),
    Item("data/listed-institutional/raw", recent_files=60),
    Item("data/listed-institutional/meta.json"),
    # 外資台指期部位（預測帳本 G 票）：期交所只開放查近三年，每天的原件全部保留、不得裁（每天兩個約 0.5 KB 的檔）。
    Item("data/foreign-futures"),
    Item("data/valuation-history/valuation.sqlite", kind="sqlite"),
    Item("data/valuation-history/twse/meta.json"),
    Item("data/fundamentals-history"),                        # 原件＋meta（load_history 要一對一），約 82 MB
    Item("data/listed-catalog/raw"),
    Item("data/otc-catalog/raw"),
    Item("data/market-snapshots/stock-detail", recent_files=60),  # 上市融資融券與法人明細（個股頁籌碼）
    # 個股融資原件的長期保存區：融資維持率（估）每天從起點重播，全部保留、不得裁（每日約 150 KB）。
    # 同一個資料夾還有重播要的行情摘要（prices/，每日約 42 KB），一樣全部保留。
    # 上面 stock-detail 裡的近期明細另有用途（首頁籌碼明細會把那裡的每一天都列出來），所以不放在一起。
    Item("data/margin-history"),
    Item("data/fundamentals/twt93u", recent_files=30),  # 個股頁融資券那一塊要近 20 個交易日走勢與 5 日增減（11-16）
    # 上櫃融資融券與借券原件（個股頁事實區塊 E 票）：融資券那一塊要近 20 個交易日；margin／sbl 兩個子資料夾各留 30 個。
    Item("data/otc-credit", recent_files=30),
    # 公司重大訊息的官方快照原件不在這裡：工作集整批換新，早上的排程寫進來會被每日更新蓋掉；
    # 改存 R2 的 announcements/raw/（檔名不重複、只增不改），見 announcements.py 的 daily()。
    Item("data/corporate-actions/events.json"),
    Item("data/corporate-actions/raw/TPEx-exDailyQ"),  # 櫃買除權息原件＋SHA＋涵蓋區間（2016 起全留）
    # 還原價的補充資料（ETF 分割、櫃買減資／面額變更、上市除權息現金股利）；第一次由 seeds/ 的種子起算。
    Item("data/corporate-actions/capital-supplement.json"),
    # 集保大戶持股：開放資料只有最新一週，歷史靠每週存檔累積（每份約 300KB，留 60 週）。
    Item("data/tdcc-holders/weekly", recent_files=60),
    Item("data/liquid-universe"),
    Item("data/market-screen-snapshots"),
    # G4-2W：跨日確認／波段狀態；EODHD／OFR 原件每天重取，VIX／SOX 不進 R2。
    Item("data/level-signals/state.json"),
    # U3：Actions 每次都是新機器。執行紀錄讓同一天後面的 cron 時段看到 already-done（不重做、不重部署），
    # status.json 保留最後成功日；前一天成功切換的 seed 是 D1 增量匯入的 base。
    Item("data/daily-update-log"),
    Item("data/daily-update-state/last-seed"),
    Item("data/daily-update-state/last-market-pack"),   # 官方月檔晚到時，大盤頁沿用這一版
    Item("data/daily-update-state/last-build-inputs"),  # 「資料不變、只重新部署程式」用：最後一次發布的建置輸入
    *(Item(path) for path in GENERATED_CARRY),
)


# ---- 物件存放 ----

class DirectoryStore:
    """以目錄模擬 R2（測試與本機 dry-run 用）。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        return self.root / key

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(path.name + ".partial")
        partial.write_bytes(data)
        os.replace(partial, path)

    def get(self, key: str) -> bytes | None:
        path = self._path(key)
        return path.read_bytes() if path.exists() else None

    def rename(self, src: str, dst: str) -> None:
        os.replace(self._path(src), self._path(dst))

    def keys(self, prefix: str = "") -> list[str]:
        if not self.root.exists():
            return []
        return sorted(key for key in (p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_file())
                      if key.startswith(prefix))

    def delete(self, key: str) -> None:
        self._path(key).unlink()


class R2Store:
    """Cloudflare R2（S3 相容）；SigV4 以標準庫實作。R2 沒有原子 rename，以 copy 後 delete 代替。"""

    def __init__(self, account_id: str, bucket: str, access_key: str, secret_key: str) -> None:
        self.host = f"{account_id}.r2.cloudflarestorage.com"
        self.bucket = bucket
        self.access_key = access_key
        self.secret_key = secret_key

    @classmethod
    def from_env(cls) -> "R2Store":
        # 剪貼簿存 secret 常多帶換行或空白，先去掉。
        env = {name: (os.environ.get(name) or "").strip() for name in
               ("CLOUDFLARE_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")}
        missing = [name for name in ("CLOUDFLARE_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")
                   if not env[name]]
        if missing:
            # Actions 上 secret 沒設時是空字串；用白話說缺哪個，不丟 KeyError。
            raise SystemExit(f"缺少 R2 金鑰：{', '.join(missing)}（R2 → Manage R2 API Tokens 建立後放進 GitHub Secrets）")
        if len(env["R2_ACCESS_KEY_ID"]) != 32:
            # R2 的 Access Key ID 固定 32 字元；長度不對多半是貼成了 Token value 或兩個值黏在一起。
            # 只報各行長度（Secret Access Key 一併列出，應為 64），不印內容。
            def shape(name: str) -> str:
                value = env[name]
                lines = "＋".join(str(len(line)) for line in value.splitlines()) or "0"
                # 字元組成（只計數）：分辨是貼錯欄位，還是傳輸時編碼被改過（例如 NUL、非 ASCII）。
                hexdigits = sum(c in "0123456789abcdefABCDEF" for c in value)
                nul = value.count("\x00")
                nonascii = sum(ord(c) > 127 for c in value)
                other = len(value) - hexdigits - nul - nonascii
                return f"{lines}（hex {hexdigits}、其他ASCII {other}、NUL {nul}、非ASCII {nonascii}）"
            raise SystemExit(f"R2_ACCESS_KEY_ID 長度是 {len(env['R2_ACCESS_KEY_ID'])}，應為 32："
                             "請重新複製 R2 API Token 頁面上的 Access Key ID（不是 Token value）"
                             f"〔各行長度：R2_ACCESS_KEY_ID {shape('R2_ACCESS_KEY_ID')}，"
                             f"R2_SECRET_ACCESS_KEY {shape('R2_SECRET_ACCESS_KEY')}〕")
        return cls(env["CLOUDFLARE_ACCOUNT_ID"], env["R2_BUCKET"] or "market-ana-state",
                   env["R2_ACCESS_KEY_ID"], env["R2_SECRET_ACCESS_KEY"])

    def _request(self, method: str, key: str, body: bytes = b"", headers: dict | None = None,
                 query: str = "") -> tuple[int, bytes]:
        now = datetime.now(timezone.utc)
        amz_date, stamp = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
        path = "/" + self.bucket + ("/" + urllib.parse.quote(key, safe="/") if key else "")
        payload_hash = hashlib.sha256(body).hexdigest()
        signed = {"host": self.host, "x-amz-content-sha256": payload_hash, "x-amz-date": amz_date,
                  **{k.lower(): v for k, v in (headers or {}).items()}}
        names = sorted(signed)
        canonical = "\n".join([method, path, query, "".join(f"{n}:{signed[n]}\n" for n in names),
                               ";".join(names), payload_hash])
        scope = f"{stamp}/auto/s3/aws4_request"
        to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
        key_bytes = ("AWS4" + self.secret_key).encode()
        for part in (stamp, "auto", "s3", "aws4_request"):
            key_bytes = hmac.new(key_bytes, part.encode(), hashlib.sha256).digest()
        signature = hmac.new(key_bytes, to_sign.encode(), hashlib.sha256).hexdigest()
        signed["authorization"] = (f"AWS4-HMAC-SHA256 Credential={self.access_key}/{scope}, "
                                   f"SignedHeaders={';'.join(names)}, Signature={signature}")
        url = f"https://{self.host}{path}" + (f"?{query}" if query else "")
        request = urllib.request.Request(url, data=body if method in ("PUT", "POST") else None, method=method,
                                         headers={k: v for k, v in signed.items() if k != "host"})
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def put(self, key: str, data: bytes) -> None:
        status, body = self._request("PUT", key, data)
        if status != 200:
            raise OSError(f"R2 PUT {key} → HTTP {status}: {body[:200]!r}")

    def get(self, key: str) -> bytes | None:
        status, body = self._request("GET", key)
        if status == 404:
            return None
        if status != 200:
            raise OSError(f"R2 GET {key} → HTTP {status}: {body[:200]!r}")
        return body

    def rename(self, src: str, dst: str) -> None:
        status, body = self._request("PUT", dst, headers={"x-amz-copy-source": f"/{self.bucket}/{src}"})
        if status != 200:
            raise OSError(f"R2 COPY {src} → {dst} HTTP {status}: {body[:200]!r}")
        self._request("DELETE", src)

    def keys(self, prefix: str = "") -> list[str]:
        """ListObjectsV2，逐頁取完。"""
        found: list[str] = []
        token = ""
        while True:
            params = {"list-type": "2", "prefix": prefix, **({"continuation-token": token} if token else {})}
            query = "&".join(f"{name}={urllib.parse.quote(value, safe='')}" for name, value in sorted(params.items()))
            status, body = self._request("GET", "", query=query)
            if status != 200:
                raise OSError(f"R2 LIST {prefix} → HTTP {status}: {body[:200]!r}")
            page = ElementTree.fromstring(body)
            fields = {}
            for element in page.iter():
                name = element.tag.rsplit("}", 1)[-1]
                if name == "Key":
                    found.append(element.text or "")
                elif name in ("IsTruncated", "NextContinuationToken"):
                    fields[name] = element.text or ""
            token = fields.get("NextContinuationToken", "")
            if fields.get("IsTruncated") != "true" or not token:
                return sorted(found)

    def delete(self, key: str) -> None:
        status, body = self._request("DELETE", key)
        if status not in (200, 204):
            raise OSError(f"R2 DELETE {key} → HTTP {status}: {body[:200]!r}")


# ---- 打包 ----

def trim_daily_db(src: Path, dst: Path, keep_days: int, keep_codes: tuple[str, ...] = ()) -> dict:
    """複製 daily＋source_days 只留最近 keep_days 個交易日（給雲端工作集；完整庫留本機）。

    ``keep_codes``（長期核心持有 0050／2330）保留全部歷史，含息報酬才能從 2016 起算。
    """
    dst.unlink(missing_ok=True)
    shutil.copyfile(src, dst)
    connection = sqlite3.connect(dst)
    try:
        days = [r[0] for r in connection.execute("SELECT DISTINCT date FROM daily ORDER BY date DESC LIMIT ?",
                                                 (keep_days,))]
        cutoff = min(days) if days else "9999-12-31"
        marks = ",".join("?" for _ in keep_codes) or "''"
        with connection:
            connection.execute(f"DELETE FROM daily WHERE date < ? AND code NOT IN ({marks})", (cutoff, *keep_codes))
            connection.execute("DELETE FROM source_days WHERE date < ?", (cutoff,))
        connection.execute("VACUUM")
        kept = connection.execute("SELECT COUNT(DISTINCT date) FROM daily").fetchone()[0]
    finally:
        connection.close()
    return {"days": kept, "from": cutoff}


def _tree_files(base: Path, item: Item) -> list[Path]:
    files = sorted(p for p in base.rglob("*") if p.is_file() and not p.name.endswith(".partial"))
    if not item.recent_files:
        return files
    # 每個子目錄各自依檔名取最近 N 個資料檔（例如 stock-detail 的 t86／margin／quotes 各留 N 天），
    # 附屬的 .sha256 sidecar 一起保留；meta／gaps／fetch-log 等帳務檔全部保留。
    by_folder: dict[Path, list[Path]] = {}
    for path in files:
        if not path.name.endswith(".sha256") and path.name not in BOOKKEEPING:
            by_folder.setdefault(path.parent, []).append(path)
    keep: set[Path] = set()
    for folder_files in by_folder.values():
        recent = sorted(folder_files, key=lambda p: p.name)[-item.recent_files:]
        keep |= set(recent) | {p.with_name(p.name + ".sha256") for p in recent}
    return [p for p in files if p in keep or p.name in BOOKKEEPING]


def _pack(root: Path, item: Item, scratch: Path) -> bytes | None:
    source = root / item.path
    if not source.exists():
        return None
    if item.kind == "sqlite":
        target = scratch / (item.path.replace("/", "__"))
        if item.trim_days:
            trim_daily_db(source, target, item.trim_days, keep_codes=CORE_CODES)
        else:
            shutil.copyfile(source, target)
        return gzip.compress(target.read_bytes(), 6, mtime=0)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz", compresslevel=6) as tar:
        if source.is_file():
            tar.add(source, arcname=item.path)
        else:
            for path in _tree_files(source, item):
                tar.add(path, arcname=path.relative_to(root).as_posix())
    return buffer.getvalue()


def _manifest_error(detail: str) -> StateIntegrityError:
    return StateIntegrityError(
        f"{MANIFEST} 無效（{detail}）；已停止讀寫，請人工修復或還原上一版 manifest"
    )


def _decode_manifest(raw: bytes) -> dict:
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _manifest_error("不是有效 JSON") from error
    if not isinstance(manifest, dict):
        raise _manifest_error("根節點必須是 object")
    try:
        manifest_date = require_iso_date(manifest.get("data_date"), "data_date")
    except StateIntegrityError as error:
        raise _manifest_error(str(error)) from error
    objects = manifest.get("objects")
    if not isinstance(objects, list):
        raise _manifest_error("objects 缺少或不是 array")
    cursors = manifest.get("cursors")
    if cursors is not None:
        if not isinstance(cursors, dict):
            raise _manifest_error("cursors 不是 object")
        if "market_data_date" not in cursors:
            raise _manifest_error("cursors.market_data_date 缺少")
        try:
            market_date = require_iso_date(cursors.get("market_data_date"), "cursors.market_data_date")
            if "level_last_processed_date" in cursors:
                require_iso_date(cursors.get("level_last_processed_date"), "cursors.level_last_processed_date")
        except StateIntegrityError as error:
            raise _manifest_error(str(error)) from error
        if market_date != manifest_date:
            raise _manifest_error("data_date 與 cursors.market_data_date 不一致")
    return manifest


def _remote_manifest(store) -> dict | None:
    raw = store.get(MANIFEST)
    return _decode_manifest(raw) if raw is not None else None


def _put_immutable(store, key: str, data: bytes, digest: str) -> None:
    """建立內容定址物件；既有 key 只能重用相同內容，絕不覆寫。"""
    existing = store.get(key)
    if existing is not None:
        if hashlib.sha256(existing).hexdigest() != digest:
            raise StateIntegrityError(f"immutable 物件 {key} 已存在但 SHA-256 不符；停止寫入並請人工檢查 R2")
        return
    staged_key = key + ".next"
    store.put(staged_key, data)
    store.rename(staged_key, key)


def _level_state_date(root: Path) -> str:
    path = Path(root) / "data/level-signals/state.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise StateIntegrityError(f"{path} 無法讀取有效 JSON；拒絕宣告 level cursor") from error
    if not isinstance(payload, dict):
        raise StateIntegrityError(f"{path} 必須是 JSON object；拒絕宣告 level cursor")
    return require_iso_date(payload.get("last_processed_date"), "level state last_processed_date")


def upload(root: Path, store, items=WORKING_SET, *, data_date: str, cursor_domain: str = "market") -> dict:
    """打包上傳；market／level 各自單調，新物件完成後才切換共同 manifest。"""
    data_date = require_iso_date(data_date, "data_date")
    if cursor_domain not in ("market", "level"):
        raise StateIntegrityError("cursor_domain 只接受 market 或 level")
    if cursor_domain == "level":
        actual_level_date = _level_state_date(root)
        if actual_level_date != data_date:
            raise StateIntegrityError(
                f"level cursor {data_date} 與 state.json.last_processed_date {actual_level_date} 不一致；拒絕寫入"
            )

    existing_manifest = _remote_manifest(store)
    existing_market = existing_manifest["data_date"] if existing_manifest is not None else None
    existing_cursors = existing_manifest.get("cursors", {}) if existing_manifest is not None else {}
    existing_level = existing_cursors.get("level_last_processed_date")
    if cursor_domain == "market":
        if existing_market and existing_market > data_date:
            raise StateIntegrityError(f"雲端市場資料日 {existing_market} 比本次 {data_date} 新，拒絕倒退上傳")
        market_date = data_date
        level_date = existing_level
    else:
        if existing_market is None:
            raise StateIntegrityError("level 狀態不能建立第一份市場 manifest；請先初始化 market cursor")
        if existing_level and existing_level > data_date:
            raise StateIntegrityError(f"雲端 level 狀態日 {existing_level} 比本次 {data_date} 新，拒絕倒退上傳")
        market_date = existing_market
        level_date = data_date

    cursors = {"market_data_date": market_date}
    if level_date is not None:
        cursors["level_last_processed_date"] = level_date
    manifest = {"schema": "market-ana-state/v1", "data_date": market_date, "cursors": cursors,
                "uploaded_at_utc": datetime.now(timezone.utc).isoformat(), "objects": []}
    with tempfile.TemporaryDirectory() as scratch_dir:
        scratch = Path(scratch_dir)
        for item in items:
            data = _pack(root, item, scratch)
            if data is None:
                manifest["objects"].append({"path": item.path, "key": None, "missing": True})
                continue
            digest = hashlib.sha256(data).hexdigest()
            immutable_key = f"{item.key}.{digest}"
            _put_immutable(store, immutable_key, data, digest)
            manifest["objects"].append({"path": item.path, "kind": item.kind, "key": immutable_key,
                                        "sha256": digest, "bytes": len(data)})
    advanced = cursor_domain == "market" and existing_market is not None and existing_market < data_date
    if existing_manifest is not None and (advanced or store.get(PREVIOUS_MANIFEST) is None):
        # 資料日前進了：原本那份就是「上一個資料日」。同日重傳或水位線上傳不輪替，上一份維持前一個資料日。
        # 例外是還沒有上一份的時候：原本那份先留下來，第一次清理才不會把它的物件全刪光而沒有退路。
        store.put(PREVIOUS_MANIFEST, json.dumps(existing_manifest, ensure_ascii=False, indent=1).encode("utf-8"))
    store.put(MANIFEST + ".next", json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8"))
    store.rename(MANIFEST + ".next", MANIFEST)
    return manifest


def collect_garbage(store) -> dict:
    """刪掉 state/ 底下沒被目前或上一份 manifest 引用的物件；只動 state/ 前綴。

    沒有目前的 manifest 就不動；任一份 manifest 讀不懂會丟 StateIntegrityError，同樣一個都不刪。
    """
    current = _remote_manifest(store)
    if current is None:
        return {"deleted": 0, "kept": 0}
    previous_raw = store.get(PREVIOUS_MANIFEST)
    previous = _decode_manifest(previous_raw) if previous_raw is not None else None
    in_use = {MANIFEST, PREVIOUS_MANIFEST} | {entry["key"] for manifest in (current, previous) if manifest
                                             for entry in manifest["objects"] if entry.get("key")}
    keys = [key for key in store.keys(PREFIX) if key.startswith(PREFIX)]
    garbage = [key for key in keys if key not in in_use]
    for key in garbage:
        store.delete(key)
    return {"deleted": len(garbage), "kept": len(keys) - len(garbage)}


def restore(target: Path, store, *, previous: bool = False) -> dict:
    """下載並驗 SHA；全部通過才換到 target。任何物件缺漏或不符，target 不動。previous＝還原上一個資料日。"""
    manifest_key = PREVIOUS_MANIFEST if previous else MANIFEST
    raw = store.get(manifest_key)
    if raw is None:
        raise StateIntegrityError(f"找不到 {manifest_key}")
    manifest = _decode_manifest(raw)
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target) as scratch_dir:
        scratch = Path(scratch_dir)
        for entry in manifest["objects"]:
            if entry.get("missing"):
                continue
            data = store.get(entry["key"])
            if data is None or hashlib.sha256(data).hexdigest() != entry["sha256"]:
                raise StateIntegrityError(f"{entry['key']} 缺漏或 SHA-256 不符")
            if entry["kind"] == "sqlite":
                out = scratch / entry["path"]
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(gzip.decompress(data))
            else:
                with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
                    for member in tar.getmembers():
                        name = Path(member.name)
                        if name.is_absolute() or ".." in name.parts:
                            raise StateIntegrityError(f"tar 內含不安全路徑：{member.name}")
                    tar.extractall(scratch)
        for entry in manifest["objects"]:
            if entry.get("missing"):
                continue
            produced = scratch / entry["path"]
            final = target / entry["path"]
            final.parent.mkdir(parents=True, exist_ok=True)
            if final.exists():
                shutil.rmtree(final) if final.is_dir() else final.unlink()
            os.replace(produced, final)
    return manifest


def remote_data_date(store) -> str | None:
    manifest = _remote_manifest(store)
    return manifest["data_date"] if manifest is not None else None


def init(root: Path, store, *, data_date: str, confirm: bool) -> dict:
    """首次初始化（一次性，本機執行）：把本機工作集上傳到 R2，再完整還原到暫存目錄核對 SHA。

    - 沒有 ``confirm`` 不上傳（會蓋掉雲端 manifest）。
    - 雲端已有比 ``data_date`` 新的狀態（Actions 已在跑）時拒絕，避免把雲端倒退回舊資料日。
    """
    data_date = require_iso_date(data_date, "data_date")
    if not confirm:
        raise SystemExit("init 會覆蓋雲端 state/manifest.json；確認後請加 --confirm（先跑 plan 看大小）")
    existing = remote_data_date(store)
    if existing and existing > data_date:
        raise SystemExit(f"雲端狀態資料日 {existing} 比本機 {data_date} 新，不倒退；要重建請先手動處理雲端")
    manifest = upload(root, store, WORKING_SET, data_date=data_date)
    with tempfile.TemporaryDirectory() as scratch:
        restore(Path(scratch) / "verify", store)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="每日更新工作集：上傳到 R2 或從 R2 還原")
    parser.add_argument("command", choices=["upload", "restore", "plan", "init"])
    parser.add_argument("--data-date", help="upload／init 時的資料日 YYYY-MM-DD")
    parser.add_argument("--cursor-domain", choices=("market", "level"), default="market",
                        help="upload 更新的分域 cursor；init 固定為 market")
    parser.add_argument("--store-dir", type=Path, help="以本機目錄模擬 R2（不給就用環境變數連 R2）")
    parser.add_argument("--target", type=Path, default=ROOT, help="restore 目標根目錄")
    parser.add_argument("--confirm", action="store_true", help="init 真的上傳（否則只報錯提示）")
    parser.add_argument("--previous", action="store_true", help="restore 改還原上一個資料日（manifest.previous.json）")
    args = parser.parse_args(argv)
    if args.command == "plan":
        with tempfile.TemporaryDirectory() as scratch:
            sizes = []
            for item in WORKING_SET:
                data = _pack(ROOT, item, Path(scratch))
                sizes.append({"path": item.path, "gz_bytes": len(data) if data else None})
        total_gz_mb = round(sum(s["gz_bytes"] or 0 for s in sizes) / 1e6, 1)
        print(json.dumps({
            "objects": sizes,
            "total_gz_mb": total_gz_mb,
            "immutable_space_estimate": f"每次成功 upload 最多新增約 {total_gz_mb} MB；未變物件依 SHA 重用",
            "remote_gc": "upload 成功後清理：只留目前與上一個資料日的 manifest 引用的物件",
        }, ensure_ascii=False, indent=1))
        return 0
    if args.command in ("upload", "init") and not args.data_date:
        parser.error(f"{args.command} 需要 --data-date")
    if args.command in ("upload", "init"):
        try:
            require_iso_date(args.data_date, "--data-date")
        except StateIntegrityError as error:
            parser.error(str(error))
    if args.command == "init" and args.cursor_domain != "market":
        parser.error("init 只能建立 market cursor")
    if args.command == "init" and not args.confirm:
        init(ROOT, None, data_date=args.data_date, confirm=False)
    store = DirectoryStore(args.store_dir) if args.store_dir else R2Store.from_env()
    if args.command == "init":
        manifest = init(ROOT, store, data_date=args.data_date, confirm=True)
    elif args.command == "upload":
        manifest = upload(ROOT, store, data_date=args.data_date, cursor_domain=args.cursor_domain)
    else:
        manifest = restore(args.target, store, previous=args.previous)
    report = {"data_date": manifest["data_date"], "cursors": manifest.get("cursors"), "objects": len(manifest["objects"])}
    if args.command == "upload":
        try:  # 上傳已經成功；清不成只記一筆，下次上傳再清
            report["cleanup"] = collect_garbage(store)
        except Exception as error:  # noqa: BLE001
            report["cleanup"] = {"error": type(error).__name__}
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
