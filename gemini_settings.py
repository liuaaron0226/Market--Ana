"""排程開跑時讀站主在設定頁「AI 模型」選的模型與思考強度（本人 2026-11-19 晚）。

本人逐題定案（共用筆記 model-settings-decisions.md，待寫進 ANALYSIS_PLAN）：
- 存在網站資料庫（D1 ``ai_model_settings``，遷移 0030），每支排程開跑第一步用 wrangler 讀一次；
- 讀不到就用 gemini_models.py 的預設照跑，另外留一筆 ``ai_model_fallbacks`` 讓設定頁標「上一班沒讀到你的設定」；
  還沒選過（表是空的）不算讀不到；
- 大盤預測的晨間版跟前一晚同一組（forecast_run 自己照帳本處理），這裡只給「現在存的值」。

用法（排程裡，wrangler 讀完之後）：
  python gemini_settings.py --input "$RUNNER_TEMP/ai-models.json" --github-env "$GITHUB_ENV" \
      --task forecast-ledger --run-id "$GITHUB_RUN_ID" --fallback-sql "$RUNNER_TEMP/ai-model-fallback.sql"
寫進 GITHUB_ENV：每個任務 ``AI_<任務>_MODEL``、``AI_<任務>_EFFORT``，以及 ``AI_SETTINGS_SOURCE``（saved／unset／unreadable）。
一律以 0 結束：讀設定只是挑模型，不能讓原本的排程失敗。
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import gemini_models as gm

# 設定頁的五項（key 也是 D1 的 task 欄）→ gemini_models 裡的預設
TASKS = {
    "forecast": ("FORECAST", "FORECAST_EFFORT"),          # 晚間、晨間、歷史重播
    "watch_judge": ("WATCH_JUDGE", "WATCH_JUDGE_EFFORT"),
    "watch_nominate": ("WATCH_NOMINATE", "WATCH_NOMINATE_EFFORT"),
    "plain": ("PLAIN", "PLAIN_EFFORT"),                    # 首頁「籌碼與資金」白話
    "digest": ("DIGEST", "DIGEST_EFFORT"),                 # 站主 AI 解讀
}


def defaults() -> dict[str, dict[str, str]]:
    return {task: {"model": getattr(gm, model), "effort": getattr(gm, effort)} for task, (model, effort) in TASKS.items()}


def _rows(text: str | None) -> list[dict[str, Any]] | None:
    """wrangler --json 的 results；讀不懂就是 None。"""
    if not text or "[" not in text:
        return None
    try:
        payload = json.loads(text[text.index("["):])
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        return None
    results = payload[0].get("results")
    return results if isinstance(results, list) else None


def resolve(text: str | None) -> dict[str, Any]:
    rows = _rows(text)
    tasks = {task: {**choice, "from": "default"} for task, choice in defaults().items()}
    if rows is None:
        return {"source": "unreadable", "tasks": tasks}
    for row in rows:
        task = row.get("task") if isinstance(row, dict) else None
        if task in tasks and row.get("model") in gm.MODELS and row.get("effort") in gm.EFFORTS:
            tasks[task] = {"model": row["model"], "effort": row["effort"], "from": "saved"}
    saved = any(choice["from"] == "saved" for choice in tasks.values())
    return {"source": "saved" if saved else "unset", "tasks": tasks}


def env_lines(resolved: dict[str, Any]) -> list[str]:
    lines = []
    for task, choice in resolved["tasks"].items():
        lines += [f"AI_{task.upper()}_MODEL={choice['model']}", f"AI_{task.upper()}_EFFORT={choice['effort']}"]
    return lines + [f"AI_SETTINGS_SOURCE={resolved['source']}"]


def _literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def fallback_sql(task: str, run_id: str, at: str) -> str:
    return (f"INSERT INTO ai_model_fallbacks (task, run_id, at) VALUES ({_literal(task)}, {_literal(run_id)}, {_literal(at)}) "
            "ON CONFLICT (task, run_id) DO NOTHING;\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", required=True, help="wrangler d1 execute --json 的輸出；檔案不在＝讀不到")
    parser.add_argument("--github-env", required=True)
    parser.add_argument("--task", required=True, help="排程檔名（記「沒讀到」時用）")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--fallback-sql", required=True, help="讀不到時寫一筆 ai_model_fallbacks；讀到時是空檔")
    args = parser.parse_args(argv)
    try:
        text = Path(args.input).read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = None
    resolved = resolve(text)
    with open(args.github_env, "a", encoding="utf-8", newline="\n") as handle:
        handle.write("".join(line + "\n" for line in env_lines(resolved)))
    note = ""
    if resolved["source"] == "unreadable":
        note = fallback_sql(args.task, args.run_id, datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        print("::warning::讀不到設定頁的 AI 模型設定，這一班用 gemini_models.py 的預設")
    Path(args.fallback_sql).write_text(note, encoding="utf-8", newline="\n")
    for task, choice in resolved["tasks"].items():
        print(f"{task}: {choice['model']} {choice['effort']}（{'設定頁' if choice['from'] == 'saved' else '預設'}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
