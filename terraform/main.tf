data "terraform_remote_state" "infra" {
  backend = "s3"
  config = {
    bucket = var.infra_state_bucket
    key    = "infra/${var.environment}/terraform.tfstate"
    region = "us-east-1"
  }
}

locals {
  name  = "autoblender-${var.environment}"
  image = "${data.terraform_remote_state.infra.outputs.ecr_repository_urls["autoblender"]}:${var.image_tag}"
}

# ─── API key — value is written by bin/package-stl-tools ─────────────────────

resource "aws_secretsmanager_secret" "api_key" {
  name = "${local.name}/api-key"
}

# ─── Job bucket: uploads in/, results out/, both reached only by presigned URL ─

resource "aws_s3_bucket" "jobs" {
  bucket = "${var.environment}-autoblender-jobs"
}

resource "aws_s3_bucket_public_access_block" "jobs" {
  bucket                  = aws_s3_bucket.jobs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "jobs" {
  bucket = aws_s3_bucket.jobs.id
  rule {
    id     = "expire-jobs"
    status = "Enabled"
    filter {}
    expiration {
      days = 1
    }
  }
}

# ─── Lambda ──────────────────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/lambda/${local.name}-api"
  retention_in_days = 30
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "api" {
  name                 = "${local.name}-api"
  assume_role_policy   = data.aws_iam_policy_document.lambda_assume.json
  permissions_boundary = data.terraform_remote_state.infra.outputs.deploy_permissions_boundary_arn
}

resource "aws_iam_role_policy_attachment" "api_logs" {
  role       = aws_iam_role.api.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Presigned URLs carry the role's own permissions, so PutObject on in/ is what
# lets the client upload and GetObject on out/ is what lets it download.
data "aws_iam_policy_document" "api" {
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.api_key.arn]
  }
  statement {
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.jobs.arn}/*"]
  }
  statement {
    # without ListBucket a missing key reads as 403, not the 404 api.py reports
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.jobs.arn]
  }
}

resource "aws_iam_role_policy" "api" {
  name   = "jobs-and-key"
  role   = aws_iam_role.api.id
  policy = data.aws_iam_policy_document.api.json
}

resource "aws_lambda_function" "api" {
  function_name = "${local.name}-api"
  role          = aws_iam_role.api.arn
  package_type  = "Image"
  image_uri     = local.image
  memory_size   = var.memory_mb
  timeout       = 900

  image_config {
    command = ["autoblender.api.handler"]
  }

  # Caps the bill if the key leaks: at most this many cuts run at once.
  reserved_concurrent_executions = var.api_concurrency

  ephemeral_storage {
    size = 2048
  }

  environment {
    variables = {
      BUCKET             = aws_s3_bucket.jobs.id
      API_KEY_SECRET_ARN = aws_secretsmanager_secret.api_key.arn
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.api.name
  }

  depends_on = [aws_iam_role_policy_attachment.api_logs]
}

# ─── Function URL: public endpoint, the API key is checked in api.py ─────────

resource "aws_lambda_function_url" "api" {
  function_name      = aws_lambda_function.api.function_name
  authorization_type = "NONE"
}

resource "aws_lambda_permission" "url" {
  statement_id           = "FunctionURLAllowPublicAccess"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.api.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

# Function URLs created since Oct 2025 also need InvokeFunction, scoped to URL calls.
resource "aws_lambda_permission" "url_invoke" {
  statement_id             = "FunctionURLInvokeAllowPublicAccess"
  action                   = "lambda:InvokeFunction"
  function_name            = aws_lambda_function.api.function_name
  principal                = "*"
  invoked_via_function_url = true
}
