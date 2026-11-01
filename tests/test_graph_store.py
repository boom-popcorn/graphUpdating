from odkg.contracts import TransactionStatus
from odkg.demo import build_demo_orchestrator, demo_observations
from odkg.graph_store import InMemoryGraphStore
from odkg.mcp_facade import EvolutionToolFacade


class FailingAfterBootstrapStore(InMemoryGraphStore):
    def publish_snapshot(self, ontology, facts, version, transaction_id):
        if transaction_id != "bootstrap":
            raise RuntimeError("simulated graph outage")
        return super().publish_snapshot(
            ontology, facts, version, transaction_id
        )


def test_graph_store_tracks_commit_and_rollback() -> None:
    store = InMemoryGraphStore("test")
    facade = EvolutionToolFacade(build_demo_orchestrator(graph_store=store))
    assert store.status().active_version == "v0.1.0"

    result = facade.run_update_cycle(
        [item.model_dump(mode="json") for item in demo_observations()]
    )
    assert result["transaction"]["status"] == "committed"
    assert store.status().active_version == "v0.2.0"

    facade.rollback_latest()
    assert store.status().active_version == "v0.1.0"


def test_graph_failure_rolls_back_in_memory_transaction() -> None:
    store = FailingAfterBootstrapStore("test")
    orchestrator = build_demo_orchestrator(graph_store=store)
    result = orchestrator.run(demo_observations())

    assert result.transaction.status == TransactionStatus.ROLLED_BACK
    assert "graph backend publication failed" in (result.transaction.rejection_reason or "")
    assert orchestrator.engine.ontology.version == "v0.1.0"
    assert len(orchestrator.engine.facts) == 0
    assert store.status().active_version == "v0.1.0"
