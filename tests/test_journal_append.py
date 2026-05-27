import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class JournalAppendTest(unittest.TestCase):
    def test_append_signal_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "script" / "journal_append.py"),
                    "signal",
                    "--repo-root",
                    tmp,
                    "--date",
                    "2026-05-26",
                    "--session",
                    "pre-market",
                    "--symbol",
                    "mu",
                    "--setup",
                    "breakout_pullback_continuation.md",
                    "--status",
                    "planned",
                    "--source-report",
                    "report/2026-05-26/pre-market.md",
                    "--trigger",
                    "breaks prior high",
                    "--invalidation",
                    "falls back into range",
                    "--risk-r",
                    "1.0",
                ],
                check=False,
                text=True,
                capture_output=True,
            )

            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["status"], "success")
            journal_path = Path(tmp) / "runtime" / "journal" / "signals.jsonl"
            line = journal_path.read_text(encoding="utf-8").strip()
            record = json.loads(line)
            self.assertEqual(record["symbol"], "MU")
            self.assertEqual(record["status"], "planned")
            self.assertEqual(record["risk_r"], 1.0)


if __name__ == "__main__":
    unittest.main()
