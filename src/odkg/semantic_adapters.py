from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Callable, Protocol

from pydantic import BaseModel, Field

from .contracts import Evidence, Observation
from .ingestion import (
    CanonicalRelationExtractor,
    EntityRegistry,
    ExtractorOutput,
    IngestionIssue,
    ModalityExtractor,
    RawArtifact,
)


class OntologyPromptContext(BaseModel):
    """Small, serializable ontology view supplied to a semantic model."""

    ontology_version: str
    class_iris: list[str] = Field(default_factory=list)
    predicate_iris: list[str] = Field(default_factory=list)
    entity_hints: dict[str, dict[str, str]] = Field(default_factory=dict)
    proposed_namespace: str | None = None
    allow_schema_gap_proposals: bool = True


class StructuredInferenceClient(Protocol):
    model_id: str

    def infer(
        self, artifact: RawArtifact, context: OntologyPromptContext
    ) -> Mapping[str, Any]: ...


class OpenAICompatibleClient:
    """Minimal OpenAI-compatible JSON client for LLM and VLM extraction.

    It intentionally depends only on the standard library. Provider-specific
    authentication, retries and model hosting remain deployment concerns.
    """

    def __init__(
        self,
        *,
        base_url: str,
        model_id: str,
        api_key: str | None = None,
        timeout_seconds: float = 90.0,
        max_tokens: int = 2048,
        thinking: str | None = None,
    ) -> None:
        if not base_url.strip():
            raise ValueError("base_url is required")
        if not model_id.strip():
            raise ValueError("model_id is required")
        endpoint = base_url.rstrip("/")
        if not endpoint.endswith("/chat/completions"):
            endpoint += "/chat/completions"
        self.endpoint = endpoint
        self.model_id = model_id
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        if max_tokens < 1:
            raise ValueError("max_tokens must be positive")
        if thinking not in (None, "enabled", "disabled"):
            raise ValueError("thinking must be enabled, disabled or omitted")
        self.max_tokens = max_tokens
        self.thinking = thinking

    def infer(
        self, artifact: RawArtifact, context: OntologyPromptContext
    ) -> Mapping[str, Any]:
        payload = {
            "model": self.model_id,
            "temperature": 0,
            "max_tokens": self.max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": self._system_prompt(context)},
                {"role": "user", "content": self._user_content(artifact)},
            ],
        }
        if self.thinking is not None:
            payload["thinking"] = {"type": self.thinking}
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.timeout_seconds
            ) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"model endpoint returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"model endpoint failed: {type(exc).__name__}") from exc

        try:
            content = result["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("model response has no choices[0].message.content") from exc
        if isinstance(content, Mapping):
            return content
        if not isinstance(content, str):
            raise RuntimeError("model response content is not JSON text")
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError("model response is not valid JSON") from exc
        if not isinstance(parsed, Mapping):
            raise RuntimeError("model response JSON must be an object")
        return parsed

    @staticmethod
    def _system_prompt(context: OntologyPromptContext) -> str:
        schema = {
            "relations": [
                {
                    "subject": "full IRI",
                    "predicate": "full IRI",
                    "object": "full IRI or literal",
                    "subject_type": "full class IRI",
                    "object_type": "full class/datatype IRI or null",
                    "object_is_literal": False,
                    "confidence": 0.0,
                    "locator": "source position",
                    "excerpt": "supporting text",
                }
            ]
        }
        return (
            "Extract only relations supported by the supplied evidence. Return one JSON "
            f"object matching this shape: {json.dumps(schema, ensure_ascii=False)}. "
            "Use full IRIs and confidence in [0,1]. Do not invent evidence. Existing "
            f"ontology version: {context.ontology_version}. Known classes: "
            f"{json.dumps(context.class_iris, ensure_ascii=False)}. Known predicates: "
            f"{json.dumps(context.predicate_iris, ensure_ascii=False)}. Entity-linking hints "
            f"(label to IRI and type): {json.dumps(context.entity_hints, ensure_ascii=False)}. "
            f"Namespace for proposed schema terms: {context.proposed_namespace or 'unspecified'}. "
            "When a mentioned label matches an entity-linking hint, copy its IRI and type "
            "exactly. Return {\"relations\": []} when no relation is supported. "
            "If the evidence requires a missing class or predicate, it may be emitted as a "
            "full proposed IRI so the downstream ontology-evolution policy can review it."
        )

    @staticmethod
    def _user_content(artifact: RawArtifact) -> Any:
        image_url = artifact.metadata.get("image_url")
        if image_url:
            text = artifact.content if isinstance(artifact.content, str) else ""
            return [
                {"type": "text", "text": text or "Extract supported relations."},
                {"type": "image_url", "image_url": {"url": str(image_url)}},
            ]
        if isinstance(artifact.content, str):
            return artifact.content
        return json.dumps(artifact.content, ensure_ascii=False)


class StructuredModelExtractor:
    """Normalize one structured LLM/VLM response through the canonical boundary."""

    def __init__(
        self,
        client: StructuredInferenceClient,
        context: OntologyPromptContext | Callable[[], OntologyPromptContext],
        *,
        extractor_id: str,
    ) -> None:
        self.client = client
        self.context_provider = context if callable(context) else lambda: context
        self.extractor_id = extractor_id
        self.canonical = CanonicalRelationExtractor()

    def extract(self, artifact: RawArtifact) -> ExtractorOutput:
        context = self.context_provider()
        try:
            payload = self.client.infer(artifact, context)
        except Exception as exc:
            return ExtractorOutput(
                issues=[
                    IngestionIssue(
                        source_id=artifact.source_id,
                        code="model_inference_failed",
                        message=f"{self.extractor_id}: {exc}",
                        locator=artifact.locator,
                    )
                ]
            )
        bridged = artifact.model_copy(
            update={
                "content": dict(payload),
                "metadata": {
                    **artifact.metadata,
                    "extractor_id": self.extractor_id,
                    "model_id": self.client.model_id,
                    "evidence_metadata": {
                        "ontology_version": context.ontology_version,
                        "adapter_family": "structured_model",
                    },
                },
            }
        )
        return self.canonical.extract(bridged)


class LabelMapping(BaseModel):
    predicates: dict[str, str] = Field(default_factory=dict)
    no_relation_labels: set[str] = Field(
        default_factory=lambda: {"no_relation", "none", "na", "0"}
    )

    def predicate_iri(self, label: str) -> str | None:
        if label.casefold() in {item.casefold() for item in self.no_relation_labels}:
            return None
        if label in self.predicates:
            return self.predicates[label]
        if label.startswith(("http://", "https://", "urn:")):
            return label
        raise KeyError(label)


class AdaKGCRecordExtractor:
    """Read AdaKGC ``*_preds_record.txt`` JSON records.

    AdaKGC inference is deliberately kept in its original Python 3.8/CUDA
    environment. This adapter consumes its documented JSONL prediction output
    and converts relation records into the system's stable observation schema.
    """

    extractor_id = "adakgc-record-v1"

    def __init__(
        self,
        registry: EntityRegistry,
        mapping: LabelMapping,
        default_confidence: float = 0.80,
    ) -> None:
        self.registry = registry
        self.mapping = mapping
        self.default_confidence = default_confidence

    def extract(self, artifact: RawArtifact) -> ExtractorOutput:
        records = artifact.content if isinstance(artifact.content, list) else [artifact.content]
        output = ExtractorOutput()
        for record_index, record in enumerate(records):
            if not isinstance(record, Mapping):
                output.issues.append(self._issue(artifact, "invalid_adakgc_record", record_index))
                continue
            relations = record.get("relation", [])
            if not isinstance(relations, list):
                output.issues.append(self._issue(artifact, "invalid_adakgc_relations", record_index))
                continue
            for relation_index, relation in enumerate(relations):
                self._append_relation(
                    output,
                    artifact,
                    record_index,
                    relation_index,
                    relation,
                    str(record.get("text", "")) or None,
                )
        return output

    def _append_relation(
        self,
        output: ExtractorOutput,
        artifact: RawArtifact,
        record_index: int,
        relation_index: int,
        relation: Any,
        record_text: str | None,
    ) -> None:
        locator = f"record:{record_index}/relation:{relation_index}"
        if not isinstance(relation, Mapping):
            output.issues.append(
                IngestionIssue(
                    source_id=artifact.source_id,
                    code="invalid_adakgc_relation",
                    message="AdaKGC relation is not an object",
                    locator=locator,
                )
            )
            return
        args = relation.get("args", [])
        if not isinstance(args, list) or len(args) < 2:
            output.issues.append(
                IngestionIssue(
                    source_id=artifact.source_id,
                    code="invalid_adakgc_relation",
                    message="AdaKGC relation must contain at least two args",
                    locator=locator,
                )
            )
            return
        left, right = args[0], args[1]
        if not isinstance(left, Mapping) or not isinstance(right, Mapping):
            output.issues.append(self._issue(artifact, "invalid_adakgc_args", record_index))
            return
        subject = self.registry.resolve(str(left.get("text", "")))
        obj = self.registry.resolve(str(right.get("text", "")))
        if subject is None or obj is None:
            missing = left.get("text") if subject is None else right.get("text")
            output.issues.append(
                IngestionIssue(
                    source_id=artifact.source_id,
                    code="unresolved_entity",
                    message=f"AdaKGC entity label is not registered: {missing}",
                    locator=locator,
                )
            )
            return
        try:
            predicate = self.mapping.predicate_iri(str(relation.get("type", "")))
        except KeyError:
            output.issues.append(
                IngestionIssue(
                    source_id=artifact.source_id,
                    code="unmapped_relation_label",
                    message=f"AdaKGC relation label is not mapped: {relation.get('type')}",
                    locator=locator,
                )
            )
            return
        if predicate is None:
            return
        confidence = float(relation.get("confidence", self.default_confidence))
        output.candidates.append(
            Observation(
                subject=subject.iri,
                predicate=predicate,
                object=obj.iri,
                subject_type=subject.type_iri,
                object_type=obj.type_iri,
                confidence=confidence,
                evidence=[
                    Evidence(
                        source_id=artifact.source_id,
                        modality=artifact.modality,
                        locator=locator,
                        excerpt=record_text,
                        extractor_id=self.extractor_id,
                        model_id=str(artifact.metadata.get("model_id", "AdaKGC")),
                        metadata={"native_format": "preds_record_jsonl"},
                    )
                ],
            )
        )

    @staticmethod
    def _issue(artifact: RawArtifact, code: str, index: int) -> IngestionIssue:
        return IngestionIssue(
            source_id=artifact.source_id,
            code=code,
            message=f"invalid AdaKGC record at index {index}",
            locator=f"record:{index}",
        )


class ContinueMKGCPredictionExtractor:
    """Normalize exported ContinueMKGC MRE predictions.

    The upstream code reports label IDs during evaluation but does not expose an
    online inference API. A thin exporter should pair each predicted relation
    with the original native ``h``/``t`` record; this adapter consumes that
    auditable result without importing its legacy Torch environment.
    """

    extractor_id = "continuemkgc-mre-v1"

    def __init__(
        self,
        registry: EntityRegistry,
        mapping: LabelMapping,
        default_confidence: float = 0.75,
    ) -> None:
        self.registry = registry
        self.mapping = mapping
        self.default_confidence = default_confidence

    def extract(self, artifact: RawArtifact) -> ExtractorOutput:
        records = artifact.content if isinstance(artifact.content, list) else [artifact.content]
        output = ExtractorOutput()
        for index, record in enumerate(records):
            locator = f"record:{index}"
            if not isinstance(record, Mapping):
                output.issues.append(
                    IngestionIssue(
                        source_id=artifact.source_id,
                        code="invalid_continuemkgc_record",
                        message="ContinueMKGC prediction is not an object",
                        locator=locator,
                    )
                )
                continue
            head = record.get("h") or record.get("head")
            tail = record.get("t") or record.get("tail")
            head_label = head.get("name") if isinstance(head, Mapping) else head
            tail_label = tail.get("name") if isinstance(tail, Mapping) else tail
            subject = self.registry.resolve(str(head_label or ""))
            obj = self.registry.resolve(str(tail_label or ""))
            if subject is None or obj is None:
                missing = head_label if subject is None else tail_label
                output.issues.append(
                    IngestionIssue(
                        source_id=artifact.source_id,
                        code="unresolved_entity",
                        message=f"ContinueMKGC entity label is not registered: {missing}",
                        locator=locator,
                    )
                )
                continue
            label = str(record.get("predicted_relation", record.get("relation", "")))
            try:
                predicate = self.mapping.predicate_iri(label)
            except KeyError:
                output.issues.append(
                    IngestionIssue(
                        source_id=artifact.source_id,
                        code="unmapped_relation_label",
                        message=f"ContinueMKGC relation label is not mapped: {label}",
                        locator=locator,
                    )
                )
                continue
            if predicate is None:
                continue
            tokens = record.get("token", [])
            excerpt = " ".join(str(item) for item in tokens) if isinstance(tokens, list) else None
            output.candidates.append(
                Observation(
                    subject=subject.iri,
                    predicate=predicate,
                    object=obj.iri,
                    subject_type=subject.type_iri,
                    object_type=obj.type_iri,
                    confidence=float(record.get("confidence", self.default_confidence)),
                    evidence=[
                        Evidence(
                            source_id=artifact.source_id,
                            modality=artifact.modality,
                            locator=locator,
                            excerpt=excerpt,
                            extractor_id=self.extractor_id,
                            model_id=str(
                                artifact.metadata.get("model_id", "ContinueMKGC-MSPT")
                            ),
                            metadata={"native_format": "mre_export_jsonl"},
                        )
                    ],
                )
            )
        return output


def model_extractors_from_env(
    context: OntologyPromptContext | Callable[[], OntologyPromptContext],
) -> dict[str, ModalityExtractor]:
    """Create only explicitly configured remote/local model adapters."""

    result: dict[str, ModalityExtractor] = {}
    for name, modality in (("LLM", "llm_text"), ("VLM", "vlm_image")):
        base_url = os.environ.get(f"ODKG_{name}_BASE_URL", "").strip()
        model = os.environ.get(f"ODKG_{name}_MODEL", "").strip()
        if not base_url or not model:
            continue
        api_key = os.environ.get(f"ODKG_{name}_API_KEY")
        if not api_key and "api.deepseek.com" in base_url.casefold():
            api_key = os.environ.get("DEEPSEEK_API_KEY")
        thinking = os.environ.get(f"ODKG_{name}_THINKING", "").strip() or None
        client = OpenAICompatibleClient(
            base_url=base_url,
            model_id=model,
            api_key=api_key,
            timeout_seconds=float(os.environ.get(f"ODKG_{name}_TIMEOUT", "90")),
            max_tokens=int(os.environ.get(f"ODKG_{name}_MAX_TOKENS", "2048")),
            thinking=thinking,
        )
        result[modality] = StructuredModelExtractor(
            client,
            context,
            extractor_id=f"openai-compatible-{name.casefold()}-v1",
        )
    return result


def adapter_status_from_env() -> list[dict[str, Any]]:
    return [
        {
            "adapter_id": "rule-text-v1",
            "family": "deterministic",
            "normalizer_available": True,
            "inference_backend_configured": True,
        },
        {
            "adapter_id": "openai-compatible-llm-v1",
            "family": "online_model",
            "normalizer_available": True,
            "inference_backend_configured": bool(
                os.environ.get("ODKG_LLM_BASE_URL") and os.environ.get("ODKG_LLM_MODEL")
            ),
        },
        {
            "adapter_id": "openai-compatible-vlm-v1",
            "family": "online_model",
            "normalizer_available": True,
            "inference_backend_configured": bool(
                os.environ.get("ODKG_VLM_BASE_URL") and os.environ.get("ODKG_VLM_MODEL")
            ),
        },
        {
            "adapter_id": AdaKGCRecordExtractor.extractor_id,
            "family": "offline_baseline_output",
            "normalizer_available": True,
            "inference_backend_configured": False,
        },
        {
            "adapter_id": ContinueMKGCPredictionExtractor.extractor_id,
            "family": "offline_baseline_output",
            "normalizer_available": True,
            "inference_backend_configured": False,
        },
    ]

