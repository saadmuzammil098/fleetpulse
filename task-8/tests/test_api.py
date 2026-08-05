"""Request/response tests for the Fleet Health Risk API (``task-4/src/main.py``).

The real ``model_registry.load_model()`` reads Task 3's MLflow registry
(``task-3/mlflow.db``), which is not tracked in git and does not exist on
a fresh clone or in CI. These tests monkeypatch the startup hook to load
the ``fixture_model`` (see conftest.py) instead, so the full FastAPI
request path, validation, feature building, prediction, response shape,
runs against a real, freshly-fit model rather than skipping the model
entirely.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from .conftest import import_task_module

VALID_READING = {
    "air_temperature_k": 300.0,
    "process_temperature_k": 310.0,
    "rotational_speed_rpm": 1500.0,
    "torque_nm": 40.0,
    "tool_wear_min": 50.0,
}


@pytest.fixture
def client(fixture_model, monkeypatch):
    main = import_task_module("task-4", "main")
    model_registry = main.model_registry

    def _fake_load_model() -> None:
        model_registry._state["model"] = fixture_model
        model_registry._state["version"] = "test-fixture"

    monkeypatch.setattr(model_registry, "load_model", _fake_load_model)

    with TestClient(main.app) as test_client:
        yield test_client


def test_health_reports_model_loaded(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["model_loaded"] is True
    assert body["model_version"] == "test-fixture"


def test_predict_happy_path(client):
    payload = {
        "vehicle_id": "veh-1",
        "type": "M",
        "telemetry_window": [VALID_READING],
    }
    response = client.post("/predict", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["vehicle_id"] == "veh-1"
    assert 0.0 <= body["failure_probability"] <= 1.0
    assert body["recommended_action"] in {"monitor", "schedule_service", "urgent_alert"}
    assert body["readings_used"] == 1


def test_predict_rejects_out_of_range_reading(client):
    bad_reading = {**VALID_READING, "torque_nm": 999.0}
    payload = {
        "vehicle_id": "veh-1",
        "type": "M",
        "telemetry_window": [bad_reading],
    }
    response = client.post("/predict", json=payload)
    assert response.status_code == 422


def test_predict_rejects_empty_telemetry_window(client):
    payload = {"vehicle_id": "veh-1", "type": "M", "telemetry_window": []}
    response = client.post("/predict", json=payload)
    assert response.status_code == 422
