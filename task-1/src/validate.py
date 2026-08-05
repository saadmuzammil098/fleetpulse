"""Validation layer: run the Pandera schema and produce a human-readable report."""
from __future__ import annotations

import pandas as pd
from pandera.errors import SchemaErrors

from .schema import cleaned_schema


def validate_dataset(df: pd.DataFrame) -> tuple[bool, str, pd.DataFrame | None]:
    """Validate df against the cleaned-data schema.

    Returns (passed, markdown_report, failure_cases). failure_cases is the
    Pandera failure-case table when validation fails, otherwise None.
    """
    try:
        cleaned_schema.validate(df, lazy=True)
    except SchemaErrors as exc:
        failure_cases = exc.failure_cases
        report = _render_failure_report(df, failure_cases)
        return False, report, failure_cases

    report = _render_pass_report(df)
    return True, report, None


def _render_pass_report(df: pd.DataFrame) -> str:
    lines = [
        "# FleetPulse Task 1 — Validation Report",
        "",
        "**Result:** PASSED",
        f"**Rows validated:** {len(df)}",
        f"**Columns checked:** {len(df.columns)}",
        "",
        "All rows satisfy the schema: no nulls in required fields, "
        "categorical fields within their allowed set, and every sensor "
        "reading within its physically plausible range.",
    ]
    return "\n".join(lines)


def _render_failure_report(df: pd.DataFrame, failure_cases: pd.DataFrame) -> str:
    by_check = failure_cases.groupby(["column", "check"]).size().reset_index(name="count")

    lines = [
        "# FleetPulse Task 1 — Validation Report",
        "",
        "**Result:** FAILED",
        f"**Rows checked:** {len(df)}",
        f"**Total failing cases:** {len(failure_cases)}",
        "",
        "## Failures by column and check",
        "",
        "| column | check | count |",
        "|---|---|---|",
    ]
    for _, row in by_check.iterrows():
        lines.append(f"| {row['column']} | {row['check']} | {row['count']} |")

    lines += [
        "",
        "## Sample failing rows (up to 10)",
        "",
        failure_cases.head(10).to_markdown(index=False),
    ]
    return "\n".join(lines)
