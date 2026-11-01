from __future__ import annotations

from dataclasses import dataclass

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF, XSD

from .contracts import CandidateFact, OntologyChangeSet, TransactionStatus, UpdateTransaction
from .ontology import OntologyRepository, clone_graph, next_minor
from .validation import ConsistencyValidator


@dataclass(slots=True)
class SystemSnapshot:
    ontology_graph: Graph
    ontology_version: str
    facts_graph: Graph
    transaction_id: str


class UpdateEngine:
    def __init__(
        self,
        ontology: OntologyRepository,
        facts_graph: Graph | None = None,
        shapes_graph: Graph | None = None,
        validator: ConsistencyValidator | None = None,
    ) -> None:
        self.ontology = ontology
        self.facts = facts_graph or Graph()
        self.shapes = shapes_graph
        self.validator = validator or ConsistencyValidator()
        self._history: list[SystemSnapshot] = []

    def prepare(
        self, change_set: OntologyChangeSet, facts: list[CandidateFact]
    ) -> tuple[UpdateTransaction, Graph, Graph]:
        preview_ontology = self.ontology.preview(change_set)
        preview_facts = clone_graph(self.facts)
        self._add_facts(preview_facts, facts)
        validation = self.validator.validate(
            preview_ontology, preview_facts, self.shapes, facts
        )
        transaction = UpdateTransaction(
            base_version=self.ontology.version,
            target_version=next_minor(self.ontology.version),
            change_set=change_set,
            facts=facts,
            validation=validation,
            status=(
                TransactionStatus.PREPARED
                if validation.conforms
                else TransactionStatus.REJECTED
            ),
            rejection_reason=None if validation.conforms else "consistency validation failed",
        )
        return transaction, preview_ontology, preview_facts

    def commit(
        self,
        transaction: UpdateTransaction,
        preview_ontology: Graph,
        preview_facts: Graph,
    ) -> UpdateTransaction:
        if transaction.status != TransactionStatus.PREPARED:
            raise ValueError("only a prepared transaction can be committed")
        if transaction.base_version != self.ontology.version:
            raise ValueError("ontology version changed after transaction preparation")
        snapshot = SystemSnapshot(
            ontology_graph=clone_graph(self.ontology.graph),
            ontology_version=self.ontology.version,
            facts_graph=clone_graph(self.facts),
            transaction_id=transaction.transaction_id,
        )
        self._history.append(snapshot)
        try:
            version = self.ontology.commit(
                preview_ontology, transaction.change_set.change_set_id
            )
            self.facts = preview_facts
            transaction.target_version = version
            transaction.status = TransactionStatus.COMMITTED
            return transaction
        except Exception:
            self._restore(snapshot)
            self._history.pop()
            raise

    def rollback_latest(self) -> str:
        if not self._history:
            raise RuntimeError("no committed transaction is available for rollback")
        snapshot = self._history.pop()
        self._restore(snapshot)
        if self.ontology._history:
            self.ontology._history.pop()
        return snapshot.transaction_id

    def latest_rollback_version(self) -> str:
        if not self._history:
            raise RuntimeError("no committed transaction is available for rollback")
        return self._history[-1].ontology_version

    def _restore(self, snapshot: SystemSnapshot) -> None:
        self.ontology.graph = snapshot.ontology_graph
        self.ontology.version = snapshot.ontology_version
        self.facts = snapshot.facts_graph

    @staticmethod
    def _add_facts(graph: Graph, facts: list[CandidateFact]) -> None:
        for fact in facts:
            subject = URIRef(fact.subject)
            predicate = URIRef(fact.predicate)
            obj = (
                Literal(
                    fact.object,
                    datatype=URIRef(fact.object_type or str(XSD.string)),
                )
                if fact.object_is_literal
                else URIRef(fact.object)
            )
            graph.add((subject, predicate, obj))
            graph.add((subject, RDF.type, URIRef(fact.subject_type)))
            if fact.object_type and not fact.object_is_literal:
                graph.add((URIRef(fact.object), RDF.type, URIRef(fact.object_type)))

