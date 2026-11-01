from rdflib import URIRef
from rdflib.namespace import OWL, RDF

from odkg.contracts import ChangeType, TransactionStatus
from odkg.demo import build_demo_orchestrator, demo_observations


def test_evolution_and_commit() -> None:
    orchestrator = build_demo_orchestrator()
    facts = orchestrator.perception.perceive(
        demo_observations(), orchestrator.engine.ontology.version
    )
    change_set = orchestrator.evolution.propose(facts, orchestrator.engine.ontology)
    types = {item.operation for item in change_set.operations}
    assert ChangeType.ADD_CLASS in types
    assert ChangeType.ADD_OBJECT_PROPERTY in types

    result = orchestrator.run(demo_observations())
    ex = "https://example.org/odkg/"
    assert result.transaction.status == TransactionStatus.COMMITTED
    assert result.transaction.validation and result.transaction.validation.conforms
    assert orchestrator.engine.ontology.version == "v0.2.0"
    triple = (URIRef(f"{ex}fundedBy"), RDF.type, OWL.ObjectProperty)
    assert triple in orchestrator.engine.ontology.graph


def test_rollback_restores_state() -> None:
    orchestrator = build_demo_orchestrator()
    result = orchestrator.run(demo_observations())
    assert orchestrator.engine.rollback_latest() == result.transaction.transaction_id
    assert orchestrator.engine.ontology.version == "v0.1.0"
    assert len(orchestrator.engine.facts) == 0

