"""Configurable sklearn Pipeline for the Task 3 experiment grid.

Same bundling rationale as Task 2: preprocessing and estimator live in one
Pipeline so cross-validation and calibration refit preprocessing inside
each fold, never once on the whole training set beforehand.
"""
from __future__ import annotations

from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import MinMaxScaler, OneHotEncoder, StandardScaler

RANDOM_STATE = 42

SCALERS = {
    "standard": StandardScaler,
    "minmax": MinMaxScaler,
}


def build_preprocessor(
    numeric_features: list[str],
    categorical_features: list[str],
    scaler: str = "standard",
) -> ColumnTransformer:
    return ColumnTransformer(
        transformers=[
            ("numeric", SCALERS[scaler](), numeric_features),
            (
                "categorical",
                OneHotEncoder(handle_unknown="ignore"),
                categorical_features,
            ),
        ]
    )


def build_pipeline(
    numeric_features: list[str],
    categorical_features: list[str],
    scaler: str = "standard",
    n_estimators: int = 300,
    max_depth: int | None = 8,
    min_samples_leaf: int = 3,
    class_weight: str | None = "balanced",
) -> Pipeline:
    return Pipeline(
        steps=[
            (
                "preprocess",
                build_preprocessor(numeric_features, categorical_features, scaler),
            ),
            (
                "classifier",
                RandomForestClassifier(
                    n_estimators=n_estimators,
                    max_depth=max_depth,
                    min_samples_leaf=min_samples_leaf,
                    class_weight=class_weight,
                    random_state=RANDOM_STATE,
                    n_jobs=-1,
                ),
            ),
        ]
    )
