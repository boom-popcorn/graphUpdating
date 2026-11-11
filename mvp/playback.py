from __future__ import annotations

from copy import deepcopy
from time import perf_counter
from typing import Any

from .harness import Harness


class PlaybackSession:
    """Advance one real MVP round one user-visible stage at a time."""

    total_steps = 8
    supported_modes = {"standard", "ontology_conflict", "plan_rejected", "execution_rollback"}

    def __init__(self, harness: Harness, raw: dict[str, Any], mode: str = "standard"):
        if mode not in self.supported_modes:
            raise ValueError(f"unsupported demo mode: {mode}")
        self.harness = harness
        self.raw = raw
        self.mode = mode
        self.step = 0
        self.started = perf_counter()
        self.trace: list[dict[str, str]] = []
        self.delta: dict[str, Any] | None = None
        self.current = None
        self.proposal: dict[str, Any] | None = None
        self.ontology_report: dict[str, Any] | None = None
        self.updated = None
        self.plan: dict[str, Any] | None = None
        self.plan_report: dict[str, Any] | None = None
        self.execution: dict[str, Any] | None = None
        self.times = {"semanticPerception": 0.0, "ontologyEvolutionAndConsistency": 0.0, "planValidation": 0.0, "execution": 0.0}
        self.status = "READY"
        self.terminal = False
        self.complete = False
        self.title = "初始态势"
        self.summary = "V-017 处于正常航行状态 S-017-0；当前图谱版本为 00，尚未写入本轮数据。"

    def _transition(self, state: str, detail: str) -> None:
        self.trace.append({"state": state, "detail": detail})

    def advance(self) -> dict[str, Any]:
        if self.terminal or self.complete:
            return self.snapshot()

        self.step += 1
        if self.step == 1:
            observations = self.raw.get("observations", [])
            self.status = "OBSERVATIONS_RECEIVED"
            self.title = "新观测进入系统"
            self.summary = f"收到 {len(observations)} 条 AIS / 雷达记录，覆盖 10:06—10:19；此时图谱仍未改变。"
            self._transition("OBSERVATIONS_RECEIVED", f"{len(observations)} observations")
        elif self.step == 2:
            self._transition("PERCEIVING", self.harness.semantic_agent.name)
            started = perf_counter()
            self.delta = self.harness.semantic_agent.perceive(self.raw)
            self.times["semanticPerception"] = round((perf_counter() - started) * 1000, 3)
            self.status = "PERCEIVED"
            self.title = "语义感知完成"
            state = self.delta["states"][0]
            self.summary = (
                f"形成状态 S-017-1：航速 {state['speed']} 节、移动 {state['travelDistance']:.2f} 海里、"
                f"持续 {state['durationSeconds']} 秒，关联 {len(self.delta['evidence'])} 条证据。图谱尚未写入。"
            )
            self._transition("PERCEIVED", "DeltaSemantics created; graph unchanged")
        elif self.step == 3:
            self._evolve_ontology()
        elif self.step == 4:
            self._plan()
        elif self.step == 5:
            self._validate_plan()
        elif self.step == 6:
            self._replan_or_prepare()
        elif self.step == 7:
            self._execute()
        elif self.step == 8:
            self._finish()
        return self.snapshot()

    def _evolve_ontology(self) -> None:
        self._transition("ONTOLOGY_EVOLVING", self.harness.ontology_agent.name)
        started = perf_counter()
        self.current = self.harness.ontology_agent.load_current()
        self.proposal = self.harness.ontology_agent.propose(self.delta, self.current)
        self.proposal["createdAt"] = self.delta["timestamp"]
        if self.mode == "ontology_conflict":
            for prop in self.proposal.get("newObjectProperties", []):
                if prop["id"] == "hasPotentialIllegalActivity":
                    prop["range"] = "State"
        self.ontology_report, self.updated = self.harness.ontology_agent.validate_ontology(
            self.current,
            self.proposal,
            self.harness.graph.document,
        )
        self.times["ontologyEvolutionAndConsistency"] = round((perf_counter() - started) * 1000, 3)
        if not self.ontology_report["consistent"] or self.updated is None:
            self.status = "REJECTED"
            self.terminal = True
            self.title = "知识约束检查拒绝本体提案"
            self.summary = "候选关系与现有类型约束冲突；本体未升级，图谱未写入。"
            self._transition("ONTOLOGY_REJECTED", self.ontology_report.get("reason") or "proposal rejected")
            return

        self.harness.ontology_agent.publish(self.updated, self.ontology_report)
        self.status = "ONTOLOGY_VALIDATED"
        self.title = "本体演化通过逻辑检查"
        self.summary = "新增事件概念、区域风险关系和规则；HermiT 检查通过，知识版本 00 → 01。图谱仍未写入。"
        self._transition("ONTOLOGY_VALIDATED", f"published ontology version {self.updated.version}")

    def _plan(self) -> None:
        self._transition("PLANNING", self.harness.planning_agent.name)
        self.plan = self.harness.planning_agent.plan(
            self.delta,
            self.updated,
            self.harness.graph.document,
        )
        if self.mode == "plan_rejected" and len(self.plan.get("createIndividuals", [])) > 1:
            self.plan["createIndividuals"][1]["type"] = "UndeclaredEventType"
        self.status = "PLANNED"
        self.title = "决策规划生成更新方案"
        self.summary = (
            f"命中规则 {', '.join(self.plan['ruleHits'])}，形成方案 P1：关闭旧状态、创建新状态与候选事件、"
            f"关联 {len(self.plan['assertRelations'])} 条关系并挂接证据。执行尚未开始。"
        )

    def _validate_plan(self) -> None:
        self._transition("PLAN_VALIDATING", f"{self.harness.ontology_agent.name}.validate_plan")
        started = perf_counter()
        self.plan_report = self.harness.ontology_agent.validate_plan(
            self.plan,
            self.updated,
            self.harness.graph.document,
            self.delta,
        )
        self.times["planValidation"] += round((perf_counter() - started) * 1000, 3)
        if self.plan_report["passed"]:
            self.status = "PLAN_VALIDATED"
            self.title = "本体演化智能体复核计划"
            self.summary = "对象类型、关系范围、时间顺序、证据来源和影响范围均通过；图谱尚未写入。"
            self._transition("PLAN_VALIDATED", "all plan checks passed in ontology evolution agent")
            return

        self.status = "PLAN_REJECTED"
        self.title = "首版计划被拦截"
        self.summary = "复核发现计划类型不符合知识模型；执行智能体没有收到写入指令，正在等待规划环节重规划。"
        self._transition("PLAN_REJECTED", "; ".join(self.plan_report["suggestions"]))

    def _replan_or_prepare(self) -> None:
        if self.plan_report and not self.plan_report["passed"]:
            self.plan = self.harness.planning_agent.replan(
                self.plan,
                self.plan_report,
                self.delta,
                self.updated,
                self.harness.graph.document,
            )
            self._transition("REPLANNING", f"attempt {self.plan['replanAttempt']}")
            started = perf_counter()
            self.plan_report = self.harness.ontology_agent.validate_plan(
                self.plan,
                self.updated,
                self.harness.graph.document,
                self.delta,
            )
            self.times["planValidation"] += round((perf_counter() - started) * 1000, 3)
            if not self.plan_report["passed"]:
                self.status = "REJECTED"
                self.terminal = True
                self.title = "重规划仍未通过复核"
                self.summary = "更新计划继续被拦截，没有启动图谱写入。"
                self._transition("PLAN_REJECTED", "; ".join(self.plan_report["suggestions"]))
                return
            self.status = "REPLANNED"
            self.title = "重规划通过"
            self.summary = f"本体演化环节把复核反馈交回规划环节；第 {self.plan['replanAttempt']} 次方案通过检查，现可执行。"
            self._transition("PLAN_VALIDATED", "replanned plan passed in ontology evolution agent")
            return

        self.status = "READY_TO_EXECUTE"
        self.title = "更新范围锁定"
        self.summary = "计划通过本体演化智能体复核，写入范围仅限 V-017 相关子图；下一步才会提交事务。"
        self._transition("EXECUTION_PREPARED", "transaction not committed yet")

    def _execute(self) -> None:
        self._transition("EXECUTING", self.harness.execution_agent.name)
        started = perf_counter()
        self.execution = self.harness.execution_agent.execute(
            self.plan,
            fail_after_operations=8 if self.mode == "execution_rollback" else None,
        )
        self.times["execution"] = round((perf_counter() - started) * 1000, 3)
        if self.execution["success"]:
            self.status = "COMMITTED"
            self.title = "图谱增量更新已提交"
            self.summary = (
                f"保留旧状态，新增 {self.execution['createdNodes']} 个节点和 "
                f"{self.execution['createdRelations']} 条关系；提交号 {self.execution['commitId']}。"
            )
            self._transition("COMMITTED", self.execution["commitId"])
        else:
            self.status = "EXECUTION_FAILED"
            self.title = "事务失败并已回滚"
            self.summary = f"工具执行中断：{self.execution['failure']}。暂存变更已撤销，未产生半写入。"
            self._transition("EXECUTION_FAILED", self.execution["failure"])

    def _finish(self) -> None:
        self.complete = True
        self.status = "FEEDBACK"
        self.title = "反馈闭环"
        if self.execution and self.execution["success"] and "E-017" in self.harness.graph.document["nodes"]:
            self.summary = "候选事件 E-017 已建立证据链并等待人工复核；确认或误报都会保留历史记录。"
        elif self.execution and not self.execution["success"]:
            self.summary = "本轮执行未提交；图谱保持事务前状态，失败原因与回滚结果已记录。"
        else:
            self.summary = "候选本体未发布；知识版本与图谱均保持不变。"
        self._transition("FEEDBACK", "round summary available")

    def _report(self) -> dict[str, Any]:
        report: dict[str, Any] = {
            "roundId": self.raw["roundId"],
            "demoMode": self.mode,
            "status": self.status,
            "ontologyValidation": self.ontology_report,
            "planValidation": self.plan_report,
            "executionResult": self.execution,
            "trace": deepcopy(self.trace),
            "metricsMs": {
                **self.times,
                "endToEnd": round((perf_counter() - self.started) * 1000, 3),
            },
        }
        if self.delta is not None:
            report["deltaSemantics"] = self.delta
        if self.proposal is not None:
            report["evolutionProposal"] = self.proposal
        if self.updated is not None:
            report["ontologyVersion"] = self.updated.version
            report["ontologyToolAudit"] = self.harness.ontology_agent.tool_audit
        if self.plan is not None:
            report["updatePlan"] = self.plan
        if self.execution is not None:
            report["mcpToolAudit"] = self.harness.execution_agent.toolbox.audit
            if self.execution["success"]:
                report["feedback"] = {
                    "ontologyChanges": self.updated.document["changeLog"],
                    "ruleHits": self.plan["ruleHits"],
                    "reviewStatus": "pending" if "E-017" in self.harness.graph.document["nodes"] else None,
                    "recommendation": "Keep the current thresholds; review the candidate event before treating it as confirmed.",
                }
                report["comparison"] = self.harness._comparison_metrics(self.execution)
        return report

    def snapshot(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "totalSteps": self.total_steps,
            "mode": self.mode,
            "status": self.status,
            "title": self.title,
            "summary": self.summary,
            "terminal": self.terminal,
            "complete": self.complete,
            "report": self._report(),
            "graph": self.harness.graph.snapshot(),
            "initialGraph": self.harness.initial_graph,
            "rawObservations": self.raw.get("observations", []) if self.step >= 1 else [],
        }
