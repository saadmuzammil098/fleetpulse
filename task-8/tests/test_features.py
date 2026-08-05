"""Unit tests for Task 3's shared feature module (``task-3/src/features.py``).

This is the one place FleetPulse's rolling-window sensor math lives,
training and serving both replay readings through the same
``FeatureComputer.compute()`` (see that module's docstring). If this math
is wrong, both training and serving are wrong identically, which is
exactly the kind of bug a unit test at this layer catches before it ever
reaches a behavioral test.
"""
from __future__ import annotations

import pytest

from .conftest import import_task_module


@pytest.fixture
def features_module():
    return import_task_module("task-3", "features")


def test_first_reading_has_zero_std_and_zero_roc(features_module):
    computer = features_module.FeatureComputer()
    reading = {
        "air_temperature_k": 300.0,
        "process_temperature_k": 310.0,
        "rotational_speed_rpm": 1500.0,
        "torque_nm": 40.0,
        "tool_wear_min": 0.0,
    }
    out = computer.compute("L", reading)

    assert out["air_temperature_k_rollstd_w3"] == 0.0
    assert out["air_temperature_k_roc"] == 0.0
    assert out["air_temperature_k_rollmean_w3"] == 300.0


def test_rolling_mean_and_roc_over_known_sequence(features_module):
    computer = features_module.FeatureComputer(windows=(2,))
    base = {
        "air_temperature_k": 300.0,
        "process_temperature_k": 310.0,
        "rotational_speed_rpm": 1500.0,
        "torque_nm": 40.0,
        "tool_wear_min": 0.0,
    }

    computer.compute("L", {**base, "torque_nm": 10.0})
    computer.compute("L", {**base, "torque_nm": 20.0})
    out = computer.compute("L", {**base, "torque_nm": 30.0})

    # window=2 mean over the two most recent readings including this one (20, 30)
    assert out["torque_nm_rollmean_w2"] == pytest.approx(25.0)
    # rate of change vs the immediately preceding reading (20 -> 30)
    assert out["torque_nm_roc"] == pytest.approx(10.0)


def test_stream_keys_are_independent(features_module):
    computer = features_module.FeatureComputer()
    base = {
        "air_temperature_k": 300.0,
        "process_temperature_k": 310.0,
        "rotational_speed_rpm": 1500.0,
        "torque_nm": 40.0,
        "tool_wear_min": 0.0,
    }

    computer.compute("L", {**base, "torque_nm": 100.0})
    # A fresh key ("H") must not see "L"'s history.
    out = computer.compute("H", {**base, "torque_nm": 5.0})

    assert out["torque_nm_rollmean_w3"] == 5.0
    assert out["torque_nm_roc"] == 0.0


def test_missing_sensor_column_raises(features_module):
    computer = features_module.FeatureComputer()
    incomplete_reading = {
        "air_temperature_k": 300.0,
        "process_temperature_k": 310.0,
        "rotational_speed_rpm": 1500.0,
        "torque_nm": 40.0,
        # tool_wear_min missing
    }
    with pytest.raises(ValueError, match="missing required sensor columns"):
        computer.compute("L", incomplete_reading)


def test_feature_names_match_computed_keys(features_module):
    computer = features_module.FeatureComputer()
    reading = {
        "air_temperature_k": 300.0,
        "process_temperature_k": 310.0,
        "rotational_speed_rpm": 1500.0,
        "torque_nm": 40.0,
        "tool_wear_min": 0.0,
    }
    out = computer.compute("L", reading)
    assert set(out.keys()) == set(computer.feature_names())


def test_compute_ordered_features_matches_stepwise_compute(features_module):
    """``compute_ordered_features`` must be a thin replay loop, not a second
    implementation, this pins that equivalence down directly."""
    ordered_readings = [
        ("L", {"air_temperature_k": 300.0, "process_temperature_k": 310.0,
               "rotational_speed_rpm": 1500.0, "torque_nm": 10.0, "tool_wear_min": 0.0}),
        ("L", {"air_temperature_k": 301.0, "process_temperature_k": 311.0,
               "rotational_speed_rpm": 1510.0, "torque_nm": 20.0, "tool_wear_min": 5.0}),
    ]

    batch_result = features_module.compute_ordered_features(ordered_readings)

    computer = features_module.FeatureComputer()
    stepwise_result = [computer.compute(key, reading) for key, reading in ordered_readings]

    assert batch_result == stepwise_result
