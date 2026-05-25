resource "aws_ses_email_identity" "sender" {
  email = var.ses_sender_email
}

# In AWS SES sandbox environments, the recipient email address must also be verified.
# We provision the resource here to allow the user to easily trigger the verification email.
resource "aws_ses_email_identity" "recipient" {
  email = var.cloudops_email
}

resource "aws_ses_domain_identity" "pragma" {
  domain = "pragma.com.co"
}

resource "aws_ses_domain_dkim" "pragma" {
  domain = aws_ses_domain_identity.pragma.domain
}

