"""Fleet Health Risk API — Task 4.

Serves the model currently aliased ``production`` in Task 3's MLflow
registry behind ``/predict``, with Pydantic schemas that reject physically
impossible sensor readings before they reach ``FeatureComputer`` or the
model at all.

Run with ``uvicorn src.main:app`` from ``task-4/`` (see README). Task 9
wraps this exact same ``app`` object with Mangum (``handler`` below) to
run it as a Lambda function behind a Function URL, no separate app or
duplicated route code, see task-9/README.md.
"""
from __future__ import annotations

import logging
import time

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from mangum import Mangum
from prometheus_fastapi_instrumentator import Instrumentator

from . import inference, model_registry
from .logging_config import configure_logging
from .schemas import HealthResponse, PredictRequest, PredictResponse

configure_logging()
logger = logging.getLogger("fleet_health_api")

app = FastAPI(
    title="FleetPulse — Fleet Health Risk API",
    description="Serves Task 3's registered component-failure model as a real API.",
    # Task 7's rolling-update demo: bumped 1.0.0 -> 1.1.0, exposed on
    # /health as api_version, specifically so a curl loop running through
    # the rolling update can watch this value flip cleanly with zero
    # failed requests instead of having to infer the rollout from pod
    # names alone.
    version="1.1.0",
)

# Task 5 addition: expose /metrics for the Prometheus service in
# task-5/docker-compose.yml — request counts, latencies, status codes,
# broken down by path, with zero application code changes beyond this
# one call.
Instrumentator().instrument(app).expose(app)

# Task 9 addition: Mangum adapts this same ASGI app to the Lambda
# handler signature (event, context) -> response. lifespan="auto" makes
# Mangum run the ASGI lifespan protocol (the @app.on_event("startup")
# hook below, which loads the model) once per container on cold start,
# the Lambda equivalent of what uvicorn already does once per process.
# This has no effect at all when the app is run with uvicorn directly
# (task-4/run_api.sh, docker-compose, Kubernetes), `handler` is simply
# never imported or called on that path.
handler = Mangum(app, lifespan="auto")


@app.on_event("startup")
def _startup() -> None:
    model_registry.load_model()


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "request_handled",
        extra={
            "event": "request_handled",
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": round(duration_ms, 2),
        },
    )
    return response


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    logger.warning(
        "validation_rejected",
        extra={
            "event": "validation_rejected",
            "path": request.url.path,
            "errors": exc.errors(),
        },
    )
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    loaded = model_registry.is_loaded()
    version = model_registry.get_model_version()
    return HealthResponse(
        status="ok" if loaded else "model_unavailable",
        model_loaded=loaded,
        model_name=model_registry.config.REGISTERED_MODEL_NAME if loaded else None,
        model_version=str(version) if loaded and version is not None else None,
        api_version=app.version,
    )


@app.post("/predict", response_model=PredictResponse)
def predict(request: PredictRequest) -> PredictResponse:
    model = model_registry.get_model()
    model_version = str(model_registry.get_model_version())
    result = inference.predict(model, model_version, request)
    # Task 10 addition: the feature row actually scored, not just the
    # response, is what the drift job needs (task-10/src/drift_report.py) —
    # the raw sensor readings and the "type" stream key are directly
    # comparable to Task 1's reference training distribution, the rolling
    # features are not (Task 1 has no rolling window). Logged here, not
    # returned in the response body, so the API contract Task 8's tests
    # pin doesn't change.
    feature_row = inference.build_feature_row(request)
    logger.info(
        "prediction_made",
        extra={
            "event": "prediction_made",
            "vehicle_id": result.vehicle_id,
            "machine_type": request.type.value,
            "readings_used": result.readings_used,
            "failure_probability": result.failure_probability,
            "recommended_action": result.recommended_action.value,
            "model_version": model_version,
            "features": {
                "air_temperature_k": feature_row["air_temperature_k"],
                "process_temperature_k": feature_row["process_temperature_k"],
                "rotational_speed_rpm": feature_row["rotational_speed_rpm"],
                "torque_nm": feature_row["torque_nm"],
                "tool_wear_min": feature_row["tool_wear_min"],
            },
        },
    )
    return result
