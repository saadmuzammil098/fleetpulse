# Deploying the same chart to Floci-emulated EKS

## Bringing the cluster up through the real AWS API

```bash
eval $(floci env)   # AWS_ENDPOINT_URL=http://localhost.floci.io:4566, test/test creds

aws eks create-cluster --name fleetpulse \
  --role-arn arn:aws:iam::000000000000:role/eks-service-role \
  --resources-vpc-config subnetIds=subnet-12345,subnet-67890
# status: CREATING -> polled `aws eks describe-cluster` until ACTIVE

aws eks update-kubeconfig --name fleetpulse
# adds context arn:aws:eks:us-east-1:000000000000:cluster/fleetpulse to ~/.kube/config
```

`docker ps` shows what that actually stood up: a real `rancher/k3s:latest`
container (`floci-eks-fleetpulse`), not a mock — `kubectl get nodes`
against it returns `v1.34.1+k3s1`, a real Kubernetes version string, not
a canned response. `aws eks describe-cluster` and `kubectl` are talking
to two different layers of the same thing: the AWS control-plane API is
Floci's emulation, but the Kubernetes API underneath it is a real k3s
API server answering real `kubectl` calls.

## Deploying the exact same chart

```bash
kind load docker-image fleetpulse-api:1.1.0 --name fleetpulse   # Kind path, for comparison
docker save fleetpulse-api:1.1.0 | docker exec -i floci-eks-fleetpulse ctr -n k8s.io images import -   # Floci path

helm install ingress-nginx ingress-nginx/ingress-nginx --namespace ingress-nginx --create-namespace --wait

cd task-7/chart
helm upgrade --install fleetpulse fleet-health-api -f fleet-health-api/values-dev.yaml --set image.tag=1.1.0 --wait
```

No edit to `Chart.yaml`, no template change, no new values file — the
`--set image.tag=1.1.0` is identical to what was run against Kind, not a
Floci-specific override. `values-dev.yaml` is untouched.

Verified through the ingress, same as Kind:

```
$ curl -s -H "Host: fleetpulse.dev.local" http://127.0.0.1:18080/health
{"status":"ok","model_loaded":true,"model_name":"fleetpulse-component-failure","model_version":"1","api_version":"1.1.0"}

$ curl -s -X POST -H "Host: fleetpulse.dev.local" ... http://127.0.0.1:18080/predict
{"vehicle_id":"veh-042","failure_probability":0.00248...,"recommended_action":"monitor",...}

$ kubectl get hpa -n fleetpulse
NAME               TARGETS        MINPODS   MAXPODS   REPLICAS
fleet-health-api   cpu: 40%/60%   1         3         2
```

## What stayed identical

- The Helm chart itself — every template, every value in
  `values.yaml`/`values-dev.yaml`. This is the actual point of the
  exercise: a chart that only knows how to talk to the Kubernetes API
  doesn't need to know or care that the control plane in front of it is
  Kind's `kindest/node` or Floci's k3s-behind-the-EKS-API.
- The HPA, probes, resource requests/limits, RollingUpdate strategy — all
  worked exactly as designed with zero chart changes, including the HPA
  reacting to real `kubectl top pod` metrics (k3s ships a working
  `metrics-server` out of the box, so this didn't even need a separate
  install here, unlike Kind).
- ingress-nginx as the ingress controller — installed via the same Helm
  chart from the same repo, and `/health`/`/predict` route through it the
  same way.
- The application itself: same image, same MLflow-backed model load,
  same `/health` and `/predict` behavior, same `api_version: 1.1.0`.

## What changed — the control plane, and what hangs off it

- **The API surface used to stand the cluster up.** `kind create cluster`
  is a local tool talking directly to Docker. `aws eks create-cluster` /
  `update-kubeconfig` are the real AWS CLI, going through Floci's
  emulated AWS control-plane API, which is the whole point of Day 7's
  second half — the workflow is the same one a real EKS cluster would
  use, not a look-alike.
- **How the node becomes reachable from the host.** Kind's
  `kind-config.yaml` declares `extraPortMappings` (80/443) up front,
  giving `localhost` direct access to whatever the ingress controller
  binds on the node. Floci creates its k3s container without exposing
  those ports (only the Kubernetes API port, 6500, is published) — there
  is no config surface in `aws eks create-cluster` for "also publish
  these container ports," because a real EKS cluster wouldn't have that
  concept either (a real cluster is reached via a real load balancer, not
  a Docker port mapping). Practical consequence: routing traffic in on
  Floci needed `kubectl port-forward svc/ingress-nginx-controller`,
  where Kind could be hit directly at `127.0.0.1`.
- **How the MLflow-backing filesystem got onto the node.** Kind's
  `extraMounts` gives the node a *live* bind mount of the Mac's `task-3/`
  directory — the same file on both sides, no copying. Floci's k3s
  container has no equivalent extra-mount hook exposed through
  `aws eks create-cluster`, so this task fell back to `docker cp`-ing a
  one-time snapshot of `task-3/` into the container at the identical
  absolute path the ConfigMap and hostPath volume both already expect
  (the same absolute-path constraint documented throughout task-6/7 —
  see `values.yaml`'s `task3Root` comment). This is a real, load-bearing
  difference: on Kind, a `dvc repro`/re-promotion in `task-3/` is visible
  to the running pod immediately; on the Floci cluster it would need a
  fresh `docker cp` to pick up. Not something the chart can paper over,
  because it isn't a Kubernetes-layer concern at all — it's an artifact
  of how each tool chooses to give its emulated node access to the host
  filesystem.
- **File ownership surfaced a bug the `docker cp` copy exposed that the
  Kind mount never did.** The first deploy attempt crash-looped —
  `PermissionError` writing to `mlartifacts/.../registered_model_meta`
  (MLflow's own housekeeping write, see task-6/README.md). Kind's
  bind-mounted files silently work regardless of the container's UID (Docker
  Desktop's macOS filesystem bridge doesn't enforce host-side POSIX
  ownership the same way), but `docker cp` produces a real file with real
  ownership (the Mac user's UID) inside a real Linux container, and the
  app's non-root UID 999 couldn't write it. Fixed with a one-time `docker
  exec ... chown -R 999:999`. This wasn't a chart bug or an application
  bug — it was a gap between how the two node types expose "the same"
  host path.
- **A pre-existing `ingress-nginx` install and `metrics-server` on
  Kind's cluster carried no state or config forward** — both were
  reinstalled fresh on the Floci cluster via the identical `helm
  install` commands (metrics-server turned out to already be built into
  k3s, so that step was a no-op there).

## Takeaway

Everything that makes this chart "production patterns" — probes sized to
real behavior, resource requests an HPA can act on sensibly, a rolling
update that's actually zero-downtime, a rollback that actually rolls back
— ported over with zero chart changes, which is the strongest evidence
those patterns are correctly built at the Kubernetes-API layer and not
accidentally coupled to Kind. Everything that *did* need attention was,
without exception, on the boundary between the emulated node and the
host machine underneath it — port exposure, filesystem access,
ownership — which is exactly the boundary a real EKS cluster (backed by
real EC2/Fargate nodes, a real VPC, a real Application Load Balancer)
replaces entirely. In other words: the parts that had to change here are
precisely the parts that disappear once this moves off a local emulator
onto real AWS.
