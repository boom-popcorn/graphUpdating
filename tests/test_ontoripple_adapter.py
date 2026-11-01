from pathlib import Path

from rdflib import URIRef
from rdflib.namespace import RDF

from odkg.adapters.ontoripple import OCH, OntoRippleRequest, build_command, to_och_graph
from odkg.demo import build_demo_orchestrator, demo_observations


def test_ontoripple_command_is_argument_safe() -> None:
    request = OntoRippleRequest(
        changes_kg=Path("artifacts/change data.ttl"),
        old_mapping=Path("artifacts/old mapping.ttl"),
        old_shapes=Path("artifacts/old shapes.ttl"),
        new_ontology=Path("artifacts/new ontology.ttl"),
        output_mapping=Path("artifacts/new mapping.ttl"),
        output_shapes=Path("artifacts/new shapes.ttl"),
    )
    command = build_command(request, python="python-test")
    assert command[:3] == ["python-test", "-m", "ontoripple"]
    assert str(Path("artifacts/change data.ttl")) in command
    assert "--yarrrml" not in command


def test_change_set_serializes_to_och_graph() -> None:
    orchestrator = build_demo_orchestrator()
    facts = orchestrator.perception.perceive(
        demo_observations(), orchestrator.engine.ontology.version
    )
    change_set = orchestrator.evolution.propose(facts, orchestrator.engine.ontology)
    graph = to_och_graph(change_set)
    funded_by = URIRef("https://example.org/odkg/fundedBy")
    assert any(graph.triples((None, RDF.type, OCH.AddObjectProperty)))
    assert any(graph.triples((None, OCH.addedObjectProperty, funded_by)))
    assert any(graph.triples((None, OCH.addedDomainToProperty, funded_by)))
    assert any(graph.triples((None, OCH.addedRangeToProperty, funded_by)))

