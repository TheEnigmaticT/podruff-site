#!/usr/bin/env python3
"""
Configure Cloudflare R2 bucket lifecycle rule to auto-delete objects older than 90 days.

This script requires a Cloudflare API token with appropriate permissions.
Currently, the R2 API credentials provided only have S3-level permissions,
which don't include lifecycle management.

Two approaches:

1. CLOUDFLARE REST API (requires Cloudflare API token):
   - Endpoint: PUT /accounts/{account_id}/r2/buckets/{bucket_name}/lifecycle
   - Requires: CLOUDFLARE_API_TOKEN environment variable or --token flag
   
2. AWS S3-COMPATIBLE API (via boto3):
   - Requires: R2 API credentials with lifecycle management permissions
   - Current R2 credentials lack this permission scope
   
Usage:
  python configure_r2_lifecycle.py --token <cloudflare-api-token>
  
Or set CLOUDFLARE_API_TOKEN env var and run:
  python configure_r2_lifecycle.py
"""

import os
import sys
import argparse
import json
import requests
from typing import Optional

def configure_via_cloudflare_api(
    account_id: str,
    bucket_name: str,
    api_token: str,
    days: int = 90
) -> bool:
    """Configure lifecycle via Cloudflare REST API."""
    
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/r2/buckets/{bucket_name}/lifecycle"
    
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "rules": [
            {
                "id": "auto-delete-90-days",
                "status": "enabled",
                "expiration": {
                    "days": days
                },
                "filter": {
                    "prefix": ""
                }
            }
        ]
    }
    
    try:
        print(f"Configuring R2 lifecycle rule via Cloudflare API...")
        print(f"Endpoint: {url}")
        print(f"Payload: {json.dumps(payload, indent=2)}")
        
        response = requests.put(url, json=payload, headers=headers)
        response.raise_for_status()
        
        result = response.json()
        if result.get("success"):
            print("\n✓ SUCCESS: Lifecycle rule configured")
            print(json.dumps(result.get("result", {}), indent=2))
            return True
        else:
            print("\n✗ FAILED: API returned success=false")
            print(f"Errors: {result.get('errors', [])}")
            return False
            
    except requests.exceptions.RequestException as e:
        print(f"\n✗ ERROR: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--token",
        help="Cloudflare API token (or set CLOUDFLARE_API_TOKEN env var)"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=90,
        help="Delete objects older than N days (default: 90)"
    )
    parser.add_argument(
        "--account-id",
        default="17b0dc3bc288a8bdb1c7cc94eb88f70a",
        help="R2 account ID"
    )
    parser.add_argument(
        "--bucket",
        default="video-pipeline",
        help="R2 bucket name"
    )
    
    args = parser.parse_args()
    
    # Get API token from arg or env
    api_token = args.token or os.getenv("CLOUDFLARE_API_TOKEN")
    
    if not api_token:
        print("ERROR: Cloudflare API token required")
        print("\nProvide token via:")
        print("  1. --token flag: python configure_r2_lifecycle.py --token YOUR_TOKEN")
        print("  2. Environment: export CLOUDFLARE_API_TOKEN=YOUR_TOKEN")
        print("\nTo get a token:")
        print("  1. Go to https://dash.cloudflare.com/profile/api-tokens")
        print("  2. Create a token with 'R2' account permissions")
        print("  3. Ensure permissions include 'Bucket Lifecycle Management'")
        sys.exit(1)
    
    success = configure_via_cloudflare_api(
        account_id=args.account_id,
        bucket_name=args.bucket,
        api_token=api_token,
        days=args.days
    )
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
