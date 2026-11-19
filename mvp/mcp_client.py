"""MCP subprocess client; no direct import/call of the server's tool implementation."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Timer

from .graph import ToolExecutionError


class MCPClientToolbox:
    def __init__(self, graph):
        self.graph = graph
        self.audit = []

    def call(self, name, arguments):
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "maritime-executor", "version": "1.0.0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": name, "arguments": arguments}},
        ]
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen([sys.executable, "-m", "mvp.mcp_server", "--dataset-dir", str(self.graph.dataset_dir)],
                                       cwd=str(Path(__file__).resolve().parents[1]), stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=errors, text=True, encoding="utf-8",
                                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            timer = Timer(120, process.kill)
            timer.daemon = True
            timer.start()
            def send(message):
                process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
                process.stdin.flush()
            def receive(expected_id):
                while True:
                    line = process.stdout.readline()
                    if not line:
                        errors.seek(0)
                        raise ToolExecutionError("MCP server exited: " + errors.read().decode("utf-8", "replace")[-3000:])
                    response = json.loads(line)
                    if response.get("id") == expected_id:
                        return response
            try:
                send(messages[0])
                init = receive(1)
                if init.get("result", {}).get("protocolVersion") != "2024-11-05":
                    raise ToolExecutionError("MCP initialization failed")
                send(messages[1])
                send(messages[2])
                response = receive(2)
            finally:
                process.stdin.close()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                timer.cancel()
                process.stdout.close()
        if "error" in response:
            raise ToolExecutionError(response["error"]["message"])
        payload = response.get("result", {})
        if payload.get("isError") or not payload.get("content"):
            raise ToolExecutionError(str(payload))
        return json.loads(payload["content"][0]["text"])

    def execute_plan(self, plan, fail_after_operations=None):
        self.audit = []
        try:
            result = self.call("executePlan", {"plan": plan, "failAfterOperations": fail_after_operations})
            self.audit = result["audit"]
            return result["execution"]
        finally:
            self.graph.refresh()

    def record_review(self, event_id, status, reason, ontology):
        if ontology is None:
            raise ValueError("an ontology model is required for event review")
        try:
            return self.call("recordReview", {"eventId": event_id, "status": status, "reason": reason,
                                               "ontology": ontology.document})
        finally:
            self.graph.refresh()
