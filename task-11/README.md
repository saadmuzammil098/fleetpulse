# FleetPulse, Task 11 (bonus): DAG Orchestration with Apache Airflow

Not part of the original 30-day roadmap's numbering (Day 11 there is GridScribe, a
different project phase) — added by request, as a hands-on introduction to DAG
orchestration using the tool that coined the term: **Apache Airflow**, run the
industry-standard way, via Docker.

## What was built

- `docker-compose.yml`: Airflow 3.3.0, **LocalExecutor** (no Celery/Redis/worker/
  flower — a real, documented, supported Airflow executor for single-machine use,
  trimmed down from [the official reference `docker-compose.yaml`](https://airflow.apache.org/docs/apache-airflow/3.3.0/docker-compose.yaml),
  not a toy shortcut). Services: `postgres` (Airflow's own metadata DB, separate
  from Task 5's app Postgres), `venv-init` (see below), `airflow-init`,
  `airflow-apiserver` (the web UI, `localhost:8080`), `airflow-scheduler`,
  `airflow-dag-processor`.
- `dags/training_pipeline_dag.py` — **DAG 1**: the from-scratch pipeline. `dvc_pull`
  → `ingest_and_validate` (Task 1) → fans out to `train_baseline_task2` (Task 2) and
  `track_and_promote_task3` (Task 3's `run_all.py`, unmodified) in parallel — a real
  fan-out, not a straight line, matching the actual dependency graph (both only need
  Task 1's cleaned dataset, not each other).
- `dags/drift_retrain_dag.py` — **DAG 2**: `check_drift` (Task 10's drift job) →
  branches on `drift_detected` → `retrain` (Task 3's `run_experiments.py` alone) →
  `compare_and_promote` (new, see below), or `skip_retrain` if there's no real drift.
- `scripts/compare_and_promote.py` — a champion/challenger promotion gate that
  Task 3 doesn't have. See "The gap this closes" below.

## Why a separate venv, not the Airflow image's own environment

Airflow pins its own dependency versions tightly. This project's `requirements.txt`
pins mlflow, pandas, scikit-learn (and Task 10's `requirements.txt` pins evidently,
boto3) at its own specific versions. Installing both into the same Python environment
is exactly the kind of resolver conflict that broke CI earlier in this project — see
the boto3/aiobotocore fix in Task 10's commit history, the identical failure mode,
just with Airflow's dependencies instead of `dvc-s3`'s.

Airflow's own documented answer to this is `@task.external_python`/`@task.venv` — run
a task in a separate, pre-built interpreter, never merged into Airflow's own
site-packages. This project's DAG tasks don't even need that decorator: every task
just shells out via `BashOperator` to a script that already exists (Task 1's
`src.pipeline`, Task 3's `src.run_experiments`, Task 10's `drift_report.py`, ...),
so a plain `bash_command` calling a separate venv's `python` binary directly is
enough, no in-process Airflow imports of project code at all.

That separate venv is built once by the `venv-init` service: a one-shot container
that creates `/opt/fleetpulse-venv` (a named Docker volume, shared with the
scheduler) and `pip install`s the repo's `requirements.txt` + `task-10/requirements.txt`
into it. A marker file makes it idempotent — a container restart doesn't
re-trigger the (slow) install, only `docker volume rm fleetpulse-airflow_fleetpulse-venv`
does. The scheduler container (the one that actually runs tasks under LocalExecutor)
never has these packages installed into its own environment at all.

## Why the whole repo is bind-mounted, not just `dags/`

Task 1/3/10's scripts read/write real sibling paths — Task 3 reads
`../task-1/data/processed/cleaned.csv`, Task 9/10's scripts import Task 3's
`features.py` by file path, Task 10's drift job reads Task 1's cleaned dataset and
Task 9's baked model artifact. A DAGs-only mount (the official reference setup's
default) can't reach any of that. `docker-compose.yml` mounts the whole repo,
read-write, at `/opt/fleetpulse` — `run_stack.sh` sets `HOST_REPO_PATH` to the real
absolute repo path first, the same pattern `task-5/run_stack.sh` already established
for Task 3's MLflow store (a fixed relative mount point can't work regardless of
where this repo happens to be cloned).

## Reaching Floci from inside a container: a real networking gotcha

`localhost.floci.io` (Floci's endpoint hostname) is a **public DNS record that
resolves to `127.0.0.1`** — confirmed directly: `python3 -c "import socket;
print(socket.gethostbyname('localhost.floci.io'))"` returns `127.0.0.1` from this
machine or anywhere else on the internet. That's fine for a process running directly
on the host (task-9/task-10's scripts, the self-hosted CI runner), where `127.0.0.1`
really is "this machine, where Floci is listening on port 4566." Inside a Docker
container, `127.0.0.1` means "this container," not the host — so the exact same DNS
lookup resolves correctly but points at nothing.

Confirmed both the failure and the fix before writing any DAG code:

```
$ docker run --rm curlimages/curl:latest -s -o /dev/null -w "%{http_code}\n" http://localhost.floci.io:4566/
# (hangs / connection refused — no Floci listening inside that container)

$ docker run --rm --add-host=localhost.floci.io:host-gateway curlimages/curl:latest \
    -s -o /dev/null -w "%{http_code}\n" http://localhost.floci.io:4566/
200
```

`extra_hosts: ["localhost.floci.io:host-gateway"]` on the `airflow-scheduler` service
(the only container that needs it — LocalExecutor runs each task as a subprocess of
the scheduler itself, so that's the one process that ever calls DVC or Task 10's
CloudWatch/S3 calls) overrides that one hostname to point at the Docker host instead
of the container's own loopback, via Docker's built-in `host-gateway` special value
(Docker 20.10+, works on Docker Desktop for Mac). Task 5's Pushgateway has no such
public-DNS collision (it's just an ordinary container port published on the host), so
reaching it only needs Docker Desktop's own built-in `host.docker.internal` hostname,
no `extra_hosts` override required.

## The gap this closes: `promote.py` always overwrites production

Reading `task-3/src/promote.py` closely before wiring up DAG 2 surfaced a real
problem: it selects the best run from whatever batch it's given and **unconditionally
promotes it to the `production` alias**, with no check against what's currently
live. That's the right behavior for Task 3's one-time bootstrap story
(`training_pipeline_dag.py` calls `run_all.py`, which resets the whole MLflow store
first — there's nothing to compare against, it's building from zero). It is not safe
to call unattended from a scheduled DAG: every drift-triggered retrain would silently
replace whatever's live with the new batch's best run, better or not.

`compare_and_promote.py` closes that gap without touching `promote.py` itself (still
used as-is for the bootstrap path) — it imports `promote.select_best` and
`promote.RECALL_FLOOR` rather than duplicating that logic, and adds the one check
that was missing: the candidate must clear the recall gate **and** beat the current
`production` alias's validation expected cost. Only then does the alias move; either
way, the candidate is registered at `staging` so every retrain attempt stays visible
in the registry, not just the ones that won.

**Verified this actually works, before wiring it into a DAG at all**: ran
`run_experiments.py` by hand (batch id `test-batch-001`), then
`compare_and_promote.py` against it. The new batch's best run tied the current
production model exactly on cost (51.0/1000 both):

```json
{"batch_id": "test-batch-001", "candidate_run_id": "39db67286d3943faa065bf851a50903d",
 "candidate_version": 2, "candidate_recall": 0.838, "candidate_cost_per_1000": 51.0,
 "passes_recall_gate": true, "champion_version": 1, "champion_cost_per_1000": 51.0,
 "beats_champion": false, "promoted_to_production": false,
 "reason": "candidate cost 51.0/1000 does not beat production's 51.0/1000"}
```

`promoted_to_production: false` — correctly refused to promote a model that only
tied, not actually improved on, what was already live. A blind `promote.py`-style
call would have overwritten `production` here for no real gain. This is exactly the
failure mode an unattended scheduled retrain needs a gate for.

## The two DAGs

**`fleetpulse_training_pipeline`** (manually triggered — `schedule=None`): the
from-scratch build. `dvc_pull >> ingest_and_validate >> [train_baseline_task2,
track_and_promote_task3]`.

**`fleetpulse_drift_triggered_retrain`** (`schedule="@hourly"`, a placeholder cadence
— a real deployment would tune this to how fast the fleet's actual sensor mix
moves): `check_drift >> branch_on_drift() >> [retrain, skip_retrain]`, with
`retrain >> compare_and_promote`. The branch reads a compact single-line JSON
(`{"drift_detected": ..., "drift_score": ...}`) that Task 10's `drift_report.py` now
prints as the literal last line of its output specifically for this — Airflow's
`BashOperator` auto-pushes a task's last stdout line to XCom, so the branch task
reads it with no extra file handoff.

Both DAGs start **paused** (`AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION: true`) —
an hourly automatic retrain check should be a decision an operator makes on purpose,
not a default that turns itself on the moment the DAG file exists.

## Reproduction steps

```bash
cd task-11
./run_stack.sh -d          # brings up postgres, venv-init (~5-10 min the first time,
                            # installs the full repo dependency tree), airflow-init,
                            # api-server, scheduler, dag-processor
# http://localhost:8080, login airflow/airflow
```

Then, from the UI (or `docker compose exec airflow-scheduler airflow dags trigger ...`):
unpause and trigger `fleetpulse_training_pipeline` first (needs a populated MLflow
registry for the second DAG's champion/challenger comparison to have a champion to
compare against), then `fleetpulse_drift_triggered_retrain`.

## Two real problems this surfaced, both fixed before either DAG ran green

1. **`dvc pull` refused to run.** `task-3/mlflow.db`/`mlartifacts` (tracked outputs of
   a *different* dvc.yaml stage) had drifted locally from manual dev/test runs earlier
   in this task's own development — DVC correctly refused to silently overwrite them:
   `ERROR: failed to pull data from the cloud - Can't remove the following unsaved
   files without confirmation. Use --force to force.` Fixed by adding `--force` to
   `dvc_pull`'s command, the right call for an *automated* pipeline run, which should
   authoritatively reset to the committed remote state — unlike a human running `dvc
   pull` interactively, who'd want that confirmation prompt.
2. **`check_drift` failed with `botocore.exceptions.NoRegionError`.** `drift_report.py`
   needs the same Floci credentials (`AWS_ACCESS_KEY_ID`, region, endpoint override)
   `dvc_pull` already had — reading CloudWatch Logs needs them regardless of the
   `--skip-cloudwatch` flag, which only skips the metric *push*. Fixed by adding the
   same `FLOCI_ENV` prefix to `check_drift`'s `bash_command`.

## Verified: both DAGs, run for real, both branches of the drift decision

**`fleetpulse_training_pipeline`**, triggered via the real API: succeeded end to end
in 71s. Confirmed the registry directly afterward, not just the green checkmark:

```
production version: 1 | cost/1000: 52.5 | batch_id tag: manual__2026-08-07T06:24:46.044469+00:00
```

The `batch_id` tag matching the Airflow run id proves this specific DAG run produced
this specific registered model, not a stale one.

**`fleetpulse_drift_triggered_retrain`, no-drift path**: 200 baseline (un-shifted)
predictions sent to the live Lambda first, then triggered. `check_drift` computed
`drift_score=0.143` (below the 0.3 threshold), `branch_on_drift` correctly routed to
`skip_retrain`, and — the actual point of the branch — `retrain` and
`compare_and_promote` show `state: skipped` in the task list, not merely "not yet
run":

```
check_drift success | retrain skipped | compare_and_promote skipped | skip_retrain success
```

**`fleetpulse_drift_triggered_retrain`, drift-detected path**: 200 winter-shifted
predictions sent (same `generate_traffic.py --shifted` exercise as Task 10), then
triggered again. This time:

```
check_drift success | retrain success | compare_and_promote success | skip_retrain skipped
```

`check_drift`'s own log: `drift_score=0.42857142857142855`, `DRIFT DETECTED`. The
retrain branch fired for real, `run_experiments.py` logged a fresh batch of 12 runs
tagged with this Airflow run id, and `compare_and_promote` made the real
champion/challenger call — and again correctly declined to promote a tie:

```json
{"batch_id": "manual__2026-08-07T06:29:38.665956+00:00", "candidate_recall": 0.853,
 "candidate_cost_per_1000": 52.5, "champion_version": 1, "champion_cost_per_1000": 52.5,
 "beats_champion": false, "promoted_to_production": false,
 "reason": "candidate cost 52.5/1000 does not beat production's 52.5/1000"}
```

Confirmed directly against the registry afterward: `production` stayed at version 1,
the new candidate landed at `staging` (version 2) — visible for review, not silently
discarded, and not silently promoted either. A `promote.py`-style blind call would
have overwritten a tied-not-improved model here; this DAG's gate correctly did not.

## Done when

- [x] Both DAGs parse in the Airflow UI with no import errors — verified via
      `GET /api/v2/importErrors`, `{"import_errors": [], "total_entries": 0}`.
- [x] `fleetpulse_training_pipeline` runs green end to end, Task 3's registry has a
      `production` alias afterward — verified above, with the batch_id tag proving
      it's this run's model.
- [x] `fleetpulse_drift_triggered_retrain` runs green on **both** branches (no drift
      → skip; real drift → retrain), and the champion/challenger decision it made is
      visible in `task-11/reports/champion_challenger_<run_id>.md` — verified above,
      both the JSON output and the registry state directly.
