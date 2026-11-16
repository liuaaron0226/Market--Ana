"""事前預測的「歷史重播（參考）」：拿模型知識截止日之後的過去交易日，照正式的方法重寫晚間版與晨間版。

本人 2026-11-17 定案（ANALYSIS_PLAN.md〈事前預測加做「歷史重播」〉）：
- 只重播目前所用模型知識截止日之後、而且當時的資料都取得得到的交易日；晚間版、晨間版各自重播。
- 重播用的資料只能是那一天當時看得到的；成績和正式、試跑分開存、分開顯示，標「歷史重播（參考）」，
  不計入正式結論（帳本裡 ``run_mode`` 是 replay，``summarize`` 永遠不判定）。
- 先回報本人可重播幾天、要用掉幾次 Gemini、缺哪些日子，答案回來才跑（``plan_replay`` 只算、不呼叫模型）。

「當時看得到」怎麼做到：
- 日 K、法人、期貨截到基準日（``forecast_run.trim_to_day``）；外資層與海外層本來就依基準日（晨間依開盤日）切。
- 相似盤勢與候選關卡只用截到基準日的日 K；教訓層只用到期日不晚於基準日的對帳（``forecast_experience.lessons``）。
- 晚間版的時鐘停在基準日 17:30、晨間版停在開盤日 07:30，期限規則和正式一樣。
- 方法稿本身寫了某段行情的實際結果（例如 0.4 的〈逐日對照〉），到期日落在那段的預測等於先看過答案，不重播。
- 模型知識截止日以前的日子不重播（模型可能在訓練資料裡看過之後的走勢）。

重播寫在自己的帳本（R2 的 ``forecast-replay/`` 前綴），不碰正式帳本。依日期先後一天一天寫，寫之前先對帳，
所以後面的日子看得到前面已到期的結果，和正式帳本累積教訓的方式一樣。模型呼叫失敗（多半是額度用完）不鎖、
整次停下，免得把額度用光害正式的預測漏記；之後再跑會從沒寫的日子接著寫，已寫的不再呼叫模型。
"""
from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import forecast_ledger as fl
import forecast_run as fr
import forecast_store
import overseas_inputs as oi
from ledger_core import method_version_of

SCHEMA = "forecast-replay/v1"
STORE_PREFIX = forecast_store.REPLAY_PREFIX

# 模型的知識截止日：從這一天（含）起的交易日才重播。要有官方文件出處；換模型要先查證再加。
MODEL_KNOWLEDGE = {
    # DeepMind 模型卡〈Known Limitations〉：「The knowledge cutoff date for Gemini 3.8 Flash is March 2026」
    # https://deepmind.google/models/model-cards/gemini-3-8-flash/（2026-09-02 發布；2026-11-17 F 票查證）
    "gemini-3.8-flash": {"cutoff": "2026-03", "replay_from": "2026-04-01",
                         "source": "https://deepmind.google/models/model-cards/gemini-3-8-flash/"},
}

# 方法稿寫了實際結果的那段行情（含頭尾）：到期日落在這段、而且基準日在這段結束之前的預測不重播。
# 換方法稿版本要先讀過、確認它寫了哪段行情再加；沒寫任何行情結果的版本填 None。
METHOD_SPOILERS = {
    # 0.4〈逐日對照〉：第一頻道 10-12 至 11-12、第二頻道 10-21 至 11-13 的判斷與之後的實際走勢。
    "0.4": ("2026-10-12", "2026-11-13"),
}

SKIP_BEFORE_CUTOFF = "模型知識截止日（{cutoff}）以前"
SKIP_NO_CHIPS = "沒有當天的法人或期貨資料"
SKIP_SPOILER = "方法稿已寫了這段行情的結果"
SKIP_NO_SESSION = "還沒有下一個開盤日（晨間版不重播）"

REPLAY_CLOCK = fr.REPLAY_CLOCK
MORNING_CLOCK = fr.MORNING_REPLAY_CLOCK
LONGEST = max(fl.HORIZONS)


def _model_knowledge(model: str) -> Mapping[str, str]:
    if model not in MODEL_KNOWLEDGE:
        raise ValueError(f"{model} 的知識截止日還沒有用官方文件查證，不能重播")
    return MODEL_KNOWLEDGE[model]


def _spoilers(method_text: str) -> tuple[str, str] | None:
    version = method_version_of(method_text)
    if version not in METHOD_SPOILERS:
        raise ValueError(f"方法稿 {version} 寫了哪段行情的結果還沒確認，不能重播")
    return METHOD_SPOILERS[version]


def plan_replay(analysis: Mapping[str, Any], *, method_text: str, model: str, start: str, end: str,
                editions: Sequence[str] = fl.EDITIONS) -> dict[str, Any]:
    """只算、不呼叫模型：哪些基準日可以重播、哪些不行（依原因分組），以及要寫幾份、呼叫模型幾次。"""
    knowledge = _model_knowledge(model)
    spoilers = _spoilers(method_text)
    dates = [row["date"] for row in analysis.get("daily") or []]
    chips = ({row["date"] for row in analysis.get("institutional") or []}
             & {row["date"] for row in analysis.get("futures") or []})
    replayable: list[str] = []
    skipped: dict[str, list[str]] = {}
    for index, day in enumerate(dates):
        if not start <= day <= end:
            continue
        reason = None
        if day < knowledge["replay_from"]:
            reason = SKIP_BEFORE_CUTOFF.format(cutoff=knowledge["cutoff"])
        elif day not in chips:
            reason = SKIP_NO_CHIPS
        elif spoilers is not None:
            spoil_from, spoil_to = spoilers
            # 二十日格的到期日還不知道（日 K 不夠長）時，當成落在那段裡，寧可少重播。
            expiry = dates[index + LONGEST] if index + LONGEST < len(dates) else None
            if day < spoil_to and (expiry is None or expiry >= spoil_from):
                reason = SKIP_SPOILER
        if reason:
            skipped.setdefault(reason, []).append(day)
        else:
            replayable.append(day)
    forecasts = sum(1 for day in replayable for edition in editions
                    if edition == "evening" or dates.index(day) + 1 < len(dates))
    no_session = [day for day in replayable if "morning" in editions and dates.index(day) + 1 >= len(dates)]
    if no_session:
        skipped[SKIP_NO_SESSION] = no_session
    return {"dates": replayable, "skipped": skipped, "forecasts": forecasts,
            "model_calls": {"at_least": forecasts, "at_most": 2 * forecasts},
            "model_knowledge": dict(knowledge), "method_spoilers": list(spoilers) if spoilers else None}


def _clock(day: str, time: str):
    frozen = datetime.fromisoformat(f"{day}T{time}").replace(tzinfo=fl.TAIPEI)
    return lambda: frozen


def run_replay(
    analysis_path: Path | str,
    *,
    ledger: Path | str,
    method_path: Path | str,
    model_bin: Path | str,
    model: str = fr.MODEL_DEFAULT,
    start: str,
    end: str,
    fetched_at: str,
    editions: Sequence[str] = fl.EDITIONS,
    max_forecasts: int | None = None,
    overseas_dir: Path | str | None = None,
    data_root: Path | str | None = None,
    seeds_root: Path | str = fr.SEEDS_ROOT,
    sql_output: Path | str | None = None,
    log_output: Path | str | None = None,
    timeout_seconds: float = 360,
) -> dict[str, Any]:
    """依日期先後重播；``max_forecasts`` 是這一次最多新寫幾份（已寫過的不算、也不再呼叫模型）。

    ``fetched_at``：這次重播的資料是什麼時候抓的（記進每一筆，說明事後修正過的數字可能混在裡面）。
    """
    analysis = fr.load_analysis(analysis_path)
    method_text = Path(method_path).read_text(encoding="utf-8")
    plan = plan_replay(analysis, method_text=method_text, model=model, start=start, end=end, editions=editions)
    meta = {"model_knowledge_cutoff": plan["model_knowledge"]["cutoff"],
            "model_knowledge_source": plan["model_knowledge"]["source"],
            "method_spoilers": plan["method_spoilers"], "fetched_at": fetched_at,
            "note": "歷史重播（參考）：用重播當時抓得到的資料，少數數字可能是事後修正過的版本；不計入正式結論。"}
    dates = [row["date"] for row in analysis.get("daily") or []]
    root = Path(ledger)
    foreign_sources = fr.load_foreign_sources(data_root, seeds_root)
    sources = oi.archive_sources(overseas_dir, root) if overseas_dir is not None else None
    log: dict[str, Any] = {"schema": SCHEMA, "model": model, "start": start, "end": end, "editions": list(editions),
                           "plan": {key: plan[key] for key in ("forecasts", "model_calls", "skipped")},
                           "replayable": len(plan["dates"]), "written": 0, "unchanged": 0, "not_ready": [],
                           "conflicts": [], "misses": [], "stop_reason": None}

    for day in plan["dates"]:
        if log["stop_reason"]:
            break
        trimmed = fr.trim_to_day(analysis, day)
        fl.reconcile(root, trimmed["daily"])  # 只對到基準日為止已到期的格子
        for edition in editions:
            session = None
            if edition == "morning":
                position = dates.index(day) + 1
                if position >= len(dates):
                    continue
                session = dates[position]
            if max_forecasts is not None and log["written"] >= max_forecasts:
                log["stop_reason"] = "到了這次的份數上限"
                break
            layer = (oi.build_layer(overseas_dir, edition=edition, base_date=day, session_date=session)
                     if overseas_dir is not None else None)
            data = fr.build_day_input(trimmed, method_text, layer, foreign_sources, edition=edition,
                                      session_date=session)
            if sources is not None:
                data["sources"] = sources
            clock = _clock(session, MORNING_CLOCK) if session else _clock(day, REPLAY_CLOCK)
            result = fl.write_forecast(root, data, edition=edition, model_bin=model_bin, model=model, now=clock,
                                       session_date=session, timeout_seconds=timeout_seconds, replay=meta)
            outcome = result["outcome"]
            if outcome == "model_failed":
                log["stop_reason"] = f"模型呼叫失敗（{day} {edition}）：{result.get('detail')}"
                break
            if outcome == "written":
                log["written"] += 1
                if result["record"]["kind"] == "miss":
                    log["misses"].append({"base_date": day, "edition": edition,
                                          "reason": result["record"].get("miss_reason")})
            elif outcome == "unchanged":
                log["unchanged"] += 1
            elif outcome == "conflict":
                log["conflicts"].append({"base_date": day, "edition": edition})
            else:
                log["not_ready"].append({"base_date": day, "edition": edition, "detail": result.get("detail")})

    daily = analysis.get("daily") or []
    log["reconciliation"] = {key: len(value) for key, value in fl.reconcile(root, daily).items()}
    if sql_output:
        Path(sql_output).parent.mkdir(parents=True, exist_ok=True)
        Path(sql_output).write_text(fl.d1_sql(root, daily, published_at=fetched_at, replay=True), encoding="utf-8")
    if log_output:
        Path(log_output).parent.mkdir(parents=True, exist_ok=True)
        Path(log_output).write_text(json.dumps(log, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return log


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="事前預測的歷史重播（參考）：先 --plan 算天數，本人同意後才真的跑")
    parser.add_argument("--analysis", type=Path, required=True, help="已發布的 analysis-data.js")
    parser.add_argument("--method", type=Path, required=True)
    parser.add_argument("--model", default=fr.MODEL_DEFAULT)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--plan", action="store_true", help="只算可重播的日子與模型呼叫次數，不呼叫模型")
    parser.add_argument("--ledger", type=Path, help="重播帳本（不可以是正式帳本）")
    parser.add_argument("--agy-bin", type=Path)
    parser.add_argument("--fetched-at", help="這次資料抓取的時間（ISO 8601）")
    parser.add_argument("--max-forecasts", type=int)
    parser.add_argument("--edition", action="append", choices=list(fl.EDITIONS))
    parser.add_argument("--overseas-dir", type=Path)
    parser.add_argument("--data-root", type=Path, default=fr.DATA_ROOT)
    parser.add_argument("--sql-output", type=Path)
    parser.add_argument("--log-output", type=Path)
    args = parser.parse_args(argv)
    editions = tuple(args.edition or fl.EDITIONS)
    if args.plan:
        plan = plan_replay(fr.load_analysis(args.analysis), method_text=args.method.read_text(encoding="utf-8"),
                           model=args.model, start=args.start, end=args.end, editions=editions)
        summary = {"replayable": len(plan["dates"]), "from": plan["dates"][0] if plan["dates"] else None,
                   "to": plan["dates"][-1] if plan["dates"] else None, "forecasts": plan["forecasts"],
                   "model_calls": plan["model_calls"],
                   "skipped": {reason: {"days": len(days), "from": days[0], "to": days[-1]}
                               for reason, days in plan["skipped"].items()}}
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    if not (args.ledger and args.agy_bin and args.fetched_at):
        parser.error("真的重播要給 --ledger、--agy-bin、--fetched-at")
    log = run_replay(args.analysis, ledger=args.ledger, method_path=args.method, model_bin=args.agy_bin,
                     model=args.model, start=args.start, end=args.end, fetched_at=args.fetched_at, editions=editions,
                     max_forecasts=args.max_forecasts, overseas_dir=args.overseas_dir, data_root=args.data_root,
                     sql_output=args.sql_output, log_output=args.log_output)
    print(json.dumps({key: log[key] for key in ("replayable", "written", "unchanged", "misses", "stop_reason",
                                                "reconciliation")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
