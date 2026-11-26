"""排程開跑時讀站主在設定頁「AI 模型」選的模型與思考強度（本人 2026-11-19 晚）。

本人逐題定案（共用筆記 model-settings-decisions.md，待寫進 ANALYSIS_PLAN）：
- 存在網站資料庫（D1 ``ai_model_settings``，遷移 0030），每支排程開跑第一步用 wrangler 讀一次；
- 讀不到就用 gemini_models.py 的預設照跑。每一班都在 ``ai_model_reads`` 記一筆讀取結果（saved／unset／unreadable），
  設定頁看每支排程最近那一筆，是 unreadable 才標「上一班沒讀到你的設定」；還沒選過（表是空的）不算讀不到；
- 大盤預測的晨間版跟前一晚同一組（forecast_run 自己照帳本處理），這裡只給「現在存的值」。

用法（排程裡，wrangler 讀完之後）：
  python gemini_settings.py --input "$RUNNER_TEMP/ai-models.json" --github-env "$GITHUB_ENV" \
      --task forecast-ledger --run-id "$GITHUB_RUN_ID" --read-sql "$RUNNER_TEMP/ai-model-read.sql"
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
from gemini_catalog import ID, EFFORTS, load_catalog, supports, now

# 設定頁的五項（key 也是 D1 的 task 欄）→ gemini_models 裡的預設
TASKS = {
    "forecast": ("FORECAST", "FORECAST_EFFORT"),          # 晚間、晨間、歷史重播
    "watch_judge": ("WATCH_JUDGE", "WATCH_JUDGE_EFFORT"),
    "watch_nominate": ("WATCH_NOMINATE", "WATCH_NOMINATE_EFFORT"),
    "plain": ("PLAIN", "PLAIN_EFFORT"),                    # 首頁「籌碼與資金」白話
    "digest": ("DIGEST", "DIGEST_EFFORT"),                 # 站主 AI 解讀
    "news": ("NEWS", "NEWS_EFFORT"),                       # 首頁「消息」（本人 11-19，market-news.yml）
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


def resolve(text: str | None, *, catalog=None) -> dict[str, Any]:
    rows = _rows(text)
    tasks = {task: {**choice, "from": "default"} for task, choice in defaults().items()}
    if rows is None:
        return {"source": "unreadable", "tasks": tasks}
    for row in rows:
        task = row.get("task") if isinstance(row, dict) else None
        # Keep a syntactically valid former selection even when it is absent from today's list.
        # Only prepare_choices (with a successful official listing) may replace it for execution.
        if task in tasks and isinstance(row.get('model'), str) and ID.fullmatch(row['model']) and row.get('effort') in EFFORTS:
            tasks[task] = {"model": row["model"], "effort": row["effort"], "from": "saved"}
    saved = any(choice["from"] == "saved" for choice in tasks.values())
    return {"source": "saved" if saved else "unset", "tasks": tasks}


class ModelUnavailable(RuntimeError):
    def __init__(self, message, resolutions):
        super().__init__(message)
        self.resolutions = resolutions


def prepare_choices(resolved, catalog, tasks, *, probe, previous=None):
    """Resolve only tasks that this run will use. Never write the requested D1 values.

    An active choice is untouched. A retired choice tries only same-effort candidates
    in official order. Only explicit unsupported errors permit trying another;
    quota/auth/network errors stop the run, not the selection policy.
    """
    result = {**resolved, 'tasks': {k: dict(v) for k, v in resolved['tasks'].items()}, 'resolutions': {}}
    for task in tasks:
        requested = {k: resolved['tasks'][task][k] for k in ('model', 'effort')}
        record = {'requested': requested, 'actual': requested, 'state': 'selected', 'checked_at': now()}
        result['resolutions'][task] = record
        if supports(catalog, **requested):
            continue
        record.update(state='failed', actual=None, reason='officially_unavailable')
        candidates = [(m['id'], requested['effort']) for m in catalog['models'] if requested['effort'] in m['efforts']]
        # Once a fallback has been selected, a newer model must not silently upgrade it either.
        old = (previous or {}).get(task, {})
        actual = old.get('actual') if old.get('state') == 'replaced' else old.get('last_actual')
        if old.get('requested') == requested and actual and actual.get('effort') == requested['effort'] and supports(catalog, **actual):
            candidates = [(actual['model'], actual['effort'])] + candidates
        for model, effort in dict.fromkeys(candidates):
            status = probe(model, effort)
            if status == 'unsupported':
                continue
            if status != 'ok':
                record['reason'] = 'availability_check_failed'
                raise ModelUnavailable('替代模型檢查失敗，未變更手動設定。', result['resolutions'])
            record.update(state='replaced', actual={'model': model, 'effort': effort})
            result['tasks'][task].update(model=model, effort=effort)
            break
        else:
            raise ModelUnavailable('沒有通過檢查的 Gemini 替代模型。', result['resolutions'])
    return result


def env_lines(resolved: dict[str, Any], *, tasks=None) -> list[str]:
    lines = []
    for task, choice in resolved["tasks"].items():
        if tasks is not None and task not in tasks:
            continue
        lines += [f"AI_{task.upper()}_MODEL={choice['model']}", f"AI_{task.upper()}_EFFORT={choice['effort']}"]
    return lines + [f"AI_SETTINGS_SOURCE={resolved['source']}"]


def _literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def read_sql(task: str, run_id: str, at: str, source: str) -> str:
    return ("INSERT INTO ai_model_reads (task, run_id, read_at, source) "
            f"VALUES ({_literal(task)}, {_literal(run_id)}, {_literal(at)}, {_literal(source)}) "
            "ON CONFLICT (task, run_id) DO UPDATE SET read_at = excluded.read_at, source = excluded.source;\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", required=True, help="wrangler d1 execute --json 的輸出；檔案不在＝讀不到")
    parser.add_argument("--github-env", required=True)
    parser.add_argument("--task", required=True, help="排程檔名（記「沒讀到」時用）")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--read-sql", required=True, help="這一班的讀取結果（ai_model_reads 一筆），排程記用量時一起寫")
    args = parser.parse_args(argv)
    try:
        text = Path(args.input).read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = None
    resolved = resolve(text)
    with open(args.github_env, "a", encoding="utf-8", newline="\n") as handle:
        handle.write("".join(line + "\n" for line in env_lines(resolved)))
    if resolved["source"] == "unreadable":
        print("::warning::讀不到設定頁的 AI 模型設定，這一班用 gemini_models.py 的預設")
    at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    Path(args.read_sql).write_text(read_sql(args.task, args.run_id, at, resolved["source"]), encoding="utf-8", newline="\n")
    for task, choice in resolved["tasks"].items():
        print(f"{task}: {choice['model']} {choice['effort']}（{'設定頁' if choice['from'] == 'saved' else '預設'}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
