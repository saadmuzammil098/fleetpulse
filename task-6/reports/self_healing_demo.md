# Self-healing demo — deleting a pod on purpose

Ran against the `fleetpulse` namespace with the Deployment at its default
`replicas: 2`.

## Before

```
$ kubectl get pods -n fleetpulse -o wide
NAME                                READY   STATUS    RESTARTS   AGE   IP           NODE
fleet-health-api-58dc4dfffb-s7fhh   1/1     Running   0          53s   10.244.0.6   fleetpulse-control-plane
fleet-health-api-58dc4dfffb-vg6j5   1/1     Running   0          53s   10.244.0.5   fleetpulse-control-plane
```

## The delete

```
$ kubectl delete pod -n fleetpulse fleet-health-api-58dc4dfffb-s7fhh
pod "fleet-health-api-58dc4dfffb-s7fhh" deleted from fleetpulse namespace
```

## Immediately after (same second)

```
$ kubectl get pods -n fleetpulse -o wide
NAME                                READY   STATUS    RESTARTS   AGE   IP           NODE
fleet-health-api-58dc4dfffb-bf9rr   1/1     Running   0          1s    10.244.0.7   fleetpulse-control-plane
fleet-health-api-58dc4dfffb-vg6j5   1/1     Running   0          54s   10.244.0.5   fleetpulse-control-plane
```

`s7fhh` is gone entirely — not `Terminating`, just absent, the delete had
already completed by the time this ran. A brand new pod, `bf9rr`, is
already `1/1 Running`, one second old. Same ReplicaSet hash
(`58dc4dfffb`) as the pod it replaced, different random suffix, different
pod IP (`10.244.0.7` vs the deleted pod's `10.244.0.6`) — a new Pod
object, not the old one restarted in place.

## What actually did the replacing

```
$ kubectl describe rs -n fleetpulse fleet-health-api-58dc4dfffb
Events:
  Type    Reason            Age   From                   Message
  ----    ------            ----  ----                   -------
  Normal  SuccessfulCreate  65s   replicaset-controller  Created pod: fleet-health-api-58dc4dfffb-s7fhh
  Normal  SuccessfulCreate  65s   replicaset-controller  Created pod: fleet-health-api-58dc4dfffb-vg6j5
  Normal  SuccessfulCreate  12s   replicaset-controller  Created pod: fleet-health-api-58dc4dfffb-bf9rr
```

The `replicaset-controller` created the replacement, not the Deployment
controller directly and not kubelet — confirms the two-level chain
(Deployment owns a ReplicaSet, the ReplicaSet owns Pods and is the thing
actually watching "does observed pod count match `replicas: 2`?"). I
never told anything to create `bf9rr`; the ReplicaSet's reconcile loop
noticed the count had dropped to 1 and created it unprompted.

## The new pod, on its own

```
$ kubectl describe pod -n fleetpulse fleet-health-api-58dc4dfffb-bf9rr
Events:
  Type    Reason     Age   From               Message
  ----    ------     ----  ----               -------
  Normal  Scheduled  12s   default-scheduler  Successfully assigned fleetpulse/fleet-health-api-58dc4dfffb-bf9rr to fleetpulse-control-plane
  Normal  Pulled     12s   kubelet            Container image "fleetpulse-api:latest" already present on machine and can be accessed by the pod
  Normal  Created    12s   kubelet            Container created
  Normal  Started    12s   kubelet            Container started

$ kubectl logs -n fleetpulse fleet-health-api-58dc4dfffb-bf9rr
INFO:     Started server process [1]
INFO:     Waiting for application startup.
{"timestamp": "2026-08-03T03:43:59Z", "level": "INFO", "logger": "fleet_health_api", "message": "model_loaded", ...}
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8811 (Press CTRL+C to quit)
```

The new pod goes through the exact same startup sequence as any other
pod in this Deployment — it re-loads the model from Task 3's MLflow
registry itself (`model_loaded`, `model_version: 1`), it isn't handed
state from the pod it replaced. Nothing about *this* pod knows a delete
just happened; it just started normally because the PodSpec it was
created from is identical.

## Confirming the Service didn't notice either

```
$ kubectl port-forward -n fleetpulse svc/fleet-health-api 18812:8811 &
$ curl -s http://127.0.0.1:18812/health
{"status":"ok","model_loaded":true,"model_name":"fleetpulse-component-failure","model_version":"1"}
```

Run after the replacement pod was already up. The Service's `selector:
app: fleet-health-api` matches on the label, not on a specific pod name
or IP, so it started routing to `bf9rr` the moment it went `Ready` —
no manual re-pointing, no restart of the Service or port-forward needed.

## The actual lesson

Deleting a pod isn't "stopping the app" the way `docker stop` on a bare
container would be — the thing that's actually running, from the
Deployment's perspective, is "2 pods matching this spec exist," and a
delete just perturbs that count for however long it takes the
ReplicaSet's controller loop to notice and correct it (here: under a
second, no probes configured yet to slow it down — see README for why
readiness/liveness probes are deliberately out of scope for Task 6).
The `replicas: 2` isn't a cosmetic setting, it's what makes this
self-healing observable at all — with `replicas: 1` the same delete
would still trigger a replacement, but there'd be a real (if brief)
window with zero pods serving, which the two-pod Service just glossed
over without me having to reason about a race.
