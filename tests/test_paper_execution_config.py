import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

from paper_execution_config import (
    broker_capability_matrix,
    ensure_paper_write_allowed,
    load_paper_execution_config,
    paper_execution_policy,
)


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

    def test_capability_matrix_marks_config_gated_and_unsupported_actions(self):
        config = {
            "broker_writes_enabled": True,
            "allow_entry_submit": True,
            "allow_cancel": False,
            "allow_protective_stop": False,
            "allow_take_profit": False,
            "allow_break_even_stop_move": False,
            "allow_exit_cancel_replace": False,
            "allow_exit_submit": False,
        }

        matrix = broker_capability_matrix(config)
        actions = {item["action"]: item for item in matrix["actions"]}
        unsupported = {item["action"]: item for item in matrix["unsupported_actions"]}

        self.assertEqual(matrix["broker"], "longbridge")
        self.assertEqual(matrix["account_channel"], "lb_papertrading")
        self.assertEqual(actions["entry_submit"]["execution_status"], "enabled")
        self.assertEqual(actions["entry_submit"]["config_key"], "allow_entry_submit")
        self.assertIn("MO", actions["entry_submit"]["order_type"])
        self.assertEqual(actions["cancel"]["execution_status"], "config_disabled")
        self.assertIn("MIT", actions["protective_stop"]["order_type"])
        self.assertIn("TSLPPCT", actions["protective_stop"]["order_type"])
        self.assertEqual(actions["break_even_stop_move"]["config_key"], "allow_break_even_stop_move")
        self.assertIn("LIT", actions["break_even_stop_move"]["order_type"])
        self.assertIn("TSLPPCT", actions["break_even_stop_move"]["order_type"])
        self.assertEqual(actions["take_profit_stop_resize"]["config_key"], "allow_take_profit_stop_resize")
        self.assertEqual(actions["exit_cancel_replace"]["config_key"], "allow_exit_cancel_replace")
        self.assertEqual(actions["exit_submit"]["config_key"], "allow_exit_submit")
        self.assertIn("native_oco", unsupported)
        self.assertNotIn("market_entry", unsupported)

    def test_policy_summarizes_allowed_and_dry_run_only_actions(self):
        config = {
            "broker_writes_enabled": True,
            "allow_entry_submit": True,
            "allow_cancel": False,
        }

        policy = paper_execution_policy(config)

        self.assertEqual(policy["allowed_broker_writes"], ["entry_submit"])
        self.assertIn("cancel", policy["dry_run_only_actions"])
        self.assertIn("protective_stop", policy["dry_run_only_actions"])
        self.assertIn("break_even_stop_move", policy["dry_run_only_actions"])
        self.assertIn("exit_submit", policy["dry_run_only_actions"])

    def test_exit_submit_and_cancel_replace_have_separate_gates(self):
        config = {
            "broker_writes_enabled": True,
            "allow_exit_cancel_replace": False,
            "allow_exit_submit": False,
        }

        with self.assertRaises(PermissionError):
            ensure_paper_write_allowed(config, execute=True, action="exit_cancel_replace")
        with self.assertRaises(PermissionError):
            ensure_paper_write_allowed(config, execute=True, action="exit_submit")

        config["allow_exit_cancel_replace"] = True
        config["allow_exit_submit"] = True
        ensure_paper_write_allowed(config, execute=True, action="exit_cancel_replace")
        ensure_paper_write_allowed(config, execute=True, action="exit_submit")

    def test_break_even_stop_move_has_separate_gate(self):
        config = {
            "broker_writes_enabled": True,
            "allow_cancel": True,
            "allow_protective_stop": True,
            "allow_break_even_stop_move": False,
        }

        with self.assertRaises(PermissionError):
            ensure_paper_write_allowed(config, execute=True, action="break_even_stop_move")

        config["allow_break_even_stop_move"] = True
        ensure_paper_write_allowed(config, execute=True, action="break_even_stop_move")

    def test_take_profit_stop_resize_has_separate_gate(self):
        config = {
            "broker_writes_enabled": True,
            "allow_take_profit": True,
            "allow_take_profit_stop_resize": False,
        }

        with self.assertRaises(PermissionError):
            ensure_paper_write_allowed(config, execute=True, action="take_profit_stop_resize")

        config["allow_take_profit_stop_resize"] = True
        ensure_paper_write_allowed(config, execute=True, action="take_profit_stop_resize")

    def test_intraday_entry_gate_is_supported_but_separate_from_entry_submit(self):
        config = {
            "broker_writes_enabled": True,
            "allow_entry_submit": True,
            "allow_intraday_entry_submit": False,
        }

        matrix = broker_capability_matrix(config)
        actions = {item["action"]: item for item in matrix["actions"]}
        policy = paper_execution_policy(config)

        self.assertEqual(actions["entry_submit"]["execution_status"], "enabled")
        self.assertEqual(actions["intraday_entry_submit"]["execution_status"], "config_disabled")
        self.assertIn("intraday_entry_submit", policy["dry_run_only_actions"])
        with self.assertRaises(PermissionError):
            ensure_paper_write_allowed(config, execute=True, action="intraday_entry_submit")

        config["allow_intraday_entry_submit"] = True
        ensure_paper_write_allowed(config, execute=True, action="intraday_entry_submit")

    def test_experimental_micro_paper_has_separate_gate(self):
        config = {
            "broker_writes_enabled": True,
            "allow_intraday_entry_submit": True,
            "allow_experimental_micro_paper": False,
        }

        matrix = broker_capability_matrix(config)
        actions = {item["action"]: item for item in matrix["actions"]}
        self.assertEqual(actions["experimental_micro_paper"]["workflow"], "experimental-micro-paper-entry")
        self.assertEqual(actions["experimental_micro_paper"]["config_key"], "allow_experimental_micro_paper")
        self.assertEqual(actions["experimental_micro_paper"]["execution_status"], "config_disabled")
        with self.assertRaises(PermissionError):
            ensure_paper_write_allowed(config, execute=True, action="experimental_micro_paper")

        config["allow_experimental_micro_paper"] = True
        ensure_paper_write_allowed(config, execute=True, action="experimental_micro_paper")

    def test_order_replace_has_separate_gate(self):
        config = {
            "broker_writes_enabled": True,
            "allow_order_replace": False,
        }

        matrix = broker_capability_matrix(config)
        actions = {item["action"]: item for item in matrix["actions"]}
        self.assertEqual(actions["order_replace"]["execution_status"], "config_disabled")
        with self.assertRaises(PermissionError):
            ensure_paper_write_allowed(config, execute=True, action="order_replace")

        config["allow_order_replace"] = True
        ensure_paper_write_allowed(config, execute=True, action="order_replace")


if __name__ == "__main__":
    unittest.main()
