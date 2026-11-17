"""U2：每日重建大盤頁資料包的薄入口（web/*-data.js → build.mjs 的 MARKET_DATA_DIR）。

大盤頁的五個資料檔：
- analysis-data.js：指數日K／週K／月K／60分K、法人、期貨、類股；由 logs/fetch_analysis_data.py 抓原件、
  logs/build_analysis_data.py 建置。兩支腳本把根目錄寫死成本機路徑，所以不直接改它們的計算邏輯，
  而是在工作目錄放一份「同樣相對結構」的副本，以模組方式載入後把 ROOT 換成工作目錄再執行。
- chips-data.js、detail-data.js：由 logs/build_chips_data.py、logs/build_stock_detail.py 建置，同樣換 ROOT 執行。
- dashboard-data.js（劇本）與 plain-data.js（白話分析）：先原樣沿用上一版人寫的那份（本人 11-06 定案「文字人寫」）。
  它們的日期會落後，由前端依資料日判斷是否過期，不在這裡偽造新日期。
- plain-data.js 例外（本人 11-17 定案，取代 11-06）：開關 MARKET_ANA_PLAIN_DAILY＝on 時，截止日核對過之後
  由 plain_daily.py 依當天數字重寫（Gemini 寫、程式核對數字；不過就改程式套句），標明資料日與來源。
  dry＝照樣呼叫並核對，但只寫進執行紀錄 data/daily-update-log/plain-daily/<資料日>.json，網站沿用舊的；
  off（預設）＝完全不呼叫。workflow 以環境變數 PLAIN_DAILY_MODE／PLAIN_AGY_BIN／PLAIN_AGY_HOME 傳進來。

輸出：工作目錄 <out>/ 下的五個 *-data.js；build.mjs 以環境變數 MARKET_DATA_DIR 指向它。web/ 不被寫入。
抓取沿用原腳本的限流（單路、≥3 秒、403/429 即停、不覆寫既有原件）；只補指定資料日缺的原件。
"""
from __future__ import annotations

import argparse
import json
import os
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
    # 首頁籌碼卡的名單對照原件檢查（11-17）：不擋發布，結果由 daily_update 放進站台狀態的 checks.chip_detail
    # （daily_update 讀這支程式最後一行印出的 report JSON 取 detail_check，main() 要維持最後才印 report）
    report["detail_check"] = _safe_check_detail(stage / "web/detail-data.js", root / "data/market-snapshots/stock-detail")

    for name in ("analysis-data.js", "chips-data.js", "detail-data.js"):
        shutil.copy2(stage / "web" / name, out / name)
        report["files"][name] = "rebuilt"
    for name in HUMAN_AUTHORED:
        shutil.copy2(root / "web" / name, out / name)
        report["files"][name] = "carried-over（人寫文字，不自動重寫）"
    if _attach_market_trend(out / "analysis-data.js", out / "dashboard-data.js"):
        report["files"]["dashboard-data.js"] += "＋computed market_trend"
    else:
        report["market_trend"] = "unavailable（未驗證趨勢當日算不出來，核心資料照常發布）"
    report["analysis_cutoff"] = _analysis_cutoff(out / "analysis-data.js")
    if report["analysis_cutoff"] != day:
        raise RuntimeError(f"analysis-data.js 截止日 {report['analysis_cutoff']} 不是 {day}")
    # 截止日對了才重寫白話分析（不對就整包改用上一版，模型這一次也不用呼叫）
    mode, caller = _plain_settings(os.environ)
    report["plain_daily"] = _apply_plain(out, day, mode=mode, call_model=caller,
                                         audit_dir=root / "data/daily-update-log/plain-daily")
    if report["plain_daily"].get("published"):
        report["files"]["plain-data.js"] = f"rewritten（{report['plain_daily']['source']}）"
    return report


def _plain_settings(environ) -> tuple[str, object]:
    """workflow 傳進來的白話分析模式與 agy 位置（沒裝好就是 agy_unavailable，交給 produce 改用程式套句）。"""
    plain_daily = _plain_module()
    mode = plain_daily.normalize_mode(environ.get("PLAIN_DAILY_MODE"))
    caller = plain_daily.agy_caller(environ.get("PLAIN_AGY_BIN") or None, home=environ.get("PLAIN_AGY_HOME") or None)
    return mode, caller


def _plain_module():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import plain_daily
    return plain_daily


def _apply_plain(out: Path, day: str, *, mode: str, call_model, audit_dir: Path) -> dict:
    """依模式處理 out/plain-data.js；永遠不丟例外（白話分析出錯不能讓整包大盤資料改用上一版）。
    回傳的摘要會印在 report 最後一行，所以不放全文；全文與核對結果寫在 audit_dir/<資料日>.json。"""
    plain_daily = _plain_module()
    mode = plain_daily.normalize_mode(mode)
    if mode == "off":
        return {"mode": "off"}
    try:
        analysis = _read_assignment(out / "analysis-data.js", "window.MARKET_ANALYSIS = ")
        chips = _read_assignment(out / "chips-data.js", "window.MARKET_CHIPS = ")
        plain, audit = plain_daily.produce(analysis, chips, mode=mode, call_model=call_model)
    except Exception as error:  # noqa: BLE001
        plain, audit = None, {"mode": mode, "source": None, "failure": f"read_failed: {type(error).__name__}", "rejected": []}
    if plain is not None:
        plain_daily.write_plain(out / "plain-data.js", plain)
    try:
        audit_dir.mkdir(parents=True, exist_ok=True)
        (audit_dir / f"{day}.json").write_text(json.dumps({"data_date": day, **audit, "published": plain},
                                                          ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    return {"mode": mode, "source": audit.get("source"), "failure": audit.get("failure"),
            "rejected": len(audit.get("rejected") or []), "published": plain is not None}


# 首頁六張籌碼卡點開的名單（11-17 本人回報最近幾天都「這一天沒有資料」，而且沒有任何檢查擋下）。
# （卡名, detail current.dates 的鍵, 名單欄位, 該有名單的日子看哪幾份原件, 要不要靠每日收盤行情換算或排序）；
# 外資期貨空單那張卡列的是外資賣超，跟外資一起檢查，所以這裡是五列。
DETAIL_CARDS = (
    ("外資", "foreign", ("foreign_buy", "foreign_sell"), ("t86",), True),
    ("投信", "trust", ("trust_buy", "trust_sell"), ("t86",), True),
    ("成交金額", "amount", ("turnover_top",), ("t86", "quotes"), True),
    ("融資", "margin", ("margin_up", "margin_down"), ("margin",), False),
    ("融券", "short", ("short_up", "short_down"), ("margin",), False),
)


def _source_days(source: Path, dataset: str) -> set[str]:
    """近期明細某一份原件有哪幾天（ISO 日期）。"""
    compacts = (path.stem.split("-")[-1] for path in (source / dataset).glob(f"{dataset}-*.json"))
    return {f"{c[:4]}-{c[4:6]}-{c[6:]}" for c in compacts}


def check_detail(detail: dict, source: Path) -> dict:
    """對照近期明細原件（source＝data/market-snapshots/stock-detail）檢查 detail-data.js：
    每張卡在每一個有原件的日子，名單都要有人；最新那一天的「目前前 10」也要有人、日期要對得上。
    不擋發布，結果進站台狀態。"""
    by_date, current = detail.get("by_date") or {}, detail.get("current") or {}
    dates = current.get("dates") or {}
    quote_days = _source_days(source, "quotes")
    problems: list[str] = []
    latest_days: list[str] = []
    for card, key, lists, datasets, priced in DETAIL_CARDS:
        days = sorted(set().union(*(_source_days(source, dataset) for dataset in datasets)))
        if not days:
            problems.append(f"{card}：沒有任何原件")
            continue
        latest = days[-1]
        latest_days.append(latest)
        empty = [day for day in days if not any((by_date.get(day) or {}).get(name) for name in lists)]
        wrong = [f"{'、'.join(empty)} 的名單是空的"] if empty else []
        if dates.get(key) != latest:
            wrong.append(f"目前前 10 停在 {dates.get(key)}，不是 {latest}")
        elif not any(current.get(name) for name in lists):
            wrong.append("目前前 10 是空的")
        unpriced = [day for day in (empty or ([latest] if wrong else [])) if day not in quote_days]
        if priced and unpriced:
            wrong.append(f"缺 {'、'.join(unpriced)} 的每日收盤行情原件")
        if wrong:
            problems.append(f"{card}：{'、'.join(wrong)}")
    return {"status": "failed" if problems else "ok", "data_date": max(latest_days, default=None),
            "reason": "；".join(problems) or None}


def _safe_check_detail(detail_js: Path, source: Path) -> dict:
    """檢查本身出錯（檔案讀不起來等）也只回報失敗：不能因為檢查讓整包大盤資料改用上一版。"""
    try:
        return check_detail(_read_assignment(detail_js, "window.MARKET_DETAIL = "), source)
    except Exception as error:  # noqa: BLE001 — 檢查不擋發布
        return {"status": "failed", "data_date": None, "reason": f"檢查本身出錯：{type(error).__name__}: {error}"}


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


def _read_assignment(path: Path, prefix: str) -> dict:
    text = path.read_text(encoding="utf-8").strip()
    if not text.startswith(prefix):
        raise RuntimeError(f"{path.name} 缺少 {prefix}")
    value = json.loads(text[len(prefix):].rstrip(";"))
    if not isinstance(value, dict):
        raise RuntimeError(f"{path.name} 最外層不是物件")
    return value


def _write_assignment(path: Path, prefix: str, payload: dict) -> None:
    path.write_text(prefix + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + ";\n", encoding="utf-8")


def _attach_market_trend(analysis_path: Path, dashboard_path: Path) -> bool:
    """只改每日輸出副本：把純數字趨勢放進雲端既有會保留的 actions，不改人寫日期與文字。

    market-trend/v1 尚未通過驗證、對外預設關閉；當日沒有合格趨勢就原樣保留 dashboard 並回 False，
    不擋核心大盤資料發布（本人 11-12「先脫鉤再上線」）。
    """
    analysis = _read_assignment(analysis_path, "window.MARKET_ANALYSIS = ")
    trend = analysis.get("market_trend")
    if not isinstance(trend, dict) or trend.get("schema") != "market-trend/v1":
        return False
    dashboard = _read_assignment(dashboard_path, "window.MARKET_DASHBOARD = ")
    actions = dashboard.get("actions")
    if actions is None:
        actions = {}
    if not isinstance(actions, dict):
        raise RuntimeError("dashboard-data.js 的 actions 不是物件")
    dashboard["actions"] = {**actions, "market_trend": trend}
    _write_assignment(dashboard_path, "window.MARKET_DASHBOARD = ", dashboard)
    return True


def _analysis_cutoff(path: Path) -> str:
    return _read_assignment(path, "window.MARKET_ANALYSIS = ")["meta"]["cutoff_date"]


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
