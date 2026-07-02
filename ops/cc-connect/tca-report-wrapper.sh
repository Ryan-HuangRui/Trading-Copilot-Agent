#!/usr/bin/env bash
set -u

REPO="${TCA_REPO:-/home/admin_ryan/repo/Trading-Copilot-Agent}"
SESSION_NAME="${1:-}"
PROJECT="${CC_CONNECT_PROJECT:-trading-copilot}"
SESSION="${CC_CONNECT_SESSION:-feishu:oc_0df4740c94656aaa83249668668c3994:ou_f60f6e25add2b35cc00bb933b6e3960c}"
CODEX_BIN="${CODEX_BIN:-/home/admin_ryan/.local/bin/codex}"
CC_CONNECT_BIN="${CC_CONNECT_BIN:-/home/admin_ryan/.local/bin/cc-connect}"

case "$SESSION_NAME" in
  pre-market)
    PROMPT_FILE="$REPO/ops/cc-connect/tca-pre-market-wrapper.prompt.md"
    SUMMARY_FILE="$REPO/runtime/cc-connect/out/pre-market-summary.md"
    META_FILE="$REPO/runtime/cc-connect/out/pre-market-delivery.env"
    LOG_ROOT="$REPO/runtime/cc-connect/logs/pre-market"
    ;;
  post-market)
    PROMPT_FILE="$REPO/ops/cc-connect/tca-post-market-wrapper.prompt.md"
    SUMMARY_FILE="$REPO/runtime/cc-connect/out/post-market-summary.md"
    META_FILE="$REPO/runtime/cc-connect/out/post-market-delivery.env"
    LOG_ROOT="$REPO/runtime/cc-connect/logs/post-market"
    ;;
  *)
    printf 'usage: %s pre-market|post-market\n' "$0" >&2
    exit 64
    ;;
esac

mkdir -p "$LOG_ROOT"
mkdir -p "$(dirname "$SUMMARY_FILE")"

run_ts="$(date +%Y%m%d-%H%M%S)"
human_ts="$(date '+%Y-%m-%d %H:%M:%S %z')"
log_file="$LOG_ROOT/$run_ts.log"
rm -f "$SUMMARY_FILE" "$META_FILE"

{
  printf 'Trading-Copilot scheduled report started: %s\n' "$human_ts"
  printf 'session: %s\n' "$SESSION_NAME"
  printf 'repo: %s\n' "$REPO"
  printf 'prompt_file: %s\n' "$PROMPT_FILE"
  printf 'summary_file: %s\n' "$SUMMARY_FILE"
  printf 'meta_file: %s\n\n' "$META_FILE"

  if [ ! -x "$CODEX_BIN" ]; then
    printf 'codex binary not executable: %s\n' "$CODEX_BIN"
    exit 127
  fi
  if [ ! -f "$PROMPT_FILE" ]; then
    printf 'prompt file not found: %s\n' "$PROMPT_FILE"
    exit 66
  fi

  "$CODEX_BIN" exec -C "$REPO" --sandbox danger-full-access - < "$PROMPT_FILE"
} >"$log_file" 2>&1

status=$?

if [ ! -x "$CC_CONNECT_BIN" ]; then
  printf 'cc-connect binary not executable: %s\n' "$CC_CONNECT_BIN" >>"$log_file"
  exit 127
fi

if [ "$status" -ne 0 ]; then
  "$CC_CONNECT_BIN" send --project "$PROJECT" --session "$SESSION" --stdin >>"$log_file" 2>&1 <<EOF
Trading-Copilot 定时报告摘要

状态：执行失败，未生成正常摘要。
流程：$SESSION_NAME
时间：$human_ts
原因：codex exec 退出码 $status
日志：$log_file
EOF
  exit "$status"
fi

if [ ! -s "$SUMMARY_FILE" ]; then
  "$CC_CONNECT_BIN" send --project "$PROJECT" --session "$SESSION" --stdin >>"$log_file" 2>&1 <<EOF
Trading-Copilot 定时报告摘要

状态：执行完成，但未找到可发送的摘要文件。
流程：$SESSION_NAME
说明：中间过程已写入本地日志，未推送到飞书。
日志：$log_file
EOF
  send_status=$?
  printf 'Feishu fallback send status: %s\n' "$send_status" >>"$log_file"
  exit "$send_status"
fi

"$CC_CONNECT_BIN" send --project "$PROJECT" --session "$SESSION" --stdin < "$SUMMARY_FILE" >>"$log_file" 2>&1
send_status=$?
printf 'Feishu summary send status: %s\n' "$send_status" >>"$log_file"
if [ "$send_status" -ne 0 ]; then
  exit "$send_status"
fi

get_meta_value() {
  key="$1"
  if [ -f "$META_FILE" ]; then
    sed -n "s/^${key}=//p" "$META_FILE" | head -1
  fi
}

should_mark="$(get_meta_value SHOULD_MARK_SENT)"
if [ "$should_mark" = "1" ]; then
  delivery_kind="$(get_meta_value DELIVERY_KIND)"
  delivery_date="$(get_meta_value DELIVERY_DATE)"
  case "$delivery_kind" in
    exec-brief|post-market) ;;
    *)
      printf 'invalid delivery kind for mark-sent: %s\n' "$delivery_kind" >>"$log_file"
      exit 65
      ;;
  esac
  case "$delivery_date" in
    ????-??-??) ;;
    *)
      printf 'invalid delivery date for mark-sent: %s\n' "$delivery_date" >>"$log_file"
      exit 65
      ;;
  esac
  python3 "$REPO/script/report_delivery_guard.py" --kind "$delivery_kind" --date "$delivery_date" --mark-sent >>"$log_file" 2>&1
  mark_status=$?
  printf 'delivery guard mark-sent status: %s\n' "$mark_status" >>"$log_file"
  exit "$mark_status"
fi

printf 'delivery guard mark-sent skipped: SHOULD_MARK_SENT=%s\n' "$should_mark" >>"$log_file"
exit 0
