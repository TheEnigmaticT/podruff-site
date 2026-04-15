"""Google Drive client using raw urllib (no google-api-python-client dependency).

Auth pattern mirrors call_task_crawler.py lines 113-144: read JSON creds,
POST to token_uri with refresh_token, write updated creds atomically.
"""
import json
import logging
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

from pipeline.config import GOOGLE_CREDS_PATH

logger = logging.getLogger(__name__)

DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"
FOLDER_MIME = "application/vnd.google-apps.folder"


class DriveClient:
    """OAuth2 Drive API client — token refresh + file/folder listing."""

    def __init__(self, creds_path: str = GOOGLE_CREDS_PATH):
        self.creds_path = os.path.expanduser(creds_path)

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _load_creds(self) -> dict:
        with open(self.creds_path) as f:
            return json.load(f)

    def _is_expired(self, creds: dict) -> bool:
        """Return True if the stored token is missing or expired (with 60s buffer)."""
        expiry_raw = creds.get("expiry")
        if not expiry_raw or not creds.get("token"):
            return True
        try:
            expiry = datetime.fromisoformat(expiry_raw)
            # Ensure timezone-aware comparison
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            return expiry <= datetime.now(timezone.utc) + timedelta(seconds=60)
        except ValueError:
            return True

    def refresh_token(self) -> str:
        """POST refresh_token to token_uri, update creds file atomically.

        Returns the new access token.
        """
        creds = self._load_creds()

        data = urllib.parse.urlencode({
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
            "refresh_token": creds["refresh_token"],
            "grant_type": "refresh_token",
        }).encode()

        req = urllib.request.Request(creds["token_uri"], data=data)
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read())

        creds["token"] = body["access_token"]
        creds["expiry"] = (
            datetime.now(timezone.utc) + timedelta(seconds=body["expires_in"])
        ).isoformat()

        tmp = self.creds_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(creds, f, indent=2)
        os.replace(tmp, self.creds_path)

        logger.debug("Token refreshed; expires %s", creds["expiry"])
        return body["access_token"]

    def _access_token(self) -> str:
        """Return a valid access token, refreshing if expired."""
        creds = self._load_creds()
        if self._is_expired(creds):
            logger.info("Access token expired or missing — refreshing")
            return self.refresh_token()
        return creds["token"]

    # ------------------------------------------------------------------
    # Drive API
    # ------------------------------------------------------------------

    def _drive_get(self, url: str) -> dict:
        """GET url with Bearer auth. Returns parsed JSON."""
        token = self._access_token()
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())

    def list_files(
        self,
        folder_id: str,
        mime_types: list[str] | None = None,
        modified_after: datetime | None = None,
    ) -> list[dict]:
        """List files inside *folder_id*.

        Args:
            folder_id: Drive folder ID to search within.
            mime_types: Optional list of MIME types to filter on (OR logic).
            modified_after: Optional datetime; only files modified after this.

        Returns:
            List of file resource dicts from the Drive API.
        """
        # Build q= query string
        q_parts = [f"'{folder_id}' in parents", "trashed = false"]

        if mime_types:
            mime_clauses = " or ".join(
                f"mimeType = '{mt}'" for mt in mime_types
            )
            q_parts.append(f"({mime_clauses})")

        if modified_after is not None:
            ts = modified_after.isoformat()
            q_parts.append(f"modifiedTime > '{ts}'")

        params = urllib.parse.urlencode({
            "q": " and ".join(q_parts),
            "fields": "files(id,name,mimeType,modifiedTime,size)",
            "pageSize": 1000,
        })
        url = f"{DRIVE_API_BASE}/files?{params}"

        logger.debug("list_files folder=%s url=%s", folder_id, url)
        data = self._drive_get(url)
        return data.get("files", [])

    def list_folders(
        self,
        parent_id: str,
        modified_after: datetime | None = None,
    ) -> list[dict]:
        """Convenience wrapper: list only folder-type items under *parent_id*."""
        return self.list_files(
            parent_id,
            mime_types=[FOLDER_MIME],
            modified_after=modified_after,
        )
