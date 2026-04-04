import os
import json
from pathlib import Path
from typing import Optional
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/analytics.readonly",
    "https://www.googleapis.com/auth/webmasters.readonly",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/adwords",
]

CREDENTIAL_SETS = {
    "analytics": "analytics_crowdtamers",
    "trevor": "trevor_longino",
}

TOKEN_DIR = Path.home() / ".config" / "reporting-pipeline"


def _token_path(name: str) -> Path:
    return TOKEN_DIR / f"{name}_token.json"


def _load_token(name: str) -> Optional[Credentials]:
    path = _token_path(name)
    if not path.exists():
        return None
    return Credentials.from_authorized_user_file(str(path), SCOPES)


def _save_token(name: str, creds: Credentials):
    TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    with open(_token_path(name), "w") as f:
        f.write(creds.to_json())


def get_credentials(credential_set: str) -> Credentials:
    if credential_set not in CREDENTIAL_SETS:
        raise ValueError(
            f"Unknown credential set: {credential_set!r}. Choose from: {list(CREDENTIAL_SETS)}"
        )

    name = CREDENTIAL_SETS[credential_set]
    creds = _load_token(name)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _save_token(name, creds)
        return creds

    raise RuntimeError(
        f"No valid token for '{credential_set}'. Run: python setup_auth.py --credential {credential_set}"
    )
