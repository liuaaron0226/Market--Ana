"""U2：每日重建大盤頁資料包的薄入口（web/*-data.js → build.mjs 的 MARKET_DATA_DIR）。

大盤頁的五個資料檔：
- analysis-data.js：指數日K／週K／月K／60分K、法人、期貨、類股；由 logs/fetch_analysis_data.py 抓原件、
  logs/build_analysis_data.py 建置。兩支腳本把根目錄寫死成本機路徑，所以不直接改它們的計算邏輯，
  而是在工作目錄放一份「同樣相對結構」的副本，以模組方式載入後把 ROOT 換成工作目錄再執行。
- chips-data.js、detail-data.js：由 logs/build_chips_data.py、logs/build_stock_detail.py 建置，同樣換 ROOT 執行。
- dashboard-data.js（劇本）與 plain-data.js（白話分析）：本人 11-06 定案「白話分析的文字仍需人寫，自動化只更新數字」，
  這兩份是人審過的文字，每日流程**不重寫**，原樣沿用上一版；它們的 market_date／for_trading_day 會落後，
  由前端讀 /api/data-status 判斷是否過期，不在這裡偽造新日期。

輸出：工作目錄 <out>/ 下的五個 *-data.js；build.mjs 以環境變數 MARKET_DATA_DIR 指向它。web/ 不被寫入。
抓取沿用原腳本的限流（單路、≥3 秒、403/429 即停、不覆寫既有原件）；只補指定資料日缺的原件。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_REL = "data/market-snapshots/analysis-20261106-close"
FILES = ("analysis-data.js", "chips-data.js", "detail-data.js", "dashboard-data.js", "plain-data.js")
HUMAN_AUTHORED = ("dashboard-data.js", "plain-data.js")


HARDCODED_ROOT = 'ROOT = Path("C:/Users/User/Desktop/Market--Ana")'


def _compile(path: Path, root: Path):
    """讀 logs/ 下的腳本，只把寫死的 ROOT 換成 root（腳本檔案本身不改、計算邏輯不動）。"""
    source = path.read_text(encoding="utf-8")
    if HARDCODED_ROOT not in source:
        raise RuntimeError(f"{path.name} 的根目錄寫法變了，請更新 market_pack_daily")
    return compile(source.replace(HARDCODED_ROOT, f"ROOT = Path({str(root)!r})", 1), str(path), "exec")


def _module_namespace(path: Path) -> dict:
    """以非 __main__ 名稱建立模組並登記在 sys.modules（@dataclass 等需要從 sys.modules 找到自己的模組）。"""
    import types
    name = f"market_pack_daily_{path.stem}"
    module = types.ModuleType(name)
    module.__file__ = str(path)
    sys.modules[name] = module
    return module.__dict__


def _call_main(path: Path, root: Path, argv: list[str]) -> int:
    """有 main() 的腳本：以非 __main__ 名稱載入（不觸發底部的 SystemExit），再用指定參數呼叫 main()。"""
    namespace = _module_namespace(path)
    exec(_compile(path, root), namespace)  # noqa: S102 — 只執行本專案 logs/ 內已審過的建置腳本
    saved = sys.argv
    sys.argv = [str(path), *argv]
    try:
        return int(namespace["main"]() or 0)
    finally:
        sys.argv = saved


def _run_script(path: Path, root: Path) -> None:
    """沒有 main() 的腳本（模組層級直接執行）：載入即執行。"""
    exec(_compile(path, root), _module_namespace(path))  # noqa: S102


def build(day: str, out: Path, *, root: Path = ROOT, fetch: bool = True) -> dict:
    """在 root 底下補原件、建置三份數字資料檔，連同兩份人寫的文字檔放到 out。"""
    out.mkdir(parents=True, exist_ok=True)
    stage = root / ".tmp" / "market-pack-stage" / day
    if not stage.resolve().is_relative_to(root.resolve()):
        raise RuntimeError("stage 必須在 root 底下（建置腳本的輸出路徑相對 ROOT）")
    (stage / "web").mkdir(parents=True, exist_ok=True)
    report: dict = {"data_date": day, "files": {}}

    # 原件目錄沿用 root 的快照；只補新原件（腳本本身不覆寫既有檔）。
    if fetch:
        code = _call_main(root / "logs/fetch_analysis_data.py", root,
                          ["--phase", "all", "--cutoff", day, "--snapshot", SNAPSHOT_REL])
        if code != 0:
            raise RuntimeError(f"大盤原件抓取停止（exit {code}）；保留上一版資料包")
        report["fetch"] = "ok"

    # 建置腳本的 --output／--backtest-out 是「相對 ROOT」；stage 必須在 root 底下。
    analysis_out = stage / "web/analysis-data.js"
    backtest_out = stage / f"backtest-{day}.json"
    if backtest_out.exists():
        backtest_out.unlink()
    _call_main(root / "logs/build_analysis_data.py", root,
               ["--cutoff", day, "--snapshot", SNAPSHOT_REL,
                "--output", analysis_out.relative_to(root).as_posix(),
                "--backtest-out", backtest_out.relative_to(root).as_posix()])

    # chips／detail 腳本讀寫 ROOT/web/*：以 stage 當 ROOT，只動 stage。
    stage_root = _stage_root(root, stage)
    _run_script(root / "logs/build_chips_data.py", stage_root)
    _run_script(root / "logs/build_stock_detail.py", stage_root)

    for name in ("analysis-data.js", "chips-data.js", "detail-data.js"):
        shutil.copy2(stage / "web" / name, out / name)
        report["files"][name] = "rebuilt"
    for name in HUMAN_AUTHORED:
        shutil.copy2(root / "web" / name, out / name)
        report["files"][name] = "carried-over（人寫文字，不自動重寫）"
    report["analysis_cutoff"] = _analysis_cutoff(out / "analysis-data.js")
    if report["analysis_cutoff"] != day:
        raise RuntimeError(f"analysis-data.js 截止日 {report['analysis_cutoff']} 不是 {day}")
    return report


def _stage_root(root: Path, stage: Path) -> Path:
    """chips／detail 腳本以 ROOT/web 與 ROOT/data 為準：stage 下建 data 的連結（不複製 GB 級資料）。"""
    data_link = stage / "data"
    if not data_link.exists():
        try:
            data_link.symlink_to(root / "data", target_is_directory=True)
        except OSError:
            # Windows 沒有開發人員模式時不能建 symlink；只複製兩支腳本要讀的小目錄。
            for rel in ("data/market-snapshots/margin", "data/market-snapshots/stock-detail"):
                shutil.copytree(root / rel, stage / rel, dirs_exist_ok=True)
            for rel in ("stock-history/stock-history.sqlite",):
                target = stage / "data" / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(root / "data" / rel, target)
    for module in ("chip_streaks.py",):
        if not (stage / module).exists():
            shutil.copy2(root / module, stage / module)
    return stage


def _analysis_cutoff(path: Path) -> str:
    text = path.read_text(encoding="utf-8").strip()
    payload = json.loads(text[text.index("=") + 1:].strip().rstrip(";"))
    return payload["meta"]["cutoff_date"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="每日重建大盤頁資料包（寫到工作目錄，不改 web/）")
    parser.add_argument("--date", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--no-fetch", action="store_true", help="只用既有原件建置（重跑或離線驗證）")
    args = parser.parse_args(argv)
    date.fromisoformat(args.date)
    report = build(args.date, args.out.resolve(), fetch=not args.no_fetch)
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
