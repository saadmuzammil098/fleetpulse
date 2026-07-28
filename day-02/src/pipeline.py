"""The sklearn Pipeline: preprocessing and estimator bundled as one object.

Bundling matters here specifically because of cross-validation: if scaling
or one-hot encoding were fit once on the whole training set before CV,
each fold's "held-out" data would already have influenced the
preprocessing statistics it's being scored against, a real leakage path,
not a hypothetical one. Wrapping both steps in one Pipeline means
`cross_val_score` / `cross_validate` refit preprocessing inside every fold
on that fold's training rows only.
"""
from __future__ import annotations

from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from .load_data import CATEGORICAL_FEATURES, NUMERIC_FEATURES

RANDOM_STATE = 42


def build_preprocessor() -> ColumnTransformer:
    return ColumnTransformer(
        transformers=[
            ("numeric", StandardScaler(), NUMERIC_FEATURES),
            (
                "categorical",
                OneHotEncoder(handle_unknown="ignore"),
                CATEGORICAL_FEATURES,
            ),
        ]
    )


def build_pipeline() -> Pipeline:
    """Uncalibrated model pipeline: preprocessing + classifier.

    RandomForestClassifier, not logistic regression, on purpose: it
    handles the sensor features' nonlinear interactions (e.g. high torque
    only matters combined with low rotational speed) without manual
    feature crosses. The tradeoff is that raw forest probabilities are
    known to be poorly calibrated (they cluster away from 0 and 1), which
    is exactly why Day 2's calibration step (src/train.py) isn't
    decorative, it measurably changes the output probabilities here in a
    way it wouldn't for an already well-calibrated model like logistic
    regression.
    """
    return Pipeline(
        steps=[
            ("preprocess", build_preprocessor()),
            (
                "classifier",
                RandomForestClassifier(
                    n_estimators=300,
                    max_depth=8,
                    min_samples_leaf=3,
                    class_weight="balanced",
                    random_state=RANDOM_STATE,
                    n_jobs=-1,
                ),
            ),
        ]
    )
