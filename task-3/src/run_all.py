"""The one command: train + track 12 runs, then promote the best.

Run as `python -m src.run_all` from task-3/ (wired into dvc.yaml). Resets
the local MLflow store first so re-running is idempotent: the same 12
deterministic configs (fixed random_state, see pipeline.py) always
produce the same run history and the same promotion decision, not an
ever-growing pile of duplicate runs.
"""
from __future__ import annotations

import shutil

from . import config
from . import run_experiments
from . import promote


def reset_mlflow_store() -> None:
    if config.MLFLOW_DB_PATH.exists():
        config.MLFLOW_DB_PATH.unlink()
    artifact_dir = config.TASK3_ROOT / "mlartifacts"
    if artifact_dir.exists():
        shutil.rmtree(artifact_dir)


def main() -> None:
    reset_mlflow_store()
    run_experiments.main()
    promote.main()


if __name__ == "__main__":
    main()
