"""MCP stdio server (JSON-RPC 2.0, newline-delimited UTF-8, protocol 2024-11-05).

Writes are submitted as one executePlan tool call, so client disconnects cannot
leave an open transaction. Atomic actions remain in the returned tool audit.
"""
from __future__ import annotations

import argparse
import json
import sys

from .graph import ActionExecutionAgent, MCPToolbox
from .io import read_json
from .jena import JenaGraphStore, ROOT
from .ontology import OntologyModel


def schema(properties, required):
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


STRING = {"type": "string", "minLength": 1}
PLAN = schema({
    "planId": STRING,
    **{key: {"type": "array", "items": {"type": "object"}} for key in (
        "createIndividuals", "assertTypes", "assertRelations", "closeStates", "temporalValues",
        "evidenceRecords", "attachEvidence", "textFacts")},
    "expectedSubgraph": {"type": "array", "items": {"type": "string"}},
}, ["planId", "createIndividuals", "assertTypes", "assertRelations", "closeStates", "expectedSubgraph"])
# Planners include diagnostic fields; keep their payload intact.
PLAN["additionalProperties"] = True
for key, required in {
    "createIndividuals": ["id", "type"], "assertTypes": ["id", "type"],
    "assertRelations": ["s", "p", "o"], "closeStates": ["id", "validTo"],
    "temporalValues": ["id", "timestamp"], "evidenceRecords": ["id"],
    "attachEvidence": ["event", "evidence"], "textFacts": ["predicate", "span", "quote", "evidence"],
}.items():
    PLAN["properties"][key]["items"] = {"type": "object", "required": required,
                                         "properties": {field: STRING for field in required if field != "span"}}
SCHEMAS = {
    "executePlan": schema({"plan": PLAN, "failAfterOperations": {"type": ["integer", "null"], "minimum": 0}}, ["plan"]),
    "queryState": schema({"vesselId": STRING}, ["vesselId"]),
    "getSubgraph": schema({"entityId": STRING}, ["entityId"]),
    "recordReview": schema({"eventId": STRING, "status": {"type": "string", "enum": ["confirmed", "rejected"]},
                            "reason": {"type": "string"}, "ontology": {"type": "object"}},
                           ["eventId", "status", "reason", "ontology"]),
}
DESCRIPTIONS = {
    "executePlan": "Atomically execute a validated plan: register evidence/instants, close state, create individuals, assert types/relations and attach evidence. Durable idempotency and audit included.",
    "queryState": "Query current open states of a vessel using SPARQL.",
    "getSubgraph": "Read a one-hop subgraph and its evidence/provenance using SPARQL.",
    "recordReview": "Persist human review and append feedback atomically; retain the event.",
}


def validate(value, spec):
    types = {"object": dict, "array": list, "string": str, "integer": int, "null": type(None)}
    expected = spec.get("type")
    if expected:
        choices = expected if isinstance(expected, list) else [expected]
        if not any(isinstance(value, types[t]) and not (t == "integer" and isinstance(value, bool)) for t in choices):
            raise ValueError(f"expected {expected}")
    if "enum" in spec and value not in spec["enum"]:
        raise ValueError("value outside enum")
    if isinstance(value, str) and len(value) < spec.get("minLength", 0):
        raise ValueError("empty identifier")
    if isinstance(value, int) and value < spec.get("minimum", value):
        raise ValueError("integer below minimum")
    if isinstance(value, dict):
        for key in spec.get("required", []):
            if key not in value:
                raise ValueError(f"missing {key}")
        props = spec.get("properties", {})
        for key, item in value.items():
            if key in props:
                validate(item, props[key])
            elif spec.get("additionalProperties") is False:
                raise ValueError(f"unknown argument {key}")
    if isinstance(value, list) and "items" in spec:
        for item in value:
            validate(item, spec["items"])


class Server:
    def __init__(self, graph):
        self.graph = graph
        self.initialized = False

    def dispatch(self, method, params):
        if not isinstance(params, dict):
            raise ValueError("params must be an object")
        if method == "initialize":
            self.initialized = True
            return {"protocolVersion": "2024-11-05", "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "maritime-jena-tools", "version": "1.0.0"}}
        if method == "ping":
            return {}
        if not self.initialized:
            raise ValueError("initialize required")
        if method == "tools/list":
            return {"tools": [{"name": name, "description": DESCRIPTIONS[name], "inputSchema": spec}
                              for name, spec in SCHEMAS.items()]}
        if method != "tools/call":
            raise LookupError("method not found")
        name, args = params.get("name"), params.get("arguments", {})
        if not isinstance(name, str) or name not in SCHEMAS:
            raise ValueError("unknown tool")
        validate(args, SCHEMAS[name])
        try:
            if name == "executePlan":
                toolbox = MCPToolbox(self.graph)
                result = {"execution": toolbox.execute_plan(args["plan"], args.get("failAfterOperations")), "audit": toolbox.audit}
            elif name == "queryState":
                result = {"vesselId": args["vesselId"], "states": self.graph.query_state(args["vesselId"])}
            elif name == "getSubgraph":
                result = self.graph.get_subgraph(args["entityId"])
            else:
                result = ActionExecutionAgent(self.graph, OntologyModel(args["ontology"])).record_review(
                    args["eventId"], args["status"], args["reason"])
            return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}], "isError": False}
        except Exception as exc:
            return {"content": [{"type": "text", "text": str(exc)}], "isError": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", required=True)
    args = parser.parse_args()
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    server = Server(JenaGraphStore(read_json(ROOT / "data" / "initial_graph.json"), args.dataset_dir))
    for line in sys.stdin:
        request = None
        try:
            request = json.loads(line)
            if not isinstance(request, dict) or request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str):
                raise ValueError("invalid JSON-RPC request")
            if "id" not in request:
                continue  # MCP notifications have no response.
            result = server.dispatch(request["method"], request.get("params", {}))
            response = {"jsonrpc": "2.0", "id": request["id"], "result": result}
        except Exception as exc:
            code = -32700 if isinstance(exc, json.JSONDecodeError) else -32601 if isinstance(exc, LookupError) else -32602 if isinstance(exc, (ValueError, TypeError)) else -32603
            response = {"jsonrpc": "2.0", "id": request.get("id") if isinstance(request, dict) else None,
                        "error": {"code": code, "message": str(exc)}}
        print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
