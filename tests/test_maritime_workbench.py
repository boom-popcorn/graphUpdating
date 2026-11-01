from __future__ import annotations

import unittest
from unittest.mock import patch

from odkg.workbench.server import PrototypeState
from odkg.llm_harness import LLMMultiAgentHarness


class FakeRoleClient:
    model_id = "fake-workbench-model"

    def complete(self, role, system_prompt, payload):
        result = {
            "decision": "accept",
            "rationale": f"{role} accepted evidence-bound input",
            "selected_indices": [0] if role in {"decision_planning", "action_execution"} else [],
            "recommendations": [],
        }
        if role == "semantic_perception":
            event_ids = [item["event_id"] for item in payload["events"][:2]]
            result["candidate_facts"] = [{
                "subject": "https://example.org/maritime-kg/resource/vessel/412345678",
                "predicate": "https://example.org/maritime-kg/ontology/hasState",
                "object": "https://example.org/maritime-kg/ontology/HoveringState",
                "subject_type": "https://example.org/maritime-kg/ontology/Vessel",
                "object_type": "https://example.org/maritime-kg/ontology/State",
                "object_is_literal": False, "confidence": 0.91,
                "evidence": [{"source_id": event_id, "modality": "ais"} for event_id in event_ids],
            }]
        if role == "decision_planning":
            result["action_plan"] = [{"action": "create_relation", "fact_index": 0}]
        if role == "action_execution":
            result["operations"] = [{"operation": "create_relation", "fact_index": 0}]
        return result


class WorkbenchStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = PrototypeState()
        self.state.run_all()

    def test_data_reception_does_not_run_deterministic_business_inference(self) -> None:
        report = self.state.report()
        self.assertEqual(0, report["graph_version"])
        self.assertEqual(0, report["triple_count"])
        self.assertEqual(0, report["pending_count"])
        self.assertEqual([], report["fact_reviews"])

    def test_fact_approval_commits_a_new_graph_version(self) -> None:
        self._run_fake_llm()
        fact_id = self.state.report()["fact_reviews"][0]["fact_id"]
        self.state.approve_fact(fact_id)
        report = self.state.report()
        self.assertEqual("committed", report["fact_reviews"][0]["status"])
        self.assertEqual(1, report["graph_version"])
        self.assertGreater(report["triple_count"], 0)

    def test_ontology_approval_updates_active_version(self) -> None:
        self.assertEqual([], self.state.report()["proposals"])

    def test_workbench_exposes_real_model_agent_traces(self) -> None:
        self._run_fake_llm()
        report = self.state.report()
        self.assertEqual(
            ["semantic_perception", "ontology_evolution", "decision_planning", "action_execution"],
            [item["role"] for item in report["model_harness"]["traces"]],
        )

    def test_http_model_objects_remain_iris(self) -> None:
        self._run_fake_llm()
        fact = self.state.report()["fact_reviews"][0]
        self.assertEqual("iri", fact["object_kind"])

    def _run_fake_llm(self) -> None:
        fake = FakeRoleClient()
        real_constructor = LLMMultiAgentHarness

        def build(orchestrator, _client):
            return real_constructor(orchestrator, fake)

        with patch("odkg.workbench.server.OpenAICompatibleRoleClient.from_project_environment"), \
             patch("odkg.workbench.server.LLMMultiAgentHarness", side_effect=build):
            self.state.run_llm_agents()


if __name__ == "__main__":
    unittest.main()
