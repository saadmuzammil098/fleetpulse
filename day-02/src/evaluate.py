"""Metrics and report rendering for the component-failure classifier.

Why not accuracy: 339 of 10,000 units failed (3.4%). A model that always
predicts "no failure" scores 96.6% accuracy while catching zero real
failures, useless for a fleet-ops decision. Every metric here is chosen to
survive that imbalance and to reflect the actual cost asymmetry: a missed
failure (false negative) risks a roadside breakdown or safety incident; a
false alarm (false positive) costs one unneeded service visit. See the
Day 2 README for the full justification.
"""
from __future__ import annotations

import numpy as np
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    fbeta_score,
    precision_score,
    recall_score,
    roc_auc_score,
)


def find_best_threshold_by_f2(y_true, y_proba, thresholds=None) -> tuple[float, float]:
    """Scan thresholds, return the one maximizing F2 (recall weighted 4x precision).

    Tuned on the validation set only, never on the held-out test set, so the
    final test-set numbers stay an honest, unbiased estimate.
    """
    if thresholds is None:
        thresholds = np.linspace(0.05, 0.95, 181)
    best_t, best_f2 = 0.5, -1.0
    for t in thresholds:
        preds = (y_proba >= t).astype(int)
        f2 = fbeta_score(y_true, preds, beta=2, zero_division=0)
        if f2 > best_f2:
            best_t, best_f2 = float(t), float(f2)
    return best_t, best_f2


def compute_metrics(y_true, y_proba, threshold: float) -> dict:
    preds = (y_proba >= threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(y_true, preds, labels=[0, 1]).ravel()
    return {
        "threshold": threshold,
        "pr_auc": float(average_precision_score(y_true, y_proba)),
        "roc_auc": float(roc_auc_score(y_true, y_proba)),
        "f2_score": float(fbeta_score(y_true, preds, beta=2, zero_division=0)),
        "recall": float(recall_score(y_true, preds, zero_division=0)),
        "precision": float(precision_score(y_true, preds, zero_division=0)),
        "brier_score": float(brier_score_loss(y_true, y_proba)),
        "confusion_matrix": {
            "true_negative": int(tn),
            "false_positive": int(fp),
            "false_negative": int(fn),
            "true_positive": int(tp),
        },
        "n_samples": int(len(y_true)),
        "n_positive": int(np.sum(y_true)),
    }


def render_evaluation_report(
    cv_pr_auc_scores: list[float],
    val_threshold_search: tuple[float, float],
    calibration_comparison: dict,
    test_metrics: dict,
) -> str:
    best_t, best_f2 = val_threshold_search
    cm = test_metrics["confusion_matrix"]
    fn, fp = cm["false_negative"], cm["false_positive"]
    tp, tn = cm["true_positive"], cm["true_negative"]
    n_pos = test_metrics["n_positive"]
    n_total = test_metrics["n_samples"]

    fn_rate = fn / n_pos if n_pos else float("nan")
    fp_rate = fp / (n_total - n_pos) if (n_total - n_pos) else float("nan")

    lines = [
        "# Day 2 Evaluation Report — Component Failure Prediction",
        "",
        "## Cross-validation (training set only, 5-fold stratified)",
        f"- PR-AUC per fold: {[round(float(s), 3) for s in cv_pr_auc_scores]}",
        f"- Mean PR-AUC: {np.mean(cv_pr_auc_scores):.3f} "
        f"(std: {np.std(cv_pr_auc_scores):.3f})",
        "",
        "## Threshold selection (validation set)",
        f"- Threshold maximizing F2 (recall weighted 4x precision): "
        f"**{best_t:.2f}**",
        f"- F2 at that threshold on validation: {best_f2:.3f}",
        "- This threshold, chosen on validation, is then applied unchanged to "
        "the test set below, it is never re-tuned on test data.",
        "",
        "## Calibration effect (Brier score, lower is better, held-out test set)",
        f"- Uncalibrated pipeline: {calibration_comparison['uncalibrated_brier']:.4f}",
        f"- Calibrated (sigmoid/Platt) pipeline: "
        f"{calibration_comparison['calibrated_brier']:.4f}",
        "",
        "## Final metrics on held-out test set (never touched until this run)",
        f"- PR-AUC: {test_metrics['pr_auc']:.3f}",
        f"- ROC-AUC: {test_metrics['roc_auc']:.3f}",
        f"- F2 score: {test_metrics['f2_score']:.3f}",
        f"- Recall (sensitivity): {test_metrics['recall']:.3f}",
        f"- Precision: {test_metrics['precision']:.3f}",
        f"- Brier score (calibrated): {test_metrics['brier_score']:.4f}",
        "",
        "## Confusion matrix (test set, n="
        f"{n_total}, {n_pos} actual failures)",
        "",
        "|                  | Predicted no-failure | Predicted failure |",
        "|------------------|----------------------|--------------------|",
        f"| **Actual no-failure** | {tn} (TN)             | {fp} (FP)          |",
        f"| **Actual failure**    | {fn} (FN)             | {tp} (TP)          |",
        "",
        "## What this means for the fleet, in plain language",
        "",
        f"- **False negative rate: {fn_rate:.1%}** ({fn} of {n_pos} real failures "
        "missed). Each of these is a component the model said was fine that "
        "actually failed, in production that's the roadside-breakdown or "
        "safety-incident case the whole project exists to prevent. This is "
        "the number to keep driving down even at the cost of more false alarms.",
        f"- **False positive rate: {fp_rate:.1%}** ({fp} of {n_total - n_pos} "
        "healthy units flagged). Each of these sends a vehicle in for an "
        "unnecessary service visit, real cost (technician time, vehicle "
        "downtime) but not a safety event. It's the acceptable side of the "
        "tradeoff this model is deliberately biased toward.",
        f"- At this operating point the model catches {tp} of {n_pos} real "
        f"failures ({test_metrics['recall']:.1%} recall) while {fp} of the "
        f"{tp + fp} flagged units turn out healthy on inspection "
        f"({test_metrics['precision']:.1%} precision). Whether that's the "
        "right point on the curve is a fleet-ops call about technician "
        "capacity, not a modeling call, the threshold is a single number "
        "in src/train.py and can be moved without retraining.",
    ]
    return "\n".join(lines) + "\n"
