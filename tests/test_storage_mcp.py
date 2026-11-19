"""Real Jena/MCP acceptance tests, not a mocked storage substitute."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from unittest.mock import patch

from mvp.graph import ActionExecutionAgent, GraphStore, MCPToolbox
from mvp.graph import ToolExecutionError
from mvp.harness import Harness
from mvp.io import read_json
from mvp.jena import JenaBridge, JenaGraphStore, NS
from mvp.mcp_client import MCPClientToolbox

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless((ROOT / "jena/target/jena-store.jar").is_file(), "build jena/build.ps1 first")
class StorageMCPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="jena_acceptance_")
        cls.root = Path(cls.temp.name)
        shutil.copytree(ROOT / "ontology/00", cls.root / "ontology/00")
        cls.initial = read_json(ROOT / "data/initial_graph.json")
        harness = Harness(cls.root, cls.initial)
        cls.report = harness.run_round(read_json(ROOT / "data/observations_disguised.json"))
        cls.plan = cls.report["updatePlan"]
        cls.expected = harness.graph.snapshot()
        cls.ontology = harness.execution_agent.ontology

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.temp_case = tempfile.TemporaryDirectory(dir=self.root)
        self.addCleanup(self.temp_case.cleanup)
        self.dataset = Path(self.temp_case.name) / "tdb2"
        self.graph = JenaGraphStore(self.initial, self.dataset)
        self.client = MCPClientToolbox(self.graph)

    def test_real_mcp_execution_matches_memory_and_queries_rdf(self):
        result = self.client.execute_plan(self.plan)
        self.assertTrue(result["success"])
        self.assertEqual(self.graph.snapshot(), self.expected)
        self.assertEqual(self.client.call("queryState", {"vesselId": "V-017"})["states"], ["S-017-1"])
        subgraph = self.client.call("getSubgraph", {"entityId": "V-017"})
        self.assertIn("S-017-1", subgraph["nodes"])
        evidence = self.client.call("getSubgraph", {"entityId": "E-017"})
        self.assertTrue(evidence["evidence"])
        self.assertTrue(evidence["provenance"])
        rows = self.graph.select('SELECT ?e WHERE { GRAPH <urn:maritime:data> { '
                                 f'<{NS}E-017> <http://www.w3.org/ns/prov#used> ?e' + ' } }')
        self.assertEqual(len(rows["results"]["bindings"]), len(self.expected["nodes"]["E-017"]["evidence"]))

    def test_restart_replay_does_not_write_second_commit(self):
        result = self.client.execute_plan(self.plan)
        reopened = JenaGraphStore({"nodes": {}, "relations": []}, self.dataset)
        client = MCPClientToolbox(reopened)
        replay = client.execute_plan(self.plan)
        self.assertTrue(replay["idempotentReplay"])
        self.assertEqual(replay["commitId"], result["commitId"])
        self.assertEqual(client.audit, [])
        self.assertEqual(reopened.snapshot(), self.expected)
        rows = reopened.select('SELECT ?c WHERE { GRAPH <urn:maritime:commits> { ?c '
                               '<http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <http://www.w3.org/ns/prov#Activity> } }')
        self.assertEqual(len(rows["results"]["bindings"]), 1)

    def test_failure_rolls_back_nodes_evidence_commits_and_idempotency(self):
        for fail_after in (0, 8, len(self.report["mcpToolAudit"]) - 1):
            result = self.client.execute_plan(self.plan, fail_after)
            self.assertFalse(result["success"])
            self.assertTrue(result["rolledBack"])
            reopened = JenaGraphStore(self.initial, self.dataset)
            self.assertEqual(reopened.snapshot(), self.initial)
            self.assertEqual(reopened.query_state("V-017"), ["S-017-0"])
        self.assertTrue(self.client.execute_plan(self.plan)["success"])

    def test_process_disconnect_aborts_native_transaction(self):
        with JenaBridge(self.dataset) as bridge:
            bridge.call("begin")
            bridge.call("read")
            bridge.process.kill()
            bridge.process.wait()
        self.assertEqual(JenaGraphStore(self.initial, self.dataset).snapshot(), self.initial)

    def test_same_plan_id_with_changed_content_is_rejected(self):
        self.client.execute_plan(self.plan)
        changed = deepcopy(self.plan)
        changed["closeStates"][0]["validTo"] = "2027-01-01T00:00:00+08:00"
        with self.assertRaises(ToolExecutionError):
            self.client.execute_plan(changed)
        self.assertEqual(self.graph.snapshot(), self.expected)

    def test_failed_native_commit_restores_python_snapshot_and_disk(self):
        original = JenaBridge.call
        def fail_commit(bridge, op, **kwargs):
            if op == "commit":
                return original(bridge, op, **kwargs, failBeforeCommit=True)
            return original(bridge, op, **kwargs)
        with patch.object(JenaBridge, "call", fail_commit):
            with self.assertRaisesRegex(ToolExecutionError, "before native commit"):
                MCPToolbox(self.graph).execute_plan(self.plan)
        self.assertEqual(self.graph.snapshot(), self.initial)
        self.assertEqual(JenaGraphStore(self.initial, self.dataset).snapshot(), self.initial)
        self.assertEqual(self.graph.query_state("V-017"), ["S-017-0"])
        rows = self.graph.select('SELECT ?e WHERE { GRAPH <urn:maritime:data> { '
                                 f'<{NS}E-017> <http://www.w3.org/ns/prov#used> ?e' + ' } }')
        self.assertEqual(rows["results"]["bindings"], [])

    def test_review_persists_across_restart_with_feedback_history(self):
        self.client.execute_plan(self.plan)
        agent = ActionExecutionAgent(self.graph, self.ontology, toolbox=self.client)
        for status in ("confirmed", "rejected"):
            agent.record_review("E-017", status, "人工复核")
        reopened = JenaGraphStore(self.initial, self.dataset)
        self.assertEqual(reopened.document["nodes"]["E-017"]["properties"]["reviewStatus"], "rejected")
        self.assertEqual(len(reopened.document["feedback"]), 2)

    def test_stdio_discovery_schemas_and_protocol_errors(self):
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "executePlan", "arguments": {"plan": {}}}},
            {"jsonrpc": "2.0", "id": 4, "method": "missing"},
        ]
        proc = subprocess.run([sys.executable, "-m", "mvp.mcp_server", "--dataset-dir", str(self.dataset)],
                              input="\n".join(json.dumps(r) for r in requests) + "\n", text=True,
                              encoding="utf-8", capture_output=True, cwd=ROOT, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        responses = [json.loads(line) for line in proc.stdout.splitlines()]
        self.assertEqual([r["id"] for r in responses], [1, 2, 3, 4])
        self.assertEqual({t["name"] for t in responses[1]["result"]["tools"]},
                         {"executePlan", "queryState", "getSubgraph", "recordReview"})
        self.assertEqual(responses[2]["error"]["code"], -32602)
        self.assertEqual(responses[3]["error"]["code"], -32601)

    def test_harness_scenarios_and_failure_modes_use_mcp(self):
        for index, (filename, mode, status) in enumerate((
            ("observations_R001.json", "standard", "COMMITTED"),
            ("observations_insufficient.json", "standard", "COMMITTED"),
            ("observations_disguised.json", "ontology_conflict", "REJECTED"),
            ("observations_disguised.json", "plan_rejected", "COMMITTED"),
            ("observations_disguised.json", "execution_rollback", "EXECUTION_FAILED"),
        )):
            with self.subTest(filename=filename, mode=mode):
                root = Path(self.temp_case.name) / str(index)
                shutil.copytree(ROOT / "ontology/00", root / "ontology/00")
                harness = Harness(root, self.initial, dataset_dir=root / "tdb2")
                report = harness.run_round(read_json(ROOT / "data" / filename), mode)
                self.assertEqual(report["status"], status)
                reopened = JenaGraphStore(self.initial, root / "tdb2")
                self.assertEqual(reopened.snapshot(), harness.graph.snapshot())
                if status != "COMMITTED":
                    self.assertEqual(reopened.snapshot(), self.initial)
                else:
                    self.assertIsInstance(harness.execution_agent.toolbox, MCPClientToolbox)
                    self.assertEqual(reopened.query_state("V-017"), ["S-017-1"])


if __name__ == "__main__":
    unittest.main()
