# Break it on purpose: undersized CPU request

## Setup

First cut of `values-dev.yaml` set:

```yaml
resources:
  requests:
    cpu: 50m
    memory: 192Mi
  limits:
    cpu: 300m
    memory: 384Mi
```

`50m` was a guess made before ever running the pod under the real chart —
exactly the kind of placeholder the roadmap spec warns against.

## What happened

`helm install` succeeded, the pod passed its startup/readiness probes, and
`kubectl top pod` showed it sitting completely idle — no load test running yet:

```
NAME                               CPU(cores)   MEMORY(bytes)
fleet-health-api-c9df65668-xmqrm   37m          233Mi
```

37m against a 50m *request* is 74% CPU utilization — the HPA (target 60%)
saw that as "over target" and started scaling, with zero real traffic:

```
NAME                 REFERENCE                     TARGETS        MINPODS   MAXPODS   REPLICAS
fleet-health-api   Deployment/fleet-health-api   cpu: 104%/60%   1         3         2
```

It climbed 1 → 2 → 3 (the dev `maxReplicas`) within about two minutes, purely
from idle overhead (sklearn Pipeline resident in memory + MLflow client +
FastAPI's own baseline + probe traffic), and stayed pinned there:

```
$ kubectl get hpa fleet-health-api -n fleetpulse -o yaml
...
    conditions:
    - message: the desired replica count is more than the maximum replica count
      reason: TooManyReplicas
      status: "True"
      type: ScalingLimited
    currentMetrics:
    - resource:
        current:
          averageUtilization: 77
          averageValue: 38m
```

`ScalingLimited: TooManyReplicas` is the tell — the HPA *wanted* to keep
scaling past 3 and couldn't, which is how a follow-up `hey` load test's
CPU numbers turned out to be misleading at first: the pods were already
maxed out on replica count before a single real request arrived (the load
test itself separately failed for an unrelated reason — wrong Host-header
flag, see `load_test.md`).

Memory told the same story more mildly: idle usage (~233Mi) was already
above the 192Mi request, meaning the scheduler's bin-packing math for this
pod was wrong from the start too.

## The fix

Bumped `values-dev.yaml` to `cpu: 120m` / `memory: 256Mi` requests (`400m`
/ `448Mi` limits) — enough headroom that idle usage sits comfortably under
the HPA's 60% target instead of on top of it. After `helm upgrade`, idle
CPU utilization settled at:

```
NAME                 REFERENCE                     TARGETS        MINPODS   MAXPODS   REPLICAS
fleet-health-api   Deployment/fleet-health-api   cpu: 31%/60%   1         3         1
```

and the deployment scaled back down to `minReplicas: 1` on its own.

## Bonus break: Helm vs. the HPA over `spec.replicas`

Applying the fix wasn't as simple as `helm upgrade`. The first attempt failed:

```
Error: UPGRADE FAILED: conflict occurred while applying object
fleetpulse/fleet-health-api apps/v1, Kind=Deployment: Apply failed with 1
conflict: conflict with "kube-controller-manager" with subresource
"scale" using apps/v1: .spec.replicas
```

Server-side apply tracks field ownership per "manager." Once the HPA
exists, `kube-controller-manager` owns `spec.replicas` on the `/scale`
subresource (it had already moved the Deployment to 3 replicas during the
earlier scale-up) — and Helm's chart was still asserting `replicas: {{
.Values.replicaCount }}` on every upgrade, a second manager claiming the
same field. Two managers fighting over one field is a hard conflict, not
a merge.

Fix: `templates/deployment.yaml` now omits `spec.replicas` entirely when
`.Values.hpa.enabled` is true, so Helm only sets an initial replica count
on first install (before the HPA exists to take over) and never touches
it again afterward. This is the standard pattern for any Deployment that
has an HPA attached — the two are not supposed to both own scale.

## Takeaway

An HPA target percentage is only meaningful relative to a request value
that reflects reality. A request set from a guess rather than a
measurement doesn't just risk under-provisioning under load — it can make
the autoscaler react to *nothing*, which is worse than not having an HPA
at all, because it looks like the system is working (pods scaling, dashboards
moving) when it's actually just chasing a bad denominator.
