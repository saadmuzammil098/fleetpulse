"""Load Day 2's training data from Day 1's DVC-tracked cleaned dataset.

Day 1 already did ingestion, cleaning, and Pandera schema validation, and
versioned the result (``day-01/data/processed/cleaned.csv``) as a DVC
pipeline output. Day 2 does not re-clean or re-validate raw sensor data,
that logic and its own reproducibility story live in day-01. This module's
only job is to locate that file (after ``dvc pull`` has populated it from
the Floci-backed S3 remote) and split it into features/label for modeling.

Why not re-import day-01's Pandera schema object directly: day-01 and
day-02 both name their package ``src`` (relative imports inside each,
``from .clean import ...`` etc.), so importing both as top-level ``src``
modules in the same process collides. Re-implementing an import shim just
to reuse a validation call whose result is already baked into the CSV
DVC is tracking would be exactly the kind of premature abstraction the
project avoids. Instead, ``build_dataset`` runs a light structural sanity
check (right columns, no nulls) as a cheap trip-wire against a stale or
hand-edited cleaned.csv, without re-deriving day-01's cleaning rules.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

DAY01_CLEANED_CSV = (
    Path(__file__).resolve().parents[2] / "day-01" / "data" / "processed" / "cleaned.csv"
)

# Sensor + machine-state features available at prediction time.
NUMERIC_FEATURES = [
    "air_temperature_k",
    "process_temperature_k",
    "rotational_speed_rpm",
    "torque_nm",
    "tool_wear_min",
]
CATEGORICAL_FEATURES = ["type"]
FEATURE_COLUMNS = NUMERIC_FEATURES + CATEGORICAL_FEATURES

TARGET_COLUMN = "machine_failure"

# twf/hdf/pwf/osf/rnf are *failure-mode* flags: machine_failure is true iff
# at least one of these is true. They are not available before a failure
# occurs, they are how the dataset records which failure occurred, so they
# are the label decomposed, not a predictor. Including them is a textbook
# label leak (see src/break_it.py), so they are named here and excluded,
# never silently dropped without explanation.
LEAKY_FAILURE_MODE_COLUMNS = ["twf", "hdf", "pwf", "osf", "rnf"]

REQUIRED_COLUMNS = FEATURE_COLUMNS + [TARGET_COLUMN]


def load_cleaned_dataframe(path: Path = DAY01_CLEANED_CSV) -> pd.DataFrame:
    """Read Day 1's cleaned, validated dataset. Raises if it's missing."""
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. This file is a DVC-tracked output of the "
            "day-01 pipeline, not something day-02 generates. From the repo "
            "root, run `dvc pull` (fetches it from the Floci S3 remote), "
            "or `cd day-01 && dvc repro` to rebuild it from raw data."
        )
    df = pd.read_csv(path)

    missing_cols = set(REQUIRED_COLUMNS) - set(df.columns)
    if missing_cols:
        raise ValueError(
            f"cleaned.csv is missing expected columns {sorted(missing_cols)}. "
            "It may be stale or built from a different day-01 schema version; "
            "re-run `dvc repro` in day-01."
        )
    null_counts = df[REQUIRED_COLUMNS].isnull().sum()
    bad = null_counts[null_counts > 0]
    if not bad.empty:
        raise ValueError(
            f"cleaned.csv has unexpected nulls in required columns: {bad.to_dict()}. "
            "Day 1's cleaning step should have dropped these; treat this as a "
            "signal the upstream pipeline output is stale or corrupted."
        )
    return df


def build_dataset(path: Path = DAY01_CLEANED_CSV) -> tuple[pd.DataFrame, pd.Series]:
    """Return (X, y) ready for an sklearn Pipeline: features and binary label."""
    df = load_cleaned_dataframe(path)
    X = df[FEATURE_COLUMNS].copy()
    y = df[TARGET_COLUMN].astype(int).copy()
    return X, y
