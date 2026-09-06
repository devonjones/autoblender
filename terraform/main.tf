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

# ─── Google OAuth credentials — value is written by bin/drive-auth ────────────

resource "aws_secretsmanager_secret" "google_oauth" {
  name = "${local.name}/google-oauth"
}

# ─── Lambda ──────────────────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "drive" {
  name              = "/aws/lambda/${local.name}-drive"
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

resource "aws_iam_role" "drive" {
  name                 = "${local.name}-drive"
  assume_role_policy   = data.aws_iam_policy_document.lambda_assume.json
  permissions_boundary = data.terraform_remote_state.infra.outputs.deploy_permissions_boundary_arn
}

resource "aws_iam_role_policy_attachment" "drive_logs" {
  role       = aws_iam_role.drive.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "drive_secret" {
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.google_oauth.arn]
  }
}

resource "aws_iam_role_policy" "drive_secret" {
  name   = "google-oauth-read"
  role   = aws_iam_role.drive.id
  policy = data.aws_iam_policy_document.drive_secret.json
}

resource "aws_lambda_function" "drive" {
  function_name = "${local.name}-drive"
  role          = aws_iam_role.drive.arn
  package_type  = "Image"
  image_uri     = local.image
  memory_size   = var.memory_mb
  timeout       = 900

  # One tick at a time: the watcher is idempotent but two overlapping ticks
  # would process the same input twice.
  reserved_concurrent_executions = 1

  ephemeral_storage {
    size = 2048
  }

  environment {
    variables = {
      DRIVE_ROOT_FOLDER_ID    = var.drive_root_folder_id
      GOOGLE_OAUTH_SECRET_ARN = aws_secretsmanager_secret.google_oauth.arn
    }
  }

  depends_on = [aws_cloudwatch_log_group.drive]
}

# EventBridge invokes asynchronously: without this, ticks throttled by the
# concurrency limit queue for hours and replay back-to-back, and a crashing
# handler is retried twice. A missed tick is just the next minute's tick.
resource "aws_lambda_function_event_invoke_config" "drive" {
  function_name                = aws_lambda_function.drive.function_name
  maximum_event_age_in_seconds = 60
  maximum_retry_attempts       = 0
}

# ─── Schedule ────────────────────────────────────────────────────────────────

resource "aws_cloudwatch_event_rule" "tick" {
  name                = "${local.name}-drive-tick"
  schedule_expression = "rate(${var.poll_interval_minutes} ${var.poll_interval_minutes == 1 ? "minute" : "minutes"})"
}

resource "aws_cloudwatch_event_target" "tick" {
  rule = aws_cloudwatch_event_rule.tick.name
  arn  = aws_lambda_function.drive.arn

  # Same reasoning as the invoke config: a tick EventBridge can't deliver now is
  # superseded by the next one, never replayed later.
  retry_policy {
    maximum_event_age_in_seconds = 60
    maximum_retry_attempts       = 0
  }
}

resource "aws_lambda_permission" "tick" {
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.drive.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.tick.arn
}
