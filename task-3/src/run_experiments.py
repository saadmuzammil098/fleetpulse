"""Train 12 FleetPulse run variants and log every one to MLflow.

Run as `python -m src.run_experiments` from task-3/. Splits are computed
once, on the base ordered dataset, and reused unchanged across every
config so every run is compared on the exact same train/validation/test
rows — the only things varying run to run are the hyperparameters,
scaler, calibration method, and rolling-feature choices named in each
config below.

The configs are a deliberately mixed bag, not a blind grid search: a
no-rolling-features baseline (to prove the rolling features earn their
keep), an unweighted-class run (to make the recall cost concrete), an
isotonic-calibration run (Task 2 argued sigmoid should win here on this
little data — this run re-checks that claim instead of assuming it), and
a couple of window-size variants (a genuine preprocessing choice, not a
hyperparameter).
"""
from __future__ import annotations

import json

import mlflow
from sklearn.calibration import CalibratedClassifierCV
from sklearn.model_selection import train_test_split

from . import config
from .features import DEFAULT_WINDOWS, SENSOR_COLUMNS
from .load_data import CATEGORICAL_FEATURES, build_feature_dataset, rolling_feature_columns
from .metrics import compute_metrics, find_best_threshold_by_f2
from .pipeline import RANDOM_STATE, build_pipeline

RUN_CONFIGS = [
    dict(name="baseline_no_rolling", n_estimators=300, max_depth=8, min_samples_leaf=3,
         class_weight="balanced", scaler="standard", calibration="sigmoid",
         use_rolling=False, windows=DEFAULT_WINDOWS),
    dict(name="baseline_rolling", n_estimators=300, max_depth=8, min_samples_leaf=3,
         class_weight="balanced", scaler="standard", calibration="sigmoid",
         use_rolling=True, windows=DEFAULT_WINDOWS),
    dict(name="small_forest", n_estimators=150, max_depth=6, min_samples_leaf=5,
         class_weight="balanced", scaler="standard", calibration="sigmoid",
         use_rolling=True, windows=DEFAULT_WINDOWS),
    dict(name="large_forest", n_estimators=500, max_depth=12, min_samples_leaf=1,
         class_weight="balanced", scaler="standard", calibration="sigmoid",
         use_rolling=True, windows=DEFAULT_WINDOWS),
    dict(name="deep_unbounded", n_estimators=300, max_depth=None, min_samples_leaf=1,
         class_weight="balanced", scaler="standard", calibration="sigmoid",
         use_rolling=True, windows=DEFAULT_WINDOWS),
    dict(name="balanced_subsample", n_estimators=300, max_depth=8, min_samples_leaf=3,
         class_weight="balanced_subsample", scaler="standard", calibration="sigmoid",
         use_rolling=True, windows=DEFAULT_WINDOWS),
    dict(name="no_class_weight", n_estimators=300, max_depth=8, min_samples_leaf=3,
         class_weight=None, scaler="standard", calibration="sigmoid",
         use_rolling=True, windows=DEFAULT_WINDOWS),
    dict(name="minmax_scaler", n_estimators=300, max_depth=8, min_samples_leaf=3,
         class_weight="balanced", scaler="minmax", calibration="sigmoid",
         use_rolling=True, windows=DEFAULT_WINDOWS),
    dict(name="isotonic_calibration", n_estimators=300, max_depth=8, min_samples_leaf=3,
         class_weight="balanced", scaler="standard", calibration="isotonic",
         use_rolling=True, windows=DEFAULT_WINDOWS),
    dict(name="conservative_leaves", n_estimators=300, max_depth=6, min_samples_leaf=5,
         class_weight="balanced", scaler="standard", calibration="sigmoid",
         use_rolling=True, windows=DEFAULT_WINDOWS),
    dict(name="wide_windows", n_estimators=300, max_depth=8, min_samples_leaf=3,
         class_weight="balanced", scaler="standard", calibration="sigmoid",
         use_rolling=True, windows=(5, 10)),
    dict(name="narrow_windows", n_estimators=300, max_depth=8, min_samples_leaf=3,
         class_weight="balanced", scaler="standard", calibration="sigmoid",
         use_rolling=True, windows=(2, 3)),
]

assert len(RUN_CONFIGS) >= 10, "spec requires 10+ tracked runs"
assert len({c["name"] for c in RUN_CONFIGS}) == len(RUN_CONFIGS), "run names must be unique"


def _feature_datasets_by_windows(windows_needed: set[tuple[int, ...]]):
    return {w: build_feature_dataset(windows=w) for w in windows_needed}


def _split_indices(y, random_state=RANDOM_STATE):
    idx = y.index
    train_full_idx, test_idx = train_test_split(
        idx, test_size=0.20, stratify=y, random_state=random_state
    )
    y_train_full = y.loc[train_full_idx]
    train_idx, val_idx = train_test_split(
        train_full_idx, test_size=0.25, stratify=y_train_full, random_state=random_state
    )
    return train_idx, val_idx, test_idx


def run_one(cfg: dict, datasets_by_windows: dict, split_ref_index) -> dict:
    windows = tuple(cfg["windows"])
    X, y, _ordered = datasets_by_windows[windows]

    train_idx, val_idx, test_idx = _split_indices(y.loc[split_ref_index])
    numeric_features = list(SENSOR_COLUMNS)
    if cfg["use_rolling"]:
        numeric_features += rolling_feature_columns(windows=windows)

    X_train, y_train = X.loc[train_idx], y.loc[train_idx]
    X_val, y_val = X.loc[val_idx], y.loc[val_idx]
    X_test, y_test = X.loc[test_idx], y.loc[test_idx]

    base_pipeline = build_pipeline(
        numeric_features=numeric_features,
        categorical_features=CATEGORICAL_FEATURES,
        scaler=cfg["scaler"],
        n_estimators=cfg["n_estimators"],
        max_depth=cfg["max_depth"],
        min_samples_leaf=cfg["min_samples_leaf"],
        class_weight=cfg["class_weight"],
    )
    calibrated_model = CalibratedClassifierCV(
        base_pipeline, method=cfg["calibration"], cv=5
    )
    calibrated_model.fit(X_train, y_train)

    val_proba = calibrated_model.predict_proba(X_val)[:, 1]
    best_threshold, best_f2 = find_best_threshold_by_f2(y_val, val_proba)
    val_metrics = compute_metrics(y_val, val_proba, best_threshold)

    test_proba = calibrated_model.predict_proba(X_test)[:, 1]
    test_metrics = compute_metrics(y_test, test_proba, best_threshold)

    params = {
        "n_estimators": cfg["n_estimators"],
        "max_depth": cfg["max_depth"],
        "min_samples_leaf": cfg["min_samples_leaf"],
        "class_weight": cfg["class_weight"],
        "scaler": cfg["scaler"],
        "calibration": cfg["calibration"],
        "use_rolling_features": cfg["use_rolling"],
        "rolling_windows": str(windows),
        "n_numeric_features": len(numeric_features),
        "threshold": best_threshold,
    }
    metrics_out = {
        "val_pr_auc": val_metrics["pr_auc"],
        "val_f2_score": val_metrics["f2_score"],
        "val_recall": val_metrics["recall"],
        "val_precision": val_metrics["precision"],
        "val_expected_cost_per_1000": val_metrics["expected_cost_per_1000"],
        "test_pr_auc": test_metrics["pr_auc"],
        "test_roc_auc": test_metrics["roc_auc"],
        "test_f2_score": test_metrics["f2_score"],
        "test_recall": test_metrics["recall"],
        "test_precision": test_metrics["precision"],
        "test_brier_score": test_metrics["brier_score"],
        "test_expected_cost_per_1000": test_metrics["expected_cost_per_1000"],
    }

    with mlflow.start_run(run_name=cfg["name"]) as run:
        mlflow.log_params(params)
        mlflow.log_metrics(metrics_out)
        mlflow.log_dict(val_metrics["confusion_matrix"], "val_confusion_matrix.json")
        mlflow.log_dict(test_metrics["confusion_matrix"], "test_confusion_matrix.json")
        mlflow.log_dict(cfg, "run_config.json")
        mlflow.sklearn.log_model(
            calibrated_model,
            name="model",
            input_example=X_train.head(3),
            serialization_format=mlflow.sklearn.SERIALIZATION_FORMAT_PICKLE,
        )
        run_id = run.info.run_id

    print(
        f"[{cfg['name']:>22}] val_cost/1000={val_metrics['expected_cost_per_1000']:.1f} "
        f"val_pr_auc={val_metrics['pr_auc']:.3f} test_pr_auc={test_metrics['pr_auc']:.3f} "
        f"run_id={run_id}"
    )
    return {"config": cfg, "run_id": run_id, "val_metrics": val_metrics, "test_metrics": test_metrics}


def render_leaderboard(results: list[dict]) -> str:
    rows = sorted(results, key=lambda r: r["val_metrics"]["expected_cost_per_1000"])
    lines = [
        "# Task 3 Run Leaderboard",
        "",
        f"{len(results)} runs tracked in MLflow (experiment "
        f"`{config.EXPERIMENT_NAME}`), ranked by validation-set "
        "**expected cost per 1000 units** (lower is better; FN costs "
        f"{5.0:.0f}x FP, see `src/metrics.py`), not by raw PR-AUC.",
        "",
        "| rank | run | rolling feats | val cost/1000 | val PR-AUC | val recall | val precision | test PR-AUC | test recall |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(rows, start=1):
        cfg = r["config"]
        vm, tm = r["val_metrics"], r["test_metrics"]
        lines.append(
            f"| {i} | {cfg['name']} | {cfg['use_rolling']} | "
            f"{vm['expected_cost_per_1000']:.1f} | {vm['pr_auc']:.3f} | "
            f"{vm['recall']:.3f} | {vm['precision']:.3f} | "
            f"{tm['pr_auc']:.3f} | {tm['recall']:.3f} |"
        )
    lines.append("")
    return "\n".join(lines)


def _ensure_experiment() -> None:
    client = mlflow.MlflowClient()
    existing = client.get_experiment_by_name(config.EXPERIMENT_NAME)
    if existing is None:
        client.create_experiment(
            config.EXPERIMENT_NAME, artifact_location=config.MLFLOW_ARTIFACT_ROOT
        )
    mlflow.set_experiment(config.EXPERIMENT_NAME)


def main() -> list[dict]:
    mlflow.set_tracking_uri(config.MLFLOW_TRACKING_URI)
    _ensure_experiment()

    windows_needed = {tuple(c["windows"]) for c in RUN_CONFIGS}
    datasets_by_windows = _feature_datasets_by_windows(windows_needed)
    split_ref_index = next(iter(datasets_by_windows.values()))[1].index

    results = [run_one(cfg, datasets_by_windows, split_ref_index) for cfg in RUN_CONFIGS]

    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    config.LEADERBOARD_PATH.write_text(render_leaderboard(results))
    config.RUN_IDS_PATH.write_text(
        json.dumps([{"name": r["config"]["name"], "run_id": r["run_id"]} for r in results], indent=2)
        + "\n"
    )
    print(f"Wrote {config.LEADERBOARD_PATH} and {config.RUN_IDS_PATH}")
    return results


if __name__ == "__main__":
    main()
