"""Drive poller — ProcessingState, DoneXmlState, and session scanners.

ProcessingState persists which Zencastr sessions have been processed (and their
current pipeline step) to a JSON file using the same atomic write pattern as
drive.py (write to .tmp, then os.replace).

DoneXmlState tracks which editor-dropped XML files in done/ folders have been
rendered into branded finals and uploaded.

scan_zencastr_sessions lists unprocessed sessions from a Zencastr root folder
in Google Drive, filtering to folders that contain recognisable video files.

scan_done_folders walks Active Clients / [Client] / Video / [session] / done/
for XML files that are new (not in DoneXmlState) and old enough (> 60 s) to
be safely processed.
"""
import json
import logging
import os
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

DEFAULT_STATE_PATH = "~/.openclaw/video-pipeline-state.json"
DEFAULT_DONE_STATE_PATH = "~/.openclaw/video-pipeline-done-state.json"
VIDEO_EXTENSIONS = {".mp4", ".webm", ".mkv", ".mov"}
UPLOAD_SETTLE_SECONDS = 60  # skip XMLs newer than this to avoid mid-upload reads


class ProcessingState:
    """Persistent tracker for Zencastr session processing state.

    State file schema::

        {
            "<session_id>": {
                "step": "transcribed" | "editorial" | ... | "complete",
                "complete": true | false
            },
            ...
        }
    """

    def __init__(self, path: str | None = None):
        self.path = os.path.expanduser(path or DEFAULT_STATE_PATH)
        self._data: dict = self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> dict:
        """Load state from disk; return empty dict if file does not exist."""
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            logger.warning("Could not load state from %s — starting fresh", self.path)
            return {}

    def _save(self) -> None:
        """Atomically persist state to disk using .tmp + os.replace."""
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self._data, f, indent=2)
        os.replace(tmp, self.path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_processed(self, session_id: str) -> bool:
        """Return True if the session is marked complete."""
        return self._data.get(session_id, {}).get("complete", False)

    def get_step(self, session_id: str) -> str:
        """Return current step string, or empty string if not started."""
        return self._data.get(session_id, {}).get("step", "")

    def mark_step(self, session_id: str, step: str) -> None:
        """Record the current pipeline step and persist to disk."""
        entry = self._data.get(session_id, {})
        entry["step"] = step
        entry.setdefault("complete", False)
        self._data[session_id] = entry
        self._save()
        logger.debug("Session %s step -> %s", session_id, step)

    def mark_complete(self, session_id: str) -> None:
        """Mark session as fully processed and persist to disk."""
        entry = self._data.get(session_id, {})
        entry["step"] = "complete"
        entry["complete"] = True
        self._data[session_id] = entry
        self._save()
        logger.debug("Session %s marked complete", session_id)


# ---------------------------------------------------------------------------
# DoneXmlState — tracks editor-dropped XMLs that have been rendered
# ---------------------------------------------------------------------------

class DoneXmlState:
    """Persistent tracker for done-folder XML processing state.

    Uses the same atomic write pattern as ProcessingState but is keyed by
    Drive XML file ID rather than session folder ID, stored in a separate
    namespace/file so the two state trackers don't interfere.

    State file schema::

        {
            "<xml_file_id>": {
                "step": "rendering" | "uploaded" | ... | "complete",
                "complete": true | false
            },
            ...
        }
    """

    def __init__(self, path: str | None = None):
        self.path = os.path.expanduser(path or DEFAULT_DONE_STATE_PATH)
        self._data: dict = self._load()

    def _load(self) -> dict:
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            logger.warning("Could not load done-xml state from %s — starting fresh", self.path)
            return {}

    def _save(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self._data, f, indent=2)
        os.replace(tmp, self.path)

    def is_processed(self, xml_file_id: str) -> bool:
        """Return True if the XML has been fully processed."""
        return self._data.get(xml_file_id, {}).get("complete", False)

    def get_step(self, xml_file_id: str) -> str:
        """Return current step string, or empty string if not started."""
        return self._data.get(xml_file_id, {}).get("step", "")

    def mark_step(self, xml_file_id: str, step: str) -> None:
        """Record the current pipeline step and persist."""
        entry = self._data.get(xml_file_id, {})
        entry["step"] = step
        entry.setdefault("complete", False)
        self._data[xml_file_id] = entry
        self._save()
        logger.debug("Done XML %s step -> %s", xml_file_id, step)

    def mark_complete(self, xml_file_id: str) -> None:
        """Mark XML as fully processed and persist."""
        entry = self._data.get(xml_file_id, {})
        entry["step"] = "complete"
        entry["complete"] = True
        self._data[xml_file_id] = entry
        self._save()
        logger.debug("Done XML %s marked complete", xml_file_id)


# ---------------------------------------------------------------------------
# Session scanner
# ---------------------------------------------------------------------------

def scan_zencastr_sessions(
    drive_client,
    zencastr_folder_id: str,
    state: ProcessingState,
) -> list[dict]:
    """Scan Zencastr root folder for unprocessed sessions.

    Steps:
    1. List all subfolders in zencastr_folder_id.
    2. Skip any folder already marked complete in state.
    3. For each remaining folder, list video files (.mp4, .webm, .mkv, .mov).
    4. Skip folders with no video files.
    5. Return list of session dicts.

    Args:
        drive_client: DriveClient (or compatible mock) instance.
        zencastr_folder_id: Root folder ID where Zencastr uploads sessions.
        state: ProcessingState to check and resume from.

    Returns:
        List of dicts with keys:
            folder_id    — Drive folder ID
            folder_name  — Human-readable session name
            modified     — modifiedTime string from Drive
            video_files  — List of Drive file resource dicts
            resume_step  — Current step from state (empty if not started)
    """
    folders = drive_client.list_folders(zencastr_folder_id)
    logger.debug("Found %d subfolders in Zencastr root", len(folders))

    sessions = []
    for folder in folders:
        folder_id = folder["id"]

        # Skip already-complete sessions
        if state.is_processed(folder_id):
            logger.debug("Skipping processed session %s (%s)", folder_id, folder["name"])
            continue

        # Zencastr nests recordings: session-folder/recording-N/files
        # Check both the session folder and any subfolders for video files
        all_files = drive_client.list_files(folder_id)
        video_files = [
            f for f in all_files
            if os.path.splitext(f["name"])[1].lower() in VIDEO_EXTENSIONS
        ]
        if not video_files:
            subfolders = [f for f in all_files if f.get("mimeType") == "application/vnd.google-apps.folder"]
            for sf in subfolders:
                inner_files = drive_client.list_files(sf["id"])
                video_files.extend(
                    f for f in inner_files
                    if os.path.splitext(f["name"])[1].lower() in VIDEO_EXTENSIONS
                )

        if not video_files:
            logger.debug("Skipping folder %s — no video files", folder_id)
            continue

        sessions.append({
            "folder_id": folder_id,
            "folder_name": folder["name"],
            "modified": folder.get("modifiedTime", ""),
            "video_files": video_files,
            "resume_step": state.get_step(folder_id),
        })

    logger.info("Found %d unprocessed Zencastr sessions", len(sessions))
    return sessions


# ---------------------------------------------------------------------------
# Done-folder scanner
# ---------------------------------------------------------------------------

def scan_done_folders(
    drive_client,
    clients_root_id: str,
    client_map: dict,
    state: DoneXmlState,
) -> list[dict]:
    """Scan Active Clients for editor-dropped XMLs in session done/ folders.

    Walk hierarchy:
        clients_root → each client folder → Video/ → each session → done/ → XMLs

    Filters applied:
    - Only files with .xml extension
    - Only files modified > UPLOAD_SETTLE_SECONDS ago (skip in-progress uploads)
    - Only files not already marked complete in DoneXmlState

    For each qualifying XML, also finds the first source video file in the
    session root (parent of done/).

    Args:
        drive_client: DriveClient (or compatible mock) instance.
        clients_root_id: Drive ID of the "Active Clients" root folder.
        client_map: Dict mapping client_slug -> {drive_folder_id, ...} entries.
        state: DoneXmlState to check and persist processing status.

    Returns:
        List of dicts with keys:
            client_slug        — client identifier (e.g. "jonathan-brill")
            session_folder_id  — Drive folder ID of the session
            session_name       — Human-readable session folder name
            xml_file           — Drive file resource dict for the XML
            source_video_file  — Drive file resource dict for the source video
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=UPLOAD_SETTLE_SECONDS)

    results = []

    # Iterate clients by their drive_folder_id (skip slugs with no folder mapped)
    folder_id_to_slug = {
        entry.get("drive_folder_id"): slug
        for slug, entry in client_map.items()
        if entry.get("drive_folder_id")
    }

    # List direct children of clients_root to find matching client folders
    client_folders = drive_client.list_folders(clients_root_id)
    logger.debug("scan_done_folders: found %d client folders under root %s", len(client_folders), clients_root_id)

    for client_folder in client_folders:
        client_folder_id = client_folder["id"]
        client_slug = folder_id_to_slug.get(client_folder_id)
        if not client_slug:
            logger.debug("No client_slug mapped for folder %s (%s), skipping", client_folder_id, client_folder["name"])
            continue

        # Find "Video/" subfolder under client folder
        video_subfolders = [
            f for f in drive_client.list_folders(client_folder_id)
            if f["name"].lower() == "video"
        ]
        if not video_subfolders:
            logger.debug("No Video/ folder under client %s, skipping", client_slug)
            continue
        video_folder_id = video_subfolders[0]["id"]

        # Each subfolder of Video/ is a session
        session_folders = drive_client.list_folders(video_folder_id)
        logger.debug("Client %s: %d session folders", client_slug, len(session_folders))

        for session_folder in session_folders:
            session_folder_id = session_folder["id"]
            session_name = session_folder["name"]

            # Find the done/ subfolder
            done_subfolders = [
                f for f in drive_client.list_folders(session_folder_id)
                if f["name"].lower() == "done"
            ]
            if not done_subfolders:
                logger.debug("Session %s/%s: no done/ folder, skipping", client_slug, session_name)
                continue
            done_folder_id = done_subfolders[0]["id"]

            # List files in done/
            done_files = drive_client.list_files(done_folder_id)
            xml_files = [
                f for f in done_files
                if os.path.splitext(f["name"])[1].lower() == ".xml"
            ]

            if not xml_files:
                continue

            # Find source video in session root (used for rendering)
            session_files = drive_client.list_files(session_folder_id)
            source_videos = [
                f for f in session_files
                if os.path.splitext(f["name"])[1].lower() in VIDEO_EXTENSIONS
            ]
            if not source_videos:
                logger.warning(
                    "Session %s/%s has done/ XMLs but no source video — skipping",
                    client_slug, session_name,
                )
                continue
            source_video_file = source_videos[0]

            for xml_file in xml_files:
                xml_id = xml_file["id"]

                # Skip already processed
                if state.is_processed(xml_id):
                    logger.debug("XML %s already processed, skipping", xml_id)
                    continue

                # Skip too-recent files (may still be uploading)
                modified_str = xml_file.get("modifiedTime", "")
                if modified_str:
                    try:
                        modified_dt = datetime.fromisoformat(
                            modified_str.replace("Z", "+00:00")
                        )
                        if modified_dt > cutoff:
                            logger.debug(
                                "XML %s modified %s < %ds ago — skipping (mid-upload guard)",
                                xml_id, modified_str, UPLOAD_SETTLE_SECONDS,
                            )
                            continue
                    except ValueError:
                        logger.warning("Could not parse modifiedTime %r for %s", modified_str, xml_id)

                results.append({
                    "client_slug": client_slug,
                    "session_folder_id": session_folder_id,
                    "session_name": session_name,
                    "xml_file": xml_file,
                    "source_video_file": source_video_file,
                })

    logger.info("scan_done_folders: found %d new XMLs to process", len(results))
    return results
