#!/usr/bin/env bash
set -u

REPO="${TCA_REPO:-/home/admin_ryan/repo/Trading-Copilot-Agent}"
CC="${CC_CONNECT_BIN:-/home/admin_ryan/.local/bin/cc-connect}"
PROJECT="${CC_CONNECT_PROJECT:-trading-copilot}"
SESSION="${CC_CONNECT_SESSION:-feishu:oc_0df4740c94656aaa83249668668c3994:ou_f60f6e25add2b35cc00bb933b6e3960c}"
DATE_ARG="${1:-}"
DATE="${DATE_ARG:-$(TZ=America/New_York date +%F)}"
TOP_N="${TCA_INTRADAY_TOP_N:-5}"
INTERVAL="${TCA_INTRADAY_INTERVAL:-5min}"
STATE="${TCA_INTRADAY_MONITOR_STATE:-config/monitor_state.json}"
STATUS="success"
LOG="$(mktemp)"
RESULT="$(mktemp)"
TEMP_STATE=""

cd "$REPO" || exit 1

cleanup() {
  rm -f "$LOG" "$RESULT"
  if [ -n "$TEMP_STATE" ]; then
    rm -f "$TEMP_STATE"
  fi
}

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

GUARD="$(python3 script/trading_day_guard.py --date "$DATE" --format json 2>>"$LOG" || true)"
IS_TRADING_DAY="$(python3 - "$GUARD" <<'PY'
import json
import sys

try:
    payload = json.loads(sys.argv[1])
except Exception:
    print("unknown")
else:
    print("1" if payload.get("is_trading_day") else "0")
PY
)"

if [ "$IS_TRADING_DAY" != "1" ]; then
  python3 - "$RESULT" "$DATE" "$GUARD" <<'PY'
import json
import sys
from pathlib import Path

result_path, date, guard_text = sys.argv[1:4]
try:
    guard = json.loads(guard_text)
except Exception:
    guard = {"is_trading_day": None, "reason": "trading_day_guard_failed"}
payload = {
    "status": "skipped",
    "date": date,
    "should_send": False,
    "reason": guard.get("reason") or "non_trading_day",
    "guard": guard,
    "summary": {"unsent_events": 0},
}
Path(result_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
PY
  cat "$RESULT"
  cleanup
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
    python3 - "$RESULT" "$DATE" "$MARKET_SESSION" <<'PY'
import json
import sys
from pathlib import Path

result_path, date, session_text = sys.argv[1:4]
try:
    market_session = json.loads(session_text)
except Exception:
    market_session = {"is_regular_session": None}
payload = {
    "status": "skipped",
    "date": date,
    "should_send": False,
    "reason": "outside_regular_session",
    "market_session": market_session,
    "summary": {"unsent_events": 0},
}
Path(result_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
PY
    cat "$RESULT"
    cleanup
    exit 0
  fi
fi

if [ "${TCA_INTRADAY_SKIP_MONITOR:-0}" != "1" ]; then
  EFFECTIVE_STATE="$STATE"
  if [ "${TCA_INTRADAY_AUTO_UNIVERSE:-1}" != "0" ]; then
    TEMP_STATE="$(mktemp)"
    if python3 - "$REPO" "$DATE" "$TOP_N" "$STATE" "$TEMP_STATE" >>"$LOG" 2>&1 <<'PY'
import json
import sys
from pathlib import Path


def read_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return dict(default)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected JSON object")
    return payload


def normalize(value: object) -> str:
    symbol = str(value or "").strip().upper()
    if "." in symbol:
        symbol = symbol.split(".", 1)[0]
    return symbol


def add_symbol(symbols: list[str], value: object) -> None:
    symbol = normalize(value)
    if symbol and symbol not in symbols:
        symbols.append(symbol)


repo = Path(sys.argv[1])
date = sys.argv[2]
top_n = int(sys.argv[3])
state_path = Path(sys.argv[4])
if not state_path.is_absolute():
    state_path = repo / state_path
output_path = Path(sys.argv[5])

base = read_json(state_path, {"symbols": []})
symbols: list[str] = []

pre_market = read_json(repo / "report" / date / "pre-market-signals.json", {"signals": []})
for signal in pre_market.get("signals") or []:
    if isinstance(signal, dict):
        add_symbol(symbols, signal.get("symbol"))
    if len(symbols) >= top_n:
        break

manual = read_json(repo / "config" / "intraday_watchlist.json", {"symbols": []})
for item in manual.get("symbols") or manual.get("watchlist") or []:
    add_symbol(symbols, item.get("symbol") if isinstance(item, dict) else item)

for item in base.get("symbols") or []:
    add_symbol(symbols, item.get("symbol") if isinstance(item, dict) else item)

payload = dict(base)
payload["symbols"] = symbols
output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
print("effective monitor symbols:", ", ".join(symbols) if symbols else "none")
PY
    then
      EFFECTIVE_STATE="$TEMP_STATE"
    else
      STATUS="failed"
      echo "step failed building effective monitor state" >>"$LOG"
    fi
  fi
  if [ "$STATUS" = "success" ]; then
    run_step python3 script/trading_copilot.py monitor-brief --state "$EFFECTIVE_STATE" --interval "$INTERVAL"
  fi
fi

if [ "$STATUS" = "success" ]; then
  run_step python3 script/trading_copilot.py intraday-tracker --date "$DATE" --top-n "$TOP_N"
fi

if [ "$STATUS" = "success" ]; then
  echo >>"$LOG"
  echo "$ python3 script/intraday_event_notify.py --date $DATE --mark-sent" >>"$LOG"
  python3 script/intraday_event_notify.py --date "$DATE" --mark-sent >"$RESULT" 2>>"$LOG"
  rc=$?
  if [ "$rc" -ne 0 ]; then
    STATUS="failed"
    echo "step failed rc=$rc" >>"$LOG"
  fi
fi

if [ ! -s "$RESULT" ]; then
  python3 - "$RESULT" "$STATUS" <<'PY'
import json
import sys
from pathlib import Path

result_path, status = sys.argv[1:3]
payload = {"status": status, "should_send": False, "reason": "notification payload missing"}
Path(result_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
PY
fi

SHOULD_SEND="$(python3 - "$RESULT" <<'PY'
import json, sys
payload = json.loads(open(sys.argv[1], encoding="utf-8").read())
print("1" if payload.get("should_send") else "0")
PY
)"

MESSAGE="$(python3 - "$RESULT" <<'PY'
import json, sys
payload = json.loads(open(sys.argv[1], encoding="utf-8").read())
print(payload.get("message_output") or "")
PY
)"

if [ "$STATUS" = "success" ] && [ "$SHOULD_SEND" = "1" ] && [ -n "$MESSAGE" ] && [ -s "$MESSAGE" ]; then
  "$CC" send -p "$PROJECT" -s "$SESSION" --stdin <"$MESSAGE"
fi

cat "$RESULT"
if [ "$STATUS" != "success" ]; then
  echo "--- log tail ---"
  tail -120 "$LOG"
fi

cleanup
[ "$STATUS" = "success" ]
