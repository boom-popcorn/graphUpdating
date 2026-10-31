"""Incremental stream adapter for replayable observation batches."""

from __future__ import annotations

from hashlib import sha256
from typing import Iterable

from .harness import UpdateHarness
from .models import IncrementalBatchResult, ObservationEvent


class IncrementalUpdateService:
    """Accept each observation once and hand only new events to the workflow."""

    def __init__(self, harness: UpdateHarness) -> None:
        self.harness = harness
        self._seen_event_ids: set[str] = set()

    @property
    def seen_event_count(self) -> int:
        return len(self._seen_event_ids)

    def consume(self, batch: Iterable[ObservationEvent]) -> IncrementalBatchResult:
        events = tuple(batch)
        accepted = tuple(event for event in events if event.event_id not in self._seen_event_ids)
        duplicates = tuple(event.event_id for event in events if event.event_id in self._seen_event_ids)
        self._seen_event_ids.update(event.event_id for event in accepted)
        batch_material = "|".join(event.event_id for event in events)
        batch_id = "batch-" + sha256(batch_material.encode("utf-8")).hexdigest()[:16]
        if accepted:
            workflow = self.harness.run(list(accepted))
        else:
            workflow = self.harness.run([])
        return IncrementalBatchResult(
            batch_id=batch_id,
            accepted_event_ids=tuple(event.event_id for event in accepted),
            duplicate_event_ids=duplicates,
            workflow=workflow,
        )
