#!/usr/bin/env python3
"""Run once per credential set to authorize and store refresh tokens."""
import argparse
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow
from auth import SCOPES, CREDENTIAL_SETS, TOKEN_DIR, _save_token

CLIENT_SECRETS_PATH = Path.home() / ".config" / "reporting-pipeline" / "client_secrets.json"

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--credential", required=True, choices=list(CREDENTIAL_SETS))
    args = parser.parse_args()

    if not CLIENT_SECRETS_PATH.exists():
        print(f"ERROR: Missing {CLIENT_SECRETS_PATH}")
        print("Download OAuth client secrets from Google Cloud Console and save there.")
        raise SystemExit(1)

    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRETS_PATH), SCOPES)
    creds = flow.run_local_server(port=0)
    name = CREDENTIAL_SETS[args.credential]
    _save_token(name, creds)
    print(f"Saved token for '{args.credential}' to {TOKEN_DIR}/{name}_token.json")

if __name__ == "__main__":
    main()
