"""Unified contracts passed between ingestion, agents, and graph storage."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Modality(str, Enum):
    AIS = "ais"
    RADAR = "radar"
    VIDEO = "video"
    REMOTE_SENSING = "remote_sensing"
    METEO_OCEAN = "meteo_ocean"
    MARITIME_TEXT = "maritime_text"


class CoordinateReferenceSystem(str, Enum):
    WGS84 = "EPSG:4326"


class ObjectKind(str, Enum):
    IRI = "iri"
    LITERAL = "literal"


class OperationKind(str, Enum):
    CREATE_OBJECT = "create_object"
    UPDATE_PROPERTY = "update_property"
    CREATE_RELATION = "create_relation"
    CREATE_EVENT = "create_event"
    INVALIDATE_FACT = "invalidate_fact"
    ROLLBACK = "rollback"


class Position(StrictModel):
    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-90, le=90)
    crs: CoordinateReferenceSystem = CoordinateReferenceSystem.WGS84


class Provenance(StrictModel):
    provider: str = Field(min_length=1)
    sensor_id: str = Field(min_length=1)
    collection_method: str = Field(min_length=1)
    raw_record_id: str = Field(min_length=1)


def _require_timezone(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime values must include an explicit timezone")
    return value


class ObservationEvent(StrictModel):
    """A normalized, replayable observation from one sensing modality."""

    event_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
    source: str = Field(min_length=1)
    modality: Modality
    observed_at: datetime
    received_at: datetime
    object_hint: str = Field(min_length=1)
    position: Position | None = None
    speed_mps: float | None = Field(default=None, ge=0, le=150)
    course_deg: float | None = Field(default=None, ge=0, lt=360)
    confidence: float = Field(ge=0, le=1)
    schema_version: str = Field(default="1.0", pattern=r"^\d+\.\d+$")
    payload: dict[str, Any] = Field(default_factory=dict)
    provenance: Provenance

    _timezone_observed = field_validator("observed_at")(_require_timezone)
    _timezone_received = field_validator("received_at")(_require_timezone)


class CandidateFact(StrictModel):
    """A proposed semantic assertion that is not yet committed to the graph."""

    fact_id: str = Field(pattern=r"^fact-[a-f0-9]{16}$")
    subject: str = Field(pattern=r"^https?://")
    predicate: str = Field(pattern=r"^https?://")
    object_value: str | int | float | bool
    object_kind: ObjectKind
    datatype: str | None = None
    valid_from: datetime
    valid_to: datetime | None = None
    asserted_at: datetime
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    ontology_version: str = Field(default="0.1.0", pattern=r"^\d+\.\d+\.\d+$")

    _timezone_valid_from = field_validator("valid_from")(_require_timezone)
    _timezone_valid_to = field_validator("valid_to")(_require_timezone)
    _timezone_asserted = field_validator("asserted_at")(_require_timezone)

    @field_validator("evidence_ids")
    @classmethod
    def evidence_must_be_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("evidence_ids must be unique")
        return value

    @model_validator(mode="after")
    def validate_interval_and_object(self) -> CandidateFact:
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError("valid_to must be later than valid_from")
        if self.object_kind == ObjectKind.IRI:
            if not isinstance(self.object_value, str) or not self.object_value.startswith(("http://", "https://")):
                raise ValueError("IRI facts require an HTTP(S) object_value")
            if self.datatype is not None:
                raise ValueError("IRI facts cannot declare a literal datatype")
        return self


class GraphOperation(StrictModel):
    """One deterministic operation inside a proposed graph transaction."""

    operation_id: str = Field(pattern=r"^op-[a-f0-9]{16}$")
    kind: OperationKind
    subject: str = Field(pattern=r"^https?://")
    predicate: str | None = Field(default=None, pattern=r"^https?://")
    object_value: str | int | float | bool | None = None
    object_kind: ObjectKind | None = None
    valid_from: datetime | None = None
    evidence_ids: tuple[str, ...] = Field(default_factory=tuple)
    preconditions: tuple[str, ...] = Field(default_factory=tuple)

    _timezone_valid_from = field_validator("valid_from")(_require_timezone)

    @model_validator(mode="after")
    def validate_operation_shape(self) -> GraphOperation:
        triple_kinds = {OperationKind.CREATE_RELATION, OperationKind.UPDATE_PROPERTY}
        if self.kind in triple_kinds:
            if self.predicate is None or self.object_value is None or self.object_kind is None:
                raise ValueError(f"{self.kind.value} requires predicate, object_value, and object_kind")
        if self.kind == OperationKind.CREATE_EVENT and self.valid_from is None:
            raise ValueError("create_event requires valid_from")
        return self


class GraphChangeSet(StrictModel):
    """An atomic, versioned proposal that is validated before graph commit."""

    change_set_id: str = Field(pattern=r"^changeset-[a-f0-9]{16}$")
    idempotency_key: str = Field(pattern=r"^idem-[a-f0-9]{16}$")
    base_graph_version: int = Field(ge=0)
    target_graph_version: int = Field(ge=1)
    ontology_version: str = Field(default="0.1.0", pattern=r"^\d+\.\d+\.\d+$")
    created_at: datetime
    reason: str = Field(min_length=1)
    candidate_facts: tuple[CandidateFact, ...] = Field(min_length=1)
    operations: tuple[GraphOperation, ...] = Field(min_length=1)

    _timezone_created = field_validator("created_at")(_require_timezone)

    @model_validator(mode="after")
    def validate_versions_and_evidence(self) -> GraphChangeSet:
        if self.target_graph_version != self.base_graph_version + 1:
            raise ValueError("target_graph_version must equal base_graph_version + 1")
        fact_evidence = {event_id for fact in self.candidate_facts for event_id in fact.evidence_ids}
        operation_evidence = {event_id for op in self.operations for event_id in op.evidence_ids}
        if not operation_evidence.issubset(fact_evidence):
            raise ValueError("operation evidence must be declared by candidate facts")
        return self

    @property
    def evidence_ids(self) -> tuple[str, ...]:
        return tuple(sorted({event_id for fact in self.candidate_facts for event_id in fact.evidence_ids}))
