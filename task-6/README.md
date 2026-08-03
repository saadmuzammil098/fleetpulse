# FleetPulse — Task 6: Kubernetes Fundamentals, By Hand

Task 6 of 30 in the [production AI/ML roadmap](../../30-day-ai-ml-roadmap-industry-portfolio.md),
FleetPulse phase.

> Roadmap spec (`Day 6 — Kubernetes fundamentals, entirely on your own
> machine`): Kind or Minikube, hand-written Deployment/Service/ConfigMap/
> Secret manifests for the Fleet Health Risk API, `kubectl` fluency, kill
> a pod on purpose. Done when: you can narrate `kubectl apply` to a
> running pod step by step, and you've watched self-healing happen.

**Scope note:** this is a deployment exercise, not a new service — same
constraint as Task 5. The Task 5 image (`fleetpulse-api:latest`) is
reused, not rebuilt from scratch; the only source change in this task is
one small, additive one (see "One small Task 4 change" below), and the
image was rebuilt from the *existing* `task-5/Dockerfile` to pick that up
— no new Dockerfile, no new build pipeline. No Helm, no generators
(`kubectl create deployment`, etc.) — every manifest in `manifests/` is
hand-written. Resource requests/limits, liveness/readiness/startup
probes, and an HPA are deliberately out of scope: the roadmap puts those
in Day 7 ("Kubernetes production patterns"), and Task 6 is about the bare
Deployment → ReplicaSet → Pod mechanics, not production hardening yet.

## What was built

- **`kind-config.yaml`** — a single-node Kind cluster config with an
  `extraMounts` entry that bind-mounts `task-3/` (MLflow's `mlflow.db` +
  `mlartifacts/`) into the Kind node at the same absolute host path it
  lives at on the Mac. Required for the same reason `task-5/README.md`
  documents for docker-compose: MLflow's local file-based artifact store
  bakes an absolute host path into `mlflow.db` at logging time, so
  wherever this data ends up mounted has to match that baked-in path
  exactly. Kind nodes are themselves containers with no access to the
  Mac's filesystem by default — this config is what gives the node (and
  from there, the pod) that access at all.
- **`manifests/00-namespace.yaml`** — a dedicated `fleetpulse` namespace.
- **`manifests/01-configmap.yaml`** — `FLEETPULSE_TASK3_ROOT` (real,
  load-bearing — `task-4/src/config.py` reads it) and `LOG_LEVEL` (new in
  this task, see below).
- **`manifests/02-secret.yaml`** — `FLEETPULSE_MLFLOW_TRACKING_TOKEN`, a
  placeholder value wired through properly (envFrom, base64-in-etcd, the
  whole mechanism) even though the API doesn't consume it yet. Named for
  a specific, already-documented future need (task-5/README.md flags that
  a real deployment would eventually swap the bare local MLflow store for
  a remote tracking server, which is the point an auth token becomes
  real), not invented from nothing.
- **`manifests/03-deployment.yaml`** — `replicas: 2` of
  `fleetpulse-api:latest`, `imagePullPolicy: IfNotPresent` (the image was
  loaded straight into the Kind node, never pushed anywhere kubelet could
  pull it from), `envFrom` pulling in both the ConfigMap and the Secret,
  and a `hostPath` volume mounting `task-3/` at the same absolute path
  the ConfigMap points at — same absolute-path constraint as the Kind
  config, one level down.
- **`manifests/04-service.yaml`** — `ClusterIP`, port `8811`. No ingress
  yet.
- **One small Task 4 change**: `task-4/src/logging_config.py`'s
  `configure_logging()` now reads `LOG_LEVEL` from the environment
  (defaulting to `INFO` if unset, identical to the old hardcoded
  behavior) instead of always hardcoding `INFO`. Added specifically so
  the `LOG_LEVEL` key in `01-configmap.yaml` is real, operator-facing
  config the API actually consumes — not a ConfigMap key sitting next to
  code that ignores it.

## What happens between `kubectl apply -f manifests/` and a running pod

Applied in filename order (`00-` through `04-`), though `kubectl apply
-f manifests/` doesn't strictly require that — the API server queues
dependent objects rather than rejecting out-of-order creation; the
numbering is for a human reading the directory, not for `kubectl`.

1. **`kubectl apply` talks to the API server, not the cluster directly.**
   Each manifest is a `POST`/`PATCH` against the Kubernetes API — `kubectl`
   itself does no scheduling, no container work. Once the API server
   accepts an object, it's written to `etcd` and `kubectl apply` returns
   immediately (`namespace/fleetpulse created`, etc.) — "created" here
   means "etcd now has this object," not "this object is running."
2. **The Namespace, ConfigMap, and Secret are inert data** the moment
   they're created — nothing watches them proactively yet. They just sit
   in etcd until something references them.
3. **The Deployment is where a controller actually reacts.** The
   deployment controller (running inside the control plane, not something
   I invoked) notices a new Deployment object with no matching ReplicaSet
   and creates one, stamping it with a hash of the pod template
   (`58dc4dfffb` in this run) so that changing the template later
   produces a *new* ReplicaSet rather than mutating pods in place — that
   hash is the whole rolling-update mechanism, one layer down.
4. **The ReplicaSet controller takes over from there.** It sees
   `replicas: 2` and zero matching pods, so it creates two Pod objects
   (again, just API objects — no container is running yet). This is the
   same loop that later re-creates a pod after a manual delete (see the
   self-healing test) — a ReplicaSet doesn't distinguish "pod count
   dropped because I just created it" from "pod count dropped because
   someone deleted one," it just continuously reconciles toward
   `replicas`.
5. **The scheduler assigns each Pod to a node.** With one node in this
   cluster the decision is trivial, but it's a real, separate step — the
   Pod object goes from unscheduled to carrying a `nodeName`.
6. **kubelet on that node is what finally does something a human would
   call "running."** It sees a Pod object assigned to its node, pulls (or,
   here, finds already-present) the image, creates the container with the
   env and volumes the PodSpec describes, and starts it. This is the
   `Pulled` / `Created` / `Started` sequence visible in `kubectl describe
   pod`'s Events.
7. **Only now does application code run** — `uvicorn` starts,
   `model_registry.load_model()` fires on FastAPI's startup event, and the
   structured `model_loaded` log line appears. Everything before this
   step was Kubernetes machinery; this step is the first point where
   Task 4's own code has any say in whether the pod is actually healthy
   (it doesn't yet, in this task — no readiness probe means kubelet marks
   the container `Ready` as soon as the process starts, not once
   `/health` says so; that gap is exactly what Day 7's probes close).
8. **The Service was ready to route the instant a matching pod went
   `Ready`.** `manifests/04-service.yaml`'s `selector: app:
   fleet-health-api` isn't resolved once at creation time — it's a live
   query the Service continuously re-evaluates, which is also why the
   self-healing test below needed no changes to the Service at all.

Verified end-to-end, not just asserted from the manifests:

```
$ kubectl get deployments,pods,services -n fleetpulse -o wide
NAME                                READY   UP-TO-DATE   AVAILABLE   AGE
deployment.apps/fleet-health-api   2/2     2            2           7s

NAME                                    READY   STATUS    RESTARTS   AGE
pod/fleet-health-api-58dc4dfffb-s7fhh   1/1     Running   0          7s
pod/fleet-health-api-58dc4dfffb-vg6j5   1/1     Running   0          7s

NAME                       TYPE        CLUSTER-IP    PORT(S)
service/fleet-health-api   ClusterIP   10.96.87.70   8811/TCP
```

```
$ kubectl exec -n fleetpulse fleet-health-api-58dc4dfffb-s7fhh -- \
    sh -c 'id; echo $LOG_LEVEL; echo $FLEETPULSE_MLFLOW_TRACKING_TOKEN'
uid=999(fleetpulse) gid=999(fleetpulse) groups=999(fleetpulse)
INFO
placeholder-not-a-real-token
```

Non-root user carried over from the Task 5 image unchanged; ConfigMap and
Secret both actually present inside the running container's environment.

```
$ kubectl port-forward -n fleetpulse svc/fleet-health-api 18811:8811 &
$ curl -s http://127.0.0.1:18811/health
{"status":"ok","model_loaded":true,"model_name":"fleetpulse-component-failure","model_version":"1"}

$ curl -s -X POST http://127.0.0.1:18811/predict -H "Content-Type: application/json" -d '{
  "vehicle_id":"veh-042","type":"M","telemetry_window":[
    {"air_temperature_k":298.1,"process_temperature_k":308.6,
     "rotational_speed_rpm":1551,"torque_nm":42.8,"tool_wear_min":0}]}'
{"vehicle_id":"veh-042","failure_probability":0.00248...,"recommended_action":"monitor","model_name":"fleetpulse-component-failure","model_version":"1","readings_used":1}
```

`port-forward` opens a tunnel from a local port through the API server to
a specific pod (or, here, a Service, which the API server resolves to a
live endpoint) — no NodePort, no ingress, nothing exposed outside the
cluster, which is the whole point of `ClusterIP` at this stage.

## Break it on purpose: deleting a pod

Full transcript with commentary: [`reports/self_healing_demo.md`](./reports/self_healing_demo.md).
Short version: `kubectl delete pod fleet-health-api-58dc4dfffb-s7fhh`
returned instantly, and a replacement pod (`...bf9rr`, a new Pod object,
new IP, same ReplicaSet hash) was already `1/1 Running` within one
second — the deploy's `SuccessfulCreate` event on the ReplicaSet (not the
Deployment, not kubelet) is what actually triggered the replacement,
confirming the Deployment → ReplicaSet → Pod ownership chain rather than
just asserting it exists. The new pod reloaded the model from Task 3's
registry independently (its own `model_loaded` log line, not inherited
state), and the Service kept routing correctly with zero manual
intervention — `kubectl port-forward` against the Service after the
replacement still answered `/health` normally.

## How to reproduce

```bash
# from the repo root, fresh clone — same prerequisites as task-5
dvc pull
cd task-3 && dvc repro && cd ..

# image reuse: build via the existing task-5/Dockerfile if
# fleetpulse-api:latest isn't already present locally
docker build -f task-5/Dockerfile -t fleetpulse-api:latest .

cd task-6
kind create cluster --config kind-config.yaml
kind load docker-image fleetpulse-api:latest --name fleetpulse
kubectl apply -f manifests/

kubectl get pods -n fleetpulse -o wide
kubectl describe pod -n fleetpulse <pod-name>
kubectl logs -n fleetpulse <pod-name>
kubectl exec -it -n fleetpulse <pod-name> -- sh

kubectl port-forward -n fleetpulse svc/fleet-health-api 8811:8811 &
curl http://127.0.0.1:8811/health

# the break-it exercise
kubectl delete pod -n fleetpulse <pod-name>
kubectl get pods -n fleetpulse -w
```

Tear down: `kind delete cluster --name fleetpulse`.

## One thing learned

Going in, the mental model was "a Deployment runs pods." The more precise
version, visible only by actually watching the events rather than just
reading the manifest, is a chain of independent controllers each
reconciling one narrow thing: the Deployment controller's job ends at
"does a correctly-templated ReplicaSet exist," the ReplicaSet
controller's job is entirely "does the observed pod count match
`replicas`," and neither of them talks to kubelet directly — kubelet
independently watches for pods assigned to its own node. Deleting a pod
didn't trigger some single "heal thyself" routine; it just changed one
number the ReplicaSet controller was already continuously watching, and
everything downstream of that (scheduling, image resolution, container
start, this app's own `model_loaded` log line) ran exactly the same
unattended path it ran the first time, because from Kubernetes's
perspective a replacement pod isn't a special case — it's just satisfying
the same declared spec it was already trying to satisfy.

## Done-when checklist (from the roadmap spec)

- [x] Kind cluster stood up on this machine, no generator/Helm shortcuts
- [x] Hand-written Deployment, Service, ConfigMap, and Secret manifests
- [x] Task 5 image reused (rebuilt from the existing Dockerfile to pick
      up one small, additive code change — not a new build pipeline),
      loaded into the cluster via `kind load docker-image`
- [x] `kubectl get`/`describe`/`logs`/`exec`/`port-forward` all
      exercised against the running pod, not just applied and left alone
- [x] `/health` and `/predict` verified through `kubectl port-forward`,
      returning real predictions from the registered model
- [x] Pod deleted on purpose; Deployment's controller chain replaced it
      unattended, captured in [`reports/self_healing_demo.md`](./reports/self_healing_demo.md),
      not just asserted
- [x] `kubectl apply -f manifests/` → running pod narrated step by step
      above, each step checked against real `kubectl` output, not
      described from documentation alone
