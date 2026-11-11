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


def run(input_path: Path, output_dir: Path) -> dict[str, Any]:
    raw = read_json(input_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="maritime_kg_mvp_") as staging:
        staging_root = Path(staging)
        (staging_root / "ontology").mkdir()
        shutil.copytree(ROOT / "ontology" / "00", staging_root / "ontology" / "00")
        graph_document = read_json(ROOT / "data" / "initial_graph.json")
        harness = Harness(staging_root, graph_document)
        report = harness.run_round(raw)
        graph = harness.graph.document
        shutil.copytree(ROOT / "ontology" / "00", output_dir / "ontology" / "00", dirs_exist_ok=True)
        shutil.copytree(staging_root / "ontology" / "01", output_dir / "ontology" / "01", dirs_exist_ok=True)
        shutil.copy2(staging_root / "ontology" / "versions.json", output_dir / "ontology" / "versions.json")

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
        "ontologyVersion": report.get("ontologyVersion"),
        "ruleHits": report.get("feedback", {}).get("ruleHits", []),
        "commitId": execution.get("commitId"),
        "createdNodes": execution.get("createdNodes"),
        "createdRelations": execution.get("createdRelations"),
        "attachedEvidence": execution.get("attachedEvidence"),
        "report": str(output_dir / f"round_{raw['roundId']}.json"),
    }, ensure_ascii=False, indent=2))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the maritime KG update MVP demo.")
    parser.add_argument("--input", type=Path, default=ROOT / "data" / "observations_R001.json")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "reports" / "R-001-run")
    args = parser.parse_args()
    run(args.input, args.output_dir)


if __name__ == "__main__":
    main()

