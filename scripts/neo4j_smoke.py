from __future__ import annotations

import json
import os

from odkg.demo import build_demo_orchestrator, demo_observations
from odkg.graph_store import neo4j_store_from_env


def main() -> None:
    if not os.environ.get("ODKG_NEO4J_URI"):
        raise RuntimeError(
            "set ODKG_NEO4J_URI, ODKG_NEO4J_USER and ODKG_NEO4J_PASSWORD first"
        )
    store = neo4j_store_from_env()
    if store is None:  # pragma: no cover - guarded above
        raise RuntimeError("Neo4j store could not be constructed")
    try:
        orchestrator = build_demo_orchestrator(graph_store=store)
        before = store.status().to_dict()
        result = orchestrator.run(demo_observations())
        after_commit = store.status().to_dict()
        previous = orchestrator.engine.latest_rollback_version()
        store.activate_version(previous)
        orchestrator.engine.rollback_latest()
        after_rollback = store.status().to_dict()
        print(
            json.dumps(
                {
                    "before": before,
                    "transaction": result.transaction.model_dump(mode="json"),
                    "after_commit": after_commit,
                    "after_rollback": after_rollback,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        store.close()


if __name__ == "__main__":
    main()
