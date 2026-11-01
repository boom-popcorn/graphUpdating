from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from rdflib import Graph
from rdflib.namespace import OWL, RDF

from .contracts import Evidence, Observation
from .ontology import OntologyRepository
from .orchestrator import UpdateOrchestrator
from .state import UpdateEngine
from .storage import ArtifactStore
from .policy import EvolutionPolicy
from .graph_store import GraphSnapshotStore
from .environment import configure_model_environment
from .ingestion import MultimodalIngestionPipeline, build_ingestion_pipeline
from .ingestion import EntityRegistry
from .semantic_adapters import (
    AdaKGCRecordExtractor,
    ContinueMKGCPredictionExtractor,
    LabelMapping,
    OntologyPromptContext,
    model_extractors_from_env,
)


def build_maritime_orchestrator(
    artifact_store: ArtifactStore | None = None,
    policy: EvolutionPolicy | None = None,
    graph_store: GraphSnapshotStore | None = None,
) -> UpdateOrchestrator:
    """Build the generic update core with the maritime ontology package."""

    root = project_root()
    active_policy = policy or EvolutionPolicy.from_file(
        root / "config" / "evolution_policy.json"
    )
    ontology = OntologyRepository.from_file(root / "ontology" / "maritime.ttl")
    shapes = Graph().parse(root / "ontology" / "maritime-shapes.ttl", format="turtle")
    return UpdateOrchestrator(
        UpdateEngine(ontology, shapes_graph=shapes),
        artifact_store=artifact_store,
        policy=active_policy,
        graph_store=graph_store,
    )


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def build_demo_orchestrator(
    artifact_store: ArtifactStore | None = None,
    policy: EvolutionPolicy | None = None,
    graph_store: GraphSnapshotStore | None = None,
) -> UpdateOrchestrator:
    root = project_root()
    active_policy = policy or EvolutionPolicy.from_file(
        root / "config" / "evolution_policy.json"
    )
    ontology = OntologyRepository.from_file(root / "ontology" / "base.ttl")
    shapes = Graph().parse(root / "ontology" / "shapes.ttl", format="turtle")
    return UpdateOrchestrator(
        UpdateEngine(ontology, shapes_graph=shapes),
        artifact_store=artifact_store,
        policy=active_policy,
        graph_store=graph_store,
    )


def build_demo_ingestion_pipeline() -> MultimodalIngestionPipeline:
    root = project_root()
    return build_ingestion_pipeline(
        root / "config" / "entity_registry.json",
        root / "config" / "extraction_rules.json",
    )


def ontology_prompt_context(
    repository: OntologyRepository,
    registry: EntityRegistry | None = None,
    proposed_namespace: str | None = None,
) -> OntologyPromptContext:
    graph = repository.graph
    entity_hints = {}
    if registry is not None:
        entity_hints = {
            label: {"iri": entity.iri, "type": entity.type_iri}
            for entity in registry.entities
            for label in entity.labels
        }
    return OntologyPromptContext(
        ontology_version=repository.version,
        class_iris=sorted(str(item) for item in graph.subjects(RDF.type, OWL.Class)),
        predicate_iris=sorted(
            {
                str(item)
                for kind in (OWL.ObjectProperty, OWL.DatatypeProperty)
                for item in graph.subjects(RDF.type, kind)
            }
        ),
        entity_hints=entity_hints,
        proposed_namespace=proposed_namespace,
    )


def build_runtime_ingestion_pipeline(
    context_provider: Callable[[], OntologyPromptContext] | None = None,
) -> MultimodalIngestionPipeline:
    """Build the stable baseline plus explicitly configured research adapters."""

    root = project_root()
    configure_model_environment(root)
    base = build_demo_ingestion_pipeline()
    registry = EntityRegistry.from_file(root / "config" / "entity_registry.json")
    mapping_payload = json.loads(
        (root / "config" / "semantic_adapters.json").read_text(encoding="utf-8")
    )
    mapping = LabelMapping(
        predicates=mapping_payload.get("relation_labels", {}),
        no_relation_labels=set(mapping_payload.get("no_relation_labels", [])),
    )
    ontology = OntologyRepository.from_file(root / "ontology" / "base.ttl")
    proposed_namespace = mapping_payload.get("proposed_namespace")

    def context() -> OntologyPromptContext:
        current = (
            context_provider()
            if context_provider is not None
            else ontology_prompt_context(ontology)
        )
        if current.entity_hints and current.proposed_namespace:
            return current
        return current.model_copy(
            update={
                "entity_hints": current.entity_hints
                or {
                    label: {"iri": entity.iri, "type": entity.type_iri}
                    for entity in registry.entities
                    for label in entity.labels
                },
                "proposed_namespace": current.proposed_namespace
                or proposed_namespace,
            }
        )
    extractors = {
        **base.extractors,
        "adakgc": AdaKGCRecordExtractor(registry, mapping),
        "continuemkgc": ContinueMKGCPredictionExtractor(registry, mapping),
        **model_extractors_from_env(context),
    }
    return MultimodalIngestionPipeline(extractors, fusion=base.fusion)


def demo_observations() -> list[Observation]:
    ex = "https://example.org/odkg/"
    return [
        Observation(
            subject=f"{ex}project_alpha",
            predicate=f"{ex}fundedBy",
            object=f"{ex}foundation_beta",
            subject_type=f"{ex}ResearchProject",
            object_type=f"{ex}Organization",
            confidence=0.91,
            evidence=[
                Evidence(
                    source_id="demo-document-001",
                    modality="text",
                    excerpt="Project Alpha is funded by Foundation Beta.",
                ),
                Evidence(
                    source_id="demo-image-001",
                    modality="image",
                    locator="region:120,80,460,300",
                ),
            ],
        )
    ]


def main() -> None:
    orchestrator = build_demo_orchestrator()
    result = orchestrator.run(demo_observations())
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

