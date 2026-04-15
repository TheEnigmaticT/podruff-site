"""
Client configuration helpers.

load_soul()      — parse a client's SOUL.md into structured fields
match_client()   — map a Zencastr session name to a client slug via aliases
get_drive_folder() — look up the Drive folder ID for a client slug
"""

import json
import re
from pathlib import Path

_DEFAULT_WORKSPACES = Path.home() / ".openclaw" / "workspaces"
_DEFAULT_CLIENT_MAP = Path.home() / "dev" / "openclaw-kanban" / "client_map.json"

_DEFAULT_BRAND_COLOR = "#1FC2F9"
_DEFAULT_FONT = "Inter"


def load_soul(client_slug: str, workspaces_dir=None) -> dict:
    """
    Read {workspaces_dir}/{client_slug}/SOUL.md and extract structured fields.

    Returns an empty dict if the file does not exist.

    Extracted keys:
        brand_color         — "#XXXXXX" (default: #1FC2F9)
        subtitle_highlight  — "#XXXXXX" (empty string if absent)
        subtitle_font       — font name before any parenthetical (default: Inter)
        slack_channel       — "#channel-name" (empty string if absent)
        notion_db           — Notion page/db ID from crowdtamers URL (empty if absent)
        raw                 — full text of SOUL.md
    """
    workspaces_dir = Path(workspaces_dir) if workspaces_dir else _DEFAULT_WORKSPACES
    soul_path = workspaces_dir / client_slug / "SOUL.md"

    if not soul_path.exists():
        return {}

    raw = soul_path.read_text()

    # Brand color: "**Brand color:** #XXXXXX"
    m = re.search(r"\*\*Brand color:\*\*\s*(#[0-9A-Fa-f]{3,6})", raw)
    brand_color = m.group(1) if m else _DEFAULT_BRAND_COLOR

    # Subtitle highlight: "Highlight color: #XXXXXX"
    m = re.search(r"Highlight color:\s*(#[0-9A-Fa-f]{3,6})", raw)
    subtitle_highlight = m.group(1) if m else ""

    # Subtitle font: "Font: XXXX" (text before first parenthesis/comma/whitespace-run)
    m = re.search(r"Font:\s*([^\(\n,]+)", raw)
    subtitle_font = m.group(1).strip() if m else _DEFAULT_FONT

    # Slack internal channel: "**Internal:** `#channel-name` (CHANNEL_ID)"
    m = re.search(r"\*\*Internal:\*\*\s*`(#[\w-]+)`", raw)
    slack_channel = m.group(1) if m else ""

    # Notion DB: URL containing crowdtamers/{id}
    m = re.search(r"notion\.so/crowdtamers/([a-f0-9]{32})", raw)
    notion_db = m.group(1) if m else ""

    return {
        "brand_color": brand_color,
        "subtitle_highlight": subtitle_highlight,
        "subtitle_font": subtitle_font,
        "slack_channel": slack_channel,
        "notion_db": notion_db,
        "raw": raw,
    }


def _load_client_map(client_map_path=None) -> dict:
    path = Path(client_map_path) if client_map_path else _DEFAULT_CLIENT_MAP
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f)


def match_client(session_name: str, client_map_path=None) -> str | None:
    """
    Match a Zencastr session name to a client slug using the aliases list.

    Matching is case-insensitive substring search: any alias present anywhere
    in the session name is considered a match.

    Returns the client slug string, or None if no alias matches.
    """
    client_map = _load_client_map(client_map_path)
    session_lower = session_name.lower()

    for slug, entry in client_map.items():
        for alias in entry.get("aliases", []):
            if alias.lower() in session_lower:
                return slug

    return None


def get_drive_folder(client_slug: str, client_map_path=None) -> str:
    """
    Return the drive_folder_id for client_slug, or empty string if not found.
    """
    client_map = _load_client_map(client_map_path)
    entry = client_map.get(client_slug, {})
    return entry.get("drive_folder_id", "")
