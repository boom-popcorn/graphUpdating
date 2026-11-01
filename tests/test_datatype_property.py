from rdflib import Literal, URIRef
from rdflib.namespace import OWL, RDF, RDFS, XSD

from odkg.contracts import Evidence, Observation, TransactionStatus
from odkg.demo import build_demo_orchestrator


def test_literal_gap_creates_typed_datatype_property() -> None:
    orchestrator = build_demo_orchestrator()
    ex = "https://example.org/odkg/"
    observation = Observation(
        subject=f"{ex}project_alpha",
        predicate=f"{ex}displayName",
        object="Project Alpha",
        subject_type=f"{ex}Entity",
        object_is_literal=True,
        confidence=0.92,
        evidence=[
            Evidence(source_id="doc-typed", modality="text"),
            Evidence(source_id="catalog-typed", modality="structured"),
        ],
    )
    result = orchestrator.run([observation])
    predicate = URIRef(f"{ex}displayName")
    assert result.transaction.status == TransactionStatus.COMMITTED
    assert (predicate, RDF.type, OWL.DatatypeProperty) in orchestrator.engine.ontology.graph
    assert (predicate, RDFS.range, XSD.string) in orchestrator.engine.ontology.graph
    assert (
        URIRef(f"{ex}project_alpha"),
        predicate,
        Literal("Project Alpha", datatype=XSD.string),
    ) in orchestrator.engine.facts

