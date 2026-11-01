from odkg.demo import project_root
from odkg.experiment import (
    ExpectedRelation,
    ExtractionBenchmarkCase,
    compare_semantic_adapters,
    load_extraction_benchmark,
    run_benchmark,
)
from odkg.ingestion import (
    EntityRegistry,
    MultimodalIngestionPipeline,
    RawArtifact,
    TextRelationExtractor,
)
from odkg.semantic_adapters import AdaKGCRecordExtractor, LabelMapping


def test_generic_benchmark_matches_expected_outcomes() -> None:
    report = run_benchmark(
        project_root() / "experiments" / "generic_update_cases.jsonl"
    )
    assert report["cases"] == 5
    assert report["status_accuracy"] == 1.0
    assert report["operation_precision"] == 1.0
    assert report["operation_recall"] == 1.0
    assert report["operation_f1"] == 1.0
    assert report["human_review_cases"] == 1


def test_adapter_benchmark_keeps_the_downstream_cycle_identical() -> None:
    root = project_root()
    registry = EntityRegistry.from_file(root / "config" / "entity_registry.json")
    mapping = LabelMapping(
        predicates={"fundedBy": "https://example.org/odkg/fundedBy"}
    )
    rule = TextRelationExtractor.from_files(
        root / "config" / "entity_registry.json",
        root / "config" / "extraction_rules.json",
    )
    pipelines = {
        "rule": MultimodalIngestionPipeline({"input": rule}),
        "adakgc": MultimodalIngestionPipeline(
            {"input": AdaKGCRecordExtractor(registry, mapping)}
        ),
    }
    case = ExtractionBenchmarkCase(
        case_id="same-evidence-1",
        adapter_artifacts={
            "rule": [
                RawArtifact(
                    source_id="doc-1",
                    modality="input",
                    content="Project Alpha is funded by Foundation Beta.",
                )
            ],
            "adakgc": [
                RawArtifact(
                    source_id="doc-1",
                    modality="input",
                    content={
                        "text": "Project Alpha is funded by Foundation Beta.",
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
                )
            ],
        },
        expected_relations=[
            ExpectedRelation(
                subject="https://example.org/odkg/project_alpha",
                predicate="https://example.org/odkg/fundedBy",
                object="https://example.org/odkg/foundation_beta",
            )
        ],
    )

    result = compare_semantic_adapters([case], pipelines)

    assert result["adapters"]["rule"]["f1"] == 1.0
    assert result["adapters"]["adakgc"]["f1"] == 1.0
    assert result["adapters"]["rule"]["results"][0]["downstream_status"] == "prepared"
    assert result["adapters"]["adakgc"]["results"][0]["downstream_status"] == "prepared"


def test_semantic_smoke_dataset_is_valid_and_balanced() -> None:
    cases = load_extraction_benchmark(
        project_root() / "experiments" / "semantic_smoke_cases.json"
    )

    assert len(cases) == 3
    assert sum(bool(case.expected_relations) for case in cases) == 2
    assert all(
        {"rule", "deepseek-llm", "deepseek-vlm", "adakgc", "continuemkgc"}
        <= set(case.adapter_artifacts)
        for case in cases
    )
