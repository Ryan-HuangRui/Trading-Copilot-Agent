#!/usr/bin/env bash
set -u

REPO="${TCA_REPO:-/home/admin_ryan/repo/Trading-Copilot-Agent}"
CC="${CC_CONNECT_BIN:-/home/admin_ryan/.local/bin/cc-connect}"
PROJECT="${CC_CONNECT_PROJECT:-trading-copilot}"
SESSION="${CC_CONNECT_SESSION:-feishu:oc_0df4740c94656aaa83249668668c3994:ou_f60f6e25add2b35cc00bb933b6e3960c}"
DATE_ARG="${1:-}"
DATE="${DATE_ARG:-$(TZ=Asia/Shanghai date +%F)}"
CONFIG="${TCA_PAPER_EXECUTION_CONFIG:-config/paper_execution.local.json}"
LOG="$(mktemp)"
MSG="$(mktemp)"
STATUS="success"

cd "$REPO" || exit 1

run_step() {
  echo >>"$LOG"
  echo "$ $*" >>"$LOG"
  "$@" >>"$LOG" 2>&1
  local rc=$?
  if [ "$rc" -ne 0 ]; then
    STATUS="failed"
    echo "step failed rc=$rc" >>"$LOG"
  fi
  return "$rc"
}

run_step python3 script/trading_copilot.py paper-trade-submit --date "$DATE" --session pre-market --require-validation --paper-execution-config "$CONFIG" --execute

# Always refresh state after an execution attempt. These steps are broker read-only
# or local projections and make partial outcomes visible before any retry decision.
run_step python3 script/trading_copilot.py paper-account-snapshot --date "$DATE"
run_step python3 script/trading_copilot.py paper-order-sync --date "$DATE"
run_step python3 script/trading_copilot.py paper-event-ledger --date "$DATE"
run_step python3 script/trading_copilot.py paper-execution-review --date "$DATE"

python3 - "$DATE" "$STATUS" "$LOG" "$REPO" >"$MSG" <<'PYMSG'
import json
import sys
from pathlib import Path

date, status, log_path, repo_root = sys.argv[1:5]
root = Path(repo_root)

def load(path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception as exc:
        return {'_load_error': str(exc)}

submission_path = root / 'report' / date / 'paper-trade-submission.json'
snapshot_path = root / 'runtime' / 'paper' / date / 'paper-account-snapshot.json'
state_path = root / 'runtime' / 'paper' / date / 'paper-execution-state.json'
ledger_path = root / 'report' / date / 'paper-event-ledger.json'
review_path = root / 'report' / date / 'paper-execution-review.json'

submission = load(submission_path) or {}
snapshot = load(snapshot_path) or {}
state = load(state_path) or {}
ledger = load(ledger_path) or {}
review = load(review_path) or {}

print(f"模拟盘入场执行: {status}")
print(f"date: {date}")
print(f"submission: {submission_path if submission_path.exists() else 'missing'}")
print(f"execution_state: {state_path if state_path.exists() else 'missing'}")
print(f"execution_review: {review_path if review_path.exists() else 'missing'}")
if submission:
    print(f"submission_summary: {json.dumps(submission.get('summary'), ensure_ascii=False)}")
    for item in submission.get('submitted', []):
        print(
            "submitted: "
            f"{item.get('symbol')} qty={item.get('quantity')} "
            f"broker_order_id={item.get('broker_order_id')} intent_id={item.get('intent_id')}"
        )
    for item in submission.get('skipped_duplicates', []):
        intent = item.get('intent') or {}
        print(f"skipped_duplicate: {intent.get('symbol')} intent_id={intent.get('intent_id')}")
    for item in submission.get('errors', []):
        intent = item.get('intent') or {}
        print(f"error: {intent.get('symbol')} {item.get('error')}")
if snapshot:
    print(
        "snapshot: "
        f"account_channel={snapshot.get('account_channel')} "
        f"positions={len(snapshot.get('positions') or [])} "
        f"orders={len(snapshot.get('orders') or [])} "
        f"executions={len(snapshot.get('executions') or [])}"
    )
if state:
    print(f"sync_summary: {json.dumps(state.get('summary'), ensure_ascii=False)}")
    for item in state.get('orders', []):
        print(
            "order_state: "
            f"{item.get('symbol')} status={item.get('status')} "
            f"filled={item.get('filled_quantity')} broker_order_id={item.get('broker_order_id')}"
        )
if ledger:
    print(f"event_summary: {json.dumps(ledger.get('summary'), ensure_ascii=False)}")
if review:
    print(f"review_summary: {json.dumps(review.get('summary'), ensure_ascii=False)}")
if status != 'success':
    print('--- log tail ---')
    print('\n'.join(Path(log_path).read_text(errors='replace').splitlines()[-120:]))
PYMSG

"$CC" send -p "$PROJECT" -s "$SESSION" --stdin <"$MSG" >/dev/null 2>&1 || true
cat "$MSG"
[ "$STATUS" = "success" ]
