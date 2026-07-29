# FleetPulse — Task 3: Experiment Tracking, Registry, and a Leak-Proof Feature Pipeline

Task 3 of 30 in the [production AI/ML roadmap](../../30-day-ai-ml-roadmap-industry-portfolio.md),
FleetPulse phase. Task 2 trained one calibrated model and hand-picked its
threshold; Task 3 asks a different question: what happens once there are
a dozen candidate models instead of one, and a live sensor stream instead
of a static CSV? Two problems, one dataset:

1. **Reproducibility** — every training run (params, metrics, artifacts)
   needs to be tracked, not just the winning one, and the winner needs to
   be promoted through the registry for a documented reason, not just a
   leaderboard glance.
2. **Training/serving skew** — the rolling-window sensor features
   (moving averages, rate-of-change) a live telemetry stream would need
   have to be computed by the *exact same code* whether they're feeding
   a training run or a real-time prediction, or the two will quietly
   drift apart.

## What this is

- **`src/features.py`** — the single shared feature module. A
  `FeatureComputer` class holds a bounded rolling-window history buffer
  per stream key and exposes one method, `compute(key, reading)`. Every
  rolling feature anywhere in this project — training or inference —
  goes through this one method. See its docstring for the full
  training/serving-skew argument.
- **`src/load_data.py`** — loads Task 1's cleaned dataset and builds the
  training feature set by *replaying* it through `FeatureComputer`, one
  row at a time, in stream order — not with a separate `pandas.rolling()`
  implementation that happens to produce similar numbers.
- **`src/pipeline.py` / `src/metrics.py`** — a configurable version of
  Task 2's sklearn Pipeline (scaler, hyperparameters, calibration method
  all become knobs), plus an explicit cost-asymmetry metric,
  `expected_cost_per_1000` (see below).
- **`src/run_experiments.py`** — 12 run configs, each logged to MLflow:
  params, validation + test metrics, confusion matrices, and the fitted
  model as an artifact.
- **`src/promote.py`** — selects the run with the lowest validation cost
  (above a recall floor), registers it, and promotes it through
  `staging` → `production` registry aliases, with a written rationale.
- **`src/run_all.py`** — the one command (`dvc.yaml`'s `experiment_tracking`
  stage): resets the local MLflow store, runs all 12 configs, promotes
  the winner.
- **`src/mock_inference.py`** — a mock live telemetry stream: raw
  readings in, one at a time, through the same `FeatureComputer`, scored
  by whatever's aliased `production`.
- **`src/verify_feature_parity.py`** — the actual done-when check: proves
  a cold-start replay of live readings through `FeatureComputer`
  reproduces training's features for that row exactly.
- **`src/break_it.py`** — three deliberate ways this can go wrong, see
  below.

## Honest limitation: there's no real time series here

Same caveat Task 2 named: the UCI AI4I 2020 dataset is 10,000 **per-unit
snapshots** (one row per `unit_id`), not a run-to-failure sensor stream —
there's no timestamp column. To have anything to compute a rolling window
*over*, Task 3 simulates one continuous telemetry stream per machine
`type` (L/M/H), ordering each type's snapshots by `tool_wear_min`
(elapsed operating minutes since service — Task 2's own proxy for "how
long has this component been running"). That's a real, defensible
ordering, but it's a simulation of a stream, not a real one, and the
result below is honest about what that simulation did and didn't buy.

## Metric choice: expected cost per 1000 units

Task 2 encoded the cost asymmetry (missed failure ≫ false alarm)
*indirectly*, through F2 as the threshold-selection metric. Task 3 makes
it an explicit, named number:

```
expected_cost_per_1000 = (5 × false_negatives + 1 × false_positives) / n × 1000
```

FN costs 5x FP — a roadside breakdown vs. one unnecessary service visit,
the same ratio Task 2's README argued in prose. Every run logs this
alongside PR-AUC, F2, recall, precision, and Brier score, but registry
promotion is driven by this number, not by PR-AUC.

## What actually won, and why it's the more interesting result

**`baseline_no_rolling`** (no rolling features at all) won: validation
cost **51.0 per 1000 units**, beating every rolling-feature run (best of
those: 56.5). Full grid in
[`reports/leaderboard.md`](./reports/leaderboard.md); full reasoning in
[`reports/promotion_rationale.md`](./reports/promotion_rationale.md).

The rolling features didn't help, and the "Honest limitation" section
above says why: they're computed over a *synthetic* ordering
(`tool_wear_min` within `type`), not a real sensor sequence, so their
"rate of change" and "moving average" are ordering artifacts, not
physical trends — noise the forest has to spend splits filtering out
rather than signal it can use. This is a real, measured finding, not a
placeholder — a real telemetry stream (true timestamps, true per-vehicle
history) is exactly what would make these features earn their keep, and
that gap is precisely Task 2's original caveat about this dataset made
concrete by an actual number.

This does **not** make the feature module or the parity check moot: the
promotion decision (which run wins) and the feature-computation code path
(what a live reading goes through) are two separate concerns. Every run,
rolling features or not, went through the same `FeatureComputer`, and
`mock_inference.py` always computes the full feature vector and lets the
model's own `ColumnTransformer` select what it was actually fit on — the
same as any real feature-store-backed serving system would.

The registry rationale itself also isn't just "lowest number wins": the
highest-PR-AUC run in the grid (`no_class_weight`, PR-AUC 0.616) has the
*worst* cost in the whole leaderboard (74.5 per 1000) because dropping
`class_weight="balanced"` trades away recall for precision — fewer false
alarms, more missed failures, exactly the wrong direction for this cost
ratio. That's the promotion rationale in one sentence: rank well ≠ decide
well, the same gap Task 2's calibration section found between rank order
and probability magnitude.

## Registry mechanics

MLflow's registry "stage" API (`Staging`/`Production` strings) is
deprecated as of MLflow 2.9 in favor of aliases. This uses aliases named
`staging` and `production` instead — the direct modern equivalent of the
promotion workflow the roadmap spec describes, without the deprecation
warning. A promotion gate (validation recall ≥ 50%) has to pass before a
run gets the `production` alias, not just `staging` — a low cost number
achieved by barely flagging anything shouldn't reach production traffic,
and cost-per-1000 alone can't catch that on its own.

## Break it on purpose

`python -m src.break_it` runs three deliberate failures against the
shared feature module and writes up what happened in
[`reports/break_it_demo.md`](./reports/break_it_demo.md):

1. **Malformed live reading** — a reading missing `torque_nm` (sensor
   dropout, or an upstream rename this call site never caught) raises
   immediately, at the feature boundary, instead of silently propagating
   a wrong prediction.
2. **Cold-start buffer vs. a warmed-up training window** — a serving
   process that starts a fresh, empty `FeatureComputer` buffer instead of
   seeding it from a stream's recent history (e.g. a pod restart) scores
   the same reading differently: 20 of 25 rolling features diverged in
   this run. Nothing crashes — every number returned is a valid rolling
   statistic, just of a different, shorter window than training ever saw
   at that stream position. This is the quiet kind of skew, not the loud
   kind.
3. **A second implementation, written with good intentions** — an
   ungrouped `pandas.rolling()` applied straight to the ordered dataframe
   (forgetting that consecutive rows can belong to different machine
   types) silently blends readings across a type boundary: 43.50 vs.
   43.26 for the same reading, no error, no warning. This scenario isn't
   wired into any real call site — it exists to make concrete exactly
   what having one shared module, keyed correctly per stream, prevents.

## How to reproduce

```bash
# from the repo root, fresh clone
dvc pull                     # fetches task-1's cleaned.csv from the
                              # Floci-backed S3 remote
cd task-3
source ../.venv/bin/activate
pip install mlflow           # not yet in the shared venv as of Task 2
dvc repro                    # the one command: reset MLflow store ->
                              # train + log 12 runs -> promote the best
```

`dvc repro` resets `mlflow.db` and `mlartifacts/` before training, so
re-running always reproduces the same 12 named runs and the same
promotion decision (every config uses `random_state=42`) — not an
ever-growing pile of duplicate runs. MLflow assigns each run and each
internal model object a fresh random ID on every run, so `mlartifacts/`
isn't byte-identical run to run even though the logged params, metrics,
and promotion outcome are.

```bash
python -m src.mock_inference          # simulate a live telemetry stream
python -m src.verify_feature_parity   # the done-when check
python -m src.break_it                # regenerate the break-it demo
```

To browse the tracked runs and registry state in MLflow's UI:

```bash
mlflow ui --backend-store-uri sqlite:///mlflow.db
```

## One thing learned

Going in, "one shared feature module" felt like the whole assignment —
write the rolling-window math once, import it twice, done. Running the
actual grid surfaced a second, less obvious lesson: *which* run wins
promotion and *whether the feature module gets exercised* are
independent questions, and conflating them would have been a mistake.
The instinct, watching `baseline_no_rolling` win, was to feel like the
rolling features "didn't work" and the day's core deliverable had failed
to prove itself. It hadn't — the module's job is to guarantee training
and serving compute rolling features identically whenever a model uses
them, not to guarantee that features help on every dataset they're
computed for. Decoupling "does this feature help this model" from "is
this feature computed correctly and identically everywhere it's used" is
the same discipline Task 2's calibration section pointed at from a
different angle: a good ranking metric and a good probability are two
different properties of a model, and here, a clean feature pipeline and
a feature that turns out to help are two different properties of a
pipeline. The `expected_cost_per_1000` number is what caught this
cleanly — without it, `no_class_weight`'s 0.616 PR-AUC would have looked
like the obvious winner, precision-shy of everything Task 2 argued.

## Done-when checklist (from the roadmap spec)

- [x] 10+ runs logged in MLflow (12 tracked) with varying hyperparameters
      and preprocessing choices, params + metrics + artifacts each
- [x] Best run promoted through registry stages (`staging` → `production`
      aliases) with a written rationale tied to the cost-asymmetry
      metric, not just the highest score — see
      `reports/promotion_rationale.md`
- [x] One shared module (`src/features.py`) for rolling-window sensor
      features, imported by both training (`src/load_data.py`) and mock
      real-time inference (`src/mock_inference.py`) — no duplicated
      feature logic anywhere else in the repo
- [x] `src/verify_feature_parity.py` confirms training-time features and
      a simulated live reading's features are bit-identical for the same
      raw input
- [x] Fresh clone + `dvc pull` + one command (`dvc repro`) reproduces the
      MLflow run history and registry state — verified above
