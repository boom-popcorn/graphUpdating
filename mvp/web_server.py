from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from typing import Any

from .graph import ActionExecutionAgent, GraphStore
from .harness import Harness
from .io import read_json


ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "web"
LOCK = threading.RLock()
CURRENT: dict[str, Any] = {"harness": None, "report": None, "temp": None}


def _run(mode: str = "standard") -> dict[str, Any]:
    with LOCK:
        old_temp = CURRENT.get("temp")
        if old_temp:
            old_temp.cleanup()
        temp = tempfile.TemporaryDirectory(prefix="maritime_demo_ui_")
        working = Path(temp.name)
        (working / "ontology").mkdir()
        shutil.copytree(ROOT / "ontology" / "00", working / "ontology" / "00")
        graph = read_json(ROOT / "data" / "initial_graph.json")
        harness = Harness(working, graph)
        scenario = read_json(ROOT / "data" / "observations_R001.json")
        report = harness.run_round(scenario, demo_mode=mode)
        CURRENT["harness"] = harness
        CURRENT["report"] = report
        CURRENT["temp"] = temp
        return {
            "report": report,
            "graph": harness.graph.document,
            "initialGraph": graph,
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
                body = self.rfile.read(int(self.headers.get("Content-Length", "0") or "0"))
                mode = json.loads(body or b"{}").get("mode", "standard")
                if mode not in {"standard", "ontology_conflict", "plan_rejected", "execution_rollback"}:
                    self._json(400, {"error": "unsupported demo mode"})
                    return
                self._json(200, _run(mode))
                return
            if path == "/api/review":
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0") or "0")) or b"{}")
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
        except Exception as exc:
            self._json(500, {"error": str(exc), "type": type(exc).__name__})


def main() -> None:
    port = int(os.environ.get("MARITIME_DEMO_PORT", "8765"))
    address = ("127.0.0.1", port)
    print(f"Maritime knowledge graph demo: http://{address[0]}:{address[1]}")
    ThreadingHTTPServer(address, Handler).serve_forever()


if __name__ == "__main__":
    main()

