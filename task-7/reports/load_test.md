# HPA load test (dev values, Kind cluster)

Tool: `hey` (installed fresh for this task — `brew install hey`; neither
`hey` nor `locust` was already on this machine). Ran through the
ingress-nginx Ingress at `http://127.0.0.1/predict`, not port-forward —
the whole point of adding the Ingress in this task was to have a stable
entry point to load-test against.

## First attempt: silently wrong, worth keeping as a lesson

```
hey -z 120s -c 30 -m POST -H "Host: fleetpulse.dev.local" \
  -H "Content-Type: application/json" -D predict.json http://127.0.0.1/predict
```
```
Status code distribution:
  [404]  1000000 responses
```

`curl -H "Host: ..."` works because curl treats a user-supplied `Host`
header as an override of the wire-level Host. Go's `net/http` (what `hey`
is built on) does not extend the same special-casing to a `Host` header
added via `-H` — the request still goes out with `Request.Host` derived
from the URL, so ingress-nginx never saw `fleetpulse.dev.local` and
served everyone's default 404 backend. `hey` has a dedicated `-host` flag
for exactly this reason. The 1,000,000 responses in 120s at concurrency
30 (all sub-millisecond) is itself a tell — that's ingress-nginx bouncing
requests before they ever reached a pod, not real inference latency.
Fixed by using `-host` instead of `-H "Host: ..."` below.

This run also happened to overlap with the undersized-CPU-request break-it
(`break_it_undersized_cpu_request.md`) — the HPA had already scaled to 3
pods from idle overhead alone before this (broken) load test even started,
which is why its HPA numbers looked like a reaction to load when they
weren't.

## Real run, after fixing both the CPU request and the Host flag

```
hey -z 90s -c 25 -m POST -host fleetpulse.dev.local \
  -H "Content-Type: application/json" -D predict.json http://127.0.0.1/predict
```

Result: 483 real `/predict` responses, all `200`, from a pipeline that
also had to survive `readiness`/`liveness` probe traffic concurrently.
Latency under saturation (3 pods, 25 concurrent clients, `maxReplicas: 3`
acting as a hard ceiling):

```
Latency distribution:
  10%% in 0.0473 secs
  50%% in 5.5925 secs
  90%% in 10.4928 secs
  99%% in 14.0648 secs
```

## HPA reaction, watched live via `kubectl get hpa,pods -n fleetpulse` every 10s

| t (s) | CPU util | replicas | note |
|---|---|---|---|
| 0   | 32%/60%  | 1 | idle baseline, post-fix |
| 10  | 52%/60%  | 1 | traffic starting |
| 40  | 334%/60% | 1→3 | scale-up triggered immediately, two new pods created |
| 50  | 334%/60% | 3 | new pods `Running`, still catching up |
| 70-90 | 205-225%/60% | 3 | pinned at `maxReplicas`, demand still exceeds capacity |
| (load ends ~90s in) | | | |
| +cool-down | 27-57%/60% | 3→2 | scale-down begins once CPU drops, `scaleDownStabilizationSeconds: 60` in effect |
| settled | 57%/60% | 2 | steady state a few minutes after load stopped |

Full raw `hey` output: [`hey_output.txt`](./hey_output.txt).

## What this actually demonstrates

- **Scale-up is fast and correct.** CPU utilization spiking to 334% of
  request against a `60%` target produced an immediate scale-to-max
  decision (1→3) in one reconcile cycle, not a slow ramp.
- **`maxReplicas` is a real ceiling, not a target.** At 3 pods the API
  was still saturated (median latency 5.6s under the synthetic spike) —
  a `dev` value of `maxReplicas: 3` is deliberately small for a fast demo,
  not a real capacity plan; `values-prod.yaml` sets `maxReplicas: 10`.
- **Scale-down is deliberately slower than scale-up**, governed by
  `hpa.scaleDownStabilizationSeconds` (60s in dev, k8s's 300s default in
  prod) — visible above as CPU dropping under target well before replica
  count actually decreased. This is Kubernetes protecting against
  flapping, not lag.
