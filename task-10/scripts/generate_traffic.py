"""Sends synthetic telemetry to the Fleet Health Risk API, for two jobs:

1. Baseline traffic — sampled straight from Task 1's cleaned dataset, no
   shift, keeps both monitoring stacks warm and gives the drift job a
   current window that (correctly) shows *no* drift.
2. The Day 10 break-it exercise — the same sampling, but with
   ``air_temperature_k``/``process_temperature_k`` shifted down toward
   the schema's valid floor (still physically plausible, a genuinely cold
   morning, not a malformed reading the API would reject at the
   boundary), simulating exactly the "cold-weather battery behavior"
   sensor-distribution shift the roadmap names as the realistic FleetPulse
   drift story.

Each request goes to every ``--target`` given (the local docker-compose
API, the Lambda Function URL, or both) with the *same* generated payload
and the same ``--batch-id``-prefixed ``vehicle_id``, so one logical
invocation is traceable in both Grafana (Prometheus scrape of the local
API + its docker logs) and CloudWatch Logs (the Lambda side) — see
``README.md``'s "finding one invocation in both places."
"""
from __future__ import annotations

import argparse
import random
import time
import uuid
from pathlib import Path

import pandas as pd
import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
CLEANED_CSV = REPO_ROOT / "task-1" / "data" / "processed" / "cleaned.csv"

# Schema bounds from task-4/src/schemas.py::SENSOR_RANGES — every shifted
# reading generated here still has to clear these, deliberately: a value
# outside them is a validation-rejection test (already covered by Task 8),
# not a drift one. The shift below stays inside [280, 320)/[285, 330) but
# moves well outside the training distribution's real observed range
# (295.3-304.5K / 305.7-313.8K, see task-10/README.md).
COLD_SHIFT_AIR_TEMP_RANGE = (282.0, 289.0)
COLD_SHIFT_PROCESS_TEMP_RANGE = (288.0, 296.0)


def _load_pool() -> pd.DataFrame:
    return pd.read_csv(CLEANED_CSV)


def _sample_reading(pool: pd.DataFrame, shifted: bool, rng: random.Random) -> dict:
    row = pool.sample(n=1, random_state=rng.randint(0, 2**31)).iloc[0]
    reading = {
        "air_temperature_k": float(row["air_temperature_k"]),
        "process_temperature_k": float(row["process_temperature_k"]),
        "rotational_speed_rpm": float(row["rotational_speed_rpm"]),
        "torque_nm": float(row["torque_nm"]),
        "tool_wear_min": float(row["tool_wear_min"]),
    }
    machine_type = str(row["type"])
    if shifted:
        reading["air_temperature_k"] = rng.uniform(*COLD_SHIFT_AIR_TEMP_RANGE)
        reading["process_temperature_k"] = rng.uniform(*COLD_SHIFT_PROCESS_TEMP_RANGE)
    return machine_type, reading


def _post(target: str, payload: dict, timeout: float) -> tuple[int, float]:
    start = time.perf_counter()
    resp = requests.post(f"{target.rstrip('/')}/predict", json=payload, timeout=timeout)
    duration_ms = (time.perf_counter() - start) * 1000
    return resp.status_code, duration_ms


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", action="append", required=True, help="Base URL, repeatable (local API and/or Lambda Function URL)")
    parser.add_argument("--count", type=int, default=40)
    parser.add_argument("--shifted", action="store_true", help="Simulate the cold-weather sensor drift")
    parser.add_argument("--batch-id", default=None)
    parser.add_argument("--sleep-seconds", type=float, default=0.05)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()

    batch_id = args.batch_id or f"{'shifted' if args.shifted else 'baseline'}-{uuid.uuid4().hex[:8]}"
    rng = random.Random(args.seed)
    pool = _load_pool()

    print(f"batch_id={batch_id} shifted={args.shifted} count={args.count} targets={args.target}")

    ok, failed = 0, 0
    for i in range(args.count):
        machine_type, reading = _sample_reading(pool, args.shifted, rng)
        payload = {
            "vehicle_id": f"{batch_id}-{i:04d}",
            "type": machine_type,
            "telemetry_window": [reading],
        }
        for target in args.target:
            try:
                status, duration_ms = _post(target, payload, args.timeout)
                if status == 200:
                    ok += 1
                else:
                    failed += 1
                print(f"  [{target}] {payload['vehicle_id']} -> {status} ({duration_ms:.0f}ms)")
            except requests.RequestException as exc:
                failed += 1
                print(f"  [{target}] {payload['vehicle_id']} -> ERROR: {exc}")
        time.sleep(args.sleep_seconds)

    print(f"done: {ok} ok, {failed} failed, batch_id={batch_id}")


if __name__ == "__main__":
    main()
