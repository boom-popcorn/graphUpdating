"""Agent boundaries corresponding to the update-chain blocks in the framework."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Iterable

from pathlib import Path

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF
from rdflib.namespace import OWL, RDFS

from .constants import ONTOLOGY_IRI
from .models import (
    CandidateFact,
    EvolutionKind,
    GraphChangeSet,
    Modality,
    ObservationEvent,
    OntologyEvolutionProposal,
    OperationKind,
    ValidationFeedback,
)
from .ontology import CommitResult, MaritimeGraphRepository
from .pipeline import ChangePlanner, SemanticPerceptionProcessor, _stable_token


KNOWN_PAYLOAD_FIELDS: dict[Modality, set[str]] = {
    Modality.AIS: {"mmsi", "declared_type", "navigation_status", "area_id"},
    Modality.RADAR: {"track_id", "correlated_mmsi", "range_km", "area_id"},
    Modality.VIDEO: {"camera_id", "detected_equipment", "correlated_mmsi", "area_id"},
    Modality.REMOTE_SENSING: {"scene_id", "detected_object", "area_id"},
    Modality.METEO_OCEAN: {"station_id", "wind_speed_mps", "wave_height_m", "area_id"},
    Modality.MARITIME_TEXT: {"document_id", "mentioned_mmsi", "event_type", "area_id"},
}


class SemanticPerceptionAgent:
    """Detect changes, align observations, and construct candidate facts."""

    name = "semantic-perception"

    def __init__(self, processor: SemanticPerceptionProcessor | None = None) -> None:
        self.processor = processor or SemanticPerceptionProcessor()

    def perceive(self, observations: Iterable[ObservationEvent]) -> tuple[CandidateFact, ...]:
        return tuple(self.processor.detect_hovering(observations))


class OntologyEvolutionAgent:
    """Recognize schema drift and produce human-reviewable evolution proposals."""

    name = "ontology-evolution"

    @staticmethod
    def _term_name(field_name: str) -> str:
        parts = [part for part in re.split(r"[^A-Za-z0-9]+", field_name) if part]
        if not parts:
            return "unknownField"
        return parts[0].lower() + "".join(part.title() for part in parts[1:])

    def analyze(self, observations: Iterable[ObservationEvent]) -> tuple[OntologyEvolutionProposal, ...]:
        unknown: dict[str, list[ObservationEvent]] = defaultdict(list)
        for observation in observations:
            known = KNOWN_PAYLOAD_FIELDS.get(observation.modality, set())
            for field_name in observation.payload:
                if field_name not in known:
                    unknown[field_name].append(observation)

        proposals: list[OntologyEvolutionProposal] = []
        for field_name, evidence in sorted(unknown.items()):
            evidence_ids = tuple(sorted({item.event_id for item in evidence}))
            modalities = tuple(sorted({item.modality for item in evidence}, key=lambda item: item.value))
            token = _stable_token(field_name, *(item.value for item in modalities))
            proposals.append(
                OntologyEvolutionProposal(
                    proposal_id=f"evolution-{token}",
                    kind=EvolutionKind.ADD_PROPERTY,
                    source_field=field_name,
                    source_modalities=modalities,
                    suggested_term=f"{ONTOLOGY_IRI}{self._term_name(field_name)}",
                    suggested_domain=f"{ONTOLOGY_IRI}Observation",
                    suggested_datatype="http://www.w3.org/2001/XMLSchema#decimal",
                    rationale="The incoming schema contains a field that has no mapping in ontology v0.1.",
                    evidence_ids=evidence_ids,
                )
            )
        return tuple(proposals)

    def approve(
        self,
        proposal: OntologyEvolutionProposal,
        source_path: Path,
        target_path: Path,
    ) -> str:
        """Apply one human-approved proposal and write a new ontology version."""
        graph = Graph()
        graph.parse(source_path, format="turtle")
        graph.add((URIRef(proposal.suggested_term), RDF.type, OWL.DatatypeProperty))
        graph.add((URIRef(proposal.suggested_term), RDFS.domain, URIRef(proposal.suggested_domain)))
        graph.add((URIRef(proposal.suggested_term), RDFS.range, URIRef(proposal.suggested_datatype)))
        graph.add((URIRef(ONTOLOGY_IRI), OWL.versionInfo, Literal("0.2.0")))
        graph.add((URIRef(ONTOLOGY_IRI), OWL.versionIRI, URIRef(f"{ONTOLOGY_IRI}0.2.0")))
        target_path.parent.mkdir(parents=True, exist_ok=True)
        graph.serialize(destination=target_path, format="turtle")
        return "0.2.0"


class DecisionPlanningAgent:
    """Match rules and produce or repair an atomic graph update plan."""

    name = "decision-planning"

    def __init__(self, planner: ChangePlanner | None = None) -> None:
        self.planner = planner or ChangePlanner()

    def plan(self, candidates: Iterable[CandidateFact], base_graph_version: int) -> GraphChangeSet:
        return self.planner.plan(candidates, base_graph_version)

    def replan(self, change_set: GraphChangeSet, feedback: ValidationFeedback) -> GraphChangeSet:
        blocked_ids = {
            hint.removeprefix("remove-operation:")
            for hint in feedback.repair_hints
            if hint.startswith("remove-operation:")
        }
        repaired_operations = tuple(
            operation for operation in change_set.operations if operation.operation_id not in blocked_ids
        )
        if not repaired_operations:
            raise ValueError("validation feedback removed every planned operation")
        return change_set.model_copy(update={"operations": repaired_operations})


class ConsistencyValidationAgent:
    """Run action support checks followed by OWL/SHACL graph validation."""

    name = "consistency-validation"
    supported_operations = {
        OperationKind.CREATE_RELATION,
        OperationKind.CREATE_EVENT,
        OperationKind.CREATE_OBJECT,
        OperationKind.INVALIDATE_FACT,
        OperationKind.ROLLBACK,
    }

    def validate(
        self,
        repository: MaritimeGraphRepository,
        change_set: GraphChangeSet,
        observations: list[ObservationEvent],
        attempt: int,
    ) -> ValidationFeedback:
        errors: list[str] = []
        hints: list[str] = []
        for operation in change_set.operations:
            if operation.kind not in self.supported_operations:
                errors.append(f"operation {operation.operation_id} is not executable in v0.1: {operation.kind.value}")
                hints.append(f"remove-operation:{operation.operation_id}")
        if errors:
            return ValidationFeedback(attempt=attempt, valid=False, errors=tuple(errors), repair_hints=tuple(hints))

        try:
            preview = repository.preview(change_set, observations)
        except (ValueError, NotImplementedError) as exc:
            return ValidationFeedback(attempt=attempt, valid=False, errors=(str(exc),))
        if not preview.conforms:
            return ValidationFeedback(
                attempt=attempt,
                valid=False,
                errors=(preview.report,),
                repair_hints=("review ontology, mapping, and operation values",),
                rule_optimization_suggestions=("Review the affected OWL/SHACL constraint before retrying.",),
                mapping_repairs=("Check whether the incoming field-to-ontology mapping is current.",),
            )
        return ValidationFeedback(attempt=attempt, valid=True)


class ActionExecutionAgent:
    """Perform the controlled atomic commit after validation succeeds."""

    name = "action-execution"

    def execute(
        self,
        repository: MaritimeGraphRepository,
        change_set: GraphChangeSet,
        observations: list[ObservationEvent],
    ) -> CommitResult:
        return repository.commit(change_set, observations)
