# modules/lambda-service: a container-image Lambda function, its own ECR
# repository, an IAM role scoped to exactly what it needs, and optional
# plain (SSM) / sensitive (Secrets Manager) config. Generic on purpose,
# see variables.tf's header comment, this is reused unmodified on Day 28.

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  ecr_repository_name = coalesce(var.ecr_repository_name, var.function_name)
  image_uri           = "${aws_ecr_repository.this.repository_url}:${var.image_tag}"
  log_group_name      = "/aws/lambda/${var.function_name}"

  # var.secrets is a sensitive map, so Terraform treats anything derived
  # from it, including its own key names, as sensitive too, and refuses
  # to use a sensitive value in for_each (it could leak into a resource
  # instance key). The key names themselves ("mlflow_tracking_credentials",
  # say) aren't secret, only the values are, so nonsensitive() here
  # declassifies just the keys; every reference to the actual value below
  # still goes through var.secrets[...] and stays sensitive.
  secret_keys = nonsensitive(keys(var.secrets))

  # Built by hand, not read from aws_ssm_parameter.this[key].arn: Floci's
  # SSM emulation doesn't return a parameter ARN (it comes back null,
  # confirmed via `terraform state show`), which would otherwise leave
  # the IAM policy below scoped to an empty resource, i.e. it would grant
  # nothing at all rather than the intended access. SSM parameter ARNs
  # are a fixed, documented shape, so constructing them here is correct
  # against real AWS too, not just a workaround for this one emulator gap.
  ssm_parameter_arns = [
    for key in keys(var.ssm_parameters) :
    "arn:aws:ssm:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:parameter/${var.function_name}/${key}"
  ]
  secret_arns = [
    for key in local.secret_keys : aws_secretsmanager_secret.this[key].arn
  ]
}

# ---------------------------------------------------------------------------
# ECR
# ---------------------------------------------------------------------------

resource "aws_ecr_repository" "this" {
  name                 = local.ecr_repository_name
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = false # Floci does not emulate ECR image scanning.
  }

  tags = var.tags
}

# ---------------------------------------------------------------------------
# IAM, scoped to exactly what this function needs: write its own log
# group, read exactly the SSM parameters and secrets this instance
# declares. No "*" resources, no permissions for services this function
# doesn't touch.
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "this" {
  name               = "${var.function_name}-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.assume_role.json
  tags               = var.tags
}

data "aws_iam_policy_document" "permissions" {
  statement {
    sid     = "WriteOwnLogGroup"
    effect  = "Allow"
    actions = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = [
      "arn:aws:logs:*:*:log-group:${local.log_group_name}:*",
    ]
  }

  dynamic "statement" {
    for_each = length(local.ssm_parameter_arns) > 0 ? [1] : []
    content {
      sid       = "ReadOwnSsmParameters"
      effect    = "Allow"
      actions   = ["ssm:GetParameter", "ssm:GetParameters"]
      resources = local.ssm_parameter_arns
    }
  }

  dynamic "statement" {
    for_each = length(local.secret_arns) > 0 ? [1] : []
    content {
      sid       = "ReadOwnSecrets"
      effect    = "Allow"
      actions   = ["secretsmanager:GetSecretValue"]
      resources = local.secret_arns
    }
  }
}

resource "aws_iam_role_policy" "this" {
  name   = "${var.function_name}-lambda-policy"
  role   = aws_iam_role.this.id
  policy = data.aws_iam_policy_document.permissions.json
}

# ---------------------------------------------------------------------------
# Plain config (SSM Parameter Store) and sensitive config (Secrets Manager)
# ---------------------------------------------------------------------------

resource "aws_ssm_parameter" "this" {
  for_each = var.ssm_parameters

  name  = "/${var.function_name}/${each.key}"
  type  = "String"
  value = each.value
  tags  = var.tags
}

resource "aws_secretsmanager_secret" "this" {
  for_each = toset(local.secret_keys)

  name = "${var.function_name}/${each.value}"
  tags = var.tags
}

resource "aws_secretsmanager_secret_version" "this" {
  for_each = toset(local.secret_keys)

  secret_id     = aws_secretsmanager_secret.this[each.value].id
  secret_string = var.secrets[each.value]
}

# ---------------------------------------------------------------------------
# Lambda function + Function URL
# ---------------------------------------------------------------------------

resource "aws_lambda_function" "this" {
  function_name = var.function_name
  role          = aws_iam_role.this.arn
  package_type  = "Image"
  image_uri     = local.image_uri
  memory_size   = var.memory_size
  timeout       = var.timeout

  environment {
    variables = var.environment_variables
  }

  tags = var.tags

  depends_on = [aws_iam_role_policy.this]
}

resource "aws_lambda_function_url" "this" {
  count = var.create_function_url ? 1 : 0

  function_name      = aws_lambda_function.this.function_name
  authorization_type = var.function_url_auth_type
}
