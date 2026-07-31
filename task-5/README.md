# FleetPulse — Task 5: Docker Refresher and Hardening

Task 5 of 30 in the [production AI/ML roadmap](../../30-day-ai-ml-roadmap-industry-portfolio.md),
FleetPulse phase.

> Roadmap spec (`Day 5 — Docker refresher and hardening`): multi-stage
> Dockerfile for the Fleet Health Risk API, non-root user, BuildKit cache
> mounts, `HEALTHCHECK`, Trivy scanning, docker-compose with Postgres and
> a monitoring stack. Done when: the image is a few hundred MB or less,
> non-root, no unaddressed high/critical CVEs.

**Note on scope:** this task started from a fallback spec describing
another API-building day, since the roadmap file wasn't located at first.
It was — Day 5 is actually a **containerization pass over Task 4's
existing API**, not a new service. Nothing about Task 4's endpoints,
schemas, or feature logic changes here; this task wraps that API in a
Dockerfile and a docker-compose stack, plus one small, additive change to
Task 4 itself (see below).

## What "clean API contracts" and "boundary validation" mean in production, and why an ML service needs graceful degradation

Task 4 already built the contract (Pydantic schemas rejecting physically
impossible sensor readings before they reach the model). What Task 5 adds
is the *operational* half of the same idea: a container should fail in
one of exactly two ways — serving correctly, or refusing to serve at all
— never a third state where it looks alive but returns wrong or corrupted
answers. A `HEALTHCHECK` that actually calls `/health` is what lets an
orchestrator (Docker, and later Kubernetes) tell those two states apart
from the outside, without knowing anything about sklearn or MLflow. For
an ML service specifically this matters more than for a typical CRUD API:
a `500` is loud and gets noticed, but a model silently serving stale or
malformed predictions because its registry connection quietly broke is
not loud — the API keeps returning `200`s and a wrong number, the kind of
failure that only shows up downstream (a fleet-ops technician trusting a
`monitor` verdict on a vehicle that should have gotten `urgent_alert`).
Section 3 below is the concrete version of this: killing the model source
outright and confirming the container refuses to start rather than
serving broken.

## What was built

- **`Dockerfile`** — multi-stage. Stage 1 (`builder`) installs pinned
  dependencies (`requirements.txt`) into a user site-packages dir with a
  BuildKit `--mount=type=cache` for pip, so repeat builds don't
  re-download the same wheels. Stage 2 (`runtime`) copies only the
  installed packages and `task-4/src` — no compiler, no pip cache, no
  build tooling. Runs as a dedicated non-root `fleetpulse` user
  (`USER fleetpulse`), declares a real `HEALTHCHECK` that calls
  `/health`, and purges `perl-base`/`login`/`util-linux`/`ncurses-bin`
  from the base image (see the Trivy section — this is what took the
  image from 23 to 12 HIGH/CRITICAL findings).
- **`requirements.txt`** — pinned versions matching what's already
  validated against Task 4 in the shared repo-root `.venv`, so the Docker
  build is reproducible without needing that whole venv inside the image.
- **`docker-compose.yml`** — four services: `api` (built from the
  Dockerfile above), `postgres` (per the roadmap spec — see "why Postgres
  is unused" below), `prometheus` (scrapes the API's `/metrics`), and
  `grafana` (provisioned with Prometheus as its default datasource).
- **`run_stack.sh`** — the one command.
- **One small Task 4 change**: `Instrumentator().instrument(app).expose(app)`
  added to `task-4/src/main.py` (via `prometheus-fastapi-instrumentator`)
  so Prometheus has something real to scrape — request counts, latencies,
  and status codes broken out by path, with no other application code
  changed. Also added `FLEETPULSE_TASK3_ROOT` as an environment override
  for `task-4/src/config.py`'s `TASK3_ROOT` (falls back to the original
  relative-sibling-directory default for local/non-container use) — see
  below for why the container needs this.

## Why the model isn't baked into the image

The image does not `COPY` Task 3's `mlflow.db` or `mlartifacts/` — they're
bind-mounted at runtime instead (`run_stack.sh` computes the real host
path and passes it through). Two things forced this, both genuine
MLflow-local-store discoveries, not design decisions made in the abstract:

1. **MLflow's local file-based artifact store bakes an *absolute* host
   path into `mlflow.db` at logging time.** Querying it directly:
   `artifact_location` for the `fleetpulse-component-failure` experiment
   is literally `file:/Users/.../fleetpulse/task-3/mlartifacts` — the
   exact path on the machine that ran `dvc repro` in Task 3. Mounting
   `task-3/` at a *different* path inside the container (the original
   plan: `/app/task-3`) makes that stored URI unresolvable, and
   `mlflow.sklearn.load_model()` fails immediately with
   `No such artifact: ''`. The fix: mount `task-3/` at its **real host
   path** inside the container too (`run_stack.sh` computes this with
   `pwd`, so it works on any machine, not just this one), and point
   `task-4/src/config.py` at it via the new `FLEETPULSE_TASK3_ROOT` env
   var instead of the old fixed relative-sibling-directory assumption.
2. **Loading a model via a `models:/name@alias` registry URI against a
   local file store writes back to the artifact directory on every
   load** — a `registered_model_meta` bookkeeping YAML, mlflow's own
   behavior, nothing this app's code does. A read-only mount
   (the original plan) throws `OSError: Read-only file system` on
   startup. The mount is read-write for this reason, documented here
   rather than silently switched.

Net effect: the image stays small (262.8 MB, well under "a few hundred MB
or less") and always serves whatever is currently aliased `production` in
the registry — a fresh `promote.py` run in Task 3 changes what this
container serves with no rebuild — but a real deployment would want this
solved differently (a remote artifact store like S3, or a proper MLflow
tracking *server* instead of a bare local file store), which is a genuine
scope boundary, not an oversight: fixing MLflow's local-store portability
is a Task 3 concern, containerizing Task 4 as it already exists is this
task's.

## Why Postgres is unused

The roadmap spec calls for docker-compose with Postgres and a monitoring
stack. Postgres is provisioned (`postgres:16-alpine`, a named volume for
persistence, a real `pg_isready` healthcheck) but **the API never
connects to it** — Task 1's DVC-tracked CSV and Task 3's MLflow registry
are still the only persistence layers this app actually uses, and
inventing a database integration with no real read/write path just to
make the compose file "complete" would be exactly the kind of unscoped
work worth avoiding. It's here because Day 5 asks for compose-orchestrated
multi-service practice before Day 6/7 do the same thing with Kubernetes
manifests, not because the API needs it yet.

## Image size, non-root, healthcheck

```
$ docker inspect fleetpulse-api:latest --format '{{.Size}}'
275606258   # 262.8 MB

$ docker inspect fleetpulse-api:latest --format '{{.Config.User}}'
fleetpulse

$ docker exec <container> id
uid=999(fleetpulse) gid=999(fleetpulse) groups=999(fleetpulse)

$ docker inspect <container> --format '{{.State.Health.Status}}'
healthy
```

## Trivy scan

Full writeup: [`reports/trivy_report.md`](./reports/trivy_report.md).
Short version: 23 HIGH/CRITICAL findings before hardening (4 CRITICAL, all
in `perl-base`), down to **12 HIGH, 0 CRITICAL** after purging unused OS
packages from the runtime stage. The remaining 12 are hard runtime
dependencies of `bash`/`coreutils` (confirmed by actually attempting to
remove them too — apt refuses, correctly: `bash : PreDepends: libtinfo6`)
with no upstream-fixed version available yet; documented as a risk
acceptance with rationale, not silently ignored.

## Break it on purpose

**Six bad requests**, fired at the containerized API through the running
compose stack — out-of-range torque, negative rotational speed, a missing
field, a wrong type, malformed JSON, and an invalid machine-type enum —
all returned a clean `422` with a structured Pydantic error body. None
reached `FeatureComputer` or the model; none produced a `500` or a raw
stack trace. Identical behavior to Task 4's own break-it results (the
Pydantic boundary is unchanged), confirmed again here specifically to
prove containerizing the app didn't alter that behavior.

**Registry unavailable** (`/predict` — or really, startup — with the
model source unreachable): running the image with no `task-3` mount at
all reproduces this cleanly. The container doesn't start in a half-broken
state; it fails fast, at import time, with a clear, structured, logged
error —

```
FileNotFoundError: /app/task-3/src/features.py not found. Task 4 serves
Task 3's registered model and reuses Task 3's feature module in place —
make sure task-3/ is present ...
```

— and exits (`Exited (1)`). Under `docker-compose.yml`'s
`restart: unless-stopped`, this becomes a restart loop rather than a
silently-broken running container — exactly the "loud failure, not a
quiet wrong answer" behavior argued for above. A real orchestrator (Task
6/7's Kubernetes) would surface this as `CrashLoopBackOff`, which is the
correct, actionable signal for "the model source is gone," not a `200`
with a stale or default score.

## How to reproduce

```bash
# from the repo root, fresh clone
dvc pull                          # task-1's cleaned.csv
cd task-3 && dvc repro && cd ..   # only if mlflow.db/mlartifacts aren't
                                   # already present locally — this
                                   # container reads Task 3's registry,
                                   # it doesn't build one
cd task-5
./run_stack.sh                    # the one command: builds the image,
                                   # brings up api + postgres +
                                   # prometheus + grafana
```

```bash
curl http://127.0.0.1:8811/health
curl http://127.0.0.1:8811/metrics | head

curl -X POST http://127.0.0.1:8811/predict \
  -H "Content-Type: application/json" \
  -d '{"vehicle_id":"veh-042","type":"M","telemetry_window":[
        {"air_temperature_k":298.1,"process_temperature_k":308.6,
         "rotational_speed_rpm":1551,"torque_nm":42.8,"tool_wear_min":0}
      ]}'
```

Prometheus UI: `http://127.0.0.1:9090` (Status → Targets should show
`fleet-health-api` as `UP`). Grafana: `http://127.0.0.1:3000`
(`admin`/`admin`, Prometheus is pre-provisioned as the default
datasource — no dashboards are pre-built, since there's no real usage
history yet to design them around).

To scan the image yourself without installing Trivy locally:

```bash
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
  -v trivy-cache:/root/.cache/ aquasec/trivy:latest \
  image --severity HIGH,CRITICAL fleetpulse-api:latest
```

## One thing learned

Going in, "containerize an existing FastAPI app" sounded like the
mechanical part of this task — the Dockerfile, the non-root user, the
healthcheck. It was. The actual work was almost entirely about MLflow's
local file-based store not being container-portable in two separate,
non-obvious ways (the baked-in absolute artifact path, and the
write-on-read `registered_model_meta` file) — neither of which showed up
anywhere in Task 3 or Task 4, because both ran the whole system on one
machine, in one filesystem, where "local" and "absolute path" and
"writable" were all trivially true at once. Docker is exactly the tool
that stops letting you get away with that: it forces every implicit
assumption about *where* something lives and *what* can touch it to
become explicit. The `chown -R` vs `COPY --chown` layer-duplication bug
(fixed early, cut the image from 507 MB to 262.8 MB) and the silent
`apt-get purge` failure (masked by `;` instead of `&&`, would have shipped
an unhardened image while claiming otherwise) were smaller versions of
the same lesson: a build step that "completes" is not the same claim as
a build step that "did what it says," and only actually running the
container and checking — not just building it — catches the difference.

## Done-when checklist (from the roadmap spec)

- [x] Multi-stage Dockerfile for the Fleet Health Risk API
- [x] Non-root user (`fleetpulse`, confirmed via `docker exec ... id`)
- [x] BuildKit cache mounts (`--mount=type=cache,target=/root/.cache/pip`)
- [x] `HEALTHCHECK` (confirmed `healthy` via `docker inspect`)
- [x] Trivy scanning (`reports/trivy_report.md`) — 0 CRITICAL, 12 HIGH
      remaining, all documented and risk-accepted with rationale, not
      silently ignored
- [x] docker-compose with Postgres and a monitoring stack (Prometheus +
      Grafana)
- [x] Image is a few hundred MB or less (262.8 MB)
- [x] Bad input reliably returns a clean 422, never a stack trace (six
      ways, tested against the running containerized stack)
- [x] Predictions from `/predict` come from the registered model with a
      visible model version, verified end-to-end through the container
- [x] Fresh clone + `dvc pull` (+ `dvc repro` in task-3 if needed) + one
      command (`./run_stack.sh`) brings the whole stack up
