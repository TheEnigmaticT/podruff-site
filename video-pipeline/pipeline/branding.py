"""Client branding configuration loaded from SOUL.md files."""

import os
import re

OPENCLAW_DIR = os.path.expanduser("~/.openclaw")

# Defaults applied when SOUL.md fields are missing
DEFAULTS = {
    "primary_color": "#FFFFFF",
    "subtitle_font": "Inter",
    "subtitle_size": 120,
    "highlight_color": None,  # Falls back to primary_color
    "logo_position": "bottom-right",
    "logo_opacity": 0.8,
}


def load_branding(client_name: str) -> dict:
    """Load branding config from a client's SOUL.md.

    Parses the ## Branding section for key-value pairs like:
        - **primary_color:** #0119FF

    Returns a dict with all DEFAULTS filled in.
    """
    soul_paths = [
        os.path.join(OPENCLAW_DIR, f"workspace-{client_name}", "SOUL.md"),
        os.path.join(OPENCLAW_DIR, f"workspace-{client_name}", "soul.md"),
    ]

    soul_text = None
    for p in soul_paths:
        try:
            with open(p) as f:
                soul_text = f.read()
            break
        except FileNotFoundError:
            continue

    config = dict(DEFAULTS)

    if soul_text:
        # Extract ## Branding section
        branding_match = re.search(
            r"^## Branding\s*\n(.*?)(?=^## |\Z)",
            soul_text,
            re.MULTILINE | re.DOTALL,
        )
        if branding_match:
            section = branding_match.group(1)
            # Parse "- **key:** value" lines
            for match in re.finditer(
                r"^\s*-\s+\*\*(\w+):\*\*\s*(.+)$", section, re.MULTILINE
            ):
                key = match.group(1).strip()
                value = match.group(2).strip()
                if value.lower() == "tbd":
                    continue
                # Convert numeric values
                try:
                    if "." in value:
                        value = float(value)
                    elif value.isdigit():
                        value = int(value)
                except (ValueError, AttributeError):
                    pass
                config[key] = value

    # highlight_color defaults to primary_color
    if config.get("highlight_color") is None:
        config["highlight_color"] = config["primary_color"]

    return config


def get_subtitle_style(client_name: str) -> dict:
    """Get subtitle styling dict for use with generate_clip_subtitles."""
    branding = load_branding(client_name)
    return {
        "font": branding["subtitle_font"],
        "size": branding["subtitle_size"],
        "highlight_color": branding["highlight_color"],
    }
