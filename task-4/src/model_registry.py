"""Loads the Fleet Health Risk model from Task 3's MLflow registry.

Nothing here is retrained or hardcoded: the model served is whatever
currently carries the ``production`` alias in the
``fleetpulse-component-failure`` registered model, exactly the source
``task-3/src/mock_inference.py`` reads from. A registry re-promotion
(``task-3/src/promote.py``) changes what this API serves without a code
change here.
"""
from __future__ import annotations

import logging
import threading

import mlflow
from mlflow.tracking import MlflowClient

from . import config

logger = logging.getLogger("fleet_health_api")

_lock = threading.Lock()
_state: dict = {"model": None, "version": None}


def load_model() -> None:
    """Load (or reload) the production-aliased model. Called once at startup."""
    mlflow.set_tracking_uri(config.MLFLOW_TRACKING_URI)
    client = MlflowClient()
    with _lock:
        mv = client.get_model_version_by_alias(config.REGISTERED_MODEL_NAME, config.MODEL_ALIAS)
        model = mlflow.sklearn.load_model(config.MODEL_URI)
        _state["model"] = model
        _state["version"] = mv.version
    logger.info(
        "model_loaded",
        extra={
            "event": "model_loaded",
            "model_name": config.REGISTERED_MODEL_NAME,
            "model_alias": config.MODEL_ALIAS,
            "model_version": mv.version,
        },
    )


def get_model():
    if _state["model"] is None:
        raise RuntimeError(
            "model is not loaded yet — this should only happen if load_model() "
            "was not called at startup."
        )
    return _state["model"]


def get_model_version() -> str | None:
    return _state["version"]


def is_loaded() -> bool:
    return _state["model"] is not None
