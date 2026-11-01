"""Interactive prototype server for the maritime knowledge-update workbench."""

from __future__ import annotations

import json
import os
import threading
from hashlib import sha256
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from rdflib import Graph

from odkg.maritime.agents import KNOWN_PAYLOAD_FIELDS, OntologyEvolutionAgent
from odkg.maritime.harness import UpdateHarness
from odkg.maritime.models import (
    CandidateFact,
    EvolutionKind,
    Modality,
    ObjectKind,
    OntologyEvolutionProposal,
    ValidationFeedback,
    WorkflowResult,
    WorkflowStatus,
)
from odkg.maritime.ontology import MaritimeGraphRepository
from odkg.maritime.pipeline import resolve_vessel_id
from odkg.maritime.scenarios import build_scenario, scenario_catalog
from odkg.demo import build_maritime_orchestrator, project_root
from odkg.environment import configure_model_environment
from odkg.llm_harness import LLMMultiAgentHarness, OpenAICompatibleRoleClient

ROOT = Path(__file__).resolve().parents[3]
WEB = Path(__file__).resolve().parent
ONTOLOGY = ROOT / "ontology" / "maritime-v0.1.ttl"
SHAPES = ROOT / "ontology" / "maritime-v0.1-shapes.ttl"
SNAPSHOTS = ROOT / "output" / "prototype-snapshots"
ONTOLOGY_VERSIONS = ROOT / "output" / "ontology-versions"
CHINA_STANDARD_TIME = timezone(timedelta(hours=8))


def _now() -> datetime:
    return datetime.now(CHINA_STANDARD_TIME)


def _json_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


@dataclass
class PrototypeState:
    scenario_key: str = "full_update"
    events: list[Any] = field(default_factory=list)
    cursor: int = 0
    accepted: list[Any] = field(default_factory=list)
    seen_event_ids: set[str] = field(default_factory=set)
    repository: MaritimeGraphRepository | None = None
    harness: UpdateHarness | None = None
    last_result: Any = None
    llm_result: Any = None
    llm_error: str | None = None
    last_message: str = "请选择场景并开始接收数据。"
    batches: list[dict[str, Any]] = field(default_factory=list)
    fact_reviews: dict[str, dict[str, Any]] = field(default_factory=dict)
    proposal_reviews: dict[str, dict[str, Any]] = field(default_factory=dict)
    audit_log: list[dict[str, Any]] = field(default_factory=list)
    active_ontology_path: Path = ONTOLOGY
    ontology_version: str = "0.1.0"
    approved_fields: set[str] = field(default_factory=set)
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def __post_init__(self) -> None:
        self.reset(self.scenario_key)

    def _audit(self, category: str, action: str, detail: str, outcome: str = "success") -> None:
        self.audit_log.insert(0, {
            "id": f"log-{len(self.audit_log) + 1:04d}",
            "time": _now().isoformat(),
            "operator": "当前值班员",
            "category": category,
            "action": action,
            "detail": detail,
            "outcome": outcome,
        })

    def _scenario(self) -> dict[str, str]:
        return next(item for item in scenario_catalog() if item["key"] == self.scenario_key)

    def reset(self, scenario_key: str) -> None:
        if scenario_key not in {item["key"] for item in scenario_catalog()}:
            raise ValueError(f"未知场景：{scenario_key}")
        with self.lock:
            self.scenario_key = scenario_key
            self.events = build_scenario(scenario_key)
            self.cursor = 0
            self.accepted = []
            self.seen_event_ids = set()
            self.repository = MaritimeGraphRepository(ONTOLOGY, SHAPES, snapshot_dir=SNAPSHOTS)
            # Every scenario starts as a clean rehearsal; persisted snapshots must not
            # be mistaken for the initial v0 graph.
            initial_graph = Graph()
            self.repository._bind_namespaces(initial_graph)
            self.repository.data_graph = initial_graph
            self.repository.current_version = 0
            self.repository._snapshots = {0: self.repository._copy_graph(self.repository.data_graph)}
            self.repository._committed_keys.clear()
            self.harness = UpdateHarness(self.repository)
            self.last_result = None
            self.llm_result = None
            self.llm_error = None
            self.batches = []
            self.fact_reviews = {}
            self.proposal_reviews = {}
            self.audit_log = []
            self.active_ontology_path = ONTOLOGY
            self.ontology_version = "0.1.0"
            self.approved_fields = set()
            self.last_message = "场景已准备好。点击“接收下一批数据”开始处理。"
            self._audit("场景", "初始化", f"已加载“{self._scenario()['title']}”演示场景")

    def _analyse(self) -> None:
        # Data reception only normalizes and deduplicates. Business facts are produced
        # exclusively by the model-driven multi-agent Harness.
        self.last_result = None

    def receive_next_batch(self, size: int = 4) -> None:
        with self.lock:
            batch = self.events[self.cursor:self.cursor + max(1, size)]
            if not batch:
                self.last_message = "当前场景的数据已经全部接收。"
                return
            self.cursor += len(batch)
            accepted = [item for item in batch if item.event_id not in self.seen_event_ids]
            duplicates = [item.event_id for item in batch if item.event_id in self.seen_event_ids]
            self.seen_event_ids.update(item.event_id for item in accepted)
            self.accepted.extend(accepted)
            self.batches.insert(0, {
                "id": f"batch-{len(self.batches) + 1:03d}",
                "received_at": _now().isoformat(),
                "accepted": len(accepted),
                "duplicates": len(duplicates),
                "event_ids": [item.event_id for item in accepted],
                "status": "processed",
            })
            self._analyse()
            self.last_message = (
                f"已接收并标准化 {len(batch)} 条数据。"
                "请点击“大模型多智能体分析”启动四智能体 Harness。"
            )
            self._audit("数据接入", "处理批次", f"接收 {len(accepted)} 条，重复 {len(duplicates)} 条")

    def run_all(self) -> None:
        with self.lock:
            if self.cursor < len(self.events):
                self.receive_next_batch(len(self.events) - self.cursor)
            else:
                self.last_message = "当前场景的数据已经全部接收并完成分析。"

    def run_llm_agents(self) -> None:
        """Run the model-driven roles; deterministic code remains a guarded tool layer."""
        with self.lock:
            if not self.accepted:
                raise ValueError("请先接收海事观测数据")
            configure_model_environment(project_root())
            client = OpenAICompatibleRoleClient.from_project_environment(
                str(project_root())
            )
            harness = LLMMultiAgentHarness(build_maritime_orchestrator(), client)
            events = [
                item.model_dump(mode="json", exclude={"provenance"})
                for item in self.accepted
            ]
            try:
                self.llm_result = harness.run(events, dry_run=True)
                self._adopt_llm_result()
                self.llm_error = None
            except Exception as exc:
                self.llm_error = str(exc)
                self._audit("大模型智能体", "分析失败", self.llm_error, "failed")
                raise
            role_names = {
                "semantic_perception": "语义感知智能体",
                "ontology_evolution": "本体演化智能体",
                "decision_planning": "决策规划智能体",
                "action_execution": "行动执行智能体",
            }
            decision_names = {"accept": "接受", "review": "需人工复核", "reject": "拒绝"}
            decisions = "，".join(
                f"{role_names.get(trace.role, trace.role)}："
                f"{decision_names.get(trace.decision.decision, trace.decision.decision)}"
                for trace in self.llm_result.traces
            )
            self.last_message = (
                f"大模型多智能体分析完成：{decisions}。"
                "确定性校验器已完成预检，等待人工审核。"
            )
            self._audit("大模型智能体", "完成协同分析", self.last_message)

    def _adopt_llm_result(self) -> None:
        """Convert model artifacts into typed candidates; no business inference occurs here."""
        assert self.llm_result is not None
        assert self.harness is not None
        event_by_id = {item.event_id: item for item in self.accepted}
        facts: list[CandidateFact] = []
        for item in self.llm_result.observations:
            evidence_ids = tuple(dict.fromkeys(e["source_id"] for e in item["evidence"]))
            evidence = [event_by_id[event_id] for event_id in evidence_ids if event_id in event_by_id]
            if not evidence or len(evidence) != len(evidence_ids):
                raise ValueError("大模型候选事实引用了不存在的证据")
            token_source = json.dumps(item, ensure_ascii=False, sort_keys=True)
            object_value = item["object"]
            object_is_http_iri = isinstance(object_value, str) and object_value.startswith(("http://", "https://"))
            facts.append(CandidateFact(
                fact_id=f"fact-{sha256(token_source.encode('utf-8')).hexdigest()[:16]}",
                subject=item["subject"], predicate=item["predicate"],
                object_value=object_value,
                object_kind=(
                    ObjectKind.LITERAL
                    if item["object_is_literal"] or not object_is_http_iri
                    else ObjectKind.IRI
                ),
                valid_from=min(event.observed_at for event in evidence),
                asserted_at=max(event.received_at for event in evidence),
                evidence_ids=evidence_ids, confidence=item["confidence"],
                ontology_version=self.ontology_version,
            ))

        new_fact_reviews: dict[str, dict[str, Any]] = {}
        new_proposal_reviews: dict[str, dict[str, Any]] = {}
        if not facts:
            self.last_result = WorkflowResult(
                status=WorkflowStatus.NO_CHANGE, graph_version=self.repository.current_version,
                observation_count=len(self.accepted), candidate_fact_count=0,
                planning_attempts=0, message="大模型未生成候选事实。",
            )
            return

        change_set = self.harness.planning_agent.plan(facts, self.repository.current_version)
        validation = self.harness.validation_agent.validate(
            self.repository, change_set, self.accepted, 1
        )
        status = WorkflowStatus.PENDING_REVIEW if validation.valid else WorkflowStatus.REJECTED
        self.last_result = WorkflowResult(
            status=status, graph_version=self.repository.current_version,
            observation_count=len(self.accepted), candidate_fact_count=len(facts),
            planning_attempts=1, change_set=change_set,
            validation_history=(validation,),
            message="大模型候选更新已通过安全校验，等待人工审核。" if validation.valid else "大模型候选更新未通过安全校验。",
        )
        for fact in facts:
            new_fact_reviews[fact.fact_id] = {
                "fact": fact, "status": "pending" if validation.valid else "failed",
                "created_at": _now().isoformat(), "reviewed_at": None, "reason": "",
                "validation": (validation,), "graph_version": None,
            }
            self._audit("知识更新", "大模型生成候选事实", f"候选事实 {fact.fact_id} 等待审核")

        ontology_namespace = "https://example.org/maritime-kg/ontology/"
        xsd_namespace = "http://www.w3.org/2001/XMLSchema#"

        def expand_ontology_iri(value: Any, fallback: str) -> str:
            text = str(value or fallback).strip()
            return text if text.startswith(("http://", "https://")) else ontology_namespace + text

        def expand_datatype_iri(value: Any) -> str:
            text = str(value or "string").strip()
            if text.startswith(("http://", "https://")):
                return text
            return xsd_namespace + text.removeprefix("xsd:")

        for draft in self.llm_result.agent_artifacts.get("evolution_proposals", []):
            evidence_ids = tuple(dict.fromkeys(draft.get("evidence_ids", [])))
            modalities = tuple(Modality(item) for item in draft.get("source_modalities", []))
            if not evidence_ids or not modalities:
                continue
            source_field = str(draft.get("source_field", "unknown_field"))
            token = sha256(json.dumps(draft, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
            proposal = OntologyEvolutionProposal(
                proposal_id=f"evolution-{token}",
                kind=EvolutionKind(draft.get("kind", "update_mapping")),
                source_field=source_field, source_modalities=modalities,
                suggested_term=expand_ontology_iri(draft.get("suggested_term"), source_field),
                suggested_domain=expand_ontology_iri(draft.get("suggested_domain"), "Observation"),
                suggested_datatype=expand_datatype_iri(draft.get("suggested_datatype")),
                rationale=draft.get("rationale", "大模型识别到语义演化需求"),
                evidence_ids=evidence_ids,
            )
            new_proposal_reviews[proposal.proposal_id] = {
                "proposal": proposal, "status": "pending", "reviewed_at": None,
                "reason": "", "generated_version": None,
            }
        self.fact_reviews = new_fact_reviews
        self.proposal_reviews = new_proposal_reviews

    def approve_fact(self, fact_id: str) -> None:
        with self.lock:
            record = self.fact_reviews.get(fact_id)
            if record is None:
                raise ValueError("未找到该候选事实")
            if record["status"] != "pending":
                raise ValueError("该候选事实已经处理")
            fact: CandidateFact = record["fact"]
            evidence_ids = set(fact.evidence_ids)
            evidence = [item for item in self.accepted if item.event_id in evidence_ids]
            assert self.harness is not None
            result = self.harness.commit_reviewed([fact], evidence)
            record["reviewed_at"] = _now().isoformat()
            record["validation"] = result.validation_history
            record["graph_version"] = result.graph_version
            self.last_result = result
            vessel_id = fact.subject.rsplit("/", 1)[-1]
            if result.status in {WorkflowStatus.COMMITTED, WorkflowStatus.NO_CHANGE}:
                record["status"] = "committed"
                self.last_message = f"目标 {vessel_id} 的状态更新已批准，并原子提交到图谱版本 {result.graph_version}。"
                self._audit("知识更新", "批准并提交", self.last_message)
            else:
                record["status"] = "failed"
                self.last_message = f"最终一致性校验未通过，目标 {vessel_id} 的更新没有写入图谱。"
                self._audit("知识更新", "提交失败", self.last_message, "failed")

    def reject_fact(self, fact_id: str, reason: str) -> None:
        with self.lock:
            record = self.fact_reviews.get(fact_id)
            if record is None or record["status"] != "pending":
                raise ValueError("该候选事实不存在或已经处理")
            record["status"] = "rejected"
            record["reviewed_at"] = _now().isoformat()
            record["reason"] = reason.strip() or "人工研判认为证据不足"
            vessel_id = record["fact"].subject.rsplit("/", 1)[-1]
            self.last_message = f"已拒绝目标 {vessel_id} 的候选更新，正式图谱未改变。"
            self._audit("知识更新", "拒绝候选事实", f"{self.last_message} 原因：{record['reason']}")

    def approve_evolution(self, proposal_id: str) -> None:
        with self.lock:
            record = self.proposal_reviews.get(proposal_id)
            if record is None:
                raise ValueError("未找到该本体演化建议")
            if record["status"] != "pending":
                raise ValueError("该本体演化建议已经处理")
            proposal = record["proposal"]
            major, minor, _ = (int(part) for part in self.ontology_version.split("."))
            target_version = f"{major}.{minor + 1}.0"
            target_path = ONTOLOGY_VERSIONS / f"maritime-v{target_version}.ttl"
            version = OntologyEvolutionAgent().approve(
                proposal,
                self.active_ontology_path,
                target_path,
                target_version=target_version,
            )
            self.active_ontology_path = target_path
            self.ontology_version = version
            self.approved_fields.add(proposal.source_field)
            record["status"] = "approved"
            record["reviewed_at"] = _now().isoformat()
            record["generated_version"] = version
            self.last_message = f"本体演化建议已批准，{proposal.source_field} 已映射到本体版本 {version}。"
            self._audit("本体演化", "批准建议", self.last_message)

    def reject_evolution(self, proposal_id: str, reason: str) -> None:
        with self.lock:
            record = self.proposal_reviews.get(proposal_id)
            if record is None or record["status"] != "pending":
                raise ValueError("该本体演化建议不存在或已经处理")
            record["status"] = "rejected"
            record["reviewed_at"] = _now().isoformat()
            record["reason"] = reason.strip() or "该字段暂不纳入业务本体"
            self.last_message = f"已拒绝字段 {record['proposal'].source_field} 的本体演化建议。"
            self._audit("本体演化", "拒绝建议", f"{self.last_message} 原因：{record['reason']}")

    def rollback(self, target: int) -> None:
        with self.lock:
            assert self.repository is not None
            source = self.repository.current_version
            self.repository.rollback_to(target)
            self.last_message = f"已将正式知识图谱从版本 {source} 恢复到版本 {target}。"
            self._audit("版本管理", "恢复版本", self.last_message)

    def _targets(self) -> list[dict[str, Any]]:
        rows: dict[str, dict[str, Any]] = {}
        for event in sorted(self.accepted, key=lambda item: item.observed_at):
            vessel_id = resolve_vessel_id(event)
            row = rows.setdefault(vessel_id, {
                "id": vessel_id, "last_time": "", "speed": None, "course": None,
                "modalities": set(), "area": "", "track": [], "observation_count": 0,
                "confidence_values": [], "status": "待观察", "review_status": None,
            })
            row["last_time"] = event.observed_at.isoformat()
            row["speed"] = event.speed_mps
            row["course"] = event.course_deg
            row["modalities"].add(event.modality.value)
            row["area"] = event.payload.get("area_id", row["area"])
            row["observation_count"] += 1
            row["confidence_values"].append(event.confidence)
            if event.position:
                row["track"].append({
                    "longitude": event.position.longitude, "latitude": event.position.latitude,
                    "time": event.observed_at.isoformat(), "modality": event.modality.value,
                    "speed": event.speed_mps,
                })
        for review in self.fact_reviews.values():
            vessel_id = review["fact"].subject.rsplit("/", 1)[-1]
            if vessel_id in rows:
                rows[vessel_id]["review_status"] = review["status"]
                rows[vessel_id]["status"] = {
                    "pending": "疑似异常，待审核", "committed": "异常悬停",
                    "rejected": "人工排除", "failed": "校验失败",
                }.get(review["status"], "待观察")
        for row in rows.values():
            if row["review_status"] is None and (row["speed"] or 0) > 0.5:
                row["status"] = "正常航行"
            row["modalities"] = sorted(row["modalities"])
            values = row.pop("confidence_values")
            row["confidence"] = round(sum(values) / len(values), 3)
        return sorted(rows.values(), key=lambda item: item["id"])

    def report(self) -> dict[str, Any]:
        with self.lock:
            assert self.repository is not None
            facts = []
            for record in self.fact_reviews.values():
                fact = record["fact"]
                facts.append({
                    **fact.model_dump(mode="json"),
                    "vessel_id": fact.subject.rsplit("/", 1)[-1],
                    "status": record["status"], "created_at": record["created_at"],
                    "reviewed_at": record["reviewed_at"], "reason": record["reason"],
                    "graph_version": record["graph_version"],
                    "validation": [_json_value(item) for item in record["validation"]],
                })
            proposals = []
            for record in self.proposal_reviews.values():
                proposals.append({
                    **record["proposal"].model_dump(mode="json"),
                    "status": record["status"], "reviewed_at": record["reviewed_at"],
                    "reason": record["reason"], "generated_version": record["generated_version"],
                })
            pending_count = sum(item["status"] == "pending" for item in facts + proposals)
            versions = [
                {"version": version, "triple_count": len(self.repository._snapshots[version]),
                 "current": version == self.repository.current_version}
                for version in self.repository.available_versions()
            ]
            workflow_status = self.last_result.status.value if self.last_result else "waiting"
            targets = self._targets()
            model_environment = configure_model_environment(project_root())
            model_harness = {
                "configured": bool(
                    model_environment["base_url_configured"]
                    and model_environment["model"]
                ),
                "model": model_environment["model"],
                "status": self.llm_result.status if self.llm_result else "waiting",
                "error": self.llm_error,
                "traces": (
                    [trace.model_dump(mode="json") for trace in self.llm_result.traces]
                    if self.llm_result
                    else []
                ),
            }
            return {
                "scenario": self._scenario(), "message": self.last_message,
                "progress": {"received": self.cursor, "total": len(self.events),
                             "percent": round(self.cursor / len(self.events) * 100) if self.events else 0},
                "targets": targets,
                "observations": [item.model_dump(mode="json") for item in self.accepted],
                "batches": self.batches, "fact_reviews": facts, "proposals": proposals,
                "pending_count": pending_count, "status": workflow_status,
                "graph_version": self.repository.current_version,
                "triple_count": len(self.repository.data_graph), "versions": versions,
                "model_harness": model_harness,
                "ontology": {
                    "version": self.ontology_version, "path": str(self.active_ontology_path),
                    "approved_fields": sorted(self.approved_fields),
                    "layers": {
                        "对象层": ["船舶", "轨迹", "状态快照", "海事区域", "事件", "观测"],
                        "关系层": ["locatedIn", "hasState", "hasTrack", "identifiedAs", "describes", "supportedBy"],
                        "规则层": ["OWL 约束", "SHACL 一致性", "时空悬停判定"],
                        "行动层": ["创建对象", "更新属性", "建立关系", "生成事件", "事实失效", "回溯补偿"],
                    },
                    "mapped_field_count": sum(len(value) for value in KNOWN_PAYLOAD_FIELDS.values()) + len(self.approved_fields),
                },
                "rules": [
                    {"id": "llm-semantic-reasoning", "name": "大模型语义事实构造", "enabled": True,
                     "expression": "由语义感知智能体完成变化检测、时空对齐、跨模态关联和事实构造",
                     "last_result": f"生成 {len(facts)} 条候选事实"},
                    {"id": "owl-shacl-guard", "name": "OWL/SHACL 安全校验", "enabled": True,
                     "expression": "只校验结构、类型、一致性和证据引用，不进行业务事实推理",
                     "last_result": "校验通过后才允许进入人工审核"},
                ],
                "pipeline": [
                    {"name": "多模态数据接入", "status": "done" if self.accepted else "waiting", "detail": f"{len(self.accepted)} 条统一观测"},
                    {"name": "语义感知 LLM Agent", "status": "done" if len(model_harness["traces"]) >= 1 else "waiting", "detail": "模型证据审查"},
                    {"name": "本体演化 LLM Agent", "status": "done" if len(model_harness["traces"]) >= 2 else "waiting", "detail": "模型影响分析"},
                    {"name": "决策规划 LLM Agent", "status": "done" if len(model_harness["traces"]) >= 3 else "waiting", "detail": "模型方案审查"},
                    {"name": "行动执行 LLM Agent", "status": "done" if len(model_harness["traces"]) >= 4 else "waiting", "detail": "模型原子操作编排"},
                    {"name": "安全校验、人工审核与提交", "status": "attention" if pending_count else ("done" if facts or proposals else "waiting"), "detail": f"{pending_count} 项待处理"},
                ],
                "audit_log": self.audit_log[:100],
            }


APP = PrototypeState()


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB), **kwargs)

    def _json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/state":
            self._json(APP.report())
            return
        if path == "/api/scenarios":
            self._json({"scenarios": scenario_catalog()})
            return
        super().do_GET()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        try:
            if path == "/api/action/reset":
                APP.reset(payload.get("scenario_key", "full_update"))
            elif path == "/api/action/receive":
                APP.receive_next_batch(int(payload.get("size", 4)))
            elif path == "/api/action/run":
                APP.run_all()
            elif path == "/api/action/run-llm":
                APP.run_llm_agents()
            elif path == "/api/action/approve-fact":
                APP.approve_fact(str(payload["fact_id"]))
            elif path == "/api/action/reject-fact":
                APP.reject_fact(str(payload["fact_id"]), str(payload.get("reason", "")))
            elif path == "/api/action/approve-evolution":
                APP.approve_evolution(str(payload["proposal_id"]))
            elif path == "/api/action/reject-evolution":
                APP.reject_evolution(str(payload["proposal_id"]), str(payload.get("reason", "")))
            elif path == "/api/action/rollback":
                APP.rollback(int(payload["version"]))
            else:
                self._json({"error": "未知操作"}, 404)
                return
            self._json(APP.report())
        except Exception as exc:
            self._json({"error": str(exc), "state": APP.report()}, 400)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")


def main() -> None:
    host = os.environ.get("ODKG_WORKBENCH_HOST", "127.0.0.1")
    port = int(os.environ.get("ODKG_WORKBENCH_PORT", "8876"))
    print(f"Prototype software: http://{host}:{port}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
