import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LlmSignalUpgradeContractTest(unittest.TestCase):
    def read(self, path: str) -> str:
        return (ROOT / path).read_text(encoding="utf-8")

    def test_report_skill_contracts_make_llm_final_sidecar_decision_owner(self):
        for path in (
            ".codex/skills/tca-pre-market-analysis/references/report-contract.md",
            ".codex/skills/tca-post-market-review/references/report-contract.md",
        ):
            with self.subTest(path=path):
                text = self.read(path)
                self.assertIn("最终由报告生成 LLM 判断", text)
                self.assertIn("完整 Trade Plan Card", text)
                self.assertIn("可以在 sidecar 中标记为 `conditional_executable`", text)
                self.assertNotIn("agent decision 只能帮助降级", text)
                self.assertNotIn("agent decision 可以降低明日计划等级", text)

    def test_report_prompts_are_thin_skill_triggers(self):
        cases = {
            "agent/daily_analysis_prompt.md": "$tca-pre-market-analysis",
            "agent/post_market_analysis_prompt.md": "$tca-post-market-review",
            "ops/cc-connect/tca-pre-market-wrapper.prompt.md": "$tca-pre-market-analysis",
            "ops/cc-connect/tca-post-market-wrapper.prompt.md": "$tca-post-market-review",
        }
        for path, skill_name in cases.items():
            with self.subTest(path=path):
                text = self.read(path)
                self.assertIn(skill_name, text)
                self.assertLessEqual(len(text.splitlines()), 12)
                self.assertNotIn("【优先使用的 setup 规则池】", text)

    def test_agent_research_contract_does_not_block_llm_upgrade(self):
        text = self.read("docs/contracts/agent-research.md")

        self.assertIn("报告生成 LLM 是 session sidecar 的最终决策者", text)
        self.assertIn("完整 Trade Plan Card", text)
        self.assertIn("可以产出 `trade_plan` / `conditional_executable`", text)
        self.assertNotIn("must not upgrade an existing `watch_only` or `no_trade` candidate", text)

    def test_daily_workflow_keeps_news_layer_from_upgrading_signals(self):
        text = self.read("docs/daily-report-workflow.md")

        self.assertIn("消息层本身不能升级", text)
        self.assertIn("报告生成 LLM", text)
        self.assertIn("完整 Trade Plan Card", text)


if __name__ == "__main__":
    unittest.main()
