"""雲端狀態同步（U1）：把每日更新需要的最小工作集打包、上傳到 R2，或從 R2 下載還原。

- 每個工作集項目存成一個 gzip 物件（SQLite 單檔或目錄 tar），附 ``state/manifest.json``：SHA-256、大小、資料日。
- 上傳先寫 ``*.next``，全部物件成功才寫 ``manifest.json.next`` 再換成正式名；中途失敗，舊 manifest 仍指向舊物件。
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
import shutil
import sqlite3
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PREFIX = "state/"
MANIFEST = PREFIX + "manifest.json"
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
    """還原時物件缺漏或 SHA 不符。"""


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
    Item("data/valuation-history/valuation.sqlite", kind="sqlite"),
    Item("data/valuation-history/twse/meta.json"),
    Item("data/fundamentals-history"),                        # 原件＋meta（load_history 要一對一），約 82 MB
    Item("data/listed-catalog/raw"),
    Item("data/otc-catalog/raw"),
    Item("data/market-snapshots/stock-detail", recent_files=60),  # 上市融資融券與法人明細（個股頁籌碼）
    Item("data/fundamentals/twt93u", recent_files=20),
    Item("data/corporate-actions/events.json"),
    Item("data/liquid-universe"),
    Item("data/market-screen-snapshots"),
    # U3：Actions 每次都是新機器。執行紀錄讓同一天後面的 cron 時段看到 already-done（不重做、不重部署），
    # status.json 保留最後成功日；前一天成功切換的 seed 是 D1 增量匯入的 base。
    Item("data/daily-update-log"),
    Item("data/daily-update-state/last-seed"),
    Item("data/daily-update-state/last-market-pack"),   # 官方月檔晚到時，大盤頁沿用這一版
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

    def keys(self) -> list[str]:
        return sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_file())


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


def upload(root: Path, store, items=WORKING_SET, *, data_date: str) -> dict:
    """打包上傳；全部物件成功才換上新 manifest。"""
    manifest = {"schema": "market-ana-state/v1", "data_date": data_date,
                "uploaded_at_utc": datetime.now(timezone.utc).isoformat(), "objects": []}
    with tempfile.TemporaryDirectory() as scratch_dir:
        scratch = Path(scratch_dir)
        staged: list[str] = []
        for item in items:
            data = _pack(root, item, scratch)
            if data is None:
                manifest["objects"].append({"path": item.path, "key": None, "missing": True})
                continue
            store.put(item.key + ".next", data)
            staged.append(item.key)
            manifest["objects"].append({"path": item.path, "kind": item.kind, "key": item.key,
                                        "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
    store.put(MANIFEST + ".next", json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8"))
    for key in staged:
        store.rename(key + ".next", key)
    store.rename(MANIFEST + ".next", MANIFEST)
    return manifest


def restore(target: Path, store) -> dict:
    """下載並驗 SHA；全部通過才換到 target。任何物件缺漏或不符，target 不動。"""
    raw = store.get(MANIFEST)
    if raw is None:
        raise StateIntegrityError("找不到 state/manifest.json")
    manifest = json.loads(raw.decode("utf-8"))
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
    raw = store.get(MANIFEST)
    return json.loads(raw.decode("utf-8")).get("data_date") if raw else None


def init(root: Path, store, *, data_date: str, confirm: bool) -> dict:
    """首次初始化（一次性，本機執行）：把本機工作集上傳到 R2，再完整還原到暫存目錄核對 SHA。

    - 沒有 ``confirm`` 不上傳（會蓋掉雲端 manifest）。
    - 雲端已有比 ``data_date`` 新的狀態（Actions 已在跑）時拒絕，避免把雲端倒退回舊資料日。
    """
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
    parser.add_argument("--store-dir", type=Path, help="以本機目錄模擬 R2（不給就用環境變數連 R2）")
    parser.add_argument("--target", type=Path, default=ROOT, help="restore 目標根目錄")
    parser.add_argument("--confirm", action="store_true", help="init 真的上傳（否則只報錯提示）")
    args = parser.parse_args(argv)
    if args.command == "plan":
        with tempfile.TemporaryDirectory() as scratch:
            sizes = []
            for item in WORKING_SET:
                data = _pack(ROOT, item, Path(scratch))
                sizes.append({"path": item.path, "gz_bytes": len(data) if data else None})
        print(json.dumps({"objects": sizes, "total_gz_mb": round(sum(s["gz_bytes"] or 0 for s in sizes) / 1e6, 1)},
                         ensure_ascii=False, indent=1))
        return 0
    if args.command in ("upload", "init") and not args.data_date:
        parser.error(f"{args.command} 需要 --data-date")
    if args.command == "init" and not args.confirm:
        init(ROOT, None, data_date=args.data_date, confirm=False)
    store = DirectoryStore(args.store_dir) if args.store_dir else R2Store.from_env()
    if args.command == "init":
        manifest = init(ROOT, store, data_date=args.data_date, confirm=True)
    elif args.command == "upload":
        manifest = upload(ROOT, store, data_date=args.data_date)
    else:
        manifest = restore(args.target, store)
    print(json.dumps({"data_date": manifest["data_date"], "objects": len(manifest["objects"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
