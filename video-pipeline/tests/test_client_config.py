import json
import pytest
from pathlib import Path
from pipeline.client_config import load_soul, match_client, get_drive_folder


SAMPLE_SOUL_MD = """\
## Brand Identity
- **Brand color:** #E38533 (orange)

## Subtitle Styling
- Highlight color: #E38533 (orange), ASS BGR: &H003385E3
- Font: Inter (fallback from Milibus)

## Slack Channels
- **Internal:** `#jonathan-brill` (C05SGKQAQGK)

## Notion
- Content Automation Pipeline: https://www.notion.so/crowdtamers/342f778746cb80ae821dc8bd79ae7506
"""

SAMPLE_CLIENT_MAP = {
    "jonathan-brill": {
        "channels": [],
        "drive_folder_id": "1ABCxyz123",
        "aliases": ["Jonathan Brill", "Brill"],
    },
    "ogment": {
        "channels": [],
        "drive_folder_id": "2DEFabc456",
        "aliases": ["Ogment", "ogment-ai"],
    },
}


# ── load_soul ──────────────────────────────────────────────────────────────────

def test_load_soul(tmp_path):
    workspace = tmp_path / "jonathan-brill"
    workspace.mkdir()
    (workspace / "SOUL.md").write_text(SAMPLE_SOUL_MD)

    data = load_soul("jonathan-brill", workspaces_dir=str(tmp_path))

    assert data["brand_color"] == "#E38533"
    assert data["subtitle_highlight"] == "#E38533"
    assert data["subtitle_font"] == "Inter"
    assert data["slack_channel"] == "#jonathan-brill"
    assert data["notion_db"] == "342f778746cb80ae821dc8bd79ae7506"
    assert data["raw"] == SAMPLE_SOUL_MD


def test_load_soul_defaults_when_fields_absent(tmp_path):
    workspace = tmp_path / "no-fields"
    workspace.mkdir()
    (workspace / "SOUL.md").write_text("# Empty soul\n")

    data = load_soul("no-fields", workspaces_dir=str(tmp_path))

    assert data["brand_color"] == "#1FC2F9"
    assert data["subtitle_font"] == "Inter"
    assert data["slack_channel"] == ""
    assert data["notion_db"] == ""
    assert data["raw"] == "# Empty soul\n"


def test_load_soul_missing(tmp_path):
    data = load_soul("nonexistent-client", workspaces_dir=str(tmp_path))
    assert data == {}


# ── match_client ───────────────────────────────────────────────────────────────

def test_match_client(tmp_path):
    map_path = tmp_path / "client_map.json"
    map_path.write_text(json.dumps(SAMPLE_CLIENT_MAP))

    assert match_client("Jonathan Brill - Episode 12", client_map_path=str(map_path)) == "jonathan-brill"
    assert match_client("brill podcast recording", client_map_path=str(map_path)) == "jonathan-brill"
    assert match_client("OGMENT weekly standup", client_map_path=str(map_path)) == "ogment"


def test_match_client_case_insensitive(tmp_path):
    map_path = tmp_path / "client_map.json"
    map_path.write_text(json.dumps(SAMPLE_CLIENT_MAP))

    assert match_client("JONATHAN BRILL interview", client_map_path=str(map_path)) == "jonathan-brill"
    assert match_client("jonathan brill", client_map_path=str(map_path)) == "jonathan-brill"


def test_match_client_no_match(tmp_path):
    map_path = tmp_path / "client_map.json"
    map_path.write_text(json.dumps(SAMPLE_CLIENT_MAP))

    assert match_client("unknown session xyz", client_map_path=str(map_path)) is None


def test_match_client_missing_map(tmp_path):
    result = match_client("Jonathan Brill", client_map_path=str(tmp_path / "missing.json"))
    assert result is None


# ── get_drive_folder ───────────────────────────────────────────────────────────

def test_get_drive_folder(tmp_path):
    map_path = tmp_path / "client_map.json"
    map_path.write_text(json.dumps(SAMPLE_CLIENT_MAP))

    assert get_drive_folder("jonathan-brill", client_map_path=str(map_path)) == "1ABCxyz123"
    assert get_drive_folder("ogment", client_map_path=str(map_path)) == "2DEFabc456"


def test_get_drive_folder_not_found(tmp_path):
    map_path = tmp_path / "client_map.json"
    map_path.write_text(json.dumps(SAMPLE_CLIENT_MAP))

    assert get_drive_folder("unknown-client", client_map_path=str(map_path)) == ""
