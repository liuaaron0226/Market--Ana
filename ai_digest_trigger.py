"""daily-update → ai-digest 的 fail-closed 發布證據契約。"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

SCHEMA = "daily-publication-evidence/v1"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _bool(value: str | bool) -> bool:
    if isinstance(value, bool):
        return value
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes"}:
        return True
    if normalized in {"0", "false", "no", ""}:
        return False
    raise ValueError("布林值只接受 true/false")


def build_publication_evidence(
    daily_result: dict[str, Any],
    *,
    dry_run: bool,
    run_id: str,
    head_sha: str,
    repository: str,
    head_branch: str,
) -> dict[str, Any]:
    data_date = str(daily_result.get("data_date") or "")
    published = (
        not dry_run
        and daily_result.get("status") == "success"
        and daily_result.get("sync_state") is True
        and bool(DATE_RE.fullmatch(data_date))
    )
    return {
        "schema": SCHEMA,
        "workflow": "daily-update",
        "run_id": str(run_id),
        "head_sha": str(head_sha).lower(),
        "repository": str(repository),
        "head_branch": str(head_branch),
        "data_date": data_date or None,
        "dry_run": bool(dry_run),
        "status": daily_result.get("status"),
        "sync_state": daily_result.get("sync_state") is True,
        "published": published,
    }


def authorize_publication(
    evidence: Any,
    *,
    expected_run_id: str,
    expected_head_sha: str,
    expected_repository: str,
    expected_branch: str,
    conclusion: str,
    allow_already_done: bool = False,
) -> dict[str, Any]:
    """只有真的發布那一班放行。``allow_already_done``（只有朋友解讀排程帶）另外放行「這一天先前已經發布成功」的
    後面時段（每日更新回 already-done），讓它替還沒有結果的朋友補跑；只重新部署程式、等資料、休市、乾跑都不放行。"""
    denied = {"allowed": False, "reason": "invalid-evidence", "data_date": "", "head_sha": ""}
    if not isinstance(evidence, dict) or evidence.get("schema") != SCHEMA or evidence.get("workflow") != "daily-update":
        return denied
    checks = (
        (str(evidence.get("run_id")) == str(expected_run_id), "run-id-mismatch"),
        (str(evidence.get("head_sha", "")).lower() == str(expected_head_sha).lower(), "head-sha-mismatch"),
        (evidence.get("repository") == expected_repository, "repository-mismatch"),
        (evidence.get("head_branch") == expected_branch, "branch-mismatch"),
        (conclusion == "success", "upstream-not-successful"),
    )
    for passed, reason in checks:
        if not passed:
            return {**denied, "reason": reason}
    head_sha = str(evidence.get("head_sha", "")).lower()
    data_date = str(evidence.get("data_date") or "")
    if not SHA_RE.fullmatch(head_sha) or not DATE_RE.fullmatch(data_date):
        return denied
    if allow_already_done and evidence.get("dry_run") is False and evidence.get("status") == "already-done":
        return {"allowed": True, "reason": "already-published", "data_date": data_date, "head_sha": head_sha}
    if evidence.get("dry_run") is not False or evidence.get("status") != "success" or evidence.get("sync_state") is not True or evidence.get("published") is not True:
        return {**denied, "reason": "upstream-not-published"}
    return {"allowed": True, "reason": "published", "data_date": data_date, "head_sha": head_sha}


def _write_outputs(path: Path, decision: dict[str, Any]) -> None:
    lines = [
        f"allowed={'true' if decision['allowed'] else 'false'}",
        f"reason={decision['reason']}",
        f"data_date={decision.get('data_date') or ''}",
        f"head_sha={decision.get('head_sha') or ''}",
    ]
    with path.open("a", encoding="utf-8", newline="\n") as output:
        output.write("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-evidence")
    create.add_argument("--daily-result", type=Path, required=True)
    create.add_argument("--dry-run", required=True)
    create.add_argument("--run-id", required=True)
    create.add_argument("--head-sha", required=True)
    create.add_argument("--repository", required=True)
    create.add_argument("--head-branch", required=True)
    create.add_argument("--output", type=Path, required=True)
    authorize = sub.add_parser("authorize")
    authorize.add_argument("--evidence", type=Path, required=True)
    authorize.add_argument("--expected-run-id", required=True)
    authorize.add_argument("--expected-head-sha", required=True)
    authorize.add_argument("--expected-repository", required=True)
    authorize.add_argument("--expected-branch", required=True)
    authorize.add_argument("--conclusion", required=True)
    authorize.add_argument("--github-output", type=Path, required=True)
    authorize.add_argument("--allow-already-done", action="store_true",
                           help="朋友解讀排程用：這一天先前已發布成功的後面時段也放行（reason=already-published）")
    args = parser.parse_args(argv)

    if args.command == "create-evidence":
        result = json.loads(args.daily_result.read_text(encoding="utf-8"))
        evidence = build_publication_evidence(
            result,
            dry_run=_bool(args.dry_run),
            run_id=args.run_id,
            head_sha=args.head_sha,
            repository=args.repository,
            head_branch=args.head_branch,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(evidence, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        return 0

    try:
        evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        evidence = None
    decision = authorize_publication(
        evidence,
        expected_run_id=args.expected_run_id,
        expected_head_sha=args.expected_head_sha,
        expected_repository=args.expected_repository,
        expected_branch=args.expected_branch,
        conclusion=args.conclusion,
        allow_already_done=args.allow_already_done,
    )
    _write_outputs(args.github_output, decision)
    print(json.dumps({"allowed": decision["allowed"], "reason": decision["reason"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
