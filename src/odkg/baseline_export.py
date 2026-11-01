from __future__ import annotations

import ast
import json
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any


def merge_continuemkgc_predictions(
    records: Sequence[Mapping[str, Any]],
    predictions: Sequence[str | int],
    relation_to_id: Mapping[str, int],
    confidences: Sequence[float] | None = None,
) -> list[dict[str, Any]]:
    """Pair ContinueMKGC label outputs with their native MRE input records."""

    if len(records) != len(predictions):
        raise ValueError(
            f"record/prediction length mismatch: {len(records)} != {len(predictions)}"
        )
    if confidences is not None and len(confidences) != len(predictions):
        raise ValueError("confidence/prediction length mismatch")
    id_to_relation = {int(value): key for key, value in relation_to_id.items()}
    merged: list[dict[str, Any]] = []
    for index, (record, prediction) in enumerate(zip(records, predictions, strict=True)):
        label = id_to_relation.get(prediction, "") if isinstance(prediction, int) else prediction
        if not label:
            raise ValueError(f"unknown relation id at prediction {index}: {prediction}")
        item = dict(record)
        item["predicted_relation"] = str(label)
        if confidences is not None:
            item["confidence"] = float(confidences[index])
        merged.append(item)
    return merged


def read_native_records(path: str | Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        Path(path).read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            value = ast.literal_eval(line)
        except (SyntaxError, ValueError) as exc:
            raise ValueError(f"invalid native record line {line_number}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"native record line {line_number} is not an object")
        records.append(value)
    return records


def read_prediction_values(path: str | Path) -> list[str | int]:
    values: list[str | int] = []
    for line_number, line in enumerate(
        Path(path).read_text(encoding="utf-8").splitlines(), start=1
    ):
        text = line.strip()
        if not text:
            continue
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            value = text
        if not isinstance(value, (str, int)):
            raise ValueError(f"prediction line {line_number} must be a label or id")
        values.append(value)
    return values


def read_confidences(path: str | Path | None) -> list[float] | None:
    if path is None:
        return None
    return [float(item) for item in Path(path).read_text(encoding="utf-8").split()]


def write_jsonl(path: str | Path, records: Iterable[Mapping[str, Any]]) -> None:
    text = "\n".join(json.dumps(dict(item), ensure_ascii=False) for item in records)
    Path(path).write_text(text + ("\n" if text else ""), encoding="utf-8")
