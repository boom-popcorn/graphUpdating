from __future__ import annotations

from typing import Any

from .contracts import Observation
from .ingestion import IngestionResult, MultimodalIngestionPipeline, RawArtifact
from .orchestrator import UpdateOrchestrator
from .domains.maritime import maritime_events_to_observations


class EvolutionToolFacade:
    """Stable tool boundary to expose through any MCP SDK/runtime adapter."""

    def __init__(
        self,
        orchestrator: UpdateOrchestrator,
        ingestion: MultimodalIngestionPipeline | None = None,
    ) -> None:
        self.orchestrator = orchestrator
        self.ingestion = ingestion

    def run_update_cycle(
        self, observations: list[dict[str, Any]], human_approved: bool = False
    ) -> dict[str, Any]:
        result = self.orchestrator.run(
            [Observation.model_validate(item) for item in observations],
            human_approved=human_approved,
        )
        return result.model_dump(mode="json")

    def preview_update_cycle(
        self, observations: list[dict[str, Any]]
    ) -> dict[str, Any]:
        result = self.orchestrator.run(
            [Observation.model_validate(item) for item in observations],
            dry_run=True,
        )
        return result.model_dump(mode="json")

    def preview_maritime_events(
        self, events: list[dict[str, Any]]
    ) -> dict[str, Any]:
        observations = maritime_events_to_observations(events)
        self._require_maritime_observations(observations)
        result = self.orchestrator.run(observations, dry_run=True)
        return {
            "normalized_observations": [
                item.model_dump(mode="json") for item in observations
            ],
            "pipeline": result.model_dump(mode="json"),
        }

    def run_maritime_events(
        self,
        events: list[dict[str, Any]],
        human_approved: bool = False,
    ) -> dict[str, Any]:
        observations = maritime_events_to_observations(events)
        self._require_maritime_observations(observations)
        result = self.orchestrator.run(
            observations, human_approved=human_approved
        )
        return {
            "normalized_observations": [
                item.model_dump(mode="json") for item in observations
            ],
            "pipeline": result.model_dump(mode="json"),
        }

    @staticmethod
    def _require_maritime_observations(observations: list[Observation]) -> None:
        if not observations:
            raise ValueError(
                "maritime events produced no candidate observation: "
                "check cross-modal evidence, duration and rule thresholds"
            )

    def ingest_and_preview(
        self, artifacts: list[dict[str, Any]]
    ) -> dict[str, Any]:
        ingestion = self._ingest(artifacts)
        result = self.orchestrator.run(ingestion.observations, dry_run=True)
        return {
            "ingestion": ingestion.model_dump(mode="json"),
            "pipeline": result.model_dump(mode="json"),
        }

    def ingest_and_run(
        self,
        artifacts: list[dict[str, Any]],
        human_approved: bool = False,
    ) -> dict[str, Any]:
        ingestion = self._ingest(artifacts)
        result = self.orchestrator.run(
            ingestion.observations, human_approved=human_approved
        )
        return {
            "ingestion": ingestion.model_dump(mode="json"),
            "pipeline": result.model_dump(mode="json"),
        }

    def _ingest(self, artifacts: list[dict[str, Any]]) -> IngestionResult:
        if self.ingestion is None:
            raise RuntimeError("multimodal ingestion is not configured")
        result = self.ingestion.ingest(
            RawArtifact.model_validate(item) for item in artifacts
        )
        if not result.observations:
            details = "; ".join(
                f"{issue.code}: {issue.message}" for issue in result.issues
            ) or "no extractor produced a candidate relation"
            raise ValueError(f"ingestion produced no observations: {details}")
        return result

    def get_ontology_version(self) -> dict[str, str]:
        return {"version": self.orchestrator.engine.ontology.version}

    def get_evolution_policy(self) -> dict[str, Any]:
        return self.orchestrator.policy.model_dump(mode="json")

    def get_graph_status(self) -> dict[str, Any]:
        if self.orchestrator.graph_store is None:
            return {"backend": "disabled", "active_version": None}
        return self.orchestrator.graph_store.status().to_dict()

    def list_versions(self) -> dict[str, list[str]]:
        if self.orchestrator.artifact_store is None:
            return {"versions": []}
        return {"versions": self.orchestrator.artifact_store.list_versions()}

    def get_version_manifest(self, version: str) -> dict[str, Any]:
        if self.orchestrator.artifact_store is None:
            raise RuntimeError("artifact persistence is not configured")
        return self.orchestrator.artifact_store.get_manifest(version)

    def rollback_latest(self) -> dict[str, str]:
        restored_version = self.orchestrator.engine.latest_rollback_version()
        if self.orchestrator.graph_store is not None:
            self.orchestrator.graph_store.activate_version(restored_version)
        transaction_id = self.orchestrator.engine.rollback_latest()
        if self.orchestrator.artifact_store is not None:
            self.orchestrator.artifact_store.record_rollback(
                transaction_id, self.orchestrator.engine.ontology.version
            )
        return {
            "rolled_back_transaction": transaction_id,
            "version": self.orchestrator.engine.ontology.version,
        }

