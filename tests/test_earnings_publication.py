import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "script"))

from earnings_common import atomic_write_json, sha256_file
from earnings_publication import _facts, build_publication, claim_occurrence_inventory, validate_reader_markdown
from earnings_period_review import resolve_report_period
from earnings_lark import LarkDocumentPublisher, _readback_key, normalize_markdown_for_lark
from earnings_publication_runner import prepare_input


SOURCE = {
    "report_id": "report-company",
    "report_type": "company",
    "scope": {"symbol": "TEST", "reporting_start": "2026-04-01", "reporting_end": "2026-06-30"},
    "cutoff": "2026-08-01T00:00:00+00:00",
    "change_summary": "收入增长但现金回收变慢。",
    "thesis_state": "emerging",
    "completeness": {"status": "partial", "missing_inputs": ["一致预期"]},
    "coverage": {"expected_issuers": 1, "disclosed_issuers": 1, "fetched_issuers": 1,
                 "researched_issuers": 1, "key_missing_issuers": []},
    "claims": [{"claim_id": "c1", "statement": "收入为 100 百万美元。", "evidence_ids": ["e1"],
                "limitations": ["现金回收待核实"], "alternative_explanation": "并购可能贡献增长"}],
    "evidence": [{"evidence_id": "e1", "source_url": "https://example.com/filing",
                  "source_locator": "p.1", "summary": "季度披露",
                  "numeric_facts": [{"metric": "revenue", "value": 100, "unit": "USD million",
                                     "period": {"kind": "duration", "start": "2026-04-01", "end": "2026-06-30"}}]}],
    "limitations": ["缺少公告前一致预期"],
    "invalidation_conditions": ["现金流继续恶化"],
    "next_checks": ["下一季经营现金流"],
}


def markdown(value="100", include_risk=True, link="https://example.com/filing"):
    risk = "\n## 最强反证与风险\n并购可能贡献增长，现金回收待核实。\n" if include_risk else ""
    return f"""# TEST 财报研究

经营期间：2026-04-01 至 2026-06-30；资料截止：2026-08-01。

## 一分钟读完
收入为 {value} 百万美元，但市场预期差未知。

## 先看懂这门生意
公司向客户销售产品。

## 本季度关键变化
收入为 {value} 百万美元，增长也可能来自并购。

## 增长留下多少钱
现金回收仍待核实。

## 潜在线索与市场预期
缺少公告前一致预期，因此不能判断超预期或低估。
{risk}
## 三种情景与下一次验证
改善、延续和恶化情景均取决于下一季经营现金流。

## 来源
- [季度披露]({link})（p.1）
"""


class EarningsPublicationTests(unittest.TestCase):
    def test_occurrence_inventory_uses_exact_raw_markdown_coordinates_with_commas(self):
        body = "首项 1,000 百万美元；次项 2,000 百万美元。"
        inventory = claim_occurrence_inventory(body)
        self.assertEqual(inventory["coordinate_contract"],
                         "python-string-codepoint-offsets-v1; tables use one-based line/column")
        self.assertEqual([body[row["occurrence"]["start"]:row["occurrence"]["end"]]
                          for row in inventory["claims"]], ["1,000 百万美元", "2,000 百万美元"])
        self.assertEqual([row["value"] for row in inventory["claims"]], ["1000", "2000"])

    def test_reader_checker_blocks_number_period_counterevidence_and_link_drift(self):
        self.assertEqual(validate_reader_markdown(markdown(), [SOURCE])["status"], "passed")
        for bad in [markdown("120"), markdown().replace("2026-06-30", "2026-09-30"),
                    markdown(include_risk=False), markdown(link="https://wrong.example")]:
            self.assertEqual(validate_reader_markdown(bad, [SOURCE])["status"], "failed")

    def test_reader_checker_binds_currency_basis_and_bare_table_cells(self):
        self.assertEqual(validate_reader_markdown(markdown().replace("100 百万美元", "1 亿美元"), [SOURCE])["status"], "passed")
        self.assertEqual(validate_reader_markdown(markdown().replace("100 百万美元", "100 百万元"), [SOURCE])["status"], "failed")
        self.assertEqual(validate_reader_markdown(markdown().replace("收入为 100 百万美元", "GAAP 收入为 100 百万美元"), [SOURCE])["status"], "failed")
        table = markdown() + "\n| 指标 | 数值 |\n|---|---|\n| 收入 | 100 |\n"
        self.assertEqual(validate_reader_markdown(table, [SOURCE])["status"], "failed")

    def test_semantic_checker_must_be_hash_bound_and_error_free(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); source = root / "report/earnings/source.json"; atomic_write_json(source, SOURCE)
            check = {"status": "passed", "errors": ["drift"], "draft_sha256": "wrong", "input_manifest_hash": "i",
                     "source_sha256s": [sha256_file(source)], "source_mapping": [{"section": "x", "evidence_ids": ["e1"]}]}
            with self.assertRaisesRegex(ValueError, "checker failed"):
                build_publication(root, "company", "TEST", "2026-Q2", [source], markdown(), semantic_checker=check)

    def test_prepare_input_is_semantically_idempotent_but_versions_changed_title(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); (root / "config").mkdir()
            (root / "config/earnings_research.json").write_bytes((Path(__file__).resolve().parents[1] / "config/earnings_research.json").read_bytes())
            source = root / "report/earnings/source.json"; atomic_write_json(source, {**SOURCE, "source_mode": "live"})
            first = prepare_input(root, publication_type="company", scope_id="TEST", quarter_id="2026-Q2", source_paths=[source])
            first_bytes = first.read_bytes()
            second = prepare_input(root, publication_type="company", scope_id="TEST", quarter_id="2026-Q2", source_paths=[source])
            self.assertEqual(first, second); self.assertEqual(first.read_bytes(), first_bytes)
            changed = prepare_input(root, publication_type="company", scope_id="TEST", quarter_id="2026-Q2", source_paths=[source], title="新标题")
            self.assertNotEqual(changed, first)

    def test_accepted_nvda_contract_uses_decimal_strings_and_evidence_period_enrichment(self):
        path = Path("/Users/cenxiangxiang/hr/repo/Trading-Copilot-Agent/report/earnings/deployment-acceptance/2026-09-15/company_report.json")
        if not path.exists(): self.skipTest("parent-reviewed public report unavailable")
        report = json.loads(path.read_text())
        self.assertEqual(len(_facts([report])), 24)
        resolved = resolve_report_period(report)
        self.assertEqual(resolved["actual_period"], {"start": "2026-04-27", "end": "2026-07-26"})
        self.assertEqual(resolved["resolution"], "evidence-period-review-v1")

    def test_build_is_versioned_immutable_and_generates_html_manifest(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            source = root / "report/earnings/source.json"; atomic_write_json(source, SOURCE)
            result = build_publication(root, "company", "TEST", "2026-Q2", [source], markdown())
            manifest = json.loads((root / result["manifest_path"]).read_text())
            self.assertEqual(manifest["checker"]["status"], "passed")
            self.assertEqual(manifest["version"], 1)
            self.assertTrue((root / manifest["artifacts"]["html"]["path"]).read_text().startswith("<!doctype html>"))
            same = build_publication(root, "company", "TEST", "2026-Q2", [source], markdown())
            self.assertEqual(same["manifest_path"], result["manifest_path"])
            revised = build_publication(root, "company", "TEST", "2026-Q2", [source], markdown().replace("向客户", "主要向客户"))
            self.assertEqual(revised["version"], 2)
            self.assertEqual(json.loads((root / revised["manifest_path"]).read_text())["previous_manifest_sha256"], sha256_file(root / result["manifest_path"]))


class LarkPublisherTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve(); (self.root / "runtime/earnings").mkdir(parents=True)
        self.binary = self.root / "runtime/earnings/lark-cli"; self.binary.write_text("x"); self.binary.chmod(0o700)
        self.config = {"lark_cli_bin": str(self.binary), "profile": "earnings-user", "as": "user",
                       "user_route": "earnings-owner", "parent_position": "my_space", "enabled": True}
        self.body = self.root / "runtime/earnings/body.md"; self.body.write_text(markdown())

    def accepted_manifest(self, publication_id):
        path = self.root / "report/earnings/publications/company/test/2026-Q2/v1/publication-manifest.json"
        atomic_write_json(path, {"publication_id": publication_id, "version": 1, "publishable": True,
            "checker": {"status": "passed", "errors": []}, "sources": [],
            "artifacts": {"markdown": {"path": str(self.body.relative_to(self.root)), "sha256": sha256_file(self.body)}}})
        return path

    def test_cloud_import_expands_reference_links_and_removes_local_targets(self):
        result = normalize_markdown_for_lark("[来源][sec]\n[目录](#local)\n[附件](../raw/a.pdf)\n\n[sec]: https://example.com/a\n")
        self.assertIn("[来源](https://example.com/a)", result)
        self.assertNotIn("#local", result); self.assertNotIn("../raw", result)

    def test_real_export_representation_preserves_title_table_cells_and_links(self):
        original = "# 标题\n\n| 指标 | 数值 |\n| --- | ---: |\n| 收入 | 100 |\n\n[来源](https://example.com/a)\n"
        exported = "<title>飞书标题</title>\n\n# 标题\n\n| 指标 | 数值 |\n|-|-|\n| 收入 | 100 |\n\n[来源](https://example.com/a)\n"
        self.assertEqual(_readback_key(original), _readback_key(exported))
        self.assertNotEqual(_readback_key(original), _readback_key(exported.replace("| 收入 | 100 |", "| 收入 | 99 |")))
        self.assertNotEqual(_readback_key(original), _readback_key(exported.replace("https://example.com/a", "https://example.com/b")))

    def test_reviewed_nvda_import_matches_actual_lark_markdown_export(self):
        base = Path("/Users/cenxiangxiang/hr/repo/Trading-Copilot-Agent/runtime/earnings")
        original_path = base / "publications/nvda-reader-feishu-import.md"
        export_path = base / "p4-sample-readback.md"
        if not original_path.exists() or not export_path.exists(): self.skipTest("parent-reviewed public export fixture unavailable")
        original, exported = original_path.read_text(), export_path.read_text()
        self.assertEqual(_readback_key(original), _readback_key(exported))
        self.assertNotEqual(_readback_key(original), _readback_key(exported.replace("| 公司收入 | 962.21 |", "| 公司收入 | 962.20 |")))
        self.assertNotEqual(_readback_key(original), _readback_key(exported.replace("https://www.sec.gov/Archives/", "https://example.invalid/", 1)))

    def test_manifest_acceptance_and_artifact_hash_are_mandatory(self):
        publisher = LarkDocumentPublisher(self.root, self.config)
        manifest = self.accepted_manifest("pub-gate")
        payload = json.loads(manifest.read_text()); payload["publishable"] = False; atomic_write_json(manifest, payload)
        with self.assertRaisesRegex(ValueError, "not accepted"):
            publisher.publish("pub-gate", "TEST", self.body, expected_sha256=sha256_file(self.body), publication_manifest=manifest)

    def test_route_identity_is_part_of_cloud_state_key(self):
        first = LarkDocumentPublisher(self.root, self.config)
        second = LarkDocumentPublisher(self.root, {**self.config, "user_route": "another-user"})
        self.assertNotEqual(first._state_path("same-series"), second._state_path("same-series"))

    def test_orphaned_inflight_state_becomes_unknown_without_second_create(self):
        publisher = LarkDocumentPublisher(self.root, self.config); manifest = self.accepted_manifest("pub-orphan")
        state_path = publisher._state_path("pub-orphan")
        atomic_write_json(state_path, {"schema_version": 1, "publication_id": "pub-orphan", "state": "creating",
            "attempts": 1, "document_id": None, "url": None, "local_sha256": sha256_file(self.body)})
        with patch("earnings_lark.subprocess.run") as run:
            result = publisher.publish("pub-orphan", "TEST", self.body, expected_sha256=sha256_file(self.body), publication_manifest=manifest)
        self.assertEqual(result["state"], "unknown"); run.assert_not_called()

    def test_executable_fake_enforces_cwd_relative_content_and_real_readback_shape(self):
        fake = self.root / "runtime/earnings/fake-lark"
        fake.write_text("""#!/usr/bin/env python3
import json, pathlib, re, sys
a=sys.argv; remote=pathlib.Path(__file__).with_name('remote.md')
if '--content' in a:
 p=a[a.index('--content')+1]
 if not p.startswith('@./'): sys.exit(41)
 content=pathlib.Path(p[1:]).read_text()
 content=re.sub(r'^\\|(?:\\s*:?-+:?\\s*\\|)+$', lambda m: '|'+'|'.join('-' for _ in m.group(0).strip('|').split('|'))+'|', content, flags=re.M)
 remote.write_text('<title>TEST</title>\\n\\n'+content)
if '+fetch' in a:
 print(json.dumps({'ok':True,'identity':'user','data':{'document':{'content':remote.read_text()}}}))
elif '+create' in a:
 print(json.dumps({'ok':True,'identity':'user','data':{'document':{'document_id':'doc-real','url':'https://lark/doc-real'}}}))
else: print(json.dumps({'ok':True,'identity':'user','data':{'updated':True}}))
""")
        fake.chmod(0o700); config = {**self.config, "lark_cli_bin": str(fake)}
        publisher = LarkDocumentPublisher(self.root, config); manifest = self.accepted_manifest("pub-real")
        result = publisher.publish("pub-real", "TEST", self.body, expected_sha256=sha256_file(self.body), publication_manifest=manifest)
        self.assertEqual((result["state"], result["document_id"], result["url"]), ("verified", "doc-real", "https://lark/doc-real"))

    @patch("earnings_lark.subprocess.run")
    def test_create_readback_and_idempotency_use_explicit_user_profile(self, run):
        run.side_effect = [
            subprocess.CompletedProcess([], 0, json.dumps({"ok": True, "identity": "user", "data": {"document_id": "doc1", "url": "https://lark/doc1"}}), ""),
            subprocess.CompletedProcess([], 0, json.dumps({"ok": True, "identity": "user", "data": {"content": self.body.read_text()}}), ""),
        ]
        publisher = LarkDocumentPublisher(self.root, self.config)
        manifest = self.accepted_manifest("pub1")
        result = publisher.publish("pub1", "TEST", self.body, expected_sha256=sha256_file(self.body), publication_manifest=manifest)
        self.assertEqual(result["state"], "verified")
        create_argv = run.call_args_list[0].args[0]
        self.assertIn("--profile", create_argv); self.assertIn("--as", create_argv)
        self.assertEqual(create_argv[create_argv.index("--doc-format") + 1], "markdown")
        self.assertEqual(create_argv[create_argv.index("--content") + 1], "@./prepared.md")
        self.assertEqual(run.call_args_list[0].kwargs["cwd"], str((self.root / "runtime/earnings/publications/cloud" / publisher._state_path("pub1").parent.name)))
        self.assertEqual(create_argv[create_argv.index("--parent-position") + 1], "my_space")
        again = publisher.publish("pub1", "TEST", self.body, expected_sha256=sha256_file(self.body), publication_manifest=manifest)
        self.assertEqual(again["state"], "verified"); self.assertEqual(run.call_count, 2)

    @patch("earnings_lark.subprocess.run", side_effect=subprocess.TimeoutExpired("lark", 30))
    def test_unknown_create_is_not_blindly_retried(self, run):
        publisher = LarkDocumentPublisher(self.root, self.config)
        manifest = self.accepted_manifest("pub2")
        self.assertEqual(publisher.publish("pub2", "TEST", self.body, expected_sha256=sha256_file(self.body), publication_manifest=manifest)["state"], "unknown")
        self.assertEqual(publisher.publish("pub2", "TEST", self.body, expected_sha256=sha256_file(self.body), publication_manifest=manifest)["state"], "unknown")
        self.assertEqual(run.call_count, 1)

    @patch("earnings_lark.subprocess.run")
    def test_remote_user_edit_blocks_update(self, run):
        run.side_effect = [
            subprocess.CompletedProcess([], 0, json.dumps({"ok": True, "identity": "user", "data": {"document_id": "doc1", "url": "https://lark/doc1"}}), ""),
            subprocess.CompletedProcess([], 0, json.dumps({"ok": True, "identity": "user", "data": {"content": self.body.read_text()}}), ""),
            subprocess.CompletedProcess([], 0, json.dumps({"ok": True, "identity": "user", "data": {"content": "用户修改"}}), ""),
        ]
        publisher = LarkDocumentPublisher(self.root, self.config)
        manifest = self.accepted_manifest("pub1")
        self.assertEqual(publisher.publish("pub1", "TEST", self.body, expected_sha256=sha256_file(self.body), series_id="series", publication_manifest=manifest)["state"], "verified")
        self.body.write_text(markdown().replace("公司向客户", "公司主要向客户"))
        manifest = self.accepted_manifest("pub2")
        result = publisher.publish("pub2", "TEST", self.body, expected_sha256=sha256_file(self.body), series_id="series", publication_manifest=manifest)
        self.assertEqual(result["state"], "conflict")
        self.assertEqual(run.call_count, 3)  # no update after the mismatched preflight fetch

    @patch("earnings_lark.subprocess.run")
    def test_unchanged_remote_baseline_allows_controlled_overwrite_and_readback(self, run):
        old = self.body.read_text(); new = old.replace("公司向客户", "公司主要向客户")
        ok = lambda content: subprocess.CompletedProcess([], 0, json.dumps({"ok": True, "identity": "user", "data": content}), "")
        run.side_effect = [ok({"document_id": "doc1", "url": "https://lark/doc1"}), ok({"content": old}),
                           ok({"content": old}), ok({"updated": True}), ok({"content": new})]
        publisher = LarkDocumentPublisher(self.root, self.config)
        manifest = self.accepted_manifest("pub1")
        publisher.publish("pub1", "TEST", self.body, expected_sha256=sha256_file(self.body), series_id="series-update", publication_manifest=manifest)
        self.body.write_text(new)
        manifest = self.accepted_manifest("pub2")
        result = publisher.publish("pub2", "TEST", self.body, expected_sha256=sha256_file(self.body), series_id="series-update", publication_manifest=manifest)
        self.assertEqual(result["state"], "verified")
        update_argv = run.call_args_list[3].args[0]
        self.assertEqual(update_argv[update_argv.index("--command") + 1], "overwrite")

    @patch("earnings_lark.subprocess.run", return_value=subprocess.CompletedProcess([], 1, "", "login required"))
    def test_expired_user_auth_is_explicit_and_terminal(self, run):
        publisher = LarkDocumentPublisher(self.root, self.config)
        manifest = self.accepted_manifest("pub-auth")
        result = publisher.publish("pub-auth", "TEST", self.body, expected_sha256=sha256_file(self.body), publication_manifest=manifest)
        self.assertEqual(result["state"], "auth_failed")
        publisher.publish("pub-auth", "TEST", self.body, expected_sha256=sha256_file(self.body), publication_manifest=manifest)
        self.assertEqual(run.call_count, 1)

    @patch("earnings_lark.subprocess.run")
    def test_auth_failure_recovers_only_after_explicit_credentials_refresh_marker(self, run):
        failed = subprocess.CompletedProcess([], 1, "", "login required")
        created = subprocess.CompletedProcess([], 0, json.dumps({"ok": True, "identity": "user",
            "data": {"document_id": "doc-auth", "url": "https://lark/doc-auth"}}), "")
        fetched = subprocess.CompletedProcess([], 0, json.dumps({"ok": True, "identity": "user",
            "data": {"content": self.body.read_text()}}), "")
        run.side_effect = [failed, created, fetched]
        manifest = self.accepted_manifest("pub-auth-refresh")
        publisher = LarkDocumentPublisher(self.root, self.config)
        first = publisher.publish("pub-auth-refresh", "TEST", self.body, expected_sha256=sha256_file(self.body), publication_manifest=manifest)
        self.assertEqual(first["state"], "auth_failed")
        refreshed = LarkDocumentPublisher(self.root, {**self.config, "credentials_refreshed_at": "9999-01-01T00:00:00Z"})
        second = refreshed.publish("pub-auth-refresh", "TEST", self.body, expected_sha256=sha256_file(self.body), publication_manifest=manifest)
        self.assertEqual(second["state"], "verified")


if __name__ == "__main__":
    unittest.main()
