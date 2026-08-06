"""Loads the Fleet Health Risk model, from Task 3's MLflow registry by
default, or from a self-contained baked-in artifact when
``MODEL_SOURCE=artifact`` (Task 9's Lambda deployment, see
``config.py``'s docstring on ``MODEL_SOURCE``).

In the default "registry" mode, nothing here is retrained or hardcoded:
the model served is whatever currently carries the ``production`` alias
in the ``fleetpulse-component-failure`` registered model, exactly the
source ``task-3/src/mock_inference.py`` reads from. A registry
re-promotion (``task-3/src/promote.py``) changes what this API serves
without a code change here.
"""
from __future__ import annotations

import logging
import threading

import joblib

from . import config

logger = logging.getLogger("fleet_health_api")

_lock = threading.Lock()
_state: dict = {"model": None, "version": None}


def load_model() -> None:
    """Load (or reload) the model. Called once at startup (or once per
    Lambda container on cold start, see ``main.py``'s ``handler``)."""
    if config.MODEL_SOURCE == "artifact":
        _load_from_artifact()
    else:
        _load_from_registry()


def _load_from_registry() -> None:
    import mlflow
    from mlflow.tracking import MlflowClient

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
            "source": "registry",
            "model_name": config.REGISTERED_MODEL_NAME,
            "model_alias": config.MODEL_ALIAS,
            "model_version": mv.version,
        },
    )


def _load_from_artifact() -> None:
    if not config.MODEL_ARTIFACT_PATH.exists():
        raise FileNotFoundError(
            f"{config.MODEL_ARTIFACT_PATH} not found. MODEL_SOURCE=artifact expects a "
            "self-contained model baked into the image by "
            "task-9/scripts/export_model.py, see task-9/README.md."
        )
    version = "unknown"
    if config.MODEL_ARTIFACT_VERSION_PATH.exists():
        version = config.MODEL_ARTIFACT_VERSION_PATH.read_text().strip()

    with _lock:
        _state["model"] = joblib.load(config.MODEL_ARTIFACT_PATH)
        _state["version"] = version
    logger.info(
        "model_loaded",
        extra={
            "event": "model_loaded",
            "source": "artifact",
            "model_name": config.REGISTERED_MODEL_NAME,
            "model_path": str(config.MODEL_ARTIFACT_PATH),
            "model_version": version,
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
