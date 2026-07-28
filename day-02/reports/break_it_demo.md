# Day 2 Break-It Demo

Three deliberate failures run against the Day 2 training pipeline: a label leak, a degenerate split, and a malformed prediction input. None of these run as part of `dvc repro`, this is a one-off exploration documenting what breaks and why.

## 1. Label leak: training on the failure-mode flags

- PR-AUC **with** `twf, hdf, pwf, osf, rnf` included as features: **0.979**
- PR-AUC on the same split with the real, leak-free feature set: **0.663**

**What happened:** `machine_failure` is defined as the logical OR of `twf`, `hdf`, `pwf`, `osf`, `rnf`, they're recorded *because* a failure happened, not observable before one. Handing them to the model as features lets it near-perfectly reconstruct the label from itself, PR-AUC jumps because the model isn't predicting anything, it's decoding. This is exactly the silent-looking failure mode that makes leakage dangerous: nothing crashes, nothing warns you, the number just looks great and would fall apart the moment it met live telemetry where those flags don't exist yet. `src/load_data.py` names these columns explicitly as `LEAKY_FAILURE_MODE_COLUMNS` and documents why they're excluded, specifically so a future edit can't casually add them back in without seeing the warning.

## 2. Degenerate split: too few positives to stratify

- 40-row random slice of the dataset contains **2 positive examples** (vs. 339 in the full 10,000-row set).
- 5-fold StratifiedKFold on this slice: succeeded.
- Stratified train/test split on this slice succeeded (PR-AUC=0.143), but with only a handful of positives the number is nearly meaningless, one flipped prediction swings it by double-digit points.

**What this shows:** the real `src/train.py` split (10,000 rows, 339 positives, 60/20/20) has enough positives in every split and fold to stay stable. That margin is a property of *this* dataset's size, not something the code enforces. A smaller or more imbalanced slice can push `StratifiedKFold` into raising outright, or push an unlucky split into a single-class test set where PR-AUC silently becomes `nan`, no crash, no warning, just a number that looks like a real score and isn't. Anyone reusing this pipeline on a smaller fleet segment should check `y.value_counts()` before trusting the split.

## 3. Bad input: unseen category + missing sensor reading

- Predicting on a row with `type="X"` (unseen at training time) and `torque_nm=NaN`: **did not raise**, returned probability 0.030.

**What happened:** `OneHotEncoder(handle_unknown="ignore")` silently zeroes out all three type columns for an unseen category instead of erroring, and the downstream `RandomForestClassifier` happily predicts on a NaN torque reading (trees route NaNs down a default branch). Both are *silent* failure modes: an unseen unit type or a dropped sensor reading produces a confident-looking number instead of an error, which is worse for a fleet-ops tool than a hard crash. This is why Day 4's API layer (`Pydantic` schemas that reject physically impossible sensor readings) exists as a separate boundary check, this pipeline alone won't catch it.
