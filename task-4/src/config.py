"""Shared paths and MLflow naming for the Task 4 serving app.

Points at Task 3's MLflow store rather than declaring a new one: the
registry Task 4 serves from is the registry Task 3 built, not a copy of it.
"""
from __future__ import annotations

import os
from pathlib import Path

TASK4_ROOT = Path(__file__).resolve().parents[1]

# Local-process default: task-3/ as a sibling directory. Overridable via
# FLEETPULSE_TASK3_ROOT for the Task 5 container, where task-3's MLflow
# store isn't necessarily reachable at that relative path — see
# task-5/README.md's "why the model isn't baked in" for the full reason
# (MLflow's local file-based artifact store records an *absolute* path in
# mlflow.db at logging time, so the mount location has to match wherever
# that path resolves, not just live at a fixed relative offset from this
# file).
TASK3_ROOT = Path(os.environ.get("FLEETPULSE_TASK3_ROOT", str(TASK4_ROOT.parent / "task-3")))

MLFLOW_DB_PATH = TASK3_ROOT / "mlflow.db"
MLFLOW_TRACKING_URI = f"sqlite:///{MLFLOW_DB_PATH}"

REGISTERED_MODEL_NAME = "fleetpulse-component-failure"
MODEL_ALIAS = "production"
MODEL_URI = f"models:/{REGISTERED_MODEL_NAME}@{MODEL_ALIAS}"

# Task 9 addition: MODEL_SOURCE picks how model_registry.load_model() gets
# its model. Default "registry" is everything above, unchanged, what
# Tasks 4/6/7 already run. "artifact" is Lambda-only (set via the Task 9
# Terraform module's environment_variables, never a code-level default):
# Lambda has no bind mounts, so it cannot reach Task 3's MLflow store the
# way Docker Compose or Kubernetes do, see task-9/README.md's "why the
# Lambda image doesn't use the MLflow registry" for the full reason. In
# "artifact" mode, MODEL_ARTIFACT_PATH points at a plain joblib file baked
# into the image at build time (task-9/scripts/export_model.py).
MODEL_SOURCE = os.environ.get("MODEL_SOURCE", "registry")
MODEL_ARTIFACT_PATH = Path(
    os.environ.get("MODEL_ARTIFACT_PATH", str(TASK4_ROOT / "model_artifact" / "model.joblib"))
)
MODEL_ARTIFACT_VERSION_PATH = MODEL_ARTIFACT_PATH.with_name("model_version.txt")

# Business thresholds on failure_probability, applied to the model's score
# to pick a recommended action. Not part of the model itself (moving them
# doesn't require retraining) and not the same number as Task 2's
# F2-optimal classification threshold (0.27, see task-2/README.md) — that
# threshold answers "is this a failure," these answer "how urgently should
# a fleet-ops technician act," a coarser, three-tier business call.
URGENT_ALERT_THRESHOLD = 0.60
SCHEDULE_SERVICE_THRESHOLD = 0.27
