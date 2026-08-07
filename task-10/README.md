# FleetPulse, Task 10: Monitor, Detect Drift, Compare Self-Hosted vs CloudWatch

Task 10 of 30 in the [production AI/ML roadmap](../../30-day-ai-ml-roadmap-industry-portfolio.md),
the final day of the FleetPulse phase: make the deployed model observable, catch a real
sensor-distribution shift before a customer does, and run a side-by-side comparison of a
self-hosted Prometheus/Grafana stack against Floci-emulated CloudWatch.

## What was built

- `task-4/src/main.py`'s `/predict` handler now logs the actual scored feature row
  (`air_temperature_k`, `process_temperature_k`, `rotational_speed_rpm`, `torque_nm`,
  `tool_wear_min`) alongside `failure_probability` and `model_version`, not just
  `readings_used` — the drift job needs the real inputs, not a summary. This didn't
  change the response schema, so Task 8's contract tests are untouched.
- `task-10/src/drift_report.py` — the drift job. Reference = a sample of Task 1's
  validated `cleaned.csv`, scored through Task 9's exact baked model (via Task 3's real
  feature pipeline) so a `prediction` column exists on both sides. Current = real
  `prediction_made` events pulled straight from CloudWatch Logs (`/aws/lambda/
  fleetpulse-api`, via Floci — confirmed this works with zero extra plumbing, see
  below), or a local JSONL file as a fallback. One Evidently `DataDriftPreset` run over
  both, boiled down to `drift_score` (share of the 7 compared columns Evidently flagged
  as drifted), pushed to **both** monitoring stacks.
- `task-10/scripts/generate_traffic.py` — sends the same generated telemetry to every
  `--target` given (local API and/or the Lambda Function URL), so one logical invocation
  is traceable in both places. `--shifted` simulates the roadmap's own named scenario:
  cold-weather sensor drift, air/process temperature shifted down toward the schema's
  valid floor (still physically plausible, not a validation-rejection test).
- `task-5/docker-compose.yml` gained a `pushgateway` service (the drift job is a batch
  job, not something Prometheus can scrape directly) and an explicit datasource `uid`
  Prometheus scrapes it. `task-5/prometheus.yml` gained the matching scrape job.
- `task-5/grafana/provisioning/dashboards/json/fleetpulse-observability.json` — one
  dashboard, four required panels (latency p50/p95, throughput, error rate, drift score)
  plus a drift gauge, window-size stat, and status-mix panel.
- `task-5/grafana/provisioning/alerting/drift-alert.yml` — the one required alert:
  fires when `fleetpulse_drift_score >= 0.3`, the same threshold `drift_report.py`'s
  own `--drift-threshold` default uses for `drift_detected`, so "the alert fired" and
  "the drift job called it drift" can never quietly disagree.
- A CloudWatch alarm (`fleetpulse-drift-score-high`, namespace `FleetPulse`, same 0.3
  threshold), created directly via `aws cloudwatch put-metric-alarm` through Floci — see
  "CloudWatch alarm: a real fidelity gap" below for why this isn't Terraform-managed.

## Why CloudWatch Logs needed zero extra plumbing

This was worth confirming before building anything else: Task 9's Lambda already runs
`task-4/src/main.py` unchanged, whose `configure_logging()` (Task 4, still in place)
writes structured JSON to stdout. AWS Lambda ships a function's stdout/stderr to
CloudWatch Logs automatically — no SDK call, no extra IAM beyond what Task 9's module
already grants (`logs:PutLogEvents` scoped to `/aws/lambda/fleetpulse-api:*`). Verified
directly, before writing a line of Task 10 code:

```
$ curl -s -X POST "$FN_URL/predict" -H "Content-Type: application/json" -d '{...}'
{"vehicle_id":"veh-day10-test","failure_probability":0.4486910414659536, ...}

$ aws logs filter-log-events --log-group-name /aws/lambda/fleetpulse-api ...
{"timestamp": "2026-08-07T04:06:33Z", "level": "INFO", "logger": "fleet_health_api",
 "message": "prediction_made", "event": "prediction_made", "vehicle_id": "veh-day10-test",
 "machine_type": "M", "readings_used": 1, "failure_probability": 0.4486910414659536,
 "recommended_action": "schedule_service", "model_version": "20260806T044658Z-10000rows"}
```

Real, unprompted, zero-code-change CloudWatch delivery. The only code change Task 10
made was adding the `features` object to that log line (needed for the drift job, not
for CloudWatch delivery itself, which already worked).

## One drift score, two destinations

The spec explicitly allows CloudWatch Logs *or* a lightweight local store, and asks for
a drift score pushed to CloudWatch as a custom metric "if you can." Rather than run two
separate drift computations (one for Grafana, one for CloudWatch — which would let the
two stacks silently disagree about whether drift happened), `drift_report.py` computes
`drift_score` exactly once and pushes the identical number to a Prometheus Pushgateway
gauge and a CloudWatch `PutMetricData` call in the same run. Whatever "drift" means on
one dashboard, it means the same thing on the other.

## Break-it exercise 1: shifted (winter) sensor data

**Baseline first**, to establish what "no drift" actually looks like at a realistic
window size. A first attempt at `n=60` un-shifted predictions showed `drift_score=0.857`
— alarming, until inspection: Evidently auto-selected **normalized Wasserstein
distance** (threshold 0.1) for the numeric columns here (not a K-S p-value; the exact
test Evidently picks depends on sample size), and a Wasserstein distance computed from
only 60 samples against a 2,000-row reference is genuinely noisy — it doesn't correct
for sample size the way a p-value does. At `n=161` the same un-shifted traffic settled
to a believable baseline:

```json
{
  "drift_score": 0.286, "drifted_column_count": 2, "total_columns": 7,
  "per_column_drift": {
    "air_temperature_k": 0.138, "process_temperature_k": 0.090,
    "rotational_speed_rpm": 0.081, "torque_nm": 0.085, "tool_wear_min": 0.063,
    "prediction": 0.297, "type": 0.024
  },
  "drift_detected": false, "threshold": 0.3,
  "current_window_size": 161, "reference_window_size": 2000
}
```

(full report: [`reports/drift_report_baseline_no_drift.html`](./reports/drift_report_baseline_no_drift.html))
— below threshold, but `prediction` sits right at the edge (0.297), a real, honest
observation: the model's own probability output is a naturally noisier signal to drift-test
than a raw sensor reading, worth a wider window or a dedicated threshold in a real
deployment, not something this exercise smooths over.

**Then the real exercise**: `generate_traffic.py --shifted` against both the local API
and the Lambda Function URL, temperature readings pulled down to 282-296K (still inside
the schema's valid [280,320)/[285,330) range — a genuinely cold morning, not a rejected
request) versus the training data's real observed 295.3-304.5K/305.7-313.8K range. Ran
the drift job immediately after, tight window:

```json
{
  "drift_score": 0.571, "drifted_column_count": 4, "total_columns": 7,
  "per_column_drift": {
    "air_temperature_k": 5.822, "process_temperature_k": 9.632,
    "rotational_speed_rpm": 0.116, "torque_nm": 0.083, "tool_wear_min": 0.099,
    "prediction": 0.387, "type": 0.012
  },
  "drift_detected": true, "threshold": 0.3,
  "current_window_size": 146, "reference_window_size": 2000
}
```

(full report: [`reports/drift_report_shifted_drift_detected.html`](./reports/drift_report_shifted_drift_detected.html))
— `air_temperature_k` and `process_temperature_k` blow past the 0.1 threshold by
50-100x, exactly the columns that were actually shifted, and `prediction` moved too
(0.297 → 0.387): the model genuinely scores cold-morning readings differently, the
"prediction drift" half of the story, not just a coincidence of the raw features moving.

**The drift panel lit up and the alert fired**, both confirmed live, not inferred:

```
$ curl http://localhost:9090/api/v1/query?query=fleetpulse_drift_score
"value": [1786076697.291, "0.5714285714285714"]

$ curl -u admin:admin http://localhost:3000/api/alertmanager/grafana/api/v2/alerts
[{
  "status": {"state": "active"},
  "labels": {"alertname": "FleetPulse: drift score above threshold", ...},
  "annotations": {
    "summary": "FleetPulse drift_score is 0.5714285714285714, at or above the 0.3 alert threshold."
  }
}]
```

And the same drift score landed in CloudWatch through the parallel push:

```
$ aws cloudwatch get-metric-statistics --namespace FleetPulse --metric-name DriftScore \
    --start-time ... --end-time ... --period 60 --statistics Maximum
{"Datapoints": [
  {"Timestamp": "...", "Maximum": 0.8571428571428571},   # the noisy n=60 run
  {"Timestamp": "...", "Maximum": 0.14285714285714285},  # the diluted mixed-window run
  {"Timestamp": "...", "Maximum": 0.5714285714285714}    # the clean shifted-window run
]}
```

One genuine wrong turn along the way, worth keeping in: a second drift run right after
the first shifted batch showed `drift_score=0.143` — *lower* than baseline, on data that
was supposed to be shifted. The `--window-minutes` lookback was wide enough to also pull
in the *previous* baseline batch (300 un-shifted predictions) sent minutes earlier,
diluting 150 shifted rows into a current window of 331 mostly-normal ones. Fixed by
sending a fresh, isolated shifted batch and re-running with a tight 1-minute window
immediately after — a real lesson about batch-job drift detection: the window has to be
scoped tightly enough that it can't quietly average out the thing you're trying to
detect, the same failure mode a badly-sized moving average has anywhere else.

## Break-it exercise 2: kill Prometheus

Stopped `fleetpulse-prometheus-1` for ~110 seconds, generated traffic against the still
running API in the meantime (the API doesn't care whether anyone is scraping it),
confirmed the dashboard's actual query path breaks while it's down:

```
$ curl -u admin:admin ".../api/datasources/proxy/uid/prometheus/api/v1/query?query=up"
HTTP_STATUS:502
```

Restarted, and the result was **not** what a naive mental model predicts. Querying the
`up{job="fleet-health-api"}` series across the whole outage window afterward showed **no
visible gap at all** — every 15s step continuous. Not backfill (a pull-based
system with no queue has no missed samples to replay), and not a bug in this setup:
Prometheus's range-query engine holds a metric's last known sample for up to five
minutes past its last real scrape by default (the "staleness" lookback), so a query
spanning a ~2-minute outage simply keeps returning the last real value it has for every
step inside that window — a genuine absence of new data, but not visible as a gap
unless the query happens to land after the 5-minute staleness window has *also* expired.
A real gap requires an outage longer than 5 minutes; this one, deliberately kept short,
demonstrated the opposite lesson from the one I expected to demonstrate: a short
Prometheus outage can be **completely invisible** on a dashboard unless someone is
watching `up` itself or the outage runs past 5 minutes, worth knowing before trusting a
"looks continuous" Grafana panel as proof nothing was ever down.

## Break-it exercise 3: break a CloudWatch permission/endpoint

Three separate breaks, each showing a different failure shape:

**Wrong CLI credentials** (`AWS_ACCESS_KEY_ID=WRONGKEYID`, garbage secret) against
Floci's `PutMetricData`: **exit 0, no error, metric accepted.** Real AWS would return
`InvalidClientTokenId`/`SignatureDoesNotMatch`. Confirms Floci does not emulate SigV4
signature verification at all — any credentials work, a genuine fidelity gap worth
knowing before assuming a Floci "success" means real IAM would also have allowed it.

**Wrong region** (`--region eu-west-9`, doesn't exist in real AWS): also exit 0,
silently accepted. Same root cause.

**Wrong endpoint override** (`AWS_ENDPOINT_URL` pointed at an unused port): this one
*does* fail the same way it would against a real misconfigured endpoint —
`Could not connect to the endpoint URL` — because this failure mode is a plain TCP
connection failure, not something either Floci or real AWS is involved in emulating.

**A fourth, more realistic break**: narrowed the Lambda execution role's own
`logs:PutLogEvents` resource ARN (in `task-9/terraform/modules/lambda-service/main.tf`)
to point at a *different, nonexistent* log group, applied via Terraform, then invoked
the real function with a unique marker in the payload:

```
$ curl -X POST "$FN_URL/predict" -d '{"vehicle_id": "iam-break-marker-...", ...}'
{"vehicle_id":"iam-break-marker-1786077274", ...}   # still 200 OK

$ aws logs filter-log-events --log-group-name /aws/lambda/fleetpulse-api \
    --filter-pattern "iam-break-marker-1786077274"
{"timestamp": "...", "message": "prediction_made", "vehicle_id": "iam-break-marker-...", ...}
```

The log still landed, despite the role no longer being allowed to write there. This
extends Task 9's own finding (a too-broad policy didn't change observed behavior either)
in the other direction: Floci doesn't enforce IAM authorization on the automatic
Lambda-to-CloudWatch-Logs log-shipping path at all, in either direction. On real AWS,
narrowing this exact statement would produce silent log-delivery failure from the
caller's perspective too — the HTTP response wouldn't change, only a missing log entry
would reveal it — so this Floci gap doesn't misrepresent AWS's actual *shape* of
failure, just skips enforcing it. Reverted via `terraform apply` back to the correct
per-function-scoped ARN, re-verified with a second marker that new invocations kept
landing.

## CloudWatch alarm: a real fidelity gap

`aws cloudwatch put-metric-alarm` on `FleetPulse/DriftScore >= 0.3` succeeds and the
alarm is real and queryable (`describe-alarms` returns it, `set-alarm-state` can
transition it manually). But after pushing multiple real datapoints above threshold and
waiting several minutes, `StateValue` stayed `INSUFFICIENT_DATA` — Floci accepts and
stores CloudWatch Alarms but does not run the periodic metric-evaluation loop real
CloudWatch runs automatically every ~60s. Confirmed this is specifically the *evaluator*
missing, not the alarm resource being broken, by hand-transitioning it:
`aws cloudwatch set-alarm-state --state-value ALARM` worked immediately. This is the
same shape of gap Task 9's README already documented for `tags`/`image_config` not
round-tripping — Floci is real and useful, but young, and this is exactly the kind of
gap worth telling apart from an actual application bug rather than silently working
around it.

## Self-hosted (Prometheus/Grafana) vs CloudWatch (via Floci): the honest comparison

**Easier, self-hosted:**
- Alerting. Grafana's unified alerting, file-provisioned, evaluated the drift-score rule
  correctly the first time the datasource `uid` was right, and gave a real, inspectable
  `Alerting` state within a minute (`/api/alertmanager/.../v2/alerts`). CloudWatch's
  equivalent alarm sat in `INSUFFICIENT_DATA` indefinitely — not because anything was
  configured wrong, but because Floci's alarm evaluator isn't implemented.
- Dashboards as code. One JSON file, one `docker compose restart grafana`, done. No
  IAM, no console clicking, fully reproducible from a fresh clone.
- Immediate, correct error feedback. `docker stop`, a scrape target going down, a bad
  PromQL query — all fail loudly and immediately, in a way that's easy to correlate to
  the actual cause.

**More painful, self-hosted:**
- Batch-job metrics need a Pushgateway, an extra moving part with its own retention
  quirks (it holds the *last* pushed value forever until explicitly deleted, unlike
  Prometheus's own time-series retention — worth knowing before assuming an old drift
  score has "expired").
- The 5-minute staleness lookback (break-it exercise 2) is a real footgun: a dashboard
  that "looks fine" during a short outage isn't proof nothing broke, it's proof the
  outage was short enough to hide.

**Easier, CloudWatch (via Floci):**
- Zero extra infrastructure for the log side — Lambda's stdout shipping "just works,"
  confirmed above, no log agent, no volume mount, no separate service to keep alive.
- One namespace, one `put-metric-data` call, queryable from anywhere with the right
  (or, on Floci, *any*) credentials — no dashboard/datasource provisioning step needed
  just to see a number.

**More painful, CloudWatch (via Floci specifically):**
- No credential or IAM enforcement at all on this Floci build (three separate breaks
  above all either silently succeeded or failed for reasons unrelated to auth). That's
  a testing-environment limitation, not a CloudWatch limitation, but it means this
  local setup cannot be used to validate least-privilege IAM the way Task 9's own
  earlier exercise already had to caveat.
- No working alarm evaluation, today, on Floci. The resource model is right; the
  runtime behavior a real on-call engineer depends on isn't there yet.

**Which I'd pick for a real team**: both, for different halves of the job, which is
exactly what this task set out to prove rather than disprove. CloudWatch (real AWS, not
Floci) for anything that has to be durable and low-maintenance with no extra
infrastructure to run — logs, and metrics/alarms once real AWS's evaluator and IAM are
actually in the loop. Self-hosted Prometheus/Grafana for anything that needs to be
iterated on quickly, as code, with dashboards a whole team can review in a pull request
instead of clicking through a console — which is exactly the FleetPulse drift dashboard
built here. A real production setup would very likely run both, the way this exercise
did: CloudWatch as the durable system of record for a serverless deployment that has no
long-lived process to scrape anyway, self-hosted Grafana as the fast-iteration dashboard
layer on top, fed from whichever source is actually reachable, exactly the pattern this
task's `drift_report.py` already uses.

## Reproduction steps

```bash
cd fleetpulse
pip install -r requirements.txt -r task-10/requirements.txt
eval $(floci env)

# Stack (Task 5's compose, extended with pushgateway + Task 10's dashboard/alert)
cd task-5 && ./run_stack.sh -d && cd ..

# Baseline traffic (both stacks; FN_URL from `terraform output -raw function_url` in task-9/terraform)
python -m task-10.scripts.generate_traffic --target http://localhost:8811 --target "$FN_URL" --count 200 --batch-id baseline

# Drift job against CloudWatch's current window
python -m task-10.src.drift_report --window-minutes 5 --pushgateway localhost:9091

# The break-it exercise
python -m task-10.scripts.generate_traffic --target http://localhost:8811 --target "$FN_URL" --count 150 --shifted --batch-id winter-shift
python -m task-10.src.drift_report --window-minutes 1 --pushgateway localhost:9091

# Grafana: http://localhost:3000 (admin/admin) -> "FleetPulse — Observability (Task 10)"
# Alert state: curl -u admin:admin http://localhost:3000/api/alertmanager/grafana/api/v2/alerts
# CloudWatch:  aws cloudwatch get-metric-statistics --namespace FleetPulse --metric-name DriftScore ...
```

## Done when

- [x] Feeding shifted (winter) sensor data lights up the drift panel: `drift_score`
      went from a 0.286 baseline to 0.571, `drift_detected: true`, confirmed via both
      the Evidently HTML report and a live Prometheus query.
- [x] The alert fires: Grafana's `fleetpulse-drift-score` rule transitioned to `Alerting`
      (`state: active`) within a minute of the shifted push, confirmed via the live
      Alertmanager API, not just the provisioning config.
- [x] The same invocation is findable in both places: `generate_traffic.py` sends
      identical payloads to the local API and the Lambda Function URL in the same call;
      Grafana/Prometheus show it in `http_requests_total`/latency, CloudWatch Logs shows
      the identical `prediction_made` JSON line, confirmed with a unique marker
      `vehicle_id` in the IAM break-it exercise above.
