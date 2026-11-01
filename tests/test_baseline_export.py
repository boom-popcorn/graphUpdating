from odkg.baseline_export import merge_continuemkgc_predictions


def test_continuemkgc_predictions_are_paired_with_native_records() -> None:
    records = [
        {
            "token": ["Project", "Alpha", "Foundation", "Beta"],
            "h": {"name": "Project Alpha", "pos": [0, 2]},
            "t": {"name": "Foundation Beta", "pos": [2, 4]},
            "relation": "no_relation",
        }
    ]

    merged = merge_continuemkgc_predictions(
        records,
        [1],
        {"no_relation": 0, "fundedBy": 1},
        [0.88],
    )

    assert merged[0]["predicted_relation"] == "fundedBy"
    assert merged[0]["confidence"] == 0.88
    assert merged[0]["h"]["name"] == "Project Alpha"
