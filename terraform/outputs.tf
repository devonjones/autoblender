output "api_url" {
  description = "Goes in the client .env as AB_URL"
  value       = aws_lambda_function_url.api.function_url
}

output "api_key_secret_arn" {
  value = aws_secretsmanager_secret.api_key.arn
}
