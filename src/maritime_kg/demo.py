"""Command-line demonstration of the first executable update loop."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .harness import UpdateHarness
from .ontology import MaritimeGraphRepository
from .pipeline import dump_change_set, load_observations
from .sample_data import write_sample_data


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def build_parser() -> argparse.ArgumentParser:
    root = _project_root()
    parser = argparse.ArgumentParser(description="Run the maritime KG update MVP")
    parser.add_argument("--input", type=Path, default=root / "data" / "sample_observations.jsonl")
    parser.add_argument("--ontology", type=Path, default=root / "ontology" / "maritime-v0.1.ttl")
    parser.add_argument("--shapes", type=Path, default=root / "ontology" / "maritime-v0.1-shapes.ttl")
    parser.add_argument("--output", type=Path, default=root / "output" / "demo")
    return parser


def run(args: argparse.Namespace) -> dict[str, object]:
    started_at = time.perf_counter()
    if not args.input.exists():
        write_sample_data(args.input)

    observations = load_observations(args.input)
    repository = MaritimeGraphRepository(args.ontology, args.shapes)
    result = UpdateHarness(repository).run(observations)

    args.output.mkdir(parents=True, exist_ok=True)
    repository.serialize(args.output / "demo_graph.ttl")
    if result.change_set is not None:
        dump_change_set(result.change_set, args.output / "demo_change_set.json")
    (args.output / "workflow_report.json").write_text(
        result.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    elapsed_ms = round((time.perf_counter() - started_at) * 1000, 2)
    summary = {
        "observation_count": len(observations),
        "modalities": sorted({event.modality.value for event in observations}),
        "candidate_fact_count": result.candidate_fact_count,
        "workflow_status": result.status.value,
        "change_set_id": result.change_set.change_set_id if result.change_set else None,
        "planning_attempts": result.planning_attempts,
        "evolution_proposal_count": len(result.evolution_proposals),
        "graph_version": result.graph_version,
        "triple_count": len(repository.data_graph),
        "elapsed_ms": elapsed_ms,
    }
    (args.output / "demo_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> None:
    summary = run(build_parser().parse_args())
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

