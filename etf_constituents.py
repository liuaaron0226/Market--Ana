"""國內股票型 ETF 成分（E4，2026-11-09）。

來源：中華民國證券投資信託暨顧問商業同業公會（SITCA）公開頁「基金月底持股明細」
（IN2629.aspx，依基金類型查詢），原件存 data/etf-constituents/raw/，SHA 記在 sitca-<YYYYMM>.meta.json。
- AH11＝國內投資指數股票型_股票型（被動）；AL11＝國內投資主動式交易所交易基金_股票型（主動）。
- 限制：SITCA 只公布每檔基金「前 10 大」持股，不是完整成分；所以本模組回傳的是「前十大成分」，
  is_constituent() 為 False 只代表不在任一 ETF 的前十大，不代表一定不是成分股。
- 月底資料，約次月中後公布；constituents(date) 取 date 當下已可取得的最新月份（保守以次月 20 日為可得日）。

E5（2026-11-09）新增完整成分（source="pcf_full"）：各投信官網公開、免登入的每日持股頁，
原件在 raw/pcf/<issuer>/，SHA 記在該資料夾 meta.json。目前涵蓋元大（ETF 持股頁內嵌狀態，以 node
在沙箱取出）、國泰（cwapi GetETFDetailStockList）、群益（CFWeb api/etf/buyback）、台新（Pcf 頁表格）。
constituent_codes() 合併兩種來源：有完整成分的 ETF 用完整成分，其餘 ETF 仍用 SITCA 前十大，並標明來源。
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import date as Date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW_DIR = ROOT / "data/etf-constituents/raw"
CLASS_KIND = {"AH11": "passive", "AL11": "active"}
AVAILABLE_DAY = 20

_ROW = re.compile(r"<tr>(.*?)</tr>", re.S)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_FUND = re.compile(r"<td[^>]*rowspan='\d+'[^>]*>(.*?)</td>", re.S)


@dataclass(frozen=True)
class Holding:
    fund: str
    kind: str          # passive | active
    rank: int
    asset_type: str    # 國內上市 / 國內上櫃 / ...
    code: str
    name: str
    weight_pct: float | None
    month: str         # YYYYMM


def parse_sitca(html: str, kind: str, month: str) -> list[Holding]:
    """解析 SITCA 月底持股明細表；每列帶基金名（rowspan 列延續上一個基金）。"""
    start = html.find("基金名稱")
    if start < 0:
        raise ValueError("SITCA 原件缺少持股明細表頭")
    holdings: list[Holding] = []
    fund: str | None = None
    for row in _ROW.findall(html[start:]):
        fund_match = _FUND.search(row)
        cells = [re.sub(r"<[^>]+>", "", c).strip() for c in _CELL.findall(row)]
        if fund_match:
            # 基金名後常接 <br>(配息來源說明)；只取第一段名稱
            fund = re.split(r"<br\s*/?>", fund_match.group(1))[0]
            fund = re.sub(r"<[^>]+>", "", fund).strip()
            cells = cells[1:]
        if fund is None or len(cells) < 9 or not cells[0].isdigit():
            continue
        rank, asset_type, code, name = int(cells[0]), cells[1], cells[2], cells[3]
        weight = cells[8].replace(",", "")
        holdings.append(Holding(fund, kind, rank, asset_type, code, name,
                                float(weight) if weight not in ("", "-") else None, month))
    if not holdings:
        raise ValueError("SITCA 原件沒有可解析的持股列")
    return holdings


def _verify(path: Path, meta: list[dict]) -> bytes:
    body = path.read_bytes()
    entry = next((m for m in meta if m["file"] == path.name), None)
    if entry is None or hashlib.sha256(body).hexdigest() != entry["sha256"]:
        raise ValueError(f"{path.name} 與 meta 的 SHA 不符或缺少紀錄")
    return body


def available_months(raw_dir: Path = RAW_DIR) -> list[str]:
    return sorted(p.name[len("sitca-"):-len(".meta.json")] for p in raw_dir.glob("sitca-*.meta.json"))


def _month_available_on(month: str) -> Date:
    year, mon = int(month[:4]), int(month[4:])
    year, mon = (year + 1, 1) if mon == 12 else (year, mon + 1)
    return Date(year, mon, AVAILABLE_DAY)


def load_month(month: str, raw_dir: Path = RAW_DIR) -> list[Holding]:
    meta = json.loads((raw_dir / f"sitca-{month}.meta.json").read_text(encoding="utf-8"))
    holdings: list[Holding] = []
    for cls, kind in CLASS_KIND.items():
        path = raw_dir / f"sitca-{month}-{cls}.html"
        if not path.exists():
            continue
        holdings.extend(parse_sitca(_verify(path, meta).decode("utf-8", "replace"), kind, month))
    return holdings


def constituents(as_of: str | Date, raw_dir: Path = RAW_DIR, kinds: tuple[str, ...] = ("passive", "active"),
                 domestic_only: bool = True) -> list[Holding]:
    """as_of 當下已可取得的最新月份，各 ETF 前十大持股。沒有可得月份回空清單。"""
    day = Date.fromisoformat(as_of) if isinstance(as_of, str) else as_of
    usable = [m for m in available_months(raw_dir) if _month_available_on(m) <= day]
    if not usable:
        return []
    rows = [h for h in load_month(usable[-1], raw_dir) if h.kind in kinds]
    if domestic_only:
        rows = [h for h in rows if h.asset_type in ("國內上市", "國內上櫃")]
    return rows


def is_constituent(code: str, as_of: str | Date, raw_dir: Path = RAW_DIR,
                   kinds: tuple[str, ...] = ("passive", "active")) -> bool:
    return any(h.code == str(code) for h in constituents(as_of, raw_dir, kinds))


# ---- 完整成分（pcf_full）----

@dataclass(frozen=True)
class PcfHolding:
    etf: str           # ETF 代號
    issuer: str
    code: str
    name: str
    weight_pct: float | None
    source: str = "pcf_full"


_YUANTA_NODE = r"""
const fs=require('fs'),vm=require('vm');const h=fs.readFileSync(0,'utf8');
const s=h.indexOf('window.__NUXT__=');if(s<0){process.exit(2)}
const c=h.slice(s,h.indexOf('</script>',s));const box={window:{}};vm.runInNewContext(c,box,{timeout:5000});
function f(o,d){if(!o||typeof o!=='object'||d>12)return null;if(o.weightData&&o.weightData.FundWeights)return o.weightData;
for(const v of Object.values(o)){const r=f(v,d+1);if(r)return r}return null}
const w=f(box.window.__NUXT__,0);if(!w){process.exit(3)}
process.stdout.write(JSON.stringify((w.FundWeights.StockWeights||[]).map(x=>[String(x.code),x.name,x.weights])));
"""
_TAISHIN_ROW = re.compile(r"<tr>\s*<td>(\S+?)(?:\s+TT)?</td>\s*<td>([^<]*)</td>\s*<td>[^<]*</td>\s*<td>([^<]*)</td>\s*</tr>")
_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)


def _html_stock_rows(html: str, code_col: int, name_col: int, weight_col: int) -> list[tuple[str, str, float | None]]:
    """伺服器渲染的持股表：取第 code_col 欄為股票代號（4 碼開頭）的列。"""
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        cells = [re.sub(r"<[^>]+>", "", c).replace("&nbsp;", "").strip() for c in _CELL.findall(row)]
        if len(cells) > max(code_col, name_col, weight_col) and re.fullmatch(r"\d{4}[0-9A-Z]{0,2}", cells[code_col]):
            out.append((cells[code_col], cells[name_col], _num(cells[weight_col])))
    return out


def _num(value) -> float | None:
    text = str(value).replace(",", "").replace("%", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def _parse_pcf(issuer: str, etf: str, body: bytes) -> list[tuple[str, str, float | None]]:
    if issuer == "cathay":
        rows = json.loads(body.decode("utf-8"))["result"] or []
        return [(r["stockCode"], r["stockName"], _num(r["weights"])) for r in rows]
    if issuer == "capital":
        rows = json.loads(body.decode("utf-8"))["data"]["stocks"]
        return [(r["stocNo"], r["stocName"], _num(r["weight"])) for r in rows]
    if issuer == "taishin":
        html = body.decode("utf-8", "replace")
        start = html.find("<th>代號</th>")
        if start < 0:
            raise ValueError(f"台新 {etf} 原件缺少股票表頭")
        table = html[start:html.find("</table>", start)]
        return [(c, n.strip(), _num(w)) for c, n, w in _TAISHIN_ROW.findall(table)]
    if issuer == "yuanta":
        out = subprocess.run(["node", "-e", _YUANTA_NODE], input=body, capture_output=True, timeout=60)
        if out.returncode != 0:
            raise ValueError(f"元大 {etf} 原件無法取出持股（node exit {out.returncode}）")
        return [(c, n, _num(w)) for c, n, w in json.loads(out.stdout.decode("utf-8"))]
    if issuer == "fubon":
        # Assets.aspx 股票表：股票代碼、股票名稱、股數、金額、權重(%)；已無持股（停止交易）的 ETF 回空表
        return _html_stock_rows(body.decode("utf-8", "replace"), 0, 1, 4)
    if issuer == "sinopac":
        # 永豐 PCF 頁股票表：股票代號、名稱、股數、權重
        return _html_stock_rows(body.decode("utf-8", "replace"), 0, 1, 3)
    if issuer == "mega":
        # 兆豐 trade_pcf.aspx 股票表：股票代號、股票名稱、股數、持股權重
        return _html_stock_rows(body.decode("utf-8", "replace"), 0, 1, 3)
    if issuer == "fsitc":
        rows = json.loads(json.loads(body.decode("utf-8"))["d"])
        return [(r["A"], r["B"], _num(r["C"])) for r in rows if r.get("group") == "1"]
    if issuer == "nomura":
        tables = json.loads(body.decode("utf-8"))["Entries"]["Data"]["Table"]
        rows = next((t.get("Rows") or [] for t in tables if t.get("TableTitle") == "股票"), [])
        return [(r[0], r[1], _num(r[3])) for r in rows]
    raise ValueError(f"未知的投信 {issuer}")


def load_pcf(raw_dir: Path = RAW_DIR) -> list[PcfHolding]:
    """讀取所有已存的完整成分原件（驗 SHA）。只回國內股票代號（4～6 碼數字開頭），不含期貨、現金。"""
    holdings: list[PcfHolding] = []
    for folder in sorted((raw_dir / "pcf").glob("*")):
        meta_path = folder / "meta.json"
        if not meta_path.exists():
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        for entry in meta:
            path = folder / entry["file"]
            etf = re.split(r"[-.]", entry["file"])[0]
            for code, name, weight in _parse_pcf(folder.name, etf, _verify(path, meta)):
                if re.fullmatch(r"\d{4}[0-9A-Z]{0,2}", code):
                    holdings.append(PcfHolding(etf, folder.name, code, name, weight))
    return holdings


def constituent_codes(as_of: str | Date, raw_dir: Path = RAW_DIR) -> dict[str, set[str]]:
    """代號 → 來源集合（pcf_full／sitca_top10）。有完整成分的 ETF 以完整成分為準；其餘 ETF 用 SITCA 前十大。
    完整成分是抓取當日快照（2026-11-06 前後），未依 as_of 回溯；as_of 只用來選 SITCA 月份。"""
    out: dict[str, set[str]] = {}
    full = load_pcf(raw_dir)
    full_etfs = {h.etf for h in full}
    for h in full:
        out.setdefault(h.code, set()).add("pcf_full")
    for h in constituents(as_of, raw_dir):
        etf_code = re.search(r"\((\w+)\)", h.fund)
        if etf_code and etf_code.group(1) in full_etfs:
            continue
        out.setdefault(h.code, set()).add("sitca_top10")
    return out
