"""Shared paths and MLflow naming for all of Task 3."""
from __future__ import annotations

from pathlib import Path

TASK3_ROOT = Path(__file__).resolve().parents[1]

MLFLOW_DB_PATH = TASK3_ROOT / "mlflow.db"
MLFLOW_TRACKING_URI = f"sqlite:///{MLFLOW_DB_PATH}"
MLFLOW_ARTIFACT_ROOT = f"file:{TASK3_ROOT / 'mlartifacts'}"

EXPERIMENT_NAME = "fleetpulse-component-failure"
REGISTERED_MODEL_NAME = "fleetpulse-component-failure"

REPORTS_DIR = TASK3_ROOT / "reports"
LEADERBOARD_PATH = REPORTS_DIR / "leaderboard.md"
PROMOTION_RATIONALE_PATH = REPORTS_DIR / "promotion_rationale.md"
RUN_IDS_PATH = REPORTS_DIR / "run_ids.json"
