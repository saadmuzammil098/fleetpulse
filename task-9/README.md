# FleetPulse, Task 9: Ship to AWS (via Floci), Lambda, ECR, Terraform

Task 9 of 30 in the [production AI/ML roadmap](../../30-day-ai-ml-roadmap-industry-portfolio.md),
deploying the Fleet Health Risk API as a serverless container-image Lambda function
through Floci, with all infrastructure defined in Terraform: a reusable module, an S3
backend with native state locking, and a GitHub Actions workflow that plans on every PR
and applies on merge to main.

## What was built

- `task-4/src/main.py` now exports `handler = Mangum(app, lifespan="auto")` alongside
  the existing `app`, the same FastAPI object Task 4 already runs with `uvicorn
  src.main:app`. Nothing about the local/Docker/Kubernetes path changed, `handler` is
  simply never imported there.
- `task-4/src/model_registry.py` gained a second model source. `MODEL_SOURCE=registry`
  (the default, unchanged) reads Task 3's MLflow registry, exactly as Tasks 4/6/7 do.
  `MODEL_SOURCE=artifact` loads a plain `joblib` file instead, see "why the Lambda image
  doesn't use the MLflow registry" below for why Lambda needs this second path at all.
- `task-9/scripts/export_model.py` produces that self-contained artifact: Task 3's real
  feature pipeline (`FeatureComputer` rolling features, `build_pipeline`), trained
  against Task 1's real cleaned dataset when available, a small synthetic fallback
  otherwise.
- `task-9/Dockerfile` builds the Lambda container image on
  `public.ecr.aws/lambda/python:3.12` (AWS's own base image, bundling the Lambda Runtime
  Interface Client), copying in Task 4's app code, the baked model artifact, and one
  file from Task 3 (see below).
- `task-9/terraform/modules/lambda-service/`: the reusable module, ECR repository,
  Lambda function (container image) with a Function URL, an IAM role scoped to exactly
  what the function needs, SSM parameters, Secrets Manager secrets. No FleetPulse
  specifics anywhere in the module body, see "the reusable module" below.
- `task-9/terraform/` (root): FleetPulse's instantiation of that module, plus the S3
  backend and provider configuration pointed at Floci.
- `.github/workflows/ci.yml`'s new `deploy` job: builds the Lambda image, pushes it to
  Floci's ECR, runs `terraform fmt -check`, `validate`, and `plan` on every PR, `apply`
  only on merge to main.

## Why the Lambda image doesn't use the MLflow registry

Task 3's MLflow registry works well for local/Kubernetes serving (Tasks 4, 6, 7), but
its local file-based artifact store bakes an **absolute host path** into `mlflow.db` at
logging time (see `task-5/README.md`'s "why the model isn't baked into the image" for
the original discovery). That's solvable in Docker Compose or Kubernetes with a bind
mount at the right absolute path. Lambda has no bind mounts at all, a container image is
a sealed, portable artifact, so that whole mechanism is unavailable.

Rather than stand up a remote MLflow artifact store (a real Task 3 concern, explicitly
out of that task's stated scope), Task 9 bakes a plain `joblib` model file into the
image and never touches the registry in that code path. `export_model.py` trains a real
model through Task 3's actual feature pipeline against Task 1's real dataset when it's
available locally (`dvc pull` has been run), or a small synthetic fallback otherwise, the
same trade-off Task 8 already made for CI, documented there.

One more wrinkle this surfaced: `task-4/src/shared_features.py` loads Task 3's
`FeatureComputer` from `task-3/src/features.py` by file path, unconditionally, at import
time, regardless of `MODEL_SOURCE` (rolling-window feature math is needed either way).
The first deploy attempt failed on exactly this, `FileNotFoundError: /var/task-3/src/
features.py not found`, because Lambda has no `task-3/` sibling directory. The fix,
confirmed working, bakes in just that one small, dependency-free file (see
`features.py`'s own docstring: it has no relative imports) at the exact sibling path
`config.py`'s existing default already expects, `task-9/Dockerfile`'s last `COPY`.

## Why the app doesn't read SSM/Secrets at runtime

The module provisions SSM parameters (`registered_model_name`, `log_level`) and a
Secrets Manager secret (a placeholder MLflow tracking credential), and the Lambda's IAM
role is scoped to read exactly those, nothing broader. But the running app gets its
actual config from plain Lambda environment variables (`MODEL_SOURCE`,
`MODEL_ARTIFACT_PATH`, `LOG_LEVEL`), the same mechanism Task 6's ConfigMap already uses,
not from a runtime `boto3` call to SSM/Secrets Manager. Wiring that up would mean
rewriting Task 4's config loading and adding cold-start latency for values that, in this
deployment, are not actually operationally rotated. This is a deliberate scope boundary,
the same shape as Task 5's Postgres service (provisioned, real health check, never
actually connected to by the app), not an oversight, the SSM/Secrets infrastructure and
IAM scoping exist and are exercised (see the IAM section below), the runtime
read path is the piece intentionally left for whenever these values need to change
without a redeploy.

## The reusable module

`modules/lambda-service` takes `function_name`, `image_tag`, `memory_size`, `timeout`,
`environment_variables`, `ssm_parameters`, `secrets`, and a few Function URL knobs, no
FleetPulse-specific column names, business logic, or defaults anywhere in the module
body. `task-9/terraform/main.tf` (the root config) is the only place FleetPulse-specific
values live: the function name (`fleetpulse-api`), which config keys to write to SSM,
what the placeholder secret is called. When Day 28 needs its own container-image Lambda
service, this directory copies over unmodified, only the root config that instantiates
it changes.

IAM is scoped per-instance: the log-write statement is restricted to this function's own
log group (`/aws/lambda/<function_name>:*`), the SSM read statement to exactly the
parameter ARNs this instance declared, the Secrets read statement to exactly the secret
ARNs this instance declared. No `Resource = "*"` anywhere in the steady-state policy.

One real bug this surfaced during development: Floci's SSM emulation doesn't return a
parameter ARN (`aws_ssm_parameter.this[key].arn` came back `null`, confirmed via
`terraform state show`), which would have left the IAM statement scoped to an empty
resource, granting nothing at all rather than the intended read access. The fix
constructs the ARN by hand from `data.aws_caller_identity` and `data.aws_region`
instead of trusting the resource attribute, SSM parameter ARNs are a fixed, documented
shape, so this is correct against real AWS too, not a Floci-specific workaround.

## S3 backend, native locking

```hcl
terraform {
  backend "s3" {
    bucket       = "fleetpulse-tfstate"
    key          = "task-9/lambda-service.tfstate"
    region       = "us-east-1"
    use_lockfile = true   # native S3 locking, not the DynamoDB lock-table pattern

    endpoints = { s3 = "http://localhost.floci.io:4566" }
    access_key = "test"
    secret_key = "test"
    skip_credentials_validation = true
    skip_metadata_api_check     = true
    skip_requesting_account_id  = true
    skip_region_validation      = true
    use_path_style              = true
  }
}
```

The bucket (`fleetpulse-tfstate`) is created once, by hand, before the first `terraform
init` (`aws s3 mb s3://fleetpulse-tfstate`), Terraform's S3 backend does not create its
own state bucket. `use_lockfile = true` is the current, non-deprecated locking
mechanism, no DynamoDB lock table anywhere in this setup.

## Why a self-hosted runner

Floci listens on `localhost.floci.io:4566`, this machine only. GitHub's hosted runners
execute in GitHub's cloud and cannot reach it, so `terraform apply` against Floci cannot
run there, the same local-only constraint `task-8/README.md` already documented for DVC,
hit again here for infrastructure instead of data. The only way to make "merge to main
deploys automatically" literally true is a **self-hosted runner**, a GitHub Actions
agent process running on this machine, registered to this repo, that picks up workflow
jobs and can reach Floci.

That's a real trade-off, not a free lunch: a self-hosted runner means any workflow run,
including one triggered by an external PR, can execute code on this laptop. For this
local, learning-project deployment that's an acceptable trade for a real merge-triggers-
deploy demonstration; a team's actual CI would either run the deploy step from a
runner inside the same private network as its real infrastructure, or, for anything
publicly reachable, use hosted runners against real AWS instead of a local-only
emulator.

Setup used here, for reproducibility:

```bash
gh api -X POST repos/<owner>/fleetpulse/actions/runners/registration-token
# then, in a fresh actions-runner install:
./config.sh --url https://github.com/<owner>/fleetpulse --token <token> \
  --name fleetpulse-local-floci --labels self-hosted,floci --work _work --unattended
./run.sh   # foreground; nohup/a service manager for anything longer-lived
```

`deploy` in `ci.yml` targets `runs-on: [self-hosted, floci]`; `lint`, `test`, and
`docker-build` stay on `ubuntu-latest`, hosted, since they don't need Floci.

**To stop exposing this laptop to workflow runs**: remove the runner
(`./config.sh remove --token <removal-token>` from the runner directory, or via the
repo's Settings, Actions, Runners in the GitHub UI) and kill the `run.sh` process.

## Reproduction steps

```bash
eval $(floci env)

# One-time: state bucket
aws s3 mb s3://fleetpulse-tfstate

# Model artifact (real data if `dvc pull` has populated task-1/data/processed/cleaned.csv,
# synthetic fallback otherwise)
cd task-9 && python -m scripts.export_model && cd ..

# Build and push the Lambda image
docker build -f task-9/Dockerfile -t fleetpulse-api-lambda:v1 .
aws ecr create-repository --repository-name fleetpulse-api 2>/dev/null || true
REPO_URI=$(aws ecr describe-repositories --repository-names fleetpulse-api --query 'repositories[0].repositoryUri' --output text)
aws ecr get-login-password | docker login --username AWS --password-stdin "${REPO_URI%%/*}"
docker tag fleetpulse-api-lambda:v1 "$REPO_URI:v1"
docker push "$REPO_URI:v1"

# Terraform
cd task-9/terraform
terraform init
terraform apply -var="image_tag=v1"

# Verify: a real prediction from the deployed model
FN_URL=$(terraform output -raw function_url)
curl -s "$FN_URL/health"
curl -s -X POST "$FN_URL/predict" -H "Content-Type: application/json" -d '{
  "vehicle_id": "veh-42", "type": "M",
  "telemetry_window": [{"air_temperature_k": 300.0, "process_temperature_k": 310.0,
    "rotational_speed_rpm": 1500.0, "torque_nm": 60.0, "tool_wear_min": 220.0}]
}'
```

## Verified: `aws lambda invoke` returns a real prediction

Function URL invocations above are the easy path (Mangum + a Function URL speak the
same event format the AWS CLI's `invoke` needs, hand-built here since `aws lambda
invoke` needs a full API Gateway v2-shaped payload, not a bare HTTP request):

```
$ aws lambda invoke --function-name fleetpulse-api --payload file:///tmp/invoke-payload.json \
    --cli-binary-format raw-in-base64-out /tmp/lambda-invoke-out2.json
{
    "StatusCode": 200,
    "ExecutedVersion": "$LATEST"
}

$ cat /tmp/lambda-invoke-out2.json
{"statusCode": 200, "body": "{\"vehicle_id\":\"veh-99\",\"failure_probability\":0.48147175588432084,
\"recommended_action\":\"schedule_service\",\"model_name\":\"fleetpulse-component-failure\",
\"model_version\":\"20260806T033502Z-10000rows\",\"readings_used\":1}", ...}
```

`failure_probability` and `model_version` are real, computed by a model trained on Task
1's real 10,000-row cleaned dataset (positive rate 3.39%, matching Task 2's original
figure), not a stub.

## Break-it exercise 1: drift detection

After a clean `apply`, the Lambda's memory was changed directly via the AWS CLI,
entirely outside Terraform:

```
$ aws lambda get-function-configuration --function-name fleetpulse-api --query 'MemorySize'
512
$ aws lambda update-function-configuration --function-name fleetpulse-api --memory-size 1024
"MemorySize": 1024,
```

`terraform plan` caught it immediately:

```
  # module.fleetpulse_api.aws_lambda_function.this will be updated in-place
  ~ resource "aws_lambda_function" "this" {
        id                             = "fleetpulse-api"
      ~ memory_size                    = 1024 -> 512
        # (29 unchanged attributes hidden)
      - image_config {
          - command           = [] -> null
          - entry_point       = [] -> null
            # (1 unchanged attribute hidden)
        }
        # (3 unchanged blocks hidden)
    }

Plan: 0 to add, 2 to change, 0 to destroy.
```

**How I'd decide accept-vs-reconcile in general**: if the manual change reflects a real,
considered operational need (memory really was undersized for a production workload,
someone bumped it under pressure during an incident), the right move is to update the
Terraform config to `1024` and apply that, so config becomes the new source of truth
instead of silently fighting a change someone made for a reason. If it's accidental,
unauthorized, or made without going through review (most manual console/CLI changes to
resources under Terraform management), the right move is what was done here: apply,
let Terraform revert it back to `512`, and treat the drift as a signal to ask who
made the change and why, not a config update to accept on its own.

Here I reverted (`terraform apply` back to `memory_size = 512`), since it was a
manufactured demo, not a real capacity decision, confirmed:

```
$ aws lambda get-function-configuration --function-name fleetpulse-api --query 'MemorySize'
512
```

**A genuine Floci fidelity gap surfaced by this exercise, not the deliberate drift**:
every `terraform plan` against this Floci instance shows a small residual diff on
`tags`/`tags_all` and `image_config`, even immediately after a clean apply with no
manual changes in between. `aws lambda get-function --query Tags` returns `null` even
right after Terraform sets tags, and `aws lambda get-function-configuration --query
ImageConfigResponse` returns an empty object regardless of what was set. Floci simply
doesn't round-trip these two fields. This is a known Floci limitation ("real and
actively developed, but young", per this roadmap's own caveat), not application drift,
worth being able to tell apart from a real, actionable diff like the memory change
above, and confirmed here by checking the actual API response directly rather than just
trusting Terraform's diff.

## Break-it exercise 2: bad IAM policy

The module's IAM policy document briefly had this statement added to the front:

```hcl
statement {
  sid       = "TEMP_TooBroad_DoNotMerge"
  effect    = "Allow"
  actions   = ["*"]
  resources = ["*"]
}
```

Applied. The API kept working exactly as before:

```
$ curl -s $FN_URL/health
{"status":"ok","model_loaded":true,"model_name":"fleetpulse-component-failure", ...}
```

But `iam simulate-principal-policy` showed what else the role could now do, none of it
related to serving predictions:

```
$ aws iam simulate-principal-policy --policy-source-arn arn:...role/fleetpulse-api-lambda-role \
    --action-names ecr:DeleteRepository iam:CreateUser secretsmanager:DeleteSecret dynamodb:DeleteTable
[
  {"Action": "ecr:DeleteRepository", "Decision": "allowed"},
  {"Action": "iam:CreateUser", "Decision": "allowed"},
  {"Action": "secretsmanager:DeleteSecret", "Decision": "allowed"},
  {"Action": "dynamodb:DeleteTable", "Decision": "allowed"}
]
```

**Why this matters even though it "worked"**: a working health check says nothing about
what a role can do, only what the application code happens to call. If this function's
code ever has a bug, a dependency compromise, or gets abused via a malicious payload
(this API deserializes user-supplied telemetry into a DataFrame passed straight to a
model, not an unreasonable place to imagine an attacker probing), the blast radius with
the broad policy is "delete any secret, create IAM users, destroy any ECR repo or
DynamoDB table in the account", not "serve one wrong prediction." The whole value of
least-privilege IAM is that it bounds the damage of a compromise that has nothing to do
with whether the happy path currently works.

Reverted, and re-verified narrow:

```
$ aws iam simulate-principal-policy --policy-source-arn arn:...role/fleetpulse-api-lambda-role \
    --action-names ecr:DeleteRepository iam:CreateUser secretsmanager:DeleteSecret ssm:GetParameter
[
  {"Action": "ecr:DeleteRepository", "Decision": "implicitDeny"},
  {"Action": "iam:CreateUser", "Decision": "implicitDeny"},
  {"Action": "secretsmanager:DeleteSecret", "Decision": "implicitDeny"},
  {"Action": "ssm:GetParameter", "Decision": "implicitDeny"}
]
$ curl -s $FN_URL/health
{"status":"ok","model_loaded":true, ...}
```

(`ssm:GetParameter` denies here too, that simulation call didn't pass `--resource-arns`,
so it checked against a wildcard resource; the actual, correctly-scoped call against the
real parameter ARN is allowed, confirmed separately, see the module section above.)

## GitHub Actions: plan on PR, apply on merge

`.github/workflows/ci.yml`'s `deploy` job (self-hosted, see above) runs on every PR and
on push to `main`:

1. `dvc pull` (Task 1's real dataset, reachable from this runner)
2. `python -m scripts.export_model` (real Task 3-shaped model)
3. Build the Lambda image, push to Floci's ECR
4. `terraform fmt -check`, `terraform validate`, `terraform plan`
5. **Post the plan as a comment on the PR** (enterprise-standard practice: a reviewer
   should be able to see exactly what infrastructure change they're approving without
   leaving the PR or digging through Actions logs)
6. `terraform apply`, **only** when the event is a push to `main`

Step 5 uses `hashicorp/setup-terraform`'s `terraform_wrapper: true` (captures
`terraform plan`'s stdout as a step output) plus `actions/github-script` to create or,
on a later push to the same PR, update one sticky comment (matched by an HTML marker
comment) rather than posting a new one every run. The job declares
`permissions: pull-requests: write` explicitly for this, default `GITHUB_TOKEN`
permissions are read-only on some repos/orgs. A failed plan still gets posted (so a
reviewer can see why), a separate step then fails the job.

One real setup issue this surfaced: `actions/setup-python@v5` assumes a hosted-runner-
style `/Users/runner` home directory for its tool cache and fails with `mkdir: /Users/
runner: Permission denied` on a self-hosted runner running as a normal user account.
Fixed by dropping that action from the `deploy` job in favor of a throwaway per-job venv
built from the system `python3.12` already on this machine's PATH, the same interpreter
every other task's `.venv` uses. `hashicorp/setup-terraform` (added for step 5 above)
hits the same tool-cache assumption, so the job also sets `RUNNER_TOOL_CACHE` explicitly
to a path this runner's account actually owns, rather than rediscovering the same bug a
second time. That path is specific to this machine, a genuinely local, single-developer
trade-off that comes with self-hosting at all, not something a hosted-runner setup would
ever need.

**Live proof, PR #1**: opened a real PR, the `deploy` job's `terraform plan` step ran on
the self-hosted runner and reported `Plan: 0 to add, 2 to change, 0 to destroy` (the two
changes being the image tag moving to this PR's commit and Floci's known tags/
image_config non-persistence, see the drift section above). Merged the PR, `terraform
apply` ran against Floci for real:

```
$ aws lambda get-function --function-name fleetpulse-api --query 'Code.ImageUri'
"000000000000.dkr.ecr.us-east-1.localhost:5100/fleetpulse-api:db8c8b7ca64839c81b0e6cde1a285f8b89b1a0d7"

$ git rev-parse HEAD   # on main, right after the merge
db8c8b7ca64839c81b0e6cde1a285f8b89b1a0d7
```

The deployed image tag is exactly the merge commit's SHA, proving this specific merge
triggered this specific deploy, not a stale or manually-pushed image. The model version
also changed, `20260806T042220Z-...` versus the manual test run's
`20260806T033502Z-...` from earlier in this same session, because the CI job ran its own
fresh `dvc pull` + `export_model.py` on the self-hosted runner, not a copy of anything
built locally:

```
$ curl -s $FN_URL/health
{"status":"ok","model_loaded":true,"model_name":"fleetpulse-component-failure",
 "model_version":"20260806T042220Z-10000rows","api_version":"1.1.0"}

$ curl -s -X POST $FN_URL/predict -H "Content-Type: application/json" -d '{...}'
{"vehicle_id":"veh-ci-verify","failure_probability":0.4460996297705364,
 "recommended_action":"schedule_service","model_name":"fleetpulse-component-failure",
 "model_version":"20260806T042220Z-10000rows","readings_used":1}
```

## Done when

- [x] A fresh clone plus the reproduction steps above produces a running Lambda that
      answers `/health` and `/predict` for real, through Floci.
- [x] `aws lambda invoke` (against Floci) returns a real prediction from the deployed
      model, verified above.
- [x] `terraform plan` correctly caught a manual memory-size change before it caused a
      config-vs-reality surprise, verified above, plan output included.
- [x] The bad-IAM-policy exercise showed the broad policy working functionally while
      granting unrelated, dangerous permissions, then was narrowed back down and
      re-verified.
- [x] Merging to `main` runs `terraform apply` against Floci through a self-hosted
      GitHub Actions runner, updating the live Lambda function automatically.
