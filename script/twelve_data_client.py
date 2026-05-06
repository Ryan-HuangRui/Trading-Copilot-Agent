from __future__ import annotations

import json
import os
import time
import fcntl
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

from urllib.parse import urlencode
from urllib.request import urlopen, Request

BASE_URL = "https://api.twelvedata.com"


@dataclass
class RateLimiter:
    max_calls: int = 8
    period_seconds: int = 60
    state_file: str | None = None

    def __post_init__(self):
        self._calls: List[float] = []

    def _wait_local(self):
        now = time.time()
        self._calls = [t for t in self._calls if now - t < self.period_seconds]
        if len(self._calls) >= self.max_calls:
            earliest = self._calls[0]
            sleep_s = self.period_seconds - (now - earliest) + 0.05
            if sleep_s > 0:
                time.sleep(sleep_s)
        self._calls.append(time.time())

    def _wait_global(self):
        if not self.state_file:
            self._wait_local()
            return

        sf = Path(self.state_file)
        sf.parent.mkdir(parents=True, exist_ok=True)
        with open(sf, "a+", encoding="utf-8") as f:
            while True:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                f.seek(0)
                raw = f.read().strip()
                try:
                    payload = json.loads(raw) if raw else {}
                except Exception:
                    payload = {}

                calls = payload.get("calls", [])
                now = time.time()
                calls = [t for t in calls if now - t < self.period_seconds]

                if len(calls) < self.max_calls:
                    calls.append(now)
                    f.seek(0)
                    f.truncate(0)
                    f.write(json.dumps({"calls": calls}))
                    f.flush()
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                    break

                earliest = calls[0]
                sleep_s = self.period_seconds - (now - earliest) + 0.05
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                if sleep_s > 0:
                    time.sleep(sleep_s)

    def wait_if_needed(self):
        self._wait_global()


class TwelveDataClient:
    def __init__(
        self,
        api_key: str | None = None,
        max_calls_per_minute: int = 8,
        state_file: str | None = None,
    ):
        self.api_key = api_key or os.getenv("TWELVE_DATA_API_KEY", "")
        if not self.api_key:
            raise ValueError("TWELVE_DATA_API_KEY is required")
        self.limiter = RateLimiter(
            max_calls=max_calls_per_minute,
            period_seconds=60,
            state_file=state_file,
        )

    def _get(self, endpoint: str, params: Dict) -> Dict:
        self.limiter.wait_if_needed()
        query = {"apikey": self.api_key, **params}
        url = f"{BASE_URL}/{endpoint}?{urlencode(query)}"
        req = Request(url, headers={"User-Agent": "trading-copilot-agent/1.0"})
        with urlopen(req, timeout=20) as resp:
            status = getattr(resp, "status", 200)
            if status >= 400:
                raise RuntimeError(f"HTTP error: {status}")
            raw = resp.read().decode("utf-8")
        data = json.loads(raw)
        if isinstance(data, dict) and data.get("status") == "error":
            raise RuntimeError(f"TwelveData error: {data.get('message')}")
        return data

    def time_series(self, symbol: str, interval: str = "1day", outputsize: int = 200) -> Dict:
        return self._get(
            "time_series",
            {
                "symbol": symbol,
                "interval": interval,
                "outputsize": outputsize,
                "format": "JSON",
                "timezone": "UTC",
            },
        )


def save_json(path: str | Path, payload: Dict):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
