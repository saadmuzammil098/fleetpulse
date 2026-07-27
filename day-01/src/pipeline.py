"""FleetPulse Day 1 pipeline: raw CSV -> cleaned, validated dataset + reports.

The single command Day 1's "done when" clause asks for:

    python -m src.pipeline

Run from day-01/, after `dvc pull` from the repo root. Deterministic: same
input, same code -> byte-identical cleaned.csv and reports every time,
which is what lets `dvc repro` prove reproducibility by checksum.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

DAY01_DIR = Path(__file__).resolve().parent.parent
RAW_PATH = DAY01_DIR / "data" / "raw" / "ai4i2020.csv"
CLEANED_PATH = DAY01_DIR / "data" / "processed" / "cleaned.csv"
PROFILE_REPORT_PATH = DAY01_DIR / "reports" / "profile_report.md"
VALIDATION_REPORT_PATH = DAY01_DIR / "reports" / "validation_report.md"
CLEAN_REPORT_JSON_PATH = DAY01_DIR / "reports" / "clean_report.json"

sys.path.insert(0, str(DAY01_DIR))

from src.ingest import ingest  # noqa: E402
from src.clean import clean_dataset  # noqa: E402
from src.validate import validate_dataset  # noqa: E402
from src.profile import generate_profile_report  # noqa: E402


def run(raw_path: Path = RAW_PATH) -> int:
    print(f"[1/4] Ingesting raw data from {raw_path}")
    df = ingest(raw_path)
    print(f"      {len(df)} rows ingested")

    print("[2/4] Cleaning (missing values, timestamps, outliers)")
    cleaned_df, clean_report = clean_dataset(df)
    print(
        f"      {clean_report['rows_dropped_total']} rows dropped "
        f"({clean_report['rows_in']} -> {clean_report['rows_out']})"
    )

    print("[3/4] Validating against schema")
    passed, validation_report, failure_cases = validate_dataset(cleaned_df)
    VALIDATION_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    VALIDATION_REPORT_PATH.write_text(validation_report)
    print(f"      validation {'PASSED' if passed else 'FAILED'} -> {VALIDATION_REPORT_PATH}")

    if not passed:
        print("      Cleaned dataset failed schema validation, refusing to write output.")
        print(f"      See {VALIDATION_REPORT_PATH} for details.")
        return 1

    print("[4/4] Writing cleaned dataset and profile report")
    CLEANED_PATH.parent.mkdir(parents=True, exist_ok=True)
    cleaned_df.to_csv(CLEANED_PATH, index=False)

    profile_report = generate_profile_report(cleaned_df, clean_report)
    PROFILE_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_REPORT_PATH.write_text(profile_report)

    CLEAN_REPORT_JSON_PATH.write_text(json.dumps(clean_report, default=str, indent=2))

    print(f"      cleaned dataset -> {CLEANED_PATH} ({len(cleaned_df)} rows)")
    print(f"      profile report  -> {PROFILE_REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
