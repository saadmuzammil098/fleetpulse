"""Training smoke test: a fast, reduced-scope run of Task 2's training
pipeline that proves it executes end to end without erroring.

This is not a retrain and makes no accuracy claim, the synthetic data
here is small and does not represent the real fleet distribution. Its job
is narrower: catch a training pipeline that has been broken outright (an
import error, a shape mismatch, a step that now raises), the kind of
regression that a metrics-focused review can miss if nobody happens to
run the real, slow training job on that PR.
"""
from __future__ import annotations

import numpy as np
import pytest
from sklearn.calibration import CalibratedClassifierCV
from sklearn.model_selection import StratifiedKFold, cross_val_score, train_test_split

from .conftest import import_task_module, make_synthetic_fleet_data


@pytest.fixture
def task2_modules():
    pipeline = import_task_module("task-2", "pipeline")
    evaluate = import_task_module("task-2", "evaluate")
    return pipeline, evaluate


def test_training_pipeline_runs_end_to_end(task2_modules):
    pipeline_mod, evaluate_mod = task2_modules
    df = make_synthetic_fleet_data(n_per_type=60)

    numeric_features = [
        "air_temperature_k",
        "process_temperature_k",
        "rotational_speed_rpm",
        "torque_nm",
        "tool_wear_min",
    ]
    X = df[numeric_features + ["type"]]
    y = df["machine_failure"].astype(int)

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.25, stratify=y, random_state=pipeline_mod.RANDOM_STATE
    )

    # Reduced-scope CV: 3 folds instead of Task 2's 5, same idea, far
    # fewer fits.
    cv = StratifiedKFold(n_splits=3, shuffle=True, random_state=pipeline_mod.RANDOM_STATE)
    cv_scores = cross_val_score(
        pipeline_mod.build_pipeline(), X_train, y_train, cv=cv, scoring="average_precision"
    )
    assert len(cv_scores) == 3
    assert np.all(np.isfinite(cv_scores))

    # Reduced-scope calibration: cv=3 instead of Task 2's cv=5.
    calibrated_model = CalibratedClassifierCV(
        pipeline_mod.build_pipeline(), method="sigmoid", cv=3
    )
    calibrated_model.fit(X_train, y_train)

    test_proba = calibrated_model.predict_proba(X_test)[:, 1]
    assert test_proba.shape == (len(X_test),)
    assert np.all((test_proba >= 0.0) & (test_proba <= 1.0))

    best_threshold, best_f2 = evaluate_mod.find_best_threshold_by_f2(y_test, test_proba)
    assert 0.0 <= best_threshold <= 1.0
    assert 0.0 <= best_f2 <= 1.0

    metrics = evaluate_mod.compute_metrics(y_test, test_proba, best_threshold)
    assert "pr_auc" in metrics
    assert "recall" in metrics
    assert "precision" in metrics
