"""Deliberately break the Task 2 pipeline three ways and record what happens.

Run as `python -m src.break_it` from task-2/. Writes reports/break_it_demo.md.
Doesn't touch models/model.joblib or the real metrics/report, this is a
side experiment, not part of the reproducible `dvc repro` train stage.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score
from sklearn.model_selection import StratifiedKFold, train_test_split

from .evaluate import compute_metrics
from .load_data import LEAKY_FAILURE_MODE_COLUMNS, build_dataset
from .pipeline import RANDOM_STATE, build_pipeline

REPORT_PATH = Path(__file__).resolve().parents[1] / "reports" / "break_it_demo.md"


def demo_label_leak() -> str:
    """Train with the failure-mode flags included as features.

    twf/hdf/pwf/osf/rnf record *which* failure occurred, machine_failure is
    literally their logical OR. A model trained on them isn't predicting
    failure, it's reading the answer off a restated copy of itself.
    """
    X, y = build_dataset()
    leaked_features = pd.read_csv(
        Path(__file__).resolve().parents[2] / "task-1" / "data" / "processed" / "cleaned.csv"
    )[LEAKY_FAILURE_MODE_COLUMNS]
    X_leaked = pd.concat([X.reset_index(drop=True), leaked_features.reset_index(drop=True)], axis=1)

    from sklearn.compose import ColumnTransformer
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder, StandardScaler
    from .load_data import CATEGORICAL_FEATURES, NUMERIC_FEATURES

    preprocess = ColumnTransformer(
        [
            ("numeric", StandardScaler(), NUMERIC_FEATURES + LEAKY_FAILURE_MODE_COLUMNS),
            ("categorical", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL_FEATURES),
        ]
    )
    leaky_pipeline = Pipeline(
        [
            ("preprocess", preprocess),
            (
                "classifier",
                RandomForestClassifier(
                    n_estimators=300, max_depth=8, min_samples_leaf=3,
                    class_weight="balanced", random_state=RANDOM_STATE, n_jobs=-1,
                ),
            ),
        ]
    )

    X_train, X_test, y_train, y_test = train_test_split(
        X_leaked, y, test_size=0.2, stratify=y, random_state=RANDOM_STATE
    )
    leaky_pipeline.fit(X_train, y_train)
    leaky_proba = leaky_pipeline.predict_proba(X_test)[:, 1]
    leaky_pr_auc = average_precision_score(y_test, leaky_proba)

    # For comparison, the honest (leak-free) pipeline on the same split.
    clean_pipeline = build_pipeline()
    clean_pipeline.fit(X_train[NUMERIC_FEATURES + CATEGORICAL_FEATURES], y_train)
    clean_proba = clean_pipeline.predict_proba(X_test[NUMERIC_FEATURES + CATEGORICAL_FEATURES])[:, 1]
    clean_pr_auc = average_precision_score(y_test, clean_proba)

    return (
        "## 1. Label leak: training on the failure-mode flags\n\n"
        f"- PR-AUC **with** `{', '.join(LEAKY_FAILURE_MODE_COLUMNS)}` included as features: "
        f"**{leaky_pr_auc:.3f}**\n"
        f"- PR-AUC on the same split with the real, leak-free feature set: "
        f"**{clean_pr_auc:.3f}**\n\n"
        "**What happened:** `machine_failure` is defined as the logical OR of "
        "`twf`, `hdf`, `pwf`, `osf`, `rnf`, they're recorded *because* a "
        "failure happened, not observable before one. Handing them to the "
        "model as features lets it near-perfectly reconstruct the label from "
        "itself, PR-AUC jumps because the model isn't predicting anything, "
        "it's decoding. This is exactly the silent-looking failure mode that "
        "makes leakage dangerous: nothing crashes, nothing warns you, the "
        "number just looks great and would fall apart the moment it met "
        "live telemetry where those flags don't exist yet. "
        "`src/load_data.py` names these columns explicitly as "
        "`LEAKY_FAILURE_MODE_COLUMNS` and documents why they're excluded, "
        "specifically so a future edit can't casually add them back in "
        "without seeing the warning.\n"
    )


def demo_degenerate_split() -> str:
    """Shrink the dataset until a stratified split can't keep a positive class member.

    The real pipeline uses ~10,000 rows with 339 positives, plenty for a
    60/20/20 stratified split. This demo asks: what actually breaks if a
    future engineer points this same code at a much smaller or much more
    imbalanced slice of fleet data (e.g. one vehicle model's early rollout)?
    """
    X, y = build_dataset()
    # Take a tiny slice: 40 rows total, keep however few positives fall in it.
    rng = np.random.RandomState(RANDOM_STATE)
    tiny_idx = rng.choice(len(X), size=40, replace=False)
    X_tiny, y_tiny = X.iloc[tiny_idx].reset_index(drop=True), y.iloc[tiny_idx].reset_index(drop=True)
    n_pos = int(y_tiny.sum())

    lines = [
        "## 2. Degenerate split: too few positives to stratify\n\n",
        f"- 40-row random slice of the dataset contains **{n_pos} positive "
        "examples** (vs. 339 in the full 10,000-row set).\n",
    ]

    try:
        cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=RANDOM_STATE)
        list(cv.split(X_tiny, y_tiny))  # force evaluation
        lines.append("- 5-fold StratifiedKFold on this slice: succeeded.\n")
    except ValueError as e:
        lines.append(f"- 5-fold StratifiedKFold on this slice: **raised `ValueError`**: `{e}`\n")

    try:
        X_train, X_test, y_train, y_test = train_test_split(
            X_tiny, y_tiny, test_size=0.5, stratify=y_tiny, random_state=RANDOM_STATE
        )
        pipe = build_pipeline()
        pipe.fit(X_train, y_train)
        proba = pipe.predict_proba(X_test)[:, 1]
        if y_test.nunique() < 2:
            lines.append(
                "- Test split ended up **single-class** (all one label). "
                "`average_precision_score` / `roc_auc_score` are undefined "
                "for a single-class y_true, scikit-learn returns `nan` "
                f"(actual value: {average_precision_score(y_test, proba) if y_test.nunique() > 1 else float('nan')}) "
                "rather than raising, which is worse: a silent NaN can slide "
                "into a metrics.json and look like a formatting bug instead "
                "of a broken evaluation.\n"
            )
        else:
            pr_auc = average_precision_score(y_test, proba)
            lines.append(f"- Stratified train/test split on this slice succeeded (PR-AUC={pr_auc:.3f}), "
                         "but with only a handful of positives the number is nearly meaningless, "
                         "one flipped prediction swings it by double-digit points.\n")
    except ValueError as e:
        lines.append(f"- `train_test_split(..., stratify=y_tiny)`: **raised `ValueError`**: `{e}`\n")

    lines.append(
        "\n**What this shows:** the real `src/train.py` split (10,000 rows, 339 "
        "positives, 60/20/20) has enough positives in every split and fold "
        "to stay stable. That margin is a property of *this* dataset's "
        "size, not something the code enforces. A smaller or more "
        "imbalanced slice can push `StratifiedKFold` into raising outright, "
        "or push an unlucky split into a single-class test set where PR-AUC "
        "silently becomes `nan`, no crash, no warning, just a number that "
        "looks like a real score and isn't. Anyone reusing this pipeline on "
        "a smaller fleet segment should check `y.value_counts()` before "
        "trusting the split.\n"
    )
    return "".join(lines)


def demo_bad_input() -> str:
    """Feed the trained pipeline a row with an unseen category and a NaN sensor value."""
    X, y = build_dataset()
    pipe = build_pipeline()
    pipe.fit(X, y)

    bad_row = X.iloc[[0]].copy()
    bad_row["type"] = "X"  # not in the training set's {L, M, H}
    bad_row["torque_nm"] = np.nan

    lines = ["## 3. Bad input: unseen category + missing sensor reading\n\n"]
    try:
        proba = pipe.predict_proba(bad_row)[:, 1]
        lines.append(
            f"- Predicting on a row with `type=\"X\"` (unseen at training time) and "
            f"`torque_nm=NaN`: **did not raise**, returned probability "
            f"{proba[0]:.3f}.\n\n"
            "**What happened:** `OneHotEncoder(handle_unknown=\"ignore\")` "
            "silently zeroes out all three type columns for an unseen "
            "category instead of erroring, and the downstream "
            "`RandomForestClassifier` happily predicts on a NaN torque "
            "reading (trees route NaNs down a default branch). Both are "
            "*silent* failure modes: an unseen unit type or a dropped "
            "sensor reading produces a confident-looking number instead of "
            "an error, which is worse for a fleet-ops tool than a hard "
            "crash. This is why Day 4's API layer (`Pydantic` schemas that "
            "reject physically impossible sensor readings) exists as a "
            "separate boundary check, this pipeline alone won't catch it.\n"
        )
    except Exception as e:
        lines.append(f"- Predicting on that row: **raised `{type(e).__name__}`**: `{e}`\n")
    return "".join(lines)


def main() -> None:
    sections = [demo_label_leak(), demo_degenerate_split(), demo_bad_input()]
    report = (
        "# Task 2 Break-It Demo\n\n"
        "Three deliberate failures run against the Task 2 training pipeline: "
        "a label leak, a degenerate split, and a malformed prediction input. "
        "None of these run as part of `dvc repro`, this is a one-off "
        "exploration documenting what breaks and why.\n\n"
        + "\n".join(sections)
    )
    REPORT_PATH.write_text(report)
    print(f"Wrote {REPORT_PATH}")


if __name__ == "__main__":
    main()
