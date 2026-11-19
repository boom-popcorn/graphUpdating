"""Embedded Jena TDB2 adapter; native write transactions cover read/check/write."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import tempfile
from threading import Timer
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from .graph import GraphStore, ToolExecutionError

ROOT = Path(__file__).resolve().parents[1]
NS = "https://example.org/maritime#"


class JenaBridge:
    def __init__(self, path):
        jar = ROOT / "jena" / "target" / "jena-store.jar"
        if not jar.is_file():
            raise RuntimeError("Jena JAR missing; run jena/build.ps1")
        java = os.environ.get("JAVA") or (str(Path(os.environ["JAVA_HOME"]) / "bin" / "java.exe")
                                          if os.environ.get("JAVA_HOME") else "java")
        self.errors = tempfile.TemporaryFile()
        self.process = subprocess.Popen([java, "-jar", str(jar), str(Path(path).resolve())],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.errors,
                                        text=True, encoding="utf-8")

    def call(self, op, **arguments):
        timer = Timer(90, self.process.kill)
        timer.daemon = True
        timer.start()
        try:
            self.process.stdin.write(json.dumps({"op": op, **arguments}, ensure_ascii=False) + "\n")
            self.process.stdin.flush()
            line = self.process.stdout.readline()
        finally:
            timer.cancel()
        if not line:
            self.errors.seek(0)
            raise ToolExecutionError("Jena bridge exited: " + self.errors.read().decode("utf-8", "replace")[-3000:])
        result = json.loads(line)
        if not result["ok"]:
            raise ToolExecutionError(result["error"])
        return result

    def close(self):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.process.stdout.close()
        self.errors.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class JenaGraphStore(GraphStore):
    def __init__(self, document, dataset_dir):
        super().__init__(document)
        self.dataset_dir = Path(dataset_dir).resolve()
        self.fuseki_url = os.environ.get("MARITIME_FUSEKI_URL", "").rstrip("/")
        with self._lock, self._bridge() as bridge:
            bridge.call("begin")
            existing = bridge.call("read")["document"]
            if existing is None:
                bridge.call("commit", document=self.document)
            else:
                bridge.call("abort")
                self.document = existing

    def _bridge(self):
        return FusekiBridge(self.dataset_dir, self.fuseki_url) if self.fuseki_url else JenaBridge(self.dataset_dir)

    def refresh(self):
        with self._lock, self._bridge() as bridge:
            self.document = bridge.call("read")["document"]

    @contextmanager
    def transaction(self):
        with self._lock, self._bridge() as bridge:
            bridge.call("begin")
            self.document = bridge.call("read")["document"]
            original = deepcopy(self.document)
            try:
                yield
                if self.document != original:
                    bridge.call("commit", document=self.document)
                else:
                    bridge.call("abort")
            except BaseException:
                self.document = original
                bridge.call("abort")
                raise

    def select(self, query):
        with self._lock, self._bridge() as bridge:
            return bridge.call("select", query=query)["result"]

    def query_state(self, vessel_id):
        iri = NS + quote(vessel_id, safe="")
        result = self.select(f'''SELECT ?stateId WHERE {{ GRAPH <urn:maritime:data> {{
            <{iri}> <{NS}hasState> ?state . ?state <{NS}id> ?stateId .
            FILTER NOT EXISTS {{ ?state <{NS}validTo> ?end }}
        }} }} ORDER BY ?stateId''')
        return [row["stateId"]["value"] for row in result["results"]["bindings"]]

    def get_subgraph(self, entity_id):
        iri = NS + quote(entity_id, safe="")
        result = self.select(f'''SELECT ?id WHERE {{ GRAPH <urn:maritime:data> {{
            {{ BIND(<{iri}> AS ?node) }} UNION {{ <{iri}> ?p ?node }} UNION {{ ?node ?p <{iri}> }}
            ?node <{NS}id> ?id
        }} }}''')
        ids = {row["id"]["value"] for row in result["results"]["bindings"]}
        self.refresh()
        nodes = {key: value for key, value in self.document["nodes"].items() if key in ids}
        evidence_ids = {e for node in nodes.values() for e in node.get("evidence", [])}
        return {"nodes": nodes,
                "relations": [r for r in self.document["relations"] if r["s"] in ids and r["o"] in ids],
                "evidence": {key: value for key, value in self.document.get("evidence", {}).items() if key in evidence_ids},
                "provenance": [p for p in self.document.get("provenance", []) if p["event"] in ids]}


class FusekiBridge:
    """HTTP adapter with optimistic concurrency checked inside the server's native transaction."""
    def __init__(self, path, url):
        self.path, self.url = Path(path).resolve(), url
        self.active = False
        self.original = None

    def request(self, endpoint, payload=None, content_type="application/json"):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None and content_type == "application/json" else payload
        request = Request(self.url + endpoint, data=data, headers={"Content-Type": content_type,
                          "Accept": "application/sparql-results+json, application/json"})
        try:
            with urlopen(request, timeout=90) as response:
                return json.load(response)
        except HTTPError as exc:
            raise ToolExecutionError(f"Fuseki HTTP {exc.code}: " + exc.read().decode("utf-8", "replace")[:2000]) from exc
        except OSError as exc:
            raise ToolExecutionError(f"Cannot reach Fuseki at {self.url}: {exc}") from exc

    def read(self):
        result = self.request("/api/snapshot")
        if os.path.normcase(str(Path(result["datasetDir"]).resolve())) != os.path.normcase(str(self.path)):
            raise ToolExecutionError("Fuseki dataset directory differs from --dataset-dir; use the same path")
        return result

    def call(self, op, **arguments):
        if op == "begin":
            self.original = self.read()["document"]
            self.active = True
            return {"ok": True}
        if op == "read":
            return {"ok": True, "document": deepcopy(self.original)} if self.active else self.read()
        if op == "abort":
            self.active = False
            return {"ok": True}
        if op == "commit":
            if not self.active:
                raise ToolExecutionError("begin required")
            result = self.request("/api/snapshot", {"expected": self.original, **arguments})
            self.active = False
            return result
        if op == "select":
            return {"ok": True, "result": self.request("/maritime/query", arguments["query"].encode("utf-8"), "application/sparql-query")}
        raise ToolExecutionError(f"unknown Fuseki operation {op}")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.active = False
