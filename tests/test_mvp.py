from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from mvp.graph import ActionExecutionAgent, GraphStore
from mvp.harness import Harness
from mvp.io import read_json
from mvp.ontology import OntologyModel


ROOT = Path(__file__).resolve().parents[1]


class MvpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "ontology").mkdir()
        shutil.copytree(ROOT / "ontology" / "00", self.root / "ontology" / "00")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def scenario(self) -> dict:
        return read_json(ROOT / "data" / "observations_R001.json")

    def new_harness(self, scenario: dict | None = None) -> Harness:
        return Harness(self.root, read_json(ROOT / "data" / "initial_graph.json"))

    def test_full_case_publishes_ontology_and_commits_incremental_subgraph(self) -> None:
        harness = self.new_harness()
        report = harness.run_round(self.scenario())
        graph = harness.graph.document

        self.assertEqual(report["status"], "COMMITTED")
        self.assertEqual(report["ontologyVersion"], "01")
        self.assertEqual(report["feedback"]["ruleHits"], ["R1", "R2", "R3"])
        self.assertTrue(report["ontologyValidation"]["consistent"])
        self.assertTrue(report["ontologyValidation"]["hermitResultUsed"])
        self.assertEqual(report["ontologyValidation"]["engine"], "HermiT")
        self.assertEqual(report["comparison"]["incrementalWrites"], 22)
        self.assertEqual(report["comparison"]["fullRebuildWritesEstimate"], 36)
        self.assertEqual(report["comparison"]["avoidedWritesEstimate"], 14)
        self.assertTrue(report["planValidation"]["passed"])
        self.assertEqual(report["agents"]["ontologyEvolutionAndValidation"], "ontology_evolution_agent")
        self.assertEqual(report["agents"]["decisionPlanning"], "decision_planning_agent")
        self.assertEqual(
            [call["tool"] for call in report["ontologyToolAudit"]],
            ["createClass", "createObjectProperty", "addSWRLRule", "addSWRLRule", "publishOntologyVersion"],
        )
        self.assertEqual(report["executionResult"]["createdNodes"], 2)
        self.assertEqual(report["executionResult"]["createdRelations"], 5)
        self.assertEqual(report["executionResult"]["createdTypeAssertions"], 2)
        self.assertEqual(report["executionResult"]["attachedEvidence"], 6)
        self.assertEqual(graph["nodes"]["S-017-0"]["validTo"], "2026-11-11T10:06:00+08:00")
        self.assertIn("S-017-0", graph["nodes"])
        self.assertIn("E-017", graph["nodes"])
        self.assertEqual(len(graph["nodes"]["E-017"]["evidence"]), 6)
        self.assertTrue((self.root / "ontology" / "01" / "ontology.ttl").exists())
        published = OntologyModel.load(self.root / "ontology" / "01" / "ontology.json")
        self.assertEqual(published.object_properties["hasInstant"]["domain"], "TemporalEntity")
        generated_ttl = (self.root / "ontology" / "01" / "ontology.ttl").read_text(encoding="utf-8")
        self.assertIn("maritime:speed a owl:DatatypeProperty", generated_ttl)
        self.assertIn("maritime:SuspectedIllegalFishingActivity", generated_ttl)
        version_index = read_json(self.root / "ontology" / "versions.json")
        self.assertEqual(version_index["latestVersion"], "01")
        self.assertEqual([item["versionId"] for item in version_index["versions"]], ["00", "01"])

    def test_fast_vessel_does_not_hit_r1_or_create_event(self) -> None:
        scenario = self.scenario()
        for item in scenario["observations"]:
            if item["source"] == "AIS":
                item["speedKn"] = 6.0
        harness = self.new_harness()
        report = harness.run_round(scenario)
        self.assertNotIn("R1", report["feedback"]["ruleHits"])
        self.assertNotIn("E-017", harness.graph.document["nodes"])
        self.assertEqual(report["executionResult"]["createdNodes"], 1)

    def test_duration_and_distance_thresholds_gate_event(self) -> None:
        short = self.scenario()
        ais = [item for item in short["observations"] if item["source"] == "AIS"]
        ais[-1]["timestamp"] = "2026-11-11T10:14:20+08:00"
        short_harness = self.new_harness()
        short_report = short_harness.run_round(short)
        self.assertNotIn("R3", short_report["feedback"]["ruleHits"])
        self.assertNotIn("E-017", short_harness.graph.document["nodes"])

        long_move = self.scenario()
        ais = [item for item in long_move["observations"] if item["source"] == "AIS"]
        ais[-1]["segmentDistanceNm"] = 0.68
        distance_harness = self.new_harness()
        distance_report = distance_harness.run_round(long_move)
        self.assertNotIn("R3", distance_report["feedback"]["ruleHits"])

    def test_outside_sensitive_area_does_not_match_r2(self) -> None:
        scenario = self.scenario()
        scenario["areaSensitive"] = False
        report = self.new_harness().run_round(scenario)
        self.assertIn("R1", report["feedback"]["ruleHits"])
        self.assertNotIn("R2", report["feedback"]["ruleHits"])
        self.assertNotIn("R3", report["feedback"]["ruleHits"])

    def test_wrong_range_proposal_is_rejected_by_disjointness(self) -> None:
        harness = self.new_harness()
        graph = harness.graph.document
        current = harness.ontology_agent.load_current()
        scenario = self.scenario()
        delta = harness.semantic_agent.perceive(scenario)
        proposal = harness.ontology_agent.propose(delta, current)
        proposal["newObjectProperties"][0]["range"] = "State"
        report, candidate = harness.ontology_agent.checker.validate_proposal(current, proposal, graph)
        self.assertFalse(report["consistent"])
        self.assertTrue(report["hermitResultUsed"])
        self.assertFalse(report["checks"]["disjointness"])
        self.assertIsNone(candidate)

    def test_demo_branches_run_real_reasoner_replanning_and_rollback(self) -> None:
        conflict = self.new_harness().run_round(self.scenario(), demo_mode="ontology_conflict")
        self.assertEqual(conflict["status"], "REJECTED")
        self.assertTrue(conflict["ontologyValidation"]["hermitResultUsed"])
        self.assertTrue(conflict["ontologyValidation"]["hermitExecuted"])
        self.assertFalse(conflict["ontologyValidation"]["consistent"])

        invalid_plan_harness = self.new_harness()
        invalid_plan = invalid_plan_harness.run_round(self.scenario(), demo_mode="plan_rejected")
        self.assertEqual(invalid_plan["status"], "COMMITTED")
        self.assertTrue(invalid_plan["planValidation"]["passed"])
        self.assertEqual(invalid_plan["updatePlan"]["replanAttempt"], 2)
        self.assertTrue(any(item["state"] == "PLAN_REJECTED" for item in invalid_plan["trace"]))

        rollback_harness = self.new_harness()
        rollback = rollback_harness.run_round(self.scenario(), demo_mode="execution_rollback")
        self.assertEqual(rollback["status"], "EXECUTION_FAILED")
        self.assertTrue(rollback["executionResult"]["rolledBack"])
        self.assertNotIn("E-017", rollback_harness.graph.document["nodes"])

    def test_plan_validator_rejects_type_range_and_missing_provenance(self) -> None:
        harness = self.new_harness()
        report = harness.run_round(self.scenario())
        ontology = OntologyModel.load(self.root / "ontology" / "01" / "ontology.json")
        delta = report["deltaSemantics"]
        plan = report["updatePlan"]

        bad_type = deepcopy(plan)
        bad_type["createIndividuals"][1]["type"] = "UnknownClass"
        bad_type_report = harness.ontology_agent.validate_plan(bad_type, ontology, read_json(ROOT / "data" / "initial_graph.json"), delta)
        self.assertFalse(bad_type_report["checks"]["typeCheck"])

        bad_range = deepcopy(plan)
        bad_range["assertRelations"].append({"s": "V-017", "p": "hasPotentialIllegalActivity", "o": "S-017-1"})
        bad_range_report = harness.ontology_agent.validate_plan(bad_range, ontology, read_json(ROOT / "data" / "initial_graph.json"), delta)
        self.assertFalse(bad_range_report["checks"]["domainRangeCheck"])

        no_evidence = deepcopy(plan)
        no_evidence["attachEvidence"] = []
        no_evidence_report = harness.ontology_agent.validate_plan(no_evidence, ontology, read_json(ROOT / "data" / "initial_graph.json"), delta)
        self.assertFalse(no_evidence_report["checks"]["provenanceCheck"])

    def test_duplicate_plan_is_idempotent(self) -> None:
        harness = self.new_harness()
        report = harness.run_round(self.scenario())
        before = harness.graph.snapshot()
        replay = harness.execution_agent.execute(report["updatePlan"])
        self.assertTrue(replay["success"])
        self.assertTrue(replay["idempotentReplay"])
        self.assertEqual(before, harness.graph.document)

    def test_failed_execution_rolls_back_every_staged_change(self) -> None:
        initial = read_json(ROOT / "data" / "initial_graph.json")
        harness = self.new_harness()
        report = harness.run_round(self.scenario())
        fresh_graph = GraphStore(initial)
        executor = ActionExecutionAgent(fresh_graph)
        result = executor.execute(report["updatePlan"], fail_after_operations=3)
        self.assertFalse(result["success"])
        self.assertTrue(result["rolledBack"])
        self.assertEqual(fresh_graph.document, initial)

    def test_rejected_review_keeps_event_and_records_feedback(self) -> None:
        harness = self.new_harness()
        harness.run_round(self.scenario())
        review = harness.execution_agent.record_review("E-017", "rejected", "manual review found insufficient evidence")
        self.assertTrue(review["eventRetained"])
        self.assertIn("E-017", harness.graph.document["nodes"])
        self.assertEqual(harness.graph.document["nodes"]["E-017"]["properties"]["reviewStatus"], "rejected")
        self.assertEqual(len(harness.graph.document["feedback"]), 1)


if __name__ == "__main__":
    unittest.main()

