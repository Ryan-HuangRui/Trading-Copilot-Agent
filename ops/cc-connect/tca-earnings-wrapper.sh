#!/usr/bin/env bash
# cc-connect cron MUST set mute=true; only earnings_daily finalizer can send.
set -euo pipefail
REPO="${TCA_REPO:-/home/admin_ryan/repo/Trading-Copilot-Agent}"
cd "$REPO"
mkdir -p runtime/earnings/logs
run_stamp="$(date +%Y%m%d-%H%M%S)-$$"
exec >"runtime/earnings/logs/wrapper-${run_stamp}.log" 2>&1
# The local contact file is parsed as JSON by Python; never evaluated as shell code.
exec python3 - "$REPO" "$@" <<'PY'
import json, os, pathlib, sys
root = pathlib.Path(sys.argv[1]).resolve()
contact = root / 'runtime/earnings/operator.json'
if contact.exists():
    if not contact.resolve().is_relative_to(root / 'runtime/earnings'):
        raise ValueError('operator config must remain inside earnings runtime')
    data = json.loads(contact.read_text())
    user_agent = data.get('sec_user_agent', '')
    if user_agent:
        os.environ['TCA_SEC_USER_AGENT'] = user_agent
os.execv(sys.executable, [sys.executable, str(root / 'script/earnings_continuation.py'),
                         '--repo-root', str(root), '--send', *sys.argv[2:]])
PY
