# FleetPulse — Task 2: Component Failure Prediction

Task 2 of 30 in the [production AI/ML roadmap](../../30-day-ai-ml-roadmap-industry-portfolio.md),
FleetPulse phase. Task 1 built a reproducible, validated, DVC-versioned
telemetry dataset ([task-1](../task-1/)); Task 2 trains the first model on
top of it: predicting the probability a given component fails, framed as
the real fleet-ops decision it is, not a leaderboard exercise.

## What this is

A model that scores each machine's current sensor snapshot with a
failure probability and turns that into a service-or-don't decision. The
dataset (UCI AI4I 2020, carried over from Task 1) is 10,000 industrial
machine snapshots with a binary `machine_failure` label and 5 failure-mode
flags (`twf`, `hdf`, `pwf`, `osf`, `rnf`) recording *which* failure
occurred.

**Honest limitation, same spirit as Task 1's:** this dataset has no
censored time-to-event data, it's per-unit snapshots, not a run-to-failure
time series. So "predict failure within the next N operating hours" is
operationalized here as: predict `machine_failure` from the current sensor
state, where `tool_wear_min` (elapsed tool-operating minutes since
service) is the closest available proxy for "how many operating hours has
this component already accumulated." That's a property of this dataset,
not a shortcut in the modeling code, a real telemetry time series
(Day 3's rolling-window features) would let this become a genuine
survival/time-to-failure formulation.

## Build

- **`src/load_data.py`** — loads Task 1's cleaned, validated CSV directly
  (no re-cleaning, no re-validation, that's Task 1's job and it's already
  proven). Defines the feature set and, just as importantly, names and
  excludes `twf/hdf/pwf/osf/rnf` as `LEAKY_FAILURE_MODE_COLUMNS`: these
  are how the label decomposes, not predictors available before a
  failure (see the break-it demo below).
- **`src/pipeline.py`** — one sklearn `Pipeline`: a `ColumnTransformer`
  (`StandardScaler` on sensor readings, `OneHotEncoder` on machine type)
  feeding a `RandomForestClassifier(class_weight="balanced")`.
  Preprocessing and the estimator are bundled together specifically so
  cross-validation refits preprocessing inside each fold, not once on the
  whole training set beforehand, which is a real leakage path, not a
  theoretical one.
- **`src/train.py`** — the one command
  (`python -m src.train`, wired into `dvc.yaml`): stratified 60/20/20
  train/validation/test split, 5-fold stratified cross-validation on the
  training set only, `CalibratedClassifierCV` (sigmoid/Platt scaling) for
  probability calibration, a validation-set threshold search that
  maximizes F2 (see metric choice below), and a single, final,
  never-touched-until-now evaluation on the test set.
- **`src/evaluate.py`** — metric computation (PR-AUC, ROC-AUC, F2,
  recall, precision, Brier score, confusion matrix) and the human-readable
  evaluation report.
- **`src/break_it.py`** — three deliberate failures, see below.
- **`dvc.yaml`/`dvc.lock`** — one stage, `train`, depending on Task 1's
  `cleaned.csv` (via a relative cross-directory path, DVC resolves it
  through Task 1's own `dvc.yaml`/lock) and all of task-2's `src/*.py`;
  producing `models/model.joblib`, `reports/metrics.json`, and
  `reports/evaluation_report.md` as tracked, checksum-verified outputs.

## Metric choice: why not accuracy

339 of 10,000 units failed (3.4%). A model that always predicts "no
failure" scores 96.6% accuracy while catching zero real failures,
accuracy would reward doing nothing. The two errors this model can make
are not symmetric in cost:

- **False negative** (model says fine, component actually fails): a
  missed failure that surfaces as a roadside breakdown or a safety
  incident. Expensive, unpredictable, and potentially dangerous.
- **False positive** (model flags a healthy component): one unnecessary
  service visit. A real cost, technician time and vehicle downtime, but
  bounded, predictable, and not a safety event.

That asymmetry is why this project optimizes for **recall-weighted
scoring**, concretely:

- **PR-AUC (average precision)** as the threshold-independent metric for
  model selection and cross-validation. Under 3.4% base-rate imbalance,
  ROC-AUC is misleadingly optimistic (the huge true-negative count
  dominates it, test-set ROC-AUC here is 0.966 while PR-AUC is a more
  sober 0.614, both are reported, but PR-AUC is what actually drove model
  and threshold choices). PR-AUC is sensitive to exactly the thing that
  matters: how well the model ranks the rare positive class.
- **F2 score** (recall weighted 4x precision) to pick the operating
  threshold on the validation set. F-beta with beta=2 encodes the same
  cost asymmetry as a single tunable number: it explicitly costs more to
  miss a failure than to raise a false alarm, which is the actual
  fleet-ops tradeoff, not an arbitrary modeling preference.

Accuracy is not reported as a headline number anywhere in the evaluation
report, on purpose, it would be actively misleading here.

## Probability calibration

A `RandomForestClassifier`'s raw `predict_proba` output is a vote
fraction across trees, not a real probability, forest probabilities are
well documented to cluster away from 0 and 1 and to be systematically
overconfident or underconfident depending on region. That matters here
because the eventual consumer (Day 4's `/predict` API) is meant to return
a calibrated risk score a fleet-ops dashboard can reason about ("this
reading means roughly a 30% failure chance"), not just a ranking.

`CalibratedClassifierCV` (sigmoid/Platt scaling, `cv=5`) is fit on the
training set, cross-fitting internally so the base classifier is never
calibrated on rows it was trained on. Sigmoid rather than isotonic:
isotonic's extra flexibility needs more calibration data than the ~200
positive training examples here comfortably support, isotonic on this
little data reduces to overfitting the calibration curve itself.

Measured effect (Brier score on the held-out test set, lower is better):
uncalibrated **0.0366** → calibrated **0.0194**, roughly halved. This is
the concrete evidence that calibration wasn't a decorative step, see
`reports/evaluation_report.md` for the exact run.

## Evaluation report

The full metrics, confusion matrix, and plain-language false
negative/false positive explanation are generated fresh by every training
run at `reports/evaluation_report.md` (DVC-tracked, not committed to git,
regenerate it with the reproduce steps below). Headline numbers from the
last run:

- Test PR-AUC: **0.614**, test ROC-AUC: **0.966**
- Chosen threshold (F2-optimal on validation): **0.27**
- Test recall: **70.6%** (48/68 real failures caught), precision: **58.5%**
- False negative rate: **29.4%** (20/68 failures missed), false positive
  rate: **1.8%** (34/1932 healthy units flagged)

In plain terms: at this operating point, roughly 7 in 10 real failures
get caught before they happen, at the cost of about 2 unnecessary service
visits for every 3 failures correctly caught. The threshold is a single
number in `src/train.py`, moving it toward more recall (fewer missed
failures, more false alarms) or more precision (fewer false alarms, more
missed failures) is a fleet-ops capacity decision, not a retraining
decision.

## Break it on purpose

`python -m src.break_it` runs three deliberate failures against this
pipeline and writes up what happened in
[reports/break_it_demo.md](./reports/break_it_demo.md):

1. **Label leak** — train with `twf/hdf/pwf/osf/rnf` included as
   features. PR-AUC jumps from 0.663 to **0.979**, because
   `machine_failure` is literally the logical OR of those columns, the
   model isn't predicting, it's decoding the label from itself. Nothing
   crashes; the number just looks great and would collapse the moment it
   met live telemetry where those flags don't exist yet.
2. **Degenerate split** — shrink to a 40-row slice (2 positives). 5-fold
   `StratifiedKFold` limps through with a `UserWarning` instead of an
   error, and an unlucky train/test split can silently produce a
   single-class test set where PR-AUC becomes `nan`, no crash, no
   warning, just a number that looks real and isn't.
3. **Bad input** — a row with an unseen `type` category and a `NaN`
   sensor reading, fed straight to `predict_proba`. It does not raise,
   `OneHotEncoder(handle_unknown="ignore")` and the forest's NaN-routing
   both fail silently and hand back a confident-looking probability. This
   is exactly why Day 4 puts a Pydantic validation boundary in front of
   the model instead of trusting the pipeline to reject bad input itself.

## One thing learned

Going in, "pick a metric that matches the cost asymmetry" felt like it
mostly meant swapping accuracy for AUC. Building the calibration step
made a second, less obvious point concrete: a model can rank failures
well (PR-AUC 0.61, decent separation) while its raw probabilities are
still wrong in an absolute sense, a forest's `predict_proba` output isn't
lying about *order*, it's lying about *magnitude*. Ranking well is what
the training metric (PR-AUC/F2) rewards; giving a fleet-ops dashboard a
number it can act on ("this is a 30% risk, that's an 80% risk") needs the
separate calibration step, and the two aren't the same problem. Halving
the Brier score without moving PR-AUC at all was the proof: calibration
didn't make the model better at distinguishing failures, it made the
model's own stated confidence trustworthy.

## How to reproduce

```bash
# from the repo root, fresh clone
dvc pull                     # fetches task-1's cleaned.csv AND task-2's
                              # model/report cache entries from the
                              # Floci-backed S3 remote
cd fleetpulse/task-2
source ../.venv/bin/activate
dvc repro                    # the one command: load -> split -> CV ->
                              # calibrate -> evaluate -> save
```

`dvc repro` is checksum-aware against both task-2's own `src/*.py` files
and task-1's `cleaned.csv`, change either and it retrains; change neither
and it prints `Stage 'train' didn't change, skipping`.

```bash
python -m src.break_it       # optional: regenerate the break-it demo
```

## Done-when checklist (from the roadmap spec)

- [x] sklearn `Pipeline` bundling preprocessing and estimator (no leakage
      between steps)
- [x] Proper train/validation/test split, stratified, plus 5-fold
      cross-validation on the training set
- [x] Metric matches the cost asymmetry: PR-AUC for model selection, F2
      (recall-weighted) for threshold choice, justified above, not
      accuracy
- [x] Probability calibration (`CalibratedClassifierCV`, sigmoid),
      measured effect on Brier score
- [x] Evaluation report: metrics, confusion matrix, plain-language
      false-negative/false-positive explanation
- [x] Fresh clone + `dvc pull` + one command (`dvc repro`) reproduces the
      trained model and evaluation report — verified above
