"""Tests for done-folder watcher: scan_done_folders + DoneXmlState."""
import json
import os
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, call

from pipeline.drive_poller import DoneXmlState, scan_done_folders


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_folder(folder_id, name, modified="2026-04-10T10:00:00Z"):
    return {
        "id": folder_id,
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "modifiedTime": modified,
    }


def _make_xml_file(file_id, name, modified=None):
    if modified is None:
        # 5 minutes ago — safe (old enough)
        dt = datetime.now(timezone.utc) - timedelta(seconds=300)
        modified = dt.isoformat()
    return {
        "id": file_id,
        "name": name,
        "mimeType": "application/xml",
        "modifiedTime": modified,
    }


def _make_video_file(file_id, name="source.mp4"):
    return {
        "id": file_id,
        "name": name,
        "mimeType": "video/mp4",
        "modifiedTime": "2026-04-10T09:00:00Z",
    }


def _make_state(tmp_path, processed_xml_ids=None):
    state_file = tmp_path / "done_state.json"
    state = DoneXmlState(path=str(state_file))
    for xml_id in (processed_xml_ids or []):
        state.mark_complete(xml_id)
    return state


# ---------------------------------------------------------------------------
# DoneXmlState tests
# ---------------------------------------------------------------------------

class TestDoneXmlState:
    def test_empty_state(self, tmp_path):
        state = DoneXmlState(path=str(tmp_path / "state.json"))
        assert state.is_processed("file-abc") is False

    def test_mark_complete(self, tmp_path):
        state = DoneXmlState(path=str(tmp_path / "state.json"))
        state.mark_complete("file-123")
        assert state.is_processed("file-123") is True

    def test_persists_to_disk(self, tmp_path):
        state_file = str(tmp_path / "state.json")
        state = DoneXmlState(path=state_file)
        state.mark_complete("file-persist")

        state2 = DoneXmlState(path=state_file)
        assert state2.is_processed("file-persist") is True

    def test_unprocessed_after_load(self, tmp_path):
        state_file = str(tmp_path / "state.json")
        state = DoneXmlState(path=state_file)
        state.mark_complete("file-a")

        state2 = DoneXmlState(path=state_file)
        assert state2.is_processed("file-b") is False

    def test_atomic_write(self, tmp_path):
        state_file = str(tmp_path / "state.json")
        replace_calls = []
        original_replace = os.replace

        def capture(src, dst):
            replace_calls.append((src, dst))
            return original_replace(src, dst)

        state = DoneXmlState(path=state_file)
        with patch("os.replace", side_effect=capture):
            state.mark_complete("file-atomic")

        assert len(replace_calls) == 1
        src, dst = replace_calls[0]
        assert src.endswith(".tmp")
        assert dst == state_file

    def test_mark_step_and_get_step(self, tmp_path):
        state = DoneXmlState(path=str(tmp_path / "state.json"))
        state.mark_step("file-x", "rendering")
        assert state.get_step("file-x") == "rendering"
        assert state.is_processed("file-x") is False

    def test_mark_complete_sets_step_complete(self, tmp_path):
        state = DoneXmlState(path=str(tmp_path / "state.json"))
        state.mark_complete("file-y")
        assert state.get_step("file-y") == "complete"


# ---------------------------------------------------------------------------
# scan_done_folders tests
# ---------------------------------------------------------------------------

class TestScanDoneFolders:
    def _build_drive_mock(self, client_folders, session_map, done_map, source_map=None):
        """Build a drive mock with a realistic folder hierarchy.

        client_folders: list of folder dicts (immediate children of clients_root)
        session_map: {client_folder_id: [session_folder_dicts]}
        done_map: {session_folder_id: {"done_folder": folder_dict, "xml_files": [...]}}
        source_map: {session_folder_id: [video_file_dicts]}
        """
        source_map = source_map or {}
        mock_drive = MagicMock()

        def list_folders(parent_id, **kwargs):
            # clients_root → client folders
            if parent_id == "clients-root":
                return client_folders
            # client folder → Video/ subfolder
            for client_f in client_folders:
                if parent_id == client_f["id"]:
                    return [_make_folder(f"video-{client_f['id']}", "Video")]
            # Video/ → session folders
            for client_f in client_folders:
                if parent_id == f"video-{client_f['id']}":
                    return session_map.get(client_f["id"], [])
            # session → done/
            for _, sessions in session_map.items():
                for sess in sessions:
                    if parent_id == sess["id"]:
                        entry = done_map.get(sess["id"])
                        if entry:
                            return [entry["done_folder"]]
                        return []
            return []

        def list_files(parent_id, **kwargs):
            # done/ → xml files
            for _, entry in done_map.items():
                if entry and parent_id == entry["done_folder"]["id"]:
                    return entry["xml_files"]
            # session/ → source video files
            for sess_id, videos in source_map.items():
                if parent_id == sess_id:
                    return videos
            return []

        mock_drive.list_folders.side_effect = list_folders
        mock_drive.list_files.side_effect = list_files
        return mock_drive

    def test_finds_new_xmls(self, tmp_path):
        """scan_done_folders returns XML files ready to process."""
        state = _make_state(tmp_path)

        client_folders = [_make_folder("client-1", "Jonathan Brill")]
        session_map = {
            "client-1": [_make_folder("session-1", "AGI Is Science Fiction")],
        }
        xml_file = _make_xml_file("xml-1", "clip-a.xml")
        source_video = _make_video_file("vid-1", "source.mp4")
        done_map = {
            "session-1": {
                "done_folder": _make_folder("done-1", "done"),
                "xml_files": [xml_file],
            }
        }
        source_map = {"session-1": [source_video]}

        client_map = {"jonathan-brill": {"drive_folder_id": "client-1"}}
        mock_drive = self._build_drive_mock(client_folders, session_map, done_map, source_map)

        results = scan_done_folders(mock_drive, "clients-root", client_map, state)

        assert len(results) == 1
        r = results[0]
        assert r["client_slug"] == "jonathan-brill"
        assert r["session_name"] == "AGI Is Science Fiction"
        assert r["xml_file"]["id"] == "xml-1"
        assert r["source_video_file"]["id"] == "vid-1"

    def test_skips_recent_xmls(self, tmp_path):
        """XMLs modified < 60 seconds ago are skipped (mid-upload guard)."""
        state = _make_state(tmp_path)

        recent_dt = datetime.now(timezone.utc) - timedelta(seconds=30)
        recent_xml = _make_xml_file("xml-recent", "clip-recent.xml", modified=recent_dt.isoformat())

        client_folders = [_make_folder("client-1", "Acme Corp")]
        session_map = {"client-1": [_make_folder("session-1", "Episode 1")]}
        done_map = {
            "session-1": {
                "done_folder": _make_folder("done-1", "done"),
                "xml_files": [recent_xml],
            }
        }
        client_map = {"acme-corp": {"drive_folder_id": "client-1"}}
        mock_drive = self._build_drive_mock(client_folders, session_map, done_map)

        results = scan_done_folders(mock_drive, "clients-root", client_map, state)
        assert results == []

    def test_skips_processed_xmls(self, tmp_path):
        """XMLs already in DoneXmlState are skipped."""
        state = _make_state(tmp_path, processed_xml_ids=["xml-done"])

        xml_file = _make_xml_file("xml-done", "clip-old.xml")
        client_folders = [_make_folder("client-1", "Acme Corp")]
        session_map = {"client-1": [_make_folder("session-1", "Episode 1")]}
        done_map = {
            "session-1": {
                "done_folder": _make_folder("done-1", "done"),
                "xml_files": [xml_file],
            }
        }
        client_map = {"acme-corp": {"drive_folder_id": "client-1"}}
        mock_drive = self._build_drive_mock(client_folders, session_map, done_map)

        results = scan_done_folders(mock_drive, "clients-root", client_map, state)
        assert results == []

    def test_skips_session_without_source_video(self, tmp_path):
        """Sessions with no source video file in their root are skipped."""
        state = _make_state(tmp_path)

        xml_file = _make_xml_file("xml-1", "clip.xml")
        client_folders = [_make_folder("client-1", "Acme Corp")]
        session_map = {"client-1": [_make_folder("session-1", "Episode 1")]}
        done_map = {
            "session-1": {
                "done_folder": _make_folder("done-1", "done"),
                "xml_files": [xml_file],
            }
        }
        source_map = {"session-1": []}  # no video files
        client_map = {"acme-corp": {"drive_folder_id": "client-1"}}
        mock_drive = self._build_drive_mock(client_folders, session_map, done_map, source_map)

        results = scan_done_folders(mock_drive, "clients-root", client_map, state)
        assert results == []

    def test_result_shape(self, tmp_path):
        """Result dicts have all required keys."""
        state = _make_state(tmp_path)

        client_folders = [_make_folder("client-1", "Acme Corp")]
        session_map = {"client-1": [_make_folder("session-1", "The Episode")]}
        xml_file = _make_xml_file("xml-1", "my-clip.xml")
        source_video = _make_video_file("vid-1", "source.mp4")
        done_map = {
            "session-1": {
                "done_folder": _make_folder("done-1", "done"),
                "xml_files": [xml_file],
            }
        }
        source_map = {"session-1": [source_video]}
        client_map = {"acme-corp": {"drive_folder_id": "client-1"}}
        mock_drive = self._build_drive_mock(client_folders, session_map, done_map, source_map)

        results = scan_done_folders(mock_drive, "clients-root", client_map, state)

        assert len(results) == 1
        r = results[0]
        required_keys = {"client_slug", "session_folder_id", "session_name", "xml_file", "source_video_file"}
        assert required_keys.issubset(r.keys())

    def test_multiple_clients_and_sessions(self, tmp_path):
        """Handles multiple clients each with multiple sessions."""
        state = _make_state(tmp_path)

        client_folders = [
            _make_folder("client-1", "Client A"),
            _make_folder("client-2", "Client B"),
        ]
        session_map = {
            "client-1": [
                _make_folder("sess-1a", "Session 1A"),
                _make_folder("sess-1b", "Session 1B"),
            ],
            "client-2": [
                _make_folder("sess-2a", "Session 2A"),
            ],
        }
        xml_a = _make_xml_file("xml-1a", "clip-1a.xml")
        xml_b = _make_xml_file("xml-1b", "clip-1b.xml")
        xml_2 = _make_xml_file("xml-2a", "clip-2a.xml")
        done_map = {
            "sess-1a": {"done_folder": _make_folder("done-1a", "done"), "xml_files": [xml_a]},
            "sess-1b": {"done_folder": _make_folder("done-1b", "done"), "xml_files": [xml_b]},
            "sess-2a": {"done_folder": _make_folder("done-2a", "done"), "xml_files": [xml_2]},
        }
        source_map = {
            "sess-1a": [_make_video_file("vid-1a")],
            "sess-1b": [_make_video_file("vid-1b")],
            "sess-2a": [_make_video_file("vid-2a")],
        }
        client_map = {
            "client-a": {"drive_folder_id": "client-1"},
            "client-b": {"drive_folder_id": "client-2"},
        }
        mock_drive = self._build_drive_mock(client_folders, session_map, done_map, source_map)

        results = scan_done_folders(mock_drive, "clients-root", client_map, state)
        assert len(results) == 3

    def test_empty_done_folder_returns_nothing(self, tmp_path):
        """Session with an empty done/ folder yields no results."""
        state = _make_state(tmp_path)

        client_folders = [_make_folder("client-1", "Client A")]
        session_map = {"client-1": [_make_folder("sess-1", "Session 1")]}
        done_map = {
            "sess-1": {
                "done_folder": _make_folder("done-1", "done"),
                "xml_files": [],
            }
        }
        client_map = {"client-a": {"drive_folder_id": "client-1"}}
        mock_drive = self._build_drive_mock(client_folders, session_map, done_map)

        results = scan_done_folders(mock_drive, "clients-root", client_map, state)
        assert results == []

    def test_non_xml_files_are_skipped(self, tmp_path):
        """Non-.xml files in done/ are ignored."""
        state = _make_state(tmp_path)

        client_folders = [_make_folder("client-1", "Client A")]
        session_map = {"client-1": [_make_folder("sess-1", "Session 1")]}
        mp4_file = {
            "id": "vid-stray",
            "name": "stray-video.mp4",
            "mimeType": "video/mp4",
            "modifiedTime": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        }
        done_map = {
            "sess-1": {
                "done_folder": _make_folder("done-1", "done"),
                "xml_files": [mp4_file],
            }
        }
        client_map = {"client-a": {"drive_folder_id": "client-1"}}
        mock_drive = self._build_drive_mock(client_folders, session_map, done_map)

        results = scan_done_folders(mock_drive, "clients-root", client_map, state)
        assert results == []
