"""Mock real-time inference call: one raw telemetry reading in, one risk score out.

Run as `python -m src.mock_inference` from task-3/. Loads whatever model
is currently aliased `production` in the registry and feeds it a
simulated live sensor reading. The feature computation step imports
``FeatureComputer`` from ``src/features.py`` — the exact same class
``load_data.build_feature_dataset`` replays training data through — and
nothing else. There is no second rolling-window implementation here.

A real serving system always computes the full feature vector (raw
sensors + every rolling feature) and lets the model's own
``ColumnTransformer`` select the columns it was actually fit on; extra
columns are harmless. That is deliberate: which run wins promotion
(see reports/promotion_rationale.md) is a modeling decision, but the
feature-computation code path a live reading goes through is not — it is
one path, used identically no matter which model ends up behind it.
"""
from __future__ import annotations

import sys

import mlflow
import pandas as pd

from . import config
from .features import FeatureComputer


def load_production_model():
    mlflow.set_tracking_uri(config.MLFLOW_TRACKING_URI)
    uri = f"models:/{config.REGISTERED_MODEL_NAME}@production"
    return mlflow.sklearn.load_model(uri)


def score_reading(model, computer: FeatureComputer, key: str, reading: dict) -> dict:
    """Compute this reading's features and return the model's risk score.

    ``reading`` must carry every column in SENSOR_COLUMNS — exactly what
    a live telemetry frame from a fleet vehicle of this ``type`` would
    carry. ``computer`` holds the rolling history for this stream key
    across calls, so features warm up the same way they would for a real
    stream that has been running for a while.
    """
    rolling_features = computer.compute(key, reading)
    row = {**reading, "type": key, **rolling_features}
    X = pd.DataFrame([row])
    proba = model.predict_proba(X)[:, 1][0]
    return {"failure_probability": float(proba), "features": row}


def main() -> None:
    model = load_production_model()
    computer = FeatureComputer()

    # A short simulated stream for one type-M vehicle: sensor readings
    # arriving one at a time, as they would off a live telemetry feed.
    stream = [
        {"air_temperature_k": 298.1, "process_temperature_k": 308.6,
         "rotational_speed_rpm": 1551, "torque_nm": 42.8, "tool_wear_min": 0},
        {"air_temperature_k": 298.3, "process_temperature_k": 308.8,
         "rotational_speed_rpm": 1500, "torque_nm": 45.1, "tool_wear_min": 12},
        {"air_temperature_k": 298.9, "process_temperature_k": 309.4,
         "rotational_speed_rpm": 1400, "torque_nm": 51.3, "tool_wear_min": 27},
        {"air_temperature_k": 300.1, "process_temperature_k": 310.9,
         "rotational_speed_rpm": 1280, "torque_nm": 58.9, "tool_wear_min": 45},
    ]

    for i, reading in enumerate(stream, start=1):
        result = score_reading(model, computer, key="M", reading=reading)
        print(
            f"reading {i}: tool_wear_min={reading['tool_wear_min']:>3} "
            f"-> failure_probability={result['failure_probability']:.3f}"
        )


if __name__ == "__main__":
    main()
    sys.exit(0)
