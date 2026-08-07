"""Champion/challenger promotion gate — the piece Task 3's `promote.py`
deliberately does not have.

`task-3/src/promote.py` always overwrites the `production` alias with the
best run from whatever batch it's pointed at, with no check against
what's currently live. That's fine for Task 3's one-time "first model
ever" story, but it is not safe to call unattended from a scheduled DAG:
every drift-triggered retrain would silently replace a good model with a
worse one the moment the new batch's best run fails to beat the old one.

This script adds exactly that missing check: it never touches Task 3's
`promote.py` (still used as-is for the manual/first-time path, see
`task-3/README.md`), and imports its `select_best`/`RECALL_FLOOR` rather
than duplicating that logic, to stay in one place.

Deliberately lives in task-11/scripts/, not task-11/dags/scripts/ — Airflow's
DAG file processor scans every ``.py`` file under ``dags/`` looking for DAG
definitions, and would otherwise report a (harmless but noisy) import error
here since this script imports ``mlflow``, on purpose never installed into
Airflow's own environment (see README.md's "why a separate venv").

Run as `python scripts/compare_and_promote.py` with
``FLEETPULSE_RUN_BATCH_ID`` set to the batch tag `run_experiments.py` was
called with (see that module's docstring on the `batch_id` tag). Always
registers the batch's best candidate as a new model version aliased
``staging`` (so it's visible in the registry either way); only moves the
``production`` alias onto it if the candidate both clears Task 3's recall
gate and beats the current production model's validation expected cost.
Prints one JSON line to stdout as its last line — the DAG task reads this
via Airflow's `do_xcom_push` (BashOperator pushes a task's last stdout
line to XCom automatically), no separate file handoff needed.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import mlflow
from mlflow.exceptions import MlflowException

REPO_ROOT = Path(__file__).resolve().parents[2]
TASK3_ROOT = REPO_ROOT / "task-3"


def _import_task3():
    """Same by-path import trick used throughout this repo (see
    task-9/scripts/export_model.py's docstring) — task-3's package is
    also named `src`, so it can't be a normal top-level import here.
    """
    sys.path.insert(0, str(TASK3_ROOT))
    try:
        config = __import__("src.config", fromlist=["config"])
        promote = __import__("src.promote", fromlist=["promote"])
        return config, promote
    finally:
        sys.path.remove(str(TASK3_ROOT))


def _current_production(client: mlflow.MlflowClient, model_name: str):
    """Returns (version, val_expected_cost_per_1000) for whatever's
    currently aliased `production`, or (None, None) if nothing is yet —
    the very first promotion in a fresh registry has no champion to beat.
    """
    try:
        mv = client.get_model_version_by_alias(model_name, "production")
    except MlflowException:
        return None, None
    run = client.get_run(mv.run_id)
    return mv.version, run.data.metrics.get("val_expected_cost_per_1000")


def main() -> dict:
    config, promote = _import_task3()
    batch_id = os.environ["FLEETPULSE_RUN_BATCH_ID"]

    mlflow.set_tracking_uri(config.MLFLOW_TRACKING_URI)
    client = mlflow.MlflowClient()

    experiment = client.get_experiment_by_name(config.EXPERIMENT_NAME)
    batch_runs = client.search_runs(
        experiment_ids=[experiment.experiment_id],
        filter_string=f"tags.batch_id = '{batch_id}'",
    )
    if not batch_runs:
        raise RuntimeError(
            f"No runs found with batch_id={batch_id!r} — run_experiments.py "
            "must have failed or wasn't given this batch id."
        )

    candidate = promote.select_best(batch_runs)
    candidate_recall = candidate.data.metrics["val_recall"]
    candidate_cost = candidate.data.metrics["val_expected_cost_per_1000"]
    passes_gate = candidate_recall >= promote.RECALL_FLOOR

    champion_version, champion_cost = _current_production(client, config.REGISTERED_MODEL_NAME)

    if champion_version is None:
        beats_champion = True
        reason = "no current production model — first promotion"
    else:
        beats_champion = candidate_cost < champion_cost
        reason = (
            f"candidate cost {candidate_cost:.1f}/1000 "
            f"{'beats' if beats_champion else 'does not beat'} "
            f"production's {champion_cost:.1f}/1000"
        )

    # Always register the candidate at `staging`, win or lose — the
    # registry should show every retrain attempt, not only the ones that
    # actually got promoted, the same visibility principle Task 3's own
    # promote.py already follows.
    model_uri = f"runs:/{candidate.info.run_id}/model"
    mv = mlflow.register_model(model_uri, config.REGISTERED_MODEL_NAME)
    client.set_registered_model_alias(config.REGISTERED_MODEL_NAME, "staging", mv.version)

    promoted = passes_gate and beats_champion
    if promoted:
        client.set_registered_model_alias(config.REGISTERED_MODEL_NAME, "production", mv.version)

    result = {
        "batch_id": batch_id,
        "candidate_run_id": candidate.info.run_id,
        "candidate_version": mv.version,
        "candidate_recall": candidate_recall,
        "candidate_cost_per_1000": candidate_cost,
        "passes_recall_gate": passes_gate,
        "champion_version": champion_version,
        "champion_cost_per_1000": champion_cost,
        "beats_champion": beats_champion,
        "promoted_to_production": promoted,
        "reason": reason,
    }

    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPO_ROOT / "task-11" / "reports" / f"champion_challenger_{batch_id}.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        "# Champion/Challenger Decision\n\n"
        f"- Batch: `{batch_id}`\n"
        f"- Candidate run: `{candidate.info.run_id}` "
        f"(recall {candidate_recall:.3f}, cost {candidate_cost:.1f}/1000)\n"
        f"- Current production: version {champion_version or '(none)'} "
        f"(cost {f'{champion_cost:.1f}/1000' if champion_cost is not None else 'n/a'})\n"
        f"- Passed recall gate (>= {promote.RECALL_FLOOR:.0%}): {passes_gate}\n"
        f"- Beat champion on cost: {beats_champion}\n"
        f"- **Decision: {'PROMOTED to production' if promoted else 'registered as staging only, production unchanged'}**\n"
        f"- Reason: {reason}\n"
    )

    print(json.dumps(result))
    return result


if __name__ == "__main__":
    main()
