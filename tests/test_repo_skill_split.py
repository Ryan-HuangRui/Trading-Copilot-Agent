import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RepoSkillSplitTest(unittest.TestCase):
    def read(self, path: str) -> str:
        return (ROOT / path).read_text(encoding="utf-8")

    def test_dedicated_skill_descriptions_are_scope_separated(self):
        cases = {
            "tca-pre-market-analysis": ("盘前", "Do not use for post-market"),
            "tca-post-market-review": ("盘后", "Do not use for pre-market"),
            "tca-price-action-analysis": ("个股价格行为", "Do not use for full pre-market/post-market"),
            "tca-swarm-research": ("Swarm 深度研究", "Do not use for routine ticker quotes"),
        }
        for name, required in cases.items():
            with self.subTest(skill=name):
                text = self.read(f".codex/skills/{name}/SKILL.md")
                self.assertTrue(text.startswith(f"---\nname: {name}\n"))
                for phrase in required:
                    self.assertIn(phrase, text)

    def test_price_action_requires_full_analysis_process(self):
        text = self.read(".codex/skills/tca-price-action-analysis/references/analysis-contract.md")
        for section in (
            "数据依据与质量",
            "大盘与行业背景",
            "高周期结构",
            "关键位置",
            "位置上的价格行为",
            "Setup 审核",
            "条件场景",
            "风险与计划约束",
            "结论",
        ):
            self.assertIn(section, text)
        self.assertIn("不能只给", text)

    def test_swarm_defaults_to_requested_model_and_reasoning(self):
        preset = self.read(".codex/vibe_swarm_presets/tca_research_review.yaml")
        config = self.read(".codex/config.toml")
        adapter = self.read(".codex/vibe_trading_mcp.py")
        self.assertEqual(
            len(re.findall(r"^    model_name: openai-codex/gpt-5\.6-sol$", preset, re.MULTILINE)),
            4,
        )
        self.assertIn('LANGCHAIN_PROVIDER = "openai-codex"', config)
        self.assertIn('LANGCHAIN_MODEL_NAME = "openai-codex/gpt-5.6-sol"', config)
        self.assertIn('LANGCHAIN_REASONING_EFFORT = "medium"', config)
        self.assertIn('DEFAULT_SWARM_PROVIDER = "openai-codex"', adapter)
        self.assertIn('DEFAULT_SWARM_MODEL = "openai-codex/gpt-5.6-sol"', adapter)
        self.assertIn('EFFECTIVE_SWARM_MODEL = "gpt-5.6-sol"', adapter)
        self.assertIn('DEFAULT_SWARM_REASONING_EFFORT = "medium"', adapter)

    def test_legacy_router_points_to_dedicated_skills(self):
        text = self.read(".codex/skills/trading-copilot/SKILL.md")
        for name in (
            "$tca-pre-market-analysis",
            "$tca-post-market-review",
            "$tca-price-action-analysis",
            "$tca-swarm-research",
            "$intraday-tracker",
        ):
            self.assertIn(name, text)


if __name__ == "__main__":
    unittest.main()
