"""
secrets_loader.py — Calyxr Secure Secrets Management
======================================================
On EC2 (or any environment with AWS_SECRET_NAME set):
    Fetches all API keys from AWS Secrets Manager.
    EC2 must have an IAM role with secretsmanager:GetSecretValue permission.
    No credentials are stored on disk.

Locally (development):
    Falls back to loading from a .env file via python-dotenv.

Usage (in app.py / main.py):
    from secrets_loader import load_secrets
    load_secrets()   # must be called before os.getenv(...)
"""

import os
import json
import logging

logger = logging.getLogger(__name__)

# Required keys — startup will abort if any are missing after loading
REQUIRED_KEYS = ["ANTHROPIC_API_KEY", "SECRET_KEY"]

# Optional keys — warned if missing but app continues
OPTIONAL_KEYS = ["APOLLO_API_KEY", "HUBSPOT_API_KEY", "MYSQL_PASSWORD"]


def _load_from_aws(secret_name: str, region: str) -> bool:
    """
    Pull secrets JSON from AWS Secrets Manager and inject into os.environ.
    Returns True on success, False on any failure.
    """
    try:
        import boto3
        from botocore.exceptions import ClientError, NoCredentialsError

        client = boto3.client("secretsmanager", region_name=region)
        response = client.get_secret_value(SecretId=secret_name)
        secret_string = response.get("SecretString")

        if not secret_string:
            logger.error("AWS Secrets Manager returned empty SecretString.")
            return False

        secrets: dict = json.loads(secret_string)
        for key, value in secrets.items():
            # Only set if not already set — lets EC2 instance env vars take precedence
            if key not in os.environ:
                os.environ[key] = str(value)

        logger.info(
            "Secrets loaded from AWS Secrets Manager (secret: %s, region: %s)",
            secret_name,
            region,
        )
        return True

    except ImportError:
        logger.warning("boto3 is not installed. Cannot load from AWS Secrets Manager.")
    except Exception as exc:  # includes NoCredentialsError, ClientError
        logger.error("Failed to load from AWS Secrets Manager: %s", exc)

    return False


def _load_from_dotenv() -> None:
    """Load .env file for local development (never used on EC2 in production)."""
    try:
        from dotenv import load_dotenv

        loaded = load_dotenv(override=False)
        if loaded:
            logger.debug(".env file loaded (local dev mode).")
        else:
            logger.debug("No .env file found — relying on system environment variables.")
    except ImportError:
        logger.warning("python-dotenv not installed; skipping .env load.")


def _validate_required_keys() -> None:
    """Raise RuntimeError if any required environment variable is not set."""
    missing = [k for k in REQUIRED_KEYS if not os.environ.get(k)]
    if missing:
        raise RuntimeError(
            f"Missing required environment variables: {', '.join(missing)}. "
            "Set them in AWS Secrets Manager (EC2) or in your .env file (local dev)."
        )


def load_secrets() -> bool:
    """
    Main entry point. Call once at application startup before any os.getenv() calls.

    Resolution order:
      1. If AWS_SECRET_NAME env var is set → load from AWS Secrets Manager
      2. Otherwise → load from .env file (local development)

    Returns True if loaded from AWS, False if loaded from .env.
    """
    secret_name = os.environ.get("AWS_SECRET_NAME", "").strip()
    aws_region = os.environ.get("AWS_REGION", "us-east-1").strip()

    used_aws = False

    if secret_name:
        # Running on EC2 (or any environment configured for AWS)
        success = _load_from_aws(secret_name, aws_region)
        if not success:
            logger.warning(
                "AWS Secrets Manager load failed — falling back to .env "
                "(should NOT happen in production)."
            )
            _load_from_dotenv()
        else:
            used_aws = True
    else:
        # Local development
        _load_from_dotenv()

    _validate_required_keys()

    # Warn about missing optional keys so developers notice early
    for key in OPTIONAL_KEYS:
        if not os.environ.get(key):
            logger.warning("Optional env var not set: %s", key)

    return used_aws
