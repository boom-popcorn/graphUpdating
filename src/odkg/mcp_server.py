from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from mcp.server import MCPServer

from .demo import (
    build_demo_orchestrator,
    build_maritime_orchestrator,
    build_runtime_ingestion_pipeline,
    ontology_prompt_context,
    project_root,
)
from .contracts import Observation
from .llm_harness import LLMMultiAgentHarness, OpenAICompatibleRoleClient
from .mcp_facade import EvolutionToolFacade
from .storage import ArtifactStore
from .graph_store import neo4j_store_from_env
from .semantic_adapters import adapter_status_from_env

mcp = MCPServer("ontology-evolution-kg")
runtime_root = Path(
    os.environ.get("ODKG_RUNTIME_DIR", Path.cwd() / "runtime")
).resolve()
orchestrator_factory = (
    build_maritime_orchestrator
    if os.environ.get("ODKG_DOMAIN", "").casefold() == "maritime"
    else build_demo_orchestrator
)
orchestrator = orchestrator_factory(
    artifact_store=ArtifactStore(runtime_root),
    graph_store=neo4j_store_from_env(),
)
facade = EvolutionToolFacade(
    orchestrator,
    ingestion=build_runtime_ingestion_pipeline(
        lambda: ontology_prompt_context(orchestrator.engine.ontology)
    ),
)


def _model_harness() -> LLMMultiAgentHarness:
    client = OpenAICompatibleRoleClient.from_project_environment(str(project_root()))
    return LLMMultiAgentHarness(orchestrator, client)


@mcp.tool()
def get_ontology_version() -> dict[str, str]:
    """Return the active ontology version."""
    return facade.get_ontology_version()


@mcp.tool()
def get_evolution_policy() -> dict[str, Any]:
    """Return the active, machine-readable ontology-evolution policy."""
    return facade.get_evolution_policy()


@mcp.tool()
def get_graph_status() -> dict[str, Any]:
    """Return the configured graph backend and its active version."""
    return facade.get_graph_status()


@mcp.tool()
def list_semantic_adapters() -> dict[str, Any]:
    """List deterministic, model-backed and research-baseline adapters."""
    return {"adapters": adapter_status_from_env()}


@mcp.tool()
def get_llm_harness_status() -> dict[str, Any]:
    """Report whether the model-driven multi-agent Harness is configured."""
    from .environment import configure_model_environment

    status = configure_model_environment(project_root())
    return {
        "configured": bool(status["base_url_configured"] and status["model"]),
        "model": status["model"],
        "roles": ["semantic_perception", "ontology_evolution", "decision_planning", "action_execution"],
        "deterministic_tools": ["schema_validation", "owl_shacl_validation", "transaction_commit", "rollback"],
    }


@mcp.tool()
def llm_semantic_perception(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Let the model construct maritime semantic facts directly from events."""
    harness = _model_harness()
    trace = harness.perception.decide(
        {
            "events": events,
            "ontology_version": orchestrator.engine.ontology.version,
            "ontology_namespace": "https://example.org/maritime-kg/ontology/",
            "resource_namespace": "https://example.org/maritime-kg/resource/",
        }
    )
    return {
        "candidates": trace.decision.candidate_facts,
        "trace": trace.model_dump(mode="json"),
    }


@mcp.tool()
def llm_ontology_evolution(observations: list[dict[str, Any]]) -> dict[str, Any]:
    """Run model-driven ontology evolution over model-proposed facts."""
    harness = _model_harness()
    normalized = [Observation.model_validate(item) for item in observations]
    trace = harness.evolution.decide(
        {
            "ontology_version": orchestrator.engine.ontology.version,
            "candidate_facts": observations,
        }
    )
    return {"candidate_count": len(normalized), "trace": trace.model_dump(mode="json")}


@mcp.tool()
def llm_decision_planning(pipeline_preview: dict[str, Any]) -> dict[str, Any]:
    """Run the model-driven decision-planning role over validated preview data."""
    trace = _model_harness().planning.decide({"pipeline_preview": pipeline_preview})
    return {"trace": trace.model_dump(mode="json")}


@mcp.tool()
def llm_action_execution(action_plan: dict[str, Any]) -> dict[str, Any]:
    """Compile a model-generated plan into guarded atomic operation intents."""
    trace = _model_harness().execution.decide(action_plan)
    return {"trace": trace.model_dump(mode="json")}


@mcp.tool()
def run_llm_multi_agent_maritime(
    events: list[dict[str, Any]],
    dry_run: bool = True,
    human_approved: bool = False,
) -> dict[str, Any]:
    """Run four LLM roles followed by safety validation and guarded execution."""
    return _model_harness().run(
        events, dry_run=dry_run, human_approved=human_approved
    ).model_dump(mode="json")


@mcp.tool()
def list_versions() -> dict[str, list[str]]:
    """List persisted ontology/KG version packages."""
    return facade.list_versions()


@mcp.tool()
def get_version_manifest(version: str) -> dict[str, Any]:
    """Read hashes and metadata for one persisted version package."""
    return facade.get_version_manifest(version)


@mcp.tool()
def run_update_cycle(
    observations: list[dict[str, Any]], human_approved: bool = False
) -> dict[str, Any]:
    """Plan, validate and conditionally commit an ontology-driven KG update."""
    return facade.run_update_cycle(observations, human_approved=human_approved)


@mcp.tool()
def preview_update_cycle(observations: list[dict[str, Any]]) -> dict[str, Any]:
    """Dry-run an update and return its changes, validation and review decision."""
    return facade.preview_update_cycle(observations)


@mcp.tool()
def preview_maritime_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Normalize AIS/radar events and dry-run the maritime update cycle."""
    return facade.preview_maritime_events(events)


@mcp.tool()
def run_maritime_events(
    events: list[dict[str, Any]], human_approved: bool = False
) -> dict[str, Any]:
    """Normalize AIS/radar events and conditionally commit the update."""
    return facade.run_maritime_events(events, human_approved=human_approved)


@mcp.tool()
def ingest_and_preview(artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract, fuse and dry-run raw multimodal artifacts without changing state."""
    return facade.ingest_and_preview(artifacts)


@mcp.tool()
def ingest_and_run(
    artifacts: list[dict[str, Any]], human_approved: bool = False
) -> dict[str, Any]:
    """Extract, fuse and conditionally commit raw multimodal artifacts."""
    return facade.ingest_and_run(artifacts, human_approved=human_approved)


@mcp.tool()
def rollback_latest() -> dict[str, str]:
    """Atomically roll back the latest committed ontology and KG transaction."""
    return facade.rollback_latest()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()

