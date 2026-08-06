output "function_name" {
  value = aws_lambda_function.this.function_name
}

output "function_arn" {
  value = aws_lambda_function.this.arn
}

output "function_url" {
  value       = var.create_function_url ? aws_lambda_function_url.this[0].function_url : null
  description = "HTTPS Function URL, null if create_function_url is false."
}

output "ecr_repository_url" {
  value = aws_ecr_repository.this.repository_url
}

output "iam_role_arn" {
  value = aws_iam_role.this.arn
}

output "ssm_parameter_names" {
  value = { for key, param in aws_ssm_parameter.this : key => param.name }
}

output "secret_arns" {
  value = { for key, secret in aws_secretsmanager_secret.this : key => secret.arn }
}
