"""Task 10 drift job: reference = Task 1's validated telemetry, current =
real logged predictions pulled straight from CloudWatch Logs (via Floci),
the same log group Task 9's Lambda already writes structured JSON
``prediction_made`` events to (confirmed: ``task-4/src/main.py``'s
``log_requests``/``predict`` handlers run unchanged on Lambda, so every
Function URL invocation lands there with no extra plumbing).

Two kinds of drift are checked in one Evidently ``DataDriftPreset`` run,
scored over the same set of columns:

* **Data drift** — the five raw sensor columns plus ``type``, compared
  directly: the reference frame is Task 1's real cleaned dataset, the
  current frame is the raw sensor readings actually sent to the deployed
  model, both live on the same physical scale, no transformation needed.
* **Prediction drift** — a ``prediction`` column, the model's own
  ``failure_probability`` output. The current side is the real value the
  deployed Lambda returned. The reference side does not exist in Task 1's
  data (it is unscored raw telemetry), so it is manufactured here by
  loading Task 9's exact baked model artifact and scoring the reference
  sample through it, through the same rolling-feature pipeline training
  used (``task-3/src/features.py::compute_ordered_features``) — the only
  way to get a "what would this model have said" column to compare
  against.

The result is boiled down to one scalar, ``drift_score`` — the share of
compared columns Evidently's K-S/PSI tests flagged as drifted — and
pushed to both monitoring stacks this task stands up: a Prometheus
Pushgateway gauge (self-hosted side) and a CloudWatch custom metric via
Floci (AWS-native side), see ``README.md``'s "one drift score, two
destinations" for why a single computation feeding both, instead of two
separate drift jobs, is the honest way to keep the comparison apples to
apples.
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import boto3
import joblib
import pandas as pd
from evidently import Dataset, Report
from evidently.presets import DataDriftPreset
from prometheus_client import CollectorRegistry, Gauge, push_to_gateway

REPO_ROOT = Path(__file__).resolve().parents[2]
TASK10_ROOT = Path(__file__).resolve().parents[1]
CLEANED_CSV = REPO_ROOT / "task-1" / "data" / "processed" / "cleaned.csv"
MODEL_ARTIFACT = REPO_ROOT / "task-9" / "model_artifact" / "model.joblib"
REPORTS_DIR = TASK10_ROOT / "reports"

SENSOR_COLUMNS = [
    "air_temperature_k",
    "process_temperature_k",
    "rotational_speed_rpm",
    "torque_nm",
    "tool_wear_min",
]
DRIFT_COLUMNS = SENSOR_COLUMNS + ["type", "prediction"]

LOG_GROUP_NAME = "/aws/lambda/fleetpulse-api"
CLOUDWATCH_NAMESPACE = "FleetPulse"
PUSHGATEWAY_JOB = "fleetpulse_drift"


def _import_task_module(task_dir: str, module_name: str):
    """Import ``src.<module_name>`` from ``task_dir`` in isolation.

    Same by-path import trick ``task-9/scripts/export_model.py`` already
    uses, needed for the same reason: every task folder in this repo
    names its own package ``src``.
    """
    task_path = REPO_ROOT / task_dir
    sys.path.insert(0, str(task_path))
    try:
        return importlib.import_module(f"src.{module_name}")
    finally:
        sys.path.remove(str(task_path))
        for name in list(sys.modules):
            if name == "src" or name.startswith("src."):
                del sys.modules[name]


def load_reference(sample_size: int = 2000, seed: int = 13) -> pd.DataFrame:
    """Task 1's cleaned dataset, scored through Task 9's baked model.

    Sampled (not the full 10,000 rows) so a drift run stays fast; sorted
    by (type, tool_wear_min) before scoring, the exact order
    ``export_model.py`` trained in, so the rolling features this model
    actually learned against are reproduced faithfully, not approximated.
    """
    if not CLEANED_CSV.exists():
        raise FileNotFoundError(
            f"{CLEANED_CSV} not found — run `dvc pull` from the repo root first "
            "(Task 1's validated telemetry is the reference window)."
        )
    df = pd.read_csv(CLEANED_CSV)[SENSOR_COLUMNS + ["type"]].copy()
    if sample_size and len(df) > sample_size:
        df = df.sample(n=sample_size, random_state=seed)
    df = df.sort_values(["type", "tool_wear_min"], kind="stable").reset_index(drop=True)

    features = _import_task_module("task-3", "features")
    ordered_readings = [
        (row["type"], {c: row[c] for c in SENSOR_COLUMNS}) for _, row in df.iterrows()
    ]
    feature_rows = features.compute_ordered_features(ordered_readings)
    feature_df = pd.DataFrame(feature_rows, index=df.index)
    rolling_columns = features.FeatureComputer().feature_names()

    model = joblib.load(MODEL_ARTIFACT)
    X = pd.concat([df[SENSOR_COLUMNS + ["type"]], feature_df[rolling_columns]], axis=1)
    df["prediction"] = model.predict_proba(X)[:, 1]

    return df[DRIFT_COLUMNS]


def load_current_from_cloudwatch(
    window_minutes: int = 60,
    log_group: str = LOG_GROUP_NAME,
    model_version: str | None = None,
    region: str | None = None,
) -> pd.DataFrame:
    """The real, currently-deployed model's predictions, straight from the
    CloudWatch Logs group Floci is emulating for the Task 9 Lambda.

    ``model_version`` (usually the value ``/health`` reports right now)
    filters out predictions from a since-replaced model version, so a
    redeploy mid-window can't be misread as data drift.
    """
    client = boto3.client("logs", region_name=region) if region else boto3.client("logs")
    start_ms = int((time.time() - window_minutes * 60) * 1000)

    rows: list[dict] = []
    kwargs: dict = {"logGroupName": log_group, "startTime": start_ms}
    while True:
        resp = client.filter_log_events(**kwargs)
        for event in resp.get("events", []):
            try:
                payload = json.loads(event["message"])
            except (json.JSONDecodeError, TypeError):
                continue
            if payload.get("event") != "prediction_made":
                continue
            if model_version and payload.get("model_version") != model_version:
                continue
            features = payload.get("features")
            if not features:
                continue
            rows.append(
                {
                    **{c: features[c] for c in SENSOR_COLUMNS},
                    "type": payload.get("machine_type"),
                    "prediction": payload.get("failure_probability"),
                }
            )
        token = resp.get("nextToken")
        if not token:
            break
        kwargs["nextToken"] = token

    return pd.DataFrame(rows, columns=DRIFT_COLUMNS)


def load_current_from_jsonl(path: Path) -> pd.DataFrame:
    """Fallback current-window source: a local JSONL file of the same
    ``prediction_made`` log records, one per line — useful for a fast,
    offline drift-job dry run without a live Lambda round trip, and the
    "lightweight local store" option the Task 10 spec explicitly allows.
    """
    rows: list[dict] = []
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            features = payload["features"]
            rows.append(
                {
                    **{c: features[c] for c in SENSOR_COLUMNS},
                    "type": payload.get("machine_type"),
                    "prediction": payload.get("failure_probability"),
                }
            )
    return pd.DataFrame(rows, columns=DRIFT_COLUMNS)


def compute_drift(reference: pd.DataFrame, current: pd.DataFrame):
    report = Report(metrics=[DataDriftPreset()])
    run = report.run(
        reference_data=Dataset.from_pandas(reference),
        current_data=Dataset.from_pandas(current),
    )
    result = run.dict()

    drift_score = None
    drifted_count = None
    per_column: dict[str, float] = {}
    for metric in result["metrics"]:
        name = metric["metric_name"]
        if name.startswith("DriftedColumnsCount"):
            drift_score = metric["value"]["share"]
            drifted_count = metric["value"]["count"]
        elif name.startswith("ValueDrift(column="):
            column = name.split("column=", 1)[1].split(",")[0]
            per_column[column] = metric["value"]

    return run, {
        "drift_score": drift_score,
        "drifted_column_count": drifted_count,
        "total_columns": len(DRIFT_COLUMNS),
        "per_column_drift": per_column,
    }


def push_to_prometheus(drift_score: float, drift_detected: bool, window_size: int, gateway: str) -> None:
    registry = CollectorRegistry()
    Gauge("fleetpulse_drift_score", "Share of compared columns Evidently flagged as drifted", registry=registry).set(
        drift_score
    )
    Gauge("fleetpulse_drift_detected", "1 if drift_score crossed the alert threshold, else 0", registry=registry).set(
        1 if drift_detected else 0
    )
    Gauge(
        "fleetpulse_drift_current_window_size", "Number of predictions in the current drift window", registry=registry
    ).set(window_size)
    Gauge("fleetpulse_drift_last_run_timestamp", "Unix timestamp of the last completed drift run", registry=registry).set(
        time.time()
    )
    push_to_gateway(gateway, job=PUSHGATEWAY_JOB, registry=registry)


def push_to_cloudwatch(drift_score: float, drift_detected: bool, window_size: int, region: str | None = None) -> None:
    client = boto3.client("cloudwatch", region_name=region) if region else boto3.client("cloudwatch")
    now = datetime.now(UTC)
    client.put_metric_data(
        Namespace=CLOUDWATCH_NAMESPACE,
        MetricData=[
            {"MetricName": "DriftScore", "Value": drift_score, "Unit": "None", "Timestamp": now},
            {"MetricName": "DriftDetected", "Value": 1.0 if drift_detected else 0.0, "Unit": "None", "Timestamp": now},
            {"MetricName": "CurrentWindowSize", "Value": float(window_size), "Unit": "Count", "Timestamp": now},
        ],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=["cloudwatch", "jsonl"], default="cloudwatch")
    parser.add_argument("--jsonl-path", type=Path, default=TASK10_ROOT / "logs" / "predictions.jsonl")
    parser.add_argument("--window-minutes", type=int, default=60)
    parser.add_argument("--model-version", default=None, help="Filter CloudWatch events to this model_version only")
    parser.add_argument("--reference-sample-size", type=int, default=2000)
    parser.add_argument("--drift-threshold", type=float, default=0.3, help="drift_score at/above this counts as drift")
    parser.add_argument("--pushgateway", default="localhost:9091")
    parser.add_argument("--skip-prometheus", action="store_true")
    parser.add_argument("--skip-cloudwatch", action="store_true")
    parser.add_argument("--region", default=None)
    args = parser.parse_args()

    print("Loading reference window (Task 1 cleaned dataset, scored through Task 9's model)...")
    reference = load_reference(sample_size=args.reference_sample_size)
    print(f"  reference rows: {len(reference)}")

    print(f"Loading current window from {args.source}...")
    if args.source == "cloudwatch":
        current = load_current_from_cloudwatch(
            window_minutes=args.window_minutes, model_version=args.model_version, region=args.region
        )
    else:
        current = load_current_from_jsonl(args.jsonl_path)
    print(f"  current rows: {len(current)}")

    if current.empty:
        print("No current-window predictions found — nothing to compare. Send some traffic first "
              "(see scripts/generate_traffic.py) and re-run.")
        raise SystemExit(1)

    run, summary = compute_drift(reference, current)
    drift_detected = summary["drift_score"] >= args.drift_threshold
    summary["drift_detected"] = drift_detected
    summary["threshold"] = args.drift_threshold
    summary["current_window_size"] = len(current)
    summary["reference_window_size"] = len(reference)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    html_path = REPORTS_DIR / f"drift_report_{stamp}.html"
    json_path = REPORTS_DIR / f"drift_summary_{stamp}.json"
    run.save_html(str(html_path))
    json_path.write_text(json.dumps(summary, indent=2))

    print(json.dumps(summary, indent=2))
    print(f"HTML report: {html_path}")
    print(f"JSON summary: {json_path}")

    if not args.skip_prometheus:
        try:
            push_to_prometheus(summary["drift_score"], drift_detected, len(current), args.pushgateway)
            print(f"Pushed drift_score={summary['drift_score']:.3f} to Pushgateway at {args.pushgateway}")
        except Exception as exc:  # pragma: no cover - operational path, not unit-tested
            print(f"WARNING: failed to push to Prometheus Pushgateway: {exc}")

    if not args.skip_cloudwatch:
        try:
            push_to_cloudwatch(summary["drift_score"], drift_detected, len(current), region=args.region)
            print(f"Pushed drift_score={summary['drift_score']:.3f} to CloudWatch namespace {CLOUDWATCH_NAMESPACE}")
        except Exception as exc:  # pragma: no cover - operational path, not unit-tested
            print(f"WARNING: failed to push to CloudWatch: {exc}")

    if drift_detected:
        print(f"\nDRIFT DETECTED: drift_score={summary['drift_score']:.3f} >= threshold={args.drift_threshold}")
    else:
        print(f"\nNo drift: drift_score={summary['drift_score']:.3f} < threshold={args.drift_threshold}")

    # Task 11 addition: one compact, single-line JSON as the literal last
    # line of stdout, on its own after the human-readable output above.
    # Airflow's BashOperator auto-pushes a task's last stdout line to
    # XCom (do_xcom_push=True is the default) — the drift-retrain DAG's
    # branch task reads `drift_detected` from exactly this line, not by
    # re-parsing the pretty-printed summary or opening the JSON report
    # file this same run already wrote above.
    print(json.dumps({"drift_detected": drift_detected, "drift_score": summary["drift_score"]}))


if __name__ == "__main__":
    main()
