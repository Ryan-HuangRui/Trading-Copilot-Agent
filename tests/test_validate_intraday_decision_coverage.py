import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

import validate_intraday_decision_coverage


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class ValidateIntradayDecisionCoverageTest(unittest.TestCase):
    def args(self, root: Path) -> Namespace:
        return Namespace(
            repo_root=str(root),
            date="2026-05-26",
            context=None,
            signals=None,
        )

    def seed_context(self, root: Path) -> None:
        write_json(
            root / "report" / "2026-05-26" / "intraday-opportunity-context.json",
            {
                "sidecar_template": {
                    "signals": [
                        {"symbol": "MU"},
                        {"symbol": "NVDA"},
                    ]
                }
            },
        )

    def test_passes_when_monitor_sidecar_covers_observation_universe(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed_context(root)
            write_json(
                root / "report" / "2026-05-26" / "monitor-signals.json",
                {"signals": [{"symbol": "MU"}, {"symbol": "NVDA"}]},
            )

            result = validate_intraday_decision_coverage.validate(self.args(root))

            self.assertEqual(result["status"], "pass")
            self.assertEqual(result["summary"]["required_symbols"], 2)
            self.assertEqual(result["summary"]["covered_symbols"], 2)

    def test_fails_when_symbol_decision_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed_context(root)
            write_json(
                root / "report" / "2026-05-26" / "monitor-signals.json",
                {"signals": [{"symbol": "MU"}]},
            )

            result = validate_intraday_decision_coverage.validate(self.args(root))

            self.assertEqual(result["status"], "fail")
            self.assertEqual(result["missing_symbols"], ["NVDA"])


if __name__ == "__main__":
    unittest.main()
