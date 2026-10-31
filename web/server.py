"""Interactive prototype server for the maritime knowledge-update workbench."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from rdflib import Graph

from maritime_kg.agents import KNOWN_PAYLOAD_FIELDS, OntologyEvolutionAgent
from maritime_kg.harness import UpdateHarness
from maritime_kg.models import CandidateFact, WorkflowStatus
from maritime_kg.ontology import MaritimeGraphRepository
from maritime_kg.pipeline import resolve_vessel_id
from maritime_kg.scenarios import build_scenario, scenario_catalog

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
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
        assert self.harness is not None
        result = self.harness.prepare(self.accepted)
        self.last_result = result
        for proposal in result.evolution_proposals:
            if proposal.proposal_id not in self.proposal_reviews:
                self.proposal_reviews[proposal.proposal_id] = {
                    "proposal": proposal,
                    "status": "pending",
                    "reviewed_at": None,
                    "reason": "",
                    "generated_version": None,
                }
                self._audit("本体演化", "生成建议", f"发现未映射字段 {proposal.source_field}")
        if result.change_set is not None:
            for fact in result.change_set.candidate_facts:
                if fact.fact_id not in self.fact_reviews:
                    self.fact_reviews[fact.fact_id] = {
                        "fact": fact,
                        "status": "pending",
                        "created_at": _now().isoformat(),
                        "reviewed_at": None,
                        "reason": "",
                        "validation": result.validation_history,
                        "graph_version": None,
                    }
                    vessel_id = fact.subject.rsplit("/", 1)[-1]
                    self._audit("知识更新", "生成候选事实", f"目标 {vessel_id} 的异常悬停状态等待审核")

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
            pending_facts = sum(item["status"] == "pending" for item in self.fact_reviews.values())
            pending_ontology = sum(item["status"] == "pending" for item in self.proposal_reviews.values())
            self.last_message = (
                f"已接收 {len(batch)} 条数据并完成关联、推理与一致性预检。"
                f"当前有 {pending_facts + pending_ontology} 项等待人工审核。"
            )
            self._audit("数据接入", "处理批次", f"接收 {len(accepted)} 条，重复 {len(duplicates)} 条")

    def run_all(self) -> None:
        with self.lock:
            if self.cursor < len(self.events):
                self.receive_next_batch(len(self.events) - self.cursor)
            else:
                self.last_message = "当前场景的数据已经全部接收并完成分析。"

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
            config = self.harness.semantic_agent.processor.config if self.harness else None
            workflow_status = self.last_result.status.value if self.last_result else "waiting"
            targets = self._targets()
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
                    {"id": "hovering-rule-v1", "name": "异常悬停识别", "enabled": True,
                     "expression": f"速度 ≤ {config.speed_threshold_mps} m/s，持续 ≥ {config.minimum_duration_seconds // 60} 分钟，且至少 {config.minimum_modalities} 种传感来源",
                     "last_result": f"生成 {len(facts)} 条候选事实"},
                    {"id": "cross-modal-rule-v1", "name": "跨模态身份关联", "enabled": True,
                     "expression": "AIS MMSI 与雷达 correlated_mmsi 指向同一目标",
                     "last_result": f"已关联 {len(targets)} 个目标"},
                ],
                "pipeline": [
                    {"name": "多模态数据接入", "status": "done" if self.accepted else "waiting", "detail": f"{len(self.accepted)} 条统一观测"},
                    {"name": "语义感知", "status": "done" if self.accepted else "waiting", "detail": f"{len(facts)} 条候选事实"},
                    {"name": "决策规划", "status": "done" if facts else "waiting", "detail": "生成原子变更集" if facts else "等待候选事实"},
                    {"name": "一致性校验", "status": "done" if any(item["validation"] for item in facts) else "waiting", "detail": "OWL / SHACL / 证据链"},
                    {"name": "人工审核与执行", "status": "attention" if pending_count else ("done" if facts or proposals else "waiting"), "detail": f"{pending_count} 项待处理"},
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
    host, port = "127.0.0.1", 8876
    print(f"Prototype software: http://{host}:{port}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
