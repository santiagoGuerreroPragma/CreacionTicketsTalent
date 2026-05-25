resource "null_resource" "install_dependencies" {
  triggers = {
    requirements = filemd5("${path.module}/../lambda/requirements.txt")
    handler      = filemd5("${path.module}/../lambda/lambda_function.py")
  }

  provisioner "local-exec" {
    command = "rm -rf ${path.module}/../lambda/dist && mkdir -p ${path.module}/../lambda/dist && python3 -m pip install --platform manylinux2014_x86_64 --only-binary=:all: --implementation cp --python-version 3.12 -r ${path.module}/../lambda/requirements.txt -t ${path.module}/../lambda/dist && cp ${path.module}/../lambda/lambda_function.py ${path.module}/../lambda/dist/"
  }
}

data "archive_file" "lambda_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda/dist"
  output_path = "${path.module}/../lambda/lambda_function.zip"

  depends_on = [null_resource.install_dependencies]
}

resource "random_id" "secret_suffix" {
  byte_length = 4
}

resource "aws_secretsmanager_secret" "google_creds" {
  name                    = "${local.name_prefix}-google-creds-${random_id.secret_suffix.hex}"
  description             = "Google Service Account Credentials JSON for Slack-Ticket Lambda"
  recovery_window_in_days = 0 # allows deletion and re-creation immediately during testing
  tags                    = local.common_tags
}
# Google Service Account credentials uploaded to Secrets Manager.
resource "aws_secretsmanager_secret_version" "google_creds_version" {
  secret_id     = aws_secretsmanager_secret.google_creds.id
  secret_string = file("${path.module}/google-credentials.json")
}

resource "aws_secretsmanager_secret" "slack_creds" {
  name                    = "pragma-tickets-cloud-token-slack"
  description             = "Slack OAuth Token and Signing Secret"
  recovery_window_in_days = 0
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "slack_creds_version" {
  secret_id     = aws_secretsmanager_secret.slack_creds.id
  secret_string = jsonencode({
    slack_bot_token      = var.slack_bot_token
    slack_signing_secret = var.slack_signing_secret
  })
}
resource "aws_iam_role" "lambda_role" {
  name = "${local.name_prefix}-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "lambda_policy" {
  name = "${local.name_prefix}-lambda-policy"
  role = aws_iam_role.lambda_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:*:*:*"
      },
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue"
        ]
        Resource = [
          aws_secretsmanager_secret.google_creds.arn,
          aws_secretsmanager_secret.slack_creds.arn
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "ses:SendEmail",
          "ses:SendRawEmail"
        ]
        Resource = "*"
      }
    ]
  })
}

resource "aws_lambda_function" "webhook" {
  filename         = data.archive_file.lambda_zip.output_path
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256
  function_name    = "${local.name_prefix}-webhook"
  role             = aws_iam_role.lambda_role.arn
  handler          = "lambda_function.lambda_handler"
  runtime          = "python3.12"
  timeout          = 30
  memory_size      = 256

  environment {
    variables = {
      SLACK_CREDS_SECRET_NAME  = aws_secretsmanager_secret.slack_creds.name
      SLACK_CHANNEL_ID         = var.slack_channel_id
      AUTHORIZED_LEADERS       = join(",", var.authorized_leaders)
      GOOGLE_SHEETS_ID         = var.google_sheets_id
      GOOGLE_SHEETS_RANGE      = var.google_sheets_range
      GOOGLE_CREDS_SECRET_NAME = aws_secretsmanager_secret.google_creds.name
      SES_SENDER_EMAIL         = var.ses_sender_email
      CLOUDOPS_EMAIL           = var.cloudops_email
    }
  }

  tags = local.common_tags
}
