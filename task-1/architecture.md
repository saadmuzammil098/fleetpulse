# FleetPulse Task 1 — Architecture

```mermaid
flowchart TD
    subgraph ext["External (one-time, not part of the reproducible pipeline)"]
        uci["UCI ML Repository\nAI4I 2020 dataset"]
    end

    subgraph dvc["DVC-tracked (repo-root .dvc project, Floci S3 remote)"]
        raw["data/raw/ai4i2020.csv\n(ai4i2020.csv.dvc pointer in git)"]
        cleaned["data/processed/cleaned.csv"]
        s3[("Floci-emulated S3\ns3://fleetpulse-dvc/dvcstore")]
    end

    subgraph pipeline["dvc.yaml stage: pipeline  (cmd: python -m src.pipeline)"]
        ingest["src/ingest.py\nload + rename columns"]
        clean["src/clean.py\nmissing values\nbad timestamps (no-op here)\noutlier ranges"]
        schema["src/schema.py\nPandera DataFrameSchema"]
        validate["src/validate.py\nvalidate_dataset()"]
        profile["src/profile.py\ngenerate_profile_report()"]
    end

    subgraph outputs["Outputs (DVC-tracked, git-ignored raw files)"]
        val_report["reports/validation_report.md"]
        prof_report["reports/profile_report.md"]
        clean_json["reports/clean_report.json"]
    end

    subgraph breakit["Break-it demo (src/break_it.py, run separately)"]
        fixture["synthetic 10-row fixture\n6 isolated faults + 4 controls"]
        breakreport["reports/break_it_demo.md"]
    end

    uci -- "one-time curl + dvc add" --> raw
    raw -- dvc push/pull --> s3
    raw --> ingest --> clean --> validate
    schema --> validate
    validate -- "passed" --> cleaned
    validate --> val_report
    cleaned --> profile --> prof_report
    clean --> clean_json
    cleaned -- dvc push/pull --> s3

    fixture --> clean
    fixture -.->|"exercises timestamp cleaner directly"| clean
    validate -.-> breakreport
    clean -.-> breakreport
```

## What's DVC-tracked vs git-tracked

| Path | Tracked by | Why |
|---|---|---|
| `data/raw/ai4i2020.csv` | DVC (`.dvc` pointer in git) | Real data, shouldn't bloat git history |
| `data/processed/cleaned.csv` | DVC (pipeline output) | Regenerated deterministically, but pinned by `dvc.lock` for the "done when" reproducibility check |
| `reports/*.md`, `reports/*.json` | DVC (pipeline output) | Same reasoning as cleaned.csv |
| `src/*.py`, `dvc.yaml`, `dvc.lock`, `*.dvc` pointer files | git | Code and pipeline definition |
| `.dvc/config` (remote URL, endpoint) | git, at repo root | Shared across all 5 roadmap projects |

## Why the DVC remote is Floci S3, not local

`dvc remote add -d floci-s3 s3://fleetpulse-dvc/dvcstore` points at a bucket created via
`aws s3 mb` against Floci, with `endpointurl` set to Floci's local endpoint. This makes
`dvc push` / `dvc pull` behave exactly like they would against real AWS S3 (same boto3
client, same auth flow, same commands), which is the roadmap's stated habit: "treat
Floci like real AWS in every way that matters." It also means the reproducibility test
(fresh clone + `dvc pull` + one command) is a genuine test of remote storage retrieval,
not just reading a file that was always on disk.
