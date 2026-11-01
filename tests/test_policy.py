from odkg.contracts import Evidence, Observation, TransactionStatus
from odkg.demo import build_demo_orchestrator


def _single_source_observation() -> Observation:
    ex = "https://example.org/odkg/"
    return Observation(
        subject=f"{ex}dataset_alpha",
        predicate=f"{ex}describedBy",
        object=f"{ex}org_alpha",
        subject_type=f"{ex}Dataset",
        object_type=f"{ex}Organization",
        confidence=0.96,
        evidence=[Evidence(source_id="only-source", modality="text")],
    )


def test_policy_routes_single_source_evolution_to_human_review() -> None:
    orchestrator = build_demo_orchestrator()
    result = orchestrator.run([_single_source_observation()])

    assert result.transaction.status == TransactionStatus.REJECTED
    assert result.transaction.requires_human_review
    assert result.transaction.change_set.policy_id == "generic-evolution-v1"
    assert "distinct source" in result.transaction.change_set.rule_findings[0]


def test_human_approval_allows_reviewed_evolution() -> None:
    orchestrator = build_demo_orchestrator()
    result = orchestrator.run(
        [_single_source_observation()], human_approved=True
    )
    assert result.transaction.status == TransactionStatus.COMMITTED


def test_dry_run_does_not_change_active_version() -> None:
    orchestrator = build_demo_orchestrator()
    result = orchestrator.run([_single_source_observation()], dry_run=True)
    assert result.transaction.status == TransactionStatus.PREPARED
    assert orchestrator.engine.ontology.version == "v0.1.0"
    assert len(orchestrator.engine.facts) == 0
