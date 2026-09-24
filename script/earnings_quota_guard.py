"""Read Codex account limits without model calls; preserve an operator quota reserve."""
from __future__ import annotations
import json
from pathlib import Path
import selectors
import subprocess
import time
from earnings_common import atomic_write_json, read_json, utc_now

POLICY = 'runtime/earnings/quota-policy.json'
STATUS = 'runtime/earnings/quota-status.json'


def read_limits(binary: str, timeout: float = 25) -> dict:
    proc = subprocess.Popen([binary, 'app-server', '--stdio'], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
    def send(value):
        proc.stdin.write(json.dumps(value) + '\n'); proc.stdin.flush()
    selector = selectors.DefaultSelector(); selector.register(proc.stdout, selectors.EVENT_READ)
    try:
        send({'id': 1, 'method': 'initialize', 'params': {'clientInfo': {'name': 'earnings-quota-monitor', 'version': '1.0'}}})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not selector.select(timeout=min(1, max(0, deadline-time.monotonic()))): continue
            line = proc.stdout.readline()
            if not line: raise RuntimeError('account limit service closed')
            message = json.loads(line)
            if message.get('id') == 1:
                if 'error' in message: raise RuntimeError('account limit initialization failed')
                send({'method': 'initialized', 'params': {}})
                send({'id': 2, 'method': 'account/rateLimits/read', 'params': {}})
            elif message.get('id') == 2:
                if 'error' in message: raise RuntimeError('account limit query failed')
                return message['result']
        raise TimeoutError('account limit query timed out')
    finally:
        selector.close(); proc.terminate()
        try: proc.wait(timeout=5)
        except subprocess.TimeoutExpired: proc.kill(); proc.wait()
        proc.stdin.close(); proc.stdout.close()


def evaluate(result: dict, threshold: float) -> dict:
    limits = (result.get('rateLimitsByLimitId') or {}).get('codex') or result.get('rateLimits') or {}
    windows = []
    for name in ('primary', 'secondary'):
        window = limits.get(name)
        if not window: continue
        used = window.get('usedPercent')
        if isinstance(used, bool) or not isinstance(used, (int, float)) or not 0 <= used <= 100:
            raise ValueError('invalid usage percentage')
        windows.append({'name': name, 'remaining_percent': 100-used,
                        'window_minutes': window.get('windowDurationMins'), 'resets_at': window.get('resetsAt')})
    if not windows: raise ValueError('no account quota windows returned')
    remaining = min(w['remaining_percent'] for w in windows)
    paused = remaining < threshold or result.get('ordinaryUsageAllowed') is False
    return {'status': 'ok', 'remaining_percent': remaining, 'windows': windows,
            'pause_required': paused, 'reason': 'below_reserve' if remaining < threshold else
            'backend_disallows_usage' if paused else 'above_reserve'}


def refresh(root: Path, binary: str) -> dict:
    path = root / POLICY
    if not path.exists(): return {'enabled': False, 'pause_required': False}
    policy = read_json(path)
    if not policy.get('enabled'): return {'enabled': False, 'pause_required': False}
    threshold = float(policy['minimum_remaining_percent'])
    if not 0 <= threshold <= 100: raise ValueError('invalid quota reserve threshold')
    try: status = evaluate(read_limits(binary), threshold)
    except Exception as exc:
        # Unknown balance must not spend the operator's protected reserve.
        status = {'status': 'unknown', 'remaining_percent': None, 'pause_required': True,
                  'reason': 'quota_check_failed', 'error_type': type(exc).__name__}
    status.update(enabled=True, checked_at=utc_now(), minimum_remaining_percent=threshold)
    atomic_write_json(root / STATUS, status)
    return status


def require_quota(root: Path, binary: str) -> dict:
    status = refresh(root, binary)
    if status.get('pause_required'):
        raise RuntimeError('model_quota_exhausted: user reserve guard; ' + status['reason'])
    return status
