"""Orchestrates Day 2: load -> split -> CV -> calibrate -> evaluate -> save.

Run as `python -m src.train` from day-02/. This is the "one command" the
Day 2 done-when clause asks for.
"""
from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import brier_score_loss
from sklearn.model_selection import StratifiedKFold, cross_val_score, train_test_split

from .evaluate import compute_metrics, find_best_threshold_by_f2, render_evaluation_report
from .load_data import build_dataset
from .pipeline import RANDOM_STATE, build_pipeline

DAY02_ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = DAY02_ROOT / "models" / "model.joblib"
METRICS_PATH = DAY02_ROOT / "reports" / "metrics.json"
REPORT_PATH = DAY02_ROOT / "reports" / "evaluation_report.md"


def main() -> None:
    X, y = build_dataset()

    # 60 / 20 / 20 train / validation / test, stratified because the
    # positive class is only ~3.4% of rows, an unstratified split can
    # easily starve one split of positives entirely (see src/break_it.py).
    X_train_full, X_test, y_train_full, y_test = train_test_split(
        X, y, test_size=0.20, stratify=y, random_state=RANDOM_STATE
    )
    X_train, X_val, y_train, y_val = train_test_split(
        X_train_full,
        y_train_full,
        test_size=0.25,  # 0.25 of the remaining 80% = 20% of the total
        stratify=y_train_full,
        random_state=RANDOM_STATE,
    )

    print(
        f"Split sizes: train={len(X_train)} (pos={y_train.sum()}), "
        f"val={len(X_val)} (pos={y_val.sum()}), "
        f"test={len(X_test)} (pos={y_test.sum()})"
    )

    # Cross-validation on the training set only, model selection / sanity
    # check happens before the model ever sees validation or test data.
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=RANDOM_STATE)
    cv_pr_auc_scores = cross_val_score(
        build_pipeline(), X_train, y_train, cv=cv, scoring="average_precision"
    )
    print(f"CV PR-AUC: {cv_pr_auc_scores.round(3)} (mean {cv_pr_auc_scores.mean():.3f})")

    # Uncalibrated pipeline, fit once on train, used only to quantify how
    # much calibration actually changes the output probabilities.
    uncalibrated = build_pipeline()
    uncalibrated.fit(X_train, y_train)
    uncalibrated_test_proba = uncalibrated.predict_proba(X_test)[:, 1]
    uncalibrated_brier = brier_score_loss(y_test, uncalibrated_test_proba)

    # Calibration: sigmoid (Platt scaling), not isotonic. Isotonic is more
    # flexible but needs more data per fold to avoid overfitting the
    # calibration map itself; with only ~200 positive examples in the
    # training set, sigmoid's 2-parameter fit is the safer choice. cv=5
    # internally cross-fits so the base classifier is never calibrated on
    # the same rows it was trained on.
    calibrated_model = CalibratedClassifierCV(build_pipeline(), method="sigmoid", cv=5)
    calibrated_model.fit(X_train, y_train)

    # Pick the decision threshold on validation (never on test), optimizing
    # F2 to reflect that a missed failure costs far more than a false alarm.
    val_proba = calibrated_model.predict_proba(X_val)[:, 1]
    best_threshold, best_f2 = find_best_threshold_by_f2(y_val, val_proba)
    print(f"Chosen threshold: {best_threshold:.2f} (val F2={best_f2:.3f})")

    # Final, one-shot evaluation on the untouched test set.
    test_proba = calibrated_model.predict_proba(X_test)[:, 1]
    test_metrics = compute_metrics(y_test, test_proba, best_threshold)
    calibrated_brier = test_metrics["brier_score"]
    print(f"Test PR-AUC: {test_metrics['pr_auc']:.3f}, recall: {test_metrics['recall']:.3f}, "
          f"precision: {test_metrics['precision']:.3f}")

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": calibrated_model, "threshold": best_threshold}, MODEL_PATH)

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    metrics_out = {
        "cv_pr_auc_scores": [float(s) for s in cv_pr_auc_scores],
        "cv_pr_auc_mean": float(np.mean(cv_pr_auc_scores)),
        "validation_threshold_search": {
            "best_threshold": best_threshold,
            "best_f2": best_f2,
        },
        "calibration_brier_scores": {
            "uncalibrated": float(uncalibrated_brier),
            "calibrated": float(calibrated_brier),
        },
        "test_metrics": test_metrics,
        "split_sizes": {
            "train": len(X_train),
            "validation": len(X_val),
            "test": len(X_test),
        },
    }
    METRICS_PATH.write_text(json.dumps(metrics_out, indent=2) + "\n")

    report_md = render_evaluation_report(
        cv_pr_auc_scores=list(cv_pr_auc_scores),
        val_threshold_search=(best_threshold, best_f2),
        calibration_comparison={
            "uncalibrated_brier": uncalibrated_brier,
            "calibrated_brier": calibrated_brier,
        },
        test_metrics=test_metrics,
    )
    REPORT_PATH.write_text(report_md)
    print(f"Wrote {MODEL_PATH}, {METRICS_PATH}, {REPORT_PATH}")


if __name__ == "__main__":
    main()
