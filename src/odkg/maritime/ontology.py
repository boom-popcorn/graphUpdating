"""RDF/OWL storage and SHACL-guarded graph commits."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from pyshacl import validate
from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, XSD

from .constants import (
    ONTOLOGY_IRI,
    assertion_iri,
    area_iri,
    change_set_iri,
    graph_version_iri,
    observation_iri,
    state_snapshot_iri,
    track_iri,
    vessel_iri,
)
from .models import GraphChangeSet, GraphOperation, Modality, ObjectKind, ObservationEvent, OperationKind
from .pipeline import resolve_vessel_id


MKG = Namespace(ONTOLOGY_IRI)


class GraphValidationError(ValueError):
    pass


class GraphVersionConflict(ValueError):
    pass


@dataclass(frozen=True)
class CommitResult:
    applied: bool
    graph_version: int
    triple_count: int
    change_set_id: str


@dataclass(frozen=True)
class PreviewResult:
    conforms: bool
    report: str
    triple_count: int
    graph: Graph


class MaritimeGraphRepository:
    """In-memory prototype repository with atomic validation before commit."""

    def __init__(self, ontology_path: Path, shapes_path: Path, snapshot_dir: Path | None = None) -> None:
        self.ontology_graph = Graph()
        self.ontology_graph.parse(ontology_path, format="turtle")
        self.shapes_graph = Graph()
        self.shapes_graph.parse(shapes_path, format="turtle")
        self.data_graph = Graph()
        self._bind_namespaces(self.data_graph)
        self.current_version = 0
        self._committed_keys: set[str] = set()
        self._snapshots: dict[int, Graph] = {0: self._copy_data_graph()}
        self.snapshot_dir = snapshot_dir
        if self.snapshot_dir is not None:
            self._load_persisted_snapshots()

    @staticmethod
    def _bind_namespaces(graph: Graph) -> None:
        graph.bind("mkg", MKG)
        graph.bind("rdf", RDF)
        graph.bind("xsd", XSD)

    @staticmethod
    def _decimal(value: float) -> Literal:
        return Literal(Decimal(str(value)), datatype=XSD.decimal)

    @staticmethod
    def _datetime(value) -> Literal:
        return Literal(value.isoformat(), datatype=XSD.dateTime)

    @staticmethod
    def _object_term(value, kind: ObjectKind | None):
        if kind == ObjectKind.IRI:
            return URIRef(str(value))
        return Literal(value)

    def _copy_data_graph(self) -> Graph:
        trial = Graph()
        self._bind_namespaces(trial)
        for triple in self.data_graph:
            trial.add(triple)
        return trial

    def _add_observation(self, graph: Graph, observation: ObservationEvent) -> None:
        observation_node = URIRef(observation_iri(observation.event_id))
        vessel_node = URIRef(vessel_iri(resolve_vessel_id(observation)))
        observation_class = {
            Modality.AIS: MKG.AISObservation,
            Modality.RADAR: MKG.RadarObservation,
        }.get(observation.modality, MKG.Observation)

        graph.add((observation_node, RDF.type, observation_class))
        graph.add((vessel_node, RDF.type, MKG.Vessel))
        track_node = URIRef(track_iri(resolve_vessel_id(observation)))
        graph.add((track_node, RDF.type, MKG.Track))
        graph.add((vessel_node, MKG.hasTrack, track_node))
        graph.add((observation_node, MKG.eventId, Literal(observation.event_id)))
        graph.add((observation_node, MKG.observedAt, self._datetime(observation.observed_at)))
        graph.add((observation_node, MKG.receivedAt, self._datetime(observation.received_at)))
        graph.add((observation_node, MKG.describes, vessel_node))
        snapshot_node = URIRef(state_snapshot_iri(observation.event_id))
        graph.add((snapshot_node, RDF.type, MKG.State))
        graph.add((snapshot_node, MKG.snapshotAt, self._datetime(observation.observed_at)))
        graph.add((vessel_node, MKG.hasState, snapshot_node))
        area_id = observation.payload.get("area_id")
        if area_id:
            area_node = URIRef(area_iri(str(area_id)))
            graph.add((area_node, RDF.type, MKG.MaritimeArea))
            graph.add((vessel_node, MKG.locatedIn, area_node))
        graph.add((observation_node, MKG.confidence, self._decimal(observation.confidence)))
        graph.add((observation_node, MKG.schemaVersion, Literal(observation.schema_version)))
        if observation.speed_mps is not None:
            graph.add((observation_node, MKG.speedMps, self._decimal(observation.speed_mps)))
        if observation.course_deg is not None:
            graph.add((observation_node, MKG.courseDegrees, self._decimal(observation.course_deg)))
        if observation.position is not None:
            graph.add((observation_node, MKG.longitude, self._decimal(observation.position.longitude)))
            graph.add((observation_node, MKG.latitude, self._decimal(observation.position.latitude)))

    def _apply_operation(self, graph: Graph, operation: GraphOperation) -> None:
        subject = URIRef(operation.subject)
        if operation.kind in {OperationKind.CREATE_RELATION, OperationKind.UPDATE_PROPERTY}:
            graph.add(
                (
                    subject,
                    URIRef(operation.predicate),
                    self._object_term(operation.object_value, operation.object_kind),
                )
            )
            return
        if operation.kind == OperationKind.CREATE_EVENT:
            graph.add((subject, RDF.type, MKG.MaritimeEvent))
            graph.add((subject, MKG.validFrom, self._datetime(operation.valid_from)))
            if operation.object_value is not None:
                graph.add((subject, MKG.involvesObject, self._object_term(operation.object_value, operation.object_kind)))
            return
        if operation.kind == OperationKind.CREATE_OBJECT:
            graph.add((subject, RDF.type, MKG.MaritimeObject))
            return
        if operation.kind == OperationKind.INVALIDATE_FACT:
            assertion_node = URIRef(assertion_iri(operation.target_fact_id))
            graph.add((assertion_node, MKG.invalidatedAt, self._datetime(operation.valid_to)))
            graph.add((assertion_node, MKG.isActive, Literal(False, datatype=XSD.boolean)))
            return
        if operation.kind == OperationKind.ROLLBACK:
            graph.add((subject, MKG.rollbackToVersion, Literal(operation.target_graph_version, datatype=XSD.integer)))
            return
        raise NotImplementedError(f"operation kind is not implemented in v0.1: {operation.kind.value}")

    def _add_assertion(self, graph: Graph, fact) -> None:
        assertion_node = URIRef(assertion_iri(fact.fact_id))
        subject = URIRef(fact.subject)
        predicate = URIRef(fact.predicate)
        object_term = self._object_term(fact.object_value, fact.object_kind)

        graph.add((assertion_node, RDF.type, MKG.KnowledgeAssertion))
        graph.add((assertion_node, RDF.subject, subject))
        graph.add((assertion_node, RDF.predicate, predicate))
        graph.add((assertion_node, RDF.object, object_term))
        graph.add((assertion_node, MKG.confidence, self._decimal(fact.confidence)))
        graph.add((assertion_node, MKG.validFrom, self._datetime(fact.valid_from)))
        graph.add((assertion_node, MKG.isActive, Literal(True, datatype=XSD.boolean)))
        if fact.valid_to is not None:
            graph.add((assertion_node, MKG.validTo, self._datetime(fact.valid_to)))
        for event_id in fact.evidence_ids:
            graph.add((assertion_node, MKG.supportedBy, URIRef(observation_iri(event_id))))

    def _add_version_metadata(self, graph: Graph, change_set: GraphChangeSet) -> None:
        version_node = URIRef(graph_version_iri(change_set.target_graph_version))
        change_node = URIRef(change_set_iri(change_set.change_set_id))
        graph.add((version_node, RDF.type, MKG.GraphVersion))
        graph.add((version_node, MKG.graphVersion, Literal(change_set.target_graph_version, datatype=XSD.integer)))
        graph.add((change_node, RDF.type, MKG.ChangeSet))
        graph.add((change_node, MKG.changeSetId, Literal(change_set.change_set_id)))
        graph.add((change_node, MKG.idempotencyKey, Literal(change_set.idempotency_key)))
        graph.add((change_node, MKG.baseGraphVersion, Literal(change_set.base_graph_version, datatype=XSD.integer)))
        graph.add((change_node, MKG.generatedAt, self._datetime(change_set.created_at)))
        graph.add((change_node, MKG.generatedVersion, version_node))

    def validate_graph(self, graph: Graph) -> tuple[bool, str]:
        conforms, _, report_text = validate(
            data_graph=graph,
            shacl_graph=self.shapes_graph,
            ont_graph=self.ontology_graph,
            inference="rdfs",
            abort_on_first=False,
            allow_infos=False,
            allow_warnings=False,
        )
        return bool(conforms), str(report_text)

    def preview(
        self,
        change_set: GraphChangeSet,
        observations: list[ObservationEvent],
    ) -> PreviewResult:
        if change_set.base_graph_version != self.current_version:
            raise GraphVersionConflict(
                f"expected base version {self.current_version}, got {change_set.base_graph_version}"
            )

        observations_by_id = {event.event_id: event for event in observations}
        missing = set(change_set.evidence_ids) - set(observations_by_id)
        if missing:
            raise ValueError(f"missing evidence observations: {sorted(missing)}")

        trial = self._copy_data_graph()
        for observation in observations_by_id.values():
            self._add_observation(trial, observation)
        for operation in change_set.operations:
            self._apply_operation(trial, operation)
        for fact in change_set.candidate_facts:
            self._add_assertion(trial, fact)
        self._add_version_metadata(trial, change_set)

        conforms, report = self.validate_graph(trial)
        return PreviewResult(conforms, report, len(trial), trial)

    def commit(
        self,
        change_set: GraphChangeSet,
        observations: list[ObservationEvent],
    ) -> CommitResult:
        if change_set.idempotency_key in self._committed_keys:
            return CommitResult(
                applied=False,
                graph_version=self.current_version,
                triple_count=len(self.data_graph),
                change_set_id=change_set.change_set_id,
            )
        preview = self.preview(change_set, observations)
        if not preview.conforms:
            raise GraphValidationError(preview.report)

        self.data_graph = preview.graph
        self.current_version = change_set.target_graph_version
        self._committed_keys.add(change_set.idempotency_key)
        self._snapshots[self.current_version] = self._copy_data_graph()
        self._persist_snapshot(self.current_version)
        return CommitResult(
            applied=True,
            graph_version=self.current_version,
            triple_count=len(self.data_graph),
            change_set_id=change_set.change_set_id,
        )

    def available_versions(self) -> tuple[int, ...]:
        return tuple(sorted(self._snapshots))

    def rollback_to(self, target_version: int) -> int:
        """Restore a previously committed in-memory snapshot."""
        if target_version not in self._snapshots:
            raise GraphVersionConflict(f"graph version {target_version} is not available")
        restored = self._copy_graph(self._snapshots[target_version])
        self.data_graph = restored
        self.current_version = target_version
        return target_version

    def _snapshot_path(self, version: int) -> Path | None:
        if self.snapshot_dir is None:
            return None
        return self.snapshot_dir / f"graph-v{version:04d}.ttl"

    def _persist_snapshot(self, version: int) -> None:
        path = self._snapshot_path(version)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        self._snapshots[version].serialize(destination=path, format="turtle")

    def _load_persisted_snapshots(self) -> None:
        assert self.snapshot_dir is not None
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        for path in sorted(self.snapshot_dir.glob("graph-v*.ttl")):
            try:
                version = int(path.stem.removeprefix("graph-v"))
            except ValueError:
                continue
            graph = Graph()
            self._bind_namespaces(graph)
            graph.parse(path, format="turtle")
            self._snapshots[version] = graph
        if self._snapshots:
            latest = max(self._snapshots)
            self.current_version = latest
            self.data_graph = self._copy_graph(self._snapshots[latest])

    @classmethod
    def _copy_graph(cls, source: Graph) -> Graph:
        target = Graph()
        cls._bind_namespaces(target)
        for triple in source:
            target.add(triple)
        return target

    def serialize(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.data_graph.serialize(destination=path, format="turtle")

