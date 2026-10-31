from __future__ import annotations

import unittest

from web.server import PrototypeState


class WorkbenchStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = PrototypeState()
        self.state.run_all()

    def test_analysis_waits_for_human_approval(self) -> None:
        report = self.state.report()
        self.assertEqual(0, report["graph_version"])
        self.assertEqual(0, report["triple_count"])
        self.assertEqual(2, report["pending_count"])
        self.assertEqual("pending", report["fact_reviews"][0]["status"])
        self.assertEqual("pending", report["proposals"][0]["status"])

    def test_fact_approval_commits_a_new_graph_version(self) -> None:
        fact_id = self.state.report()["fact_reviews"][0]["fact_id"]
        self.state.approve_fact(fact_id)
        report = self.state.report()
        self.assertEqual("committed", report["fact_reviews"][0]["status"])
        self.assertEqual(1, report["graph_version"])
        self.assertGreater(report["triple_count"], 0)

    def test_ontology_approval_updates_active_version(self) -> None:
        proposal_id = self.state.report()["proposals"][0]["proposal_id"]
        self.state.approve_evolution(proposal_id)
        report = self.state.report()
        self.assertEqual("approved", report["proposals"][0]["status"])
        self.assertEqual("0.2.0", report["ontology"]["version"])
        self.assertIn("encrypted_message_frequency", report["ontology"]["approved_fields"])


if __name__ == "__main__":
    unittest.main()
