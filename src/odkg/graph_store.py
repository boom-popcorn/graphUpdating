from __future__ import annotations

import hashlib
import os
import re
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from rdflib import BNode, Graph, Literal


@dataclass(slots=True)
class GraphStoreReport:
    backend: str
    store_id: str
    active_version: str | None
    ontology_triples: int
    fact_triples: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class GraphSnapshotStore(Protocol):
    def publish_snapshot(
        self,
        ontology: Graph,
        facts: Graph,
        version: str,
        transaction_id: str,
    ) -> GraphStoreReport: ...

    def activate_version(self, version: str) -> GraphStoreReport: ...

    def status(self) -> GraphStoreReport: ...


class InMemoryGraphStore:
    """Deterministic snapshot backend used for tests and offline development."""

    def __init__(self, store_id: str = "default") -> None:
        self.store_id = store_id
        self.snapshots: dict[str, tuple[Graph, Graph]] = {}
        self.active_version: str | None = None

    def publish_snapshot(
        self,
        ontology: Graph,
        facts: Graph,
        version: str,
        transaction_id: str,
    ) -> GraphStoreReport:
        self.snapshots[version] = (_clone(ontology), _clone(facts))
        self.active_version = version
        return self.status()

    def activate_version(self, version: str) -> GraphStoreReport:
        if version not in self.snapshots:
            raise KeyError(f"graph snapshot does not exist: {version}")
        self.active_version = version
        return self.status()

    def status(self) -> GraphStoreReport:
        ontology, facts = self.snapshots.get(
            self.active_version or "", (Graph(), Graph())
        )
        return GraphStoreReport(
            backend="memory",
            store_id=self.store_id,
            active_version=self.active_version,
            ontology_triples=len(ontology),
            fact_triples=len(facts),
        )


class Neo4jGraphStore:
    """Versioned RDF snapshot projection stored transactionally in Neo4j.

    Every RDF triple becomes an ``ODKG_RDF`` relationship. Old versions remain
    immutable; rollback only moves the store's active-version pointer.
    """

    def __init__(
        self,
        uri: str,
        user: str,
        password: str,
        database: str | None = None,
        store_id: str = "default",
        driver: Any | None = None,
    ) -> None:
        if driver is None:
            from neo4j import GraphDatabase

            driver = GraphDatabase.driver(uri, auth=(user, password))
        self.driver = driver
        self.database = database
        self.store_id = store_id

    def ensure_schema(self) -> None:
        self.driver.verify_connectivity()
        server_info = self.driver.get_server_info()
        agent = str(getattr(server_info, "agent", ""))
        match = re.search(r"Neo4j/(\d+)", agent)
        if match and int(match.group(1)) < 5:
            raise RuntimeError(
                f"Neo4j 5 or newer is required by the snapshot schema; found {agent}"
            )
        queries = [
            "CREATE CONSTRAINT odkg_term_key IF NOT EXISTS "
            "FOR (n:ODKGTerm) REQUIRE (n.store_id, n.key) IS UNIQUE",
            "CREATE CONSTRAINT odkg_store_id IF NOT EXISTS "
            "FOR (n:ODKGStore) REQUIRE n.store_id IS UNIQUE",
        ]
        with self.driver.session(database=self.database) as session:
            for query in queries:
                session.run(query).consume()

    def publish_snapshot(
        self,
        ontology: Graph,
        facts: Graph,
        version: str,
        transaction_id: str,
    ) -> GraphStoreReport:
        rows = _graph_rows(ontology, "ontology") + _graph_rows(facts, "facts")
        ontology_count = len(ontology)
        fact_count = len(facts)
        with self.driver.session(database=self.database) as session:
            session.execute_write(
                self._write_snapshot,
                self.store_id,
                version,
                transaction_id,
                rows,
                ontology_count,
                fact_count,
            )
        return self.status()

    @staticmethod
    def _write_snapshot(
        tx: Any,
        store_id: str,
        version: str,
        transaction_id: str,
        rows: list[dict[str, Any]],
        ontology_count: int,
        fact_count: int,
    ) -> None:
        tx.run(
            """
            MERGE (store:ODKGStore {store_id: $store_id})
            SET store.active_version = $version,
                store.last_transaction_id = $transaction_id,
                store.ontology_triples = $ontology_count,
                store.fact_triples = $fact_count
            WITH store
            UNWIND $rows AS row
            MERGE (subject:ODKGTerm {store_id: $store_id, key: row.subject_key})
            SET subject.kind = row.subject_kind,
                subject.value = row.subject_value
            MERGE (object:ODKGTerm {store_id: $store_id, key: row.object_key})
            SET object.kind = row.object_kind,
                object.value = row.object_value,
                object.datatype = row.datatype,
                object.language = row.language
            MERGE (subject)-[edge:ODKG_RDF {
                store_id: $store_id,
                version: $version,
                key: row.triple_key
            }]->(object)
            SET edge.predicate = row.predicate,
                edge.graph = row.graph,
                edge.transaction_id = $transaction_id
            """,
            store_id=store_id,
            version=version,
            transaction_id=transaction_id,
            rows=rows,
            ontology_count=ontology_count,
            fact_count=fact_count,
        ).consume()

    def activate_version(self, version: str) -> GraphStoreReport:
        with self.driver.session(database=self.database) as session:
            record = session.execute_write(
                self._activate, self.store_id, version
            )
        if not record:
            raise KeyError(f"graph snapshot does not exist: {version}")
        return self.status()

    @staticmethod
    def _activate(tx: Any, store_id: str, version: str) -> bool:
        record = tx.run(
            """
            MATCH (:ODKGTerm {store_id: $store_id})
                  -[edge:ODKG_RDF {store_id: $store_id, version: $version}]->()
            WITH count(edge) AS triples,
                 count(CASE WHEN edge.graph = 'ontology' THEN 1 END)
                   AS ontology_triples,
                 count(CASE WHEN edge.graph = 'facts' THEN 1 END)
                   AS fact_triples
            WHERE triples > 0
            MERGE (store:ODKGStore {store_id: $store_id})
            SET store.active_version = $version,
                store.ontology_triples = ontology_triples,
                store.fact_triples = fact_triples
            RETURN triples
            """,
            store_id=store_id,
            version=version,
        ).single()
        return record is not None

    def status(self) -> GraphStoreReport:
        with self.driver.session(database=self.database) as session:
            record = session.run(
                """
                OPTIONAL MATCH (store:ODKGStore {store_id: $store_id})
                RETURN store.active_version AS active_version,
                       coalesce(store.ontology_triples, 0) AS ontology_triples,
                       coalesce(store.fact_triples, 0) AS fact_triples
                """,
                store_id=self.store_id,
            ).single()
        return GraphStoreReport(
            backend="neo4j",
            store_id=self.store_id,
            active_version=record["active_version"] if record else None,
            ontology_triples=int(record["ontology_triples"] if record else 0),
            fact_triples=int(record["fact_triples"] if record else 0),
        )

    def close(self) -> None:
        self.driver.close()


def _clone(graph: Graph) -> Graph:
    clone = Graph()
    for triple in graph:
        clone.add(triple)
    return clone


def _term(node: Any) -> tuple[str, str, str | None, str | None, str]:
    if isinstance(node, Literal):
        datatype = str(node.datatype) if node.datatype else None
        language = node.language
        value = str(node)
        raw_key = f"literal|{value}|{datatype or ''}|{language or ''}"
        return "literal", value, datatype, language, _digest(raw_key)
    kind = "blank" if isinstance(node, BNode) else "iri"
    value = str(node)
    return kind, value, None, None, _digest(f"{kind}|{value}")


def _graph_rows(graph: Graph, graph_name: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for subject, predicate, obj in graph:
        subject_kind, subject_value, _, _, subject_key = _term(subject)
        object_kind, object_value, datatype, language, object_key = _term(obj)
        triple_key = _digest(
            f"{graph_name}|{subject_key}|{predicate}|{object_key}"
        )
        rows.append(
            {
                "subject_key": subject_key,
                "subject_kind": subject_kind,
                "subject_value": subject_value,
                "predicate": str(predicate),
                "object_key": object_key,
                "object_kind": object_kind,
                "object_value": object_value,
                "datatype": datatype,
                "language": language,
                "graph": graph_name,
                "triple_key": triple_key,
            }
        )
    return rows


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def neo4j_store_from_env() -> Neo4jGraphStore | None:
    uri = os.environ.get("ODKG_NEO4J_URI")
    if not uri:
        return None
    user = os.environ.get("ODKG_NEO4J_USER")
    password = os.environ.get("ODKG_NEO4J_PASSWORD")
    if not user or not password:
        raise RuntimeError(
            "ODKG_NEO4J_USER and ODKG_NEO4J_PASSWORD are required when "
            "ODKG_NEO4J_URI is configured"
        )
    store = Neo4jGraphStore(
        uri=uri,
        user=user,
        password=password,
        database=os.environ.get("ODKG_NEO4J_DATABASE") or None,
        store_id=os.environ.get("ODKG_NEO4J_STORE_ID", "default"),
    )
    store.ensure_schema()
    return store
