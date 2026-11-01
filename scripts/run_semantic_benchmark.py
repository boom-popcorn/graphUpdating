from __future__ import annotations

import argparse
import json
from pathlib import Path

from odkg.demo import build_runtime_ingestion_pipeline, project_root
from odkg.experiment import compare_semantic_adapters, load_extraction_benchmark


def main() -> None:
    root = project_root()
    parser = argparse.ArgumentParser(
        description="Compare rule, online LLM/VLM and replayed research baselines."
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=root / "experiments" / "semantic_smoke_cases.json",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--online",
        action="store_true",
        help="Include configured LLM/VLM endpoints and incur API usage.",
    )
    parser.add_argument(
        "--require-online",
        action="store_true",
        help="Fail unless both LLM and VLM adapters are configured.",
    )
    args = parser.parse_args()

    runtime = build_runtime_ingestion_pipeline()
    pipelines = {
        "rule": runtime,
        "adakgc": runtime,
        "continuemkgc": runtime,
    }
    online_available = {
        "deepseek-llm": "llm_text" in runtime.extractors,
        "deepseek-vlm": "vlm_image" in runtime.extractors,
    }
    if args.require_online and not all(online_available.values()):
        raise SystemExit(
            "Both online adapters are required. Run the DeepSeek session configuration first."
        )
    if args.online or args.require_online:
        for adapter_id, available in online_available.items():
            if available:
                pipelines[adapter_id] = runtime

    cases = load_extraction_benchmark(args.dataset)
    report = compare_semantic_adapters(cases, pipelines)
    report["dataset"] = str(args.dataset.resolve())
    report["dataset_kind"] = "adapter-contract smoke set"
    report["online_requested"] = bool(args.online or args.require_online)
    report["warning"] = (
        "AdaKGC and ContinueMKGC records in this smoke set are fixtures. Replace them "
        "with native checkpoint predictions before reporting model-quality results."
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()

