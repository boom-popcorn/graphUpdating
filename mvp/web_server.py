from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from typing import Any

from .harness import Harness
from .io import read_json, with_text_input


ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "web"
LOCK = threading.RLock()
CURRENT: dict[str, Any] = {"harness": None, "report": None, "temp": None}


def _run(mode: str = "standard", include_text: bool = True, scenario_name: str = "narrative") -> dict[str, Any]:
    with LOCK:
        temp = tempfile.TemporaryDirectory(prefix="maritime_demo_ui_")
        try:
            working = Path(temp.name)
            (working / "ontology").mkdir()
            shutil.copytree(ROOT / "ontology" / "00", working / "ontology" / "00")
            graph = read_json(ROOT / "data" / "initial_graph.json")
            dataset = os.environ.get("MARITIME_DATASET_DIR")
            dataset_dir = Path(dataset).resolve() if dataset else None
            if dataset_dir is not None and (dataset_dir / "ontology" / "00").is_dir():
                from run_demo import export_ontology
                export_ontology(dataset_dir / "ontology", working / "ontology")
            harness = Harness(working, graph, dataset_dir=dataset_dir)
            initial_graph = harness.graph.snapshot()
            scenario = read_json(ROOT / "data" / "observations_R001.json")
            if scenario_name not in {"narrative", "disguised", "insufficient"}:
                raise ValueError("unsupported scenario")
            if scenario_name != "narrative":
                scenario = read_json(ROOT / "data" / f"observations_{scenario_name}.json")
                if not include_text:
                    scenario.pop("texts", None)
            elif include_text:
                scenario = with_text_input(scenario, ROOT / "data" / "texts_R001.json")
            report = harness.run_round(scenario, demo_mode=mode)
            report["storage"] = {"backend": "jena-tdb2" if dataset_dir else "memory",
                                 "transport": "mcp-stdio" if dataset_dir else "local",
                                 "fusekiUrl": getattr(harness.graph, "fuseki_url", None) or None}
            if dataset_dir is not None:
                from run_demo import export_ontology
                export_ontology(working / "ontology", dataset_dir / "ontology")
        except Exception:
            temp.cleanup()
            raise
        old_temp = CURRENT.get("temp")
        CURRENT["harness"] = harness
        CURRENT["report"] = report
        CURRENT["temp"] = temp
        if old_temp:
            old_temp.cleanup()
        return {
            "report": report,
            "graph": harness.graph.snapshot(),
            "initialGraph": initial_graph,
            "mode": mode,
        }


class Handler(BaseHTTPRequestHandler):
    server_version = "MaritimeKnowledgeDemo/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        print("[demo] " + format % args)

    def _send(self, status: int, payload: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, status: int, value: Any) -> None:
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send(status, payload, "application/json; charset=utf-8")

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in {"/", "/index.html"}:
            page = (WEB_ROOT / "index.html").read_bytes()
            self._send(200, page, "text/html; charset=utf-8")
            return
        if path == "/api/health":
            jar = ROOT / "hermit" / "target" / "hermit-validator.jar"
            self._json(200, {"ok": jar.is_file(), "reasoner": "HermiT" if jar.is_file() else None})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/api/run":
                body = self._read_body()
                mode = body.get("mode", "standard")
                include_text = body.get("includeText", True)
                if not isinstance(include_text, bool):
                    raise ValueError("includeText must be a boolean")
                if not isinstance(mode, str):
                    raise ValueError("mode must be a string")
                if mode not in {"standard", "ontology_conflict", "plan_rejected", "execution_rollback"}:
                    self._json(400, {"error": "unsupported demo mode"})
                    return
                scenario_name = body.get("scenario", "narrative")
                if scenario_name not in ("narrative", "disguised", "insufficient"):
                    raise ValueError("unsupported scenario")
                self._json(200, _run(mode, include_text, scenario_name))
                return
            if path == "/api/ontology-review":
                body = self._read_body()
                with LOCK:
                    harness = CURRENT.get("harness")
                    if harness is None:
                        self._json(409, {"error": "run a case before ontology review"})
                        return
                    result = harness.ontology_agent.record_expert_review(
                        body.get("version"), body.get("status"), body.get("reviewer"), body.get("reason"))
                    CURRENT["report"]["ontologyValidation"]["expertReview"] = result
                    if hasattr(harness.graph, "dataset_dir"):
                        from run_demo import export_ontology
                        export_ontology(harness.root / "ontology", harness.graph.dataset_dir / "ontology")
                    self._json(200, result)
                return
            if path == "/api/review":
                body = self._read_body()
                for field in ("eventId", "status", "reason"):
                    if field in body and not isinstance(body[field], str):
                        raise ValueError(f"{field} must be a string")
                with LOCK:
                    harness = CURRENT.get("harness")
                    if harness is None:
                        self._json(409, {"error": "run the case before reviewing its event"})
                        return
                    result = harness.execution_agent.record_review(
                        body.get("eventId", "E-017"),
                        body.get("status", "confirmed"),
                        body.get("reason", "reviewed in presentation demo"),
                    )
                    self._json(200, {"review": result, "graph": harness.graph.document})
                return
            self._json(404, {"error": "not found"})
        except (ValueError, UnicodeDecodeError) as exc:
            self._json(400, {"error": str(exc), "type": type(exc).__name__})
        except Exception as exc:
            traceback.print_exc()
            self._json(500, {"error": str(exc), "type": type(exc).__name__})

    def _read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if not 0 <= length <= 1024 * 1024:
            raise ValueError("request body must be at most 1 MiB")
        body = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(body, dict):
            raise ValueError("request body must be a JSON object")
        return body


def main() -> None:
    port = int(os.environ.get("MARITIME_DEMO_PORT", "8765"))
    address = ("127.0.0.1", port)
    print(f"Maritime knowledge graph demo: http://{address[0]}:{address[1]}")
    ThreadingHTTPServer(address, Handler).serve_forever()


if __name__ == "__main__":
    main()

