"""Deterministic baseline for perception, planning, and versioned graph updates."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .constants import ONTOLOGY_IRI, event_iri, vessel_iri
from .models import (
    CandidateFact,
    GraphChangeSet,
    GraphOperation,
    Modality,
    ObjectKind,
    ObservationEvent,
    OperationKind,
)


def _stable_token(*parts: object) -> str:
    material = "|".join(str(part) for part in parts).encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:16]


def load_observations(path: Path) -> list[ObservationEvent]:
    observations: list[ObservationEvent] = []
    seen_event_ids: set[str] = set()
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            event = ObservationEvent.model_validate_json(line)
            if event.event_id in seen_event_ids:
                continue
            seen_event_ids.add(event.event_id)
            observations.append(event)
    return observations


def resolve_vessel_id(observation: ObservationEvent) -> str:
    if observation.modality == Modality.AIS:
        raw_id = observation.payload.get("mmsi", observation.object_hint)
    else:
        raw_id = observation.payload.get("correlated_mmsi", observation.object_hint)
    return str(raw_id).split(":")[-1]


@dataclass(frozen=True)
class HoveringRuleConfig:
    speed_threshold_mps: float = 0.5
    minimum_duration_seconds: int = 600
    minimum_modalities: int = 2


class SemanticPerceptionProcessor:
    """Turns normalized observations into evidence-backed candidate facts."""

    def __init__(self, config: HoveringRuleConfig | None = None) -> None:
        self.config = config or HoveringRuleConfig()

    def detect_hovering(self, observations: Iterable[ObservationEvent]) -> list[CandidateFact]:
        grouped: dict[str, list[ObservationEvent]] = defaultdict(list)
        deduplicated = {event.event_id: event for event in observations}
        for event in deduplicated.values():
            if event.speed_mps is not None and event.speed_mps <= self.config.speed_threshold_mps:
                grouped[resolve_vessel_id(event)].append(event)

        candidates: list[CandidateFact] = []
        for vessel_id, evidence in grouped.items():
            evidence.sort(key=lambda item: item.observed_at)
            modalities = {item.modality for item in evidence}
            duration = (evidence[-1].observed_at - evidence[0].observed_at).total_seconds()
            if len(modalities) < self.config.minimum_modalities:
                continue
            if duration < self.config.minimum_duration_seconds:
                continue

            evidence_ids = tuple(item.event_id for item in evidence)
            subject = vessel_iri(vessel_id)
            predicate = f"{ONTOLOGY_IRI}hasState"
            object_value = f"{ONTOLOGY_IRI}HoveringState"
            fact_token = _stable_token(subject, predicate, object_value, evidence[0].observed_at.isoformat())
            confidence = round(sum(item.confidence for item in evidence) / len(evidence), 4)
            candidates.append(
                CandidateFact(
                    fact_id=f"fact-{fact_token}",
                    subject=subject,
                    predicate=predicate,
                    object_value=object_value,
                    object_kind=ObjectKind.IRI,
                    valid_from=evidence[0].observed_at,
                    asserted_at=max(item.received_at for item in evidence),
                    evidence_ids=evidence_ids,
                    confidence=confidence,
                )
            )
        return sorted(candidates, key=lambda fact: fact.fact_id)


class ChangePlanner:
    """Compiles candidate facts into deterministic, reviewable graph operations."""

    def plan(self, candidates: Iterable[CandidateFact], base_graph_version: int) -> GraphChangeSet:
        facts = tuple(sorted(candidates, key=lambda fact: fact.fact_id))
        if not facts:
            raise ValueError("at least one candidate fact is required")

        operations: list[GraphOperation] = []
        for fact in facts:
            state_op_token = _stable_token("state", fact.fact_id)
            operations.append(
                GraphOperation(
                    operation_id=f"op-{state_op_token}",
                    kind=OperationKind.CREATE_RELATION,
                    subject=fact.subject,
                    predicate=fact.predicate,
                    object_value=fact.object_value,
                    object_kind=fact.object_kind,
                    valid_from=fact.valid_from,
                    evidence_ids=fact.evidence_ids,
                    preconditions=("candidate fact passed semantic checks",),
                )
            )

            vessel_id = fact.subject.rsplit("/", maxsplit=1)[-1]
            event_id = f"hovering-{vessel_id}-{fact.valid_from:%Y%m%dT%H%M%S%z}"
            event_op_token = _stable_token("event", fact.fact_id)
            operations.append(
                GraphOperation(
                    operation_id=f"op-{event_op_token}",
                    kind=OperationKind.CREATE_EVENT,
                    subject=event_iri(event_id),
                    object_value=fact.subject,
                    object_kind=ObjectKind.IRI,
                    valid_from=fact.valid_from,
                    evidence_ids=fact.evidence_ids,
                    preconditions=("state update and event creation are committed atomically",),
                )
            )

        fact_ids = ",".join(fact.fact_id for fact in facts)
        change_token = _stable_token(base_graph_version, fact_ids)
        created_at = max(fact.asserted_at for fact in facts)
        return GraphChangeSet(
            change_set_id=f"changeset-{change_token}",
            idempotency_key=f"idem-{_stable_token('idempotency', fact_ids)}",
            base_graph_version=base_graph_version,
            target_graph_version=base_graph_version + 1,
            created_at=created_at,
            reason="AIS and radar jointly indicate sustained low-speed hovering",
            candidate_facts=facts,
            operations=tuple(operations),
        )


def dump_change_set(change_set: GraphChangeSet, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(change_set.model_dump_json(indent=2), encoding="utf-8")
