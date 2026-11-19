"""Real Fuseki HTTP / MCP / TDB2 integration, using an isolated dataset and port."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.request import urlopen, Request
from urllib.error import HTTPError

from mvp.graph import ToolExecutionError
from mvp.harness import Harness
from mvp.io import read_json
from mvp.jena import FusekiBridge, JenaGraphStore, ROOT
from mvp.mcp_client import MCPClientToolbox


@unittest.skipUnless((ROOT / "jena/target/jena-store.jar").exists(), "build jena/build.ps1 first")
class FusekiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="maritime_fuseki_")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.path = cls.root / "tdb2"
        cls.initial = read_json(ROOT / "data/initial_graph.json")
        shutil.copytree(ROOT / "ontology/00", cls.root / "ontology/00")
        harness = Harness(cls.root, cls.initial)
        cls.report = harness.run_round(read_json(ROOT / "data/observations_disguised.json"))
        cls.expected = harness.graph.snapshot()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        cls.url = f"http://localhost:{port}"
        cls.log = tempfile.TemporaryFile()
        cls.addClassCleanup(cls.log.close)
        java = os.environ.get("JAVA") or (str(Path(os.environ["JAVA_HOME"]) / "bin/java.exe") if os.environ.get("JAVA_HOME") else "java")
        cls.process = subprocess.Popen([java, "-cp", str(ROOT / "jena/target/jena-store.jar"),
                                       "org.example.maritime.FusekiLauncher", str(cls.path), str(port), str(ROOT / "jena/web")],
                                      stdout=cls.log, stderr=subprocess.STDOUT)
        def stop():
            cls.process.terminate()
            cls.process.wait(timeout=15)
        cls.addClassCleanup(stop)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                with urlopen(cls.url + "/api/snapshot", timeout=1):
                    break
            except OSError:
                if cls.process.poll() is not None:
                    cls.log.seek(0)
                    raise RuntimeError(cls.log.read().decode("utf-8", "replace"))
                time.sleep(0.1)
        else:
            raise RuntimeError("Fuseki startup timed out")
        with urlopen(cls.url + "/api/snapshot") as response:
            initialized = json.load(response)["document"]
        if initialized != cls.initial:
            raise AssertionError("Fresh Fuseki must expose the initial graph before any application run")
        with urlopen(cls.url + "/api/ontology/") as response:
            if json.load(response)["versions"] != ["00"]:
                raise AssertionError("Fresh Fuseki must expose ontology 00 before any update")
        with urlopen(cls.url + "/api/ontology/00") as response:
            if json.load(response) != read_json(ROOT / "ontology/00/ontology.json"):
                raise AssertionError("Initial ontology must match the project's real baseline")

    def setUp(self):
        self.env = patch.dict(os.environ, {"MARITIME_FUSEKI_URL": self.url})
        self.env.start()
        self.addCleanup(self.env.stop)
        bridge = FusekiBridge(self.path, self.url)
        bridge.call("begin")
        bridge.call("commit", document=self.initial)
        self.graph = JenaGraphStore(self.initial, self.path)
        self.client = MCPClientToolbox(self.graph)

    def test_mcp_writes_visible_through_official_sparql_endpoint(self):
        result = self.client.execute_plan(self.report["updatePlan"])
        self.assertTrue(result["success"])
        self.assertEqual(self.graph.snapshot(), self.expected)
        query = 'SELECT ?s ?p ?o WHERE { GRAPH <urn:maritime:data> { ?s ?p ?o } }'
        request = Request(self.url + "/maritime/query", query.encode(),
                          headers={"Content-Type": "application/sparql-query", "Accept": "application/sparql-results+json"})
        with urlopen(request) as response:
            rows = json.load(response)["results"]["bindings"]
        self.assertGreater(len(rows), 200)
        self.assertTrue(any(r["s"]["value"].endswith("#S-017-1") for r in rows))
        self.assertEqual(self.graph.query_state("V-017"), ["S-017-1"])
        self.assertTrue(self.client.execute_plan(self.report["updatePlan"])["idempotentReplay"])

    def test_native_rollback_after_rdf_writes(self):
        with FusekiBridge(self.path, self.url) as bridge:
            bridge.call("begin")
            with self.assertRaisesRegex(ToolExecutionError, "before native commit"):
                bridge.call("commit", document=self.expected, failBeforeCommit=True)
        self.graph.refresh()
        self.assertEqual(self.graph.snapshot(), self.initial)
        self.assertEqual(self.graph.query_state("V-017"), ["S-017-0"])

    def test_execution_failure_rolls_back_over_http(self):
        result = self.client.execute_plan(self.report["updatePlan"], 8)
        self.assertTrue(result["rolledBack"])
        self.assertEqual(self.graph.snapshot(), self.initial)

    def test_stale_writer_is_rejected_without_lost_update(self):
        with FusekiBridge(self.path, self.url) as first, FusekiBridge(self.path, self.url) as second:
            first.call("begin")
            second.call("begin")
            changed = deepcopy(self.initial)
            changed["feedback"] = [{"reason": "first writer"}]
            first.call("commit", document=changed)
            with self.assertRaisesRegex(ToolExecutionError, "409.*snapshot conflict"):
                second.call("commit", document=self.expected)
        self.graph.refresh()
        self.assertEqual(self.graph.snapshot(), changed)

    def test_dataset_path_mismatch_is_explicit(self):
        with self.assertRaisesRegex(ToolExecutionError, "directory differs"):
            JenaGraphStore(self.initial, self.root / "another-dataset")

    def test_query_console_and_read_only_sparql_routes(self):
        with urlopen(self.url + "/") as response:
            html = response.read().decode("utf-8")
        self.assertIn("SPARQL 查询", html)
        self.assertIn("GRAPH <urn:maritime:data>", html)
        self.assertIn("重新加载全图", html)
        request = Request(self.url + "/maritime/update", b"DROP ALL", headers={"Content-Type": "application/sparql-update"})
        with self.assertRaises(HTTPError) as error:
            urlopen(request)
        self.assertIn(error.exception.code, (400, 404, 405))
        self.graph.refresh()
        self.assertEqual(self.graph.snapshot(), self.initial)

    def test_ontology_versions_follow_actual_complete_directories(self):
        ontology = self.path / "ontology"
        ontology.mkdir(exist_ok=True)
        try:
            for version in ("00", "02", "10"):
                directory = ontology / version
                directory.mkdir(exist_ok=True)
                (directory / "ontology.json").write_text(json.dumps({"versionId": version}), encoding="utf-8")
            (ontology / "03").mkdir()  # Incomplete directory is not a loadable version.
            (ontology / "versions.json").write_text('{"latestVersion":"99"}', encoding="utf-8")
            with urlopen(self.url + "/api/ontology/") as response:
                self.assertEqual(json.load(response)["versions"], ["00", "02", "10"])
            with urlopen(self.url + "/api/ontology/02") as response:
                self.assertEqual(json.load(response)["versionId"], "02")
            (ontology / "02/ontology.json").unlink()
            with urlopen(self.url + "/api/ontology/") as response:
                self.assertEqual(json.load(response)["versions"], ["00", "10"])
            with urlopen(self.url + "/ontology_viewer.html") as response:
                self.assertIn("refreshVersions", response.read().decode("utf-8"))
        finally:
            shutil.rmtree(ontology)


if __name__ == "__main__":
    unittest.main()
