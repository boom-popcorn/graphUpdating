"""Harness orchestration for the multi-agent update chain."""

from __future__ import annotations

from dataclasses import dataclass

from .agents import (
    ActionExecutionAgent,
    ConsistencyValidationAgent,
    DecisionPlanningAgent,
    OntologyEvolutionAgent,
    SemanticPerceptionAgent,
)
from .models import ObservationEvent, ValidationFeedback, WorkflowResult, WorkflowStatus
from .ontology import MaritimeGraphRepository


@dataclass(frozen=True)
class HarnessConfig:
    max_planning_attempts: int = 2

    def __post_init__(self) -> None:
        if self.max_planning_attempts < 1:
            raise ValueError("max_planning_attempts must be at least 1")


class UpdateHarness:
    """Coordinate agents without allowing any agent to bypass validation."""

    def __init__(
        self,
        repository: MaritimeGraphRepository,
        semantic_agent: SemanticPerceptionAgent | None = None,
        evolution_agent: OntologyEvolutionAgent | None = None,
        planning_agent: DecisionPlanningAgent | None = None,
        validation_agent: ConsistencyValidationAgent | None = None,
        execution_agent: ActionExecutionAgent | None = None,
        config: HarnessConfig | None = None,
    ) -> None:
        self.repository = repository
        self.semantic_agent = semantic_agent or SemanticPerceptionAgent()
        self.evolution_agent = evolution_agent or OntologyEvolutionAgent()
        self.planning_agent = planning_agent or DecisionPlanningAgent()
        self.validation_agent = validation_agent or ConsistencyValidationAgent()
        self.execution_agent = execution_agent or ActionExecutionAgent()
        self.config = config or HarnessConfig()

    def run(self, observations: list[ObservationEvent]) -> WorkflowResult:
        evolution_proposals = self.evolution_agent.analyze(observations)
        candidates = self.semantic_agent.perceive(observations)
        if not candidates:
            return WorkflowResult(
                status=WorkflowStatus.NO_CHANGE,
                graph_version=self.repository.current_version,
                observation_count=len(observations),
                candidate_fact_count=0,
                planning_attempts=0,
                evolution_proposals=evolution_proposals,
                message="No candidate facts satisfied the current rules; the graph was not changed.",
            )

        change_set = self.planning_agent.plan(candidates, self.repository.current_version)
        history: list[ValidationFeedback] = []
        for attempt in range(1, self.config.max_planning_attempts + 1):
            feedback = self.validation_agent.validate(
                self.repository,
                change_set,
                observations,
                attempt,
            )
            history.append(feedback)
            if feedback.valid:
                commit = self.execution_agent.execute(self.repository, change_set, observations)
                if not commit.applied:
                    return WorkflowResult(
                        status=WorkflowStatus.NO_CHANGE,
                        graph_version=commit.graph_version,
                        observation_count=len(observations),
                        candidate_fact_count=len(candidates),
                        planning_attempts=attempt,
                        change_set=change_set,
                        evolution_proposals=evolution_proposals,
                        validation_history=tuple(history),
                        message="The same evidence-backed update was already committed; no duplicate data was written.",
                    )
                return WorkflowResult(
                    status=WorkflowStatus.COMMITTED,
                    graph_version=commit.graph_version,
                    observation_count=len(observations),
                    candidate_fact_count=len(candidates),
                    planning_attempts=attempt,
                    change_set=change_set,
                    evolution_proposals=evolution_proposals,
                    validation_history=tuple(history),
                    message="The validated change set was committed atomically.",
                )
            if attempt < self.config.max_planning_attempts and feedback.repair_hints:
                try:
                    change_set = self.planning_agent.replan(change_set, feedback)
                except ValueError:
                    break
            else:
                break

        return WorkflowResult(
            status=WorkflowStatus.REJECTED,
            graph_version=self.repository.current_version,
            observation_count=len(observations),
            candidate_fact_count=len(candidates),
            planning_attempts=len(history),
            change_set=change_set,
            evolution_proposals=evolution_proposals,
            validation_history=tuple(history),
            message="The change set was rejected after consistency validation.",
        )
