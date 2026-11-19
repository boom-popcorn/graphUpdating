from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from mvp.harness import Harness
from mvp.io import read_json, write_json, write_text, with_text_input


ROOT = Path(__file__).resolve().parent


MODES = ("standard", "ontology_conflict", "plan_rejected", "execution_rollback")


def export_ontology(source_root: Path, destination_root: Path) -> None:
    """Export every published version and remove only stale exporter-owned files."""
    index_path = source_root / "versions.json"
    index = read_json(index_path) if index_path.exists() else {
        "latestVersion": "00", "versions": [{"versionId": "00", "path": "00/ontology.ttl"}],
    }
    versions = {item["versionId"] for item in index["versions"]}
    if index["latestVersion"] not in versions:
        raise ValueError("latest ontology version is missing from the version index")
    for version in versions:
        if not isinstance(version, str) or not version.isascii() or not version.isdigit():
            raise ValueError(f"invalid ontology version: {version!r}")
        source = source_root / version
        if not (source / "ontology.json").is_file() or not (source / "ontology.ttl").is_file():
            raise ValueError(f"published ontology version {version} is incomplete")
    for version in sorted(versions, key=int):
        shutil.copytree(source_root / version, destination_root / version, dirs_exist_ok=True)
    for destination in destination_root.iterdir():
        if (destination.is_dir() and destination.name.isascii() and destination.name.isdigit()
                and destination.name not in versions):
            for filename in ("ontology.json", "ontology.ttl", "validation_report.json"):
                (destination / filename).unlink(missing_ok=True)
            if not any(destination.iterdir()):
                destination.rmdir()
    write_json(destination_root / "versions.json", index)


def run(input_path: Path, output_dir: Path, mode: str = "standard", texts_path: Path | None = None,
        ontology_dir: Path | None = None, dataset_dir: Path | None = None) -> dict[str, Any]:
    if mode not in MODES:
        raise ValueError(f"unsupported demo mode: {mode}")
    raw = read_json(input_path)
    persistent_ontology = dataset_dir / "ontology" if dataset_dir is not None else None
    if ontology_dir is None and persistent_ontology is not None and (persistent_ontology / "00").is_dir():
        ontology_dir = persistent_ontology
    if texts_path is not None:
        raw = with_text_input(raw, texts_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="maritime_kg_mvp_") as staging:
        staging_root = Path(staging)
        if ontology_dir is None:
            shutil.copytree(ROOT / "ontology" / "00", staging_root / "ontology" / "00")
        else:
            export_ontology(ontology_dir, staging_root / "ontology")
        graph_document = read_json(ROOT / "data" / "initial_graph.json")
        harness = Harness(staging_root, graph_document, dataset_dir=dataset_dir)
        report = harness.run_round(raw, demo_mode=mode)
        report["storage"] = {"backend": "jena-tdb2" if dataset_dir else "memory",
                             "transport": "mcp-stdio" if dataset_dir else "local",
                             "datasetDir": str(dataset_dir.resolve()) if dataset_dir else None,
                             "fusekiUrl": getattr(harness.graph, "fuseki_url", None) or None}
        export_ontology(staging_root / "ontology", output_dir / "ontology")
        if persistent_ontology is not None:
            export_ontology(staging_root / "ontology", persistent_ontology)

    execution = report.get("executionResult") or {}
    comparison = report["comparison"]
    write_json(output_dir / f"round_{raw['roundId']}.json", report)
    harness.graph.save(output_dir / f"graph_after_{raw['roundId']}.json")
    write_text(
        output_dir / "metrics.csv",
        "metric,value,unit\n"
        + "\n".join(
            f"{name},{value},ms" for name, value in report.get("metricsMs", {}).items()
        )
        + f"\nincrementalWrites,{comparison['incrementalWrites']},logical_operations"
        + f"\nfullRebuildWritesEstimate,{comparison['fullRebuildWritesEstimate']},logical_operations\n",
    )
    print(json.dumps({
        "status": report["status"],
        "mode": mode,
        "ontologyVersion": report.get("ontologyVersion"),
        "ruleHits": report.get("feedback", {}).get("ruleHits", []),
        "commitId": execution.get("commitId"),
        "createdNodes": execution.get("createdNodes"),
        "createdRelations": execution.get("createdRelations"),
        "createdTypeAssertions": execution.get("createdTypeAssertions"),
        "attachedEvidence": execution.get("attachedEvidence"),
        "textSummary": (report.get("updatePlan") or {}).get("textSummary", {}),
        "threatSummary": (report.get("updatePlan") or {}).get("threatSummary", {}),
        "report": str(output_dir / f"round_{raw['roundId']}.json"),
    }, ensure_ascii=False, indent=2))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the maritime KG update MVP demo.")
    parser.add_argument("--input", type=Path, default=None)
    parser.add_argument("--mode", choices=MODES, default="standard")
    parser.add_argument("--texts", type=Path, help="Optional text evidence JSON; e.g. data/texts_R001.json")
    parser.add_argument("--ontology-dir", type=Path, help="Start from published ontology versions in this directory")
    parser.add_argument("--dataset-dir", type=Path, help="Use persistent Jena TDB2 and MCP stdio tools")
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--scenario", choices=("disguised", "insufficient"), help="Run an extended threat scenario")
    args = parser.parse_args()
    name = "R-001-run" if args.mode == "standard" else f"R-001-{args.mode}"
    output_dir = args.output_dir or ROOT / "reports" / (name + ("-text" if args.texts else ""))
    if args.scenario and args.input is not None:
        parser.error("--scenario cannot be combined with --input")
    if args.scenario:
        args.input = ROOT / "data" / f"observations_{args.scenario}.json"
        output_dir = args.output_dir or ROOT / "reports" / f"R-001-{args.scenario}-{args.mode}"
    args.input = args.input or ROOT / "data" / "observations_R001.json"
    run(args.input, output_dir, args.mode, args.texts, args.ontology_dir, args.dataset_dir)


if __name__ == "__main__":
    main()

