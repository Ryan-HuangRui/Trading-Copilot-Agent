import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "script"))

from earnings_common import atomic_write_json, canonical_json, read_json, sha256_bytes, sha256_file
from earnings_publication import (_facts, build_publication, claim_occurrence_inventory,
                                  financial_fact_catalog, validate_reader_markdown)
from earnings_period_review import resolve_report_period
from earnings_lark import LarkDocumentPublisher, _readback_key, normalize_markdown_for_lark
from earnings_publication_runner import prepare_input, prepare_repair_input, run_publication, recheck_publication


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
    def test_catalog_normalizes_scaled_units_only_with_explicit_currency(self):
        report = json.loads(json.dumps(SOURCE)); report["evidence"][0]["numeric_facts"] = [
            {"metric": "UsdScaled", "value": "12", "unit": "million", "currency": "USD",
             "period": {"kind": "instant", "start": None, "end": "2026-06-30"}},
            {"metric": "CnyScaled", "value": "3", "unit": "billion", "currency": "CNY",
             "period": {"kind": "instant", "start": None, "end": "2026-06-30"}},
            {"metric": "UnknownCurrency", "value": "7", "unit": "million", "currency": None,
             "period": {"kind": "instant", "start": None, "end": "2026-06-30"}},
        ]
        facts = {row["metric"]: row for row in financial_fact_catalog([report])["facts"]}
        self.assertEqual((facts["UsdScaled"]["source_unit"], facts["UsdScaled"]["unit"]),
                         ("million", "USD million"))
        self.assertIn({"value": "0.12", "unit": "亿美元"}, facts["UsdScaled"]["display_candidates"])
        self.assertEqual((facts["CnyScaled"]["source_unit"], facts["CnyScaled"]["unit"]),
                         ("billion", "CNY billion"))
        self.assertIn({"value": "30", "unit": "亿元"}, facts["CnyScaled"]["display_candidates"])
        self.assertEqual(facts["UnknownCurrency"]["unit"], "million")
        self.assertEqual(facts["UnknownCurrency"]["display_candidates"], [])

    def test_financial_catalog_uses_stable_ids_and_only_legitimate_derivations(self):
        report = json.loads(json.dumps(SOURCE))
        report["evidence"][0]["numeric_facts"].extend([
            {"metric": "us-gaap:Revenues", "is_total": True, "total_dimension": "consolidated-revenue",
             "value": "96221000000", "unit": "USD",
             "currency": "USD", "accounting_basis": "us-gaap",
             "period": {"kind": "duration", "start": "2026-04-27", "end": "2026-07-26"}},
            {"metric": "us-gaap:Revenues", "value": "46743000000", "unit": "USD",
             "currency": "USD", "accounting_basis": "us-gaap",
             "period": {"kind": "duration", "start": "2025-04-28", "end": "2025-07-27"}},
            {"metric": "us-gaap:OperatingIncomeLoss", "value": "63734000000", "unit": "USD",
             "currency": "USD", "accounting_basis": "us-gaap",
             "period": {"kind": "duration", "start": "2026-04-27", "end": "2026-07-26"}},
            {"metric": "AccountsReceivableNetCurrent", "value": "63059000000", "unit": "USD",
             "currency": "USD", "accounting_basis": "us-gaap",
             "period": {"kind": "instant", "start": None, "end": "2026-07-26"}},
            {"metric": "DataCenterRevenue",
             "share_relationship": {"denominator_metric": "us-gaap:Revenues", "total_dimension": "consolidated-revenue"},
             "value": "89023000000", "unit": "USD", "currency": "USD", "accounting_basis": "us-gaap",
             "period": {"kind": "duration", "start": "2026-04-27", "end": "2026-07-26"}},
        ])
        first = financial_fact_catalog([report]); second = financial_fact_catalog([report])
        self.assertEqual(first, second)
        self.assertTrue(all(row["fact_id"].startswith("financial-fact-") for row in first["facts"]))
        growth = [row for row in first["derivations"]
                  if row["operation"] == "growth_rate" and row["metric"] == "us-gaap:Revenues"]
        self.assertEqual(len(growth), 1); self.assertEqual(growth[0]["input_fact_ids"], [
            next(row["fact_id"] for row in first["facts"] if row["metric"] == "us-gaap:Revenues" and row["value"] == "96221000000"),
            next(row["fact_id"] for row in first["facts"] if row["metric"] == "us-gaap:Revenues" and row["value"] == "46743000000")])
        revenue = next(row for row in first["facts"] if row["metric"] == "us-gaap:Revenues" and row["value"] == "96221000000")
        self.assertIn({"value": "962.21", "unit": "亿美元"}, revenue["display_candidates"])
        self.assertIn({"value": "106", "unit": "%"}, growth[0]["display_candidates"])
        self.assertFalse(any(row["metric"] in {"us-gaap:OperatingIncomeLoss", "AccountsReceivableNetCurrent"}
                             and row["operation"] == "growth_rate" for row in first["derivations"]))
        shares = [row for row in first["derivations"] if row["operation"] == "share"]
        self.assertEqual([(row["metric"], row["denominator_metric"]) for row in shares],
                         [("DataCenterRevenue", "us-gaap:Revenues")])

        other = json.loads(json.dumps(SOURCE)); other["report_id"] = "report-other"
        other["scope"]["symbol"] = "OTHER"; other["evidence"][0]["issuer_id"] = "issuer-other"
        other["evidence"][0]["numeric_facts"] = [{"metric": "CrossIssuerRevenue", "value": "50", "unit": "USD million",
            "currency": "USD", "accounting_basis": "us-gaap",
            "period": {"kind": "duration", "start": "2025-04-01", "end": "2025-06-30"}}]
        report["evidence"][0]["numeric_facts"].append({"metric": "CrossIssuerRevenue", "value": "100", "unit": "USD million",
            "currency": "USD", "accounting_basis": "us-gaap",
            "period": {"kind": "duration", "start": "2026-04-01", "end": "2026-06-30"}})
        combined = financial_fact_catalog([report, other])
        self.assertFalse(any(row["metric"] == "CrossIssuerRevenue" for row in combined["derivations"]))
        self.assertEqual({row["issuer_id"] for row in combined["facts"] if row["metric"] == "CrossIssuerRevenue"},
                         {"TEST", "issuer-other"})

    def test_derived_claim_requires_catalog_id_operation_and_exact_source_fact_ids(self):
        report = json.loads(json.dumps(SOURCE)); facts = report["evidence"][0]["numeric_facts"]
        facts.extend([
            {"metric": "revenue", "value": "205.85", "unit": "USD million",
             "period": {"kind": "duration", "start": "2026-04-01", "end": "2026-06-30"}},
            {"metric": "revenue", "value": "100", "unit": "USD million",
             "period": {"kind": "duration", "start": "2025-04-01", "end": "2025-06-30"}},
        ])
        body = markdown().replace("收入为 100 百万美元，但", "收入同比增长 106%，本期收入为 205.85 百万美元，但")
        catalog = financial_fact_catalog([report]); inventory = claim_occurrence_inventory(body)["claims"]
        current = next(row for row in catalog["facts"] if row["metric"] == "revenue" and row["value"] == "205.85")
        growth = next(row for row in catalog["derivations"] if row["operation"] == "growth_rate"
                      and row["input_fact_ids"][0] == current["fact_id"])
        bindings = []
        for claim in inventory:
            if claim["display"] == "106%":
                bindings.append({"display": claim["display"], "occurrence": claim["occurrence"],
                    "derivation_id": growth["derivation_id"], "operation": growth["operation"],
                    "input_fact_ids": growth["input_fact_ids"]})
            else:
                candidates = [row for row in catalog["facts"] if row["value"] == claim["value"]]
                fact = current if claim["display"].startswith("205.85") else candidates[0]
                bindings.append({"display": claim["display"], "occurrence": claim["occurrence"],
                    "fact_id": fact["fact_id"], "metric": fact["metric"], "period": fact["period"],
                    "accounting_basis": fact["accounting_basis"]})
        self.assertEqual(validate_reader_markdown(body, [report], explicit_fact_bindings=bindings)["status"], "passed")
        forged = json.loads(json.dumps(bindings)); forged[0]["input_fact_ids"].reverse()
        failed = validate_reader_markdown(body, [report], explicit_fact_bindings=forged)
        self.assertTrue(any("changed operation or source facts" in row for row in failed["errors"]))

    def test_occurrence_inventory_uses_exact_raw_markdown_coordinates_with_commas(self):
        body = "收入962.21亿美元，同比增长106%；Revenue1,000USD million，回落-12.5%。\n| 指标 | 数值（亿美元） |\n|---|---:|\n| 本期 | 962.21 |\n| 上期 | 962.21 |"
        inventory = claim_occurrence_inventory(body)
        self.assertEqual(inventory["coordinate_contract"],
                         "python-string-codepoint-offsets-v1; tables use one-based line/column")
        prose = [row for row in inventory["claims"] if "start" in row["occurrence"]]
        table = [row for row in inventory["claims"] if "line" in row["occurrence"]]
        self.assertEqual([body[row["occurrence"]["start"]:row["occurrence"]["end"]] for row in prose],
                         ["962.21亿美元", "106%", "1,000USD million", "-12.5%"])
        self.assertEqual([row["value"] for row in prose], ["962.21", "106", "1000", "-12.5"])
        self.assertEqual([(row["display"], row["occurrence"]) for row in table],
                         [("962.21", {"line": 4, "column": 2}), ("962.21", {"line": 5, "column": 2})])

    def test_occurrence_inventory_binds_year_duration_without_treating_calendar_year_as_quantity(self):
        body = "2026年第二季度，合同加权平均剩余期限为6.4年；对比样本为5 years。"
        claims = claim_occurrence_inventory(body)["claims"]
        self.assertEqual([(row["display"], row["value"], row["unit"]) for row in claims],
                         [("6.4年", "6.4", "年"), ("5 years", "5", "years")])

    def test_period_inventory_uses_only_explicit_local_modifiers_across_mixed_rows(self):
        body = """经营期间：2026-04-27 至 2026-07-26。\n收入962.21亿美元。\n截至2026-07-26，库存315.75亿美元；库存由截至2026-01-25的214.03亿美元增至截至2026-07-26的315.75亿美元，增长47.5%。\n| 指标 | 数值（亿美元） |\n|---|---:|\n| 上半年经营现金流 | 744.21 |\n| 应收账款 | 630.59 |\n| 期后承诺 | 1050 |"""
        claims = claim_occurrence_inventory(body)["claims"]
        prose = [row for row in claims if "start" in row["occurrence"]]
        self.assertIsNone(next(row for row in prose if row["display"] == "962.21亿美元")["period"])
        inventory_values = [row for row in prose if row["display"] in {"315.75亿美元", "214.03亿美元", "47.5%"}]
        self.assertEqual([row["period"] for row in inventory_values], [
            {"kind": "instant", "start": None, "end": "2026-07-26"},
            {"kind": "instant", "start": None, "end": "2026-01-25"},
            {"kind": "instant", "start": None, "end": "2026-07-26"}, None])
        table = [row for row in claims if "line" in row["occurrence"]]
        self.assertEqual([row["period"] for row in table], [None, None, None])

    def test_current_date_does_not_override_prior_period_qualifier(self):
        body = "截至2026-07-26，承诺已由上一季末1190亿美元增至2790亿美元。"
        claims = claim_occurrence_inventory(body)["claims"]
        self.assertIsNone(claims[0]["period"])
        self.assertIsNone(claims[1]["period"])
        safe = markdown().replace("缺少公告前一致预期，因此不能判断超预期或低估。",
            "所谓预期差仍是未知项，不能写成超预期、低估或市场尚未计价。")
        self.assertEqual(validate_reader_markdown(safe, [SOURCE])["status"], "passed")

    def test_explicit_period_conflict_still_rejects_catalog_binding(self):
        report = json.loads(json.dumps(SOURCE))
        report["evidence"][0]["numeric_facts"].append({"metric": "comparison-only", "value": "999",
            "unit": "USD million", "period": {"kind": "duration", "start": "2025-04-01", "end": "2025-06-30"}})
        body = markdown().replace("收入为 100 百万美元",
            "经营期间 2025-04-01 至 2025-06-30，收入为 100 百万美元")
        fact = next(row for row in financial_fact_catalog([report])["facts"] if row["metric"] == "revenue")
        bindings = [{"display": row["display"], "occurrence": row["occurrence"], "fact_id": fact["fact_id"],
                     "metric": fact["metric"], "period": fact["period"],
                     "accounting_basis": fact["accounting_basis"]}
                    for row in claim_occurrence_inventory(body)["claims"]]
        result = validate_reader_markdown(body, [report], explicit_fact_bindings=bindings)
        self.assertTrue(any("displayed period differs from catalog fact" in row for row in result["errors"]))

    def test_market_certainty_uses_sentence_level_negation(self):
        safe = markdown().replace("缺少公告前一致预期，因此不能判断超预期或低估。",
            "本文因此不作财报惊喜、估值、目标价或确定性交易判断。")
        self.assertEqual(validate_reader_markdown(safe, [SOURCE])["status"], "passed")
        unsafe = markdown().replace("缺少公告前一致预期，因此不能判断超预期或低估。", "研究给出目标价并认为公司被低估。")
        result = validate_reader_markdown(unsafe, [SOURCE])
        self.assertEqual(result["status"], "failed")
        self.assertTrue(any("unsupported market-expectation certainty" in row for row in result["errors"]))
        scoped = markdown().replace("缺少公告前一致预期，因此不能判断超预期或低估。",
            "缺少一致预期，因此不能据此给出目标价或买卖判断。")
        self.assertEqual(validate_reader_markdown(scoped, [SOURCE])["status"], "passed")
        for assertion in ("公司可以据此给出目标价。", "不能排除公司随后给出目标价。",
                          "一致预期未知，不能判断。公司仍给出目标价。", "并非不能给出目标价。"):
            unsafe = markdown().replace("缺少公告前一致预期，因此不能判断超预期或低估。", assertion)
            self.assertTrue(any("unsupported market-expectation certainty: 目标价" in row
                                for row in validate_reader_markdown(unsafe, [SOURCE])["errors"]), assertion)

    def test_real_v4_inventory_does_not_inherit_quarter_period(self):
        path = Path("/Users/cenxiangxiang/hr/repo/Trading-Copilot-Agent/runtime/earnings/p4-v4-debug.json")
        if not path.exists(): self.skipTest("real p4-v4 debug fixture unavailable")
        payload = json.loads(path.read_text()); claims = claim_occurrence_inventory(payload["draft"])["claims"]
        self.assertEqual(len(claims), 42)
        self.assertEqual(sum(row["period"] is None for row in claims), 38)
        self.assertIsNone(next(row["period"] for row in claims
                               if row["display"] == "47.5%" and "start" in row["occurrence"]))
        self.assertIsNone(next(row["period"] for row in claims
                               if row["display"] == "1050亿美元"))
        self.assertTrue(all(row["period"] is None for row in claims
                            if row["display"] in {"744.21", "630.59", "2790"} and "line" in row["occurrence"]))

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

    def test_explicit_repair_is_single_immutable_resumable_attempt_with_usage_ledger(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); (root / "config").mkdir()
            config_source = Path(__file__).resolve().parents[1] / "config/earnings_research.json"
            (root / "config/earnings_research.json").write_bytes(config_source.read_bytes())
            live = {**SOURCE, "source_mode": "live"}
            source = root / "report/earnings/source.json"; atomic_write_json(source, live)
            binary = root / "fake-codex"; binary.write_text("fake"); binary.chmod(0o700)
            manifest = prepare_input(root, publication_type="company", scope_id="TEST", quarter_id="2026-Q2",
                                     source_paths=[source])
            good = markdown(); bad = good.replace("公司向客户销售产品。", "毛利率改善证明公司定价权稳固。")
            calls = []

            def fake_codex(_root, _binary, _profile, prompt, output, _events, _stderr, _timeout):
                calls.append(output)
                if output.name == "reader-draft.md":
                    output.write_text(good if "唯一一次修复尝试" in prompt else bad)
                elif output.parent.name == "repair-attempt-1":
                    mappings = validate_reader_markdown(good, [live])["fact_mappings"]
                    for binding in mappings:
                        binding["input_fact_ids"] = []  # optional unused JSON field from real checker schema
                    atomic_write_json(output, {"status": "passed", "errors": [], "warnings": [],
                        "source_mapping": [{"section": "全文", "claim_ids": ["c1"], "evidence_ids": ["e1"]}],
                        "fact_bindings": mappings})
                else:
                    atomic_write_json(output, {"status": "failed",
                        "errors": ["毛利率改善不能推出定价权"], "warnings": [],
                        "source_mapping": [{"section": "全文", "claim_ids": ["c1"], "evidence_ids": ["e1"]}],
                        "fact_bindings": []})
                return {"input_tokens": 10, "output_tokens": 5}

            with patch("earnings_publication_runner._codex", side_effect=fake_codex):
                failed = run_publication(root, manifest, binary=str(binary), timeout=60)
                self.assertEqual(failed["status"], "failed")
                with self.assertRaisesRegex(ValueError, "independently passed"):
                    recheck_publication(root, manifest)
                original_draft = root / read_json(manifest)["permitted_outputs"]["draft"]
                original_hash = sha256_file(original_draft)
                legacy = read_json(manifest); legacy.pop("financial_fact_catalog")
                legacy["semantic_input"].pop("financial_fact_catalog_sha256")
                legacy["semantic_input"]["method"] = "reader-publication-v3"
                legacy.pop("input_manifest_hash")
                legacy["input_manifest_hash"] = sha256_bytes(canonical_json(legacy))
                atomic_write_json(manifest, legacy)
                repair_manifest = prepare_repair_input(root, manifest)
                self.assertEqual(prepare_repair_input(root, manifest), repair_manifest)
                repaired_input = read_json(repair_manifest)
                self.assertTrue(repaired_input["financial_fact_catalog"]["facts"])
                self.assertEqual(repaired_input["semantic_input"]["method"], "reader-publication-v4-repair-1")
                with self.assertRaisesRegex(ValueError, "cannot create another repair"):
                    prepare_repair_input(root, repair_manifest)
                repaired = run_publication(root, repair_manifest, binary=str(binary), timeout=60)
            self.assertEqual(repaired["status"], "success", repaired)
            self.assertEqual(sha256_file(original_draft), original_hash)
            self.assertEqual(repaired["attempt"]["repair_attempt"], 1)
            self.assertEqual(repaired["attempt"]["model_calls_completed"], 2)
            self.assertEqual([row["usage"] for row in repaired["attempt"]["calls"]],
                             [{"input_tokens": 10, "output_tokens": 5}] * 2)
            self.assertEqual(len(calls), 4)
            repair_result = run_publication(root, repair_manifest, binary=str(binary), timeout=60)
            self.assertEqual(repair_result, repaired); self.assertEqual(len(calls), 4)
            with patch("earnings_publication_runner._codex", side_effect=AssertionError("must not call model")):
                rechecked = recheck_publication(root, repair_manifest)
                self.assertEqual(rechecked["status"], "success")
                self.assertEqual(rechecked["audit"]["model_calls"], 0)
                self.assertEqual(recheck_publication(root, repair_manifest), rechecked)
                repaired_draft = root / read_json(repair_manifest)["permitted_outputs"]["draft"]
                repaired_draft.write_text(repaired_draft.read_text() + "changed")
                with self.assertRaisesRegex(ValueError, "writer draft changed"):
                    recheck_publication(root, repair_manifest)


    def test_failed_model_call_is_recorded_once_and_never_automatically_retried(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); (root / "config").mkdir()
            config_source = Path(__file__).resolve().parents[1] / "config/earnings_research.json"
            (root / "config/earnings_research.json").write_bytes(config_source.read_bytes())
            source = root / "report/earnings/source.json"; atomic_write_json(source, {**SOURCE, "source_mode": "live"})
            binary = root / "fake-codex"; binary.write_text("fake"); binary.chmod(0o700)
            manifest = prepare_input(root, publication_type="company", scope_id="TEST", quarter_id="2026-Q2",
                                     source_paths=[source])
            with patch("earnings_publication_runner._codex", side_effect=subprocess.TimeoutExpired("codex", 2)) as codex:
                with self.assertRaises(subprocess.TimeoutExpired):
                    run_publication(root, manifest, binary=str(binary), timeout=60)
                state = read_json(manifest.parent / "attempt-state.json")
                self.assertEqual((state["model_calls_started"], state["model_calls_completed"], state["model_calls_failed"]),
                                 (1, 0, 1))
                self.assertIsNone(state["calls"][0]["usage"])
                with self.assertRaisesRegex(ValueError, "already attempted"):
                    run_publication(root, manifest, binary=str(binary), timeout=60)
                self.assertEqual(codex.call_count, 1)

    def test_completed_writer_can_resume_checker_with_new_bounded_timeout(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); (root / "config").mkdir()
            config_source = Path(__file__).resolve().parents[1] / "config/earnings_research.json"
            (root / "config/earnings_research.json").write_bytes(config_source.read_bytes())
            live = {**SOURCE, "source_mode": "live"}
            source = root / "report/earnings/source.json"; atomic_write_json(source, live)
            binary = root / "fake-codex"; binary.write_text("fake"); binary.chmod(0o700)
            manifest = prepare_input(root, publication_type="company", scope_id="TEST", quarter_id="2026-Q2",
                                     source_paths=[source])
            timeouts = []
            def fake_codex(_root, _binary, _profile, _prompt, output, _events, _stderr, timeout):
                timeouts.append(timeout)
                if output.name == "reader-draft.md":
                    output.write_text(markdown())
                    return {"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1}
                if output.name == "semantic-check.json":
                    raise subprocess.TimeoutExpired("checker", timeout)
                mappings = validate_reader_markdown(markdown(), [live])["fact_mappings"]
                for binding in mappings: binding["input_fact_ids"] = []
                atomic_write_json(output, {"status": "passed", "errors": [], "warnings": [],
                    "source_mapping": [{"section": "全文", "claim_ids": ["c1"], "evidence_ids": ["e1"]}],
                    "fact_bindings": mappings})
                return {"input_tokens": 2, "cached_input_tokens": 1, "output_tokens": 1}
            with patch("earnings_publication_runner._codex", side_effect=fake_codex):
                with self.assertRaises(subprocess.TimeoutExpired):
                    run_publication(root, manifest, binary=str(binary), timeout=30)
                result = run_publication(root, manifest, binary=str(binary), timeout=60)
            self.assertEqual(result["status"], "success", result)
            state = read_json(manifest.parent / "attempt-state.json")
            self.assertEqual([row["attempt"] for row in state["calls"] if row["role"] == "checker"], [1, 2])
            self.assertEqual([row["timeout_seconds"] for row in state["invocations"]], [30, 60])
            self.assertEqual(len(timeouts), 3)
            self.assertGreater(timeouts[-1], timeouts[1])

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
