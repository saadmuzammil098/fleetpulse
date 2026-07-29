"""Single source of truth for rolling-window sensor features.

Both training (``src/load_data.py``, replaying an ordered dataset row by
row) and the mock real-time inference call (``src/mock_inference.py``, one
reading at a time) call ``FeatureComputer.compute()`` — the exact same
method, on the exact same class. Training does not have a separate
pandas ``.rolling()`` implementation that happens to produce similar
numbers; it feeds the ordered historical readings through this same
stateful computer, one at a time, exactly as a live stream would. That is
what guarantees zero training/serving skew: there is one place this math
lives, not two that can quietly drift apart.

FleetPulse's dataset (UCI AI4I 2020, see Task 1/2) is per-unit snapshots,
not a real sensor time series — there is no timestamp column. Task 3
simulates a live telemetry stream by treating each machine `type` (L/M/H)
as one continuous production line and ordering its snapshots by
``tool_wear_min`` (elapsed operating minutes since service), which Task 2
already established as the closest available proxy for "how long has this
component been running." That's a property of the dataset, not a shortcut
in this module — see the Task 3 README's "Honest limitation" section.
"""
from __future__ import annotations

from collections import deque

SENSOR_COLUMNS = [
    "air_temperature_k",
    "process_temperature_k",
    "rotational_speed_rpm",
    "torque_nm",
    "tool_wear_min",
]

DEFAULT_WINDOWS = (3, 5)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _std(values: list[float]) -> float:
    """Sample standard deviation (ddof=1); 0.0 for a single-point window.

    A single reading has no spread yet, not an undefined one — returning
    0.0 instead of NaN keeps every feature a real number a model can
    consume, from the very first reading a stream ever produces.
    """
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    return (sum((v - m) ** 2 for v in values) / (len(values) - 1)) ** 0.5


class FeatureComputer:
    """Maintains one bounded rolling-window history buffer per stream key.

    Call ``compute(key, reading)`` once per incoming reading, in arrival
    order, for a given stream key (here, machine ``type``). It returns
    that reading's rolling-window features computed from readings seen
    *before* it for that key, then appends the reading to history.
    """

    def __init__(
        self,
        windows: tuple[int, ...] = DEFAULT_WINDOWS,
        sensor_columns: list[str] = SENSOR_COLUMNS,
    ):
        self.windows = tuple(windows)
        self.sensor_columns = list(sensor_columns)
        self._history: dict[str, deque] = {}

    def _buffer(self, key: str) -> deque:
        maxlen = max(self.windows)
        return self._history.setdefault(key, deque(maxlen=maxlen))

    def compute(self, key: str, reading: dict) -> dict:
        missing = [c for c in self.sensor_columns if c not in reading]
        if missing:
            raise ValueError(
                f"reading for key={key!r} is missing required sensor "
                f"columns {missing}; a live reading must carry the same "
                f"fields training was built from, see Task 3 README's "
                f"break-it demo for what happens when it doesn't."
            )

        buf = self._buffer(key)
        past = list(buf)
        features: dict[str, float] = {}
        for col in self.sensor_columns:
            past_values = [h[col] for h in past]
            series = past_values + [reading[col]]
            for w in self.windows:
                window_vals = series[-w:]
                features[f"{col}_rollmean_w{w}"] = _mean(window_vals)
                features[f"{col}_rollstd_w{w}"] = _std(window_vals)
            prev = past_values[-1] if past_values else reading[col]
            features[f"{col}_roc"] = float(reading[col]) - float(prev)

        buf.append({c: reading[c] for c in self.sensor_columns})
        return features

    def feature_names(self) -> list[str]:
        names: list[str] = []
        for col in self.sensor_columns:
            for w in self.windows:
                names.append(f"{col}_rollmean_w{w}")
                names.append(f"{col}_rollstd_w{w}")
            names.append(f"{col}_roc")
        return names

    def history_length(self, key: str) -> int:
        return len(self._history.get(key, ()))


def compute_ordered_features(
    ordered_readings: list[tuple[str, dict]],
    windows: tuple[int, ...] = DEFAULT_WINDOWS,
    sensor_columns: list[str] = SENSOR_COLUMNS,
) -> list[dict]:
    """Replay ``(key, reading)`` pairs, in order, through one FeatureComputer.

    This is the only function training uses to build rolling features
    (see ``load_data.build_feature_dataset``) — it is a thin loop around
    ``FeatureComputer.compute``, not a parallel implementation. Mock
    inference builds its own ``FeatureComputer`` and calls ``compute`` the
    same way, one reading at a time, which is exactly what makes the two
    call sites provably identical rather than merely "supposed to match."
    """
    computer = FeatureComputer(windows=windows, sensor_columns=sensor_columns)
    return [computer.compute(key, reading) for key, reading in ordered_readings]
