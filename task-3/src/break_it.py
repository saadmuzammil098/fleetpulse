"""Three deliberate failures against the shared feature module.

Run as `python -m src.break_it` from task-3/. Writes up what happened in
`reports/break_it_demo.md`. See the Task 3 README for the full writeup.
"""
from __future__ import annotations

from . import config
from .features import DEFAULT_WINDOWS, SENSOR_COLUMNS, FeatureComputer
from .load_data import STREAM_KEY_COLUMN, build_feature_dataset, rolling_feature_columns

REPORT_PATH = config.REPORTS_DIR / "break_it_demo.md"


def scenario_missing_field() -> str:
    """A raw reading shaped slightly differently than training expects."""
    computer = FeatureComputer()
    bad_reading = {
        "air_temperature_k": 298.5,
        "process_temperature_k": 309.0,
        "rotational_speed_rpm": 1450,
        # torque_nm is missing entirely, e.g. a sensor dropout or a field
        # rename upstream that this call site was never updated for.
        "tool_wear_min": 10,
    }
    try:
        computer.compute("M", bad_reading)
        outcome = "did NOT raise (unexpected)"
    except ValueError as e:
        outcome = f"raised ValueError: {e}"
    return (
        "### 1. Malformed live reading (missing sensor field)\n\n"
        f"Fed `FeatureComputer.compute` a reading missing `torque_nm` "
        "(a plausible sensor dropout, or an upstream field rename this "
        "call site never picked up). Result: **{outcome}**\n\n"
        "This is the training/serving-skew failure mode you *want*: loud "
        "and immediate, at the feature boundary, instead of a silently "
        "wrong prediction three steps downstream. `FeatureComputer` "
        "checks for every required sensor column before computing "
        "anything, precisely so a shape mismatch between what training "
        "assumed and what a live reading actually carries cannot pass "
        "through unnoticed.\n"
    ).format(outcome=outcome)


def scenario_cold_start_skew() -> str:
    """Cold-start inference vs. a fully warmed-up training window."""
    X, y, ordered = build_feature_dataset(windows=DEFAULT_WINDOWS)
    feature_cols = rolling_feature_columns(windows=DEFAULT_WINDOWS)

    type_rows = ordered[ordered[STREAM_KEY_COLUMN] == "L"]
    row_number = 4  # early enough that a 5-wide window isn't full yet
    prefix = type_rows.iloc[:row_number]
    target_index = prefix.index[-1]

    training_features = {col: float(X.loc[target_index, col]) for col in feature_cols}

    # Correct live replay: seeded with this stream's actual prior readings.
    seeded_computer = FeatureComputer(windows=DEFAULT_WINDOWS)
    for _, row in prefix.iterrows():
        seeded_features = seeded_computer.compute(
            "L", {c: row[c] for c in SENSOR_COLUMNS}
        )

    # Broken deployment: a live service that starts every session with an
    # empty buffer instead of replaying/loading this stream's history —
    # e.g. a serving pod restart, or a new pod picking up a stream
    # mid-flight without seeding its buffer from the vehicle's recent
    # readings. It only sees the *last* reading, cold.
    cold_computer = FeatureComputer(windows=DEFAULT_WINDOWS)
    last_row = prefix.iloc[-1]
    cold_features = cold_computer.compute("L", {c: last_row[c] for c in SENSOR_COLUMNS})

    diffs = []
    for col in feature_cols:
        tv, cv = training_features[col], float(cold_features[col])
        if abs(tv - cv) > 1e-9:
            diffs.append((col, tv, cv))

    example = diffs[0] if diffs else None
    lines = [
        "### 2. Cold-start buffer vs. a warmed-up training window\n",
        f"Same reading (`unit_id={ordered.loc[target_index, 'unit_id']}`, "
        "stream type L, 4th reading in its stream), scored two ways:\n",
        "- **Correct**: a `FeatureComputer` seeded with this stream's "
        "actual prior readings (what `mock_inference.py` does, and what "
        "training's replay does).",
        "- **Broken**: a `FeatureComputer` that starts with an empty "
        "buffer right at this reading — e.g. a serving pod that restarts "
        "or a stream handler that doesn't load recent history before "
        "serving traffic.\n",
        f"Of {len(feature_cols)} rolling features, **{len(diffs)} diverged** "
        "between the two.",
    ]
    if example:
        col, tv, cv = example
        lines.append(
            f"Example: `{col}` — training/correct-replay value **{tv:.3f}**, "
            f"cold-start value **{cv:.3f}**."
        )
    lines.append(
        "\nNothing crashes here — `FeatureComputer` happily returns a "
        "number either way, because a 1-reading window is a *valid* "
        "input to the same math, just a different one than training ever "
        "saw for this stream position. This is exactly the quiet kind of "
        "training/serving skew the roadmap spec warns about: the model "
        "sees real numbers, in range, that just don't mean what it was "
        "trained to expect. It's also why `mock_inference.py` keeps one "
        "long-lived `FeatureComputer` per process rather than building a "
        "new one per request.\n"
    )
    return "\n".join(lines)


def scenario_duplicate_implementation_drift() -> str:
    """What a second, independently-written rolling-mean would look like."""
    X, y, ordered = build_feature_dataset(windows=DEFAULT_WINDOWS)

    # ordered is sorted by [type, tool_wear_min]; alphabetically that's
    # H-block, then L-block, then M-block back to back in one dataframe.
    # Pick the 2nd reading of the L-block, right after the H/L boundary.
    l_rows = ordered[ordered[STREAM_KEY_COLUMN] == "L"]
    target_index = l_rows.index[1]
    target_pos = ordered.index.get_loc(target_index)

    shared_col = "torque_nm_rollmean_w5"
    shared_value = float(X.loc[target_index, shared_col])

    # A plausible, reasonable-looking *second* implementation someone
    # might write ad hoc in an inference service without importing
    # features.py: roll over the ordered dataframe directly, forgetting
    # that consecutive rows can belong to different streams (types).
    naive_rolling = ordered["torque_nm"].rolling(window=5, min_periods=1).mean()
    naive_value = float(naive_rolling.iloc[target_pos])

    diff = abs(shared_value - naive_value)
    return (
        "### 3. A second implementation, written with good intentions\n\n"
        "This scenario is not wired into the model or any call site — it "
        "exists only to show what Task 3's single-module rule is actually "
        "preventing. `pandas.Series.rolling(window=5)` applied straight "
        "to the ordered dataframe is the obvious, reasonable-looking way "
        "to write \"5-reading moving average\" a second time, e.g. in a "
        "notebook or a service that didn't import `features.py` — and it "
        "silently forgets that consecutive rows can belong to different "
        "streams (machine types).\n\n"
        f"For the 2nd reading of stream type L, right after the type-H "
        f"block ends in the ordered dataframe: the shared "
        f"`FeatureComputer` — which resets its buffer per stream key — "
        f"gives **{shared_value:.4f}**; the naive ungrouped "
        f"`pandas.rolling` gives **{naive_value:.4f}**, because it's "
        f"still averaging in trailing `torque_nm` readings from the "
        f"H-block. Difference: {diff:.4f}. Nothing crashes, nothing "
        "warns, the number is just wrong for this stream. This is the "
        "drift a single shared module, keyed correctly per stream, makes "
        "structurally impossible instead of merely unlikely.\n"
    )


def main() -> None:
    sections = [
        "# Task 3 Break-It Demo\n",
        scenario_missing_field(),
        scenario_cold_start_skew(),
        scenario_duplicate_implementation_drift(),
    ]
    report = "\n".join(sections)
    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report)
    print(f"Wrote {REPORT_PATH}")


if __name__ == "__main__":
    main()
