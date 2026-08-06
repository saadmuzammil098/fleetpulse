# syntax=docker/dockerfile:1.7
#
# Lambda container image for the Fleet Health Risk API (Task 9).
#
# Base: AWS's own Lambda Python base image, which bundles the Lambda
# Runtime Interface Client (RIC), the process that speaks the Lambda
# Invoke API (event, context) -> response, not a normal HTTP server.
# Mangum, wrapping the same FastAPI app from Task 4 in
# task-4/src/main.py::handler, adapts ASGI requests to that shape.
#
# Build from the repo root:
#   docker build -f task-9/Dockerfile -t fleetpulse-api-lambda:latest .
#
# The model is a self-contained joblib file baked in at build time, run
# `python -m scripts.export_model` from task-9/ first to populate
# model_artifact/ (see that script's docstring and task-9/README.md).
# This is deliberately not the Task 3 MLflow registry Tasks 4/6/7 serve
# from: Lambda has no bind mounts, and the registry's local file store
# needs one (task-4/src/model_registry.py's MODEL_SOURCE=artifact branch
# is what reads this baked-in file instead).
#
# MODEL_SOURCE and MODEL_ARTIFACT_PATH are set as Lambda environment
# variables by the Task 9 Terraform module, not baked in here, so the
# running function's config stays visible in `aws lambda get-function-
# configuration` / Terraform state instead of hidden inside the image.

FROM public.ecr.aws/lambda/python:3.12

COPY task-9/requirements.txt ${LAMBDA_TASK_ROOT}/requirements.txt
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

COPY task-4/src ${LAMBDA_TASK_ROOT}/src
COPY task-9/model_artifact ${LAMBDA_TASK_ROOT}/model_artifact

# task-4/src/shared_features.py loads Task 3's FeatureComputer from
# task-3/src/features.py by file path, unconditionally, at import time
# (see that module's docstring), regardless of MODEL_SOURCE, since
# rolling-window feature math is needed either way. There is no bind
# mount here to supply it, so this bakes in just that one small,
# dependency-free file (no relative imports, see features.py's own
# docstring) at the exact sibling path task-4/src/config.py's TASK3_ROOT
# already defaults to relative to LAMBDA_TASK_ROOT (/var/task ->
# /var/task-3), so no FLEETPULSE_TASK3_ROOT override is needed.
COPY task-3/src/features.py ${LAMBDA_TASK_ROOT}-3/src/features.py

CMD ["src.main.handler"]
