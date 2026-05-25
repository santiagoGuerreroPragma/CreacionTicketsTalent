variable "aws_region" {
  type        = string
  description = "AWS region to deploy the resources"
  default     = "us-east-1"
}

variable "slack_signing_secret" {
  type        = string
  description = "Slack Signing Secret to verify signature of webhook requests"
  default     = ""
  sensitive   = true
}

variable "authorized_leaders" {
  type        = list(string)
  description = "List of Slack User IDs or usernames allowed to approve requests globally (e.g. admins)"
  default     = []
}

variable "google_sheets_id" {
  type        = string
  description = "The spreadsheet ID of the Google Sheet to append rows to"
  default     = ""
}

variable "google_sheets_range" {
  type        = string
  description = "The sheet and range to append rows to (e.g., 'Sheet1!A:J')"
  default     = "Sheet1!A:J"
}

variable "ses_sender_email" {
  type        = string
  description = "The verified SES email address from which the notification email is sent"
}

variable "cloudops_email" {
  type        = string
  description = "The email inbox of CloudOps to receive ManageEngine tickets"
  default     = "cloudops@pragma.com.co"
}

variable "aws_profile" {
  type        = string
  description = "AWS CLI Profile to use for deployment"
  default     = "pra_reserva_prod"
}

variable "slack_bot_token" {
  type        = string
  description = "Slack Bot User OAuth Token"
  default     = ""
  sensitive   = true
}

variable "slack_channel_id" {
  type        = string
  description = "Dedicated Slack channel ID to post approvals to (falls back to source channel if empty)"
  default     = ""
}

