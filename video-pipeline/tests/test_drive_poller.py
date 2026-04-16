"""Tests for pipeline.drive_poller — ProcessingState and scan_zencastr_sessions."""
import json
import os
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

from pipeline.drive_poller import (
    ProcessingState,
    DoneXmlState,
    scan_zencastr_sessions,
    scan_done_folders,
    UPLOAD_SETTLE_SECONDS,
)


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


# ---------------------------------------------------------------------------
# scan_done_folders — .kdenlive extension support
# ---------------------------------------------------------------------------

def _make_done_file(file_id, name, ext, age_seconds=120):
    """Build a fake Drive file resource dict for a done/ edit file."""
    modified = (
        datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "id": file_id,
        "name": f"{name}{ext}",
        "mimeType": "application/octet-stream",
        "modifiedTime": modified,
    }


def _make_source_video(file_id, name="source-clip.mp4"):
    modified = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    return {
        "id": file_id,
        "name": name,
        "mimeType": "video/mp4",
        "modifiedTime": modified,
    }


class TestScanDoneFoldersKdenlive:
    """Tests that scan_done_folders accepts .kdenlive files alongside .xml."""

    def _build_mock_drive(
        self,
        client_folder_id,
        video_folder_id,
        session_folder_id,
        done_folder_id,
        done_files,
        source_files,
    ):
        """Build a MagicMock drive client with a canned folder/file hierarchy."""
        mock_drive = MagicMock()

        # list_folders dispatch
        def list_folders(folder_id):
            if folder_id == "clients-root":
                return [{"id": client_folder_id, "name": "Brill"}]
            if folder_id == client_folder_id:
                return [{"id": video_folder_id, "name": "Video"}]
            if folder_id == video_folder_id:
                return [{"id": session_folder_id, "name": "Episode 001"}]
            if folder_id == session_folder_id:
                return [{"id": done_folder_id, "name": "done"}]
            return []

        # list_files dispatch
        def list_files(folder_id):
            if folder_id == done_folder_id:
                return done_files
            if folder_id == session_folder_id:
                return source_files
            return []

        mock_drive.list_folders.side_effect = list_folders
        mock_drive.list_files.side_effect = list_files
        return mock_drive

    def _make_state(self, tmp_path):
        state_file = tmp_path / "done_state.json"
        return DoneXmlState(path=str(state_file))

    def test_kdenlive_file_is_picked_up(self, tmp_path):
        """A .kdenlive file in done/ is returned by scan_done_folders."""
        kd_file = _make_done_file("kd-001", "story-01-short", ".kdenlive", age_seconds=120)
        source = _make_source_video("src-001")

        mock_drive = self._build_mock_drive(
            client_folder_id="client-brill",
            video_folder_id="video-brill",
            session_folder_id="sess-001",
            done_folder_id="done-001",
            done_files=[kd_file],
            source_files=[source],
        )
        state = self._make_state(tmp_path)
        client_map = {"jonathan-brill": {"drive_folder_id": "client-brill"}}

        results = scan_done_folders(mock_drive, "clients-root", client_map, state)

        assert len(results) == 1
        assert results[0]["xml_file"]["name"] == "story-01-short.kdenlive"

    def test_xml_file_still_picked_up(self, tmp_path):
        """A .xml file continues to be returned (no regression)."""
        xml_file = _make_done_file("xml-001", "story-02-short", ".xml", age_seconds=120)
        source = _make_source_video("src-001")

        mock_drive = self._build_mock_drive(
            client_folder_id="client-brill",
            video_folder_id="video-brill",
            session_folder_id="sess-001",
            done_folder_id="done-001",
            done_files=[xml_file],
            source_files=[source],
        )
        state = self._make_state(tmp_path)
        client_map = {"jonathan-brill": {"drive_folder_id": "client-brill"}}

        results = scan_done_folders(mock_drive, "clients-root", client_map, state)

        assert len(results) == 1
        assert results[0]["xml_file"]["name"] == "story-02-short.xml"

    def test_both_extensions_returned_together(self, tmp_path):
        """A mix of .xml and .kdenlive files in done/ are both returned."""
        kd_file = _make_done_file("kd-001", "story-01-short", ".kdenlive", age_seconds=120)
        xml_file = _make_done_file("xml-001", "story-02-short", ".xml", age_seconds=120)
        source = _make_source_video("src-001")

        mock_drive = self._build_mock_drive(
            client_folder_id="client-brill",
            video_folder_id="video-brill",
            session_folder_id="sess-001",
            done_folder_id="done-001",
            done_files=[kd_file, xml_file],
            source_files=[source],
        )
        state = self._make_state(tmp_path)
        client_map = {"jonathan-brill": {"drive_folder_id": "client-brill"}}

        results = scan_done_folders(mock_drive, "clients-root", client_map, state)

        assert len(results) == 2
        names = {r["xml_file"]["name"] for r in results}
        assert "story-01-short.kdenlive" in names
        assert "story-02-short.xml" in names

    def test_unknown_extension_ignored(self, tmp_path):
        """Files with unrecognised extensions in done/ are silently ignored."""
        txt_file = _make_done_file("txt-001", "readme", ".txt", age_seconds=120)
        source = _make_source_video("src-001")

        mock_drive = self._build_mock_drive(
            client_folder_id="client-brill",
            video_folder_id="video-brill",
            session_folder_id="sess-001",
            done_folder_id="done-001",
            done_files=[txt_file],
            source_files=[source],
        )
        state = self._make_state(tmp_path)
        client_map = {"jonathan-brill": {"drive_folder_id": "client-brill"}}

        results = scan_done_folders(mock_drive, "clients-root", client_map, state)

        assert len(results) == 0

    def test_result_key_is_xml_file(self, tmp_path):
        """Result dict uses 'xml_file' key even for .kdenlive files (API stability)."""
        kd_file = _make_done_file("kd-001", "clip", ".kdenlive", age_seconds=120)
        source = _make_source_video("src-001")

        mock_drive = self._build_mock_drive(
            client_folder_id="client-brill",
            video_folder_id="video-brill",
            session_folder_id="sess-001",
            done_folder_id="done-001",
            done_files=[kd_file],
            source_files=[source],
        )
        state = self._make_state(tmp_path)
        client_map = {"jonathan-brill": {"drive_folder_id": "client-brill"}}

        results = scan_done_folders(mock_drive, "clients-root", client_map, state)

        assert "xml_file" in results[0]
        assert results[0]["xml_file"]["id"] == "kd-001"
