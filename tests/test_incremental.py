from __future__ import annotations

import unittest
from pathlib import Path

from maritime_kg.harness import UpdateHarness
from maritime_kg.incremental import IncrementalUpdateService
from maritime_kg.models import WorkflowStatus
from maritime_kg.ontology import MaritimeGraphRepository
from maritime_kg.sample_data import build_sample_observations


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ONTOLOGY_PATH = PROJECT_ROOT / "ontology" / "maritime-v0.1.ttl"
SHAPES_PATH = PROJECT_ROOT / "ontology" / "maritime-v0.1-shapes.ttl"


class IncrementalTests(unittest.TestCase):
    def test_batch_consumption_deduplicates_events(self) -> None:
        observations = build_sample_observations()
        repository = MaritimeGraphRepository(ONTOLOGY_PATH, SHAPES_PATH)
        service = IncrementalUpdateService(UpdateHarness(repository))

        first = service.consume(observations[:4])
        second = service.consume(observations[:4])

        self.assertEqual(4, len(first.accepted_event_ids))
        self.assertEqual(0, len(first.duplicate_event_ids))
        self.assertEqual(0, len(second.accepted_event_ids))
        self.assertEqual(4, len(second.duplicate_event_ids))
        self.assertEqual(4, service.seen_event_count)

    def test_repository_can_restore_a_previous_snapshot(self) -> None:
        observations = build_sample_observations()
        repository = MaritimeGraphRepository(ONTOLOGY_PATH, SHAPES_PATH)
        service = IncrementalUpdateService(UpdateHarness(repository))

        service.consume(observations)
        version_one_triples = len(repository.data_graph)
        self.assertEqual((0, 1), repository.available_versions())

        restored_version = repository.rollback_to(0)
        self.assertEqual(0, restored_version)
        self.assertEqual(0, len(repository.data_graph))
        self.assertLess(len(repository.data_graph), version_one_triples)

    def test_rejects_unknown_rollback_version(self) -> None:
        observations = build_sample_observations()
        repository = MaritimeGraphRepository(ONTOLOGY_PATH, SHAPES_PATH)
        service = IncrementalUpdateService(UpdateHarness(repository))
        service.consume(observations)
        with self.assertRaises(ValueError):
            repository.rollback_to(99)

    def test_persisted_snapshots_survive_repository_recreation(self) -> None:
        observations = build_sample_observations()
        snapshot_dir = PROJECT_ROOT / "output" / "test-snapshots"
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        for path in snapshot_dir.glob("graph-v*.ttl"):
            path.unlink()
        first_repository = MaritimeGraphRepository(ONTOLOGY_PATH, SHAPES_PATH, snapshot_dir=snapshot_dir)
        IncrementalUpdateService(UpdateHarness(first_repository)).consume(observations)
        second_repository = MaritimeGraphRepository(ONTOLOGY_PATH, SHAPES_PATH, snapshot_dir=snapshot_dir)
        self.assertEqual(1, second_repository.current_version)
        self.assertEqual(len(first_repository.data_graph), len(second_repository.data_graph))
        for path in snapshot_dir.glob("graph-v*.ttl"):
            path.unlink()
        snapshot_dir.rmdir()


if __name__ == "__main__":
    unittest.main()

