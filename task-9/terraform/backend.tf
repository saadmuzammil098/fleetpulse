# S3 backend against Floci's emulated S3, with native S3 state locking
# (use_lockfile = true), not the older S3-plus-DynamoDB lock table
# pattern (DynamoDB-based locking is deprecated and being removed by
# HashiCorp). The bucket must exist before `terraform init` runs, see
# task-9/README.md's "bootstrap" step, Terraform's S3 backend does not
# create its own state bucket.
terraform {
  backend "s3" {
    bucket       = "fleetpulse-tfstate"
    key          = "task-9/lambda-service.tfstate"
    region       = "us-east-1"
    use_lockfile = true

    endpoints = {
      s3 = "http://localhost.floci.io:4566"
    }

    access_key                  = "test"
    secret_key                  = "test"
    skip_credentials_validation = true
    skip_metadata_api_check     = true
    skip_requesting_account_id  = true
    skip_region_validation      = true
    use_path_style              = true
  }
}
