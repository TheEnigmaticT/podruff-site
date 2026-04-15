# Drive-Native Video Pipeline v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automate end-to-end video processing: Zencastr recordings in Google Drive are detected, transcribed, editorially analyzed, converted to FCP 7 XML for editors, and rendered with client branding into publishable shorts.

**Architecture:** A polling loop (dispatched every 15 min by OpenClaw heartbeat) scans the Zencastr Drive folder for new sessions, downloads source video, runs the existing editorial pipeline, generates FCP 7 XML, and uploads results to the client's Drive folder. A second poll watches `done/` folders for editor-tweaked XMLs, re-renders with branding (subtitles from SOUL.md, end card), and uploads finals to R2 + Notion. All state tracked in `.processed.json` for resume-on-failure.

**Tech Stack:** Python 3.14, Google Drive API v3 (raw REST, OAuth token refresh), FFmpeg, Parakeet-MLX, Ollama (qwen3:8b), FCP 7 XML (ElementTree), R2 (boto3), Slack API.

**Spec:** `docs/superpowers/specs/2026-04-15-drive-native-pipeline-v2-design.md`

**Existing auth pattern:** `call_task_crawler.py` in openclaw-kanban shows how to refresh OAuth tokens and call Drive API using raw `urllib.request`. We follow the same pattern to avoid adding `google-api-python-client` as a dependency.

---

## File Structure

| File | Responsibility |
|------|---------------|
| `pipeline/drive.py` | Google Drive API wrapper: auth, list, download, upload, create folder |
| `pipeline/drive_poller.py` | Ingest polling (Zencastr), done-folder polling, `.processed.json` state |
| `pipeline/fcp7.py` | FCP 7 XML generation from EDLs, parsing editor's XMLs back to EDLs |
| `pipeline/branding.py` | Read SOUL.md, generate end card, apply subtitle styling, render branded final |
| `pipeline/transcript_repair.py` | LLM-based transcript repair with confidence tiers |
| `pipeline/client_config.py` | Load client config from `client-map.json` and SOUL.md |
| `pipeline/config.py` | (modify) Add Drive credentials path, Zencastr folder ID |
| `pipeline/cli.py` | (modify) Add `poll-all` and `render-final` commands |
| `pipeline/edl.py` | (modify) Wire `generate_fcp7_xml()` as import from `fcp7.py` |
| `tests/test_drive.py` | Drive client tests |
| `tests/test_drive_poller.py` | Poller state machine tests |
| `tests/test_fcp7.py` | FCP 7 XML generation/parsing tests |
| `tests/test_branding.py` | End card, subtitle styling tests |
| `tests/test_transcript_repair.py` | Repair confidence tier tests |
| `tests/test_client_config.py` | SOUL.md parsing tests |

---

## Task 1: Google Drive Client — Auth + List Files

**Files:**
- Create: `pipeline/drive.py`
- Create: `tests/test_drive.py`
- Modify: `pipeline/config.py`

- [ ] **Step 1: Add Drive config to `pipeline/config.py`**

Add to the end of `pipeline/config.py`:

```python
GOOGLE_CREDS_PATH = os.environ.get(
    "GOOGLE_CREDS_PATH",
    os.path.expanduser("~/.google_workspace_mcp/credentials/tlongino@crowdtamers.com.json"),
)
ZENCASTR_FOLDER_ID = os.environ.get("ZENCASTR_FOLDER_ID", "")
DRIVE_CLIENTS_ROOT = os.environ.get("DRIVE_CLIENTS_ROOT", "")
```

- [ ] **Step 2: Write failing tests for Drive auth and list_files**

```python
# tests/test_drive.py
import json
import os
from unittest.mock import patch, MagicMock
import pytest
from pipeline.drive import DriveClient


@pytest.fixture
def fake_creds(tmp_path):
    creds = {
        "token": "old-token",
        "refresh_token": "refresh-123",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "client-id",
        "client_secret": "client-secret",
        "scopes": ["https://www.googleapis.com/auth/drive"],
        "expiry": "2026-01-01T00:00:00+00:00",
    }
    path = tmp_path / "creds.json"
    path.write_text(json.dumps(creds))
    return str(path)


def test_refresh_token(fake_creds):
    """refresh_token fetches a new access token and updates creds file."""
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps({
        "access_token": "new-token-abc",
        "expires_in": 3600,
    }).encode()
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("pipeline.drive.urllib.request.urlopen", return_value=mock_response):
        client = DriveClient(creds_path=fake_creds)
        token = client._refresh_token()

    assert token == "new-token-abc"
    updated = json.loads(open(fake_creds).read())
    assert updated["token"] == "new-token-abc"


def test_list_files(fake_creds):
    """list_files returns file metadata from a Drive folder."""
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps({
        "files": [
            {"id": "abc123", "name": "source.mp4", "mimeType": "video/mp4",
             "modifiedTime": "2026-04-14T10:00:00Z"},
        ]
    }).encode()
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("pipeline.drive.urllib.request.urlopen", return_value=mock_response):
        client = DriveClient(creds_path=fake_creds)
        client._access_token = "test-token"
        files = client.list_files("folder-id-123")

    assert len(files) == 1
    assert files[0]["name"] == "source.mp4"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd /Users/ct-mac-mini/dev/podruff-site/video-pipeline && .venv/bin/python -m pytest tests/test_drive.py -v`
Expected: FAIL (module not found)

- [ ] **Step 4: Implement `pipeline/drive.py` — auth + list**

```python
"""Google Drive API wrapper using raw REST (no google-api-python-client dependency)."""

import json
import logging
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

DRIVE_API = "https://www.googleapis.com/drive/v3"


class DriveClient:
    def __init__(self, creds_path=None):
        self._creds_path = creds_path or os.environ.get(
            "GOOGLE_CREDS_PATH",
            os.path.expanduser("~/.google_workspace_mcp/credentials/tlongino@crowdtamers.com.json"),
        )
        self._access_token = None

    def _refresh_token(self):
        with open(self._creds_path) as f:
            creds = json.load(f)

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

        tmp = self._creds_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(creds, f, indent=2)
        os.replace(tmp, self._creds_path)

        self._access_token = body["access_token"]
        return self._access_token

    def _get_token(self):
        if self._access_token:
            return self._access_token
        return self._refresh_token()

    def _api_get(self, url, params=None):
        token = self._get_token()
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())

    def list_files(self, folder_id, mime_types=None, modified_after=None):
        query_parts = [f"'{folder_id}' in parents", "trashed = false"]
        if mime_types:
            mime_filter = " or ".join(f"mimeType = '{m}'" for m in mime_types)
            query_parts.append(f"({mime_filter})")
        if modified_after:
            query_parts.append(f"modifiedTime > '{modified_after}'")

        params = {
            "q": " and ".join(query_parts),
            "fields": "files(id,name,mimeType,modifiedTime,size)",
            "orderBy": "modifiedTime desc",
            "pageSize": 100,
        }
        data = self._api_get(f"{DRIVE_API}/files", params)
        return data.get("files", [])

    def list_folders(self, parent_id, modified_after=None):
        return self.list_files(
            parent_id,
            mime_types=["application/vnd.google-apps.folder"],
            modified_after=modified_after,
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_drive.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Commit**

```bash
git add pipeline/drive.py pipeline/config.py tests/test_drive.py
git commit -m "feat(drive): add Drive client with auth and list_files"
```

---

## Task 2: Google Drive Client — Download + Upload

**Files:**
- Modify: `pipeline/drive.py`
- Modify: `tests/test_drive.py`

- [ ] **Step 1: Write failing tests for download and upload**

Add to `tests/test_drive.py`:

```python
def test_download_file(fake_creds, tmp_path):
    """download_file streams file content to local path."""
    mock_response = MagicMock()
    mock_response.read.side_effect = [b"video-content-bytes", b""]
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("pipeline.drive.urllib.request.urlopen", return_value=mock_response):
        client = DriveClient(creds_path=fake_creds)
        client._access_token = "test-token"
        dest = tmp_path / "out.mp4"
        client.download_file("file-id-123", str(dest))

    assert dest.exists()
    assert dest.read_bytes() == b"video-content-bytes"


def test_upload_file(fake_creds, tmp_path):
    """upload_file uploads local file to Drive folder."""
    src = tmp_path / "test.mp4"
    src.write_bytes(b"fake-video-data")

    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps({"id": "new-file-id"}).encode()
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("pipeline.drive.urllib.request.urlopen", return_value=mock_response):
        client = DriveClient(creds_path=fake_creds)
        client._access_token = "test-token"
        file_id = client.upload_file(str(src), "parent-folder-id", "test.mp4")

    assert file_id == "new-file-id"


def test_create_folder(fake_creds):
    """create_folder creates a new folder in Drive."""
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps({"id": "new-folder-id"}).encode()
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("pipeline.drive.urllib.request.urlopen", return_value=mock_response):
        client = DriveClient(creds_path=fake_creds)
        client._access_token = "test-token"
        folder_id = client.create_folder("My Folder", "parent-id")

    assert folder_id == "new-folder-id"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_drive.py -v`
Expected: FAIL (methods not defined)

- [ ] **Step 3: Implement download, upload, create_folder in `pipeline/drive.py`**

Add to the `DriveClient` class:

```python
    def download_file(self, file_id, local_path):
        token = self._get_token()
        url = f"{DRIVE_API}/files/{file_id}?alt=media"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
        with urllib.request.urlopen(req, timeout=600) as resp:
            with open(local_path, "wb") as f:
                while True:
                    chunk = resp.read(8 * 1024 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
        logger.info("Downloaded %s to %s", file_id, local_path)

    def upload_file(self, local_path, parent_folder_id, name=None):
        token = self._get_token()
        name = name or os.path.basename(local_path)
        metadata = json.dumps({"name": name, "parents": [parent_folder_id]}).encode()

        boundary = "----PipelineUploadBoundary"
        content_type = "application/octet-stream"
        with open(local_path, "rb") as f:
            file_data = f.read()

        body = (
            f"--{boundary}\r\n"
            f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
        ).encode() + metadata + (
            f"\r\n--{boundary}\r\n"
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode() + file_data + f"\r\n--{boundary}--\r\n".encode()

        req = urllib.request.Request(
            "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart",
            data=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": f"multipart/related; boundary={boundary}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=600) as resp:
            result = json.loads(resp.read())
        logger.info("Uploaded %s as %s", name, result["id"])
        return result["id"]

    def create_folder(self, name, parent_id):
        token = self._get_token()
        metadata = json.dumps({
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_id],
        }).encode()
        req = urllib.request.Request(
            f"{DRIVE_API}/files",
            data=metadata,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
        logger.info("Created folder '%s' (%s)", name, result["id"])
        return result["id"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_drive.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add pipeline/drive.py tests/test_drive.py
git commit -m "feat(drive): add download, upload, create_folder"
```

---

## Task 3: Client Config — SOUL.md + client-map.json

**Files:**
- Create: `pipeline/client_config.py`
- Create: `tests/test_client_config.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_client_config.py
import json
import pytest
from pipeline.client_config import load_soul, match_client


@pytest.fixture
def soul_dir(tmp_path):
    workspace = tmp_path / "workspaces" / "jonathan-brill"
    workspace.mkdir(parents=True)
    soul = workspace / "SOUL.md"
    soul.write_text("""# Jonathan Brill

## Brand Identity
- **Brand color:** #E38533 (orange)

## Subtitle Styling
- Highlight color: #E38533 (orange), ASS BGR: &H003385E3
- Font: Inter (fallback from Milibus)
- Style: Karaoke word-by-word highlight on shorts

## Slack Channels
- **Internal:** `#jonathan-brill` (C05SGKQAQGK)

## Notion
- Content Automation Pipeline: https://www.notion.so/crowdtamers/342f778746cb80ae821dc8bd79ae7506
""")
    return tmp_path


@pytest.fixture
def client_map(tmp_path):
    cm = tmp_path / "client_map.json"
    cm.write_text(json.dumps({
        "jonathan-brill": {
            "drive_folder_id": "drive-folder-abc",
            "aliases": ["Jonathan Brill", "Brill"],
        },
        "ogment": {
            "drive_folder_id": "drive-folder-def",
            "aliases": ["Ogment", "Ogment AI", "Ogment.ai"],
        },
    }))
    return str(cm)


def test_load_soul(soul_dir):
    soul = load_soul("jonathan-brill", workspaces_dir=str(soul_dir / "workspaces"))
    assert soul["brand_color"] == "#E38533"
    assert soul["subtitle_highlight"] == "#E38533"
    assert soul["subtitle_font"] == "Inter"
    assert soul["slack_channel"] == "C05SGKQAQGK"


def test_match_client(client_map):
    assert match_client("Jonathan Brill Content Call M3 S1", client_map_path=client_map) == "jonathan-brill"
    assert match_client("Ogment AI Content Call M1 S2", client_map_path=client_map) == "ogment"
    assert match_client("Unknown Corp Meeting", client_map_path=client_map) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_client_config.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement `pipeline/client_config.py`**

```python
"""Load client configuration from SOUL.md and client-map.json."""

import json
import logging
import os
import re

logger = logging.getLogger(__name__)

DEFAULT_WORKSPACES = os.path.expanduser("~/.openclaw/workspaces")
DEFAULT_CLIENT_MAP = os.path.expanduser("~/dev/openclaw-kanban/client_map.json")


def load_soul(client_slug, workspaces_dir=None):
    workspaces_dir = workspaces_dir or DEFAULT_WORKSPACES
    soul_path = os.path.join(workspaces_dir, client_slug, "SOUL.md")
    if not os.path.exists(soul_path):
        logger.warning("No SOUL.md for %s at %s", client_slug, soul_path)
        return {}

    with open(soul_path) as f:
        text = f.read()

    def _extract(pattern, default=""):
        m = re.search(pattern, text)
        return m.group(1).strip() if m else default

    brand_color = _extract(r"\*\*Brand color[:\*]*\s*[#]?([A-Fa-f0-9]{6})")
    if brand_color and not brand_color.startswith("#"):
        brand_color = f"#{brand_color}"

    subtitle_color = _extract(r"Highlight color:\s*#([A-Fa-f0-9]{6})")
    if subtitle_color:
        subtitle_color = f"#{subtitle_color}"

    subtitle_font = _extract(r"Font:\s*(\w[\w\s]*?)(?:\s*\(|$)", "Inter")
    slack_match = re.search(r"Internal.*?`#[\w-]+`\s*\((\w+)\)", text)
    slack_channel = slack_match.group(1) if slack_match else ""

    notion_match = re.search(r"https://www\.notion\.so/crowdtamers/([a-f0-9]+)", text)
    notion_db = notion_match.group(1) if notion_match else ""

    return {
        "brand_color": brand_color or "#1FC2F9",
        "subtitle_highlight": subtitle_color or brand_color or "#1FC2F9",
        "subtitle_font": subtitle_font,
        "slack_channel": slack_channel,
        "notion_db": notion_db,
        "raw": text,
    }


def _load_client_map(client_map_path=None):
    path = client_map_path or DEFAULT_CLIENT_MAP
    with open(path) as f:
        return json.load(f)


def match_client(session_name, client_map_path=None):
    cm = _load_client_map(client_map_path)
    name_lower = session_name.lower()

    for slug, cfg in cm.items():
        aliases = cfg.get("aliases", [slug])
        for alias in aliases:
            if alias.lower() in name_lower:
                return slug

    return None


def get_drive_folder(client_slug, client_map_path=None):
    cm = _load_client_map(client_map_path)
    entry = cm.get(client_slug, {})
    return entry.get("drive_folder_id", "")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_client_config.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add pipeline/client_config.py tests/test_client_config.py
git commit -m "feat: add client config loader (SOUL.md + client-map.json)"
```

---

## Task 4: Drive Poller — State Tracking + Zencastr Ingest Detection

**Files:**
- Create: `pipeline/drive_poller.py`
- Create: `tests/test_drive_poller.py`

- [ ] **Step 1: Write failing tests for state tracking**

```python
# tests/test_drive_poller.py
import json
import pytest
from pipeline.drive_poller import ProcessingState


@pytest.fixture
def state_path(tmp_path):
    return str(tmp_path / "processed.json")


def test_empty_state(state_path):
    state = ProcessingState(state_path)
    assert state.is_processed("session-abc") is False


def test_mark_processed(state_path):
    state = ProcessingState(state_path)
    state.mark_step("session-abc", "transcribed")
    assert state.is_processed("session-abc") is False
    assert state.get_step("session-abc") == "transcribed"


def test_mark_complete(state_path):
    state = ProcessingState(state_path)
    state.mark_complete("session-abc")
    assert state.is_processed("session-abc") is True


def test_state_persists(state_path):
    state1 = ProcessingState(state_path)
    state1.mark_complete("session-abc")

    state2 = ProcessingState(state_path)
    assert state2.is_processed("session-abc") is True


def test_resume_from_step(state_path):
    state = ProcessingState(state_path)
    state.mark_step("session-abc", "editorial")
    assert state.get_step("session-abc") == "editorial"
    assert state.is_processed("session-abc") is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_drive_poller.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement `pipeline/drive_poller.py` — state tracking**

```python
"""Drive polling: detect new Zencastr sessions, track processing state."""

import json
import logging
import os

logger = logging.getLogger(__name__)


class ProcessingState:
    def __init__(self, path=None):
        self._path = path or os.path.expanduser("~/.openclaw/video-pipeline-state.json")
        self._data = self._load()

    def _load(self):
        try:
            with open(self._path) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

    def _save(self):
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        tmp = self._path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self._data, f, indent=2)
        os.replace(tmp, self._path)

    def is_processed(self, session_id):
        return self._data.get(session_id, {}).get("complete", False)

    def get_step(self, session_id):
        return self._data.get(session_id, {}).get("step", "")

    def mark_step(self, session_id, step):
        if session_id not in self._data:
            self._data[session_id] = {}
        self._data[session_id]["step"] = step
        self._save()

    def mark_complete(self, session_id):
        if session_id not in self._data:
            self._data[session_id] = {}
        self._data[session_id]["complete"] = True
        self._data[session_id]["step"] = "complete"
        self._save()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_drive_poller.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add pipeline/drive_poller.py tests/test_drive_poller.py
git commit -m "feat(poller): add ProcessingState for resume-on-failure"
```

---

## Task 5: Drive Poller — Ingest Scanner

**Files:**
- Modify: `pipeline/drive_poller.py`
- Modify: `tests/test_drive_poller.py`

- [ ] **Step 1: Write failing tests for ingest scanning**

Add to `tests/test_drive_poller.py`:

```python
from unittest.mock import MagicMock
from pipeline.drive_poller import scan_zencastr_sessions


def test_scan_finds_new_sessions():
    mock_drive = MagicMock()
    mock_drive.list_folders.return_value = [
        {"id": "folder-1", "name": "Jonathan Brill Content Call M3 S1", "modifiedTime": "2026-04-14T18:00:00Z"},
        {"id": "folder-2", "name": "Ogment AI Content Call M2 S1", "modifiedTime": "2026-04-14T17:00:00Z"},
    ]
    mock_drive.list_files.side_effect = [
        [{"id": "vid-1", "name": "source.mp4", "mimeType": "video/mp4"}],
        [{"id": "vid-2", "name": "recording.mp4", "mimeType": "video/mp4"}],
    ]

    state = ProcessingState(state_path)
    sessions = scan_zencastr_sessions(mock_drive, "zencastr-root-id", state)

    assert len(sessions) == 2
    assert sessions[0]["folder_name"] == "Jonathan Brill Content Call M3 S1"
    assert sessions[0]["video_files"][0]["id"] == "vid-1"


def test_scan_skips_processed():
    mock_drive = MagicMock()
    mock_drive.list_folders.return_value = [
        {"id": "folder-1", "name": "Session One", "modifiedTime": "2026-04-14T18:00:00Z"},
    ]

    state = ProcessingState(state_path)
    state.mark_complete("folder-1")
    sessions = scan_zencastr_sessions(mock_drive, "zencastr-root-id", state)

    assert len(sessions) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_drive_poller.py::test_scan_finds_new_sessions -v`
Expected: FAIL (function not defined)

- [ ] **Step 3: Implement `scan_zencastr_sessions`**

Add to `pipeline/drive_poller.py`:

```python
VIDEO_EXTENSIONS = (".mp4", ".webm", ".mkv", ".mov")


def scan_zencastr_sessions(drive_client, zencastr_folder_id, state):
    folders = drive_client.list_folders(zencastr_folder_id)
    sessions = []

    for folder in folders:
        folder_id = folder["id"]
        if state.is_processed(folder_id):
            continue

        video_files = [
            f for f in drive_client.list_files(folder_id)
            if any(f["name"].lower().endswith(ext) for ext in VIDEO_EXTENSIONS)
        ]
        if not video_files:
            continue

        sessions.append({
            "folder_id": folder_id,
            "folder_name": folder["name"],
            "modified": folder.get("modifiedTime", ""),
            "video_files": video_files,
            "resume_step": state.get_step(folder_id),
        })

    logger.info("Found %d unprocessed sessions in Zencastr folder", len(sessions))
    return sessions
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_drive_poller.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add pipeline/drive_poller.py tests/test_drive_poller.py
git commit -m "feat(poller): add scan_zencastr_sessions"
```

---

## Task 6: FCP 7 XML Generation

**Files:**
- Create: `pipeline/fcp7.py`
- Create: `tests/test_fcp7.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_fcp7.py
import xml.etree.ElementTree as ET
from pipeline.fcp7 import generate_fcp7_xml


def test_generate_basic_fcp7():
    """Generate FCP 7 XML from a simple EDL version."""
    edl_version = {
        "segments": [
            {"type": "hook", "start": 100.0, "end": 107.5},
            {"type": "body", "start": 90.0, "end": 100.0},
        ],
        "trims": [],
    }
    xml_str = generate_fcp7_xml(
        edl_version,
        source_video="/path/to/source.mp4",
        sequence_name="test-clip-short",
        fps=30,
    )

    root = ET.fromstring(xml_str)
    assert root.tag == "xmeml"
    assert root.attrib["version"] == "5"

    seq = root.find(".//sequence")
    assert seq is not None
    assert seq.find("name").text == "test-clip-short"

    clips = root.findall(".//clipitem")
    assert len(clips) == 2

    first_in = int(clips[0].find("in").text)
    first_out = int(clips[0].find("out").text)
    assert first_in == 3000  # 100.0 * 30
    assert first_out == 3225  # 107.5 * 30


def test_fcp7_has_audio_track():
    """FCP 7 XML should include both video and audio tracks."""
    edl_version = {
        "segments": [{"type": "body", "start": 10.0, "end": 20.0}],
        "trims": [],
    }
    xml_str = generate_fcp7_xml(
        edl_version,
        source_video="/path/to/source.mp4",
        sequence_name="audio-test",
    )
    root = ET.fromstring(xml_str)
    video_track = root.find(".//video/track")
    audio_track = root.find(".//audio/track")
    assert video_track is not None
    assert audio_track is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_fcp7.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement `pipeline/fcp7.py`**

```python
"""FCP 7 XML generation and parsing for editorial interchange."""

import os
import xml.etree.ElementTree as ET

from pipeline.edl import resolve_segments


def generate_fcp7_xml(
    edl_version,
    source_video,
    sequence_name="clip",
    fps=30,
    width=1920,
    height=1080,
):
    time_ranges = resolve_segments(
        edl_version["segments"], edl_version.get("trims", [])
    )
    clip_name = os.path.basename(source_video)
    total_frames = sum(int((e - s) * fps) for s, e in time_ranges)

    xmeml = ET.Element("xmeml", version="5")
    seq = ET.SubElement(xmeml, "sequence")
    ET.SubElement(seq, "name").text = sequence_name
    ET.SubElement(seq, "duration").text = str(total_frames)

    rate = ET.SubElement(seq, "rate")
    ET.SubElement(rate, "timebase").text = str(fps)
    ET.SubElement(rate, "ntsc").text = "FALSE"

    media = ET.SubElement(seq, "media")

    # Video track
    video = ET.SubElement(media, "video")
    fmt = ET.SubElement(video, "format")
    sc = ET.SubElement(fmt, "samplecharacteristics")
    ET.SubElement(sc, "width").text = str(width)
    ET.SubElement(sc, "height").text = str(height)

    v_track = ET.SubElement(video, "track")
    timeline_cursor = 0

    for seg_start, seg_end in time_ranges:
        dur_frames = int((seg_end - seg_start) * fps)
        clip = ET.SubElement(v_track, "clipitem", id=f"clip-v-{timeline_cursor}")
        ET.SubElement(clip, "name").text = clip_name

        ET.SubElement(clip, "duration").text = str(dur_frames)
        clip_rate = ET.SubElement(clip, "rate")
        ET.SubElement(clip_rate, "timebase").text = str(fps)
        ET.SubElement(clip_rate, "ntsc").text = "FALSE"

        ET.SubElement(clip, "start").text = str(timeline_cursor)
        ET.SubElement(clip, "end").text = str(timeline_cursor + dur_frames)
        ET.SubElement(clip, "in").text = str(int(seg_start * fps))
        ET.SubElement(clip, "out").text = str(int(seg_end * fps))

        fref = ET.SubElement(clip, "file", id=f"file-{clip_name}")
        ET.SubElement(fref, "name").text = clip_name
        ET.SubElement(fref, "pathurl").text = f"file://{source_video}"

        timeline_cursor += dur_frames

    # Audio track (mirrors video)
    audio = ET.SubElement(media, "audio")
    a_track = ET.SubElement(audio, "track")
    timeline_cursor = 0

    for seg_start, seg_end in time_ranges:
        dur_frames = int((seg_end - seg_start) * fps)
        clip = ET.SubElement(a_track, "clipitem", id=f"clip-a-{timeline_cursor}")
        ET.SubElement(clip, "name").text = clip_name

        ET.SubElement(clip, "duration").text = str(dur_frames)
        clip_rate = ET.SubElement(clip, "rate")
        ET.SubElement(clip_rate, "timebase").text = str(fps)
        ET.SubElement(clip_rate, "ntsc").text = "FALSE"

        ET.SubElement(clip, "start").text = str(timeline_cursor)
        ET.SubElement(clip, "end").text = str(timeline_cursor + dur_frames)
        ET.SubElement(clip, "in").text = str(int(seg_start * fps))
        ET.SubElement(clip, "out").text = str(int(seg_end * fps))

        fref = ET.SubElement(clip, "file", id=f"file-{clip_name}")
        ET.SubElement(fref, "name").text = clip_name
        ET.SubElement(fref, "pathurl").text = f"file://{source_video}"

        timeline_cursor += dur_frames

    ET.indent(xmeml, space="  ")
    return ET.tostring(xmeml, encoding="unicode", xml_declaration=True)


def parse_fcp7_xml(xml_path):
    """Parse an FCP 7 XML file exported from Premiere/Kdenlive.

    Returns a list of (start_seconds, end_seconds) tuples representing
    the editor's cut decisions against the source video.
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()

    seq = root.find(".//sequence")
    if seq is None:
        raise ValueError(f"No <sequence> found in {xml_path}")

    rate_el = seq.find("rate/timebase")
    fps = int(rate_el.text) if rate_el is not None else 30

    time_ranges = []
    for clip in root.findall(".//video/track/clipitem"):
        in_el = clip.find("in")
        out_el = clip.find("out")
        if in_el is None or out_el is None:
            continue
        in_frame = int(in_el.text)
        out_frame = int(out_el.text)
        time_ranges.append((in_frame / fps, out_frame / fps))

    return time_ranges
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_fcp7.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Write and run test for FCP 7 parsing**

Add to `tests/test_fcp7.py`:

```python
from pipeline.fcp7 import parse_fcp7_xml


def test_parse_fcp7_roundtrip(tmp_path):
    """Generating then parsing FCP 7 XML recovers the same time ranges."""
    edl_version = {
        "segments": [
            {"type": "hook", "start": 100.0, "end": 107.5},
            {"type": "body", "start": 90.0, "end": 100.0},
        ],
        "trims": [],
    }
    xml_str = generate_fcp7_xml(edl_version, "/path/source.mp4", fps=30)
    xml_path = tmp_path / "test.xml"
    xml_path.write_text(xml_str)

    ranges = parse_fcp7_xml(str(xml_path))
    assert len(ranges) == 2
    assert abs(ranges[0][0] - 100.0) < 0.1
    assert abs(ranges[0][1] - 107.5) < 0.1
    assert abs(ranges[1][0] - 90.0) < 0.1
    assert abs(ranges[1][1] - 100.0) < 0.1
```

Run: `.venv/bin/python -m pytest tests/test_fcp7.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add pipeline/fcp7.py tests/test_fcp7.py
git commit -m "feat: add FCP 7 XML generation and parsing"
```

---

## Task 7: Client Branding — End Card + Subtitle Styling

**Files:**
- Create: `pipeline/branding.py`
- Create: `tests/test_branding.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_branding.py
import os
import subprocess
from unittest.mock import patch, MagicMock
from pipeline.branding import generate_end_card, get_subtitle_style


def test_get_subtitle_style_from_soul():
    soul = {
        "subtitle_highlight": "#E38533",
        "subtitle_font": "Inter",
    }
    style = get_subtitle_style(soul)
    assert style["highlight_color"] == "#E38533"
    assert style["font"] == "Inter"


def test_get_subtitle_style_defaults():
    style = get_subtitle_style({})
    assert style["highlight_color"] == "#1FC2F9"
    assert style["font"] == "Inter"


def test_generate_end_card(tmp_path):
    logo = tmp_path / "logo.png"
    # Create a minimal 1x1 PNG
    subprocess.run([
        "/opt/homebrew/bin/ffmpeg", "-y",
        "-f", "lavfi", "-i", "color=c=white:s=100x25:d=1",
        "-frames:v", "1", str(logo),
    ], capture_output=True)

    output = tmp_path / "end_card.mp4"
    generate_end_card(
        logo_path=str(logo),
        cta_text="Visit JonathanBrill.com for more",
        output_path=str(output),
        duration=2,
    )
    assert output.exists()
    assert output.stat().st_size > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_branding.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement `pipeline/branding.py`**

```python
"""Client branding: end cards, subtitle styling, logo overlays."""

import logging
import os
import subprocess
import tempfile

logger = logging.getLogger(__name__)

FFMPEG = "/opt/homebrew/bin/ffmpeg"

DEFAULT_STYLE = {
    "highlight_color": "#1FC2F9",
    "font": "Inter",
    "font_size": 120,
}


def get_subtitle_style(soul):
    return {
        "highlight_color": soul.get("subtitle_highlight", DEFAULT_STYLE["highlight_color"]),
        "font": soul.get("subtitle_font", DEFAULT_STYLE["font"]),
        "font_size": soul.get("subtitle_font_size", DEFAULT_STYLE["font_size"]),
    }


def generate_end_card(logo_path, cta_text, output_path, duration=2, fps=30):
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    result = subprocess.run([
        FFMPEG, "-y",
        "-f", "lavfi", "-i", f"color=c=white:s=1080x1920:d={duration}:r={fps}",
        "-i", logo_path,
        "-filter_complex",
        f"[1:v]scale=800:-1[logo];"
        f"[0:v][logo]overlay=(W-w)/2:(H-h)/2-100[bg];"
        f"[bg]drawtext=text='{cta_text}':"
        f"fontfile=/System/Library/Fonts/Helvetica.ttc:"
        f"fontsize=48:fontcolor=0x333333:"
        f"x=(w-text_w)/2:y=(h/2)+200",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-r", str(fps), "-t", str(duration),
        output_path,
    ], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"End card generation failed: {result.stderr[-500:]}")
    logger.info("Generated end card: %s", output_path)


def render_branded_short(
    draft_video,
    subtitle_path,
    end_card_path,
    output_path,
):
    """Concatenate a draft video with subtitles burned in + end card appended."""
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".mp4", delete=False) as tmp:
        tmp_with_subs = tmp.name

    try:
        # Burn subtitles
        escaped = subtitle_path.replace("\\", "\\\\").replace(":", "\\:")
        if subtitle_path.endswith(".ass"):
            sub_filter = f"ass={escaped}"
        else:
            sub_filter = f"subtitles={escaped}"

        result = subprocess.run([
            FFMPEG, "-y", "-i", draft_video,
            "-vf", sub_filter,
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
            tmp_with_subs,
        ], capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Subtitle burn failed: {result.stderr[-500:]}")

        # Add silent audio to end card for concat compatibility
        end_card_audio = tmp_with_subs + ".endcard.mp4"
        subprocess.run([
            FFMPEG, "-y", "-i", end_card_path,
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-c:v", "copy", "-c:a", "aac", "-shortest",
            end_card_audio,
        ], capture_output=True, text=True)

        # Concat
        concat_list = tmp_with_subs + ".txt"
        with open(concat_list, "w") as f:
            f.write(f"file '{tmp_with_subs}'\n")
            f.write(f"file '{end_card_audio}'\n")

        result = subprocess.run([
            FFMPEG, "-y", "-f", "concat", "-safe", "0",
            "-i", concat_list, "-c", "copy", output_path,
        ], capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Concat failed: {result.stderr[-500:]}")

        logger.info("Rendered branded short: %s", output_path)
    finally:
        for f in [tmp_with_subs, tmp_with_subs + ".endcard.mp4", tmp_with_subs + ".txt"]:
            if os.path.exists(f):
                os.unlink(f)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_branding.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add pipeline/branding.py tests/test_branding.py
git commit -m "feat: add client branding (end card, subtitle styling)"
```

---

## Task 8: Transcript Repair

**Files:**
- Create: `pipeline/transcript_repair.py`
- Create: `tests/test_transcript_repair.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_transcript_repair.py
from pipeline.transcript_repair import categorize_repair, format_repair_summary


def test_categorize_high_confidence():
    result = categorize_repair("reverie", "revenue", "quarterly revenue growth")
    assert result["confidence"] == "high"


def test_categorize_low_confidence():
    result = categorize_repair("barding", None, "we launched the barding campaign")
    assert result["confidence"] == "low"
    assert len(result["suggestions"]) > 0


def test_format_summary():
    repairs = [
        {"original": "reverie", "corrected": "revenue", "confidence": "high"},
        {"original": "barding", "corrected": None, "confidence": "low",
         "suggestions": ["boarding", "branding"], "timestamp": "02:31", "line": 47},
    ]
    summary = format_repair_summary(repairs, "Jonathan Brill", "M3 S1")
    assert "reverie→revenue" in summary
    assert "barding" in summary
    assert "Needs review" in summary
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_transcript_repair.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement `pipeline/transcript_repair.py`**

```python
"""LLM-based transcript repair with confidence tiers."""

import json
import logging

from pipeline.llm import llm_json_call

logger = logging.getLogger(__name__)


def categorize_repair(original, corrected, context):
    if corrected and corrected.lower() != original.lower():
        if _is_obvious_fix(original, corrected, context):
            return {"original": original, "corrected": corrected, "confidence": "high"}
        return {"original": original, "corrected": corrected, "confidence": "medium"}
    return {
        "original": original,
        "corrected": None,
        "confidence": "low",
        "suggestions": _generate_suggestions(original, context),
    }


def _is_obvious_fix(original, corrected, context):
    corrected_lower = corrected.lower()
    return corrected_lower in context.lower().replace(original.lower(), "")


def _generate_suggestions(word, context):
    suggestions = []
    if len(word) > 3:
        suggestions = [word[:1] + c + word[2:] for c in "aeiourn" if word[:1] + c + word[2:] != word]
    return suggestions[:3]


def repair_transcript(transcript, context_hint=""):
    """Run LLM repair on transcript segments. Returns (repaired_segments, repair_log)."""
    full_text = " ".join(seg["text"] for seg in transcript)

    prompt = f"""Review this transcript for likely speech-to-text errors. 
For each error found, provide the original word, your correction, and confidence (high/medium/low).

Context about the speaker: {context_hint}

Transcript:
{full_text[:8000]}

Return JSON: {{"repairs": [{{"original": "word", "corrected": "fix", "confidence": "high|medium|low", "timestamp": "MM:SS"}}]}}"""

    try:
        result = llm_json_call(
            pass_name="outline",
            messages=[{"role": "user", "content": prompt}],
            validator=lambda x: "repairs" in x,
        )
        return result["repairs"]
    except Exception as e:
        logger.warning("Transcript repair failed: %s", e)
        return []


def format_repair_summary(repairs, client_name, session_name):
    high = [r for r in repairs if r["confidence"] == "high"]
    medium = [r for r in repairs if r["confidence"] == "medium"]
    low = [r for r in repairs if r["confidence"] == "low"]

    lines = [f"Transcript cleanup for {client_name} — {session_name}"]

    auto_fixed = high + medium
    if auto_fixed:
        fixes = ", ".join(f'"{r["original"]}→{r["corrected"]}"' for r in auto_fixed)
        lines.append(f"Auto-corrected ({len(auto_fixed)} words): {fixes}")

    if low:
        lines.append(f"Needs review ({len(low)} words):")
        for r in low:
            ts = r.get("timestamp", "??:??")
            line_num = r.get("line", "?")
            suggestions = "/".join(r.get("suggestions", [r["original"]]))
            lines.append(f"• Line {line_num} ({ts}): \"{r['original']}\" — [{suggestions}]?")

    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_transcript_repair.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add pipeline/transcript_repair.py tests/test_transcript_repair.py
git commit -m "feat: add transcript repair with confidence tiers"
```

---

## Task 9: CLI — poll-all Command

**Files:**
- Modify: `pipeline/cli.py`

- [ ] **Step 1: Add `poll-all` command to CLI**

Add to `pipeline/cli.py` after the existing `editorial` command:

```python
@cli.command("poll-all")
@click.option("--zencastr-folder", default=None, help="Zencastr root folder ID in Drive")
@click.option("--dry-run", is_flag=True, help="Show what would be processed without doing it")
def poll_all(zencastr_folder, dry_run):
    """Poll Zencastr for new sessions, process them through the editorial pipeline."""
    from pipeline.drive import DriveClient
    from pipeline.drive_poller import ProcessingState, scan_zencastr_sessions
    from pipeline.client_config import match_client, load_soul, get_drive_folder
    from pipeline.config import ZENCASTR_FOLDER_ID

    zencastr_folder = zencastr_folder or ZENCASTR_FOLDER_ID
    if not zencastr_folder:
        click.echo("Error: ZENCASTR_FOLDER_ID not set and --zencastr-folder not provided")
        return

    drive = DriveClient()
    state = ProcessingState()
    sessions = scan_zencastr_sessions(drive, zencastr_folder, state)

    if not sessions:
        click.echo("No new sessions found.")
        return

    for session in sessions:
        client_slug = match_client(session["folder_name"])
        click.echo(f"Session: {session['folder_name']} → client: {client_slug or 'UNKNOWN'}")
        click.echo(f"  Videos: {[f['name'] for f in session['video_files']]}")

        if dry_run:
            click.echo("  (dry run, skipping)")
            continue

        if not client_slug:
            click.echo("  Skipping: could not match to a client")
            continue

        _process_session(drive, session, client_slug, state)

    click.echo("Poll complete.")


def _process_session(drive, session, client_slug, state):
    """Process a single Zencastr session through the full pipeline."""
    import json
    import os
    import tempfile
    from pipeline.transcribe import transcribe_video
    from pipeline.editorial import run_editorial_pipeline
    from pipeline.fcp7 import generate_fcp7_xml
    from pipeline.client_config import load_soul, get_drive_folder
    from pipeline.notify import post_message
    from pipeline.config import WORK_DIR

    folder_id = session["folder_id"]
    folder_name = session["folder_name"]
    soul = load_soul(client_slug)
    slack_channel = soul.get("slack_channel", "")

    work_dir = os.path.join(WORK_DIR, folder_id)
    os.makedirs(work_dir, exist_ok=True)
    cache_dir = os.path.join(work_dir, "cache")
    os.makedirs(cache_dir, exist_ok=True)

    click.echo(f"  Processing {folder_name}...")

    # Step 1: Download source video (skip if already downloaded)
    resume_step = state.get_step(folder_id)
    video_path = os.path.join(cache_dir, session["video_files"][0]["name"])

    if resume_step not in ("transcribed", "editorial", "uploaded") and not os.path.exists(video_path):
        click.echo("  Downloading source video...")
        drive.download_file(session["video_files"][0]["id"], video_path)
        state.mark_step(folder_id, "downloaded")

    # Step 2: Transcribe
    transcript_path = os.path.join(cache_dir, "transcript.json")
    if os.path.exists(transcript_path):
        click.echo("  Loading cached transcript...")
        with open(transcript_path) as f:
            transcript = json.load(f)
    else:
        click.echo("  Transcribing...")
        transcript = transcribe_video(video_path)
        with open(transcript_path, "w") as f:
            json.dump(transcript, f, ensure_ascii=False, indent=2)
    state.mark_step(folder_id, "transcribed")

    # Step 3: Editorial pipeline
    click.echo("  Running editorial pipeline...")
    edls = run_editorial_pipeline(transcript, video_path, work_dir, min_score=7)
    state.mark_step(folder_id, "editorial")

    # Step 4: Generate FCP 7 XMLs
    clips_dir = os.path.join(work_dir, "clips")
    os.makedirs(clips_dir, exist_ok=True)
    for i, edl in enumerate(edls, 1):
        story_id = edl["story_id"]
        for version_name, version in edl["versions"].items():
            suffix = f"-{version_name}" if version_name != "short" else ""
            xml_name = f"{i:02d}-{story_id}{suffix}.xml"
            xml_str = generate_fcp7_xml(
                version, video_path, sequence_name=f"{story_id}-{version_name}",
            )
            xml_path = os.path.join(clips_dir, xml_name)
            with open(xml_path, "w") as f:
                f.write(xml_str)

    # Step 5: Upload to client Drive folder
    client_drive = get_drive_folder(client_slug)
    if client_drive:
        click.echo("  Uploading to Drive...")
        video_folder = drive.create_folder(folder_name, client_drive)
        clips_folder = drive.create_folder("clips", video_folder)

        drive.upload_file(video_path, video_folder)
        for xml_file in os.listdir(clips_dir):
            drive.upload_file(os.path.join(clips_dir, xml_file), clips_folder)

        state.mark_step(folder_id, "uploaded")

    # Step 6: Notify Slack
    if slack_channel:
        hook = edls[0]["versions"]["short"]["segments"][0] if edls else {}
        msg = (
            f"Found {len(edls)} clips for {folder_name}.\n"
            f"Top moment: score {edls[0].get('engagement_score', '?')}/10\n"
            f"XMLs uploaded to Drive — ready for editing."
        )
        try:
            post_message(msg, channel=slack_channel)
        except Exception as e:
            click.echo(f"  Slack notification failed: {e}")

    state.mark_complete(folder_id)
    click.echo(f"  Done: {len(edls)} clips generated")
```

- [ ] **Step 2: Test manually**

Run: `.venv/bin/python -c "from pipeline.cli import cli; cli(['poll-all', '--dry-run'])"` (after setting ZENCASTR_FOLDER_ID)
Expected: Lists sessions or "No new sessions found"

- [ ] **Step 3: Commit**

```bash
git add pipeline/cli.py
git commit -m "feat(cli): add poll-all command for Drive-based ingest"
```

---

## Task 10: Wire FCP 7 XML Into Editorial Command

**Files:**
- Modify: `pipeline/cli.py`

- [ ] **Step 1: Update `editorial` command to generate FCP 7 XML alongside Kdenlive**

In the editorial command's render loop (after the Kdenlive XML generation block), add:

```python
            # FCP 7 XML
            from pipeline.fcp7 import generate_fcp7_xml
            fcp7_xml = generate_fcp7_xml(
                version, video_path, sequence_name=f"{story_id}-{version_name}",
            )
            fcp7_path = os.path.join(projects_dir, f"{story_id}-{version_name}.xml")
            with open(fcp7_path, "w") as f:
                f.write(fcp7_xml)
```

- [ ] **Step 2: Test by running editorial on a cached session**

Run the editorial command on an already-cached session. Verify `.xml` files appear alongside `.kdenlive` files in `projects/`.

- [ ] **Step 3: Commit**

```bash
git add pipeline/cli.py
git commit -m "feat(cli): generate FCP 7 XML alongside Kdenlive in editorial command"
```

---

## Task 11: Update client_map.json With Drive Folder IDs

**Files:**
- Modify: `/Users/ct-mac-mini/dev/openclaw-kanban/client_map.json`

- [ ] **Step 1: Add `drive_folder_id` and `aliases` to Jonathan Brill entry**

Update the `jonathan-brill` entry in `client_map.json`:

```json
"jonathan-brill": {
    "channels": [
        {
            "id": "C05SGKQAQGK",
            "name": "jonathan-brill",
            "type": "internal",
            "current_agent": "crowdtamers"
        }
    ],
    "drive_folder_id": "",
    "aliases": ["Jonathan Brill", "Brill"]
}
```

Note: `drive_folder_id` needs to be populated by looking up the actual Drive folder ID for Jonathan Brill under `Active Clients/`. Run:

```bash
# After Drive client is working:
python3 -c "
from pipeline.drive import DriveClient
d = DriveClient()
folders = d.list_files('DRIVE_CLIENTS_ROOT_ID', mime_types=['application/vnd.google-apps.folder'])
for f in folders:
    if 'brill' in f['name'].lower():
        print(f['name'], f['id'])
"
```

- [ ] **Step 2: Add `aliases` to other active clients as needed**

For each client in `client_map.json`, add `aliases` and `drive_folder_id` fields.

- [ ] **Step 3: Commit**

```bash
git add /Users/ct-mac-mini/dev/openclaw-kanban/client_map.json
git commit -m "feat: add drive_folder_id and aliases to client_map"
```

---

## Task 12: R2 Lifecycle Rule

**Files:** None (Cloudflare dashboard configuration)

- [ ] **Step 1: Configure R2 bucket lifecycle rule**

In the Cloudflare dashboard:
1. Go to R2 > `video-pipeline` bucket > Settings > Object lifecycle rules
2. Add rule: "Delete objects older than 90 days"
3. Apply to all objects (prefix: empty)

- [ ] **Step 2: Verify rule is active**

Check the bucket settings page shows the lifecycle rule.

---

## Task 13: Add .env Variables for Drive Pipeline

**Files:**
- Modify: `/Users/ct-mac-mini/dev/podruff-site/video-pipeline/.env`

- [ ] **Step 1: Add Drive pipeline config to .env**

```
ZENCASTR_FOLDER_ID=<Zencastr Apps folder ID from Drive>
DRIVE_CLIENTS_ROOT=<Active Clients folder ID from Drive>
SLACK_BOT_TOKEN=xoxb-41124765216-9085008978405-Ix79KEGJDtRCAQEp1TFHzqRu
```

- [ ] **Step 2: Verify ZENCASTR_FOLDER_ID**

Find the Zencastr folder in Drive:
```bash
python3 -c "
from pipeline.drive import DriveClient
d = DriveClient()
# List root-level folders
folders = d.list_files('root', mime_types=['application/vnd.google-apps.folder'])
for f in folders:
    if 'zencastr' in f['name'].lower() or 'apps' in f['name'].lower():
        print(f['name'], f['id'])
"
```

- [ ] **Step 3: Commit .env.example (not .env)**

Create `.env.example` with the variable names (no values):
```
ZENCASTR_FOLDER_ID=
DRIVE_CLIENTS_ROOT=
SLACK_BOT_TOKEN=
```

```bash
git add .env.example
git commit -m "docs: add Drive pipeline env vars to .env.example"
```

---

Plan complete and saved to `docs/superpowers/plans/2026-04-15-drive-native-pipeline-v2.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?