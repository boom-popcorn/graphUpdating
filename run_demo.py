from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from mvp.harness import Harness
from mvp.io import read_json, write_json, write_text


ROOT = Path(__file__).resolve().parent


MODES = ("standard", "ontology_conflict", "plan_rejected", "execution_rollback")


def run(input_path: Path, output_dir: Path, mode: str = "standard") -> dict[str, Any]:
    if mode not in MODES:
        raise ValueError(f"unsupported demo mode: {mode}")
    raw = read_json(input_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="maritime_kg_mvp_") as staging:
        staging_root = Path(staging)
        (staging_root / "ontology").mkdir()
        shutil.copytree(ROOT / "ontology" / "00", staging_root / "ontology" / "00")
        graph_document = read_json(ROOT / "data" / "initial_graph.json")
        harness = Harness(staging_root, graph_document)
        report = harness.run_round(raw, demo_mode=mode)
        # Only export published versions. A rejected proposal still exports version 00.
        for version in ("00", "01"):
            source = staging_root / "ontology" / version
            destination = output_dir / "ontology" / version
            if source.is_dir():
                shutil.copytree(source, destination, dirs_exist_ok=True)
            elif destination.is_dir():
                # Remove only files owned by this exporter after a prior successful run.
                for filename in ("ontology.json", "ontology.ttl", "validation_report.json"):
                    (destination / filename).unlink(missing_ok=True)
                if not any(destination.iterdir()):
                    destination.rmdir()
        index_path = staging_root / "ontology" / "versions.json"
        index = read_json(index_path) if index_path.exists() else {
            "latestVersion": "00", "versions": [{"versionId": "00", "path": "00/ontology.ttl"}],
        }
        write_json(output_dir / "ontology" / "versions.json", index)

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
        "report": str(output_dir / f"round_{raw['roundId']}.json"),
    }, ensure_ascii=False, indent=2))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the maritime KG update MVP demo.")
    parser.add_argument("--input", type=Path, default=ROOT / "data" / "observations_R001.json")
    parser.add_argument("--mode", choices=MODES, default="standard")
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()
    output_dir = args.output_dir or ROOT / "reports" / ("R-001-run" if args.mode == "standard" else f"R-001-{args.mode}")
    run(args.input, output_dir, args.mode)


if __name__ == "__main__":
    main()

