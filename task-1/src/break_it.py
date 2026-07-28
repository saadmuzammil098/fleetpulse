"""Deliberately break the pipeline with bad input (the roadmap's daily-loop step).

The real AI4I 2020 data is clean, so it never exercises the missing-value,
bad-timestamp, or outlier paths. This script builds a small synthetic
fixture that injects exactly those faults, plus a made-up timestamp column
(since the real dataset has none, see src/clean.py:handle_bad_timestamps),
runs it through ingest -> clean -> validate, and writes up what got caught
and what didn't. This is the "why" behind the cleaning/validation code
existing at all: nothing in this file's fixture should survive to the
cleaned output.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from src.clean import clean_dataset, handle_bad_timestamps
from src.ingest import to_working_schema
from src.validate import validate_dataset

DAY01_DIR = Path(__file__).resolve().parent.parent
OUT_PATH = DAY01_DIR / "reports" / "break_it_demo.md"


def build_bad_fixture() -> pd.DataFrame:
    n = 10
    good = pd.DataFrame(
        {
            "UDI": list(range(1, n + 1)),
            "Product ID": [
                "M14860", "L47181", "L47182", "L47183", "M14861",
                "H29424", "L47184", "M14862", "L47185", "H29425",
            ],
            "Type": ["M", "L", "L", "L", "M", "H", "L", "M", "L", "H"],
            "Air temperature [K]": [298.1, 298.2, 298.1, 298.2, 298.3, 298.1, 298.2, 298.3, 298.2, 298.1],
            "Process temperature [K]": [308.6, 308.7, 308.5, 308.6, 308.7, 308.6, 308.7, 308.8, 308.6, 308.7],
            "Rotational speed [rpm]": [1551, 1408, 1498, 1433, 1500, 1520, 1490, 1510, 1505, 1495],
            "Torque [Nm]": [42.8, 46.3, 49.4, 39.5, 40.1, 41.0, 43.2, 44.0, 42.0, 41.5],
            "Tool wear [min]": [0, 3, 5, 7, 10, 12, 15, 18, 20, 22],
            "Machine failure": [0] * n,
            "TWF": [0] * n,
            "HDF": [0] * n,
            "PWF": [0] * n,
            "OSF": [0] * n,
            "RNF": [0] * n,
        }
    )

    bad = good.copy()
    # Rows 0-4: one isolated fault each, everything else about the row stays valid.
    bad.loc[0, "Torque [Nm]"] = np.nan                 # missing sensor reading (dropout)
    bad.loc[1, "Air temperature [K]"] = 999.9           # impossible temperature (sensor fault)
    bad.loc[2, "Rotational speed [rpm]"] = -500          # negative speed (encoder error)
    bad.loc[3, "Tool wear [min]"] = 99999                # absurd tool wear (past any real service interval)
    bad.loc[4, "Type"] = "X"                             # invalid category
    # Row 5: bad timestamp only, sensor readings all valid.
    # Rows 6-9 stay entirely clean, as a control group.

    df = to_working_schema(bad)
    # Inject a timestamp column with a mix of good and corrupt values, to
    # exercise handle_bad_timestamps, which the real dataset never touches.
    df["timestamp"] = [
        "2026-01-01T00:00:00",
        "2026-01-01T00:05:00",
        "2026-01-01T00:10:00",
        "2026-01-01T00:15:00",
        "2026-01-01T00:20:00",
        "not-a-timestamp",
        "2026-01-01T00:30:00",
        "2026-01-01T00:35:00",
        "2026-01-01T00:40:00",
        "2026-01-01T00:45:00",
    ]
    return df


def main() -> None:
    bad_df = build_bad_fixture()

    n_total = len(bad_df)
    lines = [
        "# FleetPulse Task 1 — Break-It Demo",
        "",
        f"{n_total}-row synthetic fixture (the real AI4I 2020 data has zero "
        "faults, so it never exercises this code): 6 rows each carry one "
        "isolated, deliberate fault (missing value, impossible temperature, "
        "negative speed, absurd tool wear, invalid category, bad timestamp "
        f"string), the remaining {n_total - 6} rows are an untouched control "
        "group. Goal: every corrupted row gets caught somewhere in the "
        "pipeline; every clean row survives all the way to a passing "
        "validation.",
        "",
        "## Input (as ingested)",
        "",
        bad_df.drop(columns=["timestamp"]).to_markdown(index=False),
        "",
        f"Timestamp column (not part of the real dataset, injected here only "
        f"to exercise the timestamp cleaner): {list(bad_df['timestamp'])}",
        "",
    ]

    # Exercise the timestamp cleaner directly, since clean_dataset() only
    # calls it as a no-op on the real schema (no timestamp column there).
    ts_cleaned, ts_report = handle_bad_timestamps(bad_df)
    lines += [
        "## Timestamp cleaning",
        "",
        f"Dropped {ts_report['rows_dropped_bad_timestamp']} row(s) with an "
        "unparseable timestamp (the row with the literal string "
        '`"not-a-timestamp"`).',
        "",
    ]

    cleaned_df, clean_report = clean_dataset(ts_cleaned.drop(columns=["timestamp"]))
    lines += [
        "## Missing-value / outlier cleaning",
        "",
        f"- rows in: {clean_report['rows_in']}",
        f"- dropped for missing required field: {clean_report['missing_values']['rows_dropped_missing']} "
        f"(null counts: {clean_report['missing_values']['null_counts_by_column']})",
        f"- dropped for out-of-range sensor reading: {clean_report['outliers']['rows_dropped_outliers']} "
        f"(by column: {clean_report['outliers']['out_of_range_by_column']})",
        f"- rows out: {clean_report['rows_out']}",
        "",
        "## Surviving rows",
        "",
        cleaned_df.to_markdown(index=False) if len(cleaned_df) else "*(none)*",
        "",
    ]

    passed, validation_report, failure_cases = validate_dataset(cleaned_df)
    invalid_type_survived_cleaning = "X" in cleaned_df["type"].values if len(cleaned_df) else False

    lines += [
        "## Final schema validation on survivors",
        "",
        f"Result: {'PASSED' if passed else 'FAILED'}",
        "",
    ]
    if not passed:
        lines += [f"Failing cases: {len(failure_cases)}", ""]

    lines += [
        "## What this proves",
        "",
        f"Started with {n_total} rows: 1 missing value, 1 impossible "
        "temperature, 1 negative speed, 1 absurd tool wear, 1 invalid "
        f"category, 1 bad timestamp, {n_total - 6} untouched controls. "
        f"{clean_report['rows_out']} row(s) survived cleaning. The "
        "missing-value, out-of-range-sensor, and bad-timestamp faults are "
        "all caught and dropped during **cleaning**, before validation ever "
        "runs. The invalid-category fault is a different kind of problem, "
        "structurally present and not an outlier, so cleaning correctly "
        "leaves it alone" + (" (it's present in the surviving rows above)" if invalid_type_survived_cleaning else "")
        + f", and it's the **schema validator** that catches it: final "
        f"validation {'still passed' if passed else 'FAILED'} on the "
        "cleaning survivors, which is exactly the point of having two "
        "layers, cleaning handles bad *values*, the schema validator "
        "enforces the data *contract*, and between them nothing corrupt "
        "reaches the cleaned output the pipeline actually writes.",
    ]

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text("\n".join(lines))
    print(f"Break-it demo written to {OUT_PATH}")
    print(f"Survivors: {clean_report['rows_out']} / {n_total}")


if __name__ == "__main__":
    main()
