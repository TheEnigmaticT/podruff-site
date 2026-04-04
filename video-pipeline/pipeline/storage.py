import boto3
from pipeline.retry import retry
from pipeline.config import (
    R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY,
    R2_BUCKET_NAME, R2_PUBLIC_URL,
)

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = boto3.client(
            "s3",
            endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
            aws_access_key_id=R2_ACCESS_KEY_ID,
            aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        )
    return _client


@retry(max_attempts=3, exceptions=(Exception,))
def upload_file(local_path: str, remote_key: str) -> str:
    """Upload a file to R2 and return its public URL."""
    client = _get_client()
    client.upload_file(local_path, R2_BUCKET_NAME, remote_key)
    return get_public_url(remote_key)


def get_public_url(remote_key: str) -> str:
    """Get the public URL for a file in R2."""
    return f"{R2_PUBLIC_URL}/{remote_key}"
