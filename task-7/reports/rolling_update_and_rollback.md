# Rolling update and rollback

## The change

`task-4/src/main.py`: FastAPI app `version` bumped `1.0.0` → `1.1.0`.
`task-4/src/schemas.py`: `HealthResponse` gained a new `api_version` field,
populated in `main.py`'s `/health` handler from `app.version`. Small,
additive, and specifically chosen to be watchable live — a curl loop
against `/health` during the rollout can see the exact moment a request
lands on a new-version pod instead of an old one, rather than having to
infer the rollout from pod names.

Image rebuilt as `fleetpulse-api:1.1.0` (from the unchanged
`task-5/Dockerfile`, same as every prior task) and `kind load
docker-image`'d into the cluster.

## Zero-downtime rolling update

`templates/deployment.yaml` sets:

```yaml
strategy:
  type: RollingUpdate
  rollingUpdate:
    maxUnavailable: 0
    maxSurge: 1
```

`maxUnavailable: 0` means the old pods can't be torn down until enough
new, *ready* (readiness-probe-passing) pods exist to keep serving —
that's the actual zero-downtime mechanism, not a hope.

While `helm upgrade fleetpulse fleet-health-api --set image.tag=1.1.0`
ran, a loop hit `http://127.0.0.1/health` (through the ingress, `Host:
fleetpulse.dev.local`) every 250ms and logged the HTTP status code and
`api_version` from each response — full log:
[`rolling_update_probe.log`](./rolling_update_probe.log).

Result:

```
$ grep -v "code=200" rolling_update_probe.log || echo "NONE — zero non-200 responses"
NONE — zero non-200 responses
```

The `api_version` field transitioned from `None` (the pre-Task-7 image
predates this field entirely, so old pods legitimately omit it from the
JSON body) to `1.1.0`, with a short window of **both** values appearing
interleaved — that interleaving is expected and correct: `maxSurge: 1`
briefly ran one new pod alongside the two old ones before the old ones
were terminated, and the Service load-balanced across whichever pods were
`Ready` at that instant. Not a bug, the visible signature of a proper
rolling update.

Final state: every pod running `fleetpulse-api:1.1.0`, every `/health`
response showing `"api_version":"1.1.0"`.

## Rollback

```
$ helm history fleetpulse
REVISION  STATUS      APP VERSION  DESCRIPTION
1         superseded  1.0.0        Install complete
4         superseded  1.0.0        Upgrade complete   <- last good state before 1.1.0
5         superseded  1.0.0        Upgrade complete   <- this is the 1.1.0 rollout
6         deployed    1.0.0        Rollback to 4
```

(Revisions 2-3 are `helm upgrade` attempts that hit the Helm/HPA
`spec.replicas` field-ownership conflict documented in
`break_it_undersized_cpu_request.md`, fixed before revision 4.)

```
$ helm rollback fleetpulse 4 --wait --timeout 120s
Rollback was a success!

$ kubectl get pods -n fleetpulse -o jsonpath='...image...'
fleetpulse-api:latest
fleetpulse-api:latest

$ curl -s -H "Host: fleetpulse.dev.local" http://127.0.0.1/health
{"status":"ok","model_loaded":true,"model_name":"fleetpulse-component-failure","model_version":"1"}
```

No `api_version` key in the response — confirming the rollback didn't
just revert the image tag cosmetically, it actually put the pre-1.1.0
code back. `helm rollback` re-runs the same rolling-update mechanics in
reverse (still `maxUnavailable: 0`), so this was zero-downtime too, not
just zero-error — not separately re-verified with a probe loop since the
mechanism is identical to the forward rollout above.

Rolled forward again afterward (`helm upgrade ... --set
image.tag=1.1.0`) to leave the cluster on the newer version as the final
state for this task.
