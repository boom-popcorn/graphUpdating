import json

from odkg.demo import build_demo_ingestion_pipeline, build_demo_orchestrator, project_root
from odkg.ingestion import RawArtifact


def example_artifacts() -> list[RawArtifact]:
    root = project_root() / "examples" / "multimodal"
    return [
        RawArtifact(
            source_id="text-1",
            modality="text",
            content=(root / "document.txt").read_text(encoding="utf-8"),
        ),
        RawArtifact(
            source_id="table-1",
            modality="structured",
            content=json.loads((root / "structured.json").read_text(encoding="utf-8")),
        ),
        RawArtifact(
            source_id="vision-1",
            modality="image",
            content=json.loads((root / "detections.json").read_text(encoding="utf-8")),
        ),
    ]


def test_multimodal_candidates_are_conservatively_fused() -> None:
    result = build_demo_ingestion_pipeline().ingest(example_artifacts())

    assert result.artifact_count == 3
    assert result.candidate_count == 3
    assert result.fused_count == 1
    assert result.issues == []
    observation = result.observations[0]
    assert observation.confidence == 0.98
    assert {item.modality for item in observation.evidence} == {
        "text",
        "structured",
        "image",
    }


def test_multimodal_ingestion_reaches_transaction_preview() -> None:
    ingestion = build_demo_ingestion_pipeline().ingest(example_artifacts())
    orchestrator = build_demo_orchestrator()
    result = orchestrator.run(ingestion.observations, dry_run=True)

    assert result.transaction.status == "prepared"
    assert result.transaction.requires_human_review is False
    assert orchestrator.engine.ontology.version == "v0.1.0"


def test_unsupported_modality_is_auditable_not_silently_dropped() -> None:
    result = build_demo_ingestion_pipeline().ingest(
        [RawArtifact(source_id="sensor-1", modality="lidar", content={})]
    )

    assert result.observations == []
    assert result.issues[0].code == "unsupported_modality"
