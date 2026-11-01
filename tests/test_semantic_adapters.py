from __future__ import annotations

import json

from odkg.demo import project_root
from odkg.ingestion import EntityRegistry, MultimodalIngestionPipeline, RawArtifact
from odkg.semantic_adapters import (
    AdaKGCRecordExtractor,
    ContinueMKGCPredictionExtractor,
    LabelMapping,
    OpenAICompatibleClient,
    OntologyPromptContext,
    StructuredModelExtractor,
    model_extractors_from_env,
)


EX = "https://example.org/odkg/"


class FakeModelClient:
    model_id = "fixture-model"

    def infer(self, artifact, context):
        assert context.ontology_version == "v0.1.0"
        return {
            "relations": [
                {
                    "subject": f"{EX}project_alpha",
                    "predicate": f"{EX}fundedBy",
                    "object": f"{EX}foundation_beta",
                    "subject_type": f"{EX}ResearchProject",
                    "object_type": f"{EX}Organization",
                    "confidence": 0.91,
                    "locator": "char:0-43",
                    "excerpt": "Project Alpha is funded by Foundation Beta.",
                }
            ]
        }


class FailingModelClient:
    model_id = "unavailable-model"

    def infer(self, artifact, context):
        raise RuntimeError("fixture endpoint unavailable")


class RecordingModelClient(FakeModelClient):
    def __init__(self) -> None:
        self.versions = []

    def infer(self, artifact, context):
        self.versions.append(context.ontology_version)
        return {
            "relations": [
                {
                    "subject": f"{EX}project_alpha",
                    "predicate": f"{EX}fundedBy",
                    "object": f"{EX}foundation_beta",
                    "subject_type": f"{EX}ResearchProject",
                    "object_type": f"{EX}Organization",
                    "confidence": 0.91,
                }
            ]
        }


def registry() -> EntityRegistry:
    return EntityRegistry.from_file(project_root() / "config" / "entity_registry.json")


def mapping() -> LabelMapping:
    return LabelMapping(predicates={"fundedBy": f"{EX}fundedBy"})


def test_structured_llm_output_uses_canonical_observation_boundary() -> None:
    extractor = StructuredModelExtractor(
        FakeModelClient(),
        OntologyPromptContext(ontology_version="v0.1.0"),
        extractor_id="openai-compatible-llm-v1",
    )
    result = extractor.extract(
        RawArtifact(
            source_id="doc-1",
            modality="llm_text",
            content="Project Alpha is funded by Foundation Beta.",
        )
    )

    assert result.issues == []
    assert result.candidates[0].predicate == f"{EX}fundedBy"
    evidence = result.candidates[0].evidence[0]
    assert evidence.extractor_id == "openai-compatible-llm-v1"
    assert evidence.model_id == "fixture-model"
    assert evidence.metadata["ontology_version"] == "v0.1.0"


def test_model_failure_is_auditable_and_does_not_fall_back_silently() -> None:
    extractor = StructuredModelExtractor(
        FailingModelClient(),
        OntologyPromptContext(ontology_version="v0.1.0"),
        extractor_id="openai-compatible-llm-v1",
    )
    result = extractor.extract(
        RawArtifact(source_id="doc-1", modality="llm_text", content="text")
    )

    assert result.candidates == []
    assert result.issues[0].code == "model_inference_failed"


def test_model_prompt_context_is_refreshed_after_ontology_evolution() -> None:
    active = {"version": "v0.1.0"}
    client = RecordingModelClient()
    extractor = StructuredModelExtractor(
        client,
        lambda: OntologyPromptContext(ontology_version=active["version"]),
        extractor_id="openai-compatible-llm-v1",
    )
    artifact = RawArtifact(source_id="doc-1", modality="llm_text", content="text")

    extractor.extract(artifact)
    active["version"] = "v0.2.0"
    second = extractor.extract(artifact)

    assert client.versions == ["v0.1.0", "v0.2.0"]
    assert second.candidates[0].evidence[0].metadata["ontology_version"] == "v0.2.0"


def test_adakgc_native_prediction_record_is_normalized() -> None:
    extractor = AdaKGCRecordExtractor(registry(), mapping())
    artifact = RawArtifact(
        source_id="adakgc-1",
        modality="adakgc",
        content={
            "text": "Project Alpha is funded by Foundation Beta.",
            "relation": [
                {
                    "type": "fundedBy",
                    "args": [
                        {"type": "project", "text": "Project Alpha"},
                        {"type": "organization", "text": "Foundation Beta"},
                    ],
                    "confidence": 0.83,
                }
            ],
        },
    )

    result = extractor.extract(artifact)

    assert result.issues == []
    assert result.candidates[0].confidence == 0.83
    assert result.candidates[0].evidence[0].extractor_id == "adakgc-record-v1"


def test_continuemkgc_export_is_normalized() -> None:
    extractor = ContinueMKGCPredictionExtractor(registry(), mapping())
    artifact = RawArtifact(
        source_id="mspt-1",
        modality="continuemkgc",
        content={
            "token": ["Project", "Alpha", "funded", "by", "Foundation", "Beta"],
            "h": {"name": "Project Alpha", "pos": [0, 2]},
            "t": {"name": "Foundation Beta", "pos": [4, 6]},
            "predicted_relation": "fundedBy",
            "confidence": 0.79,
        },
    )

    result = extractor.extract(artifact)

    assert result.issues == []
    assert result.candidates[0].object == f"{EX}foundation_beta"
    assert result.candidates[0].evidence[0].extractor_id == "continuemkgc-mre-v1"


def test_model_and_baseline_candidates_share_the_same_fusion_path() -> None:
    context = OntologyPromptContext(ontology_version="v0.1.0")
    pipeline = MultimodalIngestionPipeline(
        {
            "llm_text": StructuredModelExtractor(
                FakeModelClient(), context, extractor_id="openai-compatible-llm-v1"
            ),
            "adakgc": AdaKGCRecordExtractor(registry(), mapping()),
        }
    )
    result = pipeline.ingest(
        [
            RawArtifact(source_id="llm-1", modality="llm_text", content="text"),
            RawArtifact(
                source_id="adakgc-1",
                modality="adakgc",
                content={
                    "text": "text",
                    "relation": [
                        {
                            "type": "fundedBy",
                            "args": [
                                {"text": "Project Alpha"},
                                {"text": "Foundation Beta"},
                            ],
                        }
                    ],
                },
            ),
        ]
    )

    assert result.candidate_count == 2
    assert result.fused_count == 1
    assert len(result.observations[0].evidence) == 2


def test_openai_compatible_client_sends_deepseek_json_controls(monkeypatch) -> None:
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(
                {"choices": [{"message": {"content": '{"relations": []}'}}]}
            ).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["authorization"] = request.headers.get("Authorization")
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OpenAICompatibleClient(
        base_url="https://api.deepseek.com",
        model_id="deepseek-flash",
        api_key="fixture-key",
        max_tokens=1024,
        thinking="disabled",
    )

    result = client.infer(
        RawArtifact(source_id="doc-1", modality="llm_text", content="No relation."),
        OntologyPromptContext(ontology_version="v0.1.0"),
    )

    assert result == {"relations": []}
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert captured["body"]["thinking"] == {"type": "disabled"}
    assert captured["body"]["max_tokens"] == 1024
    assert captured["authorization"] == "Bearer fixture-key"


def test_deepseek_key_fallback_is_used_only_for_deepseek(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture-key")
    monkeypatch.setenv("ODKG_LLM_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("ODKG_LLM_MODEL", "deepseek-flash")
    monkeypatch.setenv("ODKG_LLM_THINKING", "disabled")

    extractors = model_extractors_from_env(
        OntologyPromptContext(ontology_version="v0.1.0")
    )

    client = extractors["llm_text"].client
    assert client.api_key == "fixture-key"
    assert client.thinking == "disabled"
