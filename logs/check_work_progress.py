"""Read-only progress check; does not start, stop, or retry project work."""
import json
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def summarize(rows, count, target, now):
    recent = rows[-60:]
    saved = [r for r in rows if r.get('outcome') == 'saved']
    last = saved[-1] if saved else None
    times = [datetime.fromisoformat(r['fetched_at_utc']) for r in saved[-60:]]
    intervals = [(b - a).total_seconds() for a, b in zip(times, times[1:]) if b > a]
    age = (now - times[-1]).total_seconds() if times else None
    # No recent event is a prompt to inspect, not proof the process is stuck.
    state = 'download_count_reached_build_unverified' if count >= target else (
        'recent_progress' if age is not None and age < 180 else 'inspect_no_recent_success'
    )
    return {
        'checked_at': now.astimezone().isoformat(),
        'download_state': state,
        'saved_files': count,
        'target_days': target,
        'last_saved_day': last.get('date') if last else None,
        'last_saved_at': last.get('fetched_at_utc') if last else None,
        'seconds_since_success': round(age, 1) if age is not None else None,
        'recent_outcomes': dict(Counter(r.get('outcome', 'unknown') for r in recent)),
        'remaining_download_minutes': round(max(0, target-count)*statistics.median(intervals)/60, 1) if intervals else None,
        'estimate_excludes': ['database_build', 'gap_verification', 'backtest'],
        'ledger_final_acceptance': 'must_read_owner_handoff_not_inferred_from_file_mtime',
    }


if __name__ == '__main__':
    log = ROOT / 'data/stock-history/fetch-log.jsonl'
    rows = []
    incomplete = 0
    for line in log.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            incomplete += 1
    count = sum(1 for _ in (ROOT / 'data/stock-history/raw').glob('*/MI_INDEX-*.json.gz'))
    result = summarize(rows, count, 2641, datetime.now(timezone.utc))
    result['unparsed_log_lines'] = incomplete
    print(json.dumps(result, ensure_ascii=False, indent=2))
