"""Raw data ingestion for FleetPulse Day 1.

Loads the UCI AI4I 2020 Predictive Maintenance dataset, standing in for
per-vehicle sensor telemetry (see day-01/README.md for the mapping and why
this dataset was chosen over the literally-automotive alternative).
"""
from pathlib import Path

import pandas as pd

RAW_COLUMNS = [
    "UDI",
    "Product ID",
    "Type",
    "Air temperature [K]",
    "Process temperature [K]",
    "Rotational speed [rpm]",
    "Torque [Nm]",
    "Tool wear [min]",
    "Machine failure",
    "TWF",
    "HDF",
    "PWF",
    "OSF",
    "RNF",
]

# raw -> snake_case, unit-suffixed working column names
COLUMN_RENAME = {
    "UDI": "unit_id",
    "Product ID": "product_id",
    "Type": "type",
    "Air temperature [K]": "air_temperature_k",
    "Process temperature [K]": "process_temperature_k",
    "Rotational speed [rpm]": "rotational_speed_rpm",
    "Torque [Nm]": "torque_nm",
    "Tool wear [min]": "tool_wear_min",
    "Machine failure": "machine_failure",
    "TWF": "twf",
    "HDF": "hdf",
    "PWF": "pwf",
    "OSF": "osf",
    "RNF": "rnf",
}


def load_raw(path: str | Path) -> pd.DataFrame:
    """Load the raw CSV exactly as downloaded, no transformation.

    Uses utf-8-sig because the UCI export ships with a BOM on the first
    header cell ("﻿UDI"), which otherwise silently breaks column lookup.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"Raw data not found at {path}. Run `dvc pull` from the repo "
            "root, or place ai4i2020.csv there manually."
        )
    df = pd.read_csv(path, encoding="utf-8-sig")

    missing = set(RAW_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"Raw data is missing expected columns: {missing}")

    return df


def to_working_schema(df: pd.DataFrame) -> pd.DataFrame:
    """Rename raw columns to a stable, code-friendly working schema."""
    return df.rename(columns=COLUMN_RENAME)


def ingest(path: str | Path) -> pd.DataFrame:
    return to_working_schema(load_raw(path))
