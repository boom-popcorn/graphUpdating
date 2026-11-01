from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Literal, Mapping, Protocol

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from .contracts import Observation
from .environment import configure_model_environment
from .orchestrator import UpdateOrchestrator


class RoleModelClient(Protocol):
    model_id: str

    def complete(
        self, role: str, system_prompt: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]: ...


class OpenAICompatibleRoleClient:
    """Structured chat client used independently by each Harness role."""

    def __init__(
        self,
        *,
        base_url: str,
        model_id: str,
        api_key: str | None = None,
        timeout_seconds: float = 180,
        max_tokens: int = 4096,
        thinking: str | None = None,
    ) -> None:
        endpoint = base_url.rstrip("/")
        if not endpoint.endswith("/chat/completions"):
            endpoint += "/chat/completions"
        self.endpoint = endpoint
        self.model_id = model_id
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.max_tokens = max_tokens
        self.thinking = thinking

    @classmethod
    def from_project_environment(cls, project_root: str) -> "OpenAICompatibleRoleClient":
        configure_model_environment(project_root)
        base_url = os.environ.get("ODKG_LLM_BASE_URL", "").strip()
        model = os.environ.get("ODKG_LLM_MODEL", "").strip()
        if not base_url or not model:
            raise RuntimeError(
                "LLM Harness is not configured: set ODKG_LLM_BASE_URL, "
                "ODKG_LLM_MODEL and ODKG_LLM_API_KEY"
            )
        return cls(
            base_url=base_url,
            model_id=model,
            api_key=os.environ.get("ODKG_LLM_API_KEY"),
            timeout_seconds=float(os.environ.get("ODKG_LLM_TIMEOUT", "180")),
            max_tokens=int(os.environ.get("ODKG_LLM_MAX_TOKENS", "4096")),
            thinking=os.environ.get("ODKG_LLM_THINKING"),
        )

    def complete(
        self, role: str, system_prompt: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        body: dict[str, Any] = {
            "model": self.model_id,
            "temperature": 0,
            "max_tokens": self.max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False, sort_keys=True),
                },
            ],
            "metadata": {"harness_role": role},
        }
        if self.thinking in {"enabled", "disabled"}:
            body["thinking"] = {"type": self.thinking}
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                result = json.loads(response.read().decode("utf-8"))
            content = result["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"{role} model returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"{role} model call failed: {type(exc).__name__}") from exc
        if isinstance(content, Mapping):
            return content
        if not isinstance(content, str):
            raise RuntimeError(f"{role} model response is not JSON text")
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            start = cleaned.find("{")
            if start < 0:
                raise RuntimeError(f"{role} model response is invalid JSON") from exc
            try:
                parsed, _ = json.JSONDecoder().raw_decode(cleaned[start:])
            except json.JSONDecodeError as nested:
                raise RuntimeError(f"{role} model response is invalid JSON") from nested
        if not isinstance(parsed, Mapping):
            raise RuntimeError(f"{role} model response must be an object")
        return parsed


class AgentDecision(BaseModel):
    decision: Literal["accept", "review", "reject"]
    rationale: str = Field(min_length=1)
    selected_indices: list[int] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    candidate_facts: list[dict[str, Any]] = Field(default_factory=list)
    evolution_proposals: list[dict[str, Any]] = Field(default_factory=list)
    action_plan: list[dict[str, Any]] = Field(default_factory=list)
    operations: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("recommendations", mode="before")
    @classmethod
    def normalize_recommendations(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            value = [value]
        return [
            item if isinstance(item, str) else json.dumps(item, ensure_ascii=False, sort_keys=True)
            for item in value
        ]

    @model_validator(mode="before")
    @classmethod
    def recover_misplaced_artifacts(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        recommendations = data.get("recommendations")
        if not data.get("evolution_proposals") and isinstance(recommendations, list):
            proposals = [
                item for item in recommendations
                if isinstance(item, Mapping) and "kind" in item and "source_field" in item
            ]
            if proposals:
                data["evolution_proposals"] = proposals
        return data


class AgentTrace(BaseModel):
    role: str
    model_id: str
    prompt_version: str
    started_at: datetime
    completed_at: datetime
    input_sha256: str
    decision: AgentDecision


class HarnessResult(BaseModel):
    status: Literal["previewed", "committed", "rejected", "awaiting_human_approval"]
    model_id: str
    observations: list[dict[str, Any]]
    pipeline: dict[str, Any]
    traces: list[AgentTrace]
    agent_artifacts: dict[str, Any] = Field(default_factory=dict)


class LLMRoleAgent:
    def __init__(
        self,
        role: str,
        prompt_version: str,
        system_prompt: str,
        client: RoleModelClient,
    ) -> None:
        self.role = role
        self.prompt_version = prompt_version
        self.system_prompt = system_prompt
        self.client = client

    def decide(self, payload: Mapping[str, Any]) -> AgentTrace:
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        started = datetime.now(timezone.utc)
        active_payload: Mapping[str, Any] = payload
        for attempt in range(2):
            try:
                raw = self.client.complete(self.role, self.system_prompt, active_payload)
                decision = AgentDecision.model_validate(raw)
                break
            except (RuntimeError, ValidationError) as exc:
                if attempt:
                    raise
                active_payload = {
                    **payload,
                    "_response_correction": (
                        "上一次输出无法解析。请严格只返回一个完整 JSON 对象；"
                        "recommendations 必须是字符串数组，结构化内容放入对应字段。"
                    ),
                    "_validation_error": str(exc)[:1200],
                }
        return AgentTrace(
            role=self.role,
            model_id=self.client.model_id,
            prompt_version=self.prompt_version,
            started_at=started,
            completed_at=datetime.now(timezone.utc),
            input_sha256=sha256(serialized).hexdigest(),
            decision=decision,
        )


PERCEPTION_PROMPT = """你是海事知识图谱的语义感知大模型智能体，是语义事实的唯一业务推理来源。
请直接分析输入的标准化 AIS、雷达、视频、遥感、气象海况和海事文本事件，完成变化检测、
时空对齐、跨模态关联与候选事实构造。不得假定工具已经生成候选事实，也不得虚构输入中不存在
的证据。返回 JSON：decision、rationale、recommendations、candidate_facts。
decision 只能是 accept、review、reject。candidate_facts 中每项必须包含 subject、predicate、object、
subject_type、object_type、object_is_literal、confidence、evidence；evidence 每项至少包含 source_id
和 modality，source_id 必须来自输入 event_id。IRI 使用提供的 ontology_namespace 和 resource_namespace。
只有以 http:// 或 https:// 开头的 object 才能设置 object_is_literal=false；POINT、状态文本、数值等
都必须设置 object_is_literal=true。
若证据不足则返回 review 或 reject，不要用固定阈值代替语义判断。所有说明文字使用简体中文。"""

EVOLUTION_PROMPT = """你是海事知识图谱的本体演化大模型智能体。
根据原始事件、当前本体摘要和语义感知候选事实，识别数据、本体、映射、规则和行动五类演化需求，
完成本体影响分析与语义映射调整建议。返回 JSON：decision、rationale、recommendations、
evolution_proposals。每项建议包含 kind、source_field、source_modalities、suggested_term、
suggested_domain、suggested_datatype、rationale、evidence_ids。kind 为 add_property、add_class、
add_relation 或 update_mapping。你只能提出建议，不能提交本体。高影响或含义不明确时选择 review。
recommendations 只能放简短的中文字符串，不要把演化建议对象重复放入 recommendations。
所有说明文字使用简体中文。"""

PLANNING_PROMPT = """你是海事知识图谱的决策规划大模型智能体。
根据语义事实、本体演化建议、当前图谱版本和前序智能体判断，完成规则匹配、影响分析、更新方案生成、
一致性风险预判与异常反馈规划。返回 JSON：decision、rationale、recommendations、selected_indices、
action_plan。selected_indices 指定应进入更新方案的候选事实下标；action_plan 每项说明 action、
fact_index、reason、preconditions。不得绕过后续 OWL/SHACL 校验。accept 仅表示可以交给行动执行智能体
编排，不代表允许写图。所有说明文字使用简体中文。"""

EXECUTION_PROMPT = """你是海事知识图谱的行动执行大模型智能体。
根据决策方案把候选事实编排为受控原子操作，但不得直接连接数据库或写入图谱。
返回 JSON：decision、rationale、recommendations、selected_indices、operations。
selected_indices 是最终允许进入受控事务的候选事实下标。operations 每项包含 operation、fact_index、
reason、preconditions；operation 只能是 create_object、update_property、create_relation、create_event、
invalidate_fact 或 rollback。后续确定性工具将负责类型检查、OWL/SHACL 校验、人工审批和原子提交。
所有说明文字使用简体中文。"""


class LLMMultiAgentHarness:
    """Model-driven reasoning roles around a deterministic validation/commit core."""

    def __init__(self, orchestrator: UpdateOrchestrator, client: RoleModelClient) -> None:
        self.orchestrator = orchestrator
        self.client = client
        self.perception = LLMRoleAgent(
            "semantic_perception", "maritime-perception-v1", PERCEPTION_PROMPT, client
        )
        self.evolution = LLMRoleAgent(
            "ontology_evolution", "maritime-evolution-v1", EVOLUTION_PROMPT, client
        )
        self.planning = LLMRoleAgent(
            "decision_planning", "maritime-planning-v1", PLANNING_PROMPT, client
        )
        self.execution = LLMRoleAgent(
            "action_execution", "maritime-execution-v1", EXECUTION_PROMPT, client
        )

    def run(
        self,
        events: list[dict[str, Any]],
        *,
        dry_run: bool = True,
        human_approved: bool = False,
    ) -> HarnessResult:
        traces: list[AgentTrace] = []
        perception_trace = self.perception.decide(
            {
                "events": events,
                "ontology_version": self.orchestrator.engine.ontology.version,
                "ontology_namespace": "https://example.org/maritime-kg/ontology/",
                "resource_namespace": "https://example.org/maritime-kg/resource/",
            }
        )
        traces.append(perception_trace)
        if perception_trace.decision.decision == "reject":
            return self._rejected([], traces, "语义感知智能体拒绝生成候选事实")
        candidates = []
        for item in perception_trace.decision.candidate_facts:
            normalized = dict(item)
            object_value = normalized.get("object")
            if (
                not normalized.get("object_is_literal", False)
                and not (
                    isinstance(object_value, str)
                    and object_value.startswith(("http://", "https://"))
                )
            ):
                normalized["object_is_literal"] = True
            candidates.append(Observation.model_validate(normalized))
        if not candidates:
            return self._rejected([], traces, "语义感知智能体没有生成有证据支持的候选事实")

        evolution_trace = self.evolution.decide(
            {
                "ontology_version": self.orchestrator.engine.ontology.version,
                "events": events,
                "candidate_facts": self._observations(candidates),
            }
        )
        traces.append(evolution_trace)
        if evolution_trace.decision.decision == "reject":
            return self._rejected(candidates, traces, "本体演化智能体拒绝当前语义方案")

        planning_trace = self.planning.decide(
            {
                "graph_version": self.orchestrator.engine.ontology.version,
                "candidate_facts": self._observations(candidates),
                "evolution_proposals": evolution_trace.decision.evolution_proposals,
                "prior_agent_decisions": [
                    trace.model_dump(mode="json") for trace in traces
                ],
            }
        )
        traces.append(planning_trace)
        if planning_trace.decision.decision == "reject":
            return self._rejected(candidates, traces, "决策规划智能体拒绝更新方案")
        candidates = self._select(candidates, planning_trace.decision.selected_indices)

        execution_trace = self.execution.decide(
            {
                "candidate_facts": self._observations(candidates),
                "action_plan": planning_trace.decision.action_plan,
                "ontology_version": self.orchestrator.engine.ontology.version,
            }
        )
        traces.append(execution_trace)
        if execution_trace.decision.decision == "reject":
            return self._rejected(candidates, traces, "行动执行智能体拒绝原子操作编排")
        candidates = self._select(candidates, execution_trace.decision.selected_indices)

        preview = self.orchestrator.run(candidates, dry_run=True).model_dump(mode="json")
        artifacts = {
            "evolution_proposals": evolution_trace.decision.evolution_proposals,
            "action_plan": planning_trace.decision.action_plan,
            "operations": execution_trace.decision.operations,
        }
        if dry_run:
            status = "previewed"
        elif not human_approved:
            status = "awaiting_human_approval"
        else:
            committed = self.orchestrator.run(
                candidates, human_approved=True
            ).model_dump(mode="json")
            return HarnessResult(
                status="committed"
                if committed["transaction"]["status"] == "committed"
                else "rejected",
                model_id=self.client.model_id,
                observations=self._observations(candidates),
                pipeline=committed,
                traces=traces,
                agent_artifacts=artifacts,
            )
        return HarnessResult(
            status=status,
            model_id=self.client.model_id,
            observations=self._observations(candidates),
            pipeline=preview,
            traces=traces,
            agent_artifacts=artifacts,
        )

    @staticmethod
    def _select(items: list[Observation], indices: list[int]) -> list[Observation]:
        if not indices:
            return items
        unique = list(dict.fromkeys(indices))
        # Models frequently enumerate human-facing lists from 1. Normalize that
        # representation at the Harness boundary without changing any fact.
        if unique and 0 not in unique and all(1 <= index <= len(items) for index in unique):
            unique = [index - 1 for index in unique]
        valid = [index for index in unique if 0 <= index < len(items)]
        if not valid:
            raise ValueError(
                f"大模型选择的候选编号无效：{indices}，当前只有 {len(items)} 个候选事实"
            )
        return [items[index] for index in valid]

    @staticmethod
    def _observations(items: list[Observation]) -> list[dict[str, Any]]:
        return [item.model_dump(mode="json") for item in items]

    def _rejected(
        self, candidates: list[Observation], traces: list[AgentTrace], reason: str
    ) -> HarnessResult:
        return HarnessResult(
            status="rejected",
            model_id=self.client.model_id,
            observations=self._observations(candidates),
            pipeline={"rejection_reason": reason},
            traces=traces,
            agent_artifacts={},
        )
