"""Behavioral tests: directional-expectation checks a domain expert would
actually want to see, run through the real serving path
(``task-4/src/inference.py``).

FleetPulse's dataset (UCI AI4I 2020) has no vibration channel, the
roadmap's generic "temperature and vibration" example doesn't map onto
this project's actual sensors (air/process temperature, rotational speed,
torque, tool wear). The two sensors that jointly drive this dataset's real
overstrain failure mode (OSF) are torque and tool wear: sustained high
torque late in a component's operating life is a textbook mechanical
overstrain signature, not a coincidence a model should be free to ignore.
That's the FleetPulse-specific analogue used here, same spirit as the
temperature/vibration example, applied to the sensors this project
actually has.

A model that gets this backwards, or that a bug makes indifferent to it,
is not safe to ship even if its offline PR-AUC looks fine, this is the
check that catches that class of bug.
"""
from __future__ import annotations

from .conftest import import_task_module

VALID_READING = {
    "air_temperature_k": 300.0,
    "process_temperature_k": 310.0,
    "rotational_speed_rpm": 1500.0,
    "torque_nm": 25.0,
    "tool_wear_min": 20.0,
}


def _predict_probability(fixture_model, machine_type: str, telemetry_window: list[dict]) -> float:
    schemas = import_task_module("task-4", "schemas")
    inference = import_task_module("task-4", "inference")

    request = schemas.PredictRequest(
        vehicle_id="veh-behavioral-test",
        type=machine_type,
        telemetry_window=telemetry_window,
    )
    response = inference.predict(fixture_model, "test-fixture", request)
    return response.failure_probability


def test_high_torque_and_tool_wear_raises_risk_relative_to_baseline(fixture_model):
    baseline_window = [VALID_READING, VALID_READING, VALID_READING]

    escalated_reading = {
        **VALID_READING,
        "torque_nm": 60.0,
        "tool_wear_min": 220.0,
    }
    escalated_window = [VALID_READING, VALID_READING, escalated_reading]

    baseline_risk = _predict_probability(fixture_model, "M", baseline_window)
    escalated_risk = _predict_probability(fixture_model, "M", escalated_window)

    assert escalated_risk > baseline_risk, (
        f"expected raising torque and tool wear together to raise predicted "
        f"failure risk, got baseline={baseline_risk:.3f} escalated={escalated_risk:.3f}"
    )


def test_clearly_healthy_profile_scores_low_risk(fixture_model):
    healthy_window = [
        {
            "air_temperature_k": 298.0,
            "process_temperature_k": 308.0,
            "rotational_speed_rpm": 1500.0,
            "torque_nm": 20.0,
            "tool_wear_min": 5.0,
        }
    ]

    risk = _predict_probability(fixture_model, "L", healthy_window)

    assert risk < 0.3, f"expected a clearly healthy sensor profile to score low risk, got {risk:.3f}"
