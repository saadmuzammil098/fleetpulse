"""Turns a validated telemetry window into a risk score and an action.

Feature computation goes through exactly one call site — ``FeatureComputer
.compute()``, imported from Task 3's ``features.py`` via
``shared_features.py`` — the same class training replayed the whole
dataset through and the same class ``task-3/src/mock_inference.py`` scores
a live stream through. There is no second rolling-window implementation
here.

The request's ``type`` field is the ``FeatureComputer`` stream key, the
same key Task 3's training data was grouped and ordered by
(``task-3/src/load_data.py::STREAM_KEY_COLUMN``) — not ``vehicle_id``.
Each request gets its own fresh ``FeatureComputer``: the telemetry window
in the request body *is* the recent history to warm the rolling window
from, so there is no server-side per-vehicle stream state to keep across
requests (see the README for the tradeoff this makes against a long-lived,
per-vehicle buffer).
"""
from __future__ import annotations

import pandas as pd

from . import config
from .schemas import PredictRequest, PredictResponse, RecommendedAction
from .shared_features import FeatureComputer


def _recommend_action(failure_probability: float) -> RecommendedAction:
    if failure_probability >= config.URGENT_ALERT_THRESHOLD:
        return RecommendedAction.URGENT_ALERT
    if failure_probability >= config.SCHEDULE_SERVICE_THRESHOLD:
        return RecommendedAction.SCHEDULE_SERVICE
    return RecommendedAction.MONITOR


def build_feature_row(request: PredictRequest) -> dict:
    """Replay the telemetry window and return the most recent reading's
    full feature row (raw sensors + type + rolling features).
    """
    computer = FeatureComputer()
    key = request.type.value
    rolling_features: dict = {}
    last_reading: dict = {}
    for reading in request.telemetry_window:
        raw = reading.model_dump(exclude={"recorded_at"})
        rolling_features = computer.compute(key, raw)
        last_reading = raw

    return {**last_reading, "type": key, **rolling_features}


def predict(model, model_version: str, request: PredictRequest) -> PredictResponse:
    row = build_feature_row(request)
    X = pd.DataFrame([row])
    failure_probability = float(model.predict_proba(X)[:, 1][0])
    action = _recommend_action(failure_probability)

    return PredictResponse(
        vehicle_id=request.vehicle_id,
        failure_probability=failure_probability,
        recommended_action=action,
        model_name=config.REGISTERED_MODEL_NAME,
        model_version=model_version,
        readings_used=len(request.telemetry_window),
    )
