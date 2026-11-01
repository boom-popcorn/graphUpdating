from datetime import datetime, timedelta, timezone

from odkg.demo import build_maritime_orchestrator
from odkg.domains.maritime import maritime_events_to_observations
from odkg.mcp_facade import EvolutionToolFacade


def _events() -> list[dict[str, object]]:
    started = datetime(2026, 10, 31, 4, 0, tzinfo=timezone.utc)
    return [
        {
            "event_id": "ais-hover-001",
            "source": "ais-coastal-station",
            "modality": "ais",
            "observed_at": started.isoformat(),
            "received_at": (started + timedelta(seconds=2)).isoformat(),
            "object_hint": "vessel:412345679",
            "position": {"longitude": 121.5, "latitude": 30.7},
            "speed_mps": 0.2,
            "course_deg": 12.0,
            "confidence": 0.94,
            "payload": {"mmsi": "412345679"},
        },
        {
            "event_id": "radar-hover-001",
            "source": "radar-east-01",
            "modality": "radar",
            "observed_at": (started + timedelta(seconds=720)).isoformat(),
            "received_at": (started + timedelta(seconds=724)).isoformat(),
            "object_hint": "track:R-1007",
            "position": {"longitude": 121.5001, "latitude": 30.7001},
            "speed_mps": 0.3,
            "course_deg": 15.0,
            "confidence": 0.9,
            "payload": {"track_id": "R-1007", "correlated_mmsi": "412345679"},
        },
    ]


def test_maritime_rule_requires_cross_modal_duration() -> None:
    assert maritime_events_to_observations(_events()[:1]) == []
    observations = maritime_events_to_observations(_events())
    assert len(observations) == 1
    assert observations[0].predicate.endswith("hasState")
    assert {item.modality for item in observations[0].evidence} == {"ais", "radar"}


def test_maritime_facade_previews_and_commits_with_domain_ontology() -> None:
    facade = EvolutionToolFacade(build_maritime_orchestrator())
    preview = facade.preview_maritime_events(_events())
    assert preview["pipeline"]["transaction"]["status"] == "prepared"
    assert facade.get_ontology_version() == {"version": "v0.1.0"}

    committed = facade.run_maritime_events(_events())
    assert committed["pipeline"]["transaction"]["status"] == "committed"
    assert facade.get_ontology_version() == {"version": "v0.2.0"}
