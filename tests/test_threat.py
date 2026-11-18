from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import shutil
import tempfile
import unittest

from mvp.agents import SemanticPerceptionAgent
from mvp.harness import Harness
from mvp.io import read_json
from mvp import web_server

ROOT = Path(__file__).resolve().parents[1]


class ThreatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        shutil.copytree(ROOT / "ontology/00", root / "ontology/00")
        self.harness = Harness(root, read_json(ROOT / "data/initial_graph.json"))
        self.raw = read_json(ROOT / "data/observations_disguised.json")

    def run_case(self, raw=None, mode="standard"):
        report = self.harness.run_round(raw or self.raw, mode)
        self.assertEqual(report["status"], "COMMITTED" if mode != "execution_rollback" else "EXECUTION_FAILED")
        return report

    def test_full_cluster_real_reasoner_and_provenance(self):
        report = self.run_case()
        self.assertTrue(report["ontologyValidation"]["hermitResultUsed"])
        self.assertEqual(set(report["updatePlan"]["ruleHits"]), {f"R{i}" for i in range(1, 10)})
        summary = report["updatePlan"]["threatSummary"]
        self.assertEqual(summary["threatLevel"], "high")
        self.assertEqual(summary["confidence"], 0.90)
        self.assertEqual(len(summary["subEvents"]), 5)
        graph = self.harness.graph.document
        for event_id in summary["subEvents"] + [summary["assessmentId"], summary["alertId"]]:
            self.assertEqual(graph["nodes"][event_id]["properties"]["reviewStatus"], "pending")
            for evidence_id in graph["nodes"][event_id]["evidence"]:
                self.assertIn(evidence_id, graph["evidence"])
                self.assertTrue(any(p["event"] == event_id and p["evidence"] == evidence_id for p in graph["provenance"]))
        self.assertEqual(report["ontologyValidation"]["expertReview"]["status"], "pending")

    def test_identity_only_no_high_threat(self):
        report = self.run_case(read_json(ROOT / "data/observations_insufficient.json"))
        summary = report["updatePlan"]["threatSummary"]
        self.assertIsNone(summary["alertId"])
        self.assertEqual(summary["threatLevel"], "low")
        self.assertIn("missing_CommunicationAnomaly", summary["blockedReasons"])
        self.assertIn("R4", report["updatePlan"]["ruleHits"])

    def test_low_confidence_and_outside_area_block_alert(self):
        for mutation in ("low", "outside"):
            with self.subTest(mutation=mutation):
                raw = deepcopy(self.raw)
                if mutation == "low":
                    next(t for t in raw["texts"] if t["source"] == "EOAnnotation")["confidence"] = 0.5
                else:
                    raw["areaSensitive"] = False
                report = self.run_case(raw)
                summary = report["updatePlan"]["threatSummary"]
                self.assertIsNone(summary["alertId"])
                self.assertNotIn("R8", report["updatePlan"]["ruleHits"])

    def test_r7_patterns_are_grounded_annotations(self):
        for pattern in ("circling", "zigzag", "stationary"):
            raw = deepcopy(self.raw)
            for o in raw["observations"]:
                if "trajectoryPattern" in o:
                    o["trajectoryPattern"] = pattern
            delta = SemanticPerceptionAgent().perceive(raw)
            self.assertEqual({f["value"] for f in delta["trajectoryFacts"]}, {pattern})
        raw["observations"][0]["subject"] = "V-OTHER"
        with self.assertRaises(ValueError):
            SemanticPerceptionAgent().perceive(raw)

    def test_r9_requires_explicit_silence_outbound_and_no_response(self):
        for text in ("加密短报文25条；加密短报文外发。", "通信静默；加密短报文25条。",
                     "未发现通信静默；加密短报文25条；加密短报文外发。",
                     "通信静默；正常通信；加密短报文25条；加密短报文外发。"):
            raw = deepcopy(self.raw)
            item = next(t for t in raw["texts"] if t["source"] == "CommunicationMonitor")
            item.update(rawText=text, span=[0, len(text)])
            report = self.run_case(raw)
            self.assertNotIn("R9", report["updatePlan"]["ruleHits"])

    def test_tampered_cluster_or_alert_is_rejected(self):
        report = self.run_case()
        for tamper in ("score", "relation", "evidence", "rule", "summary"):
            plan = deepcopy(report["updatePlan"])
            summary = plan["threatSummary"]
            if tamper == "score":
                next(n for n in plan["createIndividuals"] if n["id"] == summary["assessmentId"])["properties"]["confidence"] = 1
            elif tamper == "relation":
                plan["assertRelations"] = [r for r in plan["assertRelations"] if r["p"] != "hasSubEvent"]
            elif tamper == "evidence":
                plan["attachEvidence"] = [a for a in plan["attachEvidence"] if a["event"] != summary["alertId"]]
            elif tamper == "rule":
                plan["ruleHits"].remove("R8")
            else:
                summary["threatLevel"] = "low"
            validation = self.harness.ontology_agent.validate_plan(plan, self.harness.execution_agent.ontology,
                            self.harness.initial_graph, report["deltaSemantics"])
            self.assertFalse(validation["passed"], tamper)
            self.assertFalse(validation["checks"]["threatEvidenceCheck"])

    def test_threat_transaction_failure_removes_alert_and_cluster(self):
        before = self.harness.graph.snapshot()
        report = self.run_case(mode="execution_rollback")
        self.assertTrue(report["executionResult"]["rolledBack"])
        self.assertEqual(self.harness.graph.document, before)

    def test_expert_review_is_separate_versioned_history(self):
        report = self.run_case()
        agent = self.harness.ontology_agent
        for status in ("approved", "rejected"):
            review = agent.record_expert_review(report["ontologyVersion"], status, "测试复审人", "测试意见")
            self.assertEqual(review["status"], status)
        saved = read_json(Path(self.temp.name) / "ontology/01/validation_report.json")
        self.assertEqual(len(saved["expertReviewHistory"]), 2)
        self.assertTrue(saved["consistent"])
        for args in (("../00", "approved", "x", "x"), ("01", "approved", "", "x")):
            with self.assertRaises(ValueError):
                agent.record_expert_review(*args)

    def test_web_scenarios_and_disabled_text(self):
        try:
            for name in ("disguised", "insufficient"):
                result = web_server._run(scenario_name=name)
                self.assertEqual(result["report"]["status"], "COMMITTED")
                self.assertEqual(result["report"]["updatePlan"]["threatSummary"]["threatLevel"] == "high", name == "disguised")
            result = web_server._run(scenario_name="disguised", include_text=False)
            self.assertIsNone(result["report"]["updatePlan"]["threatSummary"]["alertId"])
        finally:
            if web_server.CURRENT.get("temp"):
                web_server.CURRENT["temp"].cleanup()
            web_server.CURRENT.update(harness=None, report=None, temp=None)


if __name__ == "__main__":
    unittest.main()
