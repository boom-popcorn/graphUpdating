import pytest

from odkg.demo import (
    build_demo_ingestion_pipeline,
    build_demo_orchestrator,
    demo_observations,
)
from odkg.mcp_facade import EvolutionToolFacade
from odkg.storage import ArtifactStore


def test_mcp_facade_exposes_update_version_and_rollback(tmp_path) -> None:
    store = ArtifactStore(tmp_path / "runtime")
    facade = EvolutionToolFacade(build_demo_orchestrator(artifact_store=store))

    result = facade.run_update_cycle(
        [observation.model_dump(mode="json") for observation in demo_observations()]
    )

    assert result["transaction"]["status"] == "committed"
    assert facade.get_ontology_version() == {"version": "v0.2.0"}
    assert facade.list_versions() == {"versions": ["v0.2.0"]}
    assert facade.get_version_manifest("v0.2.0")["version"] == "v0.2.0"

    rollback = facade.rollback_latest()
    assert rollback["version"] == "v0.1.0"
    assert rollback["rolled_back_transaction"] == result["transaction"]["transaction_id"]


def test_mcp_facade_previews_raw_artifacts_without_commit() -> None:
    facade = EvolutionToolFacade(
        build_demo_orchestrator(), ingestion=build_demo_ingestion_pipeline()
    )
    artifacts = [
        {
            "source_id": "text-1",
            "modality": "text",
            "content": "Project Alpha is funded by Foundation Beta.",
        },
        {
            "source_id": "record-1",
            "modality": "structured",
            "content": demo_observations()[0].model_dump(
                mode="json", exclude={"evidence"}
            ),
        },
    ]

    result = facade.ingest_and_preview(artifacts)

    assert result["ingestion"]["fused_count"] == 1
    assert result["pipeline"]["transaction"]["status"] == "prepared"
    assert facade.get_ontology_version() == {"version": "v0.1.0"}
def test_mcp_facade_refuses_empty_ingestion_transaction() -> None:
    facade = EvolutionToolFacade(
        build_demo_orchestrator(), ingestion=build_demo_ingestion_pipeline()
    )

    with pytest.raises(ValueError, match="unsupported_modality"):
        facade.ingest_and_run(
            [{"source_id": "sensor-1", "modality": "lidar", "content": {}}]
        )

    assert facade.get_ontology_version() == {"version": "v0.1.0"}
