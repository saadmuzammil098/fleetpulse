#!/usr/bin/env bash
# The one command: brings up Airflow (Postgres, venv-init, init,
# api-server, scheduler, dag-processor) with the whole repo bind-mounted
# in, the same HOST_REPO_PATH pattern task-5/run_stack.sh already uses
# for task-3 (a fixed relative mount point can't work here regardless of
# where this repo happens to be cloned — see that script's own comment).
set -euo pipefail
cd "$(dirname "$0")"
export HOST_REPO_PATH
HOST_REPO_PATH="$(cd .. && pwd)"
exec docker compose up "$@"
