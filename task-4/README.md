# FleetPulse — Task 4: Serve as a Fleet Health Risk API

Task 4 of 30 in the [production AI/ML roadmap](../../30-day-ai-ml-roadmap-industry-portfolio.md),
FleetPulse phase. Task 3 built a registry a model could be promoted
through and a feature module that guaranteed training/serving parity;
Task 4 asks what a fleet-ops dashboard actually gets to call: a real
`/predict` endpoint, not a notebook cell or a mock stream script.

> Roadmap spec (`Day 4 — Serve as a Fleet Health Risk API`): `/predict`
> takes a vehicle's recent telemetry window and returns a failure-risk
> score plus a recommended action (schedule service, urgent alert).
> Build: FastAPI with Pydantic schemas that reject physically impossible
> sensor readings, `/health` and `/predict`, model loaded from the
> registry, structured logging. Done when: an out-of-range sensor reading
> returns a clean 422, and real predictions come from the registered
> model.

## What this is

- **`src/schemas.py`** — `PredictRequest`/`SensorReading` reuse Task 1's
  exact sensor bounds (`task-1/src/clean.py::SENSOR_RANGES`) as Pydantic
  `Field(ge=..., le=...)` constraints, plus a `MachineType` enum for
  `type`. A reading outside the range the model was trained inside, a
  missing field, a wrong type, or a bad machine type never reaches
  feature computation or the model — Pydantic rejects it with a `422`
  before the request handler even runs.
- **`src/shared_features.py`** — imports Task 3's `FeatureComputer`
  straight from `task-3/src/features.py` by file path (every task names
  its package `src`, so a normal package import would collide — see the
  module docstring). No rolling-window math is reimplemented here; this
  is the same constraint Task 3's `load_data.py` already documented one
  task earlier, now crossing a task boundary instead of just a file.
- **`src/model_registry.py`** — loads whatever is currently aliased
  `production` in the `fleetpulse-component-failure` registry (Task 3's
  MLflow store, `task-3/mlflow.db`) once at startup. Nothing is retrained
  or hardcoded; a re-promotion in Task 3 changes what this API serves
  with zero code changes here.
- **`src/inference.py`** — replays a request's `telemetry_window` through
  a fresh `FeatureComputer`, keyed by `type` (the same stream key Task
  3's training data was grouped and ordered by), and scores the most
  recent reading's full feature row (raw sensors + rolling features) —
  exactly the call pattern `task-3/src/mock_inference.py` uses, letting
  the model's own `ColumnTransformer` pick the columns it was actually
  fit on.
- **`src/main.py`** — the FastAPI app: `/health` (liveness + which model
  version is loaded), `/predict`, a request-logging middleware, and a
  `RequestValidationError` handler that logs every rejected request as a
  structured `validation_rejected` event before FastAPI's default 422.
- **`src/logging_config.py`** — one JSON line per log record (timestamp,
  level, event, and whatever fields the call site attached) to stdout,
  not a free-text message — the shape a real log aggregator expects.
- **`run_api.sh`** — the one command.

## Recommended-action thresholds

`failure_probability` is bucketed into three actions:

```
>= 0.60  -> urgent_alert
>= 0.27  -> schedule_service
<  0.27  -> monitor
```

0.27 is not an arbitrary round number — it's Task 2's F2-optimal
classification threshold (`task-2/README.md`), the validated point where
recall is weighted 4x precision for this cost asymmetry. 0.60 is a coarser
second tier layered on top: a fleet-ops team wants a cheap, frequent
`schedule_service` signal and a rare, expensive `urgent_alert` one, and
those are a business call, not a modeling one — moving either number
doesn't require retraining, unlike Task 2's actual classification
threshold.

## Design choice: request-scoped feature window, not a server-side stream

`task-3/src/mock_inference.py` keeps one `FeatureComputer` alive across
multiple calls, simulating a long-running stream. Task 4's `/predict`
does the opposite on purpose: each request gets its own fresh
`FeatureComputer`, warmed up only by the `telemetry_window` the caller
sent in that request body. A stateless API is horizontally scalable and
has no per-vehicle memory to lose on a restart or a routed-to-a-different-
replica request — the tradeoff Task 3's break-it demo #2 named directly
(a cold-start buffer scores differently than a warmed-up one). The cost:
a caller sending only 1-2 readings gets rolling features computed over a
short window, same as any real stream's first few readings would. A
production version of this would very likely move to a real feature
store keyed by `vehicle_id` instead of asking each caller to resend
history — flagged here, not built, since Task 3's registry and feature
module don't have per-vehicle stream state to serve from yet.

## Break it on purpose

Six requests against a running API — two out-of-range sensor values, a
missing field, a wrong type, an invalid machine type, and an empty
telemetry window — all return a clean `422` with a structured Pydantic
error body, logged as `validation_rejected`, never reaching
`FeatureComputer` or the model. Full transcript in
[`reports/break_it_demo.md`](./reports/break_it_demo.md).

## How to reproduce

```bash
# from the repo root, fresh clone
dvc pull                     # fetches task-1's cleaned.csv (task-4 doesn't
                              # need it directly, but task-3's `dvc repro`
                              # does, to build the registry this API reads)
cd task-3 && dvc repro && cd ..   # only needed if mlflow.db/mlartifacts
                                   # aren't already present — task-4 reads
                                   # Task 3's registry, it doesn't build one
cd task-4
./run_api.sh                 # the one command: loads the `production`
                              # model, starts the API on :8811 (override
                              # with PORT=xxxx ./run_api.sh)
```

```bash
curl http://127.0.0.1:8811/health

curl -X POST http://127.0.0.1:8811/predict \
  -H "Content-Type: application/json" \
  -d '{"vehicle_id":"veh-042","type":"M","telemetry_window":[
        {"air_temperature_k":298.1,"process_temperature_k":308.6,
         "rotational_speed_rpm":1551,"torque_nm":42.8,"tool_wear_min":0}
      ]}'
```

## One thing learned

The obvious version of "load the model from the registry" is
`mlflow.sklearn.load_model("models:/name@production")` and stop there —
that's what `mock_inference.py` already did, and it was tempting to just
copy it into a route handler. The part that wasn't obvious until writing
the boundary schema: Task 1's `SENSOR_RANGES` is a training-data
contract, not a validation library either task shares, so "reject
physically impossible sensor readings" meant deliberately re-declaring
those five ranges as Pydantic `Field` constraints rather than importing
them — the same `src`-package-name collision that forced
`shared_features.py` to load Task 3's module by file path would have made
a real import awkward here too, and a duplicated-but-explicit constant is
more honest than a workaround that hides where the numbers actually came
from. The cost is real: if Task 1's ranges ever change, this file has to
change with them, by hand, and nothing enforces that today.

## Done-when checklist (from the roadmap spec)

- [x] FastAPI app with `/predict`: telemetry window in, failure-risk
      score + recommended action out
- [x] Pydantic schemas reject physically impossible sensor readings at
      the boundary (same ranges as Task 1's data contract)
- [x] `/health` for liveness checking (reports whether the model is
      loaded and which registry version)
- [x] Model loaded from the Task 3 MLflow registry (`production` alias),
      not retrained or hardcoded
- [x] Task 3's `FeatureComputer` reused for all feature computation — one
      source of truth for feature math, no duplicated rolling-window code
- [x] Structured (JSON) logging for requests and predictions
- [x] An out-of-range sensor reading returns a clean 422 (six ways,
      `reports/break_it_demo.md`) — real predictions come from the
      registered model, verified end-to-end above
- [x] Fresh clone + `dvc pull` + one command (`./run_api.sh`) gets the
      API running
