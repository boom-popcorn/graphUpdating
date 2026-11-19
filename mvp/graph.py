from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
from threading import RLock
import hashlib
import json
from pathlib import Path
from typing import Any

from .io import write_json
from .ontology import OntologyModel


class ToolExecutionError(RuntimeError):
    pass


class GraphStore:
    def __init__(self, document: dict[str, Any]):
        self.document = deepcopy(document)
        self._lock = RLock()

    @contextmanager
    def transaction(self):
        with self._lock:
            original = self.snapshot()
            try:
                yield
            except BaseException:
                self.document = original
                raise

    def snapshot(self) -> dict[str, Any]:
        return deepcopy(self.document)

    def save(self, path: Path) -> None:
        write_json(path, self.document)


class MCPToolbox:
    """Local MCP-shaped tool adapter; every mutation is applied to a transaction copy."""

    def __init__(self, graph: GraphStore):
        self.graph = graph
        self.audit: list[dict[str, Any]] = []

    @staticmethod
    def _relation_key(relation: dict[str, str]) -> tuple[str, str, str]:
        return relation["s"], relation["p"], relation["o"]

    def execute_plan(
        self,
        plan: dict[str, Any],
        fail_after_operations: int | None = None,
    ) -> dict[str, Any]:
        self.audit = []
        with self.graph.transaction():
            return self._execute_plan(plan, fail_after_operations)

    def _execute_plan(self, plan, fail_after_operations=None):
        mutation_keys = ("closeStates", "createIndividuals", "assertTypes", "assertRelations", "attachEvidence",
                         "evidenceRecords", "temporalValues", "textFacts", "expectedSubgraph")
        digest = hashlib.sha256(json.dumps({key: plan.get(key, []) for key in mutation_keys},
                                          sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        applied = self.graph.document.setdefault("appliedPlans", {})
        if plan["planId"] in applied:
            result = deepcopy(applied[plan["planId"]])
            if result.get("planDigest") != digest:
                raise ToolExecutionError(f"plan ID {plan['planId']} already exists with different content")
            result["idempotentReplay"] = True
            for key in ("createdNodes", "createdRelations", "createdTypeAssertions", "closedStates",
                        "attachedEvidence", "registeredEvidence", "toolCalls"):
                result[key] = 0
            return result

        original = self.graph.snapshot()
        staged = deepcopy(original)
        audit: list[dict[str, Any]] = []
        operation_count = 0

        def record(tool: str, **details: Any) -> None:
            nonlocal operation_count
            operation_count += 1
            if fail_after_operations is not None and operation_count > fail_after_operations:
                raise ToolExecutionError(f"injected failure before {tool}")
            audit.append({"operationId": f"{plan['planId']}-OP-{operation_count:03d}", "tool": tool, **details})

        try:
            record("beginTransaction", planId=plan["planId"])
            for evidence in plan.get("evidenceRecords", []):
                evidence_id = evidence["id"]
                previous = staged.setdefault("evidence", {}).get(evidence_id)
                if previous is not None and previous != evidence:
                    raise ToolExecutionError(f"evidence ID {evidence_id} already has different content")
                if previous is None:
                    staged["evidence"][evidence_id] = deepcopy(evidence)
                    record("registerEvidence", evidenceId=evidence_id)
            for temporal in plan.get("temporalValues", []):
                staged.setdefault("temporalValues", {})[temporal["id"]] = temporal["timestamp"]
                record("registerInstant", instantId=temporal["id"])
            for close in plan.get("closeStates", []):
                node = staged["nodes"].get(close["id"])
                if node is None:
                    raise ToolExecutionError(f"cannot close unknown state {close['id']}")
                node["validTo"] = close["validTo"]
                record("closeState", stateId=close["id"], validTo=close["validTo"])
            for individual in plan.get("createIndividuals", []):
                entity_id = individual["id"]
                if entity_id not in staged["nodes"]:
                    staged["nodes"][entity_id] = {
                        "types": [],
                        "properties": deepcopy(individual.get("properties", {})),
                    }
                    record("createIndividual", id=entity_id, type=individual["type"])
                else:
                    existing = staged["nodes"][entity_id]
                    if individual["type"] not in existing.get("types", []):
                        raise ToolExecutionError(f"individual {entity_id} already exists with another type")
            for assertion in plan.get("assertTypes", []):
                node = staged["nodes"].get(assertion["id"])
                if node is None:
                    raise ToolExecutionError(f"cannot type unknown individual {assertion['id']}")
                if assertion["type"] not in node.setdefault("types", []):
                    node["types"].append(assertion["type"])
                    record("assertType", id=assertion["id"], type=assertion["type"])
            for relation in plan.get("assertRelations", []):
                if relation["s"] not in staged["nodes"]:
                    raise ToolExecutionError(f"unknown relation subject {relation['s']}")
                known_object = relation["o"] in staged["nodes"] or relation["o"] in staged.get("temporalValues", {})
                if not known_object:
                    raise ToolExecutionError(f"unknown relation object {relation['o']}")
                key = self._relation_key(relation)
                exists = any(self._relation_key(current) == key for current in staged["relations"])
                if not exists:
                    staged["relations"].append(deepcopy(relation))
                    record("assertRelation", **relation)
            for attachment in plan.get("attachEvidence", []):
                evidence = staged.setdefault("evidence", {}).get(attachment["evidence"])
                if evidence is None:
                    raise ToolExecutionError(f"unknown evidence {attachment['evidence']}")
                event = staged["nodes"].get(attachment["event"])
                if event is None:
                    raise ToolExecutionError(f"unknown event {attachment['event']}")
                links = event.setdefault("evidence", [])
                if attachment["evidence"] not in links:
                    links.append(attachment["evidence"])
                    provenance = {
                        "event": attachment["event"],
                        "evidence": attachment["evidence"],
                        "relation": "used",
                    }
                    if evidence.get("modality") == "text":
                        provenance.update(span=deepcopy(evidence["span"]), source=evidence["source"],
                                          rawText=evidence["rawText"], timestamp=evidence["timestamp"])
                        provenance["factSpans"] = [
                            {"predicate": fact["predicate"], "span": deepcopy(fact["span"]), "quote": fact["quote"]}
                            for fact in plan.get("textFacts", []) if fact["evidence"] == attachment["evidence"]]
                    staged.setdefault("provenance", []).append(provenance)
                    record("attachEvidence", event=attachment["event"], evidence=attachment["evidence"])

            commit_number = len(staged.setdefault("commits", [])) + 1
            result = {
                "planId": plan["planId"],
                "planDigest": digest,
                "commitId": f"C-{commit_number:04d}",
                "success": True,
                "idempotentReplay": False,
                "changedSubgraph": plan["expectedSubgraph"],
                "createdNodes": sum(1 for item in plan["createIndividuals"] if item["id"] not in original["nodes"]),
                "createdRelations": sum(
                    1 for relation in plan["assertRelations"]
                    if not any(self._relation_key(relation) == self._relation_key(old) for old in original["relations"])
                ),
                "createdTypeAssertions": sum(len(node.get("types", [])) - len(original["nodes"].get(entity_id, {}).get("types", []))
                    for entity_id, node in staged["nodes"].items()),
                "closedStates": sum(
                    1 for close in plan["closeStates"]
                    if original["nodes"].get(close["id"], {}).get("validTo") != close["validTo"]
                ),
                "attachedEvidence": len(plan.get("attachEvidence", [])),
                "registeredEvidence": len(staged.get("evidence", {})) - len(original.get("evidence", {})),
                "toolCalls": len(audit),
                "failure": None,
            }
            staged["commits"].append({"commitId": result["commitId"], "planId": plan["planId"], "summary": deepcopy(result)})
            staged["appliedPlans"][plan["planId"]] = deepcopy(result)
            self.graph.document = staged
            self.audit = audit
            return result
        except Exception as exc:
            self.audit = audit
            return {
                "planId": plan["planId"],
                "commitId": None,
                "success": False,
                "idempotentReplay": False,
                "changedSubgraph": [],
                "createdNodes": 0,
                "createdRelations": 0,
                "createdTypeAssertions": 0,
                "closedStates": 0,
                "attachedEvidence": 0,
                "registeredEvidence": 0,
                "toolCalls": len(audit),
                "failure": str(exc),
                "rolledBack": True,
            }


class ActionExecutionAgent:
    name = "action_execution_agent"

    def __init__(self, graph: GraphStore, ontology: OntologyModel | None = None, toolbox=None):
        self.toolbox = toolbox or MCPToolbox(graph)
        self.ontology = ontology

    def execute(self, plan: dict[str, Any], fail_after_operations: int | None = None) -> dict[str, Any]:
        return self.toolbox.execute_plan(plan, fail_after_operations)

    def record_review(self, event_id: str, status: str, reason: str) -> dict[str, Any]:
        if hasattr(self.toolbox, "record_review"):
            return self.toolbox.record_review(event_id, status, reason, self.ontology)
        with self.toolbox.graph.transaction():
            return self._record_review(event_id, status, reason)

    def _record_review(self, event_id: str, status: str, reason: str) -> dict[str, Any]:
        if status not in {"confirmed", "rejected"}:
            raise ValueError("review status must be 'confirmed' or 'rejected'")
        graph = self.toolbox.graph
        if self.ontology is None:
            raise ValueError("an ontology model is required for event review")
        node = graph.document.get("nodes", {}).get(event_id)
        if node is None or "Event" not in self.ontology.inferred_types(set(node.get("types", []))):
            raise ValueError(f"unknown event {event_id}")
        node.setdefault("properties", {})["reviewStatus"] = status
        node["properties"]["reviewReason"] = reason
        graph.document.setdefault("feedback", []).append({"event": event_id, "status": status, "reason": reason})
        return {"event": event_id, "status": status, "reason": reason, "eventRetained": True}


