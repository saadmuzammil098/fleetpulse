"""Lightweight, dependency-free profiling report for the cleaned dataset.

Deliberately not ydata-profiling/pandas-profiling: those pull in a heavy,
version-fragile dependency tree for what Task 1 actually needs, a clear
per-column summary of shape, missingness, distribution, and failure-label
balance. Custom and ~80 lines beats a fragile heavyweight dependency here.
"""
from __future__ import annotations

import pandas as pd

NUMERIC_COLUMNS = [
    "air_temperature_k",
    "process_temperature_k",
    "rotational_speed_rpm",
    "torque_nm",
    "tool_wear_min",
]


def generate_profile_report(df: pd.DataFrame, clean_report: dict) -> str:
    lines = [
        "# FleetPulse Task 1 — Data Profile Report",
        "",
        f"**Rows:** {len(df)}  ",
        f"**Columns:** {len(df.columns)}  ",
        f"**Rows dropped during cleaning:** {clean_report['rows_dropped_total']} "
        f"(of {clean_report['rows_in']} raw rows)",
        "",
        "## Cleaning summary",
        "",
        f"- Missing-value drops: {clean_report['missing_values']['rows_dropped_missing']}",
        f"- Bad-timestamp drops: {clean_report['timestamps'].get('rows_dropped_bad_timestamp', 'n/a — no timestamp column in this dataset')}",
        f"- Outlier drops: {clean_report['outliers']['rows_dropped_outliers']}",
        "",
        "## Numeric sensor columns",
        "",
        "| column | mean | std | min | 25% | 50% | 75% | max |",
        "|---|---|---|---|---|---|---|---|",
    ]

    desc = df[NUMERIC_COLUMNS].describe()
    for col in NUMERIC_COLUMNS:
        s = desc[col]
        lines.append(
            f"| {col} | {s['mean']:.2f} | {s['std']:.2f} | {s['min']:.2f} | "
            f"{s['25%']:.2f} | {s['50%']:.2f} | {s['75%']:.2f} | {s['max']:.2f} |"
        )

    lines += [
        "",
        "## Categorical columns",
        "",
        "**type**",
        "",
        df["type"].value_counts().rename_axis("value").reset_index(name="count").to_markdown(index=False),
        "",
        "## Failure-label balance",
        "",
        "This is the class imbalance any Task 2 model has to handle: failures "
        "are rare relative to healthy readings, which is exactly why the "
        "roadmap's Task 2 spec calls for recall-weighted or PR-AUC metrics "
        "instead of raw accuracy.",
        "",
        df["machine_failure"].value_counts().rename_axis("machine_failure").reset_index(name="count").to_markdown(index=False),
        "",
        "## Missingness (post-cleaning, should be all zero)",
        "",
        df.isnull().sum().rename("null_count").reset_index().rename(columns={"index": "column"}).to_markdown(index=False),
    ]

    return "\n".join(lines)
