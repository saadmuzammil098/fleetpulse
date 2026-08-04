# FleetPulse — Task 7: Kubernetes Production Patterns, Then the Same Thing Through the AWS API

Task 7 of 30 in the [production AI/ML roadmap](../../30-day-ai-ml-roadmap-industry-portfolio.md),
FleetPulse phase.

> Roadmap spec (`Day 7 — Kubernetes production patterns, then the same
> thing through the AWS API`): Helm chart with dev/prod values,
> ingress-nginx, liveness/readiness/startup probes, resource limits, an
> HPA load-tested under a synthetic spike, rolling update and rollback.
> Then `aws eks create-cluster` via Floci, `aws eks update-kubeconfig`,
> deploy the same chart. Done when: zero-downtime rolling update,
> one-command rollback, HPA reacts visibly, and the same chart runs on
> both clusters.

**Scope note:** this hardens Task 6's hand-written manifests into a Helm
chart — it doesn't rebuild the API. The one application change in this
task (`task-4/src/main.py`, `task-4/src/schemas.py`) is small and
additive: `api_version` on `/health`, specifically so the rolling-update
demo below could watch a real value flip live instead of inferring the
rollout from pod names.

## What was already installed vs. what this task installed

Checked before installing anything (per the roadmap's own instruction):
`helm`, `kubectl`, `kind`, `aws` CLI, and `floci` were already present.
`hey` and `locust` were not — installed `hey` (`brew install hey`, single
static binary, no server component to run) since a synthetic-spike load
test only needs a request generator, not a distributed load-testing
platform. Neither `ingress-nginx` nor `metrics-server` were installed on
the Kind cluster yet (`helm list -A` was empty); both were installed
fresh via their upstream Helm charts.

## Chart layout

```
task-7/chart/fleet-health-api/
├── Chart.yaml
├── values.yaml        # shared defaults: mount paths, probe mechanics, service port
├── values-dev.yaml     # small footprint, fast HPA, DEBUG logs
├── values-prod.yaml    # more headroom, wider HPA range, standard 300s scale-down window
└── templates/
    ├── namespace.yaml, configmap.yaml, secret.yaml   # ports of task-6's hand-written manifests
    ├── deployment.yaml   # + probes, resources, RollingUpdate strategy
    ├── service.yaml
    ├── ingress.yaml      # new
    └── hpa.yaml          # new
```

`values.yaml` holds everything that's identical in both environments
(the MLflow hostPath's absolute path, probe timing mechanics, the service
port); `values-dev.yaml` / `values-prod.yaml` only override what actually
differs — replica count, resource sizing, HPA bounds, ingress host, log
level — so a diff between the two files is a complete, honest list of
what changes between environments instead of two full copies that drift.

## Probes, tuned to what `/health` actually does

`task-4/src/main.py`'s `/health` **always returns HTTP 200**, even when
the model failed to load (`status: "model_unavailable"` in the body) —
the status code alone can't distinguish healthy from not. So:

- **`livenessProbe`** (httpGet `/health`, any 2xx) only asks "is the
  process alive and answering HTTP at all." Checking `model_loaded` here
  would be actively dangerous — see
  [`reports/break_it_bad_liveness_probe.md`](./reports/break_it_bad_liveness_probe.md)
  for what a liveness probe that's too strict actually does (hint: it
  doesn't fail soft).
- **`readinessProbe`** is an `exec` probe (`python` + `urllib` + `json`,
  both already in the runtime image — no `curl` needed) that specifically
  checks `model_loaded`, because this is the one probe where the
  distinction between "process up" and "model actually loaded" changes
  real behavior: it decides whether the Service sends traffic here.
- **`startupProbe`** exists because `model_registry.load_model()` runs
  inside FastAPI's startup event, which blocks `uvicorn` from accepting
  *any* connection — including `/health` — until the MLflow load
  finishes. A 60s budget (`periodSeconds: 2 × failureThreshold: 30`)
  gives the model load room to finish before liveness's tighter timing
  would flag a still-loading pod as unhealthy.

## Resource requests/limits, sized from real usage, not guesses

First cut of `values-dev.yaml` used placeholder numbers (`cpu: 50m`).
Deploying and running `kubectl top pod` immediately showed idle usage
(37-47m CPU, ~233Mi memory just from the loaded sklearn Pipeline + MLflow
client) already above that request — see
[`reports/break_it_undersized_cpu_request.md`](./reports/break_it_undersized_cpu_request.md)
for the full story, including the HPA scaling to `maxReplicas` from idle
overhead alone before a single real request arrived. Fixed to `120m`
(dev) / `200m` (prod) requests with margin above observed idle draw.

## Ingress

`ingress-nginx` routes `fleetpulse.dev.local` (dev) / `fleetpulse.local`
(prod) to the Service — replacing reliance on `kubectl port-forward` as
the only way in. On Kind, `task-7/kind-config.yaml` adds
`extraPortMappings` for 80/443 plus an `ingress-ready=true` node label
(the hook `ingress-nginx`'s Kind-flavored values look for), so the whole
thing is reachable at plain `http://127.0.0.1` with a `Host` header —
verified end-to-end for both `/health` and `/predict`.

## HPA, load-tested under a synthetic spike

Full writeup: [`reports/load_test.md`](./reports/load_test.md). Short
version: `hey` against `/predict` through the ingress drove CPU
utilization to 334% of the 60% target, the HPA scaled 1→3 pods
(`maxReplicas` in dev) within one reconcile cycle, stayed pinned there
while the synthetic spike continued (demand still exceeded 3-pod
capacity — median latency 5.6s under load, confirming this wasn't a
free scale), and stepped back down (3→2, continuing toward
`minReplicas: 1`) once the load stopped, governed by
`scaleDownStabilizationSeconds` (60s in dev, so the whole cycle is
watchable in a normal session; the k8s-default 300s in prod, to avoid
flapping on real traffic).

## Rolling update and rollback

Full writeup: [`reports/rolling_update_and_rollback.md`](./reports/rolling_update_and_rollback.md).
`maxUnavailable: 0, maxSurge: 1` + a real readiness probe made the
`1.0.0 → 1.1.0` rollout genuinely zero-downtime — a 250ms-interval probe
loop hitting `/health` through the ingress for the whole rollout logged
**zero non-200 responses**, with `api_version` visibly transitioning
`None → 1.1.0` (briefly interleaved during the surge, exactly what
`maxSurge: 1` should look like). `helm rollback fleetpulse 4` brought
back `fleetpulse-api:latest` cleanly (confirmed by `api_version`
disappearing from `/health` again, not just a version-string check).

## Floci-emulated EKS: the same chart, a different control plane

Full writeup: [`reports/eks_floci_deployment.md`](./reports/eks_floci_deployment.md).

```bash
eval $(floci env)
aws eks create-cluster --name fleetpulse --role-arn arn:aws:iam::000000000000:role/eks-service-role \
  --resources-vpc-config subnetIds=subnet-12345,subnet-67890
aws eks update-kubeconfig --name fleetpulse
helm upgrade --install fleetpulse fleet-health-api -f fleet-health-api/values-dev.yaml --set image.tag=1.1.0 --wait
```

**What stayed identical:** the chart — zero template or values changes.
Probes, resources, the HPA, the rolling-update strategy all worked
exactly as designed on `kubectl get nodes` reporting a real
`v1.34.1+k3s1` behind the emulated EKS API.

**What changed — all of it at the node-to-host boundary, none of it in
the chart:** `aws eks create-cluster`/`update-kubeconfig` replacing `kind
create cluster` as the control-plane API; no `extraPortMappings`
equivalent, so reaching the ingress needed `kubectl port-forward` instead
of plain `localhost`; no `extraMounts` equivalent for the MLflow store,
so `task-3/` had to be `docker cp`'d into the k3s container as a one-time
snapshot at the same absolute path (Kind's bind mount is live; this
isn't); and that copy surfaced a real permission bug (`docker cp`
preserves real Linux ownership, unlike Docker Desktop's more permissive
Mac bind-mount behavior) that needed a one-time `chown` inside the
container. None of that is a Kubernetes-layer concern — which is exactly
why the chart itself didn't need to change at all.

## Break-it exercises

1. [**Undersized CPU request**](./reports/break_it_undersized_cpu_request.md) —
   a `50m` guess made the HPA scale to `maxReplicas` from idle overhead
   alone, with zero real traffic. Also surfaced a Helm-vs-HPA
   `spec.replicas` field-ownership conflict on the fix's `helm upgrade`.
2. [**Bad liveness probe**](./reports/break_it_bad_liveness_probe.md) —
   pointed `livenessProbe` at a nonexistent path; kubelet's remedy for
   "unhealthy" (restart) doesn't fix a probe bug, it just repeats it —
   a permanent crash loop, not a one-time restart. Confirms why liveness
   checks the loosest meaningful thing and leaves the strict check to
   readiness.

## How to reproduce

```bash
# Kind, from the repo root
cd task-7
kind create cluster --config kind-config.yaml
docker build -f ../task-5/Dockerfile -t fleetpulse-api:latest ..   # if not already built
kind load docker-image fleetpulse-api:latest --name fleetpulse

helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm repo add metrics-server https://kubernetes-sigs.github.io/metrics-server/
helm install ingress-nginx ingress-nginx/ingress-nginx --namespace ingress-nginx --create-namespace \
  --set controller.hostPort.enabled=true --set controller.service.type=ClusterIP \
  --set 'controller.nodeSelector.ingress-ready=true' \
  --set 'controller.tolerations[0].key=node-role.kubernetes.io/control-plane' \
  --set 'controller.tolerations[0].operator=Exists' --set 'controller.tolerations[0].effect=NoSchedule'
helm install metrics-server metrics-server/metrics-server -n kube-system --set args="{--kubelet-insecure-tls}"

cd chart
helm install fleetpulse fleet-health-api -f fleet-health-api/values-dev.yaml

curl -H "Host: fleetpulse.dev.local" http://127.0.0.1/health
```

```bash
# Floci-backed EKS, same chart
eval $(floci env)
aws eks create-cluster --name fleetpulse --role-arn arn:aws:iam::000000000000:role/eks-service-role \
  --resources-vpc-config subnetIds=subnet-12345,subnet-67890
aws eks update-kubeconfig --name fleetpulse

docker save fleetpulse-api:latest | docker exec -i floci-eks-fleetpulse ctr -n k8s.io images import -
docker cp ../task-3 floci-eks-fleetpulse:"/Users/<you>/Documents/30-day AI plan/fleetpulse/task-3"
docker exec floci-eks-fleetpulse chown -R 999:999 "/Users/<you>/Documents/30-day AI plan/fleetpulse/task-3"

helm install ingress-nginx ingress-nginx/ingress-nginx --namespace ingress-nginx --create-namespace
helm install fleetpulse fleet-health-api -f fleet-health-api/values-dev.yaml

kubectl port-forward -n ingress-nginx svc/ingress-nginx-controller 18080:80 &
curl -H "Host: fleetpulse.dev.local" http://127.0.0.1:18080/health
```

Tear down: `kind delete cluster --name fleetpulse` /
`floci aws stop` (or leave both running — they're independent).

## Done-when checklist (from the roadmap spec)

- [x] Helm chart with dev/prod values, built from Task 6's manifests, not
      from scratch
- [x] ingress-nginx installed and routing real traffic, not just
      port-forward
- [x] Liveness/readiness/startup probes tuned to `/health`'s actual
      (always-200) behavior, not placeholder defaults
- [x] Resource requests/limits sized from measured `kubectl top pod`
      output, corrected after an initial undersized guess broke the HPA
- [x] HPA load-tested with `hey` through the ingress — scale-up (1→3)
      and scale-down (3→2, continuing toward 1) both watched live, not
      just configured
- [x] Zero-downtime rolling update — probed continuously through the
      rollout, zero non-200 responses, `api_version` visibly flipping
- [x] `helm rollback` — previous version (`api_version` absent) confirmed
      back cleanly
- [x] `aws eks create-cluster` / `update-kubeconfig` via Floci, same
      chart deployed with zero template/values changes
- [x] Two deliberate breaks (undersized CPU request, bad liveness probe),
      both documented with real `kubectl`/Helm output, not asserted
