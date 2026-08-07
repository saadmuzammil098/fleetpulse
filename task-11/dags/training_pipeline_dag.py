"""DAG 1 — the from-scratch training pipeline: ingest, validate, train,
track, promote.

Orchestrates the exact commands Tasks 1/2/3's own dvc.yaml stages already
run by hand (see each task's README "How to reproduce" section) — this
DAG doesn't reimplement any of that logic, it just gives it real task
dependencies, retries, and a schedule instead of "run these three
commands in the right order and hope you didn't forget one."

Shape: ingest_and_validate feeds two independent downstream branches
(Task 2's baseline model and Task 3's MLflow tracking + registry
promotion) that both only need Task 1's cleaned dataset, not each other —
a real fan-out, not just a linear chain, matching the actual dependency
graph documented in the repo root README's architecture diagram.

Every task shells out to the fleetpulse-venv Python (see
docker-compose.yml's FLEETPULSE_VENV_PYTHON and README.md's "why a
separate venv"), never Airflow's own interpreter.

`run_all.py` (Task 3's "the one command") resets the local MLflow store
and unconditionally promotes its best run to `production` — the right
behavior for *this* DAG, which represents "build FleetPulse from zero,"
the same one-time bootstrap story task-3/README.md already documents.
Compare this to `drift_retrain_dag.py`, which must NOT reset the store
or promote blindly — see that DAG's docstring for why.
"""
from __future__ import annotations

import pendulum
from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG

FLEETPULSE_VENV = "/opt/fleetpulse-venv/bin/python"
REPO = "/opt/fleetpulse"

FLOCI_ENV = (
    "AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test "
    "AWS_DEFAULT_REGION=us-east-1 AWS_ENDPOINT_URL=http://localhost.floci.io:4566"
)

with DAG(
    dag_id="fleetpulse_training_pipeline",
    description="Ingest -> validate (Task 1) -> train baseline (Task 2) + track/promote (Task 3)",
    schedule=None,  # manually triggered — see README.md for why this DAG defaults to on-demand
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    tags=["fleetpulse", "training"],
) as dag:
    dvc_pull = BashOperator(
        task_id="dvc_pull",
        # --force: an automated pipeline run authoritatively resets to
        # the committed remote state — unlike a human running `dvc pull`
        # interactively, who'd want the confirmation prompt DVC shows by
        # default before overwriting local changes it doesn't recognize
        # (real thing this hit: task-3's mlflow.db/mlartifacts, tracked
        # outputs of a *different* dvc.yaml stage, had drifted locally
        # from manual dev/test runs during this task's own development).
        bash_command=(
            f"cd {REPO}/task-1 && {FLOCI_ENV} {FLEETPULSE_VENV} -m dvc pull --force"
        ),
    )

    ingest_and_validate = BashOperator(
        task_id="ingest_and_validate",
        # task-1/dvc.yaml's "pipeline" stage: ingest -> clean -> validate
        # -> profile, writing task-1/data/processed/cleaned.csv.
        bash_command=f"cd {REPO}/task-1 && {FLEETPULSE_VENV} -m src.pipeline",
    )

    train_baseline = BashOperator(
        task_id="train_baseline_task2",
        # task-2/dvc.yaml's "train" stage. Informational/parallel: Task 4
        # serves from Task 3's registry, not this model, see the repo
        # root README's architecture diagram.
        bash_command=f"cd {REPO}/task-2 && {FLEETPULSE_VENV} -m src.train",
    )

    track_and_promote = BashOperator(
        task_id="track_and_promote_task3",
        # task-3/dvc.yaml's "experiment_tracking" stage: 12 tracked runs,
        # then promote.py's (unconditional, bootstrap-appropriate here)
        # best-run promotion. FLEETPULSE_RUN_BATCH_ID tags all 12 runs
        # with this DAG run's id (see run_experiments.py's docstring).
        bash_command=(
            f"cd {REPO}/task-3 && FLEETPULSE_RUN_BATCH_ID={{{{ run_id }}}} "
            f"{FLEETPULSE_VENV} -m src.run_all"
        ),
    )

    dvc_pull >> ingest_and_validate >> [train_baseline, track_and_promote]
