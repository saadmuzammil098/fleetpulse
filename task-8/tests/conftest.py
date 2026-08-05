"""Shared fixtures for Task 8's pytest suite.

Every task folder in this repo names its own package ``src`` (see
``task-2/src/load_data.py``'s docstring for the original reason this
collides). Importing two tasks' modules into one pytest process the normal
way clobbers ``sys.modules["src"]`` the moment a second task is imported.
``import_task_module`` below is the test-side version of the same trick
``task-4/src/shared_features.py`` already uses in application code: import
one task's ``src`` package, hand back the requested submodule, then evict
every ``src``-rooted entry from ``sys.modules`` (and the path we added) so
the next task starts from a clean slate.

Fixture models here are trained on small synthetic data generated in this
file, not on Task 1's real DVC-tracked dataset. GitHub-hosted CI runners
cannot reach ``localhost.floci.io``, the local Floci S3 endpoint Task 1's
DVC remote points at, so CI cannot ``dvc pull`` the real dataset. See
``task-8/README.md``'s "why synthetic data in CI" section for the tradeoff
this makes.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

SENSOR_COLUMNS = [
    "air_temperature_k",
    "process_temperature_k",
    "rotational_speed_rpm",
    "torque_nm",
    "tool_wear_min",
]


def import_task_module(task_dir: str, module_name: str):
    """Import ``src.<module_name>`` from ``task_dir`` in isolation.

    Returns the requested submodule. Any sibling submodule it pulled in
    via a relative import (``main`` pulling in ``model_registry``, say) is
    reachable as an attribute on the returned module, not via a second
    call to this function, since a second call evicts the first import.
    """
    task_path = REPO_ROOT / task_dir
    sys.path.insert(0, str(task_path))
    try:
        return importlib.import_module(f"src.{module_name}")
    finally:
        sys.path.remove(str(task_path))
        for name in list(sys.modules):
            if name == "src" or name.startswith("src."):
                del sys.modules[name]


def make_synthetic_fleet_data(n_per_type: int = 150, seed: int = 42) -> pd.DataFrame:
    """A small synthetic stand-in for Task 1's cleaned dataset.

    Readings are grouped by machine ``type`` (L/M/H) and sorted by
    ``tool_wear_min`` within each type, the same stream ordering Task 3's
    ``load_data.build_feature_dataset`` uses, so rolling features computed
    over this data behave the same way they would over the real dataset.

    The label carries a deliberate torque/tool-wear interaction: sustained
    high torque late in a component's tool life is FleetPulse's overstrain
    failure mode (AI4I 2020's OSF), the real sensor relationship this
    project's data models. A 3% random label flip keeps the two classes
    from being perfectly separable, closer to how the real dataset behaves
    and enough to make calibration/CV meaningful for the training smoke
    test without needing hundreds of rows.
    """
    rng = np.random.default_rng(seed)
    rows = []
    for machine_type in ["L", "M", "H"]:
        tool_wear = np.sort(rng.uniform(0, 250, n_per_type))
        torque = rng.uniform(15, 65, n_per_type)
        air_temp = rng.normal(300, 2, n_per_type)
        process_temp = air_temp + rng.normal(10, 1, n_per_type)
        rpm = rng.normal(1500, 150, n_per_type)
        overstrain = (torque > 50) & (tool_wear > 150)
        noise_flip = rng.random(n_per_type) < 0.03
        failure = np.logical_xor(overstrain, noise_flip).astype(int)
        for i in range(n_per_type):
            rows.append(
                {
                    "type": machine_type,
                    "air_temperature_k": float(air_temp[i]),
                    "process_temperature_k": float(process_temp[i]),
                    "rotational_speed_rpm": float(rpm[i]),
                    "torque_nm": float(torque[i]),
                    "tool_wear_min": float(tool_wear[i]),
                    "machine_failure": int(failure[i]),
                }
            )
    df = pd.DataFrame(rows)
    return df.sort_values(["type", "tool_wear_min"], kind="stable").reset_index(drop=True)


@pytest.fixture(scope="session")
def synthetic_dataset() -> pd.DataFrame:
    return make_synthetic_fleet_data()


@pytest.fixture(scope="session")
def fixture_model(synthetic_dataset):
    """A small pipeline trained the same shape as Task 3's registered model.

    Raw sensors + type + rolling-window features (Task 3's
    ``features.FeatureComputer``), fit with Task 3's configurable
    ``build_pipeline``. Fast on purpose (no calibration, no CV), this
    exists to exercise the real feature/serving code paths in tests, not
    to be an accurate model.
    """
    features = import_task_module("task-3", "features")
    pipeline_mod = import_task_module("task-3", "pipeline")

    ordered_readings = [
        (row["type"], {c: row[c] for c in SENSOR_COLUMNS})
        for _, row in synthetic_dataset.iterrows()
    ]
    feature_rows = features.compute_ordered_features(ordered_readings)
    feature_df = pd.DataFrame(feature_rows, index=synthetic_dataset.index)
    rolling_columns = features.FeatureComputer().feature_names()

    X = pd.concat(
        [synthetic_dataset[SENSOR_COLUMNS + ["type"]], feature_df], axis=1
    )
    y = synthetic_dataset["machine_failure"]

    model = pipeline_mod.build_pipeline(
        numeric_features=SENSOR_COLUMNS + rolling_columns,
        categorical_features=["type"],
        n_estimators=100,
    )
    model.fit(X, y)
    return model
