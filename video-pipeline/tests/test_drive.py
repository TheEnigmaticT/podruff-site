"""Tests for pipeline.drive — DriveClient auth and file listing."""
import json
import os
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock, mock_open, call

from pipeline.drive import DriveClient, DRIVE_API_BASE


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FAKE_CREDS = {
    "token": "old-access-token",
    "refresh_token": "my-refresh-token",
    "token_uri": "https://oauth2.googleapis.com/token",
    "client_id": "client-id-123",
    "client_secret": "client-secret-456",
    "scopes": ["https://www.googleapis.com/auth/drive.readonly"],
    "expiry": "2020-01-01T00:00:00+00:00",
}

FAKE_TOKEN_RESPONSE = {
    "access_token": "new-access-token",
    "expires_in": 3600,
    "token_type": "Bearer",
}

FAKE_FILES_RESPONSE = {
    "files": [
        {"id": "file-1", "name": "episode.mp4", "mimeType": "video/mp4", "modifiedTime": "2026-04-10T10:00:00Z"},
        {"id": "file-2", "name": "transcript.txt", "mimeType": "text/plain", "modifiedTime": "2026-04-10T11:00:00Z"},
    ]
}

FAKE_FOLDER_RESPONSE = {
    "files": [
        {"id": "folder-1", "name": "Client A", "mimeType": "application/vnd.google-apps.folder", "modifiedTime": "2026-04-10T09:00:00Z"},
        {"id": "folder-2", "name": "Client B", "mimeType": "application/vnd.google-apps.folder", "modifiedTime": "2026-04-11T09:00:00Z"},
    ]
}


def make_mock_urlopen(response_body: dict):
    """Return a context-manager mock that yields a response with .read()."""
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(response_body).encode()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


# ---------------------------------------------------------------------------
# Token refresh tests
# ---------------------------------------------------------------------------

class TestTokenRefresh:
    def test_refresh_reads_creds_file(self, tmp_path):
        creds_file = tmp_path / "creds.json"
        creds_file.write_text(json.dumps(FAKE_CREDS))

        mock_resp = make_mock_urlopen(FAKE_TOKEN_RESPONSE)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            client = DriveClient(str(creds_file))
            token = client.refresh_token()

        assert token == "new-access-token"

    def test_refresh_posts_to_token_uri(self, tmp_path):
        creds_file = tmp_path / "creds.json"
        creds_file.write_text(json.dumps(FAKE_CREDS))

        mock_resp = make_mock_urlopen(FAKE_TOKEN_RESPONSE)
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
            with patch("urllib.request.Request") as mock_request:
                client = DriveClient(str(creds_file))
                client.refresh_token()

            # Request should have been constructed with the token_uri
            args, kwargs = mock_request.call_args
            assert args[0] == FAKE_CREDS["token_uri"]

    def test_refresh_encodes_correct_form_fields(self, tmp_path):
        creds_file = tmp_path / "creds.json"
        creds_file.write_text(json.dumps(FAKE_CREDS))

        captured_data = {}

        def capture_request(url, data=None):
            captured_data["url"] = url
            captured_data["data"] = data
            return MagicMock()

        mock_resp = make_mock_urlopen(FAKE_TOKEN_RESPONSE)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            with patch("urllib.request.Request", side_effect=capture_request):
                client = DriveClient(str(creds_file))
                client.refresh_token()

        # Decode the posted form data
        from urllib.parse import parse_qs
        posted = parse_qs(captured_data["data"].decode())
        assert posted["grant_type"] == ["refresh_token"]
        assert posted["refresh_token"] == [FAKE_CREDS["refresh_token"]]
        assert posted["client_id"] == [FAKE_CREDS["client_id"]]
        assert posted["client_secret"] == [FAKE_CREDS["client_secret"]]

    def test_refresh_updates_token_in_creds_file(self, tmp_path):
        creds_file = tmp_path / "creds.json"
        creds_file.write_text(json.dumps(FAKE_CREDS))

        mock_resp = make_mock_urlopen(FAKE_TOKEN_RESPONSE)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            client = DriveClient(str(creds_file))
            client.refresh_token()

        updated = json.loads(creds_file.read_text())
        assert updated["token"] == "new-access-token"

    def test_refresh_writes_atomically(self, tmp_path):
        """Verifies write-to-.tmp + os.replace pattern."""
        creds_file = tmp_path / "creds.json"
        creds_file.write_text(json.dumps(FAKE_CREDS))
        tmp_file = str(creds_file) + ".tmp"

        mock_resp = make_mock_urlopen(FAKE_TOKEN_RESPONSE)
        replace_calls = []
        original_replace = os.replace

        def capture_replace(src, dst):
            replace_calls.append((src, dst))
            return original_replace(src, dst)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            with patch("os.replace", side_effect=capture_replace):
                client = DriveClient(str(creds_file))
                client.refresh_token()

        assert len(replace_calls) == 1
        assert replace_calls[0] == (tmp_file, str(creds_file))

    def test_refresh_updates_expiry_timestamp(self, tmp_path):
        creds_file = tmp_path / "creds.json"
        creds_file.write_text(json.dumps(FAKE_CREDS))

        mock_resp = make_mock_urlopen(FAKE_TOKEN_RESPONSE)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            client = DriveClient(str(creds_file))
            client.refresh_token()

        updated = json.loads(creds_file.read_text())
        # expiry should be a parseable ISO timestamp in the future
        expiry = datetime.fromisoformat(updated["expiry"])
        assert expiry > datetime.now(timezone.utc)

    def test_refresh_raises_on_http_error(self, tmp_path):
        import urllib.error
        creds_file = tmp_path / "creds.json"
        creds_file.write_text(json.dumps(FAKE_CREDS))

        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("401")):
            client = DriveClient(str(creds_file))
            with pytest.raises(urllib.error.URLError):
                client.refresh_token()


# ---------------------------------------------------------------------------
# list_files tests
# ---------------------------------------------------------------------------

class TestListFiles:
    def _make_client(self, tmp_path):
        creds_file = tmp_path / "creds.json"
        creds = dict(FAKE_CREDS)
        # Use a future expiry so token is not stale
        creds["expiry"] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        creds_file.write_text(json.dumps(creds))
        return DriveClient(str(creds_file))

    def test_list_files_returns_file_list(self, tmp_path):
        client = self._make_client(tmp_path)
        mock_resp = make_mock_urlopen(FAKE_FILES_RESPONSE)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            with patch("urllib.request.Request") as mock_req:
                files = client.list_files("folder-abc")

        assert len(files) == 2
        assert files[0]["id"] == "file-1"
        assert files[1]["id"] == "file-2"

    def test_list_files_queries_correct_folder(self, tmp_path):
        client = self._make_client(tmp_path)
        mock_resp = make_mock_urlopen(FAKE_FILES_RESPONSE)
        captured_urls = []

        def capture_req(url, *args, **kwargs):
            captured_urls.append(url)
            return MagicMock()

        with patch("urllib.request.urlopen", return_value=mock_resp):
            with patch("urllib.request.Request", side_effect=capture_req):
                client.list_files("folder-xyz")

        assert len(captured_urls) == 1
        assert "folder-xyz" in captured_urls[0]

    def test_list_files_sends_bearer_token(self, tmp_path):
        client = self._make_client(tmp_path)
        mock_resp = make_mock_urlopen(FAKE_FILES_RESPONSE)
        captured_headers = {}

        def capture_req(url, *args, **kwargs):
            req = MagicMock()
            req.add_header = lambda k, v: captured_headers.update({k: v})
            return req

        with patch("urllib.request.urlopen", return_value=mock_resp):
            with patch("urllib.request.Request", side_effect=capture_req):
                client.list_files("folder-abc")

        assert "Authorization" in captured_headers
        assert captured_headers["Authorization"].startswith("Bearer ")

    def test_list_files_filters_by_mime_type(self, tmp_path):
        client = self._make_client(tmp_path)
        mock_resp = make_mock_urlopen(FAKE_FILES_RESPONSE)
        captured_urls = []

        def capture_req(url, *args, **kwargs):
            captured_urls.append(url)
            return MagicMock()

        with patch("urllib.request.urlopen", return_value=mock_resp):
            with patch("urllib.request.Request", side_effect=capture_req):
                client.list_files("folder-abc", mime_types=["video/mp4"])

        assert "video%2Fmp4" in captured_urls[0] or "video/mp4" in captured_urls[0]

    def test_list_files_filters_by_modified_after(self, tmp_path):
        client = self._make_client(tmp_path)
        mock_resp = make_mock_urlopen(FAKE_FILES_RESPONSE)
        captured_urls = []
        cutoff = datetime(2026, 4, 9, tzinfo=timezone.utc)

        def capture_req(url, *args, **kwargs):
            captured_urls.append(url)
            return MagicMock()

        with patch("urllib.request.urlopen", return_value=mock_resp):
            with patch("urllib.request.Request", side_effect=capture_req):
                client.list_files("folder-abc", modified_after=cutoff)

        assert "modifiedTime" in captured_urls[0]
        # Verify that the timestamp includes timezone offset (RFC 3339 format)
        assert "%2B00%3A00" in captured_urls[0] or "+00:00" in captured_urls[0]

    def test_list_files_returns_empty_on_no_results(self, tmp_path):
        client = self._make_client(tmp_path)
        mock_resp = make_mock_urlopen({"files": []})
        with patch("urllib.request.urlopen", return_value=mock_resp):
            with patch("urllib.request.Request"):
                files = client.list_files("empty-folder")

        assert files == []

    def test_list_files_auto_refreshes_expired_token(self, tmp_path):
        """If token is expired, should refresh before calling Drive API."""
        creds_file = tmp_path / "creds.json"
        creds = dict(FAKE_CREDS)
        creds["expiry"] = "2020-01-01T00:00:00+00:00"  # stale
        creds_file.write_text(json.dumps(creds))
        client = DriveClient(str(creds_file))

        files_resp = make_mock_urlopen(FAKE_FILES_RESPONSE)
        token_resp = make_mock_urlopen(FAKE_TOKEN_RESPONSE)

        urlopen_responses = [token_resp, files_resp]
        with patch("urllib.request.urlopen", side_effect=urlopen_responses):
            with patch("urllib.request.Request"):
                files = client.list_files("some-folder")

        assert len(files) == 2


# ---------------------------------------------------------------------------
# list_folders tests
# ---------------------------------------------------------------------------

class TestListFolders:
    def _make_client(self, tmp_path):
        creds_file = tmp_path / "creds.json"
        creds = dict(FAKE_CREDS)
        creds["expiry"] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        creds_file.write_text(json.dumps(creds))
        return DriveClient(str(creds_file))

    def test_list_folders_returns_only_folders(self, tmp_path):
        client = self._make_client(tmp_path)
        mock_resp = make_mock_urlopen(FAKE_FOLDER_RESPONSE)
        captured_urls = []

        def capture_req(url, *args, **kwargs):
            captured_urls.append(url)
            return MagicMock()

        with patch("urllib.request.urlopen", return_value=mock_resp):
            with patch("urllib.request.Request", side_effect=capture_req):
                folders = client.list_folders("root-id")

        # Should filter for folder mime type — the URL-encoded form uses %2F for /
        # urllib.parse.urlencode percent-encodes slashes but leaves dots intact
        folder_mime_encoded = "application%2Fvnd.google-apps.folder"
        assert folder_mime_encoded in captured_urls[0]
        assert len(folders) == 2

    def test_list_folders_passes_modified_after(self, tmp_path):
        client = self._make_client(tmp_path)
        mock_resp = make_mock_urlopen(FAKE_FOLDER_RESPONSE)
        captured_urls = []
        cutoff = datetime(2026, 4, 10, tzinfo=timezone.utc)

        def capture_req(url, *args, **kwargs):
            captured_urls.append(url)
            return MagicMock()

        with patch("urllib.request.urlopen", return_value=mock_resp):
            with patch("urllib.request.Request", side_effect=capture_req):
                client.list_folders("root-id", modified_after=cutoff)

        assert "modifiedTime" in captured_urls[0]

    def test_list_folders_returns_empty_list(self, tmp_path):
        client = self._make_client(tmp_path)
        mock_resp = make_mock_urlopen({"files": []})
        with patch("urllib.request.urlopen", return_value=mock_resp):
            with patch("urllib.request.Request"):
                folders = client.list_folders("empty-parent")

        assert folders == []


# ---------------------------------------------------------------------------
# download_file tests
# ---------------------------------------------------------------------------

class TestDownloadFile:
    def _make_client(self, tmp_path):
        creds_file = tmp_path / "creds.json"
        creds = dict(FAKE_CREDS)
        creds["expiry"] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        creds_file.write_text(json.dumps(creds))
        return DriveClient(str(creds_file))

    def test_download_file_writes_content_to_disk(self, tmp_path):
        client = self._make_client(tmp_path)
        dest = tmp_path / "output" / "file.mp4"

        fake_content = b"fake video content"
        mock_resp = MagicMock()
        mock_resp.read.side_effect = [fake_content, b""]
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            with patch("urllib.request.Request") as mock_req:
                client.download_file("file-abc", str(dest))

        assert dest.exists()
        assert dest.read_bytes() == fake_content

    def test_download_file_creates_parent_directories(self, tmp_path):
        client = self._make_client(tmp_path)
        dest = tmp_path / "deep" / "nested" / "dir" / "file.mp4"

        fake_content = b"data"
        mock_resp = MagicMock()
        mock_resp.read.side_effect = [fake_content, b""]
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            with patch("urllib.request.Request"):
                client.download_file("file-abc", str(dest))

        assert dest.parent.exists()

    def test_download_file_requests_correct_url(self, tmp_path):
        client = self._make_client(tmp_path)
        dest = tmp_path / "file.mp4"
        captured_urls = []

        def capture_req(url, *args, **kwargs):
            captured_urls.append(url)
            req = MagicMock()
            req.add_header = MagicMock()
            return req

        mock_resp = MagicMock()
        mock_resp.read.side_effect = [b"data", b""]
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            with patch("urllib.request.Request", side_effect=capture_req):
                client.download_file("file-xyz", str(dest))

        assert len(captured_urls) == 1
        assert "file-xyz" in captured_urls[0]
        assert "alt=media" in captured_urls[0]

    def test_download_file_sends_bearer_token(self, tmp_path):
        client = self._make_client(tmp_path)
        dest = tmp_path / "file.mp4"
        captured_headers = {}

        def capture_req(url, *args, **kwargs):
            req = MagicMock()
            req.add_header = lambda k, v: captured_headers.update({k: v})
            return req

        mock_resp = MagicMock()
        mock_resp.read.side_effect = [b"data", b""]
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            with patch("urllib.request.Request", side_effect=capture_req):
                client.download_file("file-xyz", str(dest))

        assert "Authorization" in captured_headers
        assert captured_headers["Authorization"].startswith("Bearer ")


# ---------------------------------------------------------------------------
# upload_file tests
# ---------------------------------------------------------------------------

class TestUploadFile:
    def _make_client(self, tmp_path):
        creds_file = tmp_path / "creds.json"
        creds = dict(FAKE_CREDS)
        creds["expiry"] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        creds_file.write_text(json.dumps(creds))
        return DriveClient(str(creds_file))

    def test_upload_file_returns_new_file_id(self, tmp_path):
        client = self._make_client(tmp_path)
        src = tmp_path / "clip.mp4"
        src.write_bytes(b"video data")

        upload_resp = make_mock_urlopen({"id": "new-file-id-123"})

        with patch("urllib.request.urlopen", return_value=upload_resp):
            with patch("urllib.request.Request"):
                file_id = client.upload_file(str(src), "parent-folder-id")

        assert file_id == "new-file-id-123"

    def test_upload_file_sends_multipart_body(self, tmp_path):
        client = self._make_client(tmp_path)
        src = tmp_path / "clip.mp4"
        src.write_bytes(b"video data")
        captured_requests = []

        def capture_req(url, data=None, headers=None, method=None, **kwargs):
            req = MagicMock()
            req.add_header = lambda k, v: captured_requests.append({"header": k, "value": v, "url": url, "data": data})
            return req

        upload_resp = make_mock_urlopen({"id": "new-file-id-123"})

        with patch("urllib.request.urlopen", return_value=upload_resp):
            with patch("urllib.request.Request", side_effect=capture_req):
                client.upload_file(str(src), "parent-folder-id")

        # Should hit the multipart upload endpoint
        urls = [r["url"] for r in captured_requests]
        assert any("uploadType=multipart" in u for u in urls)

    def test_upload_file_uses_filename_as_default_name(self, tmp_path):
        client = self._make_client(tmp_path)
        src = tmp_path / "my_clip.mp4"
        src.write_bytes(b"data")
        captured_bodies = []

        def capture_req(url, data=None, **kwargs):
            req = MagicMock()
            req.add_header = MagicMock()
            captured_bodies.append(data)
            return req

        upload_resp = make_mock_urlopen({"id": "fid"})

        with patch("urllib.request.urlopen", return_value=upload_resp):
            with patch("urllib.request.Request", side_effect=capture_req):
                client.upload_file(str(src), "parent-folder-id")

        # The multipart body should contain the filename
        body = b"".join(b for b in captured_bodies if b is not None)
        assert b"my_clip.mp4" in body

    def test_upload_file_uses_custom_name_when_provided(self, tmp_path):
        client = self._make_client(tmp_path)
        src = tmp_path / "my_clip.mp4"
        src.write_bytes(b"data")
        captured_bodies = []

        def capture_req(url, data=None, **kwargs):
            req = MagicMock()
            req.add_header = MagicMock()
            captured_bodies.append(data)
            return req

        upload_resp = make_mock_urlopen({"id": "fid"})

        with patch("urllib.request.urlopen", return_value=upload_resp):
            with patch("urllib.request.Request", side_effect=capture_req):
                client.upload_file(str(src), "parent-folder-id", name="custom_name.mp4")

        body = b"".join(b for b in captured_bodies if b is not None)
        assert b"custom_name.mp4" in body

    def test_upload_file_includes_parent_in_metadata(self, tmp_path):
        client = self._make_client(tmp_path)
        src = tmp_path / "clip.mp4"
        src.write_bytes(b"data")
        captured_bodies = []

        def capture_req(url, data=None, **kwargs):
            req = MagicMock()
            req.add_header = MagicMock()
            captured_bodies.append(data)
            return req

        upload_resp = make_mock_urlopen({"id": "fid"})

        with patch("urllib.request.urlopen", return_value=upload_resp):
            with patch("urllib.request.Request", side_effect=capture_req):
                client.upload_file(str(src), "my-parent-folder")

        body = b"".join(b for b in captured_bodies if b is not None)
        assert b"my-parent-folder" in body


# ---------------------------------------------------------------------------
# create_folder tests
# ---------------------------------------------------------------------------

class TestCreateFolder:
    def _make_client(self, tmp_path):
        creds_file = tmp_path / "creds.json"
        creds = dict(FAKE_CREDS)
        creds["expiry"] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        creds_file.write_text(json.dumps(creds))
        return DriveClient(str(creds_file))

    def test_create_folder_returns_folder_id(self, tmp_path):
        client = self._make_client(tmp_path)
        folder_resp = make_mock_urlopen({"id": "new-folder-id-456"})

        with patch("urllib.request.urlopen", return_value=folder_resp):
            with patch("urllib.request.Request"):
                folder_id = client.create_folder("My Folder", "parent-id-789")

        assert folder_id == "new-folder-id-456"

    def test_create_folder_sends_correct_mime_type(self, tmp_path):
        client = self._make_client(tmp_path)
        captured_bodies = []

        def capture_req(url, data=None, **kwargs):
            req = MagicMock()
            req.add_header = MagicMock()
            captured_bodies.append(data)
            return req

        folder_resp = make_mock_urlopen({"id": "fid"})

        with patch("urllib.request.urlopen", return_value=folder_resp):
            with patch("urllib.request.Request", side_effect=capture_req):
                client.create_folder("My Folder", "parent-id")

        body = b"".join(b for b in captured_bodies if b is not None)
        assert b"application/vnd.google-apps.folder" in body

    def test_create_folder_sends_folder_name(self, tmp_path):
        client = self._make_client(tmp_path)
        captured_bodies = []

        def capture_req(url, data=None, **kwargs):
            req = MagicMock()
            req.add_header = MagicMock()
            captured_bodies.append(data)
            return req

        folder_resp = make_mock_urlopen({"id": "fid"})

        with patch("urllib.request.urlopen", return_value=folder_resp):
            with patch("urllib.request.Request", side_effect=capture_req):
                client.create_folder("Episode 42", "parent-id")

        body = b"".join(b for b in captured_bodies if b is not None)
        assert b"Episode 42" in body

    def test_create_folder_sends_parent_id(self, tmp_path):
        client = self._make_client(tmp_path)
        captured_bodies = []

        def capture_req(url, data=None, **kwargs):
            req = MagicMock()
            req.add_header = MagicMock()
            captured_bodies.append(data)
            return req

        folder_resp = make_mock_urlopen({"id": "fid"})

        with patch("urllib.request.urlopen", return_value=folder_resp):
            with patch("urllib.request.Request", side_effect=capture_req):
                client.create_folder("My Folder", "specific-parent-id-xyz")

        body = b"".join(b for b in captured_bodies if b is not None)
        assert b"specific-parent-id-xyz" in body

    def test_create_folder_posts_to_files_endpoint(self, tmp_path):
        client = self._make_client(tmp_path)
        captured_urls = []

        def capture_req(url, data=None, **kwargs):
            captured_urls.append(url)
            req = MagicMock()
            req.add_header = MagicMock()
            return req

        folder_resp = make_mock_urlopen({"id": "fid"})

        with patch("urllib.request.urlopen", return_value=folder_resp):
            with patch("urllib.request.Request", side_effect=capture_req):
                client.create_folder("My Folder", "parent-id")

        assert any("drive/v3/files" in u for u in captured_urls)
