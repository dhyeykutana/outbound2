#!/usr/bin/env bash
# =============================================================================
#  Calyxr — EC2 Secrets Setup Script
#  Run this ONCE from your local machine (with AWS CLI configured) to push
#  your API keys into AWS Secrets Manager.
#
#  Prerequisites:
#    1. AWS CLI installed and configured (aws configure)
#    2. IAM user/role with secretsmanager:CreateSecret + secretsmanager:PutSecretValue
#
#  Usage:
#    chmod +x ec2_setup.sh
#    ./ec2_setup.sh
# =============================================================================

set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────────────
SECRET_NAME="calyxr/production"
AWS_REGION="us-east-1"          # change to your EC2 region

# ── Prompt for values (never hard-code keys in this file) ────────────────────
echo ""
echo "=== Calyxr EC2 Secrets Setup ==="
echo "Values will be stored encrypted in AWS Secrets Manager."
echo "Leave a value blank to skip it."
echo ""

read -rsp "ANTHROPIC_API_KEY    : " ANTHROPIC_API_KEY;  echo
read -rsp "APOLLO_API_KEY       : " APOLLO_API_KEY;     echo
read -rsp "HUBSPOT_API_KEY      : " HUBSPOT_API_KEY;    echo
read -rsp "SECRET_KEY (Flask)   : " SECRET_KEY;         echo
read -rsp "MYSQL_PASSWORD       : " MYSQL_PASSWORD;     echo
read -rp  "MYSQL_HOST           : " MYSQL_HOST
read -rp  "MYSQL_USER           : " MYSQL_USER
read -rp  "MYSQL_DATABASE       : " MYSQL_DATABASE
read -rp  "MYSQL_PORT [3306]    : " MYSQL_PORT
MYSQL_PORT="${MYSQL_PORT:-3306}"

# ── Build JSON payload ────────────────────────────────────────────────────────
SECRET_JSON=$(python3 -c "
import json, sys
d = {
    'ANTHROPIC_API_KEY': '''$ANTHROPIC_API_KEY''',
    'APOLLO_API_KEY':    '''$APOLLO_API_KEY''',
    'HUBSPOT_API_KEY':   '''$HUBSPOT_API_KEY''',
    'SECRET_KEY':        '''$SECRET_KEY''',
    'MYSQL_PASSWORD':    '''$MYSQL_PASSWORD''',
    'MYSQL_HOST':        '''$MYSQL_HOST''',
    'MYSQL_USER':        '''$MYSQL_USER''',
    'MYSQL_DATABASE':    '''$MYSQL_DATABASE''',
    'MYSQL_PORT':        '''$MYSQL_PORT''',
}
# Remove blank keys so we don't overwrite with empty strings
d = {k: v for k, v in d.items() if v.strip()}
print(json.dumps(d))
")

# ── Push to AWS Secrets Manager ───────────────────────────────────────────────
echo ""
echo "Pushing secrets to AWS Secrets Manager (${SECRET_NAME} in ${AWS_REGION})..."

if aws secretsmanager describe-secret \
       --secret-id "$SECRET_NAME" \
       --region "$AWS_REGION" \
       --output text > /dev/null 2>&1; then
    # Secret already exists — update it
    aws secretsmanager put-secret-value \
        --secret-id "$SECRET_NAME" \
        --secret-string "$SECRET_JSON" \
        --region "$AWS_REGION"
    echo "✓ Secret updated."
else
    # First time — create it
    aws secretsmanager create-secret \
        --name "$SECRET_NAME" \
        --description "Calyxr production API keys" \
        --secret-string "$SECRET_JSON" \
        --region "$AWS_REGION"
    echo "✓ Secret created."
fi

# ── EC2 environment variable instructions ─────────────────────────────────────
echo ""
echo "=== Next Steps on your EC2 instance ==="
echo ""
echo "1. Attach an IAM role to your EC2 with this inline policy:"
echo ""
cat <<POLICY
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": "secretsmanager:GetSecretValue",
    "Resource": "arn:aws:secretsmanager:${AWS_REGION}:*:secret:${SECRET_NAME}*"
  }]
}
POLICY
echo ""
echo "2. On EC2, set these two environment variables (add to /etc/environment or"
echo "   your systemd service file — NOT in .env):"
echo ""
echo "   AWS_SECRET_NAME=${SECRET_NAME}"
echo "   AWS_REGION=${AWS_REGION}"
echo ""
echo "3. Install boto3 on EC2:"
echo "   pip install boto3"
echo ""
echo "4. Do NOT copy your .env file to EC2."
echo ""
echo "Done. Your keys are encrypted at rest in AWS Secrets Manager."
