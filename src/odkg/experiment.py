from __future__ import annotations

import json
import time
from pathlib import Path
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, Field

from .contracts import Observation
from .demo import build_demo_orchestrator
from .ingestion import MultimodalIngestionPipeline, RawArtifact
from .policy import EvolutionPolicy


class BenchmarkCase(BaseModel):
    case_id: str
    observations: list[Observation]
    expected_operations: list[str] = Field(default_factory=list)
    expected_status: str
    human_approved: bool = False


class CaseResult(BaseModel):
    case_id: str
    expected_status: str
    actual_status: str
    expected_operations: list[str]
    actual_operations: list[str]
    requires_human_review: bool
    latency_ms: float


class ExpectedRelation(BaseModel):
    subject: str
    predicate: str
    object: str


class ExtractionBenchmarkCase(BaseModel):
    case_id: str
    artifacts: list[RawArtifact] = Field(default_factory=list)
    adapter_artifacts: dict[str, list[RawArtifact]] = Field(default_factory=dict)
    expected_relations: list[ExpectedRelation]


class ExtractionCaseResult(BaseModel):
    case_id: str
    true_positive: int
    false_positive: int
    false_negative: int
    issue_count: int
    fused_count: int
    downstream_status: str
    latency_ms: float


def load_benchmark(path: str | Path) -> list[BenchmarkCase]:
    cases: list[BenchmarkCase] = []
    for line_number, line in enumerate(
        Path(path).read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            cases.append(BenchmarkCase.model_validate_json(line))
        except Exception as exc:
            raise ValueError(f"invalid benchmark line {line_number}: {exc}") from exc
    return cases


def load_extraction_benchmark(path: str | Path) -> list[ExtractionBenchmarkCase]:
    raw_text = Path(path).read_text(encoding="utf-8")
    if raw_text.lstrip().startswith("["):
        try:
            payload = json.loads(raw_text)
            return [ExtractionBenchmarkCase.model_validate(item) for item in payload]
        except Exception as exc:
            raise ValueError(f"invalid extraction benchmark JSON: {exc}") from exc
    cases: list[ExtractionBenchmarkCase] = []
    for line_number, line in enumerate(
        raw_text.splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            cases.append(ExtractionBenchmarkCase.model_validate_json(line))
        except Exception as exc:
            raise ValueError(
                f"invalid extraction benchmark line {line_number}: {exc}"
            ) from exc
    return cases


def run_benchmark(
    path: str | Path, policy: EvolutionPolicy | None = None
) -> dict[str, Any]:
    results: list[CaseResult] = []
    true_positive = false_positive = false_negative = 0
    for case in load_benchmark(path):
        orchestrator = build_demo_orchestrator(policy=policy)
        started = time.perf_counter()
        pipeline = orchestrator.run(
            case.observations, human_approved=case.human_approved
        )
        latency_ms = (time.perf_counter() - started) * 1000
        actual_operations = sorted(
            f"{operation.operation.value}|{operation.target}"
            for operation in pipeline.transaction.change_set.operations
        )
        expected_operations = sorted(case.expected_operations)
        actual_set = set(actual_operations)
        expected_set = set(expected_operations)
        true_positive += len(actual_set & expected_set)
        false_positive += len(actual_set - expected_set)
        false_negative += len(expected_set - actual_set)
        results.append(
            CaseResult(
                case_id=case.case_id,
                expected_status=case.expected_status,
                actual_status=pipeline.transaction.status.value,
                expected_operations=expected_operations,
                actual_operations=actual_operations,
                requires_human_review=pipeline.transaction.requires_human_review,
                latency_ms=round(latency_ms, 3),
            )
        )

    precision = _ratio(true_positive, true_positive + false_positive)
    recall = _ratio(true_positive, true_positive + false_negative)
    f1 = _ratio(2 * precision * recall, precision + recall)
    status_correct = sum(
        item.actual_status == item.expected_status for item in results
    )
    return {
        "cases": len(results),
        "status_accuracy": _ratio(status_correct, len(results)),
        "operation_precision": precision,
        "operation_recall": recall,
        "operation_f1": f1,
        "average_latency_ms": round(
            sum(item.latency_ms for item in results) / max(len(results), 1), 3
        ),
        "human_review_cases": sum(item.requires_human_review for item in results),
        "results": [item.model_dump(mode="json") for item in results],
    }


def compare_semantic_adapters(
    cases: list[ExtractionBenchmarkCase],
    pipelines: Mapping[str, MultimodalIngestionPipeline],
) -> dict[str, Any]:
    """Compare extraction front ends while reusing the same downstream cycle."""

    report: dict[str, Any] = {"cases": len(cases), "adapters": {}}
    for adapter_id, pipeline in pipelines.items():
        true_positive = false_positive = false_negative = 0
        case_results: list[ExtractionCaseResult] = []
        for case in cases:
            started = time.perf_counter()
            artifacts = case.adapter_artifacts.get(adapter_id, case.artifacts)
            ingestion = pipeline.ingest(artifacts)
            actual = {
                (item.subject, item.predicate, item.object)
                for item in ingestion.observations
            }
            expected = {
                (item.subject, item.predicate, item.object)
                for item in case.expected_relations
            }
            tp = len(actual & expected)
            fp = len(actual - expected)
            fn = len(expected - actual)
            true_positive += tp
            false_positive += fp
            false_negative += fn
            if ingestion.observations:
                downstream_status = build_demo_orchestrator().run(
                    ingestion.observations, dry_run=True
                ).transaction.status.value
            else:
                downstream_status = "no_observations"
            case_results.append(
                ExtractionCaseResult(
                    case_id=case.case_id,
                    true_positive=tp,
                    false_positive=fp,
                    false_negative=fn,
                    issue_count=len(ingestion.issues),
                    fused_count=ingestion.fused_count,
                    downstream_status=downstream_status,
                    latency_ms=round((time.perf_counter() - started) * 1000, 3),
                )
            )
        precision = _ratio(true_positive, true_positive + false_positive)
        recall = _ratio(true_positive, true_positive + false_negative)
        report["adapters"][adapter_id] = {
            "precision": precision,
            "recall": recall,
            "f1": _ratio(2 * precision * recall, precision + recall),
            "average_latency_ms": round(
                sum(item.latency_ms for item in case_results)
                / max(len(case_results), 1),
                3,
            ),
            "issues": sum(item.issue_count for item in case_results),
            "results": [item.model_dump(mode="json") for item in case_results],
        }
    return report


def _ratio(numerator: float, denominator: float) -> float:
    return round(numerator / denominator, 6) if denominator else 1.0
