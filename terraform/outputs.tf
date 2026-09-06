output "lambda_function_name" {
  value = aws_lambda_function.drive.function_name
}

output "google_oauth_secret_arn" {
  description = "Pass to bin/drive-auth to store the Drive refresh token"
  value       = aws_secretsmanager_secret.google_oauth.arn
}
