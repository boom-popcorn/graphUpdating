from __future__ import annotations

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import OWL, RDF, RDFS

from .contracts import CandidateFact, ValidationIssue, ValidationReport

try:
    from pyshacl import validate as shacl_validate
except ImportError:  # pragma: no cover
    shacl_validate = None


class ConsistencyValidator:
    """Deterministic consistency checks plus SHACL when available."""

    def validate(
        self,
        ontology: Graph,
        facts_graph: Graph,
        shapes: Graph | None,
        candidates: list[CandidateFact],
    ) -> ValidationReport:
        issues = self._structural_checks(ontology, candidates)
        engine = "structural"
        if shapes is not None and shacl_validate is not None:
            conforms, report_graph, _ = shacl_validate(
                data_graph=ontology + facts_graph,
                shacl_graph=shapes,
                ont_graph=ontology,
                inference="rdfs",
                abort_on_first=False,
            )
            engine = "structural+pyshacl"
            if not conforms:
                sh = "http://www.w3.org/ns/shacl#"
                for result in report_graph.subjects(RDF.type, URIRef(f"{sh}ValidationResult")):
                    message = next(
                        report_graph.objects(result, URIRef(f"{sh}resultMessage")),
                        Literal("SHACL constraint violation"),
                    )
                    focus = next(
                        report_graph.objects(result, URIRef(f"{sh}focusNode")), None
                    )
                    issues.append(
                        ValidationIssue(
                            severity="error",
                            code="SHACL_VIOLATION",
                            message=str(message),
                            focus_node=str(focus) if focus else None,
                        )
                    )
        return ValidationReport(
            conforms=not any(issue.severity == "error" for issue in issues),
            issues=issues,
            engine=engine,
        )

    @staticmethod
    def _structural_checks(
        ontology: Graph, candidates: list[CandidateFact]
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for fact in candidates:
            subject_type = URIRef(fact.subject_type)
            if (subject_type, RDF.type, OWL.Class) not in ontology:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="UNDECLARED_SUBJECT_CLASS",
                        message=f"Subject class is not declared: {fact.subject_type}",
                        focus_node=fact.subject_type,
                    )
                )
            if fact.object_type and not fact.object_is_literal:
                object_type = URIRef(fact.object_type)
                if (object_type, RDF.type, OWL.Class) not in ontology:
                    issues.append(
                        ValidationIssue(
                            severity="error",
                            code="UNDECLARED_OBJECT_CLASS",
                            message=f"Object class is not declared: {fact.object_type}",
                            focus_node=fact.object_type,
                        )
                    )
            predicate = URIRef(fact.predicate)
            is_object_property = (predicate, RDF.type, OWL.ObjectProperty) in ontology
            is_data_property = (predicate, RDF.type, OWL.DatatypeProperty) in ontology
            if not (is_object_property or is_data_property):
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="UNDECLARED_PROPERTY",
                        message=f"Property is not declared: {fact.predicate}",
                        focus_node=fact.predicate,
                    )
                )
            elif fact.object_is_literal and is_object_property:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="OBJECT_PROPERTY_LITERAL",
                        message=f"Object property received a literal: {fact.predicate}",
                        focus_node=fact.fact_id,
                    )
                )
            elif not fact.object_is_literal and is_data_property:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="DATA_PROPERTY_RESOURCE",
                        message=f"Datatype property received a resource: {fact.predicate}",
                        focus_node=fact.fact_id,
                    )
                )
            else:
                for domain in ontology.objects(predicate, RDFS.domain):
                    if not _is_subclass_or_same(ontology, subject_type, domain):
                        issues.append(
                            ValidationIssue(
                                severity="error",
                                code="DOMAIN_MISMATCH",
                                message=(
                                    f"{fact.subject_type} is incompatible with domain "
                                    f"{domain} of {fact.predicate}"
                                ),
                                focus_node=fact.fact_id,
                            )
                        )
                if is_object_property:
                    if not fact.object_type:
                        issues.append(
                            ValidationIssue(
                                severity="error",
                                code="MISSING_OBJECT_TYPE",
                                message=f"Object type is required for {fact.predicate}",
                                focus_node=fact.fact_id,
                            )
                        )
                    else:
                        object_type = URIRef(fact.object_type)
                        for range_iri in ontology.objects(predicate, RDFS.range):
                            if not _is_subclass_or_same(
                                ontology, object_type, range_iri
                            ):
                                issues.append(
                                    ValidationIssue(
                                        severity="error",
                                        code="RANGE_MISMATCH",
                                        message=(
                                            f"{fact.object_type} is incompatible with range "
                                            f"{range_iri} of {fact.predicate}"
                                        ),
                                        focus_node=fact.fact_id,
                                    )
                                )
        return issues


def _is_subclass_or_same(ontology: Graph, candidate: URIRef, required: URIRef) -> bool:
    if candidate == required:
        return True
    visited: set[URIRef] = set()
    frontier = [candidate]
    while frontier:
        current = frontier.pop()
        if current in visited:
            continue
        visited.add(current)
        for parent in ontology.objects(current, RDFS.subClassOf):
            if parent == required:
                return True
            if isinstance(parent, URIRef):
                frontier.append(parent)
    return False

