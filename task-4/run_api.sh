#!/usr/bin/env bash
# The one command: fresh clone -> dvc pull -> this -> a running API.
set -euo pipefail
cd "$(dirname "$0")"
source ../.venv/bin/activate
exec uvicorn src.main:app --host 0.0.0.0 --port "${PORT:-8811}"
