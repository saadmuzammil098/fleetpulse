# FleetPulse

Predictive maintenance and fleet health monitoring for a commercial vehicle fleet — from
raw sensor telemetry ingestion through a monitored, production-style API. Days 1–10 of a
[30-day production AI/ML engineering roadmap](https://github.com/saadmuzammil098), built
entirely against [Floci](https://floci.io) (a local AWS emulator) instead of a real AWS
account, so every AWS-shaped piece of this — S3, and later Lambda/ECR/EKS — runs at $0.

## Days

| Day | Folder | What it is |
|---|---|---|
| 1 | [`day-01/`](./day-01) | Reproducible ingestion, cleaning, and validation pipeline for fleet sensor telemetry, versioned with DVC |

## Setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install pandas pandera great-expectations dvc dvc-s3
```

DVC is configured against a Floci-emulated S3 bucket (`s3://fleetpulse-dvc`) as its
remote — `dvc push`/`dvc pull` behave exactly as they would against real AWS S3, no
account or billing involved. Requires `eval $(floci env)` in your shell (see each day's
README for exact commands).
