# Drive-Native Video Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate the CrowdTamers video pipeline from Descript/R2 to Zencastr/Google Drive/FCP 7 XML with automated ingest, LLM transcript repair, and last-mile branding automation.

**Architecture:** Google Drive is the single storage layer. OpenClaw heartbeat dispatches a `video-poll` skill every 15 minutes to check for new Zencastr recordings and Jude's finished edits. The pipeline generates FCP 7 XML for Premiere and renders branded final videos. Slack notifications via Botty at every stage.

**Tech Stack:** Python 3, Google Drive API v3 (`google-api-python-client`), FFmpeg, Parakeet-MLX, OpenAI-compatible LLM (Ollama), FCP 7 XML (ElementTree), OpenClaw skill dispatch.

**Spec:** `docs/superpowers/specs/2026-03-25-drive-native-video-pipeline-design.md`

---

## Phase 1: Google Drive Module

Foundation layer — all other phases depend on this.

### Task 1: Google Drive client wrapper

**Files:**
- Create: `pipeline/drive.py`
- Create: `tests/test_drive.py`
- Modify: `pipeline/config.py`

- [ ] **Step 1: Write failing tests for Drive client**

```python
# tests/test_drive.py
from unittest.mock import patch, MagicMock
from pipeline.drive import DriveClient


def test_list_files_in_folder():
    """list_files returns file metadata from a Drive folder."""
    mock_service = MagicMock()
    mock_service.files().list().execute.return_value = {
        "files": [
            {"id": "abc123", "name": "source.mp4", "mimeType": "video/mp4",
             "modifiedTime": "2026-03-25T10:00:00Z"}
        ]
    }
    client = DriveClient(service=mock_service)
    files = client.list_files("folder_id_123", mime_types=["video/mp4"])
    assert len(files) == 1
    assert files[0]["name"] == "source.mp4"


def test_download_file(tmp_path):
    """download_file streams to local path."""
    mock_service = MagicMock()
    client = DriveClient(service=mock_service)
    with patch("pipeline.drive.MediaIoBaseDownload") as mock_dl_class:
        mock_dl = MagicMock()
        mock_dl.next_chunk.return_value = (None, True)  # done immediately
        mock_dl_class.return_value = mock_dl
        dest = tmp_path / "test.mp4"
        client.download_file("file_id_123", str(dest))
        assert dest.exists()
        mock_dl_class.assert_called_once()


def test_upload_file(tmp_path):
    """upload_file creates a file in the target folder and returns file ID."""
    mock_service = MagicMock()
    mock_service.files().create().execute.return_value = {"id": "new_file_id"}
    client = DriveClient(service=mock_service)
    src = tmp_path / "test.mp4"
    src.write_bytes(b"data")
    file_id = client.upload_file(str(src), "parent_folder_id", "test.mp4")
    assert file_id == "new_file_id"


def test_create_folder():
    """create_folder creates a folder and returns its ID."""
    mock_service = MagicMock()
    mock_service.files().create().execute.return_value = {"id": "folder_id"}
    client = DriveClient(service=mock_service)
    folder_id = client.create_folder("Test Folder", "parent_id")
    assert folder_id == "folder_id"


def test_get_shareable_link():
    """get_shareable_link returns a web view link."""
    mock_service = MagicMock()
    mock_service.files().get().execute.return_value = {
        "webViewLink": "https://drive.google.com/file/d/abc/view"
    }
    client = DriveClient(service=mock_service)
    link = client.get_shareable_link("abc")
    assert "drive.google.com" in link
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/ct-mac-mini/dev/video-pipeline && python -m pytest tests/test_drive.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline.drive'`

- [ ] **Step 3: Add Drive config vars**

Add to `pipeline/config.py`:
```python
GOOGLE_SERVICE_ACCOUNT_PATH = os.environ.get(
    "GOOGLE_SERVICE_ACCOUNT_PATH",
    os.path.expanduser("~/.config/google/service-account.json"),
)
DRIVE_ZENCASTR_FOLDER_ID = os.environ.get("DRIVE_ZENCASTR_FOLDER_ID", "")  # Drive folder ID, not path
DRIVE_CLIENTS_ROOT = os.environ.get("DRIVE_CLIENTS_ROOT", "")  # Drive folder ID for Active Clients
CLIENT_MAP_PATH = os.environ.get("CLIENT_MAP_PATH", "client-map.json")
```

- [ ] **Step 4: Implement DriveClient**

```python
# pipeline/drive.py
"""Google Drive operations for the video pipeline."""

import io
import json
import logging
import os

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

from pipeline.config import GOOGLE_SERVICE_ACCOUNT_PATH

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive"]


class DriveClient:
    """Wrapper around Google Drive API v3."""

    def __init__(self, service=None, credentials=None):
        if service is not None:
            self._service = service
            self._credentials = credentials
        else:
            self._credentials = service_account.Credentials.from_service_account_file(
                GOOGLE_SERVICE_ACCOUNT_PATH, scopes=SCOPES,
            )
            self._service = build("drive", "v3", credentials=self._credentials)

    def list_files(
        self, folder_id: str, mime_types: list[str] | None = None,
    ) -> list[dict]:
        """List files in a Drive folder, optionally filtered by MIME type."""
        q = f"'{folder_id}' in parents and trashed = false"
        if mime_types:
            mime_q = " or ".join(f"mimeType='{m}'" for m in mime_types)
            q += f" and ({mime_q})"
        result = self._service.files().list(
            q=q,
            fields="files(id, name, mimeType, modifiedTime, size)",
            orderBy="modifiedTime desc",
            pageSize=100,
        ).execute()
        return result.get("files", [])

    def list_folders(self, folder_id: str) -> list[dict]:
        """List subfolders in a Drive folder."""
        return self.list_files(folder_id, mime_types=["application/vnd.google-apps.folder"])

    def find_folder_by_path(self, path: str, root_id: str = "root") -> str | None:
        """Walk a slash-separated path from root and return the final folder ID."""
        current_id = root_id
        for part in path.strip("/").split("/"):
            q = (f"'{current_id}' in parents and trashed = false "
                 f"and mimeType='application/vnd.google-apps.folder' "
                 f"and name='{part}'")
            result = self._service.files().list(q=q, fields="files(id)").execute()
            files = result.get("files", [])
            if not files:
                return None
            current_id = files[0]["id"]
        return current_id

    def download_file(self, file_id: str, dest_path: str) -> None:
        """Download a file from Drive to a local path. Streams to disk (no memory buffering)."""
        os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
        request = self._service.files().get_media(fileId=file_id)
        with open(dest_path, "wb") as f:
            downloader = MediaIoBaseDownload(f, request, chunksize=50 * 1024 * 1024)
            done = False
            while not done:
                status, done = downloader.next_chunk()
                if status:
                    logger.info("Download %s: %d%%", dest_path, int(status.progress() * 100))

    def upload_file(
        self, local_path: str, parent_folder_id: str, name: str | None = None,
    ) -> str:
        """Upload a local file to a Drive folder. Returns the new file ID."""
        name = name or os.path.basename(local_path)
        media = MediaFileUpload(local_path, resumable=True)
        metadata = {"name": name, "parents": [parent_folder_id]}
        result = self._service.files().create(
            body=metadata, media_body=media, fields="id",
        ).execute()
        return result["id"]

    def create_folder(self, name: str, parent_id: str) -> str:
        """Create a subfolder. Returns the new folder ID."""
        metadata = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_id],
        }
        result = self._service.files().create(body=metadata, fields="id").execute()
        return result["id"]

    def get_shareable_link(self, file_id: str) -> str:
        """Get a web view link for a file."""
        result = self._service.files().get(
            fileId=file_id, fields="webViewLink",
        ).execute()
        return result["webViewLink"]

    def create_google_doc(self, title: str, content: str, parent_id: str) -> str:
        """Create a Google Doc by uploading a text file with conversion.

        Uses Drive API's built-in conversion — no Docs API dependency needed.
        Returns the doc URL.
        """
        import tempfile
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
        try:
            tmp.write(content)
            tmp.close()
            media = MediaFileUpload(tmp.name, mimetype="text/plain", resumable=True)
            metadata = {
                "name": title,
                "parents": [parent_id],
                "mimeType": "application/vnd.google-apps.document",  # triggers conversion
            }
            result = self._service.files().create(
                body=metadata, media_body=media,
                fields="id, webViewLink",
            ).execute()
            return result["webViewLink"]
        finally:
            os.unlink(tmp.name)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/ct-mac-mini/dev/video-pipeline && python -m pytest tests/test_drive.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Commit**

```bash
git add pipeline/drive.py tests/test_drive.py pipeline/config.py
git commit -m "feat: add Google Drive client wrapper (drive.py)"
```

---

### Task 2: Client mapping and session detection

**Files:**
- Create: `pipeline/client_map.py`
- Create: `tests/test_client_map.py`
- Create: `client-map.json` (template)

- [ ] **Step 1: Write failing tests**

```python
# tests/test_client_map.py
import json
import pytest
from pipeline.client_map import match_client, load_client_map


@pytest.fixture
def client_map(tmp_path):
    data = {
        "clients": {
            "acme": {
                "drive_folder_id": "folder_acme_123",
                "aliases": ["ACME Corp", "Acme"],
                "slack_channel": "C0ACME",
                "mode": "interview"
            },
            "brightway": {
                "drive_folder_id": "folder_bright_456",
                "aliases": ["Brightway", "BrightWay Education"],
                "slack_channel": "C0BRIGHT",
                "mode": "interview"
            }
        }
    }
    path = tmp_path / "client-map.json"
    path.write_text(json.dumps(data))
    return str(path)


def test_exact_match(client_map):
    cm = load_client_map(client_map)
    result = match_client("ACME Corp Content Call M3 S1", cm)
    assert result["key"] == "acme"
    assert result["drive_folder_id"] == "folder_acme_123"
    assert result["session_name"] == "ACME Corp Content Call M3 S1"


def test_case_insensitive_match(client_map):
    cm = load_client_map(client_map)
    result = match_client("acme content call M1 S2", cm)
    assert result["key"] == "acme"


def test_no_match_returns_none(client_map):
    cm = load_client_map(client_map)
    result = match_client("Unknown Company Call M1 S1", cm)
    assert result is None


def test_parse_session_info(client_map):
    cm = load_client_map(client_map)
    result = match_client("Brightway Content Call M3 S1", cm)
    assert result["key"] == "brightway"
    assert result["mode"] == "interview"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/ct-mac-mini/dev/video-pipeline && python -m pytest tests/test_client_map.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement client_map.py**

```python
# pipeline/client_map.py
"""Client name matching for Zencastr session → Drive folder mapping."""

import json
import logging

logger = logging.getLogger(__name__)


def load_client_map(path: str) -> dict:
    """Load client mapping from JSON file."""
    with open(path) as f:
        return json.load(f)


def match_client(session_name: str, client_map: dict) -> dict | None:
    """Match a Zencastr session name to a client config.

    Tries exact alias match (case-insensitive) against each client's aliases.
    Returns a dict with client key, config fields, and session_name, or None.
    """
    name_lower = session_name.lower()

    for client_key, config in client_map["clients"].items():
        for alias in config.get("aliases", []):
            if name_lower.startswith(alias.lower()):
                return {
                    "key": client_key,
                    "session_name": session_name,
                    **config,
                }

    logger.warning("No client match for session: %s", session_name)
    return None
```

- [ ] **Step 4: Create template client-map.json**

```json
{
  "clients": {
    "example-client": {
      "drive_folder_id": "REPLACE_WITH_DRIVE_FOLDER_ID",
      "aliases": ["Example Client", "ExampleClient"],
      "slack_channel": "C0EXAMPLE",
      "mode": "interview",
      "languages": ["es"]
    }
  }
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/ct-mac-mini/dev/video-pipeline && python -m pytest tests/test_client_map.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add pipeline/client_map.py tests/test_client_map.py client-map.json
git commit -m "feat: add client mapping for Zencastr session detection"
```

---

## Phase 2: LLM Transcript Repair

### Task 3: Transcript repair with confidence tiers

**Files:**
- Create: `pipeline/transcript_repair.py`
- Create: `tests/test_transcript_repair.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_transcript_repair.py
from unittest.mock import patch
from pipeline.transcript_repair import repair_transcript, Correction


def test_high_confidence_auto_fixed():
    """Obvious contextual errors are auto-fixed."""
    transcript = [
        {"start": 0.0, "end": 3.0, "text": "We need to increase our reverie this quarter"},
    ]
    mock_response = {
        "corrections": [
            {"line": 0, "original": "reverie", "replacement": "revenue",
             "confidence": "high", "reason": "business context"}
        ]
    }
    with patch("pipeline.transcript_repair._call_llm", return_value=mock_response):
        result, corrections = repair_transcript(transcript)

    assert "revenue" in result[0]["text"]
    assert len(corrections) == 1
    assert corrections[0].confidence == "high"
    assert corrections[0].applied is True


def test_low_confidence_not_applied():
    """Ambiguous words are flagged but not changed."""
    transcript = [
        {"start": 0.0, "end": 3.0, "text": "We launched the barding campaign"},
    ]
    mock_response = {
        "corrections": [
            {"line": 0, "original": "barding", "replacement": "branding",
             "confidence": "low", "reason": "could be barding, boarding, or branding",
             "alternatives": ["branding", "boarding", "barding"]}
        ]
    }
    with patch("pipeline.transcript_repair._call_llm", return_value=mock_response):
        result, corrections = repair_transcript(transcript)

    assert "barding" in result[0]["text"]  # NOT changed
    assert corrections[0].confidence == "low"
    assert corrections[0].applied is False


def test_medium_confidence_applied_and_flagged():
    """Likely errors are fixed but included in summary."""
    transcript = [
        {"start": 0.0, "end": 3.0, "text": "The compliant was about pricing"},
    ]
    mock_response = {
        "corrections": [
            {"line": 0, "original": "compliant", "replacement": "complaint",
             "confidence": "medium", "reason": "compliant vs complaint"}
        ]
    }
    with patch("pipeline.transcript_repair._call_llm", return_value=mock_response):
        result, corrections = repair_transcript(transcript)

    assert "complaint" in result[0]["text"]
    assert corrections[0].confidence == "medium"
    assert corrections[0].applied is True


def test_no_corrections_returns_unchanged():
    """Clean transcript passes through unchanged."""
    transcript = [
        {"start": 0.0, "end": 3.0, "text": "Everything is perfectly clear"},
    ]
    mock_response = {"corrections": []}
    with patch("pipeline.transcript_repair._call_llm", return_value=mock_response):
        result, corrections = repair_transcript(transcript)

    assert result[0]["text"] == "Everything is perfectly clear"
    assert len(corrections) == 0


def test_format_slack_summary():
    """Slack summary formats corrections by tier."""
    from pipeline.transcript_repair import format_repair_summary

    corrections = [
        Correction(line=0, start=1.0, original="reverie", replacement="revenue",
                   confidence="high", applied=True, reason="business context"),
        Correction(line=5, start=12.0, original="barding", replacement="branding",
                   confidence="low", applied=False, reason="ambiguous",
                   alternatives=["branding", "boarding"]),
    ]
    summary = format_repair_summary("Acme", "M3 S1", corrections)
    assert "reverie→revenue" in summary
    assert "barding" in summary
    assert "Needs review" in summary
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/ct-mac-mini/dev/video-pipeline && python -m pytest tests/test_transcript_repair.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Add transcript_repair config to editorial_config.py**

Add to `_DEFAULTS` in `pipeline/editorial_config.py`:
```python
    "transcript_repair": {
        "base_url": OLLAMA_URL,
        "api_key": OLLAMA_KEY,
        "model": os.environ.get("EDITORIAL_REPAIR_MODEL", "qwen3:8b"),
        "timeout": 1800,
        "num_ctx": 32768,
    },
```

- [ ] **Step 4: Implement transcript_repair.py**

```python
# pipeline/transcript_repair.py
"""LLM-based transcript repair with confidence tiers."""

import copy
import json
import logging
from dataclasses import dataclass, field

from pipeline.llm import llm_json_call

logger = logging.getLogger(__name__)


@dataclass
class Correction:
    line: int
    start: float
    original: str
    replacement: str
    confidence: str  # "high", "medium", "low"
    applied: bool
    reason: str
    alternatives: list[str] = field(default_factory=list)


REPAIR_SYSTEM = """You are a transcript proofreader. Many speakers are English-as-a-second-language.
Common transcription errors include: wrong homophones, mangled proper nouns, misheard technical terms,
and words that don't make sense in context.

For each error you find, classify your confidence:
- "high": contextually obvious (e.g., "reverie" should be "revenue" in a business discussion)
- "medium": likely wrong but some ambiguity (e.g., "compliant" vs "complaint")
- "low": genuinely unclear — provide 2-3 alternatives

Return ONLY JSON:
{
  "corrections": [
    {
      "line": <int, 0-indexed>,
      "original": "wrong word",
      "replacement": "best guess",
      "confidence": "high|medium|low",
      "reason": "why this is likely wrong",
      "alternatives": ["alt1", "alt2"]  // only for low confidence
    }
  ]
}

If the transcript looks clean, return {"corrections": []}.
Return ONLY JSON, no explanation. /no_think"""


def _call_llm(transcript_text: str) -> dict:
    """Call LLM for transcript repair. Separated for testability."""
    return llm_json_call(
        pass_name="transcript_repair",
        system=REPAIR_SYSTEM,
        user=transcript_text,
        validator=lambda x: x,  # minimal validation
    )


def repair_transcript(
    transcript: list[dict],
) -> tuple[list[dict], list[Correction]]:
    """Repair transcript errors using LLM with confidence tiers.

    Returns:
        (repaired_transcript, corrections) — repaired transcript is a deep copy
        with high/medium confidence fixes applied. Low confidence items are
        flagged but not applied.
    """
    numbered = "\n".join(
        f"[{i}] [{s['start']:.1f}s] {s['text']}" for i, s in enumerate(transcript)
    )
    result = _call_llm(numbered)
    corrections = []
    repaired = copy.deepcopy(transcript)

    for c in result.get("corrections", []):
        line_idx = c["line"]
        if line_idx < 0 or line_idx >= len(repaired):
            continue

        apply = c["confidence"] in ("high", "medium")
        correction = Correction(
            line=line_idx,
            start=repaired[line_idx]["start"],
            original=c["original"],
            replacement=c["replacement"],
            confidence=c["confidence"],
            applied=apply,
            reason=c.get("reason", ""),
            alternatives=c.get("alternatives", []),
        )

        if apply:
            repaired[line_idx]["text"] = repaired[line_idx]["text"].replace(
                c["original"], c["replacement"], 1
            )

        corrections.append(correction)

    return repaired, corrections


def format_repair_summary(
    client_name: str, session_label: str, corrections: list[Correction],
) -> str:
    """Format corrections into a Slack-friendly summary."""
    if not corrections:
        return f"Transcript cleanup for {client_name} — {session_label}\nNo corrections needed."

    auto = [c for c in corrections if c.applied]
    flagged = [c for c in corrections if not c.applied]

    lines = [f"*Transcript cleanup for {client_name} — {session_label}*"]

    if auto:
        fixes = ", ".join(f'"{c.original}→{c.replacement}"' for c in auto)
        lines.append(f"Auto-corrected ({len(auto)} words): {fixes}")

    if flagged:
        lines.append(f"Needs review ({len(flagged)} words):")
        for c in flagged:
            ts = f"{int(c.start // 60):02d}:{int(c.start % 60):02d}"
            alts = "/".join(c.alternatives) if c.alternatives else c.replacement
            lines.append(f"• Line {c.line} ({ts}): *[{alts}]* — {c.reason}")

    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/ct-mac-mini/dev/video-pipeline && python -m pytest tests/test_transcript_repair.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add pipeline/transcript_repair.py tests/test_transcript_repair.py
git commit -m "feat: add LLM transcript repair with confidence tiers"
```

---

## Phase 3: FCP 7 XML Generation and Parsing

### Task 4: FCP 7 XML generator

**Files:**
- Create: `pipeline/fcp7.py`
- Create: `tests/test_fcp7.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_fcp7.py
import xml.etree.ElementTree as ET
from pipeline.fcp7 import generate_fcp7_xml, parse_fcp7_xml


def _make_edl_version():
    return {
        "segments": [
            {"type": "hook", "start": 60.0, "end": 67.0, "narrative_bridge": "tension"},
            {"type": "body", "start": 30.0, "end": 55.0},
        ],
        "trims": [],
        "estimated_duration": 32.0,
    }


def test_generate_produces_valid_xml():
    """Generated FCP 7 XML is well-formed."""
    edl = _make_edl_version()
    xml_str = generate_fcp7_xml(
        edl, source_videos=["source-client.mp4"],
        name="test-clip", fps=30,
    )
    root = ET.fromstring(xml_str)
    assert root.tag == "xmeml"
    assert root.attrib["version"] == "5"


def test_generate_has_correct_clip_count():
    """Each segment becomes a clipitem in the timeline."""
    edl = _make_edl_version()
    xml_str = generate_fcp7_xml(
        edl, source_videos=["source-client.mp4"],
        name="test-clip", fps=30,
    )
    root = ET.fromstring(xml_str)
    clips = root.findall(".//track/clipitem")
    assert len(clips) == 2  # hook + body


def test_generate_multi_track_interview():
    """Interview mode includes both client and interviewer tracks."""
    edl = _make_edl_version()
    xml_str = generate_fcp7_xml(
        edl,
        source_videos=["source-client.mp4", "source-interviewer.mp4"],
        name="test-clip", fps=30,
    )
    root = ET.fromstring(xml_str)
    video_tracks = root.findall(".//video/track")
    assert len(video_tracks) == 2  # V1=client, V2=interviewer


def test_generate_relative_paths():
    """File references use relative paths."""
    edl = _make_edl_version()
    xml_str = generate_fcp7_xml(
        edl, source_videos=["source-client.mp4"],
        name="test-clip", fps=30,
    )
    assert "source-client.mp4" in xml_str
    # Should NOT contain absolute paths
    assert "/Users/" not in xml_str


def test_parse_extracts_segments():
    """Parsing a generated XML recovers the original segments."""
    edl = _make_edl_version()
    xml_str = generate_fcp7_xml(
        edl, source_videos=["source-client.mp4"],
        name="test-clip", fps=30,
    )
    segments, warnings = parse_fcp7_xml(xml_str)
    assert len(segments) == 2
    # Hook at 60-67, body at 30-55 (in source time)
    assert abs(segments[0]["start"] - 60.0) < 0.1
    assert abs(segments[0]["end"] - 67.0) < 0.1
    assert abs(segments[1]["start"] - 30.0) < 0.1
    assert abs(segments[1]["end"] - 55.0) < 0.1
    assert len(warnings) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/ct-mac-mini/dev/video-pipeline && python -m pytest tests/test_fcp7.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement FCP 7 XML generator**

```python
# pipeline/fcp7.py
"""FCP 7 XML generation and parsing for Premiere interchange."""

import logging
import xml.etree.ElementTree as ET

from pipeline.edl import resolve_segments

logger = logging.getLogger(__name__)


def _frames(seconds: float, fps: int) -> int:
    """Convert seconds to frame count."""
    return int(round(seconds * fps))


def _tc(seconds: float, fps: int) -> str:
    """Convert seconds to SMPTE non-drop timecode HH:MM:SS:FF."""
    total_frames = int(round(seconds * fps))
    ff = total_frames % fps
    ss = (total_frames // fps) % 60
    mm = (total_frames // (fps * 60)) % 60
    hh = total_frames // (fps * 3600)
    return f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"


def generate_fcp7_xml(
    edl_version: dict,
    source_videos: list[str],
    name: str = "Clip",
    fps: int = 30,
    width: int = 1920,
    height: int = 1080,
) -> str:
    """Generate FCP 7 XML from an EDL version.

    Args:
        edl_version: EDL dict with segments and trims.
        source_videos: List of source video filenames (relative paths).
            First is primary (client), additional are secondary tracks.
        name: Sequence name.
        fps: Frame rate.
        width/height: Frame dimensions.

    Returns:
        FCP 7 XML string.
    """
    time_ranges = resolve_segments(
        edl_version["segments"], edl_version.get("trims", [])
    )

    root = ET.Element("xmeml", version="5")
    sequence = ET.SubElement(root, "sequence")
    ET.SubElement(sequence, "name").text = name
    ET.SubElement(sequence, "duration").text = str(
        sum(_frames(e - s, fps) for s, e in time_ranges)
    )

    rate = ET.SubElement(sequence, "rate")
    ET.SubElement(rate, "timebase").text = str(fps)
    ET.SubElement(rate, "ntsc").text = "FALSE"

    media = ET.SubElement(sequence, "media")

    # Generate video tracks — one per source video
    video = ET.SubElement(media, "video")
    for track_idx, src in enumerate(source_videos):
        track = ET.SubElement(video, "track")
        if track_idx > 0:
            # Secondary tracks are locked (interviewer context, not for editing)
            ET.SubElement(track, "locked").text = "TRUE"

        timeline_cursor = 0
        for seg_idx, (seg_start, seg_end) in enumerate(time_ranges):
            seg_dur_frames = _frames(seg_end - seg_start, fps)
            clip = ET.SubElement(track, "clipitem", id=f"{name}-{track_idx}-{seg_idx}")
            ET.SubElement(clip, "name").text = f"{name} seg {seg_idx + 1}"
            ET.SubElement(clip, "duration").text = str(seg_dur_frames)

            clip_rate = ET.SubElement(clip, "rate")
            ET.SubElement(clip_rate, "timebase").text = str(fps)
            ET.SubElement(clip_rate, "ntsc").text = "FALSE"

            ET.SubElement(clip, "start").text = str(timeline_cursor)
            ET.SubElement(clip, "end").text = str(timeline_cursor + seg_dur_frames)

            ET.SubElement(clip, "in").text = str(_frames(seg_start, fps))
            ET.SubElement(clip, "out").text = str(_frames(seg_end, fps))

            # FCP 7 convention: define <file> fully on first use, ref by id after
            if seg_idx == 0:
                file_elem = ET.SubElement(clip, "file", id=f"file-{track_idx}")
                ET.SubElement(file_elem, "name").text = src
                ET.SubElement(file_elem, "pathurl").text = f"file://./{src}"

                file_rate = ET.SubElement(file_elem, "rate")
                ET.SubElement(file_rate, "timebase").text = str(fps)
                ET.SubElement(file_rate, "ntsc").text = "FALSE"

                file_media = ET.SubElement(file_elem, "media")
                file_video = ET.SubElement(file_media, "video")
                sc = ET.SubElement(file_video, "samplecharacteristics")
                ET.SubElement(sc, "width").text = str(width)
                ET.SubElement(sc, "height").text = str(height)
            else:
                ET.SubElement(clip, "file", id=f"file-{track_idx}")

            timeline_cursor += seg_dur_frames

    # Audio track (from primary source only)
    audio = ET.SubElement(media, "audio")
    audio_track = ET.SubElement(audio, "track")
    timeline_cursor = 0
    for seg_idx, (seg_start, seg_end) in enumerate(time_ranges):
        seg_dur_frames = _frames(seg_end - seg_start, fps)
        clip = ET.SubElement(audio_track, "clipitem", id=f"{name}-audio-{seg_idx}")
        ET.SubElement(clip, "name").text = f"{name} seg {seg_idx + 1}"
        ET.SubElement(clip, "duration").text = str(seg_dur_frames)

        clip_rate = ET.SubElement(clip, "rate")
        ET.SubElement(clip_rate, "timebase").text = str(fps)
        ET.SubElement(clip_rate, "ntsc").text = "FALSE"

        ET.SubElement(clip, "start").text = str(timeline_cursor)
        ET.SubElement(clip, "end").text = str(timeline_cursor + seg_dur_frames)
        ET.SubElement(clip, "in").text = str(_frames(seg_start, fps))
        ET.SubElement(clip, "out").text = str(_frames(seg_end, fps))

        # Reference existing file element by id only
        ET.SubElement(clip, "file", id="file-0")

        timeline_cursor += seg_dur_frames

    ET.indent(root)
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def parse_fcp7_xml(xml_str: str) -> tuple[list[dict], list[str]]:
    """Parse FCP 7 XML and extract edit decisions.

    Returns:
        (segments, warnings) — segments as list of {start, end} in source time,
        warnings for any unsupported elements encountered.
    """
    root = ET.fromstring(xml_str)
    warnings = []
    segments = []

    sequence = root.find(".//sequence")
    if sequence is None:
        warnings.append("No <sequence> found in XML")
        return segments, warnings

    rate_el = sequence.find("rate/timebase")
    fps = int(rate_el.text) if rate_el is not None else 30

    # Parse first video track only (client track / V1)
    video_tracks = root.findall(".//video/track")
    if not video_tracks:
        warnings.append("No video tracks found")
        return segments, warnings

    primary_track = video_tracks[0]
    for clip in primary_track.findall("clipitem"):
        in_frames = clip.find("in")
        out_frames = clip.find("out")
        if in_frames is None or out_frames is None:
            warnings.append(f"clipitem missing in/out: {clip.get('id', '?')}")
            continue

        start = int(in_frames.text) / fps
        end = int(out_frames.text) / fps
        segments.append({"start": start, "end": end})

        # Check for unsupported elements
        for unsupported in ["filter", "effect", "transition"]:
            if clip.find(unsupported) is not None:
                warnings.append(
                    f"Unsupported element <{unsupported}> in clipitem "
                    f"{clip.get('id', '?')} — ignored"
                )

    return segments, warnings
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/ct-mac-mini/dev/video-pipeline && python -m pytest tests/test_fcp7.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add pipeline/fcp7.py tests/test_fcp7.py
git commit -m "feat: add FCP 7 XML generation and parsing"
```

---

## Phase 4: Last-Mile Branding

### Task 5: Branding support (extend existing render path)

**Files:**
- Create: `pipeline/branding.py` (style loading + outro/logo helpers only — rendering stays in `edl.py`)
- Create: `tests/test_branding.py`
- Modify: `pipeline/edl.py` (add `logo_path` and `outro_path` params to `render_edl_version`)

- [ ] **Step 1: Write failing tests**

```python
# tests/test_branding.py
import json
from unittest.mock import patch, MagicMock
from pipeline.branding import load_client_style, build_ffmpeg_filter, DEFAULT_STYLE


def test_load_style_from_file(tmp_path):
    """Loads style.json from _assets folder."""
    style = {
        "font": "Arial",
        "primary_color": "#FF0000",
        "gradient_background": True,
    }
    style_path = tmp_path / "style.json"
    style_path.write_text(json.dumps(style))
    loaded = load_client_style(str(style_path))
    assert loaded["font"] == "Arial"
    assert loaded["gradient_background"] is True


def test_load_style_missing_file_returns_defaults():
    """Missing style.json returns CrowdTamers defaults."""
    loaded = load_client_style("/nonexistent/style.json")
    assert loaded == DEFAULT_STYLE
    assert loaded["font"] == "Raleway"
    assert loaded["primary_color"] == "#1FC2F9"


def test_build_filter_vertical():
    """Vertical crop filter with face detection position."""
    vf = build_ffmpeg_filter(
        crop_mode="vertical", face_pos=(0.5, 0.3),
        video_width=1920, video_height=1080,
        subtitle_path=None, logo_path=None,
    )
    assert "crop=" in vf
    assert "scale=1080:1920" in vf


def test_build_filter_with_subtitle():
    """Subtitle filter is appended when path provided."""
    vf = build_ffmpeg_filter(
        crop_mode="horizontal", face_pos=None,
        video_width=1920, video_height=1080,
        subtitle_path="/tmp/test.ass", logo_path=None,
    )
    assert "ass=" in vf


def test_build_filter_without_logo():
    """Logo is handled separately via filter_complex, not in vf chain."""
    vf = build_ffmpeg_filter(
        crop_mode="horizontal", face_pos=None,
        video_width=1920, video_height=1080,
        subtitle_path=None, logo_path=None,
    )
    assert "overlay" not in vf
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/ct-mac-mini/dev/video-pipeline && python -m pytest tests/test_branding.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement branding.py (style loading + outro helper only)**

```python
# pipeline/branding.py
"""Client branding config: style loading and outro crossfade.
Rendering is handled by edl.render_edl_version — this module provides
config and the outro crossfade step that runs after the main render."""

import json
import logging
import os

logger = logging.getLogger(__name__)

DEFAULT_STYLE = {
    "font": "Raleway",
    "primary_color": "#1FC2F9",
    "outline_color": "#000000",
    "font_size": 24,
    "gradient_background": False,
    "logo_position": "bottom-right",
    "logo_opacity": 0.8,
}


def load_client_style(style_path: str) -> dict:
    """Load client style from style.json, falling back to defaults."""
    if os.path.exists(style_path):
        with open(style_path) as f:
            user_style = json.load(f)
        return {**DEFAULT_STYLE, **user_style}
    return dict(DEFAULT_STYLE)


def crossfade_outro(main_video: str, outro_path: str, output_path: str) -> str:
    """Crossfade an outro clip onto the end of a rendered video.

    Returns output_path.
    """
    from pipeline.edl import _run_ffmpeg, _probe_duration

    main_dur = _probe_duration(main_video)
    outro_dur = _probe_duration(outro_path)
    xfade_dur = min(1.0, outro_dur / 2)
    offset = main_dur - xfade_dur

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    _run_ffmpeg([
        "-i", main_video,
        "-i", outro_path,
        "-filter_complex",
        f"[0:v][1:v]xfade=transition=fade:duration={xfade_dur}:offset={offset}[v];"
        f"[0:a][1:a]acrossfade=d={xfade_dur}[a]",
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        output_path,
    ])
    return output_path
```

- [ ] **Step 4: Extend render_edl_version in edl.py with logo_path**

Add `logo_path: str | None = None` parameter to `render_edl_version` in `pipeline/edl.py`. When provided, switch from `-vf` to `-filter_complex` for the final render:

```python
# In render_edl_version, replace the final ffmpeg call:
if logo_path and os.path.exists(logo_path):
    vf_str = ",".join(vf_filters) if vf_filters else "null"
    filter_complex = (
        f"[0:v]{vf_str}[base];"
        f"[1:v]format=rgba,colorchannelmixer=aa=0.8[logo];"
        f"[base][logo]overlay=W-w-20:H-h-20[v]"
    )
    _run_ffmpeg([
        "-f", "concat", "-safe", "0", "-i", concat_path,
        "-i", logo_path,
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", "0:a",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-r", "30", "-vsync", "cfr",
        "-c:a", "aac", "-b:a", "192k",
        output_path,
    ])
else:
    # existing -vf path unchanged
```

The done-flow orchestrator calls: `render_edl_version(...)` then `crossfade_outro(...)` — two clean steps, one render path.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/ct-mac-mini/dev/video-pipeline && python -m pytest tests/test_branding.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add pipeline/branding.py tests/test_branding.py
git commit -m "feat: add last-mile branding renderer (subtitles, logo, outro)"
```

---

## Phase 5: Ingest and Done Pollers

### Task 6: Ingest poller (Zencastr → pipeline)

**Files:**
- Create: `pipeline/ingest_poller.py`
- Create: `tests/test_ingest_poller.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_ingest_poller.py
import json
import pytest
from unittest.mock import patch, MagicMock, call
from pipeline.ingest_poller import IngestPoller


@pytest.fixture
def state_file(tmp_path):
    return str(tmp_path / ".processed.json")


@pytest.fixture
def client_map(tmp_path):
    data = {
        "clients": {
            "acme": {
                "drive_folder_id": "folder_acme",
                "aliases": ["Acme"],
                "slack_channel": "C0ACME",
                "mode": "interview"
            }
        }
    }
    path = tmp_path / "client-map.json"
    path.write_text(json.dumps(data))
    return str(path)


import pytest


def test_skips_already_processed(state_file, client_map):
    """Sessions in .processed.json are skipped."""
    with open(state_file, "w") as f:
        json.dump({"processed": {"session-abc": {"status": "complete"}}}, f)

    mock_drive = MagicMock()
    mock_drive.list_folders.return_value = [
        {"id": "session-abc", "name": "Acme Content Call M3 S1"}
    ]

    poller = IngestPoller(
        drive=mock_drive, client_map_path=client_map,
        state_path=state_file, work_dir="/tmp/test",
    )
    sessions = poller.find_new_sessions("zencastr_folder_id")
    assert len(sessions) == 0


def test_detects_new_session(state_file, client_map):
    """New sessions not in .processed.json are detected."""
    mock_drive = MagicMock()
    mock_drive.list_folders.return_value = [
        {"id": "session-new", "name": "Acme Content Call M3 S1"}
    ]

    poller = IngestPoller(
        drive=mock_drive, client_map_path=client_map,
        state_path=state_file, work_dir="/tmp/test",
    )
    sessions = poller.find_new_sessions("zencastr_folder_id")
    assert len(sessions) == 1
    assert sessions[0]["client"]["key"] == "acme"


def test_unknown_client_flagged(state_file, client_map):
    """Sessions that don't match any client are flagged, not processed."""
    mock_drive = MagicMock()
    mock_drive.list_folders.return_value = [
        {"id": "session-unknown", "name": "Unknown Corp Call M1 S1"}
    ]

    poller = IngestPoller(
        drive=mock_drive, client_map_path=client_map,
        state_path=state_file, work_dir="/tmp/test",
    )
    sessions = poller.find_new_sessions("zencastr_folder_id")
    assert len(sessions) == 0
    # Should log a warning (tested via caplog if needed)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/ct-mac-mini/dev/video-pipeline && python -m pytest tests/test_ingest_poller.py -v`
Expected: FAIL

- [ ] **Step 3: Implement IngestPoller**

```python
# pipeline/ingest_poller.py
"""Polls Google Drive for new Zencastr recordings and processes them."""

import json
import logging
import os
import shutil

from pipeline.client_map import load_client_map, match_client
from pipeline.drive import DriveClient

logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {".mp4", ".webm", ".mkv", ".mov"}


class IngestPoller:
    """Watches Zencastr upload folder for new sessions."""

    def __init__(
        self, drive: DriveClient, client_map_path: str,
        state_path: str, work_dir: str,
    ):
        self.drive = drive
        self.client_map = load_client_map(client_map_path)
        self.state_path = state_path
        self.work_dir = work_dir
        self._state = self._load_state()

    def _load_state(self) -> dict:
        if os.path.exists(self.state_path):
            with open(self.state_path) as f:
                return json.load(f)
        return {"processed": {}}

    def _save_state(self):
        os.makedirs(os.path.dirname(self.state_path) or ".", exist_ok=True)
        with open(self.state_path, "w") as f:
            json.dump(self._state, f, indent=2)

    def find_new_sessions(self, zencastr_folder_id: str) -> list[dict]:
        """Find unprocessed Zencastr sessions with matching clients.

        Returns list of {folder_id, folder_name, client: {...}} dicts.
        """
        folders = self.drive.list_folders(zencastr_folder_id)
        new_sessions = []

        for folder in folders:
            fid = folder["id"]
            fname = folder["name"]

            if fid in self._state["processed"]:
                continue

            client = match_client(fname, self.client_map)
            if client is None:
                logger.warning("No client match for Zencastr session: %s", fname)
                continue

            new_sessions.append({
                "folder_id": fid,
                "folder_name": fname,
                "client": client,
            })

        return new_sessions

    def download_session_videos(
        self, session: dict,
    ) -> list[str]:
        """Download video files from a Zencastr session folder.

        Returns list of local file paths.
        """
        files = self.drive.list_files(session["folder_id"])
        video_files = [
            f for f in files
            if any(f["name"].lower().endswith(ext) for ext in VIDEO_EXTENSIONS)
        ]

        local_dir = os.path.join(self.work_dir, session["folder_name"])
        os.makedirs(local_dir, exist_ok=True)

        local_paths = []
        for vf in video_files:
            dest = os.path.join(local_dir, vf["name"])
            if not os.path.exists(dest):
                logger.info("Downloading %s (%s)...", vf["name"], vf.get("size", "?"))
                self.drive.download_file(vf["id"], dest)

            # Normalize non-MP4 formats to H.264 MP4
            if not dest.lower().endswith(".mp4"):
                mp4_dest = os.path.splitext(dest)[0] + ".mp4"
                logger.info("Converting %s to MP4...", vf["name"])
                import subprocess
                subprocess.run(
                    ["/opt/homebrew/bin/ffmpeg", "-y", "-i", dest,
                     "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                     "-c:a", "aac", "-b:a", "192k", mp4_dest],
                    check=True, capture_output=True,
                )
                os.remove(dest)
                dest = mp4_dest

            local_paths.append(dest)

        return local_paths

    def mark_complete(self, session: dict, status: str = "complete"):
        """Mark a session as processed."""
        self._state["processed"][session["folder_id"]] = {
            "status": status,
            "name": session["folder_name"],
            "client": session["client"]["key"],
        }
        self._save_state()

    def cleanup_local(self, session: dict):
        """Remove local working files for a session."""
        local_dir = os.path.join(self.work_dir, session["folder_name"])
        if os.path.exists(local_dir):
            shutil.rmtree(local_dir)
            logger.info("Cleaned up local files: %s", local_dir)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/ct-mac-mini/dev/video-pipeline && python -m pytest tests/test_ingest_poller.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add pipeline/ingest_poller.py tests/test_ingest_poller.py
git commit -m "feat: add ingest poller for Zencastr session detection"
```

---

### Task 7: Done poller (Jude's XMLs → final render)

**Files:**
- Create: `pipeline/done_poller.py`
- Create: `tests/test_done_poller.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_done_poller.py
import time
from unittest.mock import MagicMock
from pipeline.done_poller import DonePoller


def test_ignores_recently_modified_files():
    """Files modified less than 60 seconds ago are skipped."""
    mock_drive = MagicMock()
    # Return a file with modifiedTime = now (too recent)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    mock_drive.list_files.return_value = [
        {"id": "file1", "name": "01-clip.xml", "modifiedTime": now}
    ]
    poller = DonePoller(drive=mock_drive)
    ready = poller.find_ready_xmls("done_folder_id")
    assert len(ready) == 0


def test_detects_old_xml_files():
    """Files modified more than 60 seconds ago are picked up."""
    mock_drive = MagicMock()
    from datetime import datetime, timezone, timedelta
    old = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    mock_drive.list_files.return_value = [
        {"id": "file1", "name": "01-clip.xml", "modifiedTime": old}
    ]
    poller = DonePoller(drive=mock_drive)
    ready = poller.find_ready_xmls("done_folder_id")
    assert len(ready) == 1
    assert ready[0]["name"] == "01-clip.xml"


def test_skips_non_xml_files():
    """Non-XML files in done/ are ignored."""
    mock_drive = MagicMock()
    from datetime import datetime, timezone, timedelta
    old = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    mock_drive.list_files.return_value = [
        {"id": "file1", "name": "notes.txt", "modifiedTime": old},
        {"id": "file2", "name": "01-clip.xml", "modifiedTime": old},
    ]
    poller = DonePoller(drive=mock_drive)
    ready = poller.find_ready_xmls("done_folder_id")
    assert len(ready) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/ct-mac-mini/dev/video-pipeline && python -m pytest tests/test_done_poller.py -v`
Expected: FAIL

- [ ] **Step 3: Implement DonePoller**

```python
# pipeline/done_poller.py
"""Polls client done/ folders for Jude's finished FCP 7 XMLs."""

import logging
from datetime import datetime, timezone, timedelta

from pipeline.drive import DriveClient

logger = logging.getLogger(__name__)

MIN_AGE_SECONDS = 60


class DonePoller:
    """Watches done/ folders for stable XML files."""

    def __init__(self, drive: DriveClient):
        self.drive = drive

    def find_ready_xmls(self, done_folder_id: str) -> list[dict]:
        """Find XML files in done/ that are old enough to be stable.

        Returns list of Drive file metadata dicts for ready XMLs.
        """
        files = self.drive.list_files(done_folder_id)
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=MIN_AGE_SECONDS)
        ready = []

        for f in files:
            if not f["name"].lower().endswith(".xml"):
                continue

            mod_time = datetime.fromisoformat(f["modifiedTime"].replace("Z", "+00:00"))
            if mod_time < cutoff:
                ready.append(f)
            else:
                logger.debug("Skipping %s — modified too recently", f["name"])

        return ready
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/ct-mac-mini/dev/video-pipeline && python -m pytest tests/test_done_poller.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add pipeline/done_poller.py tests/test_done_poller.py
git commit -m "feat: add done poller for detecting Jude's finished XMLs"
```

---

## Phase 6: Pipeline Orchestration

### Task 8: Main pipeline orchestrator

**Files:**
- Create: `pipeline/orchestrator.py`
- Modify: `pipeline/cli.py`

This wires all components together into the two main workflows:
1. **Ingest flow:** new recording → transcribe → repair → editorial → FCP XML → Drive
2. **Done flow:** Jude's XML → render branded video → Drive → Notion → Slack

- [ ] **Step 1: Implement orchestrator**

```python
# pipeline/orchestrator.py
"""Main pipeline orchestrator — wires ingest and done flows."""

import json
import logging
import os

from pipeline.drive import DriveClient
from pipeline.client_map import load_client_map
from pipeline.config import (
    DRIVE_ZENCASTR_FOLDER_ID, DRIVE_CLIENTS_ROOT, CLIENT_MAP_PATH, WORK_DIR,
)
from pipeline.ingest_poller import IngestPoller
from pipeline.done_poller import DonePoller
from pipeline.transcribe import transcribe_video
from pipeline.transcript_repair import repair_transcript, format_repair_summary
from pipeline.editorial import run_editorial_pipeline
from pipeline.fcp7 import generate_fcp7_xml, parse_fcp7_xml
from pipeline.branding import render_branded_video, load_client_style
from pipeline.edl import generate_clip_subtitles, resolve_segments
from pipeline.editor import _detect_face_center
from pipeline.notify import post_message

logger = logging.getLogger(__name__)


def _acquire_lock(lock_path: str) -> bool:
    """Acquire a file lock. Returns True if lock acquired, False if already held."""
    import fcntl
    try:
        lock_fd = open(lock_path, "w")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_fd.write(str(os.getpid()))
        lock_fd.flush()
        # Keep fd open — lock released when process exits
        _acquire_lock._fd = lock_fd
        return True
    except (IOError, OSError):
        return False


def run_ingest(drive: DriveClient | None = None, work_dir: str | None = None):
    """Poll Zencastr folder and process new recordings."""
    drive = drive or DriveClient()
    work_dir = work_dir or WORK_DIR

    lock_path = os.path.join(work_dir, ".ingest.lock")
    os.makedirs(work_dir, exist_ok=True)
    if not _acquire_lock(lock_path):
        logger.info("Another ingest process is running — skipping.")
        return

    state_path = os.path.join(work_dir, ".processed.json")
    poller = IngestPoller(
        drive=drive, client_map_path=CLIENT_MAP_PATH,
        state_path=state_path, work_dir=work_dir,
    )

    zencastr_id = DRIVE_ZENCASTR_FOLDER_ID
    if not zencastr_id:
        logger.error("DRIVE_ZENCASTR_FOLDER_ID not configured")
        return

    sessions = poller.find_new_sessions(zencastr_id)
    if not sessions:
        logger.info("No new sessions found.")
        return

    for session in sessions:
        try:
            _process_session(drive, poller, session, work_dir)
        except Exception:
            logger.exception("Failed to process session: %s", session["folder_name"])
            poller.mark_complete(session, status="failed")


def _process_session(
    drive: DriveClient, poller: IngestPoller, session: dict, work_dir: str,
):
    """Process a single recording session end-to-end."""
    client = session["client"]
    logger.info("Processing: %s (client: %s)", session["folder_name"], client["key"])

    # Notify Slack
    post_message(
        f"New recording detected for {client['key']} — {session['folder_name']}. Processing started.",
        channel=client["slack_channel"],
    )

    # Download videos
    local_videos = poller.download_session_videos(session)
    if not local_videos:
        logger.warning("No video files found in session: %s", session["folder_name"])
        return

    primary_video = local_videos[0]  # Client track

    # Create session folder on Drive
    client_video_folder = _ensure_session_folder(drive, client, session)

    # Upload source videos to Drive
    for lv in local_videos:
        drive.upload_file(lv, client_video_folder, os.path.basename(lv))

    # Transcribe
    logger.info("Transcribing...")
    transcript = transcribe_video(primary_video)

    # Transcript repair
    logger.info("Repairing transcript...")
    repaired, corrections = repair_transcript(transcript)

    # Save repaired SRT
    local_dir = os.path.join(work_dir, session["folder_name"])
    srt_path = os.path.join(local_dir, "transcript.srt")
    _write_srt_from_transcript(repaired, srt_path)
    drive.upload_file(srt_path, client_video_folder, "transcript.srt")

    # Create Google Doc (non-blocking)
    try:
        doc_text = "\n".join(f"[{s['start']:.1f}s] {s['text']}" for s in repaired)
        doc_url = drive.create_google_doc(
            f"{client['key']} — {session['folder_name']} Transcript",
            doc_text, client_video_folder,
        )
        # Save doc link
        link_path = os.path.join(local_dir, "transcript-doc-link.txt")
        with open(link_path, "w") as f:
            f.write(doc_url)
        drive.upload_file(link_path, client_video_folder, "transcript-doc-link.txt")
    except Exception:
        logger.warning("Failed to create Google Doc — continuing", exc_info=True)
        post_message(
            f"⚠ Google Doc creation failed for {session['folder_name']}. "
            f"Transcript corrections will need manual SRT editing.",
            channel=client["slack_channel"],
        )

    # Post repair summary
    if corrections:
        summary = format_repair_summary(client["key"], session["folder_name"], corrections)
        post_message(summary, channel=client["slack_channel"])

    # Editorial pipeline
    logger.info("Running editorial pipeline...")
    editorial_dir = os.path.join(local_dir, "editorial")
    edls = run_editorial_pipeline(repaired, primary_video, editorial_dir)

    if not edls:
        post_message(
            f"No clips found for {client['key']} — {session['folder_name']}.",
            channel=client["slack_channel"],
        )
        poller.mark_complete(session)
        poller.cleanup_local(session)
        return

    # Upload editorial outputs
    editorial_folder_id = drive.create_folder("editorial", client_video_folder)
    for fname in ["outline.json", "stories.json"]:
        fpath = os.path.join(editorial_dir, fname)
        if os.path.exists(fpath):
            drive.upload_file(fpath, editorial_folder_id, fname)

    # Generate FCP 7 XMLs
    clips_folder_id = drive.create_folder("clips", client_video_folder)
    source_filenames = [os.path.basename(v) for v in local_videos]

    for i, edl in enumerate(edls, 1):
        story_id = edl["story_id"]
        for version_name, version in edl["versions"].items():
            suffix = f"-{version_name}" if version_name != "short" else ""
            xml_name = f"{i:02d}-{story_id}{suffix}.xml"

            crop = "vertical" if version_name == "short" else "horizontal"
            w, h = (1080, 1920) if crop == "vertical" else (1920, 1080)

            xml_str = generate_fcp7_xml(
                version, source_videos=source_filenames,
                name=story_id, width=w, height=h,
            )
            xml_path = os.path.join(local_dir, xml_name)
            with open(xml_path, "w") as f:
                f.write(xml_str)
            drive.upload_file(xml_path, clips_folder_id, xml_name)

    # Create done/ and final/ folders
    drive.create_folder("done", client_video_folder)
    drive.create_folder("final", client_video_folder)

    # Get Drive link for notification
    folder_link = drive.get_shareable_link(client_video_folder)

    # Load stories data for notification (has engagement scores)
    stories_path = os.path.join(editorial_dir, "stories.json")
    top_hook = ""
    top_score = 0
    if os.path.exists(stories_path):
        with open(stories_path) as f:
            stories_data = json.load(f)
        if stories_data.get("stories"):
            top = max(stories_data["stories"], key=lambda s: s.get("engagement_score", 0))
            top_score = top.get("engagement_score", 0)
            hooks = top.get("hook_candidates", [])
            top_hook = hooks[0]["text"] if hooks else top.get("title", "")

    msg = f"Found {len(edls)} clips from {session['folder_name']}."
    if top_hook:
        msg += f" Top moment: '{top_hook}' (score: {top_score}/10)."
    msg += f"\nDrive folder: {folder_link}"
    post_message(msg, channel=client["slack_channel"])

    poller.mark_complete(session)
    poller.cleanup_local(session)
    logger.info("Session complete: %s", session["folder_name"])


def _ensure_session_folder(drive: DriveClient, client: dict, session: dict) -> str:
    """Create the session folder structure on Drive. Returns session folder ID."""
    # Check for existing Video folder under client
    client_folder_id = client["drive_folder_id"]
    video_folders = [
        f for f in drive.list_folders(client_folder_id)
        if f["name"] == "Video"
    ]
    if video_folders:
        video_folder_id = video_folders[0]["id"]
    else:
        video_folder_id = drive.create_folder("Video", client_folder_id)

    session_folder_id = drive.create_folder(session["folder_name"], video_folder_id)
    return session_folder_id


def _write_srt_from_transcript(transcript: list[dict], path: str):
    """Write SRT file from transcript segments."""
    from pipeline.edl import _srt_time
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    lines = []
    for i, seg in enumerate(transcript, 1):
        lines.append(str(i))
        lines.append(f"{_srt_time(seg['start'])} --> {_srt_time(seg['end'])}")
        lines.append(seg["text"].strip())
        lines.append("")
    with open(path, "w") as f:
        f.write("\n".join(lines))
```

- [ ] **Step 2: Implement run_done (done-flow orchestrator)**

Add `run_done` and `_process_done_xml` to `pipeline/orchestrator.py`:

```python
def run_done(drive: DriveClient | None = None, work_dir: str | None = None):
    """Poll client done/ folders and render branded final videos."""
    drive = drive or DriveClient()
    work_dir = work_dir or WORK_DIR
    done_poller = DonePoller(drive=drive)
    client_map = load_client_map(CLIENT_MAP_PATH)

    for client_key, config in client_map["clients"].items():
        # Find Video folder under client
        video_folders = [
            f for f in drive.list_folders(config["drive_folder_id"])
            if f["name"] == "Video"
        ]
        if not video_folders:
            continue

        # Scan each session folder for done/ subfolders
        video_folder_id = video_folders[0]["id"]
        session_folders = drive.list_folders(video_folder_id)

        for session_folder in session_folders:
            done_folders = [
                f for f in drive.list_folders(session_folder["id"])
                if f["name"] == "done"
            ]
            if not done_folders:
                continue

            final_folders = [
                f for f in drive.list_folders(session_folder["id"])
                if f["name"] == "final"
            ]
            final_folder_id = final_folders[0]["id"] if final_folders else \
                drive.create_folder("final", session_folder["id"])

            ready_xmls = done_poller.find_ready_xmls(done_folders[0]["id"])

            # Check which XMLs have already been rendered (matching final/ mp4)
            existing_finals = {
                f["name"] for f in drive.list_files(final_folder_id)
            }

            for xml_file in ready_xmls:
                final_name = xml_file["name"].replace(".xml", "-final.mp4")
                if final_name in existing_finals:
                    continue  # Already rendered

                try:
                    _process_done_xml(
                        drive, xml_file, session_folder,
                        final_folder_id, config, client_key, work_dir,
                    )
                except Exception:
                    logger.exception(
                        "Failed to render %s for %s", xml_file["name"], client_key
                    )


def _process_done_xml(
    drive: DriveClient, xml_file: dict, session_folder: dict,
    final_folder_id: str, client_config: dict, client_key: str,
    work_dir: str,
):
    """Render one of Jude's edited XMLs into a branded final video."""
    local_dir = os.path.join(work_dir, f"done-{session_folder['name']}")
    os.makedirs(local_dir, exist_ok=True)

    # Download the XML
    xml_path = os.path.join(local_dir, xml_file["name"])
    drive.download_file(xml_file["id"], xml_path)

    with open(xml_path) as f:
        xml_str = f.read()

    segments, warnings = parse_fcp7_xml(xml_str)
    if warnings:
        logger.warning("FCP XML parse warnings for %s: %s", xml_file["name"], warnings)

    if not segments:
        logger.warning("No segments found in %s", xml_file["name"])
        return

    # Download source video (client track)
    source_files = [
        f for f in drive.list_files(session_folder["id"])
        if f["name"].startswith("source-") and f["name"].endswith(".mp4")
    ]
    if not source_files:
        logger.error("No source video found in %s", session_folder["name"])
        return

    # Use client track (first source file alphabetically = source-client.mp4)
    source_file = sorted(source_files, key=lambda f: f["name"])[0]
    source_path = os.path.join(local_dir, source_file["name"])
    if not os.path.exists(source_path):
        drive.download_file(source_file["id"], source_path)

    # Download client assets
    assets_folders = [
        f for f in drive.list_folders(client_config["drive_folder_id"])
        if f["name"] == "_assets"
    ]
    outro_path = None
    logo_path = None
    style_path = os.path.join(local_dir, "style.json")

    if assets_folders:
        assets_id = assets_folders[0]["id"]
        asset_files = drive.list_files(assets_id)
        for af in asset_files:
            dest = os.path.join(local_dir, af["name"])
            if not os.path.exists(dest):
                drive.download_file(af["id"], dest)
            if af["name"] == "outro.mp4":
                outro_path = dest
            elif af["name"] == "logo.png":
                logo_path = dest
            elif af["name"] == "style.json":
                style_path = dest

    # Download transcript for subtitle generation
    srt_files = [f for f in drive.list_files(session_folder["id"]) if f["name"] == "transcript.srt"]
    srt_path = None
    if srt_files:
        srt_path = os.path.join(local_dir, "transcript.srt")
        if not os.path.exists(srt_path):
            drive.download_file(srt_files[0]["id"], srt_path)

    # Detect face position for vertical crop
    face_pos = _detect_face_center(source_path)

    # Determine crop mode from XML name
    crop_mode = "vertical"  # default to shorts
    if "-long" in xml_file["name"]:
        crop_mode = "horizontal"

    # Render branded video
    final_name = xml_file["name"].replace(".xml", "-final.mp4")
    final_path = os.path.join(local_dir, final_name)

    render_branded_video(
        source_video=source_path,
        segments=segments,
        output_path=final_path,
        crop_mode=crop_mode,
        face_pos=face_pos,
        subtitle_path=srt_path,
        logo_path=logo_path,
        outro_path=outro_path,
    )

    # Upload to final/ folder on Drive
    drive.upload_file(final_path, final_folder_id, final_name)

    # Get Drive link
    folder_link = drive.get_shareable_link(final_folder_id)

    # Push to Notion
    # TODO: integrate with notion_board.py when ready

    # Notify via Slack
    post_message(
        f"Final video ready: {final_name}\nDrive: {folder_link}",
        channel=client_config.get("slack_channel", ""),
    )

    # Cleanup local files
    import shutil
    shutil.rmtree(local_dir, ignore_errors=True)
```

- [ ] **Step 3: Add new CLI commands**

Modify `pipeline/cli.py` — add `ingest-poll` and `done-poll` commands:

```python
# Add after existing imports at top of cli.py:
# from pipeline.orchestrator import run_ingest

@cli.command("ingest-poll")
def ingest_poll():
    """Poll Zencastr Drive folder for new recordings and process them."""
    from pipeline.orchestrator import run_ingest
    click.echo("Polling for new Zencastr recordings...")
    run_ingest()
    click.echo("Done.")


@cli.command("done-poll")
def done_poll():
    """Poll client done/ folders for Jude's finished XMLs and render."""
    from pipeline.orchestrator import run_done
    click.echo("Polling for finished edits...")
    run_done()
    click.echo("Done.")
```

- [ ] **Step 3: Commit**

```bash
git add pipeline/orchestrator.py pipeline/cli.py
git commit -m "feat: add main pipeline orchestrator with ingest and done flows"
```

---

### Task 9: OpenClaw skill dispatch + poll-all CLI command

**Files:**
- Create: `~/.openclaw/scripts/video-poll.sh`
- Modify: `~/.openclaw/skills/manifest.md` (add video-poll skill)
- Modify: `pipeline/cli.py` (add `poll-all` command)

Replaces launchd crons with OpenClaw heartbeat dispatch. The heartbeat triggers a `video-poll` skill every 15 minutes, which runs both ingest and done polling plus cleanup in a single invocation.

- [ ] **Step 1: Add poll-all CLI command**

Add to `pipeline/cli.py`:
```python
@cli.command("poll-all")
def poll_all():
    """Run ingest poll, done poll, and cleanup in one invocation."""
    from pipeline.orchestrator import run_ingest, run_done
    import shutil, time

    click.echo("Polling for new recordings...")
    run_ingest()

    click.echo("Polling for finished edits...")
    run_done()

    # Cleanup local files older than 48 hours
    work_dir = os.environ.get("PIPELINE_WORK_DIR", "/tmp/video-pipeline")
    if os.path.exists(work_dir):
        cutoff = time.time() - (48 * 3600)
        for entry in os.scandir(work_dir):
            if entry.is_dir() and entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry.path, ignore_errors=True)
                click.echo(f"Cleaned up: {entry.name}")

    click.echo("Done.")
```

- [ ] **Step 2: Create video-poll.sh**

```bash
#!/bin/bash
# OpenClaw skill: poll for new recordings and finished edits
set -euo pipefail
cd /Users/ct-mac-mini/dev/video-pipeline
python -m pipeline.cli poll-all 2>&1 | tail -20
```

- [ ] **Step 3: Add video-poll skill to manifest**

Add to `~/.openclaw/skills/manifest.md`:
```markdown
## video-poll

**Triggers:** video-poll, poll videos, check recordings
**Script:** `~/.openclaw/scripts/video-poll.sh`
**Schedule:** Every 15 minutes via heartbeat dispatch
**Description:** Polls Zencastr Google Drive folder for new recordings,
checks client done/ folders for Jude's finished FCP XML edits, and
cleans up local working files older than 48 hours.
```

- [ ] **Step 4: Register with heartbeat dispatch**

Add video-poll to the heartbeat's skill dispatch schedule. The heartbeat script at `~/.openclaw/scripts/botty-heartbeat.sh` dispatches skills at intervals — add a 15-minute check:

```bash
# Add to botty-heartbeat.sh main loop, alongside existing morning-planner dispatch:
# Every 15 minutes (every 15th iteration of the 60s loop)
if [ $((LOOP_COUNT % 15)) -eq 0 ]; then
    openclaw dispatch video-poll 2>/dev/null || true
fi
```

If the heartbeat binary is compiled and can't be modified, add the dispatch to `slack-heartbeat.sh` instead (already runs every 5 minutes via launchd, just gate on `$((INVOCATION_COUNT % 3))`).

- [ ] **Step 5: Commit**

```bash
cd /Users/ct-mac-mini/dev/video-pipeline
git add pipeline/cli.py
git commit -m "feat: add poll-all CLI command for OpenClaw skill dispatch"
```

---

## Phase 7: OpenClaw Skill Updates

### Task 10: Update bshort and add bshort-refresh

**Files:**
- Modify: `~/.openclaw/scripts/bshort.sh`
- Create: `~/.openclaw/scripts/bshort-refresh.sh`
- Modify: `~/.openclaw/scripts/btranslate.sh` (accept Drive URLs)

- [ ] **Step 1: Update bshort.sh to accept Drive URLs**

Add Google Drive URL handling at the top of the URL detection logic in `bshort.sh`. When a Drive URL is detected, download the file using the pipeline's Drive module instead of Slack download:

```bash
# Add after existing URL detection in bshort.sh:
elif echo "$VIDEO_URL" | grep -q "drive.google.com"; then
    echo "Downloading from Google Drive..."
    VIDEO_PATH=$(python3 -c "
from pipeline.drive import DriveClient
import re, sys
url = '$VIDEO_URL'
file_id = re.search(r'/d/([a-zA-Z0-9_-]+)', url).group(1)
client = DriveClient()
dest = '/tmp/video-pipeline/drive-download.mp4'
client.download_file(file_id, dest)
print(dest)
")
```

- [ ] **Step 2: Create bshort-refresh.sh**

```bash
#!/bin/bash
# Re-render subtitles from updated Google Doc transcript
set -euo pipefail

SESSION_NAME="$1"
CHANNEL_ID="${2:-}"
THREAD_TS="${3:-}"

cd /Users/ct-mac-mini/dev/video-pipeline

python3 -c "
from pipeline.orchestrator import refresh_session
refresh_session('$SESSION_NAME')
"

if [ -n "$CHANNEL_ID" ]; then
    python3 -c "
from pipeline.notify import post_message
post_message('Subtitles refreshed for $SESSION_NAME', channel='$CHANNEL_ID', thread_ts='$THREAD_TS')
"
fi
```

- [ ] **Step 3: Add refresh_session to orchestrator.py**

Add to `pipeline/orchestrator.py`:
```python
def refresh_session(session_name: str):
    """Re-render subtitles from updated Google Doc transcript.

    1. Find the session folder on Drive by name
    2. Download the Google Doc content (plain text)
    3. Re-generate SRT using original Parakeet timestamps + corrected words
    4. Re-render any final/ videos that have corresponding done/ XMLs
    """
    drive = DriveClient()
    client_map = load_client_map(CLIENT_MAP_PATH)

    # Find session folder
    from pipeline.client_map import match_client
    client = match_client(session_name, client_map)
    if not client:
        logger.error("No client match for session: %s", session_name)
        return

    # Navigate to session folder
    video_folders = [f for f in drive.list_folders(client["drive_folder_id"]) if f["name"] == "Video"]
    if not video_folders:
        logger.error("No Video folder for client %s", client["key"])
        return

    session_folders = [f for f in drive.list_folders(video_folders[0]["id"]) if f["name"] == session_name]
    if not session_folders:
        logger.error("Session folder not found: %s", session_name)
        return

    session_id = session_folders[0]["id"]
    work_dir_local = os.path.join(WORK_DIR, f"refresh-{session_name}")
    os.makedirs(work_dir_local, exist_ok=True)

    # Download transcript-doc-link.txt to get Doc ID
    link_files = [f for f in drive.list_files(session_id) if f["name"] == "transcript-doc-link.txt"]
    if not link_files:
        logger.error("No transcript doc link found for %s", session_name)
        return

    link_path = os.path.join(work_dir_local, "transcript-doc-link.txt")
    drive.download_file(link_files[0]["id"], link_path)
    with open(link_path) as f:
        doc_url = f.read().strip()

    # Extract doc ID and download content
    import re
    doc_id_match = re.search(r'/d/([a-zA-Z0-9_-]+)', doc_url)
    if not doc_id_match:
        logger.error("Cannot parse doc ID from URL: %s", doc_url)
        return

    doc_id = doc_id_match.group(1)
    from googleapiclient.discovery import build as build_svc
    docs_service = build_svc("docs", "v1", credentials=drive._credentials)
    doc = docs_service.documents().get(documentId=doc_id).execute()

    # Extract plain text from doc
    doc_text = ""
    for element in doc.get("body", {}).get("content", []):
        for para in element.get("paragraph", {}).get("elements", []):
            doc_text += para.get("textRun", {}).get("content", "")

    # Re-generate SRT: original timestamps + corrected words
    # Download original transcript.srt for timing info
    srt_files = [f for f in drive.list_files(session_id) if f["name"] == "transcript.srt"]
    if srt_files:
        srt_path = os.path.join(work_dir_local, "transcript.srt")
        drive.download_file(srt_files[0]["id"], srt_path)
        # Update SRT words from doc text while preserving timestamps
        # (timestamps come from Parakeet, doc only corrects words)
        _update_srt_words(srt_path, doc_text)
        # Re-upload corrected SRT
        drive.upload_file(srt_path, session_id, "transcript.srt")

    # Re-render any existing final/ videos
    done_folders = [f for f in drive.list_folders(session_id) if f["name"] == "done"]
    if done_folders:
        logger.info("Re-rendering finals from done/ XMLs...")
        run_done(drive=drive, work_dir=WORK_DIR)

    # Cleanup
    import shutil
    shutil.rmtree(work_dir_local, ignore_errors=True)

    post_message(
        f"Subtitles refreshed for {session_name}.",
        channel=client.get("slack_channel", ""),
    )


def _update_srt_words(srt_path: str, doc_text: str):
    """Update SRT file words from Google Doc text while preserving timestamps.

    Simple approach: replace each SRT text line with the corresponding
    line from the doc (matched by line number), keeping all timing intact.
    """
    doc_lines = [l.strip() for l in doc_text.strip().split("\n") if l.strip()]
    # Strip timestamp prefixes from doc lines (format: "[1.0s] text")
    import re
    cleaned = []
    for line in doc_lines:
        m = re.match(r'\[\d+\.\d+s\]\s*(.*)', line)
        cleaned.append(m.group(1) if m else line)

    with open(srt_path) as f:
        srt_content = f.read()

    # Parse SRT blocks and replace text
    blocks = srt_content.strip().split("\n\n")
    new_blocks = []
    text_idx = 0
    for block in blocks:
        lines = block.split("\n")
        if len(lines) >= 3 and text_idx < len(cleaned):
            # lines[0] = index, lines[1] = timestamp, lines[2+] = text
            lines[2] = cleaned[text_idx]
            if len(lines) > 3:
                lines = lines[:3]  # drop extra text lines
            text_idx += 1
        new_blocks.append("\n".join(lines))

    with open(srt_path, "w") as f:
        f.write("\n\n".join(new_blocks) + "\n")
```

- [ ] **Step 4: Update skill manifest**

Add `bshort-refresh` to `~/.openclaw/skills/manifest.md` with triggers: "bshort-refresh", "refresh subtitles", "re-render subtitles".

- [ ] **Step 5: Commit**

```bash
cd /Users/ct-mac-mini/dev/video-pipeline
git add pipeline/orchestrator.py
git commit -m "feat: add bshort-refresh and Drive URL support for OpenClaw skills"
```

---

## Phase 8: Editorial Pipeline Tuning

### Task 11: Tune engagement scoring for interview mode

**Files:**
- Modify: `pipeline/editorial.py` — update STORIES_SYSTEM prompt
- Modify: `tests/test_editorial.py` — add interview mode tests

- [ ] **Step 1: Update STORIES_SYSTEM prompt**

Add interview-mode guidance to the system prompt in `pipeline/editorial.py`:

```python
# Add to STORIES_SYSTEM prompt, after "For each story, provide 2-3 hook candidates..."

"""
INTERVIEW MODE (when the transcript has labeled speakers):
- The INTERVIEWER's reactions are engagement signals, not clip content:
  - "wow", "that's great", "that's a great insight" → BOOST the score of the CLIENT's preceding statement
  - "let's try that again", "can you repeat that?" → the NEXT client segment is the good take, the PREVIOUS one should be trimmed
  - Laughter, excitement → strong engagement signal for the preceding client content
- ALL clip timestamps must reference the CLIENT speaker only — never include interviewer audio in a clip
- The interviewer's questions can inform the setup/context but should NOT appear in the final clip
"""
```

- [ ] **Step 2: Add session_mode parameter to extract_stories**

```python
def extract_stories(outline: dict, transcript: list[dict],
                    min_score: int = 7, session_mode: str = "interview") -> dict:
    """Pass 2: Extract self-contained stories from the outline."""
    # ... existing code ...
    mode_note = ""
    if session_mode == "interview":
        mode_note = "\n\nNOTE: This is an INTERVIEW. Apply interview mode rules."

    user_prompt = f"OUTLINE:\n{outline_text}\n\nFULL TRANSCRIPT:\n{transcript_text}{mode_note}"
    # ... rest unchanged ...
```

- [ ] **Step 3: Run existing tests to verify no regressions**

Run: `cd /Users/ct-mac-mini/dev/video-pipeline && python -m pytest tests/test_editorial.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add pipeline/editorial.py tests/test_editorial.py
git commit -m "feat: tune editorial pipeline for interview mode engagement scoring"
```

---

## Phase 9: Integration and Docs

### Task 12: Google service account setup

This is a manual setup task — no code, just configuration.

- [ ] **Step 1: Create Google service account**

In Google Cloud Console:
1. Create or select a project
2. Enable Google Drive API and Google Docs API
3. Create a service account
4. Download JSON key to `~/.config/google/service-account.json`
5. Share the CrowdTamers Drive folders with the service account email

- [ ] **Step 2: Install Python dependencies**

```bash
cd /Users/ct-mac-mini/dev/video-pipeline
pip install google-api-python-client google-auth
```

- [ ] **Step 3: Populate client-map.json with real client data**

Update `client-map.json` with actual client names, Drive folder IDs, and Slack channels.

- [ ] **Step 4: Test Drive access**

```bash
python3 -c "
from pipeline.drive import DriveClient
c = DriveClient()
files = c.list_files('root')
print(f'Found {len(files)} files in root')
"
```

---

### Task 13: End-to-end smoke test

- [ ] **Step 1: Run ingest poller in dry-run mode**

```bash
cd /Users/ct-mac-mini/dev/video-pipeline
python -m pipeline.cli ingest-poll
```

Verify: connects to Drive, finds Zencastr folder, lists sessions.

- [ ] **Step 2: Process a single test video manually**

```bash
python -m pipeline.cli editorial /path/to/test/video.mp4 --output-dir /tmp/smoke-test
```

Verify: generates editorial outputs, FCP 7 XMLs.

- [ ] **Step 3: Open generated FCP 7 XML in Premiere**

Manual verification: import XML in Premiere, confirm source video links, confirm timeline looks correct.

- [ ] **Step 4: Export edited XML from Premiere to done/**

Manual verification: export FCP XML from Premiere, confirm pipeline can parse it.

- [ ] **Step 5: Run done poller**

```bash
python -m pipeline.cli done-poll
```

Verify: detects XML, renders branded video, uploads to Drive.

- [ ] **Step 6: Test OpenClaw skill dispatch**

```bash
# Trigger the video-poll skill manually
openclaw dispatch video-poll
```

Verify: skill runs, polls Drive, reports results.

- [ ] **Step 7: Commit final state**

```bash
git add -A
git commit -m "feat: complete drive-native pipeline integration"
```

---

### Task 14: Migrate existing editorial CLI command

**Files:**
- Modify: `pipeline/cli.py:64-155`

- [ ] **Step 1: Replace yt-dlp download with Drive/local file support**

In the `editorial` command, replace the `yt-dlp` download block (lines 78-86) with:
```python
    # Support Drive URLs, local files, and YouTube URLs
    if url.startswith("https://drive.google.com"):
        import re
        from pipeline.drive import DriveClient
        file_id = re.search(r'/d/([a-zA-Z0-9_-]+)', url).group(1)
        drive = DriveClient()
        os.makedirs(cache_dir, exist_ok=True)
        video_path = os.path.join(cache_dir, "source.mp4")
        if not os.path.exists(video_path):
            click.echo("Downloading from Google Drive...")
            drive.download_file(file_id, video_path)
    elif os.path.isfile(url):
        video_path = url
    else:
        # Fallback to yt-dlp for YouTube URLs
        click.echo("Downloading video...")
        import subprocess as sp
        os.makedirs(cache_dir, exist_ok=True)
        output_template = os.path.join(cache_dir, "%(id)s.%(ext)s")
        sp.run(["yt-dlp", "-f", "bestvideo+bestaudio/best",
                "-o", output_template, "--merge-output-format", "mp4", url],
               check=True, capture_output=True)
        video_path = next(f for f in [os.path.join(cache_dir, x) for x in os.listdir(cache_dir)] if f.endswith(".mp4"))
```

- [ ] **Step 2: Replace generate_kdenlive_xml with generate_fcp7_xml**

Replace the Kdenlive XML generation block (lines 146-153) with:
```python
            from pipeline.fcp7 import generate_fcp7_xml
            source_filenames = [os.path.basename(video_path)]
            w, h = (1080, 1920) if version_name == "short" else (1920, 1080)
            xml = generate_fcp7_xml(
                version, source_videos=source_filenames,
                name=story_id, width=w, height=h,
            )
            project_path = os.path.join(projects_dir, f"{story_id}-{version_name}.xml")
            with open(project_path, "w") as f:
                f.write(xml)
```

- [ ] **Step 3: Run existing tests**

Run: `cd /Users/ct-mac-mini/dev/video-pipeline && python -m pytest tests/ -v`
Expected: PASS (no regressions)

- [ ] **Step 4: Commit**

```bash
git add pipeline/cli.py
git commit -m "feat: migrate editorial CLI to FCP 7 XML and Drive URL support"
```

---

### Task 15: Update Obsidian documentation

The overview and editor guide were created during brainstorming. Update them to reflect any implementation details that changed during planning.

**Files:**
- Modify: `/Users/ct-mac-mini/Documents/Obsidian/CrowdTamers Obsidian Vault/work/CrowdTamers/Tools/Video Pipeline Overview.md`
- Modify: `/Users/ct-mac-mini/Documents/Obsidian/CrowdTamers Obsidian Vault/work/CrowdTamers/Tools/Video Pipeline - Editor Guide.md`

- [ ] **Step 1: Review and update overview doc**

Verify the Mermaid diagram and folder structure match the final implementation. Update any details that changed.

- [ ] **Step 2: Review and update editor guide**

Verify Jude's workflow steps match the actual file naming conventions and folder structure.

- [ ] **Step 3: Commit (if changes made)**

```bash
cd /Users/ct-mac-mini/dev/video-pipeline
git add -A
git commit -m "docs: update Obsidian pipeline docs to match implementation"
```

---

## Dependency Graph

```
Task 1 (Drive client) ──┬──→ Task 6 (Ingest poller) ──→ Task 8 (Orchestrator)
Task 2 (Client map)   ──┘                                      │
Task 3 (Transcript repair) ────────────────────────────────────→│
Task 4 (FCP 7 XML) ──────────────────────────────────┬────────→│
Task 5 (Branding) ────────────────────────────────────│────────→│
                                                      │         │
Task 7 (Done poller) ─────────────────────────────────│────────→│
                                                      │         ↓
Task 14 (CLI migration) ←────────────────────────────┘
Task 9 (Cron scripts) ──→ Task 12 (Service account setup) ──→ Task 13 (Smoke test)
Task 10 (OpenClaw skills) ──→ Task 13
Task 11 (Editorial tuning) ──→ Task 13
Task 15 (Obsidian docs) ──→ Task 13
```

Tasks 1-5 can be parallelized (no cross-dependencies). Tasks 6-7 depend on Task 1. Task 8 depends on all of 1-7. Task 14 depends on Task 4. Tasks 9-11, 14-15 can run after 8. Tasks 12-13 are final integration.

---

## Post-Launch Backlog

These items were identified during architecture review and should be addressed after the core pipeline is validated:

1. **Fuzzy client matching + Slack confirmation** — when session name doesn't match any alias, post to #video-pipeline-ops asking "Who is this client?" instead of silently logging a warning. AM replies, session retries.
2. **Resumable large file uploads** — wrap `upload_file` in a retry loop that catches `HttpError` and resumes from the last chunk instead of re-uploading 7GB from scratch.
3. **Done-flow deduplication state file** — `.done-processed.json` that tracks full completion (render + Notion + Slack) separately from just "file exists in final/". Prevents incomplete deliveries from being silently skipped.
4. **Drive API call optimization** — done poller currently scans all client folders. Filter by `modifiedTime` (last 30 days) to reduce API calls as client count grows.
5. **Unknown session Slack escalation** — sessions with no client match should escalate to Slack after 3 consecutive poll cycles of being unmatched, not just log.
6. **Per-client subtitle styling from style.json** — wire `load_client_style()` into the ASS header generation in `edl.py` so subtitle colors/fonts actually come from the client config instead of hardcoded CrowdTamers defaults.
7. **`filelock` package** — replace `fcntl.flock` with Python `filelock` for more robust cross-platform lock handling.
8. **Heartbeat no-activity alert** — if no sessions processed in 48 hours on a weekday, post a warning to an ops Slack channel. Same pattern as existing botty-heartbeat health checks.
