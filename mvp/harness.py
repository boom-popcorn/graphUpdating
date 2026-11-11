from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from time import perf_counter
from typing import Any

from .agents import DecisionPlanningAgent, SemanticPerceptionAgent
from .graph import ActionExecutionAgent, GraphStore
from .io import read_json
from .ontology import OntologyAgent, OntologyModel


class Harness:
    def __init__(self, root: Path, graph_document: dict[str, Any] | None = None):
        self.root = root
        self.ontology_agent = OntologyAgent(root / "ontology")
        self.semantic_agent = SemanticPerceptionAgent()
        self.planning_agent = DecisionPlanningAgent()
        if graph_document is None:
            graph_document = read_json(root / "data" / "initial_graph.json")
        self.initial_graph = deepcopy(graph_document)
        self.graph = GraphStore(graph_document)
        self.execution_agent = ActionExecutionAgent(self.graph)

    def _comparison_metrics(self, execution: dict[str, Any] | None = None) -> dict[str, Any]:
        graph = self.graph.document
        execution = execution or {}
        full_rebuild_writes = (
            len(graph.get("nodes", {}))
            + len(graph.get("relations", []))
            + sum(len(node.get("types", [])) for node in graph.get("nodes", {}).values())
            + len(graph.get("evidence", {}))
            + len(graph.get("provenance", []))
        )
        incremental_writes = (
            execution.get("createdNodes", 0)
            + execution.get("createdRelations", 0)
            + execution.get("createdTypeAssertions", 0)
            + execution.get("attachedEvidence", 0)
            + max(
                len(graph.get("provenance", []))
                - len(self.initial_graph.get("provenance", [])),
                0,
            )
            + execution.get("closedStates", 0)
        )
        return {
            "incrementalWrites": incremental_writes,
            "fullRebuildWritesEstimate": full_rebuild_writes,
            "avoidedWritesEstimate": max(full_rebuild_writes - incremental_writes, 0),
            "note": "按节点、关系、类型、证据来源记录及状态关闭计数；全量为本案例逻辑项估算，不代表数据库基准测试。",
        }

    def run_round(self, raw: dict[str, Any], demo_mode: str = "standard") -> dict[str, Any]:
        if demo_mode not in {"standard", "ontology_conflict", "plan_rejected", "execution_rollback"}:
            raise ValueError(f"unsupported demo mode: {demo_mode}")
        started = perf_counter()
        trace: list[dict[str, Any]] = []

        def transition(state: str, detail: str) -> None:
            trace.append({"state": state, "detail": detail})

        transition("PERCEIVING", self.semantic_agent.name)
        t0 = perf_counter()
        delta = self.semantic_agent.perceive(raw)
        perception_ms = round((perf_counter() - t0) * 1000, 3)

        transition("ONTOLOGY_EVOLVING", self.ontology_agent.name)
        t0 = perf_counter()
        current = self.ontology_agent.load_current()
        proposal = self.ontology_agent.propose(delta, current)
        proposal["createdAt"] = delta["timestamp"]
        if demo_mode == "ontology_conflict":
            for prop in proposal.get("newObjectProperties", []):
                if prop["id"] == "hasPotentialIllegalActivity":
                    prop["range"] = "State"
        ontology_report, updated = self.ontology_agent.validate_ontology(current, proposal, self.graph.document)
        ontology_ms = round((perf_counter() - t0) * 1000, 3)
        if not ontology_report["consistent"] or updated is None:
            transition("ONTOLOGY_REJECTED", ontology_report.get("reason") or "ontology proposal rejected")
            result = self._rejection_report(raw, delta, proposal, ontology_report, trace, started, perception_ms, ontology_ms)
            result["demoMode"] = demo_mode
            result["comparison"] = self._comparison_metrics()
            return result

        self.ontology_agent.publish(updated, ontology_report)
        transition("ONTOLOGY_VALIDATED", f"published ontology version {updated.version}")

        transition("PLANNING", self.planning_agent.name)
        plan = self.planning_agent.plan(delta, updated, self.graph.document)
        if demo_mode == "plan_rejected" and len(plan.get("createIndividuals", [])) > 1:
            plan["createIndividuals"][1]["type"] = "UndeclaredEventType"
        transition("PLAN_VALIDATING", f"{self.ontology_agent.name}.validate_plan")
        t0 = perf_counter()
        plan_report = self.ontology_agent.validate_plan(plan, updated, self.graph.document, delta)
        plan_validation_ms = round((perf_counter() - t0) * 1000, 3)
        if not plan_report["passed"]:
            transition("PLAN_REJECTED", "; ".join(plan_report["suggestions"]))
            plan = self.planning_agent.replan(plan, plan_report, delta, updated, self.graph.document)
            transition("REPLANNING", f"attempt {plan['replanAttempt']}")
            t0 = perf_counter()
            plan_report = self.ontology_agent.validate_plan(plan, updated, self.graph.document, delta)
            plan_validation_ms += round((perf_counter() - t0) * 1000, 3)
            if not plan_report["passed"]:
                transition("PLAN_REJECTED", "; ".join(plan_report["suggestions"]))
                result = self._rejection_report(raw, delta, proposal, ontology_report, trace, started, perception_ms, ontology_ms, plan, plan_report, plan_validation_ms)
                result["demoMode"] = demo_mode
                result["comparison"] = self._comparison_metrics()
                return result

        transition("PLAN_VALIDATED", "plan validation passed in ontology evolution agent")
        transition("EXECUTING", self.execution_agent.name)
        t0 = perf_counter()
        result = self.execution_agent.execute(
            plan,
            fail_after_operations=8 if demo_mode == "execution_rollback" else None,
        )
        execution_ms = round((perf_counter() - t0) * 1000, 3)
        if result["success"]:
            transition("COMMITTED", result["commitId"])
        else:
            transition("EXECUTION_FAILED", result["failure"])

        transition("FEEDBACK", "record update summary and round metrics")
        duration_ms = round((perf_counter() - started) * 1000, 3)
        report = {
            "roundId": raw["roundId"],
            "demoMode": demo_mode,
            "status": "COMMITTED" if result["success"] else "EXECUTION_FAILED",
            "agents": {
                "semanticPerception": self.semantic_agent.name,
                "ontologyEvolutionAndValidation": self.ontology_agent.name,
                "decisionPlanning": self.planning_agent.name,
                "actionExecution": self.execution_agent.name,
            },
            "deltaSemantics": delta,
            "evolutionProposal": proposal,
            "ontologyValidation": ontology_report,
            "ontologyToolAudit": self.ontology_agent.tool_audit,
            "ontologyVersion": updated.version,
            "updatePlan": plan,
            "planValidation": plan_report,
            "executionResult": result,
            "mcpToolAudit": self.execution_agent.toolbox.audit,
            "feedback": {
                "ontologyChanges": updated.document["changeLog"],
                "ruleHits": plan["ruleHits"],
                "reviewStatus": "pending" if "E-017" in self.graph.document["nodes"] else None,
                "recommendation": "Keep the current thresholds; review the candidate event before treating it as confirmed.",
            },
            "metricsMs": {
                "semanticPerception": perception_ms,
                "ontologyEvolutionAndConsistency": ontology_ms,
                "planValidation": plan_validation_ms,
                "execution": execution_ms,
                "endToEnd": duration_ms,
            },
            "trace": trace,
        }
        report["comparison"] = self._comparison_metrics(result)
        return report

    @staticmethod
    def _rejection_report(
        raw: dict[str, Any],
        delta: dict[str, Any],
        proposal: dict[str, Any],
        ontology_report: dict[str, Any],
        trace: list[dict[str, Any]],
        started: float,
        perception_ms: float,
        ontology_ms: float,
        plan: dict[str, Any] | None = None,
        plan_report: dict[str, Any] | None = None,
        plan_validation_ms: float = 0,
    ) -> dict[str, Any]:
        return {
            "roundId": raw["roundId"],
            "status": "REJECTED",
            "deltaSemantics": delta,
            "evolutionProposal": proposal,
            "ontologyValidation": ontology_report,
            "updatePlan": plan,
            "planValidation": plan_report,
            "metricsMs": {
                "semanticPerception": perception_ms,
                "ontologyEvolutionAndConsistency": ontology_ms,
                "planValidation": plan_validation_ms,
                "execution": 0,
                "endToEnd": round((perf_counter() - started) * 1000, 3),
            },
            "trace": trace,
        }

