#!/usr/bin/env bash
set -u

REPO="${TCA_REPO:-/home/admin_ryan/repo/Trading-Copilot-Agent}"
DATE_ARG="${1:-}"
DATE="${DATE_ARG:-$(TZ=America/New_York date +%F)}"
LOG_DIR="$REPO/runtime/intraday"
LOCK_FILE="$LOG_DIR/codex-monitor.lockfile"
CODEX_BIN="${CODEX_BIN:-/home/admin_ryan/.local/bin/codex}"
CC_BIN="${CC_CONNECT_BIN:-/home/admin_ryan/.local/bin/cc-connect}"
PROJECT="${CC_CONNECT_PROJECT:-trading-copilot}"
SESSION="${CC_CONNECT_SESSION:-feishu:oc_0df4740c94656aaa83249668668c3994:ou_f60f6e25add2b35cc00bb933b6e3960c}"
PAPER_CONFIG="${TCA_PAPER_EXECUTION_CONFIG:-config/paper_execution.local.json}"
ENABLE_PAPER_DRY_RUN="${TCA_INTRADAY_ENABLE_PAPER_DRY_RUN:-0}"
ENABLE_PAPER_EXECUTE="${TCA_INTRADAY_PAPER_EXECUTE:-0}"
ENABLE_PAPER_LIFECYCLE="${TCA_INTRADAY_ENABLE_PAPER_LIFECYCLE:-0}"
ENABLE_EXIT_EXECUTE="${TCA_INTRADAY_EXIT_EXECUTE:-0}"
ENABLE_CANCEL_EXECUTE="${TCA_INTRADAY_CANCEL_EXECUTE:-0}"
ENABLE_PROTECTIVE_STOP_EXECUTE="${TCA_INTRADAY_PROTECTIVE_STOP_EXECUTE:-0}"
ENABLE_TAKE_PROFIT_EXECUTE="${TCA_INTRADAY_TAKE_PROFIT_EXECUTE:-0}"
ENABLE_RESIZE_STOP_BEFORE_TAKE_PROFIT="${TCA_INTRADAY_RESIZE_STOP_BEFORE_TAKE_PROFIT:-0}"
ENABLE_BREAK_EVEN_STOP_EXECUTE="${TCA_INTRADAY_BREAK_EVEN_STOP_EXECUTE:-0}"

cd "$REPO" || exit 1
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/codex-monitor-cron.log"

log_json() {
  printf '%s %s\n' "$(date -Is)" "$1" >>"$LOG_FILE"
}

exec 9>"$LOCK_FILE" || exit 0
if ! flock -n 9; then
  log_json "{\"status\":\"skipped\",\"date\":\"$DATE\",\"reason\":\"previous_intraday_codex_run_still_active\"}"
  exit 0
fi

GUARD="$(python3 script/trading_day_guard.py --date "$DATE" --format json 2>>"$LOG_FILE" || true)"
IS_TRADING_DAY="$(python3 - "$GUARD" <<'PY'
import json
import sys
try:
    payload = json.loads(sys.argv[1])
except Exception:
    print("0")
else:
    print("1" if payload.get("is_trading_day") else "0")
PY
)"

if [ "$IS_TRADING_DAY" != "1" ]; then
  log_json "{\"status\":\"skipped\",\"date\":\"$DATE\",\"reason\":\"non_trading_day\"}"
  exit 0
fi

if [ "${TCA_INTRADAY_FORCE:-0}" != "1" ]; then
  MARKET_SESSION="$(python3 - "$DATE" <<'PY'
import datetime as dt
import json
import sys
from zoneinfo import ZoneInfo

date = sys.argv[1]
now = dt.datetime.now(ZoneInfo("America/New_York"))
start = dt.time(9, 30)
end = dt.time(16, 0)
is_regular = now.date().isoformat() == date and now.weekday() < 5 and start <= now.time() <= end
print(json.dumps({
    "is_regular_session": is_regular,
    "market_time": now.isoformat(timespec="seconds"),
    "session": "09:30-16:00 America/New_York",
}, ensure_ascii=False))
PY
)"
  IS_REGULAR_SESSION="$(python3 - "$MARKET_SESSION" <<'PY'
import json
import sys
try:
    payload = json.loads(sys.argv[1])
except Exception:
    print("0")
else:
    print("1" if payload.get("is_regular_session") else "0")
PY
)"
  if [ "$IS_REGULAR_SESSION" != "1" ]; then
    log_json "{\"status\":\"skipped\",\"date\":\"$DATE\",\"reason\":\"outside_regular_session\"}"
    exit 0
  fi
fi

RUN_LOG="$LOG_DIR/codex-monitor-${DATE}-$(date +%Y%m%dT%H%M%S%z).log"
PROMPT="你是 Trading-Copilot-Agent 的 cc-connect 盘中 Codex 盯盘定时任务。进入 $REPO，日期 $DATE。

安全边界：
- 禁止真实账户写操作；Longbridge 真实账户只读。
- 模拟盘写操作只允许通过本 repo 的 paper workflow，且必须同时满足 --execute、lb_papertrading 校验、以及 $PAPER_CONFIG 中的对应 gate。
- 不输出确定性买卖指令；盘中交易机会只能写成带触发、失效、风险的条件化模拟盘计划。
- 不提交 git，不修改配置，不修改代码；只允许写当天 report/runtime 运行产物。

默认只读流程：
1. 执行：bash ops/cc-connect/tca-intraday-notify.sh $DATE
2. 读取输出 JSON、report/latest-monitor.json、report/$DATE/intraday.md、runtime/intraday/$DATE/state.json、runtime/intraday/$DATE/events.jsonl。
3. 如果 status=skipped 或 should_send=false，只在最终回复说明原因，不要额外发送飞书。
4. 如果 should_send=true，tca-intraday-notify.sh 已经发送 intraday-notification.md，不要重复发送。

盘中模拟盘 dry-run 开关：TCA_INTRADAY_ENABLE_PAPER_DRY_RUN=$ENABLE_PAPER_DRY_RUN。
- 只有该值为 1 时，才可以基于最新 monitor 数据、盘前计划、人工观察列表、knowledge/refined 生成或更新 report/$DATE/monitor-signals.json。
- 先运行：
  python3 script/trading_copilot.py intraday-opportunity-context --date $DATE
- 读取 report/$DATE/intraday-opportunity-context.json。它提供候选扫描、盘前计划、盘中状态、paper 状态和 sidecar_template。
- 如果没有高质量条件化机会，保持 watch_only，并运行 dry-run 或说明没有 ready 订单。
- 若要升级为 conditional_executable，必须由你基于 context、knowledge/refined 和完整 Trade Plan Card 主观判断；不得由 extract-monitor-signals 自动升级。
- 写出 report/$DATE/monitor-signals.json 后，必须随后运行：
  python3 script/trading_copilot.py validate-trade-plan --session monitor --date $DATE --signals report/$DATE/monitor-signals.json
  python3 script/trading_copilot.py paper-account-snapshot --date $DATE
  python3 script/trading_copilot.py intraday-dry-run --date $DATE --signals report/$DATE/monitor-signals.json
- dry-run 结果只写产物，不发送下单成功消息。

盘中模拟盘入场执行开关：TCA_INTRADAY_PAPER_EXECUTE=$ENABLE_PAPER_EXECUTE。
- 只有 dry-run summary.ready > 0 且该值为 1 时，才运行：
  python3 script/trading_copilot.py intraday-paper-entry --date $DATE --require-validation --paper-execution-config $PAPER_CONFIG --execute
- 执行后必须刷新：
  python3 script/trading_copilot.py paper-account-snapshot --date $DATE
  python3 script/trading_copilot.py paper-order-sync --date $DATE
  python3 script/trading_copilot.py paper-event-ledger --date $DATE
  python3 script/trading_copilot.py paper-execution-review --date $DATE
- 如果有 submitted/skipped/error，使用 cc-connect send 向当前飞书会话发送一条简短模拟盘状态，包含 artifact 路径。

盘中模拟盘生命周期开关：TCA_INTRADAY_ENABLE_PAPER_LIFECYCLE=$ENABLE_PAPER_LIFECYCLE。
- 只有 ENABLE_PAPER_LIFECYCLE=1 且 runtime/paper/$DATE/paper-orders.jsonl 存在时，才运行同步与 exit 管理：
  python3 script/trading_copilot.py paper-lifecycle --date $DATE --paper-execution-config $PAPER_CONFIG --append-lessons --strategy-review
- exit 执行必须逐项打开，不能因为 TCA_INTRADAY_EXIT_EXECUTE=$ENABLE_EXIT_EXECUTE 就一次性打开全部：
  - TCA_INTRADAY_CANCEL_EXECUTE=$ENABLE_CANCEL_EXECUTE；只有该值为 1 且 config 允许时才追加 --execute-cancel。
  - TCA_INTRADAY_PROTECTIVE_STOP_EXECUTE=$ENABLE_PROTECTIVE_STOP_EXECUTE；只有该值为 1 且 config 允许时才追加 --execute-protective-stop。
  - TCA_INTRADAY_TAKE_PROFIT_EXECUTE=$ENABLE_TAKE_PROFIT_EXECUTE；只有该值为 1 且 config 允许时才追加 --execute-take-profit。
  - TCA_INTRADAY_RESIZE_STOP_BEFORE_TAKE_PROFIT=$ENABLE_RESIZE_STOP_BEFORE_TAKE_PROFIT；只有该值为 1 且 config 允许 take_profit_stop_resize 时，TP1 执行才可追加 --resize-stop-before-take-profit。
  - TCA_INTRADAY_BREAK_EVEN_STOP_EXECUTE=$ENABLE_BREAK_EVEN_STOP_EXECUTE；只有该值为 1 且 config 允许时才追加 --execute-break-even-stop。
- 该 wrapper 已包含 account-snapshot、paper-order-sync、exit 计划、再次同步、paper-event-ledger、paper-execution-review。
- lifecycle dry-run 或执行后，如果有 executed/submitted/moved/errors/candidate lessons，使用 cc-connect send 发送一条简短飞书状态。

失败处理：
- 任一步失败时，使用 cc-connect send 发送简短失败状态，包含 date、失败命令、reason、关键 log tail。
- 最终回复给 wrapper 的内容要短，列出执行了哪些阶段、是否发送飞书、主要 artifact。"

"$CODEX_BIN" exec --cd "$REPO" --sandbox danger-full-access --output-last-message "$LOG_DIR/last-codex-monitor-message.txt" "$PROMPT" >>"$RUN_LOG" 2>&1
RC=$?
if [ "$RC" -ne 0 ]; then
  FAILURE="$LOG_DIR/codex-monitor-failure.md"
  {
    echo "盘中 Codex 盯盘失败"
    echo "date: $DATE"
    echo "exit_code: $RC"
    echo "log: $RUN_LOG"
    echo
    echo "最近日志："
    tail -80 "$RUN_LOG" 2>/dev/null || true
  } >"$FAILURE"
  "$CC_BIN" send -p "$PROJECT" -s "$SESSION" --stdin <"$FAILURE" >>"$RUN_LOG" 2>&1 || true
fi
exit 0
