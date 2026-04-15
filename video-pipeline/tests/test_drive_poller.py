"""Tests for pipeline.drive_poller — ProcessingState and scan_zencastr_sessions."""
import json
import os
import pytest
from unittest.mock import MagicMock, patch

from pipeline.drive_poller import ProcessingState, scan_zencastr_sessions


# ---------------------------------------------------------------------------
# ProcessingState tests
# ---------------------------------------------------------------------------

class TestProcessingState:
    def test_empty_state(self, tmp_path):
        """New state reports nothing processed and empty step."""
        state_file = tmp_path / "state.json"
        state = ProcessingState(path=str(state_file))
        assert state.is_processed("session-abc") is False
        assert state.get_step("session-abc") == ""

    def test_mark_step_does_not_mark_complete(self, tmp_path):
        """mark_step records the step but does not mark the session complete."""
        state_file = tmp_path / "state.json"
        state = ProcessingState(path=str(state_file))
        state.mark_step("session-1", "transcribed")
        assert state.is_processed("session-1") is False
        assert state.get_step("session-1") == "transcribed"

    def test_mark_complete(self, tmp_path):
        """is_processed returns True after mark_complete."""
        state_file = tmp_path / "state.json"
        state = ProcessingState(path=str(state_file))
        state.mark_complete("session-2")
        assert state.is_processed("session-2") is True

    def test_state_persists_to_disk(self, tmp_path):
        """Reload from disk retains mark_step and mark_complete state."""
        state_file = tmp_path / "state.json"
        state = ProcessingState(path=str(state_file))
        state.mark_step("session-persist", "editorial")
        state.mark_complete("session-done")

        # Reload from disk
        state2 = ProcessingState(path=str(state_file))
        assert state2.get_step("session-persist") == "editorial"
        assert state2.is_processed("session-persist") is False
        assert state2.is_processed("session-done") is True

    def test_resume_from_step(self, tmp_path):
        """get_step returns the most recently marked step."""
        state_file = tmp_path / "state.json"
        state = ProcessingState(path=str(state_file))
        state.mark_step("session-resume", "transcribed")
        state.mark_step("session-resume", "editorial")
        assert state.get_step("session-resume") == "editorial"

    def test_persist_uses_atomic_write(self, tmp_path):
        """State is written via .tmp + os.replace (atomic pattern)."""
        state_file = tmp_path / "state.json"
        replace_calls = []
        original_replace = os.replace

        def capture_replace(src, dst):
            replace_calls.append((src, dst))
            return original_replace(src, dst)

        state = ProcessingState(path=str(state_file))
        with patch("os.replace", side_effect=capture_replace):
            state.mark_step("session-atomic", "transcribed")

        assert len(replace_calls) == 1
        src, dst = replace_calls[0]
        assert src == str(state_file) + ".tmp"
        assert dst == str(state_file)

    def test_default_path_uses_openclaw_dir(self):
        """Default path resolves to ~/.openclaw/video-pipeline-state.json."""
        state = ProcessingState()
        expected = os.path.expanduser("~/.openclaw/video-pipeline-state.json")
        assert state.path == expected

    def test_mark_complete_also_clears_partial_step(self, tmp_path):
        """After mark_complete, get_step returns 'complete' or a sentinel."""
        state_file = tmp_path / "state.json"
        state = ProcessingState(path=str(state_file))
        state.mark_step("session-x", "editorial")
        state.mark_complete("session-x")
        assert state.is_processed("session-x") is True
        # Step should reflect completion
        assert state.get_step("session-x") == "complete"


# ---------------------------------------------------------------------------
# scan_zencastr_sessions tests
# ---------------------------------------------------------------------------

VIDEO_EXTENSIONS = {".mp4", ".webm", ".mkv", ".mov"}


def _make_folder(folder_id, folder_name, modified="2026-04-10T10:00:00Z"):
    return {
        "id": folder_id,
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
        "modifiedTime": modified,
    }


def _make_video_file(file_id, name, ext=".mp4"):
    return {
        "id": file_id,
        "name": f"{name}{ext}",
        "mimeType": f"video/{ext.lstrip('.')}",
        "modifiedTime": "2026-04-10T10:05:00Z",
    }


class TestScanZencastrSessions:
    def _make_state(self, tmp_path, processed_ids=None):
        state_file = tmp_path / "state.json"
        state = ProcessingState(path=str(state_file))
        for sid in (processed_ids or []):
            state.mark_complete(sid)
        return state

    def test_scan_finds_new_sessions(self, tmp_path):
        """Returns sessions for folders that have video files."""
        state = self._make_state(tmp_path)

        folders = [
            _make_folder("folder-1", "Episode 001"),
            _make_folder("folder-2", "Episode 002"),
        ]
        videos_by_folder = {
            "folder-1": [_make_video_file("vid-1", "episode_001")],
            "folder-2": [_make_video_file("vid-2", "episode_002", ".webm")],
        }

        mock_drive = MagicMock()
        mock_drive.list_folders.return_value = folders
        mock_drive.list_files.side_effect = lambda folder_id, **kwargs: videos_by_folder.get(folder_id, [])

        sessions = scan_zencastr_sessions(mock_drive, "zencastr-root-id", state)

        assert len(sessions) == 2
        ids = {s["folder_id"] for s in sessions}
        assert ids == {"folder-1", "folder-2"}

    def test_scan_skips_processed_sessions(self, tmp_path):
        """Already-complete sessions are excluded from results."""
        state = self._make_state(tmp_path, processed_ids=["folder-1"])

        folders = [
            _make_folder("folder-1", "Episode 001"),
            _make_folder("folder-2", "Episode 002"),
        ]
        videos_by_folder = {
            "folder-2": [_make_video_file("vid-2", "episode_002")],
        }

        mock_drive = MagicMock()
        mock_drive.list_folders.return_value = folders
        mock_drive.list_files.side_effect = lambda folder_id, **kwargs: videos_by_folder.get(folder_id, [])

        sessions = scan_zencastr_sessions(mock_drive, "zencastr-root-id", state)

        assert len(sessions) == 1
        assert sessions[0]["folder_id"] == "folder-2"

    def test_scan_skips_folders_with_no_videos(self, tmp_path):
        """Folders containing no recognised video files are excluded."""
        state = self._make_state(tmp_path)

        folders = [
            _make_folder("folder-empty", "Empty Session"),
            _make_folder("folder-with-video", "Good Session"),
        ]
        videos_by_folder = {
            "folder-empty": [],
            "folder-with-video": [_make_video_file("vid-1", "clip")],
        }

        mock_drive = MagicMock()
        mock_drive.list_folders.return_value = folders
        mock_drive.list_files.side_effect = lambda folder_id, **kwargs: videos_by_folder.get(folder_id, [])

        sessions = scan_zencastr_sessions(mock_drive, "zencastr-root-id", state)

        assert len(sessions) == 1
        assert sessions[0]["folder_id"] == "folder-with-video"

    def test_scan_result_shape(self, tmp_path):
        """Each result dict has the required keys."""
        state = self._make_state(tmp_path)
        state.mark_step("folder-1", "transcribed")

        folders = [_make_folder("folder-1", "Episode 001", "2026-04-12T08:00:00Z")]
        video = _make_video_file("vid-1", "episode_001")
        videos_by_folder = {"folder-1": [video]}

        mock_drive = MagicMock()
        mock_drive.list_folders.return_value = folders
        mock_drive.list_files.side_effect = lambda folder_id, **kwargs: videos_by_folder.get(folder_id, [])

        sessions = scan_zencastr_sessions(mock_drive, "zencastr-root-id", state)

        assert len(sessions) == 1
        s = sessions[0]
        assert s["folder_id"] == "folder-1"
        assert s["folder_name"] == "Episode 001"
        assert s["modified"] == "2026-04-12T08:00:00Z"
        assert s["video_files"] == [video]
        assert s["resume_step"] == "transcribed"

    def test_scan_lists_zencastr_root(self, tmp_path):
        """list_folders is called on the zencastr_folder_id."""
        state = self._make_state(tmp_path)

        mock_drive = MagicMock()
        mock_drive.list_folders.return_value = []

        scan_zencastr_sessions(mock_drive, "zencastr-specific-root", state)

        mock_drive.list_folders.assert_called_once_with("zencastr-specific-root")

    def test_scan_returns_resume_step_empty_for_new(self, tmp_path):
        """resume_step is empty string for sessions with no prior state."""
        state = self._make_state(tmp_path)

        folders = [_make_folder("folder-new", "New Episode")]
        videos_by_folder = {"folder-new": [_make_video_file("vid-new", "new_ep")]}

        mock_drive = MagicMock()
        mock_drive.list_folders.return_value = folders
        mock_drive.list_files.side_effect = lambda folder_id, **kwargs: videos_by_folder.get(folder_id, [])

        sessions = scan_zencastr_sessions(mock_drive, "root", state)

        assert sessions[0]["resume_step"] == ""

    def test_scan_all_video_extensions(self, tmp_path):
        """Folders with .webm, .mkv, .mov files are also picked up."""
        state = self._make_state(tmp_path)

        folders = [
            _make_folder(f"folder-{ext.lstrip('.')}", f"Episode {ext}")
            for ext in [".webm", ".mkv", ".mov"]
        ]
        videos_by_folder = {
            f"folder-{ext.lstrip('.')}": [_make_video_file(f"vid-{ext.lstrip('.')}", "clip", ext)]
            for ext in [".webm", ".mkv", ".mov"]
        }

        mock_drive = MagicMock()
        mock_drive.list_folders.return_value = folders
        mock_drive.list_files.side_effect = lambda folder_id, **kwargs: videos_by_folder.get(folder_id, [])

        sessions = scan_zencastr_sessions(mock_drive, "root", state)

        assert len(sessions) == 3
