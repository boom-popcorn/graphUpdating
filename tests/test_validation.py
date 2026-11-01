from odkg.contracts import Evidence, Observation, TransactionStatus
from odkg.demo import build_demo_orchestrator


def test_low_confidence_schema_change_fails_closed() -> None:
    orchestrator = build_demo_orchestrator()
    ex = "https://example.org/odkg/"
    observation = Observation(
        subject=f"{ex}suspicious_subject",
        predicate=f"{ex}uncertainRelation",
        object=f"{ex}uncertain_object",
        subject_type=f"{ex}Entity",
        object_type=f"{ex}Entity",
        confidence=0.40,
        evidence=[Evidence(source_id="weak-source", modality="text")],
    )
    result = orchestrator.run([observation])
    assert result.transaction.status == TransactionStatus.REJECTED
    assert result.transaction.validation is not None
    assert not result.transaction.validation.conforms
    assert any(
        issue.code == "UNDECLARED_PROPERTY"
        for issue in result.transaction.validation.issues
    )


def test_existing_property_domain_mismatch_is_rejected() -> None:
    orchestrator = build_demo_orchestrator()
    ex = "https://example.org/odkg/"
    observation = Observation(
        subject=f"{ex}entity_alpha",
        predicate=f"{ex}relatedTo",
        object=f"{ex}entity_beta",
        subject_type=f"{ex}Entity",
        object_type=f"{ex}Organization",
        confidence=0.92,
        evidence=[
            Evidence(source_id="source-a", modality="text"),
            Evidence(source_id="source-b", modality="structured"),
        ],
    )
    # Entity is compatible with relatedTo's Entity domain, so this is valid.
    valid = orchestrator.run([observation])
    assert valid.transaction.status == TransactionStatus.COMMITTED

    second = build_demo_orchestrator()
    incompatible = observation.model_copy(
        update={"subject_type": "https://example.org/odkg/UnknownType"}
    )
    rejected = second.run([incompatible])
    assert rejected.transaction.status == TransactionStatus.REJECTED
    assert any(
        issue.code == "DOMAIN_MISMATCH"
        for issue in rejected.transaction.validation.issues
    )

