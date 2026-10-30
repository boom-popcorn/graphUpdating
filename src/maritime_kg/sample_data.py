"""Generate deterministic AIS and radar observations for the MVP tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import Modality, ObservationEvent, Position, Provenance


CHINA_STANDARD_TIME = timezone(timedelta(hours=8))


def _ais_event(
    mmsi: str,
    minute: int,
    longitude: float,
    latitude: float,
    speed_mps: float,
    course_deg: float,
    label: str,
) -> ObservationEvent:
    observed_at = datetime(2026, 10, 26, 10, minute, tzinfo=CHINA_STANDARD_TIME)
    event_id = f"ais-{mmsi}-{label}"
    return ObservationEvent(
        event_id=event_id,
        source="ais-receiver-east-01",
        modality=Modality.AIS,
        observed_at=observed_at,
        received_at=observed_at + timedelta(seconds=1),
        object_hint=f"mmsi:{mmsi}",
        position=Position(longitude=longitude, latitude=latitude),
        speed_mps=speed_mps,
        course_deg=course_deg,
        confidence=0.98,
        payload={
            "mmsi": mmsi,
            "declared_type": "fishing",
            "navigation_status": "under_way" if speed_mps > 0.5 else "restricted_manoeuvrability",
            "area_id": "sensitive-area-alpha" if mmsi.endswith("679") else "open-sea-lane-01",
        },
        provenance=Provenance(
            provider="coastal-ais-network",
            sensor_id="ais-receiver-east-01",
            collection_method="terrestrial_ais",
            raw_record_id=event_id,
        ),
    )


def _radar_event(
    mmsi: str,
    track_id: str,
    minute: int,
    longitude: float,
    latitude: float,
    speed_mps: float,
    course_deg: float,
    label: str,
) -> ObservationEvent:
    observed_at = datetime(2026, 10, 26, 10, minute, 5, tzinfo=CHINA_STANDARD_TIME)
    event_id = f"radar-{track_id}-{label}"
    return ObservationEvent(
        event_id=event_id,
        source="coastal-radar-03",
        modality=Modality.RADAR,
        observed_at=observed_at,
        received_at=observed_at + timedelta(seconds=2),
        object_hint=f"radar-track:{track_id}",
        position=Position(longitude=longitude, latitude=latitude),
        speed_mps=speed_mps,
        course_deg=course_deg,
        confidence=0.91,
        payload={
            "track_id": track_id,
            "correlated_mmsi": mmsi,
            "range_km": 24.8,
            "area_id": "sensitive-area-alpha" if mmsi.endswith("679") else "open-sea-lane-01",
        },
        provenance=Provenance(
            provider="coastal-surveillance-network",
            sensor_id="coastal-radar-03",
            collection_method="x_band_radar",
            raw_record_id=event_id,
        ),
    )


def build_sample_observations() -> list[ObservationEvent]:
    events: list[ObservationEvent] = []

    # Vessel 412345678 remains under way and should not trigger the hovering rule.
    normal_points = [
        (0, 113.8000, 21.9000, 4.6, 72.0),
        (5, 113.8180, 21.9050, 4.8, 73.0),
        (10, 113.8360, 21.9100, 4.7, 72.5),
        (15, 113.8540, 21.9150, 4.9, 73.0),
    ]
    for index, point in enumerate(normal_points):
        minute, longitude, latitude, speed, course = point
        label = f"{index:02d}"
        events.append(_ais_event("412345678", minute, longitude, latitude, speed, course, label))
        events.append(
            _radar_event(
                "412345678",
                "R-1001",
                minute,
                longitude + 0.0002,
                latitude - 0.0001,
                speed - 0.1,
                course + 0.5,
                label,
            )
        )

    # Vessel 412345679 slows down for more than ten minutes in a sensitive area.
    hovering_points = [
        (0, 113.9200, 21.9800, 4.2, 135.0),
        (6, 113.9250, 21.9750, 0.32, 182.0),
        (12, 113.9252, 21.9751, 0.21, 15.0),
        (18, 113.9251, 21.9752, 0.28, 270.0),
    ]
    for index, point in enumerate(hovering_points):
        minute, longitude, latitude, speed, course = point
        label = f"{index:02d}"
        ais_event = _ais_event("412345679", minute, longitude, latitude, speed, course, label)
        if index == 3:
            # New schema field: detected but not automatically added to the ontology.
            ais_event = ais_event.model_copy(
                update={"payload": {**ais_event.payload, "encrypted_message_frequency": 18}}
            )
        events.append(ais_event)
        radar_speed = speed + 0.04 if speed <= 0.5 else speed - 0.1
        events.append(
            _radar_event(
                "412345679",
                "R-1002",
                minute,
                longitude - 0.0001,
                latitude + 0.0002,
                radar_speed,
                (course + 2.0) % 360,
                label,
            )
        )

    # Interleave sources and reverse radar arrival order to exercise event-time sorting.
    return events[::2] + list(reversed(events[1::2]))


def write_sample_data(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [event.model_dump_json() for event in build_sample_observations()]
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def main() -> None:
    project_root = Path(__file__).resolve().parents[2]
    output_path = project_root / "data" / "sample_observations.jsonl"
    write_sample_data(output_path)
    print(f"Wrote {len(build_sample_observations())} observations to {output_path}")


if __name__ == "__main__":
    main()

