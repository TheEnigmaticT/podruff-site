"""Drive poller — ProcessingState and Zencastr session scanner.

ProcessingState persists which Zencastr sessions have been processed (and their
current pipeline step) to a JSON file using the same atomic write pattern as
drive.py (write to .tmp, then os.replace).

scan_zencastr_sessions lists unprocessed sessions from a Zencastr root folder
in Google Drive, filtering to folders that contain recognisable video files.
"""
import json
import logging
import os

logger = logging.getLogger(__name__)

DEFAULT_STATE_PATH = "~/.openclaw/video-pipeline-state.json"
VIDEO_EXTENSIONS = {".mp4", ".webm", ".mkv", ".mov"}


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
