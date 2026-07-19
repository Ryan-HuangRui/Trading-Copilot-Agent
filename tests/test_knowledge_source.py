import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

from knowledge_source import KnowledgeSourceError, resolve_knowledge_source


class KnowledgeSourceTest(unittest.TestCase):
    def build_fixture(self, root: Path) -> tuple[Path, Path]:
        repo = root / "repo"
        vault = root / "vault"
        (repo / "config").mkdir(parents=True)
        (vault / "_meta").mkdir(parents=True)
        paths = [
            "playbooks/rules/global/risk.md",
            "playbooks/rules/setups/breakout.md",
            "playbooks/methods/breakout.md",
            "playbooks/knowledge-pack.md",
        ]
        for value in paths:
            path = vault / value
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"# {path.stem}\n", encoding="utf-8")
        (repo / "config" / "knowledge_source.json").write_text(
            json.dumps({"canonical_root": str(vault)}), encoding="utf-8"
        )
        manifest = {
            "schema": "trading-copilot-rulebook/v1",
            "approved_setup_files": ["breakout.md"],
            "analysis_methods_path": "playbooks/knowledge-pack.md",
            "knowledge_pack_manifest": "_meta/trading-copilot-knowledge-pack.json",
            "approved_rules": [
                {"id": "risk.md", "kind": "global", "path": "playbooks/rules/global/risk.md"},
                {"id": "breakout.md", "kind": "setup", "path": "playbooks/rules/setups/breakout.md"},
            ],
        }
        (vault / "_meta" / "trading-copilot-rulebook.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        pack = {
            "schema": "trading-copilot-knowledge-pack/v1",
            "raw_source_policy": "compiler_only",
            "records": [
                {"id": "risk", "kind": "canonical_rule", "consumer_role": "execution_constraint", "status": "active", "path": "playbooks/rules/global/risk.md", "canonical_alignment": []},
                {"id": "breakout", "kind": "canonical_rule", "consumer_role": "setup", "status": "active", "path": "playbooks/rules/setups/breakout.md", "canonical_alignment": []},
                {"id": "method-breakout", "kind": "method_card", "consumer_role": "analysis_context", "status": "active", "path": "playbooks/methods/breakout.md", "canonical_alignment": ["breakout"]},
            ],
        }
        pack_path = vault / "_meta" / "trading-copilot-knowledge-pack.json"
        pack_path.write_text(json.dumps(pack), encoding="utf-8")
        return repo, pack_path

    def test_validates_record_roles_alignment_and_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, _ = self.build_fixture(Path(tmp))
            source = resolve_knowledge_source(repo)
            self.assertEqual(source.method_card_paths, frozenset({"playbooks/methods/breakout.md"}))

    def test_rejects_duplicate_record_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, pack_path = self.build_fixture(Path(tmp))
            pack = json.loads(pack_path.read_text())
            pack["records"][-1]["id"] = "breakout"
            pack_path.write_text(json.dumps(pack), encoding="utf-8")
            with self.assertRaisesRegex(KnowledgeSourceError, "duplicate knowledge pack record id"):
                resolve_knowledge_source(repo)

    def test_rejects_unknown_method_alignment(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, pack_path = self.build_fixture(Path(tmp))
            pack = json.loads(pack_path.read_text())
            pack["records"][-1]["canonical_alignment"] = ["missing"]
            pack_path.write_text(json.dumps(pack), encoding="utf-8")
            with self.assertRaisesRegex(KnowledgeSourceError, "unknown canonical alignment"):
                resolve_knowledge_source(repo)


if __name__ == "__main__":
    unittest.main()
