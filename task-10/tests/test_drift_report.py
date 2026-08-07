"""Tests for the Evidently comparison at the heart of ``drift_report.py``.

Deliberately does not touch ``load_reference``/``load_current_from_cloudwatch``
(those need Task 1's real dataset, Task 9's baked model, and a live
CloudWatch Logs group via Floci — none of which a hosted CI runner can
reach, the same constraint ``task-8/README.md`` already documents). What
*is* testable without any of that is the actual drift math:
``compute_drift`` takes two dataframes and returns a score, in isolation
from where those dataframes came from. These two cases mirror the two
real runs in ``task-10/README.md``'s break-it exercise almost exactly —
an un-shifted current window scoring low, a temperature-shifted one
scoring high enough to cross the alert threshold.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from .conftest import import_task_module


@pytest.fixture(scope="module")
def drift_report():
    return import_task_module("task-10", "drift_report")


def _make_frame(drift_columns: list[str], n: int, seed: int, shift: dict | None = None) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    shift = shift or {}
    df = pd.DataFrame(
        {
            "air_temperature_k": rng.normal(300 + shift.get("air_temperature_k", 0), 2, n),
            "process_temperature_k": rng.normal(310 + shift.get("process_temperature_k", 0), 1.5, n),
            "rotational_speed_rpm": rng.normal(1500 + shift.get("rotational_speed_rpm", 0), 180, n),
            "torque_nm": rng.normal(40, 10, n),
            "tool_wear_min": rng.uniform(0, 250, n),
            "type": rng.choice(["L", "M", "H"], n),
            "prediction": rng.uniform(0, 1, n),
        }
    )
    return df[drift_columns]


def test_no_shift_scores_below_alert_threshold(drift_report):
    reference = _make_frame(drift_report.DRIFT_COLUMNS, n=2000, seed=1)
    current = _make_frame(drift_report.DRIFT_COLUMNS, n=300, seed=2)

    _, summary = drift_report.compute_drift(reference, current)

    # Same 0.3 threshold task-10/README.md's real baseline run (0.286)
    # landed just under — un-shifted traffic from the same distribution
    # should not trip the alert.
    assert summary["drift_score"] < 0.3


def test_temperature_shift_scores_above_alert_threshold(drift_report):
    reference = _make_frame(drift_report.DRIFT_COLUMNS, n=2000, seed=1)
    current = _make_frame(
        drift_report.DRIFT_COLUMNS,
        n=500,
        seed=3,
        # Three columns shifted, not two: drift_score is a discrete
        # count/total (see DriftedColumnsCount in compute_drift), so with
        # 7 total columns, 2 flagged columns caps out at 2/7≈0.286, just
        # under the 0.3 alert threshold no matter how large that shift
        # is — this synthetic data's columns are independent, unlike the
        # real dataset's, where a temperature shift also moved
        # `prediction` (see task-10/README.md's real run). Shifting three
        # independent columns here reproduces "enough columns flagged to
        # cross the threshold" without relying on inter-column
        # correlation the real model provides for free.
        shift={"air_temperature_k": -20, "process_temperature_k": -18, "rotational_speed_rpm": 400},
    )

    _, summary = drift_report.compute_drift(reference, current)

    # Mirrors the real winter-shift exercise (drift_score 0.571): enough
    # shifted columns to cross the same 0.3 alert threshold, and the
    # shifted columns themselves clearly past ValueDrift's own 0.1
    # per-column threshold.
    assert summary["drift_score"] >= 0.3
    assert summary["per_column_drift"]["air_temperature_k"] > 0.1
    assert summary["per_column_drift"]["process_temperature_k"] > 0.1
