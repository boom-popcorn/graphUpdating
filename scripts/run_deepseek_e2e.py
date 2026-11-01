from __future__ import annotations

import argparse
import json
from pathlib import Path

from odkg.demo import (
    build_demo_orchestrator,
    build_runtime_ingestion_pipeline,
    ontology_prompt_context,
    project_root,
)
from odkg.mcp_facade import EvolutionToolFacade
from odkg.storage import ArtifactStore


def main() -> None:
    root = project_root()
    parser = argparse.ArgumentParser(
        description="Run DeepSeek extraction through the complete ontology/KG update cycle."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=root / "examples" / "deepseek" / "update_artifacts.json",
    )
    parser.add_argument(
        "--runtime",
        type=Path,
        default=root / "runtime" / "deepseek-e2e",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Run the commit path. The default is a non-mutating preview.",
    )
    parser.add_argument(
        "--human-approved",
        action="store_true",
        help="Approve transactions that policy routes to human review.",
    )
    args = parser.parse_args()

    orchestrator = build_demo_orchestrator(artifact_store=ArtifactStore(args.runtime))
    ingestion = build_runtime_ingestion_pipeline(
        lambda: ontology_prompt_context(orchestrator.engine.ontology)
    )
    if "llm_text" not in ingestion.extractors:
        raise SystemExit(
            "DeepSeek LLM adapter is not configured. Run "
            "integrations/deepseek/configure-session.ps1 in this PowerShell session."
        )

    artifacts = json.loads(args.input.read_text(encoding="utf-8"))
    facade = EvolutionToolFacade(orchestrator, ingestion=ingestion)
    result = (
        facade.ingest_and_run(artifacts, human_approved=args.human_approved)
        if args.commit
        else facade.ingest_and_preview(artifacts)
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()

