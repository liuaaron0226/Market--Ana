"""盤勢解讀：一班一份、分類別的解讀，存進 D1 ``market_brief_runs``（本人 2026-11-23「共識達成」，.scratch/market-brief）。

資料約定在 .scratch/market-brief/contract.md（D1 表、payload ``market-brief/v1``、命令列）。票 02 的範圍：
- 時段四個都收（morning／midday／close／evening），目前只有 evening 排進每日更新；其他時段先做同一件事。
- 類別兩個：chips（籌碼面）、tech（技術面）。之後的票加 overseas／news／tracking 時，在 ``CATEGORIES`` 加一列
  （主題、資料日、方向核對、提示片段、程式套句），再把 key 放進 ``SLOT_CATEGORIES`` 那一班。
- 事實沿用 ``plain_daily.build_facts``（同一張事實表、同一套數字排版）；模型只寫句子，數字一律寫 ``{代號}``，
  程式核對後代入。文字檢查沿用 ``plain_daily._text_problem``（數字、禁用字樣、買賣指令、準備要「如果…就…」），
  另加兩項：每一類只能代入自己主題的事實；結論與各類的判斷不可和當天數字的方向相反。
- 回答不合格：帶著原因重問一次（``ledger_core.retry_note``）；還是不合格、或模型沒回來 → 程式套句（source ``template``）。
- 圖的數字不在這一份裡：前端用 ``/api/market/overview`` 已有的序列畫。

用法（daily-update.yml，大盤資料包發布成功之後）：
  python market_brief.py run --slot evening --analysis <last-market-pack>/analysis-data.js --sql-output <檔> \
      [--chips <chips-data.js>] [--date YYYY-MM-DD] [--dry-run] [--agy-bin … --agy-home …] [--report <不公開的 JSON>]
  python market_brief.py usage --report <run 印出來的那一份> --run-id <排程編號> [--agy-bin …] --sql-output <檔>
``--chips`` 不給就讀 ``--analysis`` 同一個資料夾的 chips-data.js。``--dry-run`` 照樣呼叫模型與核對，但 SQL 檔是空的。
印到 stdout 的是遮過的報告（狀態、時段、來源、問了幾次、類別、模型；沒有模型寫的任何一句），完整的只寫到 ``--report``。
兩個子命令一律以 0 結束：這一份寫不成，網站沿用上一班（或原本的解讀面板），不能讓每日更新失敗。
模型與思考程度沿用設定頁「AI 模型」的 plain 那一項（排程帶 ``AI_PLAIN_MODEL``／``AI_PLAIN_EFFORT``；空的照 gemini_models.PLAIN）。
"""
from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, NamedTuple

import gemini_models
import ledger_core
import plain_daily
import public_log

SCHEMA = "market-brief/v1"
TABLE = "market_brief_runs"
TASK = "market-brief"          # Gemini 用量裡的任務名稱；設定頁（web/settings.js 的 TASKS）要對成「盤勢解讀」
SLOTS = ("morning", "midday", "close", "evening")
KEEP_DAYS = 35                 # 比照首頁消息（market_news.KEEP_DAYS）
TAIPEI = timezone(timedelta(hours=8))
MODEL_DEFAULT = gemini_models.PLAIN
EFFORT_DEFAULT = gemini_models.PLAIN_EFFORT
CALL_ATTEMPTS = 2              # 回答被核對退回時，帶著原因再問一次
POINTS_MAX = 2                 # 觀察、因應各最多幾句；多寫的不看
LIMITS = {"one_line": 80, "say": 80, "effect": 100, "conditions": 60, "prepare": 90}
NO_MODEL = "agy_unavailable"   # plain_daily.agy_caller：agy 沒裝好，沒有呼叫到模型（不算一次用量）

Problem = tuple[str, str]      # (退回的原因, 被退回的那一句)
ModelAsker = plain_daily.ModelAsker


# ---- 類別 ----

class Category(NamedTuple):
    topics: tuple[str, ...]       # 這一類的句子可以代入哪些主題的事實（plain_daily.build_facts 的 topic）
    date_key: str                 # 這一類的資料日：build_facts 的 data_dates 裡哪一個
    directions: tuple[str, ...]   # 判斷那一句要過 plain_daily 哪幾段的方向核對
    prompt: str                   # 提示裡這一類的說明
    template: Callable[[Mapping[str, Any]], dict[str, Any]]   # 程式套句：{say, effect, conditions, prepare, tone, phrase}


def _chips_template(bundle: Mapping[str, Any]) -> dict[str, Any]:
    facts, signals = bundle["facts"], bundle["signals"]
    selling, buying = signals["foreign"] < 0, signals["foreign"] > 0
    net_short, adding = signals["futures_short"], signals["futures_short"] and signals["futures_growing"]
    score = (buying - selling) + (-1 if adding else 0 if net_short else 1)
    tone = (score > 0) - (score < 0)
    phrase = "籌碼面" + {-1: "偏空", 1: "偏多"}.get(tone, "多空分歧")
    spot = "foreign_streak" if "foreign_streak" in facts else "foreign_today"
    turn = "轉為買超" if selling else "轉為賣超" if buying else "出現明顯買賣超"
    prepare = [f"如果外資現貨{turn}，就先確認台指期部位是否同步轉向。"]
    if signals["margin"]:
        prepare.append("如果融資餘額持續增加，就先評估行情轉弱時的槓桿風險。" if signals["margin"] > 0
                       else "如果融資餘額持續減少，就先檢視融資部位的比重。")
    return {
        "say": f"{phrase}：{{{spot}}}，{{futures_net}}。",
        "effect": {-1: "外資現貨與期貨部位同步偏空，權值股賣壓未解。",
                   1: "外資站在買方，權值股具支撐。"}.get(tone, "外資現貨與期貨部位方向分歧，權值股易隨之震盪。"),
        "conditions": [f"外資現貨是否{turn}",
                       "外資台指期淨空單是否持續增加" if net_short else "外資台指期淨多單是否減少"],
        "prepare": prepare, "tone": tone, "phrase": phrase,
    }


def _tech_template(bundle: Mapping[str, Any]) -> dict[str, Any]:
    facts, signals = bundle["facts"], bundle["signals"]
    below20, below60 = signals.get("below_ma20"), signals.get("below_ma60")
    tone = -1 if below20 and below60 else 1 if below20 is False and below60 is False else 0
    phrase = "技術面" + {-1: "偏弱", 1: "偏強"}.get(tone, "區間整理")
    ratio = signals.get("amount_ratio")
    conditions, prepare = [], []
    if "ma20_line" in facts:
        conditions.append(f"收盤是否{'站回' if below20 else '守住'} {{ma20_line}}")
        prepare.append("如果收盤站回 {ma20_line}，就先確認外資現貨是否同步轉買。" if below20
                       else "如果收盤跌破 {ma20_line}，就先檢視持股的支撐位置。")
    else:
        conditions.append("指數能否延續當日方向")
        prepare.append("如果指數方向反轉，就先檢視持股的支撐位置。")
    if ratio is not None:
        conditions.append("成交量能否維持在均量之上" if ratio >= 1 else "成交量能否回到均量之上")
    return {
        "say": f"{phrase}：{{{'ma20_side' if 'ma20_side' in facts else 'close'}}}。",
        "effect": {-1: "指數位於均線之下，反彈易遇賣壓。",
                   1: "指數位於均線之上，回檔具支撐。"}.get(tone, "指數位於均線之間，方向尚未表態。"),
        "conditions": conditions, "prepare": prepare, "tone": tone, "phrase": phrase,
    }


# 順序就是 payload 裡 cats 的順序（畫面順序由前端決定）。
CATEGORIES: dict[str, Category] = {
    "chips": Category(
        topics=("foreign", "futures", "trust", "margin", "short", "date"), date_key="foreign", directions=("foreign",),
        prompt="chips（籌碼面）：外資現貨買賣超、外資台指期淨部位、投信、融資與融券。判斷籌碼面偏多、偏空或分歧，點出主導的力量。",
        template=_chips_template),
    "tech": Category(
        topics=("position", "volume", "date"), date_key="price_and_foreign", directions=("position",),
        prompt="tech（技術面）：加權指數收盤與漲跌、相對均線的位置、成交金額。判斷技術面偏強、偏弱或整理，點出關鍵均線。",
        template=_tech_template),
}
SLOT_CATEGORIES: dict[str, tuple[str, ...]] = {slot: ("chips", "tech") for slot in SLOTS}
SLOT_TEXT = {"morning": "盤前（台北 08:00）", "midday": "盤中（台北 11:30）", "close": "收盤後（台北 14:00）",
             "evening": "盤後定稿（台北 18:00，當日籌碼到齊）"}


# ---- 核對 ----

def _sentence_problem(text: Any, label: str, bundle: Mapping[str, Any], limit: int, *, topics: Sequence[str] | None = None,
                      prepare: bool = False, directions: Sequence[str] = ()) -> str | None:
    """模型（或程式套句）寫的一句能不能上站。label 是寫進原因的位置（例如 chips.say）。"""
    reason = plain_daily._text_problem(text, label, bundle, limit, prepare=prepare)
    if reason:
        return reason
    if topics is not None:
        for key in plain_daily.PLACEHOLDER.findall(text):
            if bundle["facts"][key]["topic"] not in topics:
                return f"{label}：{{{key}}} 不屬於這一類的主題"
    prose = plain_daily.PLACEHOLDER.sub("", text)
    for sections, terms in plain_daily._contradictions(bundle["signals"]) if directions else ():
        if sections.intersection(directions):
            hit = next((term for term in terms if term in prose), None)
            if hit:
                return f"{label}：「{hit}」和當天數字的方向矛盾"
    return None


def validate(output: Any, bundle: Mapping[str, Any], keys: Sequence[str]) -> tuple[dict[str, Any] | None, list[Problem]]:
    """模型的回答 → ({one_line, lead, cats:{key:{say, effect, conditions, prepare}}}（數字已代入）, 退回的原因)。
    任何一句不合格就整份退回（重問或改用程式套句）；觀察、因應多寫的只取前 POINTS_MAX 句；lead 不在這一班的類別裡就用第一個。"""
    if isinstance(output, str):
        try:
            output = json.loads(output)
        except json.JSONDecodeError:
            output = None
    if not isinstance(output, Mapping):
        return None, [("回答不是物件", "")]
    problems: list[Problem] = []

    def passed(text: Any, label: str, field: str, **check: Any) -> str:
        reason = _sentence_problem(text, label, bundle, LIMITS[field], **check)
        if reason:
            problems.append((reason, text[:200] if isinstance(text, str) else ""))
            return ""
        return plain_daily._render(text, bundle, form="text")

    def listed(value: Any, label: str, field: str, **check: Any) -> list[str]:
        items = [value] if isinstance(value, str) else value if isinstance(value, (list, tuple)) else []
        return [passed(item, label, field, **check) for item in [item for item in items if item not in (None, "")][:POINTS_MAX]]

    body: dict[str, Any] = {"one_line": passed(output.get("one_line"), "one_line", "one_line", directions=("one_line",)), "cats": {}}
    rows = output.get("cats")
    by_key = {row.get("key"): row for row in (rows if isinstance(rows, (list, tuple)) else []) if isinstance(row, Mapping)}
    for key in keys:
        row, spec = by_key.get(key), CATEGORIES[key]
        if row is None:
            problems.append((f"cats 少了 {key}", ""))
            continue
        body["cats"][key] = {
            "say": passed(row.get("say"), f"{key}.say", "say", topics=spec.topics, directions=spec.directions),
            "effect": passed(row.get("effect"), f"{key}.effect", "effect", topics=spec.topics),
            "conditions": listed(row.get("conditions"), f"{key}.conditions", "conditions", topics=spec.topics),
            "prepare": listed(row.get("prepare"), f"{key}.prepare", "prepare", topics=spec.topics, prepare=True),
        }
    if problems:
        return None, problems
    body["lead"] = output.get("lead") if output.get("lead") in keys else keys[0]
    return body, []


# ---- 程式套句 ----

def template_body(bundle: Mapping[str, Any], keys: Sequence[str]) -> dict[str, Any]:
    """模型沒寫成時用的那一份：同一張事實表、同一個核對器。套句自己沒過核對＝程式錯，丟例外。"""
    drafts = {key: CATEGORIES[key].template(bundle) for key in keys}
    total = sum(draft["tone"] for draft in drafts.values())
    head = "盤勢偏空" if total < 0 else "盤勢偏多" if total > 0 else "多空分歧"
    raw = {"one_line": f"{head}：" + "、".join(drafts[key]["phrase"] for key in keys) + "。",
           "lead": max(keys, key=lambda key: abs(drafts[key]["tone"])),   # 一樣大時取排在前面的
           "cats": [{"key": key, **{field: drafts[key][field] for field in ("say", "effect", "conditions", "prepare")}}
                    for key in keys]}
    body, problems = validate(raw, bundle, keys)
    if body is None:
        raise RuntimeError("程式套句沒通過核對：" + "；".join(reason for reason, _ in problems))
    return body


# ---- 模型 ----

def output_schema(keys: Sequence[str]) -> dict[str, Any]:
    line = lambda field: {"type": "string", "maxLength": LIMITS[field]}   # noqa: E731
    points = lambda field: {"type": "array", "maxItems": POINTS_MAX, "items": line(field)}   # noqa: E731
    return {"type": "object", "required": ["one_line", "lead", "cats"], "properties": {
        "one_line": line("one_line"), "lead": {"type": "string", "enum": list(keys)},
        "cats": {"type": "array", "minItems": len(keys), "maxItems": len(keys), "items": {
            "type": "object", "required": ["key", "say", "effect", "conditions", "prepare"], "properties": {
                "key": {"type": "string", "enum": list(keys)}, "say": line("say"), "effect": line("effect"),
                "conditions": points("conditions"), "prepare": points("prepare")}}}}}


def build_prompt(slot: str, bundle: Mapping[str, Any], keys: Sequence[str]) -> str:
    topics = {topic for key in keys for topic in CATEGORIES[key].topics}
    facts = {key: {"topic": fact["topic"], "代入成": fact["text"]} for key, fact in bundle["facts"].items() if fact["topic"] in topics}
    allowed = "；".join(f"{key} 只能用 topic 為 {'、'.join(CATEGORIES[key].topics)} 的代號" for key in keys)
    rules = [
        f"你是台股研究部的盤勢分析師，撰寫「盤勢解讀」{SLOT_TEXT[slot]}這一班。只能根據 FACTS，不可連網、不可使用工具，"
        "不可加入 FACTS 沒有的事實（國際股市、新聞、個股、傳聞都不寫）。",
        "用字專業、精簡，採研究報告語氣（籌碼面、技術面、外資、淨空單、均線、量能、權值股），繁體中文，不寫贅詞與口語。",
        "所有文字一律不得出現阿拉伯數字、全形數字、中文數字（一、兩、十、百、千、萬、億、兆等）、日期、股票代號或百分比。"
        "要提到數字或日期時寫 {代號}，程式會換成核對過的句子（FACTS 的「代入成」）；代入後的句子自帶主詞（外資、加權指數…），"
        "代號前後不要重複主詞，換進去後要讀得通順。只能用 FACTS 裡有的代號。",
        f"只有這些含「一」的詞可以用：{'、'.join(plain_daily.YI_WORDS)}；其他含中文數字的詞（例如「不一」「兩者」「十分」）請改寫。",
        "cats 每一類各一筆（key 照抄）：" + " ".join(CATEGORIES[key].prompt for key in keys),
        f"各類可用的代號：{allowed}。",
        "每一類四個欄位：say 一句判斷（方向與關鍵數字）；effect 一句影響（影響到誰、多大）；"
        f"conditions 接下來要觀察的條件，一到{'兩' if POINTS_MAX == 2 else POINTS_MAX}句，寫「什麼是否發生」；"
        f"prepare 因應，一到{'兩' if POINTS_MAX == 2 else POINTS_MAX}句，每句用「如果…就…」寫情境與要先確認、檢視或評估的事。",
        "one_line 是整體一句結論，綜合各類的方向。lead 填這一班影響最大的那一類的 key。",
        "整份不得有買賣指令或個人化投資建議：不要出現「建議買進」「可以加碼」「應該減碼」「逢低布局」「停損出場」這類字眼；"
        "prepare 的「就」後面只能是確認、檢視、評估，不能是買賣動作。",
        "方向不可和 FACTS 相反（例如外資賣超時不可寫外資買超、淨空單增加時不可寫空單減少）。原因分不出來的不要說死。",
        "不寫模型、提示詞、方法、參考站、會員等字樣。",
    ]
    return "\n".join([*rules, "DATA_DATES:", json.dumps(bundle["data_dates"], ensure_ascii=False),
                      "FACTS:", json.dumps(facts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))])


def _retry_note(problem: Problem) -> str:
    reason, text = problem
    return (ledger_core.retry_note(reason, text or None)
            + "要提數字一律寫 {代號}；不得有買賣指令；prepare 每一句用「如果…就…」。")


# ---- 一班 ----

def run(slot: str, analysis: Mapping[str, Any], chips: Mapping[str, Any], *, ask_model: ModelAsker,
        model: str = MODEL_DEFAULT, effort: str = EFFORT_DEFAULT, now: datetime | None = None,
        trade_date: str | None = None, dry_run: bool = False) -> dict[str, Any]:
    """產生一班。回傳完整的報告：status ok 時帶 payload 與 sql（乾跑時 sql 是空字串）。不丟例外。"""
    now = now or datetime.now(timezone.utc)
    keys = SLOT_CATEGORIES[slot]
    report: dict[str, Any] = {"status": "failed", "slot": slot, "trade_date": trade_date, "source": None, "attempts": 0,
                              "categories": list(keys), "model": model, "effort": effort, "model_called": False,
                              "failure": None, "rejected": [], "dry_run": dry_run}
    try:
        bundle = plain_daily.build_facts(analysis, chips)
        template = template_body(bundle, keys)
    except Exception as error:  # noqa: BLE001 — 事實算不出來或套句有錯：這一班不寫，網站沿用上一班
        report["failure"] = f"facts_failed: {type(error).__name__}: {str(error)[:200]}"
        return report
    dates = bundle["data_dates"]
    # 18:00 那一班的交易日就是剛發布的資料日；白天的班是當天（台北）。
    trade_date = trade_date or (dates["price_and_foreign"] if slot == "evening" else now.astimezone(TAIPEI).date().isoformat())
    report.update({"trade_date": trade_date, "data_dates": dates})
    prompt, schema, suffix, body = build_prompt(slot, bundle, keys), output_schema(keys), "", None
    for attempt in range(1, CALL_ATTEMPTS + 1):
        report["attempts"] = attempt
        try:
            status, structured, detail = ask_model(prompt + suffix, schema)
        except Exception as error:  # noqa: BLE001
            status, structured, detail = "model_failed", None, f"{type(error).__name__}: {str(error)[:200]}"
        report["model_called"] = report["model_called"] or status != NO_MODEL
        if status != "ok":   # 模型沒回來不重試（額度與時間）
            report.update({"failure": status, "detail": str(detail)[:300]})
            break
        body, problems = validate(structured, bundle, keys)
        if body is not None:
            report["failure"] = None
            break
        report["failure"] = "validation_failed"
        report["rejected"].append({"attempt": attempt, "problems": [{"reason": reason, "text": text} for reason, text in problems]})
        suffix = _retry_note(problems[0])
    source = "ai" if body is not None else "template"
    body = body or template
    for key in keys:   # 模型沒寫觀察或因應的那一類，用程式套句的補上（畫面三段都有內容）
        for field in ("conditions", "prepare"):
            body["cats"][key][field] = body["cats"][key][field] or template["cats"][key][field]
    payload = {
        "schema": SCHEMA, "trade_date": trade_date, "slot": slot,
        "generated_at": now.astimezone(TAIPEI).isoformat(timespec="seconds"), "source": source,
        "one_line": body["one_line"], "lead": body["lead"],
        "cats": [{"key": key, **body["cats"][key], "data_date": dates[CATEGORIES[key].date_key], "quiet": False} for key in keys],
    }
    report.update({"status": "ok", "source": source, "lead": payload["lead"], "payload": payload,
                   "sql": "" if dry_run else run_sql(payload, model=model if source == "ai" else None)})
    return report


def run_sql(payload: Mapping[str, Any], *, model: str | None) -> str:
    """同一個（交易日, 時段）重跑就蓋掉；超過 KEEP_DAYS 天的清掉。不寫 BEGIN／COMMIT（D1 不收明確的交易）。"""
    q = ledger_core.sql_literal
    cutoff = (date.fromisoformat(payload["trade_date"]) - timedelta(days=KEEP_DAYS)).isoformat()
    values = ", ".join(q(value) for value in (payload["trade_date"], payload["slot"], payload["generated_at"], payload["source"],
                                              model, json.dumps(payload, ensure_ascii=False, separators=(",", ":"))))
    return (f"INSERT INTO {TABLE} (trade_date, slot, generated_at, source, model, payload) VALUES ({values}) "
            "ON CONFLICT (trade_date, slot) DO UPDATE SET generated_at = excluded.generated_at, source = excluded.source, "
            "model = excluded.model, payload = excluded.payload;\n"
            f"DELETE FROM {TABLE} WHERE trade_date < {q(cutoff)};\n")


def public_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """可以印到公開的排程輸出的那一份：沒有 payload、SQL、被退回的句子；模型失敗的說明只留字數。"""
    out = {key: value for key, value in report.items() if key not in ("payload", "sql", "rejected")}
    out["rejected"] = sum(len(entry.get("problems") or []) for entry in report.get("rejected") or [])
    return public_log.text_to_length(out, ("detail",))


# ---- 命令列 ----

def usage_sql(report: Any, buckets: list[dict[str, Any]] | None, *, run_id: str, taken_at: str) -> str:
    """這一班的 Gemini 用量：有呼叫到模型就一筆（重問那一次不另外記）；沒呼叫、乾跑、報告讀不懂都是空的。
    成功＝模型寫的那一份通過核對；改用程式套句算失敗。"""
    import gemini_usage
    if not isinstance(report, Mapping) or report.get("dry_run") or not report.get("model_called"):
        return ""
    return gemini_usage.snapshot_sql(buckets, task=TASK, run_id=run_id, taken_at=taken_at, model=report.get("model"),
                                     call={"effort": report.get("effort"), "dry_run": False,
                                           "outcome": "ok" if report.get("source") == "ai" else "failed"})


def _usage(args: Any) -> int:
    import gemini_usage
    try:
        lines = [line for line in Path(args.report).read_text(encoding="utf-8").splitlines() if line.strip().startswith("{")]
        report = json.loads(lines[-1])   # 報告是那一步輸出的最後一行
    except (OSError, ValueError, IndexError):
        report = None
    buckets = None
    if isinstance(report, Mapping) and report.get("model_called") and not report.get("dry_run"):
        try:
            buckets = gemini_usage.read_usage(args.agy_bin) if args.agy_bin else None
        except gemini_usage.UsageUnavailable as error:
            print(f"::warning::讀不到 Gemini 用量（{error}）；只記下這次任務")
    script = usage_sql(report, buckets, run_id=args.run_id, taken_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    Path(args.sql_output).write_text(script, encoding="utf-8", newline="\n")
    print(f"盤勢解讀這一班記 {script.count('INSERT INTO')} 筆用量")
    return 0


def _run(args: Any, ask_model: ModelAsker | None, now: datetime | None) -> dict[str, Any]:
    import os

    import impact_watch
    model = os.environ.get("AI_PLAIN_MODEL") or MODEL_DEFAULT
    effort = os.environ.get("AI_PLAIN_EFFORT") or EFFORT_DEFAULT
    base = {"status": "failed", "slot": args.slot, "model": model, "effort": effort, "model_called": False, "dry_run": args.dry_run}
    try:
        analysis_path = Path(args.analysis)
        analysis = impact_watch.read_assignment(analysis_path)
        chips = impact_watch.read_assignment(Path(args.chips) if args.chips else analysis_path.with_name("chips-data.js"))
    except Exception as error:  # noqa: BLE001 — 資料包讀不到：這一班不寫
        return {**base, "failure": f"input_failed: {type(error).__name__}"}
    ask = ask_model or plain_daily.agy_caller(args.agy_bin or None, home=args.agy_home or None, model=model, effort=effort)
    return run(args.slot, analysis, chips, ask_model=ask, model=model, effort=effort, now=now, trade_date=args.date,
               dry_run=args.dry_run)


def main(argv: Sequence[str] | None = None, *, ask_model: ModelAsker | None = None, now: datetime | None = None) -> int:
    """ask_model、now 只給測試換掉模型與時間。"""
    import argparse
    parser = argparse.ArgumentParser(description="盤勢解讀：一班")
    parser.add_argument("command", choices=["run", "usage"])
    parser.add_argument("--slot", choices=SLOTS, help="run：哪一班")
    parser.add_argument("--analysis", help="run：大盤資料包的 analysis-data.js")
    parser.add_argument("--chips", help="run：chips-data.js；不給就讀 --analysis 同一個資料夾的")
    parser.add_argument("--date", help="run：台北交易日；不給時 evening 用資料日、其他時段用今天")
    parser.add_argument("--sql-output", help="寫進 D1 的 SQL（run：這一份；usage：用量）")
    parser.add_argument("--dry-run", action="store_true", help="run：照樣呼叫模型與核對，但 SQL 檔是空的")
    parser.add_argument("--agy-bin")
    parser.add_argument("--agy-home")
    parser.add_argument("--report", help="run：完整的報告（不公開的地方）；usage：run 印出來的那一份")
    parser.add_argument("--run-id", help="usage：這一次排程的編號")
    args = parser.parse_args(argv)
    if args.command == "usage":
        if not (args.report and args.run_id and args.sql_output):
            parser.error("usage 要 --report、--run-id、--sql-output")
        try:
            return _usage(args)
        except Exception as error:  # noqa: BLE001 — 用量只是附帶資訊
            print(f"::warning::盤勢解讀的用量沒記（{type(error).__name__}）")
            return 0
    if not (args.slot and args.analysis and args.sql_output):
        parser.error("run 要 --slot、--analysis、--sql-output")
    try:
        report = _run(args, ask_model, now)
        Path(args.sql_output).write_text(report.get("sql") or "", encoding="utf-8", newline="\n")
    except Exception as error:  # noqa: BLE001 — 這一份寫不成不能讓每日更新失敗
        report = {"status": "failed", "slot": args.slot, "failure": f"{type(error).__name__}", "model_called": False,
                  "dry_run": args.dry_run}
    public_log.write_private(args.report, {key: value for key, value in report.items() if key != "sql"})
    # 倉庫公開之後排程的輸出誰都看得到：印的是遮過的那一份，完整的（含被退回的句子）只在 --report
    print(json.dumps(public_report(report), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
