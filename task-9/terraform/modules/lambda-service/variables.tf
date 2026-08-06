# Generic container-image Lambda service module. No FleetPulse-specific
# names, columns, or business logic anywhere in this module, the whole
# point of pulling this out of task-9's root config is that Day 28 reuses
# this exact module, unmodified, for a completely unrelated project. Any
# instance-specific values (function name, image tag, config) are inputs,
# not defaults baked into the module body.

variable "function_name" {
  description = "Lambda function name. Also used to derive the ECR repository name, IAM role name, and SSM/Secrets Manager parameter paths, so it must be unique per deployment."
  type        = string
}

variable "image_tag" {
  description = "Tag of the image already pushed to this function's ECR repository (module does not build or push images, only references the tag)."
  type        = string
}

variable "ecr_repository_name" {
  description = "ECR repository name. Defaults to function_name if not set."
  type        = string
  default     = null
}

variable "memory_size" {
  description = "Lambda memory in MB."
  type        = number
  default     = 512
}

variable "timeout" {
  description = "Lambda timeout in seconds."
  type        = number
  default     = 30
}

variable "environment_variables" {
  description = "Plain (non-sensitive) environment variables set directly on the Lambda function."
  type        = map(string)
  default     = {}
}

variable "ssm_parameters" {
  description = "Plain config values (model name, log level, etc.) written to SSM Parameter Store as String parameters under /<function_name>/<key>. The Lambda's IAM role is granted read access to exactly these parameter ARNs, nothing broader."
  type        = map(string)
  default     = {}
  sensitive   = false
}

variable "secrets" {
  description = "Sensitive values (credentials, tokens) written to Secrets Manager as one secret per key, named <function_name>/<key>. The Lambda's IAM role is granted read access to exactly these secret ARNs, nothing broader."
  type        = map(string)
  default     = {}
  sensitive   = true
}

variable "create_function_url" {
  description = "Whether to create a Lambda Function URL for direct HTTPS invocation."
  type        = bool
  default     = true
}

variable "function_url_auth_type" {
  description = "Function URL authorization type: NONE or AWS_IAM. NONE is used for this local Floci-emulated deployment only; a real deployment should use AWS_IAM or front the URL with an authorizer."
  type        = string
  default     = "NONE"

  validation {
    condition     = contains(["NONE", "AWS_IAM"], var.function_url_auth_type)
    error_message = "function_url_auth_type must be NONE or AWS_IAM."
  }
}

variable "tags" {
  description = "Tags applied to every resource this module creates."
  type        = map(string)
  default     = {}
}
