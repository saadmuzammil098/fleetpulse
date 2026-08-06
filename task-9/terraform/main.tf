# FleetPulse-specific wiring of the generic modules/lambda-service module.
# Every FleetPulse detail (function name, model config, the placeholder
# credential) lives here, not in the module, exactly so this file is what
# changes and the module is what doesn't when Day 28 reuses it.

module "fleetpulse_api" {
  source = "./modules/lambda-service"

  function_name = "fleetpulse-api"
  image_tag     = var.image_tag

  memory_size = 512
  timeout     = 30

  # What the running Lambda actually reads at cold start
  # (task-4/src/config.py). MODEL_SOURCE=artifact points model_registry.py
  # at the joblib file task-9/Dockerfile bakes into the image, never the
  # Task 3 MLflow registry, see task-9/README.md for why.
  environment_variables = {
    MODEL_SOURCE        = "artifact"
    MODEL_ARTIFACT_PATH = "/var/task/model_artifact/model.joblib"
    LOG_LEVEL           = "INFO"
  }

  # Plain config, provisioned and IAM-scoped for read access, but not
  # fetched by the app at runtime today, see README's "why the app
  # doesn't read SSM/Secrets at runtime" for the deliberate scope
  # boundary this draws (same shape as Task 5's unused Postgres service).
  ssm_parameters = {
    registered_model_name = "fleetpulse-component-failure"
    log_level             = "INFO"
  }

  secrets = {
    mlflow_tracking_credentials = var.mlflow_tracking_credentials
  }

  tags = {
    Project = "fleetpulse"
    Task    = "9"
  }
}
