"""Shared paths and MLflow naming for the Task 4 serving app.

Points at Task 3's MLflow store rather than declaring a new one: the
registry Task 4 serves from is the registry Task 3 built, not a copy of it.
"""
from __future__ import annotations

from pathlib import Path

TASK4_ROOT = Path(__file__).resolve().parents[1]
TASK3_ROOT = TASK4_ROOT.parent / "task-3"

MLFLOW_DB_PATH = TASK3_ROOT / "mlflow.db"
MLFLOW_TRACKING_URI = f"sqlite:///{MLFLOW_DB_PATH}"

REGISTERED_MODEL_NAME = "fleetpulse-component-failure"
MODEL_ALIAS = "production"
MODEL_URI = f"models:/{REGISTERED_MODEL_NAME}@{MODEL_ALIAS}"

# Business thresholds on failure_probability, applied to the model's score
# to pick a recommended action. Not part of the model itself (moving them
# doesn't require retraining) and not the same number as Task 2's
# F2-optimal classification threshold (0.27, see task-2/README.md) — that
# threshold answers "is this a failure," these answer "how urgently should
# a fleet-ops technician act," a coarser, three-tier business call.
URGENT_ALERT_THRESHOLD = 0.60
SCHEDULE_SERVICE_THRESHOLD = 0.27
