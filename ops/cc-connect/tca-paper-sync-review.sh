#!/usr/bin/env bash
set -u

REPO="${TCA_REPO:-/home/admin_ryan/repo/Trading-Copilot-Agent}"
CC="${CC_CONNECT_BIN:-/home/admin_ryan/.local/bin/cc-connect}"
PROJECT="${CC_CONNECT_PROJECT:-trading-copilot}"
SESSION="${CC_CONNECT_SESSION:-feishu:oc_0df4740c94656aaa83249668668c3994:ou_f60f6e25add2b35cc00bb933b6e3960c}"
DATE_ARG="${1:-}"
DATE="${DATE_ARG:-$(TZ=Asia/Shanghai date -d yesterday +%F)}"
CONFIG="${TCA_PAPER_EXECUTION_CONFIG:-config/paper_execution.local.json}"
CANCEL_EXECUTE="${TCA_PAPER_CANCEL_EXECUTE:-0}"
STOP_EXECUTE="${TCA_PAPER_PROTECTIVE_STOP_EXECUTE:-0}"
TP_EXECUTE="${TCA_PAPER_TAKE_PROFIT_EXECUTE:-0}"
PLAN_EXIT_EXECUTE="${TCA_PAPER_PLAN_EXIT_EXECUTE:-0}"
BE_EXECUTE="${TCA_PAPER_BREAK_EVEN_STOP_EXECUTE:-0}"
APPEND_LESSONS="${TCA_PAPER_APPEND_LESSONS:-1}"
STRATEGY_REVIEW="${TCA_PAPER_STRATEGY_REVIEW:-1}"
LOG="$(mktemp)"
MSG="$(mktemp)"
STATUS="success"

cd "$REPO" || exit 1

ORDERS="runtime/paper/$DATE/paper-orders.jsonl"
if [ ! -s "$ORDERS" ]; then
  printf "模拟盘订单同步与复盘: skipped\ndate: %s\nreason: no submitted paper orders journal: %s\n" "$DATE" "$ORDERS" >"$MSG"
  "$CC" send -p "$PROJECT" -s "$SESSION" --stdin <"$MSG" >/dev/null 2>&1 || true
  cat "$MSG"
  exit 0
fi

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

LIFECYCLE_CMD=(python3 script/trading_copilot.py paper-lifecycle --date "$DATE" --paper-execution-config "$CONFIG")
if [ "$CANCEL_EXECUTE" = "1" ]; then
  LIFECYCLE_CMD+=(--execute-cancel)
fi
if [ "$STOP_EXECUTE" = "1" ]; then
  LIFECYCLE_CMD+=(--execute-protective-stop)
fi
if [ "$TP_EXECUTE" = "1" ]; then
  LIFECYCLE_CMD+=(--execute-take-profit)
fi
if [ "$PLAN_EXIT_EXECUTE" = "1" ]; then
  LIFECYCLE_CMD+=(--execute-exit)
fi
if [ "$BE_EXECUTE" = "1" ]; then
  LIFECYCLE_CMD+=(--execute-break-even-stop)
fi
if [ "$APPEND_LESSONS" = "1" ]; then
  LIFECYCLE_CMD+=(--append-lessons)
fi
if [ "$STRATEGY_REVIEW" = "1" ]; then
  LIFECYCLE_CMD+=(--strategy-review)
fi

run_step "${LIFECYCLE_CMD[@]}"

python3 - "$DATE" "$STATUS" "$LOG" "$REPO" "$CANCEL_EXECUTE" "$STOP_EXECUTE" "$TP_EXECUTE" "$PLAN_EXIT_EXECUTE" "$BE_EXECUTE" >"$MSG" <<'PYMSG'
import json
import sys
from pathlib import Path

date, status, log_path, repo_root, cancel_execute, stop_execute, tp_execute, plan_exit_execute, be_execute = sys.argv[1:10]
root = Path(repo_root)

def load(path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception as exc:
        return {'_load_error': str(exc)}

state_path = root / 'runtime' / 'paper' / date / 'paper-execution-state.json'
cancel_path = root / 'report' / date / 'paper-order-cancel-plan.json'
stop_path = root / 'report' / date / 'paper-protective-stop-plan.json'
tp_path = root / 'report' / date / 'paper-take-profit-plan.json'
exit_path = root / 'report' / date / 'paper-exit-plan.json'
be_path = root / 'report' / date / 'paper-break-even-stop-plan.json'
ledger_path = root / 'report' / date / 'paper-event-ledger.json'
review_path = root / 'report' / date / 'paper-execution-review.json'
lessons_path = root / 'report' / date / 'paper-learning-lessons.json'
strategy_path = root / 'report' / 'strategy' / 'paper-strategy-review.json'

state = load(state_path) or {}
cancel = load(cancel_path) or {}
stop = load(stop_path) or {}
tp = load(tp_path) or {}
exit_plan = load(exit_path) or {}
be = load(be_path) or {}
ledger = load(ledger_path) or {}
review = load(review_path) or {}
lessons = load(lessons_path) or {}
strategy = load(strategy_path) or {}

print(f"模拟盘订单同步与复盘: {status}")
print(f"date: {date}")
print(f"cancel_execute_policy: {'enabled' if cancel_execute == '1' else 'dry_run'}")
print(f"protective_stop_execute_policy: {'enabled' if stop_execute == '1' else 'dry_run'}")
print(f"take_profit_execute_policy: {'enabled' if tp_execute == '1' else 'dry_run'}")
print(f"plan_exit_execute_policy: {'enabled' if plan_exit_execute == '1' else 'dry_run'}")
print(f"break_even_stop_execute_policy: {'enabled' if be_execute == '1' else 'dry_run'}")
for path in [state_path, cancel_path, stop_path, tp_path, exit_path, be_path, ledger_path, review_path, lessons_path, strategy_path]:
    print(f"artifact: {path if path.exists() else 'missing'}")
if state:
    print(f"sync_summary: {json.dumps(state.get('summary'), ensure_ascii=False)}")
    for item in state.get('orders', []):
        print(
            "order_state: "
            f"{item.get('symbol')} status={item.get('status')} "
            f"filled={item.get('filled_quantity')} broker_order_id={item.get('broker_order_id')}"
        )
if cancel:
    print(f"cancel_summary: {json.dumps(cancel.get('summary'), ensure_ascii=False)}")
if stop:
    print(f"protective_stop_summary: {json.dumps(stop.get('summary'), ensure_ascii=False)}")
if tp:
    print(f"take_profit_summary: {json.dumps(tp.get('summary'), ensure_ascii=False)}")
if exit_plan:
    print(f"plan_exit_summary: {json.dumps(exit_plan.get('summary'), ensure_ascii=False)}")
if be:
    print(f"break_even_summary: {json.dumps(be.get('summary'), ensure_ascii=False)}")
if ledger:
    print(f"event_summary: {json.dumps(ledger.get('summary'), ensure_ascii=False)}")
if review:
    print(f"review_summary: {json.dumps(review.get('summary'), ensure_ascii=False)}")
if lessons:
    print(f"lessons_summary: {json.dumps(lessons.get('summary'), ensure_ascii=False)}")
if strategy:
    print(f"strategy_summary: {json.dumps(strategy.get('summary'), ensure_ascii=False)}")
if status != 'success':
    print('--- log tail ---')
    print('\n'.join(Path(log_path).read_text(errors='replace').splitlines()[-140:]))
PYMSG

"$CC" send -p "$PROJECT" -s "$SESSION" --stdin <"$MSG" >/dev/null 2>&1 || true
cat "$MSG"
[ "$STATUS" = "success" ]
