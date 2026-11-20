"""帳本共用的底層：預測帳本（``forecast_ledger``）與關注帳本（``watch_ledger``）都用這一套，不各寫一份。

對外：

- 鎖定：``sha``、``seal``、``append``（只新增不覆寫，``os.link``）、``read_record``／``read_folders``
  （讀取時核對封存與輸入雜湊，不符丟 ``LedgerIntegrityError``）。
- 模型呼叫：``build_prompt``、``call_model``（agy 串流模式的命令列程式，提示從 stdin 送，回答讀最後一個 result 事件）。
- 回答裡的自由文字與引用：``text_problem``（不得出現數字）、``refs_problem``、``render_refs``（數值由程式依編號代入）。
- 版本與期限：``method_version_of``、``deadline``（晚間／晨間的鎖定期限）、``add_months``、``default_now``。
- 日 K 與顯示：``clean_daily``、``label_date``、``format_number``。
- 發布到網站資料庫：``sql_literal``、``sql_insert``、``compact_json``（指令檔不包交易，部署工具自己包）。

這裡只放兩本帳意思相同的東西；各自的候選關卡、回答格式、對帳規則留在各自的模組。
"""
from __future__ import annotations

import calendar
import copy
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from ai_digest import CHINESE_NUMERAL_CHARS

TAIPEI = timezone(timedelta(hours=8))
EDITIONS = ("evening", "morning")
# 晨間版須在開盤（09:00）前 15 分鐘鎖定。
MORNING_DEADLINE = (8, 45)
# 模型寫的自由文字長度上限，與一段文字最多能引用幾個編號。
TEXT_LIMIT = 160
REFS_LIMIT = 3


class LedgerIntegrityError(Exception):
    """紀錄的封存雜湊不符：原件被改動，或不是由帳本模組寫入。"""


# ---- 雜湊與鎖定 ----

def _canonical(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha(payload: Any) -> str:
    return hashlib.sha256(_canonical(payload)).hexdigest()


def seal(record: dict[str, Any]) -> dict[str, Any]:
    record["record_sha256"] = sha({k: v for k, v in record.items() if k != "record_sha256"})
    return record


def _verify(record: Mapping[str, Any], path: Path) -> None:
    body = {k: v for k, v in record.items() if k != "record_sha256"}
    if record.get("record_sha256") != sha(body):
        raise LedgerIntegrityError(f"{path} 封存雜湊不符：原件被改動")
    if "input" in record and record.get("input_sha256") != sha(record["input"]):
        raise LedgerIntegrityError(f"{path} 輸入雜湊不符：存檔的輸入被改動")


# 帳本檔名含輸入版本，層名越加越長：Windows 上完整路徑超過 260 字元就開不了檔（雲端排程是 Linux，不受影響）。
# 長路徑在 Windows 改用延伸長度寫法（\\?\ 開頭的絕對路徑）；其他系統、短路徑原樣不動。
_WINDOWS_PATH_LIMIT = 240


def long_path(path: Path) -> Path:
    if os.name != "nt":
        return Path(path)
    text = os.path.abspath(str(path))   # 收掉 ..（延伸長度寫法不會幫忙整理路徑）
    if len(text) < _WINDOWS_PATH_LIMIT:
        return Path(path)
    return Path(text if text.startswith("\\\\?\\") else "\\\\?\\" + text)


def exists(path: Path) -> bool:
    return long_path(Path(path)).exists()


def _against_existing(path: Path, record: Mapping[str, Any]) -> str:
    existing = json.loads(long_path(path).read_text(encoding="utf-8"))
    return "unchanged" if existing.get("record_sha256") == record["record_sha256"] else "conflict"


def append(path: Path, record: Mapping[str, Any]) -> str:
    """只新增：檔案不存在才寫；已存在時同內容回 ``unchanged``、不同內容回 ``conflict``，都不覆寫。"""
    if exists(path):
        return _against_existing(path, record)
    long_path(path.parent).mkdir(parents=True, exist_ok=True)
    partial = long_path(path.with_name(f".{path.name}.{os.getpid()}.partial"))
    partial.write_bytes(json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n")
    try:
        # os.link 在目標已存在時失敗：兩個寫入者競爭時後者不會蓋掉前者。
        os.link(partial, long_path(path))
    except FileExistsError:
        return _against_existing(path, record)
    finally:
        partial.unlink(missing_ok=True)
    return "written"


def read_record(path: Path) -> dict[str, Any]:
    record = json.loads(long_path(path).read_text(encoding="utf-8"))
    _verify(record, path)
    return record


def read_folders(root: Path | str, kinds: Sequence[str]) -> dict[str, list[dict[str, Any]]]:
    """讀出帳本各資料夾的全部紀錄並核對雜湊；資料夾不存在就是空的。"""
    folder = Path(root)
    return {kind: [read_record(path) for path in sorted((folder / kind).glob("*.json"))]
            if (folder / kind).is_dir() else [] for kind in kinds}


# ---- 數字、日 K、日期 ----

def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def format_number(value: float) -> str:
    try:
        rendered = format(Decimal(str(value)), ",f")
    except InvalidOperation:
        return str(value)
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def clean_daily(daily: Sequence[Mapping[str, Any]], *, name: str = "index_daily",
                keys: Sequence[str] = ("open", "high", "low", "close", "amount")) -> list[dict[str, Any]]:
    """日 K 轉成數字（讀不出來的是 None），並要求日期遞增、不重複。"""
    rows = [{"date": str(row.get("date")), **{key: _number(row.get(key)) for key in keys}} for row in daily]
    dates = [row["date"] for row in rows]
    if dates != sorted(dates) or len(set(dates)) != len(dates):
        raise ValueError(f"{name} 必須依日期遞增且日期不可重複")
    return rows


def label_date(day: str, base_date: str) -> str:
    """同一年只寫月／日；和基準日不同年時連年份一起寫，免得一年前的關卡被看成今年。"""
    return f"{day[5:7]}/{day[8:10]}" if day[:4] == base_date[:4] else day.replace("-", "/")


# ---- 方法版本與期限 ----

def method_version_of(method_text: str) -> str:
    """方法稿開頭「版本：X.Y」那一行的版本號；用的是開始記錄當時最新的方法稿。"""
    match = re.search(r"^版本：\s*([0-9]+(?:\.[0-9]+)*)", method_text or "", re.MULTILINE)
    if not match:
        raise ValueError("方法稿缺少「版本：」行，無法標示方法版本")
    return match.group(1)


def deadline(edition: str, base_date: str, session_date: str | None) -> datetime:
    """晚間版在基準日結束前鎖定；晨間版在開盤日 08:45 前鎖定。"""
    if edition == "evening":
        return datetime.fromisoformat(base_date).replace(tzinfo=TAIPEI) + timedelta(days=1)
    if not session_date or session_date <= base_date:
        raise ValueError("晨間版須給基準日之後的開盤日 session_date")
    hour, minute = MORNING_DEADLINE
    return datetime.fromisoformat(session_date).replace(hour=hour, minute=minute, tzinfo=TAIPEI)


def add_months(day: date, months: int) -> date:
    """加幾個月，月底對齊（例如 8/31 加三個月是 11/30）。"""
    month_index = day.month - 1 + months
    year, month = day.year + month_index // 12, month_index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def default_now() -> datetime:
    return datetime.now(TAIPEI)


# ---- 模型呼叫 ----

def build_prompt(rules: Sequence[str], model_input: Mapping[str, Any]) -> str:
    """提示 = 規則逐行，接著整份輸入的正規化 JSON。"""
    return "\n".join([*rules, "INPUT_JSON:",
                      json.dumps(model_input, ensure_ascii=False, sort_keys=True, separators=(",", ":"))])


def _model_command(model_bin: Path | str, model: str, output_schema: Mapping[str, Any], effort: str = "medium",
                   print_timeout: int = 300) -> list[str]:
    """agy 的串流模式：提示從 stdin 送（Windows 單一指令列約 32K 字元的上限擋不住方法稿全文），結果讀最後的 result 事件。"""
    path = Path(model_bin)
    prefix = [sys.executable, str(path)] if path.suffix.lower() == ".py" else [str(path)]
    return [*prefix, "-p=", "--input-format", "stream-json", "--output-format", "stream-json",
            "--model", model, "--effort", effort,
            "--json-schema", json.dumps(output_schema, ensure_ascii=False, separators=(",", ":")),
            "--print-timeout", f"{int(print_timeout)}s", "--sandbox"]


def _stream_input(prompt: str) -> bytes:
    return (json.dumps({"event": "user", "message": {"content": prompt}}, ensure_ascii=False) + "\n").encode("utf-8")


def _result_event(stdout: bytes) -> dict[str, Any] | None:
    """串流輸出一行一個事件；回傳最後一個 result 事件的內容。"""
    result = None
    for line in stdout.decode("utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if isinstance(event, dict) and event.get("event") == "result" and isinstance(event.get("result"), dict):
            result = event["result"]
    return result


def _failure(returncode: int, stderr: bytes) -> str:
    """漏記原因帶上錯誤輸出的最後一段，查得到是額度、登入還是程式問題。"""
    tail = stderr.decode("utf-8", errors="replace").strip()[-300:]
    return f"結束碼 {returncode}" + (f"：{tail}" if tail else "")


def call_model(model_bin: Path | str, prompt: str, model: str, timeout: float,
               output_schema: Mapping[str, Any], *, env: Mapping[str, str] | None = None,
               effort: str = "medium", print_timeout: int = 300) -> tuple[str, Any, str]:
    """回傳 (狀態, structured_output, 說明)；狀態是 ok／model_failed／model_timeout／output_garbled。
    env 給了就只用在 agy 這個子行程（例如登入檔放在隔離 HOME，不改呼叫端整個步驟的 HOME）；effort 是思考程度，預設照舊 medium。"""
    try:
        completed = subprocess.run(_model_command(model_bin, model, output_schema, effort, print_timeout), input=_stream_input(prompt),
                                   capture_output=True, timeout=timeout, check=False,
                                   env=dict(env) if env is not None else None)
    except subprocess.TimeoutExpired:
        return "model_timeout", None, f"超過 {timeout} 秒沒有回應"
    except OSError as error:
        return "model_failed", None, f"{type(error).__name__}: {error}"
    try:
        envelope = _result_event(completed.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError):
        if completed.returncode != 0:
            return "model_failed", None, _failure(completed.returncode, completed.stderr)
        return "output_garbled", None, "輸出不是 UTF-8 JSON 事件"
    if completed.returncode != 0 or envelope is None or envelope.get("status") != "SUCCESS":
        detail = _failure(completed.returncode, completed.stderr)
        if envelope and envelope.get("error"):
            detail += f"；{str(envelope['error'])[:200]}"
        return "model_failed", None, detail
    return "ok", envelope.get("structured_output"), ""


# 放寬檢查（本人 2026-11-18 晚「放寬檢查（推薦）」）：方法稿用詞至少幾個字、其中至少幾個字不是數字才算一個詞。
_TERM_SPAN = (3, 8)
_TERM_MIN_WORDS = 2
_MASK = "\x00"


def _is_numeral(character: str) -> bool:
    return character.isnumeric() or character in CHINESE_NUMERAL_CHARS


def _is_han(character: str) -> bool:
    return "一" <= character <= "鿿" or character in CHINESE_NUMERAL_CHARS


# 2026-11-19 晚間正式預測兩次被擋：模型把「未回補向上缺口下緣（04/15）」這種名字省掉前綴寫成「缺口下緣（04/15）」。
# 帶結尾括號（日期）的名字，括號前至少這麼多字的尾段也算名字；太短的（「下緣（04/15）」）不算。
_NAME_TAIL_MIN = 4


def _name_forms(names: Sequence[str]) -> list[str]:
    """候選名字的整個名字、去掉結尾括號（日期）的那一段，以及帶著結尾括號的尾段；長的先比，才不會把長名字拆開。"""
    forms = set()
    for name in names:
        name = str(name or "").strip()
        if len(name) >= 2:
            forms.add(name)
            short = re.sub(r"[（(][^（）()]*[）)]$", "", name).strip()
            if len(short) >= 2:
                forms.add(short)
            if short != name:
                for start in range(1, len(short) - _NAME_TAIL_MIN + 1):
                    forms.add(name[start:])
    return sorted(forms, key=len, reverse=True)


# 被擋下的回答原樣記進 attempts 的最大字數（2026-11-19 關注晚間兩檔漏記，只留下「缺欄位或含未允許欄位」查不出是哪個）。
_ANSWER_SNAPSHOT_MAX = 2000


def known_fields(answer: Any, required: Sequence[str], defaults: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    """模型回答整理成只有已知欄位：多寫的未知欄位丟掉（不整份作廢）；``defaults`` 裡的欄位沒寫就補上；
    其餘必填欄位缺一個就回 None（真的缺東西才算無效）。2026-11-19 owner：Flash 高常多寫或少寫欄位。"""
    if not isinstance(answer, Mapping):
        return None
    defaults = defaults or {}
    kept: dict[str, Any] = {}
    for key in required:
        if key in answer:
            kept[key] = answer[key]
        elif key in defaults:
            kept[key] = copy.deepcopy(defaults[key])
        else:
            return None
    return kept


def answer_snapshot(answer: Any) -> dict[str, Any]:
    """被擋下的回答：欄位名稱與原文（截短），記進 attempts，之後查得到模型實際回了什麼。"""
    keys = sorted(str(key) for key in answer) if isinstance(answer, Mapping) else []
    text = json.dumps(answer, ensure_ascii=False, default=str)
    return {"answer_keys": keys, "rejected_answer": text[:_ANSWER_SNAPSHOT_MAX]}


def input_names(value: Any) -> list[str]:
    """整份輸入裡所有 ``name`` 欄位的字（候選關卡、教訓層裡過去選過的線、同類股、海外各項……）：理由照抄這些名字裡的
    數字放行（2026-11-19：模型寫了教訓層裡的「缺口下緣（04/15）」，它當天不在候選裡，日期數字就被擋）。"""
    found: list[str] = []
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, Mapping):
            if isinstance(item.get("name"), str):
                found.append(item["name"])
            stack.extend(item.values())
        elif isinstance(item, (list, tuple)):
            stack.extend(item)
    return sorted(set(found))


def _in_method_term(text: str, at: int, method_text: str) -> bool:
    """text[at] 這個中文數字是不是落在方法稿原文的一個詞裡：包含它、全是漢字、至少 3 個字且至少 2 個字不是數字的
    一段，原樣出現在方法稿裡。"""
    low, high = _TERM_SPAN
    for start in range(max(0, at - high + 1), at + 1):
        for end in range(at + 1, min(len(text), start + high) + 1):
            piece = text[start:end]
            if (len(piece) >= low and all(_is_han(c) for c in piece)
                    and sum(not _is_numeral(c) for c in piece) >= _TERM_MIN_WORDS and piece in method_text):
                return True
    return False


def text_problem(text: Any, *, names: Sequence[str] = (), method_text: str = "") -> str | None:
    """模型寫的自由文字：不可空白、不可過長、不可出現任何數字（數值一律由程式依編號代入）。

    唯一的例外是照抄這一份輸入裡本來就有的名稱（本人 2026-11-18 晚「放寬檢查（推薦）」）：``names`` 是候選關卡的名字
    （整個名字，或去掉結尾括號日期的那一段），``method_text`` 是方法稿原文——落在其中一個詞裡的數字放行。
    先把名字整個遮掉，剩下的阿拉伯／全形數字一律擋；中文數字要落在方法稿原文的一個詞裡才放行，貼在名字旁邊的照擋。"""
    if not isinstance(text, str) or not text.strip():
        return "文字為空"
    if len(text) > TEXT_LIMIT:
        return "文字過長"
    masked = text
    for form in [*_name_forms(names), *COMMON_NUMERAL_WORDS]:
        masked = masked.replace(form, _MASK)
    for at, character in enumerate(masked):
        if not _is_numeral(character):
            continue
        if _counts_something(masked, at):
            return "自由文字不得包含數字；價位請用候選編號"
        if character in _FORMAL_NUMERALS and not _next_to_numeral(masked, at):
            continue   # 參考、大陸、零組件、收拾、隊伍：單獨出現的大寫數字字不是數字
        if (character in CHINESE_NUMERAL_CHARS and not _next_to_numeral(masked, at)
                and not any(0 <= near < len(masked) and masked[near] == _MASK for near in (at - 1, at + 1))
                and not _LINE_NAME_TAIL.match(masked, at + 1)):
            # 2026-11-20 正式晚間連兩晚被擋，原文沒有任何數值：「兩個頻道」「前一日」「第二隻腳」。
            # 單獨一個中文數字、後面不是量詞（量詞在上面已經擋了），不是數值，放行。連著兩個以上的（二十、五千）照舊往下查；
            # 緊貼著名字的（「二」＋十日線＝輸入裡沒有的二十日線）、自己就是線名開頭的（五日線、十週均）也照舊往下查。
            continue
        if character in CHINESE_NUMERAL_CHARS and method_text and _in_method_term(masked, at, method_text):
            continue
        return "自由文字不得包含數字；價位請用候選編號"
    return None


# 第二輪（owner 11-19 轉本人「放寬檢查」）：常見、不帶數值的詞放行；數字後面接量詞（三成、五張、十點）一律擋，
# 即使提示裡原樣有（新聞標題的「年增三成」照抄進理由也不行）。``method_text`` 由呼叫端給當份完整提示（規則＋輸入）。
COMMON_NUMERAL_WORDS = ("一起", "一致", "一些", "一般", "一路", "一度", "一旦", "一再", "單一", "統一", "第一", "第二",
                        "萬一", "兩線", "兩條線")
_MEASURE_WORDS = frozenset("成張點元塊倍檔根天年月週周次分秒季%％")
# 2026-11-20 正式晨間被擋的「僅作參考」：大寫數字字（參、陸、零、拾、伍…）在一般文章裡幾乎都不是數字（參考、大陸、
# 零組件、收拾、隊伍），只有前後接著別的數字（零點五的「點」是量詞、陸佰、二〇二六）或量詞（參成）時才當數字擋。
_FORMAL_NUMERALS = frozenset("壹貳貮參参肆伍陸陆柒捌玖拾佰仟零〇")
# 數字後面接這些就是在叫一條線或一個區間的名字（五日線、十週均、三日高點）：不在輸入裡的照擋。
_LINE_NAME_TAIL = re.compile(r"[日週周月季年][線均高低Kk]")


def _next_to_numeral(text: str, at: int) -> bool:
    return any(0 <= near < len(text) and _is_numeral(text[near]) for near in (at - 1, at + 1))


def _counts_something(text: str, at: int) -> bool:
    """text[at] 起的這一串數字後面接的是量詞（不是「第…」那種序數）：就是一個數量。"""
    end = at
    while end < len(text) and _is_numeral(text[end]):
        end += 1
    return end < len(text) and text[end] in _MEASURE_WORDS and not (at > 0 and text[at - 1] == "第")


def retry_note(problem: str | None, rejected_text: str | None = None) -> str:
    """回答被退回後重問時接在提示後面的一段（本人 2026-11-20「要，重問一次」）：原本重問用的是同一份提示，模型不知道
    錯在哪，多半再錯一次。規則本身不變，只是把退回的原因與那一句原文告訴它。"""
    quoted = f"被退回的那一句：「{rejected_text}」。" if rejected_text else ""
    return ("\n\n【上一次的回答被退回，請整份重寫】"
            f"原因：{problem or '格式不符'}。{quoted}"
            "重寫時自由文字裡不要出現阿拉伯數字、日期（例如 04/08）或帶量詞的數量（例如三成、五百點）；"
            "要指某一條線或某一個價位，只用輸入裡的編號或原樣照抄它的名字。欄位與格式照原本的規定，不要多也不要少。")


def refs_problem(refs: Any, known: Mapping[str, Any]) -> bool:
    """引用的編號清單不合格：不是清單、太多、重複，或引用了輸入裡沒有的編號。"""
    return (not isinstance(refs, list) or len(refs) > REFS_LIMIT or len(set(refs)) != len(refs)
            or any(ref not in known for ref in refs))


def render_refs(text: str, refs: Sequence[str], by_id: Mapping[str, Mapping[str, Any]], unit: str) -> str:
    """把模型引用的候選編號代成名字與數值，接在它寫的文字後面。"""
    if not refs:
        return text.strip()
    facts = "；".join(f"{by_id[ref]['name']} {format_number(by_id[ref]['value'])} {unit}" for ref in refs)
    return f"{text.strip()}【資料：{facts}】"


# ---- 發布到網站讀的資料庫（寫入雲端資料庫的指令檔） ----

def sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def sql_insert(table: str, columns: Mapping[str, Any], key: str | None) -> str:
    """只新增的一列：同一筆再發布一次被 ON CONFLICT 吸收；內容不同由資料表的觸發器拒絕。"""
    names = ",".join(columns)
    values = ",".join(sql_literal(value) for value in columns.values())
    target = f"({key})" if key else ""
    return f"INSERT INTO {table} ({names}) VALUES ({values}) ON CONFLICT{target} DO NOTHING;"


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
