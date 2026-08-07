"""DAG 2 — check drift, and only retrain/promote if it's real.

This is the DAG the earlier "we can't avoid drift, what do we actually
do about it" conversation turns into runnable orchestration:

    check_drift --(branch on drift_detected)--> retrain --> compare_and_promote
                \\--(no drift)--------------------------> skip_retrain

Two things this DAG deliberately does NOT do, both on purpose:

1. It does not call task-3's `run_all.py`. That script resets the whole
   MLflow store and unconditionally promotes its best run to
   `production` — correct for a one-time bootstrap
   (`training_pipeline_dag.py`), unsafe for an unattended, scheduled
   retrain: every run would silently overwrite whatever's currently
   live, better or not. This DAG calls `run_experiments.py` alone (logs
   a fresh batch of runs, touches no registry alias), then hands off to
   `compare_and_promote.py`.
2. `compare_and_promote.py` (this task's own script, not task-3's
   `promote.py`) only moves the `production` alias if the new batch's
   best run both clears Task 3's recall gate AND beats the *current*
   production model's validation expected cost — see that script's
   docstring for the full champion/challenger logic, and
   task-11/README.md for a real run where the challenger did NOT win and
   production correctly stayed unchanged.

Starts paused (`AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION=true` in
docker-compose.yml) like every DAG in this project — an hourly retrain
check should be a decision an operator makes deliberately, not a default
this DAG's mere existence turns on.
"""
from __future__ import annotations

import json

import pendulum
from airflow.providers.standard.operators.bash import BashOperator
from airflow.providers.standard.operators.empty import EmptyOperator
from airflow.sdk import DAG, task

FLEETPULSE_VENV = "/opt/fleetpulse-venv/bin/python"
REPO = "/opt/fleetpulse"

# host.docker.internal: Docker Desktop's built-in hostname for "the
# machine running Docker," reachable without the extra_hosts trick
# docker-compose.yml needed for localhost.floci.io (that one had to
# override a *public* DNS record that otherwise resolves to the
# container's own loopback, not the host's — see docker-compose.yml's
# comment on the scheduler service; task-5's Pushgateway has no such
# public-DNS collision, so the plain Docker Desktop hostname works
# as-is).
PUSHGATEWAY = "host.docker.internal:9091"

FLOCI_ENV = (
    "AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test "
    "AWS_DEFAULT_REGION=us-east-1 AWS_ENDPOINT_URL=http://localhost.floci.io:4566"
)

with DAG(
    dag_id="fleetpulse_drift_triggered_retrain",
    description="Check Evidently drift score; retrain and champion/challenger-promote only if it's real",
    schedule="@hourly",  # a real deployment would tune this to how fast the fleet's sensor mix actually moves
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    tags=["fleetpulse", "drift", "retraining"],
) as dag:
    check_drift = BashOperator(
        task_id="check_drift",
        bash_command=(
            f"cd {REPO}/task-10 && {FLOCI_ENV} {FLEETPULSE_VENV} src/drift_report.py "
            f"--window-minutes 60 --pushgateway {PUSHGATEWAY} --skip-cloudwatch"
        ),
    )

    @task.branch(task_id="branch_on_drift")
    def branch_on_drift(**kwargs) -> str:
        # check_drift's BashOperator auto-pushed its last stdout line to
        # XCom (do_xcom_push=True is the default) — that line is the
        # compact {"drift_detected": ..., "drift_score": ...} JSON
        # drift_report.py prints as the literal last thing it does, see
        # that script's main(). Airflow context variables (including
        # task_instance) arrive via **kwargs — see Airflow 3.x's TaskFlow
        # docs on context access.
        ti = kwargs["task_instance"]
        last_line = ti.xcom_pull(task_ids="check_drift")
        result = json.loads(last_line)
        return "retrain" if result["drift_detected"] else "skip_retrain"

    retrain = BashOperator(
        task_id="retrain",
        # run_experiments.py alone, not run_all.py — see this module's
        # docstring for why an unattended retrain must not reset the
        # store or auto-promote the way the from-scratch DAG does.
        bash_command=(
            f"cd {REPO}/task-3 && FLEETPULSE_RUN_BATCH_ID={{{{ run_id }}}} "
            f"{FLEETPULSE_VENV} -m src.run_experiments"
        ),
    )

    compare_and_promote = BashOperator(
        task_id="compare_and_promote",
        bash_command=(
            f"cd {REPO}/task-11 && FLEETPULSE_RUN_BATCH_ID={{{{ run_id }}}} "
            f"{FLEETPULSE_VENV} scripts/compare_and_promote.py"
        ),
    )

    skip_retrain = EmptyOperator(task_id="skip_retrain")

    check_drift >> branch_on_drift() >> [retrain, skip_retrain]
    retrain >> compare_and_promote
