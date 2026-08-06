"""Exports a self-contained model artifact for Task 9's Lambda deployment.

Task 3's MLflow registry works well for local/Kubernetes serving (Tasks 4,
6, 7), but its local file-based artifact store bakes an *absolute host
path* into ``mlflow.db`` at logging time (see ``task-5/README.md``'s "why
the model isn't baked into the image"). That's fine for a bind-mounted
container; it is fundamentally incompatible with a Lambda container
image, which has no bind mounts and must be a fully self-contained,
portable artifact. Rather than stand up a remote MLflow artifact store (a
real Task 3 concern, and explicitly out of that task's stated scope), Task
9 bakes a plain ``joblib`` model file into the Lambda image and never
touches the MLflow registry at all in that code path (see
``task-4/src/model_registry.py``'s ``MODEL_SOURCE=artifact`` branch).

Two data sources, in order of preference, so this script produces a real
trained model either way:

1. Task 1's DVC-tracked cleaned dataset (``task-1/data/processed/
   cleaned.csv``), if present, i.e. ``dvc pull`` has been run locally.
   This is what a real local deploy should ship.
2. Otherwise (a fresh CI checkout, GitHub-hosted runners cannot reach the
   local Floci S3 endpoint Task 1's DVC remote points at, see
   ``task-8/README.md``), a small synthetic dataset with the same
   deliberate torque/tool-wear interaction Day 8's test suite uses. This
   keeps CI honest and functional rather than silently skipping the
   model-build step; which source was used is printed and stamped into
   ``model_version.txt``.

Either way, the model is trained through Task 3's *real* feature pipeline
(``FeatureComputer`` rolling-window features, ``build_pipeline``) so the
exported artifact has the same shape the registry-served model has, run
as ``python -m scripts.export_model`` from ``task-9/``.
"""
from __future__ import annotations

import importlib
import sys
from datetime import UTC, datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
TASK9_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = TASK9_ROOT / "model_artifact"
CLEANED_CSV = REPO_ROOT / "task-1" / "data" / "processed" / "cleaned.csv"

SENSOR_COLUMNS = [
    "air_temperature_k",
    "process_temperature_k",
    "rotational_speed_rpm",
    "torque_nm",
    "tool_wear_min",
]


def _import_task_module(task_dir: str, module_name: str):
    """Import ``src.<module_name>`` from ``task_dir`` in isolation.

    Every task folder in this repo names its own package ``src`` (see
    ``task-2/src/load_data.py``'s docstring); this is the same
    import-by-path trick ``task-4/src/shared_features.py`` and
    ``task-8/tests/conftest.py`` already use to avoid the collision.
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


def _make_synthetic_fleet_data(n_per_type: int = 300, seed: int = 42) -> pd.DataFrame:
    """CI-only fallback. See ``task-8/tests/conftest.py::make_synthetic_fleet_data``
    for the canonical version this mirrors (duplicated here so Task 9's
    deploy tooling doesn't depend on Task 8's test package)."""
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
    return pd.DataFrame(rows)


def _build_training_frame() -> tuple[pd.DataFrame, str]:
    if CLEANED_CSV.exists():
        df = pd.read_csv(CLEANED_CSV)[SENSOR_COLUMNS + ["type", "machine_failure"]].copy()
        source = f"task-1 cleaned dataset ({CLEANED_CSV}, dvc-pulled)"
    else:
        df = _make_synthetic_fleet_data()
        source = "synthetic fallback (task-1/data/processed/cleaned.csv not found, no dvc pull)"
    df = df.sort_values(["type", "tool_wear_min"], kind="stable").reset_index(drop=True)
    return df, source


def main() -> None:
    df, source = _build_training_frame()
    print(f"Training data source: {source}")
    print(f"Rows: {len(df)}, positive rate: {df['machine_failure'].mean():.3%}")

    features = _import_task_module("task-3", "features")
    pipeline_mod = _import_task_module("task-3", "pipeline")

    ordered_readings = [
        (row["type"], {c: row[c] for c in SENSOR_COLUMNS}) for _, row in df.iterrows()
    ]
    feature_rows = features.compute_ordered_features(ordered_readings)
    feature_df = pd.DataFrame(feature_rows, index=df.index)
    rolling_columns = features.FeatureComputer().feature_names()

    X = pd.concat([df[SENSOR_COLUMNS + ["type"]], feature_df], axis=1)
    y = df["machine_failure"].astype(int)

    model = pipeline_mod.build_pipeline(
        numeric_features=SENSOR_COLUMNS + rolling_columns,
        categorical_features=["type"],
        n_estimators=300,
        max_depth=8,
        min_samples_leaf=3,
        class_weight="balanced",
    )
    model.fit(X, y)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    model_path = OUTPUT_DIR / "model.joblib"
    joblib.dump(model, model_path)

    version = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{len(df)}rows"
    (OUTPUT_DIR / "model_version.txt").write_text(version + "\n")
    (OUTPUT_DIR / "model_source.txt").write_text(source + "\n")

    print(f"Wrote {model_path} (version {version})")


if __name__ == "__main__":
    main()
