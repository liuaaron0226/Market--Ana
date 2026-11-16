"""股票型 ETF 的判斷（本人 2026-11-16 第 14 題「只要股票型」；規格 .scratch/stock-signals/spec.md〈ETF 納入〉）。

兩種判斷，都只用現成的公開資料，不新增抓取：
1. 代號字尾。無字尾＝股票型、A＝主動式股票型；L 槓桿、R 反向、K 外幣計價是商品結構，不論持有什麼都排除；
   B、D 債券、U 期貨商品、T 平衡（股債混合）也排除。不認得的字尾不猜。
2. 官方名錄的類別：證交所／櫃買 ISIN 名錄（data/listed-catalog/raw、data/otc-catalog/raw，網站已在用的原件）
   每一檔 ETF 的 CFICode 第 5 碼是資產類別（E 股票、B 債券、D 衍生性商品、C 商品……）。

核對只在兩邊說的是同一件事時才做：字尾說「持有股票」（無字尾、A）或「持有別的」（B、D、U、T 等），就拿 CFI
的資產類別對；槓桿、反向、外幣計價是結構，CFI 沒有這個欄位（例如 00685L 的 CFI 是股票），不拿來比。
兩邊不一致、字尾說股票型但名錄查不到、或字尾不認得的，一律先不納入並列出來給本人看。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from listed_catalog import _TableRows

ROOT = Path(__file__).resolve().parent

STOCK = "stock"            # 納入
EXCLUDED = "excluded"      # 不是股票型，排除
MISMATCH = "mismatch"      # 兩種判斷不一致或無法核對：先不納入，列出來

# 字尾 → (白話類別, 字尾說的資產是不是股票；None＝結構類，不拿 CFI 核對)
SUFFIXES: dict[str, tuple[str, bool | None]] = {
    "": ("股票型", True),
    "A": ("主動式股票型", True),
    "L": ("槓桿型", None),
    "R": ("反向型", None),
    "K": ("外幣計價", None),
    "B": ("債券型", False),
    "D": ("主動式債券型", False),
    "U": ("期貨商品型", False),
    "T": ("平衡型（股債混合）", False),
    "C": ("非股票型（字尾 C）", False),
}
CFI_ASSETS = {"E": "股票", "B": "債券", "D": "衍生性商品", "C": "商品", "M": "其他", "L": "混合", "R": "不動產"}


@dataclass(frozen=True)
class Classification:
    code: str
    status: str            # STOCK／EXCLUDED／MISMATCH
    suffix_label: str      # 字尾看起來是哪一類
    official_label: str    # 官方名錄的資產類別（查不到就寫查不到）
    reason: str            # 白話原因（納入的也寫，方便列清單）


def suffix_of(code: str) -> str:
    return code[-1] if code and code[-1].isalpha() else ""


def official_label(cfi: str | None) -> str:
    if not cfi:
        return "官方名錄查不到"
    if not cfi.startswith("CE") or len(cfi) < 5:
        return f"不是 ETF（CFI {cfi}）"
    return CFI_ASSETS.get(cfi[4], f"其他（CFI {cfi}）")


def classify(code: str, cfi: str | None) -> Classification:
    suffix = suffix_of(code)
    label = official_label(cfi)
    if suffix not in SUFFIXES:
        return Classification(code, MISMATCH, f"不認得的字尾 {suffix}", label, f"代號字尾 {suffix} 不認得，先不納入")
    suffix_label, claims_stock = SUFFIXES[suffix]
    if claims_stock is None:
        return Classification(code, EXCLUDED, suffix_label, label, suffix_label)
    official_stock = None if not cfi or not cfi.startswith("CE") or len(cfi) < 5 else cfi[4] == "E"
    if official_stock is None:
        if claims_stock:
            return Classification(code, MISMATCH, suffix_label, label, f"字尾看是{suffix_label}，但官方名錄查不到這一檔")
        return Classification(code, EXCLUDED, suffix_label, label, suffix_label)
    if official_stock != claims_stock:
        return Classification(code, MISMATCH, suffix_label, label,
                              f"字尾看是{suffix_label}，官方名錄的資產類別是{label}")
    if claims_stock:
        return Classification(code, STOCK, suffix_label, label, suffix_label)
    return Classification(code, EXCLUDED, suffix_label, label, suffix_label)


@dataclass(frozen=True)
class EtfEntry:
    code: str
    name: str
    market: str   # 上市／上櫃
    cfi: str


def _catalog_rows(raw_html: Path, metadata_json: Path) -> list[list[str]]:
    metadata = json.loads(metadata_json.read_text(encoding="utf-8"))
    raw = raw_html.read_bytes()
    if hashlib.sha256(raw).hexdigest() != metadata["sha256"]:
        raise ValueError(f"{raw_html.name} SHA-256 與來源中繼資料不符")
    parser = _TableRows()
    parser.feed(raw.decode("cp950"))
    return parser.rows


def load_etf_catalog(*sources: tuple[Path, Path]) -> dict[str, EtfEntry]:
    """ISIN 名錄裡的 ETF（CFI 以 CE 開頭）：代號 → 名稱、市場、CFI。每份原件先驗 SHA。"""
    out: dict[str, EtfEntry] = {}
    for raw_html, metadata_json in sources:
        for row in _catalog_rows(raw_html, metadata_json):
            if len(row) != 7 or not row[5].startswith("CE") or row[3] not in ("上市", "上櫃"):
                continue
            parts = row[0].split(maxsplit=1)
            if len(parts) != 2:
                continue
            out.setdefault(parts[0], EtfEntry(parts[0], parts[1], row[3], row[5]))
    return out


def default_catalog_sources(root: Path = ROOT) -> list[tuple[Path, Path]]:
    listed = sorted((root / "data/listed-catalog/raw").glob("twse-isin-listed-*.html"))[-1]
    otc = sorted((root / "data/otc-catalog/raw").glob("tpex-isin-otc-*.html"))[-1]
    return [(listed, listed.with_suffix(".meta.json")), (otc, otc.with_suffix(".meta.json"))]


def load_default_catalog(root: Path = ROOT) -> dict[str, EtfEntry]:
    return load_etf_catalog(*default_catalog_sources(root))


def stock_etf_codes(catalog: Mapping[str, EtfEntry], market: str | None = None) -> frozenset[str]:
    """名錄裡判定為股票型的 ETF（不看成交金額名次）；market 給了就只取那個市場。"""
    return frozenset(code for code, entry in catalog.items()
                     if (market is None or entry.market == market) and classify(code, entry.cfi).status == STOCK)
