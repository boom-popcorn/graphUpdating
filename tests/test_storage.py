import json

from odkg.demo import build_demo_orchestrator, demo_observations
from odkg.storage import ArtifactStore


def test_committed_update_persists_version_and_audit(tmp_path) -> None:
    store = ArtifactStore(tmp_path / "runtime")
    orchestrator = build_demo_orchestrator(artifact_store=store)
    result = orchestrator.run(demo_observations())

    version_dir = store.versions / "v0.2.0"
    assert (version_dir / "ontology.ttl").exists()
    assert (version_dir / "facts.ttl").exists()
    assert (version_dir / "changes.kgcl").exists()
    assert (version_dir / "changes.och.ttl").exists()
    assert (version_dir / "policy.json").exists()
    manifest = json.loads((version_dir / "manifest.json").read_text("utf-8"))
    assert manifest["transaction_id"] == result.transaction.transaction_id
    assert manifest["policy_id"] == "generic-evolution-v1"
    assert len(manifest["files"]["ontology.ttl"]["sha256"]) == 64
    transaction_file = store.transactions / f"{result.transaction.transaction_id}.json"
    assert transaction_file.exists()
    assert store.audit.exists()
    assert store.list_versions() == ["v0.2.0"]
    assert store.get_manifest("v0.2.0")["version"] == "v0.2.0"

