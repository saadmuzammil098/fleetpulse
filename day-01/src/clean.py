"""Cleaning functions for FleetPulse telemetry.

Each function does one job and returns a (cleaned_df, report_dict) pair so
the pipeline can report exactly what was fixed, dropped, or flagged, rather
than silently mutating data.
"""
from __future__ import annotations

import pandas as pd

# Physically plausible sensor ranges, derived from the observed data with a
# margin, not invented numbers. See day-01/README.md for the reasoning.
SENSOR_RANGES = {
    "air_temperature_k": (280.0, 320.0),
    "process_temperature_k": (285.0, 330.0),
    "rotational_speed_rpm": (0.0, 5000.0),
    "torque_nm": (0.0, 150.0),
    "tool_wear_min": (0.0, 400.0),
}

TIMESTAMP_COLUMNS = ("timestamp", "reading_time", "recorded_at")


def handle_missing_values(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Drop rows missing a required sensor reading; report how many/why.

    A missing sensor reading can't be safely imputed for a physical
    quantity like torque or tool wear without domain modeling, so Day 1
    drops and reports rather than guessing.
    """
    required = list(SENSOR_RANGES.keys()) + ["unit_id", "product_id", "type"]
    before = len(df)
    null_counts = df[required].isnull().sum()
    null_counts = null_counts[null_counts > 0].to_dict()

    cleaned = df.dropna(subset=required).copy()
    dropped = before - len(cleaned)

    return cleaned, {
        "rows_before": before,
        "rows_dropped_missing": dropped,
        "null_counts_by_column": null_counts,
    }


def handle_bad_timestamps(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Parse and validate any timestamp-like column, dropping unparseable rows.

    The AI4I 2020 dataset has no timestamp field (it's per-unit sensor
    snapshots, not a time series), so this is a documented no-op here. It
    stays as a real function, not a stub, because every other dataset this
    pipeline will see in later FleetPulse days *does* carry telemetry
    timestamps, and it's exercised directly in the break-it demo
    (see src/break_it.py) against a synthetic bad-timestamp fixture.
    """
    ts_col = next((c for c in TIMESTAMP_COLUMNS if c in df.columns), None)
    if ts_col is None:
        return df, {"timestamp_column": None, "note": "no timestamp column in this dataset"}

    before = len(df)
    parsed = pd.to_datetime(df[ts_col], errors="coerce")
    bad = parsed.isnull() & df[ts_col].notnull()
    cleaned = df.loc[~bad].copy()
    cleaned[ts_col] = parsed.loc[~bad]

    return cleaned, {
        "timestamp_column": ts_col,
        "rows_before": before,
        "rows_dropped_bad_timestamp": int(bad.sum()),
    }


def handle_outliers(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Flag and drop sensor readings outside physically plausible ranges.

    A negative torque or a 900K air temperature isn't a rare-but-real
    event, it's a sensor fault or transmission error, so these rows are
    dropped rather than clipped, clipping would silently fabricate a
    plausible-looking but fake reading.
    """
    before = len(df)
    out_of_range_mask = pd.Series(False, index=df.index)
    per_column = {}

    for col, (lo, hi) in SENSOR_RANGES.items():
        col_mask = ~df[col].between(lo, hi)
        per_column[col] = int(col_mask.sum())
        out_of_range_mask |= col_mask

    cleaned = df.loc[~out_of_range_mask].copy()

    return cleaned, {
        "rows_before": before,
        "rows_dropped_outliers": int(out_of_range_mask.sum()),
        "out_of_range_by_column": per_column,
        "ranges_used": SENSOR_RANGES,
    }


def clean_dataset(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Run the full cleaning sequence, returning the cleaned frame plus a report."""
    report = {"rows_in": len(df)}

    df, missing_report = handle_missing_values(df)
    report["missing_values"] = missing_report

    df, ts_report = handle_bad_timestamps(df)
    report["timestamps"] = ts_report

    df, outlier_report = handle_outliers(df)
    report["outliers"] = outlier_report

    df = df.reset_index(drop=True)
    report["rows_out"] = len(df)
    report["rows_dropped_total"] = report["rows_in"] - report["rows_out"]

    return df, report
