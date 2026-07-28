import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

from validate_report import validate


GOOD_REPORT = """# 今日盘前完整报告（2026-05-26）

## 总览
- 今日最多3个重点标的：MU

## 消息层汇总
### 特朗普持仓与交易变化
- 数据来源：未接入结构化 OGE/Open Cabinet 披露输入；本节不构成交易信号。
- 持仓变化：未获取到可核验的最新披露。
- 交易变化：未获取到可核验的最新披露。
- 对今日计划影响：只作为消息层风险背景，不能提升任何标的执行等级。

## 深度研究状态
- 已完成研究：无
- 待处理升级：无
- 边界：Vibe Swarm 仅为二级研究证据，不提升执行等级。

## 持仓与组合风险
- 数据覆盖：未取得插件持仓快照。
- 跨账户重复持仓：未知。
- 边界：只读风险复核，不生成仓位调整指令。

## 重点执行候选
### MU
- 参考 setup：breakout_pullback_continuation.md
- 触发条件：突破 100 后回踩站稳。
- 主场景：只作为候选观察，不是交易指令。
- 备选场景：跌回区间中部则等待。
- 失效条件：跌破 95。
- 风险约束：单笔风险 <=1%；止损过宽则放弃。
"""


class ValidateReportTest(unittest.TestCase):
    def make_repo(self, report_text):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        setup_dir = root / "knowledge" / "refined" / "setups"
        setup_dir.mkdir(parents=True)
        (setup_dir / "breakout_pullback_continuation.md").write_text("# setup\n", encoding="utf-8")
        report_dir = root / "report" / "2026-05-26"
        report_dir.mkdir(parents=True)
        (report_dir / "pre-market.md").write_text(report_text, encoding="utf-8")
        (report_dir / "exec-brief.md").write_text(report_text, encoding="utf-8")
        return temp, root

    def validate_repo(self, root):
        args = argparse.Namespace(
            repo_root=str(root),
            date="2026-05-26",
            session="pre-market",
            report="report/2026-05-26/pre-market.md",
        )
        return validate(args)

    def write_sidecar(self, root, signals):
        sidecar = {
            "date": "2026-05-26",
            "session": "pre-market",
            "source_report": "report/2026-05-26/exec-brief.md",
            "signals": signals,
        }
        (root / "report" / "2026-05-26" / "pre-market-signals.json").write_text(
            json.dumps(sidecar, ensure_ascii=False),
            encoding="utf-8",
        )

    def trade_plan_signal(self, **overrides):
        signal = {
            "symbol": "MU",
            "setup": "breakout_pullback_continuation.md",
            "direction": "long",
            "trigger": {"type": "break_above", "price": 100, "text": "突破 100"},
            "invalidation": {"type": "break_below", "price": 95, "text": "跌破 95"},
            "risk": {
                "max_risk_pct": 1,
                "max_account_risk_pct": 1,
                "risk_per_share": 5,
                "min_rr": 2,
            },
            "status": "planned",
            "plan_type": "trade_plan",
            "execution_status": "conditional_executable",
            "entry": {
                "type": "breakout_pullback",
                "trigger_price": 100,
                "confirmation": "5m/15m close above trigger, pullback holds",
                "no_chase_rule": "skip if entry would be >2R from stop",
            },
            "stop": {
                "initial_stop": 95,
                "invalidation": "breaks below 95 and fails to reclaim",
            },
            "take_profit": {
                "tp1": 112,
                "management": "at +1R consider partial or move stop to breakeven",
            },
            "execution_rules": {
                "valid_time_window": "first 90 minutes or after clean pullback",
                "skip_conditions": ["market turns risk-off"],
            },
        }
        signal.update(overrides)
        return signal

    def test_passes_good_report(self):
        temp, root = self.make_repo(GOOD_REPORT)
        with temp:
            payload = self.validate_repo(root)

        self.assertEqual(payload["status"], "pass")
        self.assertEqual(payload["errors"], [])

    def test_pre_market_news_layer_accepts_numbered_heading(self):
        report = GOOD_REPORT.replace("## 消息层汇总", "## 四、消息层汇总")
        temp, root = self.make_repo(report)
        with temp:
            payload = self.validate_repo(root)

        self.assertEqual(payload["status"], "pass")
        self.assertEqual(payload["errors"], [])

    def test_pre_market_report_requires_trump_disclosure_news_section(self):
        report = GOOD_REPORT.replace(
            """## 消息层汇总
### 特朗普持仓与交易变化
- 数据来源：未接入结构化 OGE/Open Cabinet 披露输入；本节不构成交易信号。
- 持仓变化：未获取到可核验的最新披露。
- 交易变化：未获取到可核验的最新披露。
- 对今日计划影响：只作为消息层风险背景，不能提升任何标的执行等级。

""",
            "",
        )
        temp, root = self.make_repo(report)
        with temp:
            payload = self.validate_repo(root)

        self.assertEqual(payload["status"], "fail")
        self.assertTrue(any("Trump disclosure news section" in error for error in payload["errors"]))

    def test_pre_market_report_requires_deep_research_status_section(self):
        report = GOOD_REPORT.replace(
            """## 深度研究状态
- 已完成研究：无
- 待处理升级：无
- 边界：Vibe Swarm 仅为二级研究证据，不提升执行等级。

""",
            "",
        )
        temp, root = self.make_repo(report)
        with temp:
            payload = self.validate_repo(root)

        self.assertEqual(payload["status"], "fail")
        self.assertTrue(any("missing ## 深度研究状态" in error for error in payload["errors"]))

    def test_pre_market_report_requires_position_risk_section(self):
        report = GOOD_REPORT.replace(
            """## 持仓与组合风险
- 数据覆盖：未取得插件持仓快照。
- 跨账户重复持仓：未知。
- 边界：只读风险复核，不生成仓位调整指令。

""",
            "",
        )
        temp, root = self.make_repo(report)
        with temp:
            payload = self.validate_repo(root)

        self.assertEqual(payload["status"], "fail")
        self.assertTrue(any("missing ## 持仓与组合风险" in error for error in payload["errors"]))

    def test_post_market_report_requires_daily_trade_review(self):
        report = """# 今日盘后复盘（2026-05-26）

## 总览
- 明日最多3个重点观察标的：MU

## 持仓与组合风险复盘
- 数据覆盖：IBKR。
- 边界：只读复盘，不生成订单动作。

## 当日交易复盘
- 数据覆盖：IBKR；Longbridge 未授权。
- 订单上下文不等于成交，只复盘 executions。

## 深度研究复盘
- 已完成研究：无
- 待升级研究：无

## 重点标的复盘
### MU
- 参考 setup：breakout_pullback_continuation.md
- 触发条件：突破 100 后回踩站稳。
- 失效/放弃条件：跌破 95。
- 风险提醒：单笔风险 <=1%，止损过宽则放弃。
"""
        temp, root = self.make_repo(report)
        with temp:
            path = root / "report" / "2026-05-26" / "post-market.md"
            path.write_text(report, encoding="utf-8")
            args = argparse.Namespace(
                repo_root=str(root),
                date="2026-05-26",
                session="post-market",
                report=str(path),
            )
            passing = validate(args)
            path.write_text(
                report.replace(
                    """## 当日交易复盘
- 数据覆盖：IBKR；Longbridge 未授权。
- 订单上下文不等于成交，只复盘 executions。

""",
                    "",
                ),
                encoding="utf-8",
            )
            failing = validate(args)

        self.assertEqual(passing["status"], "pass")
        self.assertEqual(failing["status"], "fail")
        self.assertTrue(any("missing ## 当日交易复盘" in error for error in failing["errors"]))

    def test_fails_missing_setup_and_invalidation(self):
        bad_report = """# 报告
### MU
- 触发条件：突破 100。
- 风险约束：单笔风险 <=1%。
"""
        temp, root = self.make_repo(bad_report)
        with temp:
            payload = self.validate_repo(root)

        self.assertEqual(payload["status"], "fail")
        self.assertTrue(any("missing setup" in error for error in payload["errors"]))
        self.assertTrue(any("invalidation" in error for error in payload["errors"]))

    def test_fails_unknown_setup(self):
        report = GOOD_REPORT.replace("breakout_pullback_continuation.md", "missing_setup.md")
        temp, root = self.make_repo(report)
        with temp:
            payload = self.validate_repo(root)

        self.assertEqual(payload["status"], "fail")
        self.assertTrue(any("not approved by the canonical rulebook" in error for error in payload["errors"]))

    def test_ignores_non_setup_markdown_references_in_body(self):
        report = GOOD_REPORT + "\n补充：参见 docs/contracts/agent-research.md 和 market_regime_preconditions.md。\n"
        temp, root = self.make_repo(report)
        with temp:
            payload = self.validate_repo(root)

        self.assertEqual(payload["status"], "pass")
        self.assertEqual(payload["errors"], [])

    def test_method_context_requires_active_compiled_card(self):
        method_path = "playbooks/trading-price-action-analysis-methods/breakout-pullback-and-failure.md"
        report = GOOD_REPORT + f"\n## 方法上下文\n- 突破质量；来源：{method_path}\n"
        temp, root = self.make_repo(report)
        with temp, patch("validate_report.active_method_card_paths", return_value={method_path}):
            payload = self.validate_repo(root)
        self.assertEqual(payload["status"], "pass")

    def test_rejects_raw_runtime_reference(self):
        report = GOOD_REPORT + "\n## 方法上下文\n- 来源：raw/transcripts/example.srt\n"
        temp, root = self.make_repo(report)
        with temp:
            payload = self.validate_repo(root)
        self.assertEqual(payload["status"], "fail")
        self.assertTrue(any("compiler-only" in error for error in payload["errors"]))

    def test_sidecar_method_context_requires_active_card(self):
        method_path = "playbooks/trading-price-action-analysis-methods/breakout-pullback-and-failure.md"
        temp, root = self.make_repo(GOOD_REPORT.replace("今日盘前完整报告", "今日盘前执行简版"))
        with temp:
            signal = self.trade_plan_signal(
                method_context=[{"path": "playbooks/trading-price-action-analysis-methods/missing.md", "summary": "突破"}]
            )
            self.write_sidecar(root, [signal])
            args = argparse.Namespace(repo_root=str(root), date="2026-05-26", session="pre-market", report=None, signals=None)
            with patch("validate_report.active_method_card_paths", return_value={method_path}):
                payload = validate(args)
        self.assertEqual(payload["status"], "fail")
        self.assertTrue(any("not an active method card" in error for error in payload["errors"]))

    def test_default_session_requires_and_validates_signals_sidecar(self):
        temp, root = self.make_repo(GOOD_REPORT.replace("今日盘前完整报告", "今日盘前执行简版"))
        with temp:
            self.write_sidecar(
                root,
                [
                    {
                        "symbol": "MU",
                        "setup": "breakout_pullback_continuation.md",
                        "trigger": {"type": "break_above", "price": 100, "text": "突破 100"},
                        "invalidation": {"type": "break_below", "price": 95, "text": "跌破 95"},
                        "risk": {"max_risk_pct": 1},
                        "status": "planned",
                    }
                ],
            )
            args = argparse.Namespace(
                repo_root=str(root),
                date="2026-05-26",
                session="pre-market",
                report=None,
                signals=None,
            )
            payload = validate(args)

        self.assertEqual(payload["status"], "pass")
        self.assertTrue(payload["checked_signals"].endswith("pre-market-signals.json"))

    def test_default_session_fails_missing_signals_sidecar(self):
        temp, root = self.make_repo(GOOD_REPORT)
        with temp:
            args = argparse.Namespace(
                repo_root=str(root),
                date="2026-05-26",
                session="pre-market",
                report=None,
                signals=None,
            )
            payload = validate(args)

        self.assertEqual(payload["status"], "fail")
        self.assertTrue(any("missing structured signal sidecar" in error for error in payload["errors"]))

    def test_actionable_sidecar_requires_trigger_price(self):
        temp, root = self.make_repo(GOOD_REPORT)
        with temp:
            self.write_sidecar(
                root,
                [
                    {
                        "symbol": "MU",
                        "setup": "breakout_pullback_continuation.md",
                        "trigger": {"type": "break_above", "text": "突破 100"},
                        "invalidation": {"type": "break_below", "price": 95, "text": "跌破 95"},
                        "risk": {"max_risk_pct": 1},
                        "status": "planned",
                    }
                ],
            )
            args = argparse.Namespace(repo_root=str(root), date="2026-05-26", session="pre-market", report=None, signals=None)
            payload = validate(args)

        self.assertEqual(payload["status"], "fail")
        self.assertTrue(any("trigger.price" in error for error in payload["errors"]))

    def test_actionable_sidecar_requires_invalidation_price(self):
        temp, root = self.make_repo(GOOD_REPORT)
        with temp:
            self.write_sidecar(
                root,
                [
                    {
                        "symbol": "MU",
                        "setup": "breakout_pullback_continuation.md",
                        "trigger": {"type": "break_above", "price": 100, "text": "突破 100"},
                        "invalidation": {"type": "break_below", "text": "跌破 95"},
                        "risk": {"max_risk_pct": 1},
                        "status": "planned",
                    }
                ],
            )
            args = argparse.Namespace(repo_root=str(root), date="2026-05-26", session="pre-market", report=None, signals=None)
            payload = validate(args)

        self.assertEqual(payload["status"], "fail")
        self.assertTrue(any("invalidation.price" in error for error in payload["errors"]))

    def test_sidecar_symbols_must_match_markdown_focus_list_exactly(self):
        report = GOOD_REPORT.replace("今日最多3个重点标的：MU", "今日最多3个重点标的：MU、NVDA")
        temp, root = self.make_repo(report)
        with temp:
            self.write_sidecar(
                root,
                [
                    {
                        "symbol": "MU",
                        "setup": "breakout_pullback_continuation.md",
                        "trigger": {"type": "break_above", "price": 100, "text": "突破 100"},
                        "invalidation": {"type": "break_below", "price": 95, "text": "跌破 95"},
                        "risk": {"max_risk_pct": 1},
                        "status": "planned",
                    }
                ],
            )
            args = argparse.Namespace(repo_root=str(root), date="2026-05-26", session="pre-market", report=None, signals=None)
            payload = validate(args)

        self.assertEqual(payload["status"], "fail")
        self.assertTrue(any("do not match focus list" in error for error in payload["errors"]))

    def test_conditional_executable_trade_plan_requires_complete_plan_card(self):
        temp, root = self.make_repo(GOOD_REPORT)
        with temp:
            self.write_sidecar(root, [self.trade_plan_signal(take_profit={})])
            args = argparse.Namespace(repo_root=str(root), date="2026-05-26", session="pre-market", report=None, signals=None)
            payload = validate(args)

        self.assertEqual(payload["status"], "fail")
        self.assertTrue(any("take_profit.tp1 is required" in error for error in payload["errors"]))

    def test_conditional_executable_trade_plan_requires_minimum_rr(self):
        temp, root = self.make_repo(GOOD_REPORT)
        with temp:
            self.write_sidecar(root, [self.trade_plan_signal(take_profit={"tp1": 104})])
            args = argparse.Namespace(repo_root=str(root), date="2026-05-26", session="pre-market", report=None, signals=None)
            payload = validate(args)

        self.assertEqual(payload["status"], "fail")
        self.assertTrue(any("reward_risk_ratio must be >= 2" in error for error in payload["errors"]))

    def test_watch_only_plan_does_not_require_full_trade_plan_card(self):
        temp, root = self.make_repo(GOOD_REPORT)
        with temp:
            self.write_sidecar(
                root,
                [
                    {
                        "symbol": "MU",
                        "setup": "breakout_pullback_continuation.md",
                        "direction": "long",
                        "trigger": {"type": "break_above", "price": 100, "text": "突破 100"},
                        "invalidation": {"type": "break_below", "price": 95, "text": "跌破 95"},
                        "risk": {"max_risk_pct": 1},
                        "status": "planned",
                        "plan_type": "watch_only",
                        "execution_status": "watch_only",
                    }
                ],
            )
            args = argparse.Namespace(repo_root=str(root), date="2026-05-26", session="pre-market", report=None, signals=None)
            payload = validate(args)

        self.assertEqual(payload["status"], "pass")


if __name__ == "__main__":
    unittest.main()
