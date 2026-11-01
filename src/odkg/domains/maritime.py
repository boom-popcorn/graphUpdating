from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..contracts import Evidence, Observation


ONTOLOGY_IRI = "https://example.org/maritime-kg/ontology/"
RESOURCE_IRI = "https://example.org/maritime-kg/resource/"


class MaritimeModality(StrEnum):
    AIS = "ais"
    RADAR = "radar"
    VIDEO = "video"
    REMOTE_SENSING = "remote_sensing"
    METEO_OCEAN = "meteo_ocean"
    MARITIME_TEXT = "maritime_text"


class Position(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-90, le=90)
    crs: str = Field(default="EPSG:4326", pattern=r"^EPSG:\d+$")


class MaritimeObservationEvent(BaseModel):
    """Normalized, replayable maritime observation at the domain boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
    source: str = Field(min_length=1)
    modality: MaritimeModality
    observed_at: datetime
    received_at: datetime
    object_hint: str = Field(min_length=1)
    position: Position | None = None
    speed_mps: float | None = Field(default=None, ge=0, le=150)
    course_deg: float | None = Field(default=None, ge=0, lt=360)
    confidence: float = Field(ge=0, le=1)
    schema_version: str = Field(default="1.0", pattern=r"^\d+\.\d+$")
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("observed_at", "received_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime values must include an explicit timezone")
        return value


class HoveringRuleConfig(BaseModel):
    speed_threshold_mps: float = Field(default=0.5, ge=0)
    minimum_duration_seconds: int = Field(default=600, ge=1)
    minimum_modalities: int = Field(default=2, ge=1)


class MaritimeSemanticPerception:
    """Detect evidence-backed maritime states before the generic update cycle."""

    def __init__(self, config: HoveringRuleConfig | None = None) -> None:
        self.config = config or HoveringRuleConfig()

    def detect_hovering(
        self, events: Iterable[MaritimeObservationEvent]
    ) -> list[Observation]:
        grouped: dict[str, list[MaritimeObservationEvent]] = defaultdict(list)
        deduplicated = {event.event_id: event for event in events}
        for event in deduplicated.values():
            if event.speed_mps is None or event.speed_mps > self.config.speed_threshold_mps:
                continue
            grouped[self._vessel_id(event)].append(event)

        observations: list[Observation] = []
        for vessel_id, evidence_events in sorted(grouped.items()):
            evidence_events.sort(key=lambda item: item.observed_at)
            modalities = {event.modality for event in evidence_events}
            duration = (
                evidence_events[-1].observed_at - evidence_events[0].observed_at
            ).total_seconds()
            if len(modalities) < self.config.minimum_modalities:
                continue
            if duration < self.config.minimum_duration_seconds:
                continue

            observations.append(
                Observation(
                    subject=f"{RESOURCE_IRI}vessel/{vessel_id}",
                    predicate=f"{ONTOLOGY_IRI}hasState",
                    object=f"{ONTOLOGY_IRI}HoveringState",
                    subject_type=f"{ONTOLOGY_IRI}Vessel",
                    object_type=f"{ONTOLOGY_IRI}State",
                    confidence=round(
                        sum(event.confidence for event in evidence_events)
                        / len(evidence_events),
                        6,
                    ),
                    evidence=[self._evidence(event) for event in evidence_events],
                )
            )
        return observations

    @staticmethod
    def _vessel_id(event: MaritimeObservationEvent) -> str:
        if event.modality == MaritimeModality.AIS:
            value = event.payload.get("mmsi", event.object_hint)
        else:
            value = event.payload.get("correlated_mmsi", event.object_hint)
        return str(value).split(":")[-1]

    @staticmethod
    def _evidence(event: MaritimeObservationEvent) -> Evidence:
        locator = None
        if event.position is not None:
            locator = (
                f"geo:{event.position.latitude},{event.position.longitude}"
                f";crs={event.position.crs}"
            )
        return Evidence(
            source_id=event.event_id,
            modality=event.modality.value,
            locator=locator,
            extractor_id="maritime-hovering-rule-v1",
            metadata={
                "provider": event.source,
                "observed_at": event.observed_at.isoformat(),
                "received_at": event.received_at.isoformat(),
                "schema_version": event.schema_version,
                "payload_digest": sha256(
                    repr(sorted(event.payload.items())).encode("utf-8")
                ).hexdigest(),
            },
        )


def maritime_events_to_observations(
    payloads: Iterable[dict[str, Any]],
    config: HoveringRuleConfig | None = None,
) -> list[Observation]:
    events = [MaritimeObservationEvent.model_validate(item) for item in payloads]
    return MaritimeSemanticPerception(config).detect_hovering(events)
