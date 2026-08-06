variable "image_tag" {
  description = "Tag of the Lambda image already built and pushed to Floci's ECR (see task-9/README.md's build/push steps). No default: CI and local runs must both supply this explicitly so the deployed function always matches a real, known image, never an implicit :latest."
  type        = string
}

variable "mlflow_tracking_credentials" {
  description = "Placeholder for what would be a real MLflow tracking-server credential in a deployment with a remote tracking server. Treated as sensitive here even though this Lambda doesn't call MLflow at runtime (see README's 'why the app doesn't read SSM/Secrets at runtime'), so the habit of routing anything credential-shaped through Secrets Manager, never a plain var or tfvars committed to git, stays real."
  type        = string
  sensitive   = true
  default     = "placeholder-not-a-real-credential"
}
