from __future__ import annotations

from .agents import (
    ActionExecutionAgent,
    DecisionPlanningAgent,
    OntologyEvolutionAgent,
    SemanticPerceptionAgent,
    provenance,
)
from .contracts import Observation, PipelineResult, TransactionStatus
from .state import UpdateEngine
from .storage import ArtifactStore
from .policy import EvolutionPolicy
from .graph_store import GraphSnapshotStore


class UpdateOrchestrator:
    """Framework-neutral Harness contract for one update cycle."""

    def __init__(
        self,
        engine: UpdateEngine,
        artifact_store: ArtifactStore | None = None,
        policy: EvolutionPolicy | None = None,
        graph_store: GraphSnapshotStore | None = None,
    ) -> None:
        self.engine = engine
        self.artifact_store = artifact_store
        self.policy = policy or EvolutionPolicy()
        self.graph_store = graph_store
        self.perception = SemanticPerceptionAgent()
        self.evolution = OntologyEvolutionAgent(self.policy)
        self.planning = DecisionPlanningAgent(
            engine,
            auto_commit_threshold=self.policy.auto_commit_confidence,
            high_impact_operation_limit=self.policy.max_auto_commit_operations,
        )
        self.execution = ActionExecutionAgent(engine)
        if self.graph_store is not None and self.graph_store.status().active_version is None:
            self.graph_store.publish_snapshot(
                self.engine.ontology.graph,
                self.engine.facts,
                self.engine.ontology.version,
                "bootstrap",
            )

    def run(
        self,
        observations: list[Observation],
        human_approved: bool = False,
        dry_run: bool = False,
    ) -> PipelineResult:
        records = []
        facts = self.perception.perceive(observations, self.engine.ontology.version)
        records.append(
            provenance(
                self.perception.name,
                "construct_candidate_facts",
                [e.source_id for o in observations for e in o.evidence],
                [fact.fact_id for fact in facts],
                fact_count=len(facts),
            )
        )

        change_set = self.evolution.propose(facts, self.engine.ontology)
        records.append(
            provenance(
                self.evolution.name,
                "propose_ontology_changes",
                [fact.fact_id for fact in facts],
                [change_set.change_set_id],
                operation_count=len(change_set.operations),
                policy_id=change_set.policy_id,
                requires_human_review=change_set.requires_human_review,
                rule_findings=change_set.rule_findings,
            )
        )

        transaction, ontology_preview, facts_preview = self.planning.plan(
            change_set, facts
        )
        records.append(
            provenance(
                self.planning.name,
                "validate_and_prepare_transaction",
                [change_set.change_set_id],
                [transaction.transaction_id],
                conforms=bool(transaction.validation and transaction.validation.conforms),
                requires_human_review=transaction.requires_human_review,
            )
        )

        if transaction.status == TransactionStatus.PREPARED and not dry_run:
            transaction = self.execution.execute(
                transaction,
                ontology_preview,
                facts_preview,
                human_approved=human_approved,
            )
            if (
                transaction.status == TransactionStatus.COMMITTED
                and self.graph_store is not None
            ):
                try:
                    report = self.graph_store.publish_snapshot(
                        self.engine.ontology.graph,
                        self.engine.facts,
                        transaction.target_version or transaction.base_version,
                        transaction.transaction_id,
                    )
                    records.append(
                        provenance(
                            self.execution.name,
                            "publish_graph_snapshot",
                            [transaction.transaction_id],
                            [report.active_version or ""],
                            **report.to_dict(),
                        )
                    )
                except Exception as exc:
                    self.engine.rollback_latest()
                    transaction.status = TransactionStatus.ROLLED_BACK
                    transaction.rejection_reason = (
                        f"graph backend publication failed: {type(exc).__name__}: {exc}"
                    )
            records.append(
                provenance(
                    self.execution.name,
                    "execute_transaction",
                    [transaction.transaction_id],
                    [transaction.target_version or transaction.base_version],
                    status=transaction.status,
                )
            )

        if dry_run and transaction.status == TransactionStatus.PREPARED:
            feedback = "dry run prepared; no ontology or knowledge graph state was changed"
        elif transaction.status == TransactionStatus.COMMITTED:
            feedback = "update committed; monitor runtime indicators for the next evolution trigger"
        elif transaction.status == TransactionStatus.ROLLED_BACK:
            feedback = "graph publication failed; ontology and KG transaction rolled back"
        elif transaction.requires_human_review:
            feedback = "transaction paused or rejected pending human review"
        else:
            feedback = "validation feedback returned to ontology evolution and planning"

        result = PipelineResult(
            transaction=transaction,
            feedback=feedback,
            provenance=records,
        )
        if self.artifact_store is not None:
            self.artifact_store.record_pipeline_result(
                result,
                self.engine.ontology.graph,
                self.engine.facts,
                self.engine.shapes,
                self.policy,
            )
        return result

