# FleetPulse — Day 1: Reproducible Data Pipeline for Fleet Telemetry

Day 1 of 30 in the [production AI/ML roadmap](../../30-day-ai-ml-roadmap-industry-portfolio.md), FleetPulse phase (automotive predictive maintenance, Days 1–10).

## What this is

FleetPulse ingests sensor telemetry from a fleet of vehicles/machines and will eventually
predict component failure risk (that's Day 2 onward). Day 1's job is narrower and more
foundational: get raw sensor data into a **reproducible, validated, versioned** state
before any model ever touches it. See [architecture.md](./architecture.md) for the full
pipeline diagram.

## Dataset: UCI AI4I 2020, not the literally-automotive Kaggle option

The roadmap's Day 1 spec names two "most established run-to-failure sensor benchmarks":
NASA C-MAPSS (aircraft engines) or the **UCI AI4I 2020 Predictive Maintenance** dataset
(10,000 rows of industrial machine sensor readings — air/process temperature, rotational
speed, torque, tool wear — plus binary failure labels and failure-mode flags). Neither is
literally automotive; the spec is explicit that the *methodology* — remaining-useful-life /
failure-risk from sensor time-series — is what transfers, not the domain.

A more literally car-shaped Kaggle dataset was considered and rejected for Day 1
specifically because it requires a Kaggle account and API token, which weren't set up on
this machine, and the roadmap's own fallback guidance for that path is "availability there
shifts, pick whichever is live" — i.e. it's explicitly a lower-priority option. UCI AI4I
2020 needs no account, downloads directly, and is the dataset the spec calls "most
established."

**Honest limitation:** this dataset has no timestamp column — it's per-unit sensor
snapshots, not a telemetry time series, and no missing values or corrupted rows exist in
the raw file. That means the cleaning/validation code below is written to handle those
problems generically (and is proven against them in the break-it demo), but the *real*
run through this dataset never needs to use most of that handling. That's a property of
this specific dataset, not a gap in the pipeline.

## Build

- **`src/ingest.py`** — loads the raw CSV (`utf-8-sig` to handle the BOM UCI ships in the
  header), validates expected columns are present, renames to a stable snake_case working
  schema.
- **`src/clean.py`** — three independent, reportable cleaning steps:
  - `handle_missing_values` — drops rows missing a required sensor reading (no imputation:
    guessing a physical quantity like torque isn't safe without domain modeling).
  - `handle_bad_timestamps` — parses and validates any timestamp-like column; a real
    function, not a stub, because it's a no-op on *this* dataset but will matter on later
    FleetPulse days that carry real telemetry timestamps. Proven against a synthetic
    bad-timestamp fixture in the break-it demo.
  - `handle_outliers` — drops (not clips) sensor readings outside physically plausible
    ranges, derived from the observed data with margin. Clipping would fabricate a
    plausible-looking but fake reading; dropping and reporting is honest about what
    happened.
- **`src/schema.py` + `src/validate.py`** — a Pandera `DataFrameSchema` as the data
  contract (types, nullability, categorical sets, numeric ranges), run with `lazy=True` so
  *all* failures are collected and reported, not just the first one. This is the layer
  that catches structural problems cleaning doesn't, like an invalid category value, see
  the break-it demo.
- **`src/profile.py`** — a custom, dependency-light profiling report (column stats,
  categorical breakdown, failure-label class balance, missingness). Deliberately not
  ydata-profiling/pandas-profiling: those pull in a heavy, version-fragile dependency tree
  for what Day 1 actually needs.
- **`src/pipeline.py`** — orchestrates all of the above; the single command the "done
  when" clause asks for.
- **`src/break_it.py`** — the deliberate-breakage exercise, see below.
- **`dvc.yaml` / `dvc.lock`** — one DVC pipeline stage wrapping `python -m src.pipeline`,
  with the raw CSV, all `src/*.py` files, and the schema as tracked deps, and the cleaned
  dataset + all three reports as tracked outs.

## How to reproduce

```bash
# from the repo root
dvc pull                     # fetches ai4i2020.csv from the Floci-backed S3 remote
cd fleetpulse/day-01
source ../.venv/bin/activate
dvc repro                    # the one command: ingest -> clean -> validate -> profile
```

`dvc repro` is checksum-aware: rerunning it with nothing changed prints `Stage 'pipeline'
didn't change, skipping` instead of re-executing, which is the actual proof of
reproducibility (same deps, same code, same output hash), not just "it ran without
crashing."

## Break it on purpose

`python -m src.break_it` builds a 10-row synthetic fixture: 6 rows each carry one isolated,
deliberate fault (missing value, impossible temperature, negative rotational speed, absurd
tool wear, invalid category, unparseable timestamp), 4 rows are an untouched control group.
Full writeup in [reports/break_it_demo.md](./reports/break_it_demo.md).

What it showed: **cleaning and validation catch different classes of problems.**
Missing values, out-of-range sensor readings, and bad timestamps are all dropped during
*cleaning*, before validation ever runs. The invalid-category row, though, isn't an
outlier or a missing value, structurally it's a complete, well-typed row, so cleaning
correctly leaves it alone. It's the *schema validator* that catches it. Neither layer
alone would have caught everything; together, nothing corrupt reaches the cleaned output.

## One thing learned

Going in, "data validation" and "data cleaning" felt like they'd overlap — a range check
is a range check either way. Building the break-it demo made the actual boundary
concrete: cleaning operates on *values* (is this number plausible, is this timestamp
parseable), validation operates on the *contract* (is this the right type, is this
category one of the allowed ones, are all required fields present). A row can pass every
cleaning check and still violate the contract, which is exactly what happened with the
invalid `Type` value here. That's the real argument for keeping them as two separate
layers instead of one "make the data okay" function: they fail on genuinely different
things.

## Done-when checklist (from the roadmap spec)

- [x] Ingestion script loads raw data
- [x] Cleaning functions handle missing values, bad timestamps, outliers
- [x] Pandera validation: schema checks, null rules, range checks
- [x] Automated profile report
- [x] Cleaned dataset versioned with DVC (Floci S3 remote)
- [x] Fresh clone + `dvc pull` + one command (`dvc repro`) reproduces the exact cleaned
      dataset and validation report — verified below
