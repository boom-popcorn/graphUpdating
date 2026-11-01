from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field, ValidationError

from .contracts import Evidence, Observation


class RawArtifact(BaseModel):
    """One input item before semantic normalization.

    ``content`` is text for the built-in text extractor and a JSON-compatible
    relation payload for structured/model-output adapters. Binary decoding and
    model inference intentionally stay behind external adapters.
    """

    source_id: str
    modality: str
    content: Any
    locator: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EntityReference(BaseModel):
    iri: str
    type_iri: str
    labels: list[str] = Field(default_factory=list)


class ExtractionRule(BaseModel):
    rule_id: str
    pattern: str
    predicate: str
    confidence: float = Field(ge=0.0, le=1.0)


class IngestionIssue(BaseModel):
    source_id: str
    code: str
    message: str
    locator: str | None = None


class ExtractorOutput(BaseModel):
    candidates: list[Observation] = Field(default_factory=list)
    issues: list[IngestionIssue] = Field(default_factory=list)


class IngestionResult(BaseModel):
    observations: list[Observation]
    issues: list[IngestionIssue]
    artifact_count: int
    candidate_count: int
    fused_count: int


class ModalityExtractor(Protocol):
    def extract(self, artifact: RawArtifact) -> ExtractorOutput: ...


class EntityRegistry:
    def __init__(self, entities: Iterable[EntityReference]) -> None:
        self.entities = tuple(entities)
        self._by_label: dict[str, EntityReference] = {}
        for entity in self.entities:
            for label in [entity.iri, *entity.labels]:
                key = self._normalize(label)
                existing = self._by_label.get(key)
                if existing is not None and existing.iri != entity.iri:
                    raise ValueError(f"ambiguous entity label: {label}")
                self._by_label[key] = entity

    @classmethod
    def from_file(cls, path: str | Path) -> "EntityRegistry":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(EntityReference.model_validate(item) for item in payload["entities"])

    def resolve(self, label: str) -> EntityReference | None:
        return self._by_label.get(self._normalize(label))

    @staticmethod
    def _normalize(label: str) -> str:
        return " ".join(label.strip().casefold().split())


class TextRelationExtractor:
    """Deterministic baseline for auditable text relation extraction."""

    def __init__(
        self, registry: EntityRegistry, rules: Iterable[ExtractionRule]
    ) -> None:
        self.registry = registry
        self.rules = [
            (rule, re.compile(rule.pattern, flags=re.IGNORECASE | re.MULTILINE))
            for rule in rules
        ]

    @classmethod
    def from_files(
        cls, registry_path: str | Path, rules_path: str | Path
    ) -> "TextRelationExtractor":
        registry = EntityRegistry.from_file(registry_path)
        payload = json.loads(Path(rules_path).read_text(encoding="utf-8"))
        rules = [ExtractionRule.model_validate(item) for item in payload["rules"]]
        return cls(registry, rules)

    def extract(self, artifact: RawArtifact) -> ExtractorOutput:
        if not isinstance(artifact.content, str):
            return ExtractorOutput(
                issues=[
                    IngestionIssue(
                        source_id=artifact.source_id,
                        code="invalid_text_content",
                        message="text artifact content must be a string",
                        locator=artifact.locator,
                    )
                ]
            )
        output = ExtractorOutput()
        for rule, pattern in self.rules:
            for match in pattern.finditer(artifact.content):
                try:
                    subject_label = match.group("subject")
                    object_label = match.group("object")
                except (IndexError, KeyError):
                    output.issues.append(
                        IngestionIssue(
                            source_id=artifact.source_id,
                            code="invalid_extraction_rule",
                            message=(
                                f"rule {rule.rule_id} must expose named subject and "
                                "object groups"
                            ),
                        )
                    )
                    continue
                subject = self.registry.resolve(subject_label)
                obj = self.registry.resolve(object_label)
                locator = f"char:{match.start()}-{match.end()}"
                if subject is None or obj is None:
                    missing = subject_label if subject is None else object_label
                    output.issues.append(
                        IngestionIssue(
                            source_id=artifact.source_id,
                            code="unresolved_entity",
                            message=f"entity label is not registered: {missing}",
                            locator=locator,
                        )
                    )
                    continue
                output.candidates.append(
                    Observation(
                        subject=subject.iri,
                        predicate=rule.predicate,
                        object=obj.iri,
                        subject_type=subject.type_iri,
                        object_type=obj.type_iri,
                        confidence=rule.confidence,
                        evidence=[
                            Evidence(
                                source_id=artifact.source_id,
                                modality=artifact.modality,
                                locator=locator,
                                excerpt=match.group(0),
                                extractor_id="rule-text-v1",
                                metadata={"rule_id": rule.rule_id},
                            )
                        ],
                    )
                )
        return output


class CanonicalRelationExtractor:
    """Adapter for records emitted by ETL, OCR, detector or VLM components."""

    def extract(self, artifact: RawArtifact) -> ExtractorOutput:
        records = self._records(artifact.content)
        if records is None:
            return ExtractorOutput(
                issues=[
                    IngestionIssue(
                        source_id=artifact.source_id,
                        code="invalid_relation_payload",
                        message=(
                            "content must be a relation record, a record list, or an "
                            "object containing 'relations'/'records'"
                        ),
                        locator=artifact.locator,
                    )
                ]
            )
        output = ExtractorOutput()
        for index, raw_record in enumerate(records):
            if not isinstance(raw_record, Mapping):
                output.issues.append(
                    IngestionIssue(
                        source_id=artifact.source_id,
                        code="invalid_relation_record",
                        message=f"relation record {index} is not an object",
                    )
                )
                continue
            record = dict(raw_record)
            locator = self._locator(record, artifact.locator, index)
            excerpt = record.pop("excerpt", None)
            record.pop("locator", None)
            record.pop("bbox", None)
            record["evidence"] = [
                Evidence(
                    source_id=artifact.source_id,
                    modality=artifact.modality,
                    locator=locator,
                    excerpt=excerpt,
                    extractor_id=self._optional_text(
                        artifact.metadata.get("extractor_id")
                    ),
                    model_id=self._optional_text(artifact.metadata.get("model_id")),
                    metadata=self._evidence_metadata(artifact.metadata),
                )
            ]
            try:
                output.candidates.append(Observation.model_validate(record))
            except ValidationError as exc:
                output.issues.append(
                    IngestionIssue(
                        source_id=artifact.source_id,
                        code="relation_validation_failed",
                        message=f"relation record {index}: {exc.errors()[0]['msg']}",
                        locator=locator,
                    )
                )
        return output

    @staticmethod
    def _records(content: Any) -> list[Any] | None:
        if isinstance(content, list):
            return content
        if not isinstance(content, Mapping):
            return None
        if "relations" in content:
            value = content["relations"]
            return value if isinstance(value, list) else None
        if "records" in content:
            value = content["records"]
            return value if isinstance(value, list) else None
        return [content]

    @staticmethod
    def _locator(record: Mapping[str, Any], fallback: str | None, index: int) -> str:
        if record.get("locator"):
            return str(record["locator"])
        bbox = record.get("bbox")
        if isinstance(bbox, list):
            return "bbox:" + ",".join(str(value) for value in bbox)
        return fallback or f"record:{index}"

    @staticmethod
    def _optional_text(value: Any) -> str | None:
        return str(value) if value not in (None, "") else None

    @staticmethod
    def _evidence_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
        value = metadata.get("evidence_metadata", {})
        return dict(value) if isinstance(value, Mapping) else {}


class EvidenceFusion:
    """Merge identical statements without treating correlated evidence as independent.

    Confidence starts at the strongest candidate and receives only a small,
    bounded corroboration bonus for additional sources and modalities.
    """

    def fuse(self, candidates: Iterable[Observation]) -> list[Observation]:
        groups: dict[tuple[Any, ...], list[Observation]] = defaultdict(list)
        for candidate in candidates:
            groups[self._key(candidate)].append(candidate)

        fused: list[Observation] = []
        for key in sorted(groups, key=lambda item: tuple(str(value) for value in item)):
            group = groups[key]
            evidence_by_key: dict[
                tuple[str, str, str | None, str | None, str | None], Evidence
            ] = {}
            for candidate in group:
                for evidence in candidate.evidence:
                    evidence_by_key[
                        (
                            evidence.source_id,
                            evidence.modality,
                            evidence.locator,
                            evidence.extractor_id,
                            evidence.model_id,
                        )
                    ] = evidence
            evidence = sorted(
                evidence_by_key.values(),
                key=lambda item: (
                    item.source_id,
                    item.modality,
                    item.locator or "",
                    item.extractor_id or "",
                    item.model_id or "",
                ),
            )
            source_count = len({item.source_id for item in evidence})
            modality_count = len({item.modality for item in evidence})
            base_confidence = max(item.confidence for item in group)
            bonus = min(
                0.10,
                0.04 * max(0, source_count - 1)
                + 0.02 * max(0, modality_count - 1),
            )
            prototype = group[0]
            fused.append(
                Observation(
                    subject=prototype.subject,
                    predicate=prototype.predicate,
                    object=prototype.object,
                    subject_type=prototype.subject_type,
                    object_type=prototype.object_type,
                    object_is_literal=prototype.object_is_literal,
                    confidence=min(0.99, round(base_confidence + bonus, 6)),
                    evidence=evidence,
                )
            )
        return fused

    @staticmethod
    def _key(candidate: Observation) -> tuple[Any, ...]:
        return (
            candidate.subject,
            candidate.predicate,
            candidate.object,
            candidate.subject_type,
            candidate.object_type,
            candidate.object_is_literal,
        )


class MultimodalIngestionPipeline:
    def __init__(
        self,
        extractors: Mapping[str, ModalityExtractor],
        fusion: EvidenceFusion | None = None,
    ) -> None:
        self.extractors = dict(extractors)
        self.fusion = fusion or EvidenceFusion()

    def ingest(self, artifacts: Iterable[RawArtifact]) -> IngestionResult:
        materialized = list(artifacts)
        candidates: list[Observation] = []
        issues: list[IngestionIssue] = []
        for artifact in materialized:
            extractor = self.extractors.get(artifact.modality)
            if extractor is None:
                issues.append(
                    IngestionIssue(
                        source_id=artifact.source_id,
                        code="unsupported_modality",
                        message=f"no extractor registered for modality: {artifact.modality}",
                        locator=artifact.locator,
                    )
                )
                continue
            output = extractor.extract(artifact)
            candidates.extend(output.candidates)
            issues.extend(output.issues)
        observations = self.fusion.fuse(candidates)
        return IngestionResult(
            observations=observations,
            issues=issues,
            artifact_count=len(materialized),
            candidate_count=len(candidates),
            fused_count=len(observations),
        )


def build_ingestion_pipeline(
    registry_path: str | Path, rules_path: str | Path
) -> MultimodalIngestionPipeline:
    text = TextRelationExtractor.from_files(registry_path, rules_path)
    canonical = CanonicalRelationExtractor()
    return MultimodalIngestionPipeline(
        {
            "text": text,
            "structured": canonical,
            "image": canonical,
            "video": canonical,
            "audio": canonical,
        }
    )
