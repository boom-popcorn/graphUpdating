from __future__ import annotations

import argparse
import json
from pathlib import Path

from odkg.experiment import run_benchmark


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the deterministic ODKG benchmark")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("experiments/generic_update_cases.jsonl"),
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run_benchmark(args.dataset)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
