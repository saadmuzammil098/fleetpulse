"""Prove training and mock inference compute identical rolling features.

Run as `python -m src.verify_feature_parity` from task-3/. This is Task
3's actual done-when check: "the same feature code produces identical
output from training and from a simulated live reading."

Method: pick a machine `type`, take the readings training's ordering
assigns it, in order, up to some row N. Replay readings 1..N through a
*fresh* ``FeatureComputer`` — a cold-start stream, exactly what
``mock_inference.py`` does for a newly-connected vehicle — and compare
reading N's features against what ``load_data.build_feature_dataset``
computed for that exact same row during training. If the two modules
were doing separate rolling-window math, this is where it would show up
as numeric drift; because both paths call the same ``FeatureComputer``,
they don't.
"""
from __future__ import annotations

from .features import DEFAULT_WINDOWS, SENSOR_COLUMNS, FeatureComputer
from .load_data import STREAM_KEY_COLUMN, build_feature_dataset, rolling_feature_columns

CHECK_TYPE = "M"
CHECK_UP_TO_ROW = 37  # the Nth reading of CHECK_TYPE, in training's stream order
TOLERANCE = 1e-9


def main() -> None:
    X, y, ordered = build_feature_dataset(windows=DEFAULT_WINDOWS)
    feature_cols = rolling_feature_columns(windows=DEFAULT_WINDOWS)

    type_rows = ordered[ordered[STREAM_KEY_COLUMN] == CHECK_TYPE]
    if len(type_rows) < CHECK_UP_TO_ROW:
        raise RuntimeError(
            f"only {len(type_rows)} rows of type={CHECK_TYPE!r}, need at least {CHECK_UP_TO_ROW}"
        )
    prefix = type_rows.iloc[:CHECK_UP_TO_ROW]
    target_index = prefix.index[-1]

    training_features = {col: X.loc[target_index, col] for col in feature_cols}

    computer = FeatureComputer(windows=DEFAULT_WINDOWS)
    live_features = None
    for _, row in prefix.iterrows():
        reading = {c: row[c] for c in SENSOR_COLUMNS}
        live_features = computer.compute(CHECK_TYPE, reading)

    mismatches = []
    for col in feature_cols:
        train_val = float(training_features[col])
        live_val = float(live_features[col])
        if abs(train_val - live_val) > TOLERANCE:
            mismatches.append((col, train_val, live_val))

    print(
        f"Checked reading #{CHECK_UP_TO_ROW} of stream type={CHECK_TYPE!r} "
        f"(unit_id={ordered.loc[target_index, 'unit_id']}) across "
        f"{len(feature_cols)} rolling features."
    )
    if mismatches:
        print(f"FAIL: {len(mismatches)} feature(s) diverged between training and live replay:")
        for col, tv, lv in mismatches:
            print(f"  {col}: training={tv!r} live={lv!r}")
        raise SystemExit(1)

    print("PASS: training-time features and cold-start live-replay features are bit-identical.")


if __name__ == "__main__":
    main()
