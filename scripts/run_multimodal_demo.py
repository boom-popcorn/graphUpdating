from __future__ import annotations

import argparse
import json
from pathlib import Path

from odkg.demo import (
    build_demo_ingestion_pipeline,
    build_demo_orchestrator,
    project_root,
)
from odkg.ingestion import RawArtifact
from odkg.storage import ArtifactStore


def load_artifacts(root: Path) -> list[RawArtifact]:
    example = root / "examples" / "multimodal"
    return [
        RawArtifact(
            source_id="document-001",
            modality="text",
            content=(example / "document.txt").read_text(encoding="utf-8"),
        ),
        RawArtifact(
            source_id="table-001",
            modality="structured",
            content=json.loads((example / "structured.json").read_text(encoding="utf-8")),
        ),
        RawArtifact(
            source_id="image-model-001",
            modality="image",
            content=json.loads((example / "detections.json").read_text(encoding="utf-8")),
        ),
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--commit",
        action="store_true",
        help="commit the validated transaction; default is a side-effect-free preview",
    )
    parser.add_argument(
        "--runtime-dir",
        type=Path,
        default=None,
        help="optional persistent artifact directory",
    )
    args = parser.parse_args()

    root = project_root()
    store = ArtifactStore(args.runtime_dir) if args.runtime_dir else None
    orchestrator = build_demo_orchestrator(artifact_store=store)
    ingestion = build_demo_ingestion_pipeline().ingest(load_artifacts(root))
    result = orchestrator.run(ingestion.observations, dry_run=not args.commit)
    print(
        json.dumps(
            {
                "ingestion": ingestion.model_dump(mode="json"),
                "pipeline": result.model_dump(mode="json"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
