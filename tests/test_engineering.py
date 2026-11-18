from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from mvp import web_server
from mvp.agents import DecisionPlanningAgent
from mvp.graph import ActionExecutionAgent, GraphStore
from mvp.harness import Harness
from mvp.io import read_json, write_json, write_text
from mvp.ontology import OntologyModel
from mvp.playback import PlaybackSession
from run_demo import export_ontology, run


ROOT = Path(__file__).resolve().parents[1]


class ScenarioTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT / "ontology" / "00", self.root / "ontology" / "00")
        self.initial = read_json(ROOT / "data" / "initial_graph.json")
        self.raw = read_json(ROOT / "data" / "observations_R001.json")
        self.harness = Harness(self.root, self.initial)

    def prepared(self):
        delta = self.harness.semantic_agent.perceive(self.raw)
        agent = self.harness.ontology_agent
        current = agent.load_current()
        report, ontology = agent.validate_ontology(current, agent.propose(delta, current), self.initial)
        self.assertTrue(report["consistent"])
        plan = self.harness.planning_agent.plan(delta, ontology, self.initial)
        return delta, ontology, plan


class ReplanningTests(ScenarioTest):
    def test_type_and_evidence_repairs_preserve_unrelated_decisions(self):
        delta, ontology, plan = self.prepared()
        plan["createIndividuals"][1]["type"] = "UnknownEvent"
        plan["assertTypes"][1]["type"] = "UnknownEvent"
        plan["attachEvidence"] = [{"event": "E-017", "evidence": "missing"}]
        plan["evidenceRecords"] = []
        plan["operatorNote"] = "retain this decision"
        before = deepcopy(plan)
        validation = self.harness.ontology_agent.validate_plan(plan, ontology, self.initial, delta)
        self.assertFalse(validation["passed"])
        # Regeneration is forbidden: repairs must apply to the actual rejected plan.
        with patch.object(DecisionPlanningAgent, "plan", side_effect=AssertionError("unexpected regeneration")):
            repaired = self.harness.planning_agent.replan(plan, validation, delta, ontology, self.initial)
        self.assertEqual(plan, before)
        self.assertEqual(repaired["operatorNote"], "retain this decision")
        self.assertEqual(repaired["closeStates"], plan["closeStates"])
        self.assertEqual(repaired["assertRelations"], plan["assertRelations"])
        self.assertEqual(repaired["replanAttempt"], 2)
        self.assertEqual(len(repaired["attachEvidence"]), 6)
        self.assertEqual(len(repaired["evidenceRecords"]), 6)
        self.assertEqual({item["action"] for item in repaired["replanRepairs"]}, {"restoreEventType", "restoreEvidence"})
        self.assertTrue(self.harness.ontology_agent.validate_plan(repaired, ontology, self.initial, delta)["passed"])

    def test_missing_evidence_is_restored_from_matching_observations_only(self):
        delta, ontology, plan = self.prepared()
        plan["attachEvidence"] = []
        validation = self.harness.ontology_agent.validate_plan(plan, ontology, self.initial, delta)
        repaired = self.harness.planning_agent.replan(plan, validation, delta, ontology, self.initial)
        self.assertTrue(self.harness.ontology_agent.validate_plan(repaired, ontology, self.initial, delta)["passed"])
        delta["evidence"] = []
        unrepaired = self.harness.planning_agent.replan(plan, validation, delta, ontology, self.initial)
        self.assertEqual(unrepaired["attachEvidence"], [])
        self.assertFalse(self.harness.ontology_agent.validate_plan(unrepaired, ontology, self.initial, delta)["passed"])

    def test_no_suggestion_does_not_silently_repair_a_plan(self):
        delta, ontology, plan = self.prepared()
        plan["createIndividuals"][1]["type"] = "UnknownEvent"
        repaired = self.harness.planning_agent.replan(plan, {"suggestions": []}, delta, ontology, self.initial)
        self.assertEqual(repaired["createIndividuals"], plan["createIndividuals"])
        self.assertEqual(repaired["replanRepairs"], [])

    def test_missing_registration_and_combined_missing_links_are_repaired(self):
        delta, ontology, base = self.prepared()
        for combined in (False, True):
            with self.subTest(combined=combined):
                plan = deepcopy(base)
                plan["evidenceRecords"] = []
                if combined:
                    plan["createIndividuals"][1]["type"] = "UnknownEvent"
                    plan["attachEvidence"] = []
                validation = self.harness.ontology_agent.validate_plan(plan, ontology, self.initial, delta)
                self.assertFalse(validation["checks"]["provenanceCheck"])
                repaired = self.harness.planning_agent.replan(plan, validation, delta, ontology, self.initial)
                self.assertTrue(self.harness.ontology_agent.validate_plan(repaired, ontology, self.initial, delta)["passed"])

    def test_report_retains_rejected_plan_and_its_feedback(self):
        report = self.harness.run_round(self.raw, demo_mode="plan_rejected")
        history = report["planHistory"]
        self.assertEqual(len(history), 2)
        self.assertFalse(history[0]["validation"]["passed"])
        self.assertTrue(history[1]["validation"]["passed"])
        self.assertEqual(history[0]["plan"]["createIndividuals"][1]["type"], "UndeclaredEventType")
        self.assertTrue(history[1]["plan"]["replanRepairs"])


class ReviewTests(unittest.TestCase):
    def test_arbitrary_multilevel_event_subclass_uses_current_ontology(self):
        ontology = OntologyModel({"classes": {"Event": None, "NewEvent": "Event", "CustomEvent": "NewEvent", "State": None}})
        graph = GraphStore({"nodes": {"custom": {"types": ["CustomEvent"]}, "state": {"types": ["State"]}}})
        executor = ActionExecutionAgent(graph, ontology)
        result = executor.record_review("custom", "confirmed", "verified")
        self.assertTrue(result["eventRetained"])
        self.assertEqual(graph.document["nodes"]["custom"]["properties"]["reviewStatus"], "confirmed")
        before = graph.snapshot()
        for event, status in (("state", "confirmed"), ("absent", "confirmed"), ("custom", "invalid")):
            with self.assertRaises(ValueError):
                executor.record_review(event, status, "invalid")
        self.assertEqual(graph.snapshot(), before)


class PlaybackTests(ScenarioTest):
    def test_all_eight_stages_write_only_at_execution_and_finish_once(self):
        session = PlaybackSession(self.harness, self.raw)
        self.assertEqual(session.snapshot()["step"], 0)
        statuses = ["OBSERVATIONS_RECEIVED", "PERCEIVED", "ONTOLOGY_VALIDATED", "PLANNED",
                    "PLAN_VALIDATED", "READY_TO_EXECUTE", "COMMITTED", "FEEDBACK"]
        for step, status in enumerate(statuses, 1):
            state = session.advance()
            self.assertEqual(state["step"], step)
            self.assertEqual(state["status"], status)
            if step < 7:
                self.assertEqual(self.harness.graph.document, self.initial)
        self.assertTrue(state["complete"])
        self.assertEqual(len(state["graph"]["commits"]), 1)
        self.assertEqual(session.advance()["step"], 8)
        self.assertEqual(len(self.harness.graph.document["commits"]), 1)
        self.assertTrue(self.harness.execution_agent.record_review("E-017", "confirmed", "checked")["eventRetained"])

    def test_ontology_rejection_stops_at_step_three(self):
        session = PlaybackSession(self.harness, self.raw, "ontology_conflict")
        for _ in range(3):
            state = session.advance()
        self.assertTrue(state["terminal"])
        self.assertEqual(state["status"], "REJECTED")
        self.assertEqual(session.advance()["step"], 3)
        self.assertEqual(self.harness.graph.document, self.initial)
        self.assertFalse((self.root / "ontology" / "01").exists())

    def test_replanning_revalidates_at_step_six(self):
        session = PlaybackSession(self.harness, self.raw, "plan_rejected")
        for _ in range(5):
            state = session.advance()
        self.assertEqual(state["status"], "PLAN_REJECTED")
        self.assertEqual(self.harness.graph.document, self.initial)
        repaired = session.advance()
        self.assertEqual(repaired["status"], "REPLANNED")
        self.assertTrue(repaired["report"]["planValidation"]["passed"])
        self.assertTrue(repaired["report"]["updatePlan"]["replanRepairs"])
        self.assertEqual(session.advance()["status"], "COMMITTED")
        self.assertTrue(session.advance()["complete"])

    def test_execution_failure_rolls_back_and_finishes(self):
        session = PlaybackSession(self.harness, self.raw, "execution_rollback")
        for _ in range(7):
            state = session.advance()
        self.assertEqual(state["status"], "EXECUTION_FAILED")
        self.assertTrue(state["report"]["executionResult"]["rolledBack"])
        self.assertEqual(state["graph"], self.initial)
        self.assertTrue(session.advance()["complete"])
        self.assertEqual(session.advance()["step"], 8)

    def test_invalid_mode_is_rejected_before_advancing(self):
        with self.assertRaises(ValueError):
            PlaybackSession(self.harness, self.raw, "unknown")


class AtomicIOTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_json_and_text_roundtrip_and_replace(self):
        path = self.root / "nested" / "result.json"
        write_json(path, {"中文": [1, 2]})
        self.assertEqual(read_json(path), {"中文": [1, 2]})
        write_json(path, {"new": True})
        self.assertEqual(read_json(path), {"new": True})
        write_text(path, "海上\n更新\n")
        self.assertEqual(path.read_bytes(), "海上\n更新\n".encode())
        self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_serialization_failure_preserves_existing_file(self):
        path = self.root / "result.json"
        write_json(path, {"old": True})
        before = path.read_bytes()
        with self.assertRaises(TypeError):
            write_json(path, {"notSerializable": object()})
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_replace_and_fsync_failure_preserve_file_and_cleanup(self):
        path = self.root / "result.txt"
        write_text(path, "old")
        for operation in ("os.replace", "os.fsync"):
            with self.subTest(operation=operation):
                with patch("mvp.io." + operation, side_effect=OSError("injected")):
                    with self.assertRaises(OSError):
                        write_text(path, "new")
                self.assertEqual(path.read_text(), "old")
                self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_concurrent_writers_use_unique_staging_files(self):
        path = self.root / "result.json"
        barrier = threading.Barrier(4)
        from mvp.io import os
        fsync = os.fsync
        def synchronized_flush(descriptor):
            fsync(descriptor)
            barrier.wait(timeout=10)
        values = [{"writer": i, "payload": str(i) * 10000} for i in range(4)]
        with patch("mvp.io.os.fsync", side_effect=synchronized_flush):
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda value: write_json(path, value), values))
        self.assertIn(read_json(path), values)
        self.assertEqual(list(self.root.glob("*.tmp")), [])


class CLITests(ScenarioTest):
    def test_unpublished_directories_are_not_exported(self):
        unpublished = self.root / "ontology" / "03"
        unpublished.mkdir()
        (unpublished / "ontology.json").write_text("{}", encoding="utf-8")
        destination = self.root / "export"
        export_ontology(self.root / "ontology", destination)
        self.assertTrue((destination / "00" / "ontology.json").is_file())
        self.assertFalse((destination / "03").exists())

    def test_incomplete_version_index_fails_before_export(self):
        from mvp.io import write_json
        write_json(self.root / "ontology" / "versions.json", {
            "latestVersion": "02", "versions": [{"versionId": "02", "path": "02/ontology.ttl"}],
        })
        destination = self.root / "export"
        with self.assertRaisesRegex(ValueError, "incomplete"):
            export_ontology(self.root / "ontology", destination)
        self.assertFalse(destination.exists())

    def test_all_modes_export_reports_and_only_published_versions(self):
        output = self.root / "reports"
        for mode, status, version in (("standard", "COMMITTED", "01"),
                                      ("ontology_conflict", "REJECTED", "00"),
                                      ("plan_rejected", "COMMITTED", "01"),
                                      ("execution_rollback", "EXECUTION_FAILED", "01")):
            with self.subTest(mode=mode), contextlib.redirect_stdout(io.StringIO()):
                report = run(ROOT / "data" / "observations_R001.json", output, mode)
                saved = read_json(output / "round_R-001.json")
                self.assertEqual(saved, report)
                self.assertEqual(saved["status"], status)
                self.assertEqual(saved["ontologyVersion"], version)
                index = read_json(output / "ontology" / "versions.json")
                self.assertEqual(index["latestVersion"], version)
                self.assertEqual((output / "ontology" / "01").exists(), version == "01")
                self.assertIn("incrementalWrites,", (output / "metrics.csv").read_text())
                if status != "COMMITTED":
                    self.assertEqual(read_json(output / "graph_after_R-001.json"), self.initial)


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        self.clear_session()

    def tearDown(self):
        self.clear_session()

    def clear_session(self):
        with web_server.LOCK:
            if web_server.CURRENT["temp"]:
                web_server.CURRENT["temp"].cleanup()
            web_server.CURRENT.update(harness=None, report=None, temp=None)

    def request(self, method, path, body=None):
        conn = HTTPConnection(*self.server.server_address, timeout=30)
        self.addCleanup(conn.close)
        data = body if isinstance(body, str) else json.dumps(body) if body is not None else None
        conn.request(method, path, data, {"Content-Type": "application/json"})
        response = conn.getresponse()
        return response.status, json.loads(response.read())

    def test_health_reports_jar_presence_without_starting_java(self):
        with patch("mvp.ontology.subprocess.run", side_effect=AssertionError("Java must not run")):
            status, body = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["reasoner"], "HermiT")

    def test_run_all_modes_returns_real_reports(self):
        for mode, expected in (("standard", "COMMITTED"), ("ontology_conflict", "REJECTED"),
                               ("plan_rejected", "COMMITTED"), ("execution_rollback", "EXECUTION_FAILED")):
            with self.subTest(mode=mode):
                status, body = self.request("POST", "/api/run", {"mode": mode})
                self.assertEqual(status, 200)
                self.assertEqual(body["report"]["status"], expected)
                self.assertEqual(body["mode"], mode)
                if expected != "COMMITTED":
                    self.assertEqual(body["graph"], body["initialGraph"])

    def test_review_before_run_is_conflict(self):
        self.assertEqual(self.request("POST", "/api/review", {})[0], 409)

    def test_text_input_is_default_and_can_be_disabled(self):
        status, payload = self.request("POST", "/api/run", {})
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["graph"]["nodes"]["E-017"]["evidence"]), 11)
        self.assertIn("R4", payload["report"]["feedback"]["ruleHits"])
        status, payload = self.request("POST", "/api/run", {"includeText": False})
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["graph"]["nodes"]["E-017"]["evidence"]), 6)
        self.assertNotIn("R4", payload["report"]["feedback"]["ruleHits"])
        self.assertEqual(self.request("POST", "/api/run", {"includeText": "false"})[0], 400)

    def test_review_confirm_reject_and_invalid_inputs(self):
        self.assertEqual(self.request("POST", "/api/run", {})[0], 200)
        for status in ("confirmed", "rejected"):
            code, body = self.request("POST", "/api/review", {"eventId": "E-017", "status": status, "reason": "人工复核"})
            self.assertEqual(code, 200)
            self.assertTrue(body["review"]["eventRetained"])
            self.assertEqual(body["graph"]["nodes"]["E-017"]["properties"]["reviewStatus"], status)
        for payload in ({"status": "invalid"}, {"eventId": "S-017-0"}, {"eventId": "missing"}, {"reason": []}):
            self.assertEqual(self.request("POST", "/api/review", payload)[0], 400)

    def test_invalid_run_requests_are_client_errors(self):
        for body in ("{", "[]", "null", {"mode": "unknown"}, {"mode": []}):
            with self.subTest(body=body):
                self.assertEqual(self.request("POST", "/api/run", body)[0], 400)
        self.assertIsNone(web_server.CURRENT["harness"])

    def test_failed_run_keeps_previous_session_and_cleans_new_workspace(self):
        self.request("POST", "/api/run", {})
        previous = dict(web_server.CURRENT)
        created = []
        factory = tempfile.TemporaryDirectory
        def temporary_directory(*args, **kwargs):
            temporary = factory(*args, **kwargs)
            created.append(Path(temporary.name))
            return temporary
        with patch("mvp.web_server.tempfile.TemporaryDirectory", side_effect=temporary_directory), \
             patch.object(Harness, "run_round", side_effect=RuntimeError("injected failure")):
            status, _ = self.request("POST", "/api/run", {})
        self.assertEqual(status, 500)
        self.assertEqual(web_server.CURRENT, previous)
        self.assertTrue(Path(previous["temp"].name).exists())
        self.assertTrue(all(not path.exists() for path in created))


if __name__ == "__main__":
    unittest.main()
