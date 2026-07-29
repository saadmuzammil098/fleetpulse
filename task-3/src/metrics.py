"""Metrics for Task 3's experiment grid, extending Task 2's cost-asymmetry framing.

Task 2 encoded "a missed failure costs more than a false alarm" indirectly,
through F2 (recall weighted 4x precision) as the threshold-selection
metric. Task 3 makes that cost asymmetry an explicit, named number —
``expected_cost_per_1000`` — so run selection for the registry can be
justified by a metric that is literally denominated in the business
tradeoff (roadside breakdowns vs. service visits), not just "whichever
run has the highest PR-AUC."

FN_COST / FP_COST are an illustrative assumption, stated plainly here
rather than left implicit: a missed failure (roadside breakdown, possible
safety incident) is assumed to cost roughly 5x an unnecessary service
visit. Changing that ratio is a fleet-ops capacity conversation, not a
retraining decision — same spirit as Task 2's threshold being a single
number in one place.
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

FN_COST = 5.0
FP_COST = 1.0


def find_best_threshold_by_f2(y_true, y_proba, thresholds=None) -> tuple[float, float]:
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
    n = len(y_true)
    expected_cost_per_1000 = (FN_COST * fn + FP_COST * fp) / n * 1000
    return {
        "threshold": threshold,
        "pr_auc": float(average_precision_score(y_true, y_proba)),
        "roc_auc": float(roc_auc_score(y_true, y_proba)),
        "f2_score": float(fbeta_score(y_true, preds, beta=2, zero_division=0)),
        "recall": float(recall_score(y_true, preds, zero_division=0)),
        "precision": float(precision_score(y_true, preds, zero_division=0)),
        "brier_score": float(brier_score_loss(y_true, y_proba)),
        "expected_cost_per_1000": float(expected_cost_per_1000),
        "confusion_matrix": {
            "true_negative": int(tn),
            "false_positive": int(fp),
            "false_negative": int(fn),
            "true_positive": int(tp),
        },
        "n_samples": int(n),
        "n_positive": int(np.sum(y_true)),
    }
