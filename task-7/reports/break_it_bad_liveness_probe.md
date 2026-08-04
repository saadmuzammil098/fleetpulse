# Break it on purpose: bad liveness probe

## Setup

Live-patched the running Deployment (not the chart — a deliberate,
temporary sabotage of a working system, reverted afterward) to point
`livenessProbe` at a path that doesn't exist and made it fail fast:

```
kubectl patch deployment fleet-health-api -n fleetpulse --type='json' -p='[
  {"op":"replace","path":"/spec/template/spec/containers/0/livenessProbe/httpGet/path","value":"/nonexistent-endpoint"},
  {"op":"replace","path":"/spec/template/spec/containers/0/livenessProbe/failureThreshold","value":1},
  {"op":"replace","path":"/spec/template/spec/containers/0/livenessProbe/periodSeconds","value":3}
]'
```

## What happened

The patch changed the pod template, so it also triggered its own rolling
update (new ReplicaSet hash) — but every pod that came up, old or new,
immediately started failing its liveness check against a path that
returns 404:

```
$ kubectl get pods -n fleetpulse
NAME                                RESTARTS
fleet-health-api-6c55d846c7-hwdns   3 (3s ago)
fleet-health-api-6c55d846c7-rq7wb   2 (7s ago)
```

Events (`kubectl describe pod`) show the actual mechanism, not just the
restart count:

```
Warning  Unhealthy  kubelet  Liveness probe failed: HTTP probe failed with statuscode: 404
Normal   Killing    kubelet  Container fleet-health-api failed liveness probe, will be restarted
Normal   Started    kubelet  Container started
Warning  Unhealthy  kubelet  Liveness probe failed: HTTP probe failed with statuscode: 404
...(repeats)
```

kubelet doesn't consult the Deployment or ReplicaSet controller to decide
this — restarting a container that fails its liveness probe is entirely
kubelet's own local decision on the node, the same "narrow, independent
controller" pattern task-6/README.md's self-healing writeup already
found for pod replacement, one level lower (container restart vs. pod
replacement). Because `maxUnavailable: 0` also applies during this
self-inflicted rollout, and the new pods could never pass their
`startupProbe` (which hits the *correct* `/health` path and did pass) but
then immediately fell over on liveness, the Deployment was stuck cycling
pods that came up healthy-ish and got killed within a few seconds —
a permanent crash loop, not a one-time restart, because the probe
misconfiguration itself never went away on its own.

The HPA noticed too, indirectly:

```
Warning  FailedGetResourceMetric  horizontalpodautoscaler/fleet-health-api
  failed to get cpu utilization: did not receive metrics for targeted pods (pods might be unready)
```

A crash-looping pod has no stable CPU metric to report, so the HPA's own
scaling decisions degrade right alongside the direct symptom.

## Why this is the right split of responsibilities (and the actual risk)

This is exactly the failure mode `deployment.yaml`'s comment about
liveness vs. readiness warns about, from the other direction: a
liveness probe that's too strict (or, here, flatly wrong) doesn't fail
soft — it actively prevents recovery, because kubelet's response to
"unhealthy" is "restart," and a restarted process hits the exact same
broken probe again. A readiness-only failure would have just pulled the
pod out of the Service's endpoints and left it alone to recover or be
debugged; a liveness failure keeps killing it. This is why the chart's
real `livenessProbe` (see `deployment.yaml`) deliberately checks only
"does the process answer HTTP at all" (`/health`, any 200) rather than
anything stricter — the failure mode of a liveness probe with a bug in
it is much worse than a readiness probe with the same bug.

## The fix

`kubectl delete deployment fleet-health-api -n fleetpulse` followed by
`helm upgrade` — a straight `helm upgrade` back to the correct chart
values hit a *different* conflict first (`kubectl patch` had taken field
ownership of the three probe fields it touched via server-side apply's
field-manager tracking, the same category of conflict documented in
`break_it_undersized_cpu_request.md` for `spec.replicas`, just on
different fields). Deleting the Deployment object outright removed the
conflicting field manager along with it, and the next `helm upgrade`
recreated it clean, with the chart's correct probes, in ~15s:

```
$ kubectl get pods -n fleetpulse
NAME                                RESTARTS
fleet-health-api-b985fbc8c-4w9vf    0

$ curl -s -H "Host: fleetpulse.dev.local" http://127.0.0.1/health
{"status":"ok","model_loaded":true, ..., "api_version":"1.1.0"}
```

## Takeaway

Two different lessons stacked on top of each other here: (1) a bad
liveness probe is uniquely dangerous among the three probe types because
kubelet's remedy for "unhealthy" — restart — doesn't fix a probe
misconfiguration, it just repeats it, so liveness should check the
loosest thing that's still meaningful ("is the process alive") and leave
anything stricter to readiness; and (2) `kubectl patch`-ing a
Helm-managed object creates a real field-manager conflict on the next
`helm upgrade`, the same class of problem as the HPA/`spec.replicas`
conflict, just triggered by a human this time instead of a controller.
