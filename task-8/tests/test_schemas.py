"""Unit tests for the Fleet Health Risk API's Pydantic contracts
(``task-4/src/schemas.py``).

These are the boundary checks Task 4's done-when clause cares about: a
physically implausible sensor reading must never reach ``FeatureComputer``
or the model, it must be rejected as a clean validation error first.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from .conftest import import_task_module


@pytest.fixture
def schemas():
    return import_task_module("task-4", "schemas")


VALID_READING = {
    "air_temperature_k": 300.0,
    "process_temperature_k": 310.0,
    "rotational_speed_rpm": 1500.0,
    "torque_nm": 40.0,
    "tool_wear_min": 50.0,
}


def test_valid_reading_accepted(schemas):
    reading = schemas.SensorReading(**VALID_READING)
    assert reading.air_temperature_k == 300.0


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("air_temperature_k", 500.0),  # above 320.0
        ("process_temperature_k", 100.0),  # below 285.0
        ("rotational_speed_rpm", -1.0),  # below 0.0
        ("torque_nm", 999.0),  # above 150.0
        ("tool_wear_min", -5.0),  # below 0.0
    ],
)
def test_out_of_range_reading_rejected(schemas, field, bad_value):
    bad_reading = {**VALID_READING, field: bad_value}
    with pytest.raises(ValidationError):
        schemas.SensorReading(**bad_reading)


def test_predict_request_requires_at_least_one_reading(schemas):
    with pytest.raises(ValidationError):
        schemas.PredictRequest(vehicle_id="veh-1", type="L", telemetry_window=[])


def test_predict_request_rejects_unknown_machine_type(schemas):
    with pytest.raises(ValidationError):
        schemas.PredictRequest(
            vehicle_id="veh-1",
            type="Z",
            telemetry_window=[VALID_READING],
        )


def test_predict_request_accepts_valid_window(schemas):
    request = schemas.PredictRequest(
        vehicle_id="veh-1",
        type="M",
        telemetry_window=[VALID_READING, VALID_READING],
    )
    assert len(request.telemetry_window) == 2
    assert request.type.value == "M"
