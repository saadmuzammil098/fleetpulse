# All service endpoints point at Floci's single emulated endpoint
# (localhost.floci.io:4566), the same one `floci env` exports as
# AWS_ENDPOINT_URL, matching how every earlier task (Task 1's DVC S3
# remote, Task 7's `aws eks`) reaches Floci. Credentials are Floci's fixed
# test values, not real AWS credentials, real AWS would omit every
# skip_*/endpoints/access_key setting below and rely on the ambient
# environment (profile, OIDC, instance role) instead.
provider "aws" {
  region = "us-east-1"

  access_key = "test"
  secret_key = "test"

  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true
  skip_region_validation      = true

  endpoints {
    ecr            = "http://localhost.floci.io:4566"
    iam            = "http://localhost.floci.io:4566"
    lambda         = "http://localhost.floci.io:4566"
    s3             = "http://localhost.floci.io:4566"
    secretsmanager = "http://localhost.floci.io:4566"
    ssm            = "http://localhost.floci.io:4566"
    sts            = "http://localhost.floci.io:4566"
  }
}
