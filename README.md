# FleetPulse

Predictive maintenance and fleet health monitoring for a commercial vehicle fleet — from
raw sensor telemetry ingestion through a monitored, production-style API. Tasks 1–10 of a
[30-day production AI/ML engineering roadmap](https://github.com/saadmuzammil098), built
entirely against [Floci](https://floci.io) (a local AWS emulator) instead of a real AWS
account, so every AWS-shaped piece of this — S3, and later Lambda/ECR/EKS — runs at $0.

## Tasks

| Task | Folder | What it is |
|---|---|---|
| 1 | [`task-1/`](./task-1) | Reproducible ingestion, cleaning, and validation pipeline for fleet sensor telemetry, versioned with DVC |
| 2 | [`task-2/`](./task-2) | Calibrated component-failure prediction model (sklearn Pipeline, CV, recall-weighted metrics) trained on Task 1's dataset |
| 3 | [`task-3/`](./task-3) | MLflow experiment tracking (12 runs) and model registry promotion, plus a single shared rolling-window feature module used identically by training and a mock real-time inference call |
| 4 | [`task-4/`](./task-4) | Fleet Health Risk API (FastAPI) serving Task 3's registered model, with Pydantic boundary validation and structured logging |

## Architecture

```mermaid
flowchart LR
    subgraph T1["Task 1 — Ingest & Validate"]
        raw[("Raw telemetry\nAI4I 2020 CSV")] --> clean[clean.py]
        clean --> validate[validate.py\npandera schema]
        validate --> cleaned[("cleaned.csv")]
    end

    cleaned -- "DVC (Floci S3)" --> T2 & T3

    subgraph T2["Task 2 — Train baseline"]
        pipe2[sklearn Pipeline\nscale + calibrate] --> model2[("model.joblib")]
    end

    subgraph T3["Task 3 — Track & Register"]
        feat[["features.py\nFeatureComputer\n(shared, one source of truth)"]]
        cleaned --> loadd[load_data.py] --> feat
        feat --> runs[run_experiments.py\n12 MLflow runs]
        runs --> promote[promote.py]
        promote --> registry[("MLflow Registry\nalias: production")]
    end

    subgraph T4["Task 4 — Serve"]
        api[FastAPI\n/predict /health]
        api --> loader[model_registry.py] --> registry
        api --> feat2["shared_features.py\n(imports Task 3's FeatureComputer)"]
        feat -.->|"same class,\nno duplicated math"| feat2
    end

    client(["Fleet-ops dashboard"]) -- "POST /predict\ntelemetry window" --> api
    api -- "risk score +\nrecommended action" --> client
```

Each task hands the next one a durable artifact, not a shared in-process
object: Task 1 hands Task 2/3 a DVC-tracked CSV, Task 3 hands Task 4 an
MLflow registry alias. The one thing that *is* shared directly, by
import rather than by artifact, is `features.py` — Task 4 loads Task 3's
copy by file path (see `task-4/src/shared_features.py`) so there is
exactly one implementation of the rolling-window math, used identically
whether it's replaying historical training data or scoring a live
request.

## Setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install pandas pandera great-expectations dvc dvc-s3 mlflow fastapi uvicorn
```

DVC is configured against a Floci-emulated S3 bucket (`s3://fleetpulse-dvc`) as its
remote — `dvc push`/`dvc pull` behave exactly as they would against real AWS S3, no
account or billing involved. Requires `eval $(floci env)` in your shell (see each task's
README for exact commands).
