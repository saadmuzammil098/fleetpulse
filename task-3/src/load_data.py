"""Load Task 1's cleaned dataset and attach rolling-window sensor features.

Reads ``task-1/data/processed/cleaned.csv`` directly, the same way Task 2
does (not via a cross-package import — Task 1 and Task 2 both name their
package ``src``, so importing both as top-level ``src`` modules in one
process collides; see Task 2's ``load_data.py`` for the full explanation).
Task 3 also does not import Task 2's copy for the same reason.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from .features import SENSOR_COLUMNS, DEFAULT_WINDOWS, compute_ordered_features

TASK1_CLEANED_CSV = (
    Path(__file__).resolve().parents[2] / "task-1" / "data" / "processed" / "cleaned.csv"
)

CATEGORICAL_FEATURES = ["type"]
TARGET_COLUMN = "machine_failure"
LEAKY_FAILURE_MODE_COLUMNS = ["twf", "hdf", "pwf", "osf", "rnf"]

# The synthetic stream key: Task 3 simulates one continuous telemetry
# stream per machine `type` (L/M/H), ordered by tool_wear_min. See
# features.py's module docstring for why.
STREAM_KEY_COLUMN = "type"
ORDER_COLUMN = "tool_wear_min"

REQUIRED_COLUMNS = SENSOR_COLUMNS + CATEGORICAL_FEATURES + [TARGET_COLUMN]


def load_cleaned_dataframe(path: Path = TASK1_CLEANED_CSV) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. This is a DVC-tracked output of the task-1 "
            "pipeline. From the repo root, run `dvc pull`, or `cd task-1 && "
            "dvc repro` to rebuild it from raw data."
        )
    df = pd.read_csv(path)
    missing_cols = set(REQUIRED_COLUMNS) - set(df.columns)
    if missing_cols:
        raise ValueError(f"cleaned.csv is missing expected columns {sorted(missing_cols)}.")
    return df


def build_feature_dataset(
    path: Path = TASK1_CLEANED_CSV,
    windows: tuple[int, ...] = DEFAULT_WINDOWS,
) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame]:
    """Return (X, y, ordered_raw) with rolling-window features attached.

    ``ordered_raw`` is the base dataframe in the exact stream order
    features were computed in (grouped by `type`, sorted by
    `tool_wear_min`) — ``verify_feature_parity.py`` uses it to replay a
    prefix of a real stream through a fresh, cold-start FeatureComputer
    and confirm it reproduces this dataframe's features for that row
    exactly.
    """
    df = load_cleaned_dataframe(path)
    ordered = df.sort_values(
        [STREAM_KEY_COLUMN, ORDER_COLUMN], kind="stable"
    ).reset_index(drop=True)

    ordered_readings = [
        (row[STREAM_KEY_COLUMN], {c: row[c] for c in SENSOR_COLUMNS})
        for _, row in ordered.iterrows()
    ]
    feature_rows = compute_ordered_features(ordered_readings, windows=windows)
    feature_df = pd.DataFrame(feature_rows, index=ordered.index)

    X = pd.concat(
        [ordered[SENSOR_COLUMNS + CATEGORICAL_FEATURES], feature_df], axis=1
    )
    y = ordered[TARGET_COLUMN].astype(int)
    return X, y, ordered


def rolling_feature_columns(windows: tuple[int, ...] = DEFAULT_WINDOWS) -> list[str]:
    from .features import FeatureComputer

    return FeatureComputer(windows=windows).feature_names()
