# Task 3 Break-It Demo

### 1. Malformed live reading (missing sensor field)

Fed `FeatureComputer.compute` a reading missing `torque_nm` (a plausible sensor dropout, or an upstream field rename this call site never picked up). Result: **raised ValueError: reading for key='M' is missing required sensor columns ['torque_nm']; a live reading must carry the same fields training was built from, see Task 3 README's break-it demo for what happens when it doesn't.**

This is the training/serving-skew failure mode you *want*: loud and immediate, at the feature boundary, instead of a silently wrong prediction three steps downstream. `FeatureComputer` checks for every required sensor column before computing anything, precisely so a shape mismatch between what training assumed and what a live reading actually carries cannot pass through unnoticed.

### 2. Cold-start buffer vs. a warmed-up training window

Same reading (`unit_id=419`, stream type L, 4th reading in its stream), scored two ways:

- **Correct**: a `FeatureComputer` seeded with this stream's actual prior readings (what `mock_inference.py` does, and what training's replay does).
- **Broken**: a `FeatureComputer` that starts with an empty buffer right at this reading — e.g. a serving pod that restarts or a stream handler that doesn't load recent history before serving traffic.

Of 25 rolling features, **20 diverged** between the two.
Example: `air_temperature_k_rollmean_w3` — training/correct-replay value **297.900**, cold-start value **297.400**.

Nothing crashes here — `FeatureComputer` happily returns a number either way, because a 1-reading window is a *valid* input to the same math, just a different one than training ever saw for this stream position. This is exactly the quiet kind of training/serving skew the roadmap spec warns about: the model sees real numbers, in range, that just don't mean what it was trained to expect. It's also why `mock_inference.py` keeps one long-lived `FeatureComputer` per process rather than building a new one per request.

### 3. A second implementation, written with good intentions

This scenario is not wired into the model or any call site — it exists only to show what Task 3's single-module rule is actually preventing. `pandas.Series.rolling(window=5)` applied straight to the ordered dataframe is the obvious, reasonable-looking way to write "5-reading moving average" a second time, e.g. in a notebook or a service that didn't import `features.py` — and it silently forgets that consecutive rows can belong to different streams (machine types).

For the 2nd reading of stream type L, right after the type-H block ends in the ordered dataframe: the shared `FeatureComputer` — which resets its buffer per stream key — gives **43.5000**; the naive ungrouped `pandas.rolling` gives **43.2600**, because it's still averaging in trailing `torque_nm` readings from the H-block. Difference: 0.2400. Nothing crashes, nothing warns, the number is just wrong for this stream. This is the drift a single shared module, keyed correctly per stream, makes structurally impossible instead of merely unlikely.
