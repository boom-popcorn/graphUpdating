from __future__ import annotations

import argparse
import json
from pathlib import Path

from odkg.baseline_export import (
    merge_continuemkgc_predictions,
    read_confidences,
    read_native_records,
    read_prediction_values,
    write_jsonl,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export ContinueMKGC MRE predictions as ODKG adapter JSONL"
    )
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--rel2id", type=Path, required=True)
    parser.add_argument("--confidences", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    relation_to_id = json.loads(args.rel2id.read_text(encoding="utf-8"))
    merged = merge_continuemkgc_predictions(
        read_native_records(args.records),
        read_prediction_values(args.predictions),
        relation_to_id,
        read_confidences(args.confidences),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.output, merged)
    print(f"exported {len(merged)} predictions to {args.output.resolve()}")


if __name__ == "__main__":
    main()
