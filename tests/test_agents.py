from __future__ import annotations

import unittest
from pathlib import Path

from maritime_kg.agents import DecisionPlanningAgent, OntologyEvolutionAgent
from maritime_kg.constants import ONTOLOGY_IRI
from maritime_kg.harness import HarnessConfig, UpdateHarness
from maritime_kg.models import GraphOperation, ObjectKind, OperationKind, WorkflowStatus
from maritime_kg.ontology import MaritimeGraphRepository
from maritime_kg.pipeline import _stable_token
from maritime_kg.sample_data import build_sample_observations


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ONTOLOGY_PATH = PROJECT_ROOT / "ontology" / "maritime-v0.1.ttl"
SHAPES_PATH = PROJECT_ROOT / "ontology" / "maritime-v0.1-shapes.ttl"


def new_repository() -> MaritimeGraphRepository:
    return MaritimeGraphRepository(ONTOLOGY_PATH, SHAPES_PATH)


class FaultInjectingPlanningAgent(DecisionPlanningAgent):
    """Add one unsupported operation so the feedback/replanning path is exercised."""

    def plan(self, candidates, base_graph_version):
        change_set = super().plan(candidates, base_graph_version)
        fact = change_set.candidate_facts[0]
        invalid_operation = GraphOperation(
            operation_id=f"op-{_stable_token('unsupported', fact.fact_id)}",
            kind=OperationKind.UPDATE_PROPERTY,
            subject=fact.subject,
            predicate=f"{ONTOLOGY_IRI}temporaryRiskScore",
            object_value=0.7,
            object_kind=ObjectKind.LITERAL,
            evidence_ids=fact.evidence_ids,
        )
        return change_set.model_copy(update={"operations": (*change_set.operations, invalid_operation)})


class AgentWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.observations = build_sample_observations()

    def test_schema_drift_creates_reviewable_evolution_proposal(self) -> None:
        proposals = OntologyEvolutionAgent().analyze(self.observations)
        self.assertEqual(1, len(proposals))
        proposal = proposals[0]
        self.assertEqual("encrypted_message_frequency", proposal.source_field)
        self.assertTrue(proposal.requires_human_approval)
        self.assertEqual(f"{ONTOLOGY_IRI}encryptedMessageFrequency", proposal.suggested_term)

    def test_human_approval_creates_ontology_v02(self) -> None:
        proposals = OntologyEvolutionAgent().analyze(self.observations)
        target = PROJECT_ROOT / "output" / "test-ontology-v0.2.ttl"
        version = OntologyEvolutionAgent().approve(
            proposals[0],
            ONTOLOGY_PATH,
            target,
        )
        self.assertEqual("0.2.0", version)
        self.assertTrue(target.exists())
        self.assertIn("encryptedMessageFrequency", target.read_text(encoding="utf-8"))
        target.unlink()

    def test_harness_commits_valid_multi_agent_workflow(self) -> None:
        repository = new_repository()
        result = UpdateHarness(repository).run(self.observations)
        self.assertEqual(WorkflowStatus.COMMITTED, result.status)
        self.assertEqual(1, result.graph_version)
        self.assertEqual(1, result.planning_attempts)
        self.assertTrue(result.validation_history[0].valid)
        self.assertEqual(1, len(result.evolution_proposals))

    def test_no_candidate_fact_does_not_change_graph(self) -> None:
        normal_vessel_only = [
            observation
            for observation in self.observations
            if "412345678" in observation.event_id
        ]
        repository = new_repository()
        result = UpdateHarness(repository).run(normal_vessel_only)
        self.assertEqual(WorkflowStatus.NO_CHANGE, result.status)
        self.assertEqual(0, repository.current_version)
        self.assertEqual(0, len(repository.data_graph))

    def test_repeated_workflow_is_reported_as_no_change(self) -> None:
        repository = new_repository()
        harness = UpdateHarness(repository)
        first = harness.run(self.observations)
        triple_count = len(repository.data_graph)
        second = harness.run(self.observations)
        self.assertEqual(WorkflowStatus.COMMITTED, first.status)
        self.assertEqual(WorkflowStatus.NO_CHANGE, second.status)
        self.assertEqual(1, second.graph_version)
        self.assertEqual(triple_count, len(repository.data_graph))

    def test_validation_feedback_triggers_replanning_then_commit(self) -> None:
        repository = new_repository()
        harness = UpdateHarness(
            repository,
            planning_agent=FaultInjectingPlanningAgent(),
            config=HarnessConfig(max_planning_attempts=2),
        )
        result = harness.run(self.observations)
        self.assertEqual(WorkflowStatus.COMMITTED, result.status)
        self.assertEqual(2, result.planning_attempts)
        self.assertFalse(result.validation_history[0].valid)
        self.assertTrue(result.validation_history[1].valid)
        self.assertIn("remove-operation:", result.validation_history[0].repair_hints[0])
        self.assertNotIn(
            OperationKind.UPDATE_PROPERTY,
            {operation.kind for operation in result.change_set.operations},
        )


if __name__ == "__main__":
    unittest.main()

