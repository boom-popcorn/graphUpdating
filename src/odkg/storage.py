from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rdflib import Graph

from .adapters.kgcl import to_kgcl_commands
from .adapters.ontoripple import to_och_graph
from .contracts import PipelineResult, TransactionStatus
from .policy import EvolutionPolicy


class ArtifactStore:
    """Persistent, append-audited storage for versions and transactions."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.versions = self.root / "versions"
        self.transactions = self.root / "transactions"
        self.audit = self.root / "audit" / "events.jsonl"
        for directory in (self.versions, self.transactions, self.audit.parent):
            directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def record_pipeline_result(
        self,
        result: PipelineResult,
        ontology: Graph,
        facts: Graph,
        shapes: Graph | None = None,
        policy: EvolutionPolicy | None = None,
    ) -> Path:
        with self._lock:
            transaction = result.transaction
            transaction_path = self.transactions / f"{transaction.transaction_id}.json"
            self._atomic_text(
                transaction_path,
                json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2),
            )
            if transaction.status == TransactionStatus.COMMITTED:
                self._write_version(result, ontology, facts, shapes, policy)
            for record in result.provenance:
                self._append_audit(record.model_dump(mode="json"))
            self._append_audit(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "event": "transaction_result",
                    "transaction_id": transaction.transaction_id,
                    "status": transaction.status,
                    "version": transaction.target_version or transaction.base_version,
                    "rejection_reason": transaction.rejection_reason,
                }
            )
            return transaction_path

    def record_rollback(self, transaction_id: str, restored_version: str) -> None:
        with self._lock:
            self._append_audit(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "event": "transaction_rollback",
                    "transaction_id": transaction_id,
                    "restored_version": restored_version,
                }
            )

    def list_versions(self) -> list[str]:
        return sorted(
            path.name
            for path in self.versions.iterdir()
            if path.is_dir() and (path / "manifest.json").exists()
        )

    def get_manifest(self, version: str) -> dict[str, Any]:
        if version not in self.list_versions():
            raise KeyError(f"unknown version: {version}")
        return json.loads(
            (self.versions / version / "manifest.json").read_text(encoding="utf-8")
        )

    def _write_version(
        self,
        result: PipelineResult,
        ontology: Graph,
        facts: Graph,
        shapes: Graph | None,
        policy: EvolutionPolicy | None,
    ) -> None:
        transaction = result.transaction
        if not transaction.target_version:
            raise ValueError("committed transaction has no target version")
        version_dir = self.versions / transaction.target_version
        version_dir.mkdir(parents=True, exist_ok=True)
        ontology_path = version_dir / "ontology.ttl"
        facts_path = version_dir / "facts.ttl"
        changes_path = version_dir / "changes.kgcl"
        och_path = version_dir / "changes.och.ttl"
        ontology.serialize(destination=ontology_path, format="turtle")
        facts.serialize(destination=facts_path, format="turtle")
        commands = to_kgcl_commands(transaction.change_set)
        self._atomic_text(changes_path, "\n".join(commands) + "\n")
        to_och_graph(transaction.change_set).serialize(
            destination=och_path, format="turtle"
        )
        files = [ontology_path, facts_path, changes_path, och_path]
        if policy is not None:
            policy_path = version_dir / "policy.json"
            self._atomic_text(policy_path, policy.canonical_json() + "\n")
            files.append(policy_path)
        if shapes is not None:
            shapes_path = version_dir / "shapes.ttl"
            shapes.serialize(destination=shapes_path, format="turtle")
            files.append(shapes_path)
        manifest = {
            "version": transaction.target_version,
            "base_version": transaction.base_version,
            "transaction_id": transaction.transaction_id,
            "change_set_id": transaction.change_set.change_set_id,
            "policy_id": transaction.change_set.policy_id,
            "requires_human_review": transaction.requires_human_review,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "ontology_triples": len(ontology),
            "fact_triples": len(facts),
            "files": {
                path.name: {"sha256": self._sha256(path), "bytes": path.stat().st_size}
                for path in files
            },
        }
        self._atomic_text(
            version_dir / "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2),
        )

    def _append_audit(self, event: dict[str, Any]) -> None:
        with self.audit.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(65536), b""):
                digest.update(block)
        return digest.hexdigest()

    @staticmethod
    def _atomic_text(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, path)
        except Exception:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise

