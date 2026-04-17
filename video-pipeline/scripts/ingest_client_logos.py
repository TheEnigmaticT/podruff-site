"""One-time ingestion: upload client logos to Drive as `{Client}/_assets/logo.png`.

Reads a manifest (slug -> local path), for each entry:
1. Looks up the client's Drive folder from client_map.json
2. Finds or creates `_assets/` subfolder
3. Uploads the local file as `logo.png` (replacing any existing)
4. Prints a summary line for each client

Usage:
    cd /Users/ct-mac-mini/dev/podruff-site/video-pipeline
    .venv/bin/python scripts/ingest_client_logos.py [--dry-run]

Edit the MANIFEST below to add/remove clients.
"""

import os
import sys
import urllib.parse
import urllib.request

# Add parent to path so we can import pipeline modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.drive import DriveClient
from pipeline.client_config import get_drive_folder

MANIFEST = {
    "jonathan-brill": os.path.expanduser("~/Downloads/JB_Horz_BLK_ORG.png"),
    "blue-light-it": "/Users/ct-mac-mini/dev/landing-pages/blue-light-it/logo.jpeg",
    "mugatuai": "/Users/ct-mac-mini/dev/approyo-overwatch-web/src/assets/mugatu-logo.png",
    "crestway": "/Users/ct-mac-mini/dev/ad-renderer/assets/crestway-logo.png",
    "notis": "/Users/ct-mac-mini/dev/ad-renderer/assets/notis-logo-transparent.png",
    "datafy": "/Users/ct-mac-mini/dev/datafy-insight-flow/src/assets/datafy-logo.png",
}


def find_or_create_assets_folder(drive, client_folder_id):
    """Find `_assets/` under the client folder, or create it."""
    folders = drive.list_folders(client_folder_id)
    for f in folders:
        if f["name"] == "_assets":
            return f["id"]
    return drive.create_folder("_assets", client_folder_id)


def find_existing_logo(drive, assets_folder_id):
    """Return the file ID of an existing logo.png in _assets/, or None."""
    files = drive.list_files(assets_folder_id)
    for f in files:
        if f["name"].lower() == "logo.png":
            return f["id"]
    return None


def delete_file(drive, file_id):
    """DELETE a file by ID."""
    token = drive._access_token()
    req = urllib.request.Request(
        f"https://www.googleapis.com/drive/v3/files/{file_id}",
        headers={"Authorization": f"Bearer {token}"},
        method="DELETE",
    )
    with urllib.request.urlopen(req, timeout=30):
        pass


def main():
    dry_run = "--dry-run" in sys.argv
    drive = DriveClient()

    for slug, local_path in MANIFEST.items():
        prefix = f"[{slug}]"

        if not os.path.exists(local_path):
            print(f"{prefix} SKIP: local file not found at {local_path}")
            continue

        client_folder_id = get_drive_folder(slug)
        if not client_folder_id:
            print(f"{prefix} SKIP: no drive_folder_id in client_map.json")
            continue

        if dry_run:
            print(f"{prefix} DRY RUN: would upload {local_path} -> client folder {client_folder_id}/_assets/logo.png")
            continue

        assets_folder_id = find_or_create_assets_folder(drive, client_folder_id)

        # Delete existing logo if present (Drive allows duplicates; we want one)
        existing = find_existing_logo(drive, assets_folder_id)
        if existing:
            delete_file(drive, existing)

        file_id = drive.upload_file(local_path, assets_folder_id, name="logo.png")
        print(f"{prefix} OK: uploaded {os.path.basename(local_path)} -> _assets/logo.png (file_id={file_id})")


if __name__ == "__main__":
    main()
