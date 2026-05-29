import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

from paper_execution_config import ensure_paper_write_allowed, load_paper_execution_config


class PaperExecutionConfigTest(unittest.TestCase):
    def test_missing_config_defaults_to_no_broker_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            config, path = load_paper_execution_config(Path(tmp))

        self.assertTrue(str(path).endswith("config/paper_execution.json"))
        self.assertFalse(config["broker_writes_enabled"])
        with self.assertRaises(PermissionError):
            ensure_paper_write_allowed(config, execute=True, action="entry_submit")

    def test_loads_nested_config_and_allows_selected_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "config" / "paper_execution.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "paper_execution": {
                            "broker_writes_enabled": True,
                            "allow_entry_submit": True,
                            "allow_cancel": False,
                        }
                    }
                ),
                encoding="utf-8",
            )

            config, loaded_path = load_paper_execution_config(root)

        self.assertEqual(loaded_path, path)
        ensure_paper_write_allowed(config, execute=True, action="entry_submit")
        with self.assertRaises(PermissionError):
            ensure_paper_write_allowed(config, execute=True, action="cancel")

    def test_execute_flag_is_still_required(self):
        config = {"broker_writes_enabled": True, "allow_entry_submit": True}

        with self.assertRaises(PermissionError):
            ensure_paper_write_allowed(config, execute=False, action="entry_submit")


if __name__ == "__main__":
    unittest.main()
