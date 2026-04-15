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

    def download_file(self, file_id: str, local_path: str) -> None:
        """Download a Drive file by ID, streaming in 8 MB chunks.

        Args:
            file_id: Drive file ID.
            local_path: Destination path on disk. Parent dirs are created.
        """
        url = f"{DRIVE_API_BASE}/files/{file_id}?alt=media"
        token = self._access_token()
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {token}")

        os.makedirs(os.path.dirname(os.path.abspath(local_path)), exist_ok=True)

        logger.debug("download_file file_id=%s -> %s", file_id, local_path)
        chunk_size = 8 * 1024 * 1024  # 8 MB
        with urllib.request.urlopen(req, timeout=300) as resp:
            with open(local_path, "wb") as f:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)

    def upload_file(
        self,
        local_path: str,
        parent_folder_id: str,
        name: str | None = None,
    ) -> str:
        """Upload a local file to Drive using multipart upload.

        Args:
            local_path: Path to the file on disk.
            parent_folder_id: Drive folder ID to upload into.
            name: Filename on Drive; defaults to the local filename.

        Returns:
            The new file's Drive ID.
        """
        filename = name or os.path.basename(local_path)
        url = f"https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart"
        token = self._access_token()

        metadata = json.dumps({
            "name": filename,
            "parents": [parent_folder_id],
        }).encode()

        with open(local_path, "rb") as f:
            file_data = f.read()

        boundary = b"ct_drive_boundary_7f3a9b2e"
        body = (
            b"--" + boundary + b"\r\n"
            b"Content-Type: application/json; charset=UTF-8\r\n\r\n"
            + metadata + b"\r\n"
            b"--" + boundary + b"\r\n"
            b"Content-Type: application/octet-stream\r\n\r\n"
            + file_data + b"\r\n"
            b"--" + boundary + b"--"
        )

        req = urllib.request.Request(url, data=body)
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header(
            "Content-Type",
            f"multipart/related; boundary={boundary.decode()}",
        )

        logger.debug("upload_file %s -> parent=%s name=%s", local_path, parent_folder_id, filename)
        with urllib.request.urlopen(req, timeout=300) as resp:
            result = json.loads(resp.read())

        return result["id"]

    def create_folder(self, name: str, parent_id: str) -> str:
        """Create a Drive folder.

        Args:
            name: Folder name.
            parent_id: Parent folder ID.

        Returns:
            The new folder's Drive ID.
        """
        url = f"{DRIVE_API_BASE}/files"
        token = self._access_token()

        metadata = json.dumps({
            "name": name,
            "mimeType": FOLDER_MIME,
            "parents": [parent_id],
        }).encode()

        req = urllib.request.Request(url, data=metadata)
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Content-Type", "application/json; charset=UTF-8")

        logger.debug("create_folder name=%s parent=%s", name, parent_id)
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())

        return result["id"]
