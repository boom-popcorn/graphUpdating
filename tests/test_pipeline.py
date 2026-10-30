from __future__ import annotations

import time
import unittest
from pathlib import Path

from rdflib import Graph, URIRef
from rdflib.namespace import RDF

from maritime_kg.constants import ONTOLOGY_IRI, vessel_iri
from maritime_kg.models import Modality
from maritime_kg.ontology import MKG, MaritimeGraphRepository
from maritime_kg.pipeline import ChangePlanner, SemanticPerceptionProcessor
from maritime_kg.sample_data import build_sample_observations


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ONTOLOGY_PATH = PROJECT_ROOT / "ontology" / "maritime-v0.1.ttl"
SHAPES_PATH = PROJECT_ROOT / "ontology" / "maritime-v0.1-shapes.ttl"


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.observations = build_sample_observations()
        self.processor = SemanticPerceptionProcessor()

    def test_only_hovering_vessel_produces_candidate_fact(self) -> None:
        candidates = self.processor.detect_hovering(self.observations)
        self.assertEqual(1, len(candidates))
        candidate = candidates[0]
        self.assertEqual(vessel_iri("412345679"), candidate.subject)
        self.assertEqual(f"{ONTOLOGY_IRI}hasState", candidate.predicate)
        self.assertEqual(f"{ONTOLOGY_IRI}HoveringState", candidate.object_value)

        evidence_by_id = {event.event_id: event for event in self.observations}
        modalities = {evidence_by_id[event_id].modality for event_id in candidate.evidence_ids}
        self.assertEqual({Modality.AIS, Modality.RADAR}, modalities)

    def test_single_modality_does_not_trigger_rule(self) -> None:
        ais_only = [event for event in self.observations if event.modality == Modality.AIS]
        self.assertEqual([], self.processor.detect_hovering(ais_only))

    def test_change_set_is_deterministic(self) -> None:
        candidates = self.processor.detect_hovering(self.observations)
        planner = ChangePlanner()
        first = planner.plan(candidates, base_graph_version=0)
        second = planner.plan(candidates, base_graph_version=0)
        self.assertEqual(first, second)
        self.assertEqual(0, first.base_graph_version)
        self.assertEqual(1, first.target_graph_version)
        self.assertEqual(2, len(first.operations))

    def test_commit_is_validated_versioned_and_idempotent(self) -> None:
        started_at = time.perf_counter()
        candidates = self.processor.detect_hovering(self.observations)
        change_set = ChangePlanner().plan(candidates, base_graph_version=0)
        repository = MaritimeGraphRepository(ONTOLOGY_PATH, SHAPES_PATH)

        first = repository.commit(change_set, self.observations)
        second = repository.commit(change_set, self.observations)

        self.assertTrue(first.applied)
        self.assertFalse(second.applied)
        self.assertEqual(1, first.graph_version)
        self.assertEqual(first.triple_count, second.triple_count)
        self.assertIn(
            (
                URIRef(vessel_iri("412345679")),
                URIRef(f"{ONTOLOGY_IRI}hasState"),
                URIRef(f"{ONTOLOGY_IRI}HoveringState"),
            ),
            repository.data_graph,
        )
        self.assertLess(time.perf_counter() - started_at, 10.0)

    def test_shacl_rejects_incomplete_observation(self) -> None:
        repository = MaritimeGraphRepository(ONTOLOGY_PATH, SHAPES_PATH)
        invalid_graph = Graph()
        invalid_graph.add((URIRef("https://example.org/test/bad-observation"), RDF.type, MKG.Observation))
        conforms, report = repository.validate_graph(invalid_graph)
        self.assertFalse(conforms)
        self.assertIn("Validation Report", report)


if __name__ == "__main__":
    unittest.main()

