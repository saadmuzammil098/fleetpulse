#!/usr/bin/env bash
# The one command: fresh clone -> dvc pull (or task-3 dvc repro) -> this.
#
# Exports HOST_TASK3_PATH so docker-compose.yml can bind-mount task-3/ at
# its real absolute path inside the container, matching the absolute path
# MLflow's local file-based artifact store baked into mlflow.db at
# training time (see README "why the model isn't baked in") — a fixed
# relative mount point (e.g. /app/task-3) can't work here regardless of
# where this repo happens to be cloned.
set -euo pipefail
cd "$(dirname "$0")"
export HOST_TASK3_PATH
HOST_TASK3_PATH="$(cd ../task-3 && pwd)"
exec docker compose up --build "$@"
