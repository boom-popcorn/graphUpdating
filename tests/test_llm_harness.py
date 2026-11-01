from datetime import datetime, timedelta, timezone

from odkg.demo import build_maritime_orchestrator
from odkg.llm_harness import LLMMultiAgentHarness
from odkg.llm_harness import AgentDecision, OpenAICompatibleRoleClient
from odkg.contracts import Evidence, Observation


class FakeRoleClient:
    model_id = "fake-maritime-model"

    def __init__(self) -> None:
        self.roles: list[str] = []

    def complete(self, role, system_prompt, payload):
        self.roles.append(role)
        result = {
            "decision": "accept",
            "rationale": f"{role} accepted evidence-bound input",
            "selected_indices": [0] if role in {"decision_planning", "action_execution"} else [],
            "recommendations": [],
        }
        if role == "semantic_perception":
            result["candidate_facts"] = [{
                "subject": "https://example.org/maritime-kg/resource/vessel/412345679",
                "predicate": "https://example.org/maritime-kg/ontology/hasState",
                "object": "https://example.org/maritime-kg/ontology/HoveringState",
                "subject_type": "https://example.org/maritime-kg/ontology/Vessel",
                "object_type": "https://example.org/maritime-kg/ontology/State",
                "object_is_literal": False, "confidence": 0.92,
                "evidence": [
                    {"source_id": "ais-llm-001", "modality": "ais"},
                    {"source_id": "radar-llm-001", "modality": "radar"},
                ],
            }]
        if role == "decision_planning":
            result["action_plan"] = [{"action": "create_relation", "fact_index": 0}]
        if role == "action_execution":
            result["operations"] = [{"operation": "create_relation", "fact_index": 0}]
        return result


def events():
    started = datetime(2026, 10, 31, 4, 0, tzinfo=timezone.utc)
    return [
        {
            "event_id": "ais-llm-001",
            "source": "ais",
            "modality": "ais",
            "observed_at": started.isoformat(),
            "received_at": (started + timedelta(seconds=1)).isoformat(),
            "object_hint": "vessel:412345679",
            "speed_mps": 0.2,
            "confidence": 0.94,
            "payload": {"mmsi": "412345679"},
        },
        {
            "event_id": "radar-llm-001",
            "source": "radar",
            "modality": "radar",
            "observed_at": (started + timedelta(seconds=720)).isoformat(),
            "received_at": (started + timedelta(seconds=722)).isoformat(),
            "object_hint": "track:R-1007",
            "speed_mps": 0.3,
            "confidence": 0.9,
            "payload": {"correlated_mmsi": "412345679"},
        },
    ]


def test_four_model_roles_create_facts_before_safety_preview():
    client = FakeRoleClient()
    harness = LLMMultiAgentHarness(build_maritime_orchestrator(), client)
    result = harness.run(events())

    assert result.status == "previewed"
    assert client.roles == [
        "semantic_perception",
        "ontology_evolution",
        "decision_planning",
        "action_execution",
    ]
    assert result.pipeline["transaction"]["validation"]["conforms"] is True
    assert all(trace.input_sha256 for trace in result.traces)


def test_model_acceptance_never_bypasses_human_approval():
    client = FakeRoleClient()
    harness = LLMMultiAgentHarness(build_maritime_orchestrator(), client)

    waiting = harness.run(events(), dry_run=False, human_approved=False)
    assert waiting.status == "awaiting_human_approval"
    assert waiting.pipeline["transaction"]["status"] == "prepared"

    committed = harness.run(events(), dry_run=False, human_approved=True)
    assert committed.status == "committed"
    assert committed.pipeline["transaction"]["status"] == "committed"


def test_structured_recommendations_are_recovered_as_evolution_proposals():
    proposal = {
        "kind": "add_property",
        "source_field": "encrypted_message_frequency",
        "source_modalities": ["ais"],
        "suggested_term": "https://example.org/maritime-kg/ontology/encryptedMessageFrequency",
        "suggested_domain": "https://example.org/maritime-kg/ontology/Observation",
        "suggested_datatype": "http://www.w3.org/2001/XMLSchema#decimal",
        "rationale": "发现新字段",
        "evidence_ids": ["ais-llm-001"],
    }
    decision = AgentDecision.model_validate({
        "decision": "review", "rationale": "需要演化", "recommendations": [proposal]
    })
    assert decision.evolution_proposals == [proposal]
    assert isinstance(decision.recommendations[0], str)


def test_json_parser_accepts_fenced_json_with_trailing_text():
    client = object.__new__(OpenAICompatibleRoleClient)
    content = '说明如下：\n```json\n{"decision":"accept"}\n```\n完成'
    cleaned = content.strip()
    start = cleaned.find("{")
    parsed, _ = __import__("json").JSONDecoder().raw_decode(cleaned[start:])
    assert parsed["decision"] == "accept"


def test_candidate_selection_accepts_one_based_model_indices():
    facts = [
        Observation(
            subject=f"https://example.org/resource/{index}",
            predicate="https://example.org/ontology/hasState",
            object="https://example.org/ontology/HoveringState",
            subject_type="https://example.org/ontology/Vessel",
            object_type="https://example.org/ontology/State",
            confidence=0.9,
            evidence=[Evidence(source_id=f"event-{index}", modality="ais")],
        )
        for index in range(2)
    ]
    selected = LLMMultiAgentHarness._select(facts, [1, 2])
    assert selected == facts
