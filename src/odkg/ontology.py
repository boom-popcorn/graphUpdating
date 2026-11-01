from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rdflib import Graph, URIRef
from rdflib.namespace import OWL, RDF, RDFS

from .contracts import ChangeOperation, ChangeType, OntologyChangeSet


def clone_graph(graph: Graph) -> Graph:
    clone = Graph()
    for prefix, namespace in graph.namespaces():
        clone.bind(prefix, namespace)
    for triple in graph:
        clone.add(triple)
    return clone


def next_minor(version: str) -> str:
    major, minor, patch = (int(part) for part in version.lstrip("v").split("."))
    return f"v{major}.{minor + 1}.{patch}"


@dataclass(slots=True)
class OntologySnapshot:
    version: str
    graph: Graph
    change_set_id: str | None


class OntologyRepository:
    def __init__(self, graph: Graph, version: str = "v0.1.0") -> None:
        self.graph = graph
        self.version = version
        self._history: list[OntologySnapshot] = []

    @classmethod
    def from_file(cls, path: str | Path, version: str = "v0.1.0") -> "OntologyRepository":
        graph = Graph()
        graph.parse(Path(path), format="turtle")
        return cls(graph, version)

    def has_class(self, iri: str) -> bool:
        return (URIRef(iri), RDF.type, OWL.Class) in self.graph

    def has_property(self, iri: str) -> bool:
        node = URIRef(iri)
        return any(
            (node, RDF.type, property_type) in self.graph
            for property_type in (OWL.ObjectProperty, OWL.DatatypeProperty, RDF.Property)
        )

    def preview(self, change_set: OntologyChangeSet) -> Graph:
        if change_set.base_version != self.version:
            raise ValueError(
                f"stale change set: expected {self.version}, got {change_set.base_version}"
            )
        graph = clone_graph(self.graph)
        for operation in change_set.operations:
            self._apply(graph, operation)
        return graph

    def commit(self, graph: Graph, change_set_id: str) -> str:
        self._history.append(
            OntologySnapshot(self.version, clone_graph(self.graph), change_set_id)
        )
        self.graph = graph
        self.version = next_minor(self.version)
        return self.version

    @staticmethod
    def _apply(graph: Graph, operation: ChangeOperation) -> None:
        target = URIRef(operation.target)
        if operation.operation == ChangeType.ADD_CLASS:
            graph.add((target, RDF.type, OWL.Class))
        elif operation.operation == ChangeType.ADD_OBJECT_PROPERTY:
            graph.add((target, RDF.type, OWL.ObjectProperty))
            if operation.domain:
                graph.add((target, RDFS.domain, URIRef(operation.domain)))
            if operation.range:
                graph.add((target, RDFS.range, URIRef(operation.range)))
        elif operation.operation == ChangeType.ADD_DATA_PROPERTY:
            graph.add((target, RDF.type, OWL.DatatypeProperty))
            if operation.domain:
                graph.add((target, RDFS.domain, URIRef(operation.domain)))
            if operation.range:
                graph.add((target, RDFS.range, URIRef(operation.range)))
        elif operation.operation == ChangeType.ADD_SUBCLASS:
            if not operation.parent:
                raise ValueError("add_subclass requires parent")
            graph.add((target, RDFS.subClassOf, URIRef(operation.parent)))
        else:
            raise ValueError(f"unsupported operation: {operation.operation}")

