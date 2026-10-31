from __future__ import annotations

import unittest

from maritime_kg.scenarios import build_scenario, scenario_catalog


class ScenarioTests(unittest.TestCase):
    def test_catalog_has_learning_scenarios(self) -> None:
        keys = {item["key"] for item in scenario_catalog()}
        self.assertEqual({"full_update", "normal_only", "late_data", "single_modality", "schema_drift"}, keys)

    def test_scenarios_have_expected_evidence(self) -> None:
        self.assertGreater(len(build_scenario("full_update")), len(build_scenario("normal_only")))
        self.assertTrue(all(event.modality.value == "ais" for event in build_scenario("single_modality")))
        self.assertEqual(
            [event.event_id for event in build_scenario("late_data")],
            [event.event_id for event in reversed(build_scenario("full_update"))],
        )


if __name__ == "__main__":
    unittest.main()
