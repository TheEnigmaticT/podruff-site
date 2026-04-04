#!/usr/bin/env python3
"""Upload a local file to Cloudflare R2 and print its public URL.

Usage: python3 social_upload.py LOCAL_PATH REMOTE_KEY

Reads R2 credentials from social_config.json (r2_* keys) or falls back
to the video-pipeline .env file.
"""

import json
import os
import sys

import boto3

_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_r2_config():
    """Load R2 credentials from social_config.json or video-pipeline .env."""
    config_path = os.path.join(_DIR, "social_config.json")
    try:
        with open(config_path) as f:
            cfg = json.load(f)
        if cfg.get("r2_account_id"):
            return {
                "account_id": cfg["r2_account_id"],
                "access_key_id": cfg["r2_access_key_id"],
                "secret_access_key": cfg["r2_secret_access_key"],
                "bucket_name": cfg.get("r2_bucket_name", "video-pipeline"),
                "public_url": cfg["r2_public_url"],
            }
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        pass

    # Fallback: read from video-pipeline .env
    env_path = os.path.expanduser("~/video-pipeline/.env")
    env = {}
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    env[k] = v
    except FileNotFoundError:
        pass

    return {
        "account_id": env.get("R2_ACCOUNT_ID", ""),
        "access_key_id": env.get("R2_ACCESS_KEY_ID", ""),
        "secret_access_key": env.get("R2_SECRET_ACCESS_KEY", ""),
        "bucket_name": env.get("R2_BUCKET_NAME", "video-pipeline"),
        "public_url": env.get("R2_PUBLIC_URL", ""),
    }


def upload(local_path, remote_key):
    """Upload a file to R2 and return its public URL."""
    cfg = _load_r2_config()
    client = boto3.client(
        "s3",
        endpoint_url=f"https://{cfg['account_id']}.r2.cloudflarestorage.com",
        aws_access_key_id=cfg["access_key_id"],
        aws_secret_access_key=cfg["secret_access_key"],
    )
    client.upload_file(local_path, cfg["bucket_name"], remote_key)
    return f"{cfg['public_url']}/{remote_key}"


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python3 social_upload.py LOCAL_PATH REMOTE_KEY", file=sys.stderr)
        sys.exit(1)
    url = upload(sys.argv[1], sys.argv[2])
    print(url)
