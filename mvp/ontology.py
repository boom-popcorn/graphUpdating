from __future__ import annotations

import json
import hashlib
import os
import subprocess
import tempfile
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from .io import read_json, write_json, write_text


def _turtle_local_name(value: str) -> str:
    safe = "".join(character if character.isalnum() or character in "-_" else "_" for character in value)
    if not safe or not safe[0].isalpha() and safe[0] != "_":
        safe = "e_" + safe
    return safe


class OntologyError(ValueError):
    pass


class OntologyModel:
    def __init__(self, document: dict[str, Any]):
        self.document = document

    @classmethod
    def load(cls, path: Path) -> "OntologyModel":
        return cls(read_json(path))

    @property
    def version(self) -> str:
        return self.document["versionId"]

    @property
    def classes(self) -> dict[str, str | None]:
        return self.document["classes"]

    @property
    def object_properties(self) -> dict[str, dict[str, str]]:
        return self.document["objectProperties"]

    @property
    def rules(self) -> dict[str, dict[str, Any]]:
        return self.document["rules"]

    def ancestors(self, class_id: str) -> set[str]:
        result: set[str] = set()
        parent = self.classes.get(class_id)
        while parent is not None and parent not in result:
            result.add(parent)
            parent = self.classes.get(parent)
        return result

    def is_subclass(self, actual: str, expected: str) -> bool:
        return actual == expected or expected in self.ancestors(actual)

    def inferred_types(self, asserted: set[str]) -> set[str]:
        result = set(asserted)
        for class_id in tuple(asserted):
            result.update(self.ancestors(class_id))
        return result

    def disjoint_conflicts(self, asserted: set[str]) -> list[list[str]]:
        inferred = self.inferred_types(asserted)
        conflicts = []
        for pair in self.document.get("disjointClasses", []):
            if all(class_id in inferred for class_id in pair):
                conflicts.append(pair)
        return conflicts


class OntologyConsistencyChecker:
    """Validate candidate ontologies with the HermiT OWL 2 reasoner."""

    engine = "HermiT"
    _cache: dict[str, dict[str, Any]] = {}

    def __init__(self, jar_path: Path | None = None, java_command: str | None = None):
        self.jar_path = jar_path or Path(__file__).resolve().parents[1] / "hermit" / "target" / "hermit-validator.jar"
        configured_java = os.environ.get("JAVA")
        java_home = os.environ.get("JAVA_HOME")
        bundled_java = Path(java_home) / "bin" / "java.exe" if java_home else None
        self.java_command = java_command or configured_java or (
            str(bundled_java) if bundled_java and bundled_java.is_file() else "java"
        )

    def _reason(self, turtle: str) -> dict[str, Any]:
        cache_key = hashlib.sha256(turtle.encode("utf-8")).hexdigest()
        cached = self._cache.get(cache_key)
        if cached is not None:
            return {
                **cached,
                "cacheHit": True,
                "cachedDurationMs": cached.get("durationMs"),
                "durationMs": 0.0,
            }
        if not self.jar_path.is_file():
            raise RuntimeError(
                f"HermiT bridge not found at {self.jar_path}. Build it with: "
                "powershell -ExecutionPolicy Bypass -File hermit/build.ps1"
            )
        with tempfile.TemporaryDirectory(prefix="maritime_hermit_") as work:
            ontology_path = Path(work) / "candidate.ttl"
            ontology_path.write_text(turtle, encoding="utf-8")
            started = datetime.now().timestamp()
            completed = subprocess.run(
                [self.java_command, "-jar", str(self.jar_path), str(ontology_path)],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=60,
            )
            duration_ms = round((datetime.now().timestamp() - started) * 1000, 3)
        if completed.returncode != 0:
            raise RuntimeError(
                "HermiT invocation failed. "
                f"Exit code {completed.returncode}; stderr: {completed.stderr[-4000:]}"
            )
        lines = [line.strip() for line in completed.stdout.splitlines() if line.strip().startswith("{")]
        if not lines:
            raise RuntimeError(f"HermiT returned no JSON result: {completed.stdout[-2000:]}")
        try:
            report = json.loads(lines[-1])
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"HermiT returned invalid JSON: {lines[-1]}") from exc
        report["durationMs"] = duration_ms
        report["cacheHit"] = False
        self._cache[cache_key] = dict(report)
        return report

    @staticmethod
    def _abox_turtle(
        candidate: "OntologyModel",
        graph: dict[str, Any],
        facts: list[dict[str, str]],
    ) -> str:
        lines: list[str] = []
        type_map = {
            entity_id: set(node.get("types", []))
            for entity_id, node in graph.get("nodes", {}).items()
        }
        relations = list(graph.get("relations", [])) + list(facts)
        for relation in relations:
            signature = candidate.object_properties.get(relation.get("p", ""))
            if signature:
                type_map.setdefault(relation["s"], set()).add(signature["domain"])
                type_map.setdefault(relation["o"], set()).add(signature["range"])
        for entity_id, class_ids in type_map.items():
            local_id = _turtle_local_name(entity_id)
            for class_id in class_ids:
                if class_id in candidate.classes:
                    lines.append(f"maritime:{local_id} a maritime:{class_id} .")
        for relation in relations:
            if relation.get("p") in candidate.object_properties:
                subject = _turtle_local_name(relation["s"])
                object_ = _turtle_local_name(relation["o"])
                lines.append(f"maritime:{subject} maritime:{relation['p']} maritime:{object_} .")
        return "\n".join(lines) + "\n"

    def validate_proposal(
        self,
        current: OntologyModel,
        proposal: dict[str, Any],
        graph: dict[str, Any],
    ) -> tuple[dict[str, Any], "OntologyModel | None"]:
        checks = {
            "baseVersion": proposal.get("baseOntologyVersion") == current.version,
            "classHierarchy": True,
            "domainRangeDeclarations": True,
            "ruleReferences": True,
            "disjointness": True,
        }
        issues: list[str] = []
        candidate = deepcopy(current.document)

        if not checks["baseVersion"]:
            issues.append("proposal baseOntologyVersion is stale")

        for new_class in proposal.get("newClasses", []):
            class_id, parent = new_class.get("id"), new_class.get("parent")
            if not class_id or class_id in candidate["classes"]:
                checks["classHierarchy"] = False
                issues.append(f"class {class_id!r} is empty or already declared")
            elif parent not in candidate["classes"]:
                checks["classHierarchy"] = False
                issues.append(f"parent class {parent!r} is not declared")
            else:
                candidate["classes"][class_id] = parent

        for prop in proposal.get("newObjectProperties", []):
            prop_id = prop.get("id")
            domain, range_ = prop.get("domain"), prop.get("range")
            if prop_id in candidate["objectProperties"]:
                checks["domainRangeDeclarations"] = False
                issues.append(f"object property {prop_id!r} is already declared")
            elif domain not in candidate["classes"] or range_ not in candidate["classes"]:
                checks["domainRangeDeclarations"] = False
                issues.append(f"object property {prop_id!r} has an undeclared domain or range")
            else:
                candidate["objectProperties"][prop_id] = {"domain": domain, "range": range_}

        definitions = {rule["id"]: rule for rule in proposal.get("ruleDefinitions", [])}
        for rule_id in proposal.get("newRules", []):
            if rule_id in candidate["rules"] or rule_id not in definitions:
                checks["ruleReferences"] = False
                issues.append(f"rule {rule_id!r} is duplicated or has no definition")
            else:
                candidate["rules"][rule_id] = definitions[rule_id]

        for fact in proposal.get("validationFacts", []):
            if fact.get("p") not in candidate["objectProperties"]:
                checks["domainRangeDeclarations"] = False
                issues.append(f"validation fact uses undeclared property {fact.get('p')!r}")

        candidate_model = OntologyModel(candidate)
        reasoner_result: dict[str, Any] | None = None
        if all(checks[key] for key in ("baseVersion", "classHierarchy", "domainRangeDeclarations", "ruleReferences")):
            turtle = serialize_turtle(candidate) + "\n" + self._abox_turtle(
                candidate_model, graph, proposal.get("validationFacts", [])
            )
            reasoner_result = self._reason(turtle)
            checks["disjointness"] = bool(reasoner_result["consistent"])
            if not checks["disjointness"]:
                issues.append("HermiT found the candidate ontology and graph assertions inconsistent")

        consistent = all(checks.values()) and reasoner_result is not None
        report = {
            "proposalId": proposal.get("proposalId"),
            "consistent": consistent,
            "engine": self.engine,
            "hermitExecuted": reasoner_result is not None and not reasoner_result.get("cacheHit", False),
            "hermitResultUsed": reasoner_result is not None,
            "cacheHit": bool((reasoner_result or {}).get("cacheHit", False)),
            "checks": checks,
            "unsatisfiableClasses": (reasoner_result or {}).get("unsatisfiableClasses", []),
            "violatedConstraints": issues,
            "reason": None if consistent else "; ".join(issues),
            "durationMs": (reasoner_result or {}).get("durationMs"),
            "cachedDurationMs": (reasoner_result or {}).get("cachedDurationMs"),
        }
        return report, candidate_model if consistent else None


class OntologyAgent:
    name = "ontology_evolution_agent"

    def __init__(self, ontology_root: Path):
        self.ontology_root = ontology_root
        self.checker = OntologyConsistencyChecker()
        self.tool_audit: list[dict[str, Any]] = []

    def load_current(self) -> OntologyModel:
        return OntologyModel.load(self.ontology_root / "00" / "ontology.json")

    def propose(self, delta: dict[str, Any], current: OntologyModel) -> dict[str, Any]:
        new_classes = []
        new_properties = []
        new_rules = []
        definitions = []
        if "SuspectedIllegalFishingActivity" not in current.classes:
            new_classes.append({"id": "SuspectedIllegalFishingActivity", "parent": "FishingEvent"})
        if "hasPotentialIllegalActivity" not in current.object_properties:
            new_properties.append({
                "id": "hasPotentialIllegalActivity",
                "domain": "Plat",
                "range": "SensitiveArea",
            })
        for rule_id in ("R2", "R3"):
            if rule_id not in current.rules:
                new_rules.append(rule_id)
                definitions.append(candidate_rule_definition(rule_id))
        return {
            "proposalId": "EP-001",
            "baseOntologyVersion": current.version,
            "newClasses": new_classes,
            "newObjectProperties": new_properties,
            "newRules": new_rules,
            "ruleDefinitions": definitions,
            "validationFacts": [
                {"s": delta["vesselId"], "p": "hasPotentialIllegalActivity", "o": delta["areaId"]}
            ] if new_properties else [],
            "rationale": "Add the event class, risk relation, and rules required by the newly observed behavior.",
        }

    def validate_ontology(
        self,
        current: OntologyModel,
        proposal: dict[str, Any],
        graph: dict[str, Any],
    ) -> tuple[dict[str, Any], OntologyModel | None]:
        report, candidate_model = self.checker.validate_proposal(current, proposal, graph)
        if candidate_model is None:
            return report, None
        candidate = deepcopy(candidate_model.document)
        candidate["versionId"] = "01"
        candidate["parentVersion"] = current.version
        candidate["proposalId"] = proposal["proposalId"]
        candidate["createdAt"] = proposal.get("createdAt")
        candidate["changeLog"] = {
            "newClasses": [item["id"] for item in proposal["newClasses"]],
            "newObjectProperties": [item["id"] for item in proposal["newObjectProperties"]],
            "newRules": proposal["newRules"],
            "validationEngine": report["engine"],
        }
        report["ontologyVersion"] = "01"
        return report, OntologyModel(candidate)

    def validate_plan(
        self,
        plan: dict[str, Any],
        ontology: OntologyModel,
        graph: dict[str, Any],
        delta: dict[str, Any],
    ) -> dict[str, Any]:
        started_checks: dict[str, bool] = {
            "typeCheck": True,
            "domainRangeCheck": True,
            "mutualExclusionCheck": True,
            "temporalCheck": True,
            "provenanceCheck": True,
            "ruleReferenceCheck": True,
            "impactSubgraphCheck": True,
        }
        suggestions: list[str] = []
        if plan.get("baseOntologyVersion") != ontology.version:
            started_checks["ruleReferenceCheck"] = False
            suggestions.append(f"replan against ontology version {ontology.version}")

        types_by_id = {
            entity_id: set(node.get("types", []))
            for entity_id, node in graph.get("nodes", {}).items()
        }
        for temporal in delta.get("temporalEntities", []):
            types_by_id.setdefault(temporal["id"], set()).add("Instant")
        for individual in plan.get("createIndividuals", []):
            types_by_id[individual["id"]] = {individual["type"]}
            if individual["type"] not in ontology.classes:
                started_checks["typeCheck"] = False
                suggestions.append(f"use a declared class for {individual['id']}")

        for assertion in plan.get("assertTypes", []):
            entity_id, class_id = assertion["id"], assertion["type"]
            if class_id not in ontology.classes or entity_id not in types_by_id:
                started_checks["typeCheck"] = False
                suggestions.append(f"declare {class_id} and its individual before asserting the type")
                continue
            types_by_id[entity_id].add(class_id)
            if ontology.disjoint_conflicts(types_by_id[entity_id]):
                started_checks["mutualExclusionCheck"] = False
                suggestions.append(f"remove a type that conflicts with another type on {entity_id}")

        for relation in plan.get("assertRelations", []):
            subject, property_id, object_id = relation["s"], relation["p"], relation["o"]
            signature = ontology.object_properties.get(property_id)
            if signature is None or subject not in types_by_id or object_id not in types_by_id:
                started_checks["domainRangeCheck"] = False
                suggestions.append(f"use declared entities and relation for {subject} {property_id} {object_id}")
                continue
            subject_types = ontology.inferred_types(types_by_id[subject])
            object_types = ontology.inferred_types(types_by_id[object_id])
            if not any(ontology.is_subclass(item, signature["domain"]) for item in subject_types):
                started_checks["domainRangeCheck"] = False
                suggestions.append(f"{property_id} requires a {signature['domain']} subject")
            if not any(ontology.is_subclass(item, signature["range"]) for item in object_types):
                started_checks["domainRangeCheck"] = False
                suggestions.append(f"{property_id} requires a {signature['range']} object")

        for close in plan.get("closeStates", []):
            existing = graph.get("nodes", {}).get(close["id"])
            valid_from = (existing or {}).get("validFrom") or (existing or {}).get("properties", {}).get("validFrom")
            try:
                time_is_valid = bool(valid_from) and datetime.fromisoformat(
                    close["validTo"].replace("Z", "+00:00")
                ) >= datetime.fromisoformat(valid_from.replace("Z", "+00:00"))
            except (TypeError, ValueError):
                time_is_valid = False
            if not existing or not time_is_valid:
                started_checks["temporalCheck"] = False
                suggestions.append(f"set a valid close time for {close['id']}")
        if delta.get("validFrom") and delta.get("validTo") and delta["validFrom"] > delta["validTo"]:
            started_checks["temporalCheck"] = False
            suggestions.append("order the new state's validFrom and validTo")

        event_ids = {
            item["id"] for item in plan.get("createIndividuals", [])
            if ontology.is_subclass(item.get("type", ""), "Event")
        }
        evidence_by_id = {item["id"]: item for item in delta.get("evidence", [])}
        evidence_for_event: dict[str, list[dict[str, Any]]] = {}
        for attachment in plan.get("attachEvidence", []):
            evidence = evidence_by_id.get(attachment["evidence"])
            if attachment["event"] not in event_ids or evidence is None:
                started_checks["provenanceCheck"] = False
                suggestions.append("attach existing evidence to a planned event")
                continue
            if evidence["subject"] != delta["vesselId"]:
                started_checks["provenanceCheck"] = False
                suggestions.append("attach evidence whose subject matches the event subject")
            evidence_for_event.setdefault(attachment["event"], []).append(evidence)
        for event_id in event_ids:
            if not evidence_for_event.get(event_id):
                started_checks["provenanceCheck"] = False
                suggestions.append(f"attach at least one evidence record to {event_id}")

        for rule_id in plan.get("ruleHits", []):
            if rule_id not in ontology.rules:
                started_checks["ruleReferenceCheck"] = False
                suggestions.append(f"use only rules declared in ontology {ontology.version}")

        allowed = (
            set(plan.get("expectedSubgraph", []))
            | set(evidence_by_id)
            | {item["id"] for item in plan.get("temporalValues", [])}
            | {item["id"] for item in plan.get("evidenceRecords", [])}
        )
        for close in plan.get("closeStates", []):
            if close["id"] not in allowed:
                started_checks["impactSubgraphCheck"] = False
                suggestions.append("limit changes to the declared affected subgraph")
        for individual in plan.get("createIndividuals", []):
            if individual["id"] not in allowed:
                started_checks["impactSubgraphCheck"] = False
                suggestions.append("limit creation to the declared affected subgraph")
        for relation in plan.get("assertRelations", []):
            if relation["s"] not in allowed or relation["o"] not in allowed:
                started_checks["impactSubgraphCheck"] = False
                suggestions.append("limit relationships to the declared affected subgraph")

        return {
            "planId": plan.get("planId"),
            "ontologyVersion": ontology.version,
            "validatorAgent": self.name,
            "passed": all(started_checks.values()),
            "checks": started_checks,
            "suggestions": list(dict.fromkeys(suggestions)),
        }

    def publish(self, ontology: OntologyModel, report: dict[str, Any]) -> None:
        self.tool_audit = []
        for class_id in ontology.document.get("changeLog", {}).get("newClasses", []):
            self.tool_audit.append({"tool": "createClass", "classId": class_id})
        for prop_id in ontology.document.get("changeLog", {}).get("newObjectProperties", []):
            self.tool_audit.append({"tool": "createObjectProperty", "propertyId": prop_id})
        for rule_id in ontology.document.get("changeLog", {}).get("newRules", []):
            self.tool_audit.append({"tool": "addSWRLRule", "ruleId": rule_id})
        version_dir = self.ontology_root / ontology.version
        json_path = version_dir / "ontology.json"
        if json_path.exists():
            if read_json(json_path) != ontology.document:
                raise OntologyError(f"refusing to overwrite a different ontology version at {json_path}")
        else:
            write_json(json_path, ontology.document)
        turtle = serialize_turtle(ontology.document)
        ttl_path = version_dir / "ontology.ttl"
        if ttl_path.exists() and ttl_path.read_text(encoding="utf-8") != turtle:
            raise OntologyError(f"refusing to overwrite a different ontology version at {ttl_path}")
        if not ttl_path.exists():
            write_text(ttl_path, turtle)
        self.tool_audit.append({
            "tool": "publishOntologyVersion",
            "baseVersion": ontology.document["parentVersion"],
            "newVersion": ontology.version,
        })
        write_json(version_dir / "validation_report.json", report)
        index_path = self.ontology_root / "versions.json"
        if index_path.exists():
            index = read_json(index_path)
        else:
            index = {
                "latestVersion": "00",
                "versions": [{"versionId": "00", "path": "00/ontology.ttl"}],
            }
        published = {item["versionId"]: item for item in index.get("versions", [])}
        published[ontology.version] = {
            "versionId": ontology.version,
            "parentVersion": ontology.document["parentVersion"],
            "proposalId": ontology.document["proposalId"],
            "createdAt": ontology.document["createdAt"],
            "path": f"{ontology.version}/ontology.ttl",
            "validationReport": f"{ontology.version}/validation_report.json",
        }
        index["versions"] = sorted(published.values(), key=lambda item: item["versionId"])
        index["latestVersion"] = ontology.version
        write_json(index_path, index)


def candidate_rule_definition(rule_id: str) -> dict[str, Any]:
    if rule_id == "R2":
        return {
            "id": "R2",
            "name": "sensitive_area_risk",
            "swrl": "FishingVessel(?v) ^ hasState(?v, ?s) ^ LoiteringState(?s) ^ locatedIn(?v, ?a) ^ SensitiveArea(?a) -> hasPotentialIllegalActivity(?v, ?a)",
            "when": {"stateType": "LoiteringState", "inSensitiveArea": True},
            "then": {"relation": "hasPotentialIllegalActivity"},
        }
    if rule_id == "R3":
        return {
            "id": "R3",
            "name": "suspected_illegal_fishing_candidate",
            "swrlPattern": "hasPotentialIllegalActivity(?v, ?a) ^ hasState(?v, ?s) ^ durationSeconds(?s, ?d) ^ swrlb:greaterThan(?d, 600) ^ travelDistance(?s, ?m) ^ swrlb:lessThan(?m, 0.5)",
            "plannerAction": "materialize SuspectedIllegalFishingActivity using the deterministic event ID",
            "when": {"durationSecondsGreaterThan": 600, "travelDistanceLessThan": 0.5},
            "then": {"eventType": "SuspectedIllegalFishingActivity"},
        }
    raise OntologyError(f"no candidate definition for rule {rule_id}")


def serialize_turtle(document: dict[str, Any]) -> str:
    lines = [
        "@prefix maritime: <https://example.org/maritime#> .",
        "@prefix owl: <http://www.w3.org/2002/07/owl#> .",
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
        "@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .",
        "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .",
        "",
        f"<https://example.org/maritime/ontology/{document['versionId']}> a owl:Ontology .",
        "",
    ]
    for class_id, parent in sorted(document["classes"].items()):
        line = f"maritime:{class_id} a owl:Class"
        if parent:
            line += f" ; rdfs:subClassOf maritime:{parent}"
        lines.append(line + " .")
    for pair in document.get("disjointClasses", []):
        if len(pair) == 2:
            lines.append(f"maritime:{pair[0]} owl:disjointWith maritime:{pair[1]} .")
    lines.append("")
    for prop_id, signature in sorted(document["objectProperties"].items()):
        lines.extend([
            f"maritime:{prop_id} a owl:ObjectProperty ;",
            f"    rdfs:domain maritime:{signature['domain']} ;",
            f"    rdfs:range maritime:{signature['range']} .",
        ])
    lines.append("")
    datatypes = {"decimal": "decimal", "integer": "integer", "string": "string", "dateTime": "dateTime"}
    for prop_id, signature in sorted(document.get("dataProperties", {}).items()):
        datatype = datatypes.get(signature.get("datatype"), "string")
        lines.extend([
            f"maritime:{prop_id} a owl:DatatypeProperty ;",
            f"    rdfs:domain maritime:{signature['domain']} ;",
            f"    rdfs:range xsd:{datatype} .",
        ])
    lines.append("")
    for rule_id, rule in sorted(document["rules"].items()):
        rule_text = rule.get("swrl", rule.get("swrlPattern", ""))
        swrl = rule_text.replace('"', '\\"')
        lines.append(f'maritime:{rule_id} a maritime:Rule ; rdfs:label "{swrl}" .')
    return "\n".join(lines) + "\n"

