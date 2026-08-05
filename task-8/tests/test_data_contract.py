"""Data tests: Task 1's Pandera schema (``task-1/src/schema.py``) run as
automated tests, not only as a one-off report at ingestion time.

Task 1 already validates the real dataset at ingestion (see
``task-1/reports/validation_report.md``). What was missing is a test that
runs on every PR and fails loudly the moment the schema itself, or the
cleaning code that's supposed to satisfy it, regresses, independent of
whether anyone remembers to re-run ``dvc repro`` and eyeball the report.
"""
from __future__ import annotations

import pandas as pd
import pytest
from pandera.errors import SchemaErrors

from .conftest import make_synthetic_fleet_data


@pytest.fixture
def schema_module():
    from .conftest import import_task_module

    return import_task_module("task-1", "schema")


def _as_cleaned_schema_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Add the columns Task 1's schema requires beyond the five sensors."""
    out = df.copy()
    out["unit_id"] = range(1, len(out) + 1)
    out["product_id"] = out["type"] + out["unit_id"].astype(str).str.zfill(5)
    out["rotational_speed_rpm"] = out["rotational_speed_rpm"].round().astype(int)
    out["tool_wear_min"] = out["tool_wear_min"].round().astype(int)
    for flag in ("twf", "hdf", "pwf", "osf", "rnf"):
        out[flag] = 0
    return out


def test_synthetic_dataset_passes_the_cleaned_data_schema(schema_module):
    df = _as_cleaned_schema_frame(make_synthetic_fleet_data(n_per_type=10))
    # Raises SchemaErrors on failure, a clean pass is the assertion.
    schema_module.cleaned_schema.validate(df, lazy=True)


def test_schema_rejects_sensor_reading_outside_physical_range(schema_module):
    df = _as_cleaned_schema_frame(make_synthetic_fleet_data(n_per_type=10))
    df.loc[0, "torque_nm"] = 999.0  # far outside (0.0, 150.0)

    with pytest.raises(SchemaErrors):
        schema_module.cleaned_schema.validate(df, lazy=True)


def test_schema_rejects_unknown_machine_type(schema_module):
    df = _as_cleaned_schema_frame(make_synthetic_fleet_data(n_per_type=10))
    df.loc[0, "type"] = "X"

    with pytest.raises(SchemaErrors):
        schema_module.cleaned_schema.validate(df, lazy=True)


def test_schema_rejects_null_required_field(schema_module):
    df = _as_cleaned_schema_frame(make_synthetic_fleet_data(n_per_type=10))
    df.loc[0, "air_temperature_k"] = None

    with pytest.raises(SchemaErrors):
        schema_module.cleaned_schema.validate(df, lazy=True)


def test_schema_rejects_duplicate_unit_id(schema_module):
    df = _as_cleaned_schema_frame(make_synthetic_fleet_data(n_per_type=10))
    df.loc[1, "unit_id"] = df.loc[0, "unit_id"]

    with pytest.raises(SchemaErrors):
        schema_module.cleaned_schema.validate(df, lazy=True)
