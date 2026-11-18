from __future__ import annotations

import contextlib
import io
import math
import shutil
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from mvp.agents import SemanticPerceptionAgent
from mvp.harness import Harness
from mvp.io import read_json, with_text_input
from mvp.ontology import OntologyModel
from mvp.playback import PlaybackSession
from mvp.text import LLMTextExtractor, TextPerceptionAgent, TEXT_CLASSES, TEXT_DATA_PROPERTIES, TEXT_RELATIONS
from run_demo import run


ROOT = Path(__file__).resolve().parents[1]


def scenario():
    return with_text_input(read_json(ROOT / "data" / "observations_R001.json"), ROOT / "data" / "texts_R001.json")


def edit_record(record, text):
    record.update(rawText=text, span=[0, len(text)])


class TextExtractionTests(unittest.TestCase):
    def test_sources_window_and_grounded_spans(self):
        raw = scenario()
        self.assertEqual(len({item["source"] for item in raw["texts"]}), 7)
        delta = SemanticPerceptionAgent().perceive(raw)
        self.assertEqual(len(delta["evidence"]), 11)
        self.assertEqual(delta["modalities"], ["trajectory", "text"])
        self.assertEqual({item["id"] for item in delta["excludedTexts"]}, {"TX-LOG-017", "TX-REG-017"})
        evidence = {item["id"]: item for item in delta["evidence"]}
        expected = {"vesselName", "callSign", "declaredVesselType", "declaredGearType", "hasFishingGear",
                    "hasDirectionalAntenna", "callAttempts", "responseStatus", "encryptedMessageCount",
                    "foreignAddress", "reportedSuspicion"}
        self.assertEqual({item["predicate"] for item in delta["textFacts"]}, expected)
        for fact in delta["textFacts"]:
            record = evidence[fact["evidence"]]
            start, end = fact["span"]
            self.assertEqual(record["rawText"][start:end], fact["quote"])
            self.assertEqual(fact["subject"], raw["vesselId"])

    def test_tolerance_boundaries_and_timezone_normalization(self):
        record = scenario()["texts"][0]
        for instant, count in (("2026-11-11T10:04:00+08:00", 1), ("2026-11-11T10:03:59+08:00", 0),
                               ("2026-11-11T02:21:00Z", 1), ("2026-11-11T10:21:01+08:00", 0)):
            with self.subTest(instant=instant):
                record["timestamp"] = instant
                delta = TextPerceptionAgent().perceive([record], "V-017", "2026-11-11T10:06:00+08:00", "2026-11-11T10:19:00+08:00")
                self.assertEqual(len(delta["evidence"]), count)

    def test_wrong_subject_excluded_and_zero_tolerance_respected(self):
        raw = scenario()
        raw["texts"][0]["subject"] = "V-OTHER"
        raw["textToleranceSeconds"] = 0
        delta = SemanticPerceptionAgent().perceive(raw)
        self.assertEqual(len(delta["evidence"]), 9)
        self.assertIn({"id": "TX-AIS-017", "reason": "subject_mismatch"}, delta["excludedTexts"])
        self.assertIn({"id": "TX-REPORT-017", "reason": "outside_window"}, delta["excludedTexts"])

    def test_duplicate_collision_and_malformed_records_fail(self):
        mutations = [lambda r: r["texts"].append(deepcopy(r["texts"][0])),
                     lambda r: r["texts"][0].update(id="EV-AIS-1006"),
                     lambda r: r["texts"][0].update(span=[0, 10000]),
                     lambda r: r["texts"][0].update(confidence=math.nan),
                     lambda r: r["texts"][0].update(confidence=1.1),
                     lambda r: r["texts"][0].update(timestamp="2026-11-11T10:05:00"),
                     lambda r: r.update(textToleranceSeconds=-1),
                     lambda r: r.update(textToleranceSeconds=True)]
        for mutate in mutations:
            raw = scenario()
            mutate(raw)
            with self.assertRaises(ValueError):
                SemanticPerceptionAgent().perceive(raw)

    def test_span_limits_extraction_and_negations_do_not_assert_positive_facts(self):
        record = scenario()["texts"][1]
        edit_record(record, "未发现定向天线；标注：有渔具。")
        result = TextPerceptionAgent().perceive([record], "V-017", "2026-11-11T10:06:00+08:00", "2026-11-11T10:19:00+08:00")
        self.assertEqual([(f["predicate"], f["value"]) for f in result["textFacts"]], [("hasFishingGear", True)])
        edit_record(record, "没有渔具；没有定向天线。")
        result = TextPerceptionAgent().perceive([record], "V-017", "2026-11-11T10:06:00+08:00", "2026-11-11T10:19:00+08:00")
        self.assertEqual([(f["predicate"], f["value"]) for f in result["textFacts"]], [("hasFishingGear", False)])
        record = scenario()["texts"][0]
        record["span"] = [0, record["rawText"].index("；")]
        result = TextPerceptionAgent().perceive([record], "V-017", "2026-11-11T10:06:00+08:00", "2026-11-11T10:19:00+08:00")
        self.assertEqual([f["predicate"] for f in result["textFacts"]], ["vesselName"])

    def test_llm_adapter_candidates_are_grounded_and_do_not_enter_rules(self):
        class Extractor(LLMTextExtractor):
            def extract(self, record):
                return [{"predicate": "declaredVesselType", "value": "捕捞船", "span": record["span"]}]
        agent = SemanticPerceptionAgent(TextPerceptionAgent(Extractor()))
        raw = scenario()
        raw["texts"] = [raw["texts"][1]]
        delta = agent.perceive(raw)
        self.assertEqual(len(delta["textCandidates"]), 1)
        self.assertNotIn("declaredVesselType", {f["predicate"] for f in delta["textFacts"]})
        self.assertEqual(delta["textCandidates"][0]["evidence"], "TX-EO-017")
        class Invalid(LLMTextExtractor):
            def extract(self, record):
                return [{"predicate": "callSign", "value": "invented", "span": [0, 9999]}]
        with self.assertRaises(ValueError):
            SemanticPerceptionAgent(TextPerceptionAgent(Invalid())).perceive(raw)


class TextIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT / "ontology" / "00", self.root / "ontology" / "00")
        self.initial = read_json(ROOT / "data" / "initial_graph.json")

    def harness(self):
        return Harness(self.root, self.initial)

    def test_fused_round_reasoner_rules_events_and_eleven_main_evidence_links(self):
        harness = self.harness()
        report = harness.run_round(scenario())
        self.assertEqual(report["status"], "COMMITTED")
        self.assertTrue(report["ontologyValidation"]["hermitResultUsed"])
        self.assertEqual(report["feedback"]["ruleHits"], [f"R{i}" for i in range(1, 7)])
        graph = harness.graph.document
        main = graph["nodes"]["E-017"]
        self.assertEqual(len(main["evidence"]), 11)
        self.assertEqual(main["properties"]["labels"], ["身份矛盾", "通信异常"])
        self.assertEqual(main["properties"]["reviewStatus"], "pending")
        self.assertEqual(main["properties"]["confidence"], 0.95)
        self.assertEqual(main["properties"]["threatLevel"], "elevated")
        events = {node_id: node for node_id, node in graph["nodes"].items() if node_id in report["updatePlan"]["textSummary"]["events"]}
        self.assertEqual({tuple(node["types"]) for node in events.values()}, {("IdentityConflict",), ("CommunicationAnomaly",)})
        for event in events:
            self.assertTrue(harness.execution_agent.record_review(event, "rejected", "待核实")["eventRetained"])
        for provenance in graph["provenance"]:
            if provenance["evidence"].startswith("TX-"):
                evidence = graph["evidence"][provenance["evidence"]]
                self.assertEqual(provenance["span"], evidence["span"])
                self.assertEqual(provenance["rawText"], evidence["rawText"])
                for fact in provenance["factSpans"]:
                    self.assertEqual(evidence["rawText"][slice(*fact["span"])], fact["quote"])
        self.assertEqual(report["executionResult"]["createdNodes"], 10)
        self.assertEqual(report["executionResult"]["createdRelations"], 23)
        self.assertEqual(report["executionResult"]["attachedEvidence"], 17)
        self.assertEqual(report["executionResult"]["registeredEvidence"], 11)
        self.assertEqual(report["comparison"]["incrementalWrites"], 74)
        self.assertEqual(report["comparison"]["fullRebuildWritesEstimate"], 88)

    def test_minimal_declaration_and_visual_annotation_create_identity_conflict(self):
        raw = scenario()
        raw["texts"] = raw["texts"][:2]
        edit_record(raw["texts"][0], "申报为捕捞船。")
        edit_record(raw["texts"][1], "无渔具。")
        report = self.harness().run_round(raw)
        self.assertEqual(report["status"], "COMMITTED")
        self.assertIn("R4", report["feedback"]["ruleHits"])
        self.assertNotIn("R5", report["feedback"]["ruleHits"])
        self.assertNotIn("R6", report["feedback"]["ruleHits"])

    def test_negative_rule_conditions_and_source_scoping(self):
        raw = scenario()
        edit_record(raw["texts"][1], "标注：有渔具；未发现定向天线。")
        edit_record(raw["texts"][2], "呼叫3次，应答状态：已应答。")
        raw["texts"][4]["subject"] = "V-OTHER"
        report = self.harness().run_round(raw)
        self.assertEqual(report["feedback"]["ruleHits"], ["R1", "R2", "R3"])
        self.assertEqual(report["updatePlan"]["textSummary"]["events"], [])

    def test_r5_thresholds_and_responded_calls_are_not_mixed(self):
        for text, expected in (("呼叫2次，均无应答。", False), ("呼叫3次，均无应答。", True),
                                ("呼叫3次，均无应答；正常应答。", False)):
            # Build the plan against a validated text ontology without publishing per case.
            harness = self.harness()
            raw = scenario()
            edit_record(raw["texts"][2], text)
            edit_record(raw["texts"][3], "加密短报文20条。")
            delta = harness.semantic_agent.perceive(raw)
            current = harness.ontology_agent.load_current()
            _, ontology = harness.ontology_agent.validate_ontology(current, harness.ontology_agent.propose(delta, current), self.initial)
            plan = harness.planning_agent.plan(delta, ontology, self.initial)
            self.assertEqual("R5" in plan["ruleHits"], expected)
            if expected:
                edit_record(raw["texts"][3], "加密短报文19条。")
                delta = harness.semantic_agent.perceive(raw)
                self.assertNotIn("R5", harness.planning_agent.plan(delta, ontology, self.initial)["ruleHits"])

    def test_r6_requires_sensitive_loitering_and_candidate_and_caps_confidence(self):
        harness = self.harness()
        raw = scenario()
        for record in raw["texts"] + raw["observations"]:
            record["confidence"] = 0.98
        delta = harness.semantic_agent.perceive(raw)
        current = harness.ontology_agent.load_current()
        _, ontology = harness.ontology_agent.validate_ontology(current, harness.ontology_agent.propose(delta, current), self.initial)
        plan = harness.planning_agent.plan(delta, ontology, self.initial)
        self.assertEqual(plan["createIndividuals"][1]["properties"]["confidence"], 0.99)
        delta["areaSensitive"] = False
        self.assertNotIn("R6", harness.planning_agent.plan(delta, ontology, self.initial)["ruleHits"])
        delta["areaSensitive"] = True
        delta["states"][0]["speed"] = 6
        self.assertNotIn("R6", harness.planning_agent.plan(delta, ontology, self.initial)["ruleHits"])

    def test_text_ontology_can_follow_existing_trajectory_version(self):
        harness = self.harness()
        harness.run_round(read_json(ROOT / "data" / "observations_R001.json"))
        current = harness.ontology_agent.load_current()
        self.assertEqual(current.version, "01")
        delta = harness.semantic_agent.perceive(scenario())
        proposal = harness.ontology_agent.propose(delta, current)
        validation, updated = harness.ontology_agent.validate_ontology(current, proposal, harness.graph.document)
        self.assertTrue(validation["consistent"])
        self.assertEqual(updated.version, "02")
        harness.ontology_agent.publish(updated, validation)
        self.assertEqual(harness.ontology_agent.load_current().version, "02")
        self.assertTrue(set(TEXT_CLASSES) <= set(updated.classes))
        self.assertTrue(set(TEXT_RELATIONS) <= set(updated.object_properties))
        self.assertTrue(set(TEXT_DATA_PROPERTIES) <= set(updated.document["dataProperties"]))

    def test_missing_text_provenance_and_mutated_facts_are_rejected(self):
        harness = self.harness()
        report = harness.run_round(scenario())
        ontology = harness.ontology_agent.load_current()
        plan = deepcopy(report["updatePlan"])
        plan["evidenceRecords"] = [item for item in plan["evidenceRecords"] if item["id"] != "TX-AIS-017"]
        self.assertFalse(harness.ontology_agent.validate_plan(plan, ontology, self.initial, report["deltaSemantics"])["passed"])
        plan = deepcopy(report["updatePlan"])
        plan["textFacts"][0]["span"] = [0, 1]
        self.assertFalse(harness.ontology_agent.validate_plan(plan, ontology, self.initial, report["deltaSemantics"])["passed"])

    def test_text_modes_reject_replan_and_roll_back_with_no_partial_writes(self):
        for mode, status in (("ontology_conflict", "REJECTED"), ("plan_rejected", "COMMITTED"), ("execution_rollback", "EXECUTION_FAILED")):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                shutil.copytree(ROOT / "ontology" / "00", root / "ontology" / "00")
                harness = Harness(root, self.initial)
                report = harness.run_round(scenario(), mode)
                self.assertEqual(report["status"], status)
                if status != "COMMITTED":
                    self.assertEqual(harness.graph.document, self.initial)
                else:
                    self.assertEqual(len(harness.graph.document["nodes"]["E-017"]["evidence"]), 11)

    def test_cli_text_option_exports_report_and_evidence_snapshot(self):
        with contextlib.redirect_stdout(io.StringIO()):
            report = run(ROOT / "data" / "observations_R001.json", self.root / "reports", texts_path=ROOT / "data" / "texts_R001.json")
        self.assertEqual(report["status"], "COMMITTED")
        saved = read_json(self.root / "reports" / "graph_after_R-001.json")
        self.assertEqual(len(saved["nodes"]["E-017"]["evidence"]), 11)

    def test_cli_exports_02_from_published_01_and_removes_stale_versions(self):
        first = self.root / "first"
        second = self.root / "second"
        with contextlib.redirect_stdout(io.StringIO()):
            run(ROOT / "data" / "observations_R001.json", first)
            report = run(ROOT / "data" / "observations_R001.json", second,
                         texts_path=ROOT / "data" / "texts_R001.json", ontology_dir=first / "ontology")
        self.assertEqual(report["status"], "COMMITTED")
        self.assertEqual(report["ontologyVersion"], "02")
        index = read_json(second / "ontology" / "versions.json")
        self.assertEqual(index["latestVersion"], "02")
        self.assertEqual([item["versionId"] for item in index["versions"]], ["00", "01", "02"])
        for version in ("00", "01", "02"):
            self.assertTrue((second / "ontology" / version / "ontology.json").is_file())
            self.assertTrue((second / "ontology" / version / "ontology.ttl").is_file())
        self.assertTrue(read_json(second / "ontology" / "02" / "validation_report.json")["consistent"])
        self.assertNotIn("TextEvidence", read_json(first / "ontology" / "01" / "ontology.json")["classes"])
        note = second / "ontology" / "02" / "keep.txt"
        note.write_text("user note", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            rejected = run(ROOT / "data" / "observations_R001.json", second, mode="ontology_conflict")
        self.assertEqual(rejected["status"], "REJECTED")
        self.assertEqual(read_json(second / "ontology" / "versions.json")["latestVersion"], "00")
        self.assertFalse((second / "ontology" / "01").exists())
        self.assertFalse((second / "ontology" / "02" / "ontology.json").exists())
        self.assertEqual(note.read_text(encoding="utf-8"), "user note")

    def test_playback_text_session_writes_only_at_step_seven(self):
        harness = self.harness()
        session = PlaybackSession(harness, scenario())
        for _ in range(6):
            session.advance()
            self.assertEqual(harness.graph.document, self.initial)
        self.assertEqual(session.advance()["status"], "COMMITTED")
        self.assertEqual(len(harness.graph.document["nodes"]["E-017"]["evidence"]), 11)


if __name__ == "__main__":
    unittest.main()
