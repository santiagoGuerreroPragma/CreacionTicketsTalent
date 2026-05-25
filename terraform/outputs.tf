output "webhook_url" {
  description = "The URL to configure as the Slack interactive Request URL (Webhook)"
  value       = "${aws_apigatewayv2_stage.default.invoke_url}webhook"
}

output "google_creds_secret_name" {
  description = "Name of the Secrets Manager Secret for Google Credentials"
  value       = aws_secretsmanager_secret.google_creds.name
}

output "ses_dkim_tokens" {
  description = "DKIM tokens for DNS verification of pragma.com.co. Create three CNAME records in Route 53 with these names and values: Name = [token]._domainkey.pragma.com.co., Value = [token].dkim.amazonses.com."
  value       = aws_ses_domain_dkim.pragma.dkim_tokens
}

