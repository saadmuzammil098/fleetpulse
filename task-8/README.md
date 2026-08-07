# FleetPulse, Task 8: CI and the ML Testing Pyramid

Task 8 of 30 in the [production AI/ML roadmap](../../30-day-ai-ml-roadmap-industry-portfolio.md),
building a GitHub Actions pipeline that lints, tests, and builds the Fleet Health Risk
API on every PR, plus a pytest suite that covers the ML testing pyramid: unit tests,
data tests, a training smoke test, and behavioral tests specific to FleetPulse.

## What's tested, and why

**Unit tests** (`tests/test_features.py`, `tests/test_schemas.py`) check the smallest
correct units in isolation: Task 3's `FeatureComputer` rolling-window math (mean, std,
rate-of-change, per-stream-key independence, the missing-column error), and Task 4's
Pydantic request/response contracts (in-range readings accepted, out-of-range readings
rejected, an empty telemetry window rejected). These are fast, and they're the layer
that catches an off-by-one in the rolling window or a wrong Pydantic bound before it
ever reaches an integration test.

**Data tests** (`tests/test_data_contract.py`) run Task 1's Pandera schema
(`task-1/src/schema.py::cleaned_schema`) as an automated pytest check instead of only
as a one-off report at ingestion time. A schema regression, or a cleaning-code change
that quietly stops satisfying it, now fails CI on the PR that caused it, not months
later when someone happens to reread `validation_report.md`.

**A training smoke test** (`tests/test_training_smoke.py`) runs Task 2's actual
pipeline shape, `build_pipeline` → `StratifiedKFold` cross-validation → 
`CalibratedClassifierCV` → threshold search → metrics, reduced to 3 folds on ~180
synthetic rows instead of Task 2's real 5-fold CV on the full dataset. It makes no
accuracy claim. Its only job is proving the training code still runs end to end:
catching an import error, a shape mismatch, or a step that now raises, the kind of
break that a metrics-focused review can miss if nobody happens to run the real, slow
training job on that PR.

**Behavioral tests** (`tests/test_behavioral.py`) are directional-expectation checks a
domain expert would actually want to see, run through the real serving path
(`task-4/src/inference.py::predict`), not a mocked shortcut. FleetPulse's dataset (UCI
AI4I 2020) has no vibration channel, so the roadmap's generic "temperature and
vibration" example doesn't map onto this project's actual sensors. The two sensors
that jointly drive this dataset's real overstrain failure mode (OSF) are torque and
tool wear: sustained high torque late in a component's operating life is a textbook
mechanical overstrain signature. `test_high_torque_and_tool_wear_raises_risk_relative_to_baseline`
asserts predicted risk goes up when both rise together, and
`test_clearly_healthy_profile_scores_low_risk` asserts a clearly healthy profile scores
low. A model that gets either backwards, or a bug that makes the API indifferent to
it, is not safe to ship even if its offline PR-AUC looks fine, and a metrics-only test
suite would never catch it.

## Why synthetic data in CI

GitHub-hosted runners cannot reach `localhost.floci.io`, the local Floci S3 endpoint
Task 1's DVC remote (`s3://fleetpulse-dvc`) points at, so CI cannot `dvc pull` the real
cleaned dataset, and Task 3's MLflow registry (`task-3/mlflow.db`, `mlartifacts/`) is
gitignored and doesn't exist on a fresh clone either. `tests/conftest.py`'s
`make_synthetic_fleet_data` generates a small dataset (450 rows across the three
machine types) with a deliberate torque/tool-wear interaction standing in for the real
overstrain relationship, and `fixture_model` fits a real pipeline (Task 3's
`build_pipeline`, on raw sensors + type + Task 3's actual rolling features) against it.
Every test in this suite runs against that fixture model and that synthetic data, not
against the real production model. Local development and the real training/tracking
runs still go through the full DVC/MLflow path documented in Tasks 1-3, this only
affects what CI can reach.

## Why every task's `src` package needs an import shim

Every task folder in this repo names its own package `src` (see
`task-2/src/load_data.py`'s docstring for the original reason). Importing two tasks'
modules into one pytest process the normal way collides on `sys.modules["src"]`.
`tests/conftest.py::import_task_module` is the test-side version of the same trick
`task-4/src/shared_features.py` already uses in application code: import one task's
`src` package, hand back the requested submodule, then evict every `src`-rooted entry
from `sys.modules` so the next task's import starts clean.

## Lint

[Ruff](https://docs.astral.sh/ruff/) lints and formats the whole repo, configured once
in the repo-root `pyproject.toml`. `E501` (line too long) is ignored on purpose: most
of this repo's over-length lines are deliberately long, wrapped prose in
docstrings/comments explaining a non-obvious design decision, not code ruff can
usefully reflow.

```bash
ruff check .        # lint
ruff format .       # format
```

## Pre-commit

Installed once per clone, these hooks then run automatically on every `git commit`
against staged files only, using the same ruff config CI lints against.

```bash
pip install -r requirements-dev.txt
pre-commit install
```

## Running the suite locally

```bash
python3.12 -m venv .venv && source .venv/bin/activate   # from the repo root, if not already set up
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest task-8/tests -v
```

## What CI catches

`.github/workflows/ci-cd.yml` runs three jobs on every PR and on push to `main`: `lint`
(`ruff check .`), `test` (unit, data, training-smoke, and behavioral tests, as separate
named steps), and `docker-build` (Task 5's Dockerfile, built but not pushed, proving
the image still builds from a clean checkout).

**Break-it demo:** `task-4/src/inference.py::predict` was temporarily changed to
`failure_probability = 1.0 - float(model.predict_proba(X)[:, 1][0])`, inverting the
risk score. Both behavioral tests failed immediately:

```
FAILED task-8/tests/test_behavioral.py::test_high_torque_and_tool_wear_raises_risk_relative_to_baseline
FAILED task-8/tests/test_behavioral.py::test_clearly_healthy_profile_scores_low_risk
AssertionError: expected a clearly healthy sensor profile to score low risk, got 0.826
```

A model whose risk score points the wrong way would have shipped clean past every
metrics-based check in Tasks 2 and 3, calibration and PR-AUC don't care which class
label 1 means. The behavioral test is what actually catches it. The change was
reverted immediately after confirming the failure; `git diff` on `task-4/src/inference.py`
is clean on `main`.

## Done when

- A fresh clone plus `pip install -r requirements.txt -r requirements-dev.txt` plus
  `python -m pytest task-8/tests` reproduces a green run with no DVC pull and no
  running MLflow server required.
- `ruff check .` passes clean across the whole repo.
- A PR that breaks the torque/tool-wear behavioral expectation fails CI; a clean PR
  passes lint, all four test steps, and the Docker build.
