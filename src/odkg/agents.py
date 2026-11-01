from __future__ import annotations

from collections.abc import Iterable

from rdflib import Graph
from rdflib.namespace import XSD

from .contracts import (
    CandidateFact,
    ChangeOperation,
    ChangeType,
    Observation,
    OntologyChangeSet,
    ProvenanceRecord,
    TransactionStatus,
    UpdateTransaction,
)
from .ontology import OntologyRepository
from .policy import EvolutionPolicy, EvolutionRuleEngine
from .state import UpdateEngine


class SemanticPerceptionAgent:
    name = "semantic_perception"

    def perceive(
        self, observations: Iterable[Observation], ontology_version: str
    ) -> list[CandidateFact]:
        return [
            CandidateFact(**observation.model_dump(), ontology_version=ontology_version)
            for observation in observations
        ]


class OntologyEvolutionAgent:
    name = "ontology_evolution"

    def __init__(self, policy: EvolutionPolicy | None = None) -> None:
        self.policy = policy or EvolutionPolicy()
        self.rules = EvolutionRuleEngine(self.policy)

    def propose(
        self, facts: list[CandidateFact], ontology: OntologyRepository
    ) -> OntologyChangeSet:
        operations: dict[tuple[ChangeType, str], ChangeOperation] = {}
        triggers: list[str] = []
        findings: list[str] = []
        requires_human_review = False
        for fact in facts:
            evaluation = self.rules.evaluate(fact)
            findings.extend(
                f"{fact.fact_id}: {finding}" for finding in evaluation.findings
            )
            if evaluation.decision == "reject":
                continue
            requires_human_review |= evaluation.decision == "review"
            triggers.append(fact.fact_id)
            self._ensure_class(
                operations, ontology, fact.subject_type, fact.confidence, fact.fact_id
            )
            if fact.object_type and not fact.object_is_literal:
                self._ensure_class(
                    operations, ontology, fact.object_type, fact.confidence, fact.fact_id
                )
            if not ontology.has_property(fact.predicate):
                change_type = (
                    ChangeType.ADD_DATA_PROPERTY
                    if fact.object_is_literal
                    else ChangeType.ADD_OBJECT_PROPERTY
                )
                operations[(change_type, fact.predicate)] = ChangeOperation(
                    operation=change_type,
                    target=fact.predicate,
                    domain=fact.subject_type,
                    range=(
                        fact.object_type
                        if not fact.object_is_literal
                        else fact.object_type or str(XSD.string)
                    ),
                    reason=f"schema gap observed in candidate fact {fact.fact_id}",
                    confidence=fact.confidence,
                )
        return OntologyChangeSet(
            base_version=ontology.version,
            operations=list(operations.values()),
            trigger_fact_ids=triggers,
            policy_id=self.policy.policy_id,
            requires_human_review=requires_human_review,
            rule_findings=findings,
        )

    @staticmethod
    def _ensure_class(
        operations: dict[tuple[ChangeType, str], ChangeOperation],
        ontology: OntologyRepository,
        class_iri: str,
        confidence: float,
        fact_id: str,
    ) -> None:
        if not ontology.has_class(class_iri):
            operations[(ChangeType.ADD_CLASS, class_iri)] = ChangeOperation(
                operation=ChangeType.ADD_CLASS,
                target=class_iri,
                reason=f"new class observed in candidate fact {fact_id}",
                confidence=confidence,
            )


class DecisionPlanningAgent:
    name = "decision_planning"

    def __init__(
        self,
        engine: UpdateEngine,
        auto_commit_threshold: float = 0.75,
        high_impact_operation_limit: int = 5,
    ) -> None:
        self.engine = engine
        self.auto_commit_threshold = auto_commit_threshold
        self.high_impact_operation_limit = high_impact_operation_limit

    def plan(
        self, change_set: OntologyChangeSet, facts: list[CandidateFact]
    ) -> tuple[UpdateTransaction, Graph, Graph]:
        transaction, ontology_preview, facts_preview = self.engine.prepare(
            change_set, facts
        )
        if transaction.status == TransactionStatus.PREPARED:
            minimum_confidence = min((fact.confidence for fact in facts), default=1.0)
            transaction.requires_human_review = (
                change_set.requires_human_review
                or minimum_confidence < self.auto_commit_threshold
                or len(change_set.operations) > self.high_impact_operation_limit
            )
        return transaction, ontology_preview, facts_preview


class ActionExecutionAgent:
    name = "action_execution"

    def __init__(self, engine: UpdateEngine) -> None:
        self.engine = engine

    def execute(
        self,
        transaction: UpdateTransaction,
        ontology_preview: Graph,
        facts_preview: Graph,
        human_approved: bool = False,
    ) -> UpdateTransaction:
        if transaction.requires_human_review and not human_approved:
            transaction.status = TransactionStatus.REJECTED
            transaction.rejection_reason = "human approval is required"
            return transaction
        return self.engine.commit(transaction, ontology_preview, facts_preview)


def provenance(
    agent: str, action: str, inputs: list[str], outputs: list[str], **metadata: object
) -> ProvenanceRecord:
    return ProvenanceRecord(
        agent=agent,
        action=action,
        inputs=inputs,
        outputs=outputs,
        metadata=metadata,
    )

