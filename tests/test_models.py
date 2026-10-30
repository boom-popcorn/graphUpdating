from __future__ import annotations

import unittest
from datetime import datetime

from pydantic import ValidationError

from maritime_kg.models import ObservationEvent
from maritime_kg.sample_data import build_sample_observations


class ObservationEventTests(unittest.TestCase):
    def test_sample_observation_round_trips_as_json(self) -> None:
        event = build_sample_observations()[0]
        restored = ObservationEvent.model_validate_json(event.model_dump_json())
        self.assertEqual(event, restored)

    def test_naive_datetime_is_rejected(self) -> None:
        event_data = build_sample_observations()[0].model_dump()
        event_data["observed_at"] = datetime(2026, 10, 26, 10, 0)
        with self.assertRaisesRegex(ValidationError, "explicit timezone"):
            ObservationEvent.model_validate(event_data)

    def test_confidence_outside_zero_to_one_is_rejected(self) -> None:
        event_data = build_sample_observations()[0].model_dump()
        event_data["confidence"] = 1.1
        with self.assertRaises(ValidationError):
            ObservationEvent.model_validate(event_data)


if __name__ == "__main__":
    unittest.main()

