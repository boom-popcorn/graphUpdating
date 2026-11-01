from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from rdflib import Graph, Namespace, RDF, URIRef

from ..contracts import ChangeType, OntologyChangeSet

OCH = Namespace("http://w3id.org/def/och#")


@dataclass(frozen=True, slots=True)
class PropagationReport:
    mapping_triples: int
    shape_triples: int
    stdout: str
    stderr: str
    output_mapping: Path
    output_shapes: Path


@dataclass(frozen=True, slots=True)
class OntoRippleRequest:
    changes_kg: Path
    old_mapping: Path
    old_shapes: Path
    new_ontology: Path
    output_mapping: Path
    output_shapes: Path
    yarrrml: bool = False


def build_command(request: OntoRippleRequest, python: str = "python") -> list[str]:
    """Build an argument-safe OntoRipple command without invoking a shell."""
    command = [
        python,
        "-m",
        "ontoripple",
        "--changes_kg_path",
        str(request.changes_kg),
        "--old_mapping_path",
        str(request.old_mapping),
        "--old_shacl_path",
        str(request.old_shapes),
        "--ontology_path",
        str(request.new_ontology),
        "--new_mappings_path",
        str(request.output_mapping),
        "--new_shapes_path",
        str(request.output_shapes),
    ]
    if request.yarrrml:
        command.append("--yarrrml")
    return command


def propagate(
    request: OntoRippleRequest,
    python: str = "python",
    timeout_seconds: int = 120,
) -> PropagationReport:
    """Run OntoRipple and verify that its RDF outputs are readable."""
    for source in (
        request.changes_kg,
        request.old_mapping,
        request.old_shapes,
        request.new_ontology,
    ):
        if not source.exists():
            raise FileNotFoundError(source)
    request.output_mapping.parent.mkdir(parents=True, exist_ok=True)
    request.output_shapes.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        build_command(request, python),
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
    if not request.output_mapping.exists() or not request.output_shapes.exists():
        raise RuntimeError("OntoRipple completed without producing declared outputs")
    mapping_graph = Graph().parse(request.output_mapping)
    shapes_graph = Graph().parse(request.output_shapes)
    return PropagationReport(
        mapping_triples=len(mapping_graph),
        shape_triples=len(shapes_graph),
        stdout=completed.stdout,
        stderr=completed.stderr,
        output_mapping=request.output_mapping,
        output_shapes=request.output_shapes,
    )


def to_och_graph(change_set: OntologyChangeSet) -> Graph:
    """Convert supported internal changes to OntoRipple's OCH RDF input."""
    graph = Graph()
    graph.bind("och", OCH)
    for index, operation in enumerate(change_set.operations):
        change = URIRef(f"urn:odkg:{change_set.change_set_id}:change:{index}")
        target = URIRef(operation.target)
        if operation.operation == ChangeType.ADD_CLASS:
            graph.add((change, RDF.type, OCH.AddClass))
            graph.add((change, OCH.addedClass, target))
        elif operation.operation == ChangeType.ADD_SUBCLASS:
            if not operation.parent:
                raise ValueError("add_subclass requires parent")
            graph.add((change, RDF.type, OCH.AddSubClass))
            graph.add((change, OCH.sourceAddSubClass, target))
            graph.add((change, OCH.targetAddSubClass, URIRef(operation.parent)))
        elif operation.operation in {
            ChangeType.ADD_OBJECT_PROPERTY,
            ChangeType.ADD_DATA_PROPERTY,
        }:
            is_object = operation.operation == ChangeType.ADD_OBJECT_PROPERTY
            graph.add(
                (change, RDF.type, OCH.AddObjectProperty if is_object else OCH.AddDataProperty)
            )
            graph.add(
                (change, OCH.addedObjectProperty if is_object else OCH.addedDataProperty, target)
            )
            if operation.domain:
                domain_change = URIRef(f"{change}:domain")
                graph.add((domain_change, OCH.addedDomainToProperty, target))
                graph.add((domain_change, OCH.addedDomain, URIRef(operation.domain)))
            if operation.range:
                range_change = URIRef(f"{change}:range")
                graph.add((range_change, OCH.addedRangeToProperty, target))
                graph.add(
                    (
                        range_change,
                        OCH.addedObjectRange if is_object else OCH.addedDataRange,
                        URIRef(operation.range),
                    )
                )
        else:
            raise ValueError(f"unsupported OntoRipple operation: {operation.operation}")
    return graph

