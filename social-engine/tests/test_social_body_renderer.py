import json
from unittest.mock import patch, MagicMock


def _extract_text(block):
    """Helper: extract all plain text from a Notion block dict."""
    btype = block.get("type", "")
    rich_text = block.get(btype, {}).get("rich_text", [])
    return " ".join(rt.get("text", {}).get("content", "") for rt in rich_text)


def test_build_preview_blocks_includes_all_platforms():
    """build_preview_blocks should create heading + text blocks for each platform with content."""
    from social_body_renderer import build_preview_blocks

    row = {
        "Title": "The 11 PM Fire Drill",
        "LinkedIn Text": "POV: You're working until 11 PM...",
        "X Text": "Finance teams don't have control of their data pipeline. That's the real problem.",
        "YouTube Text": "",
        "Instagram Text": "The 11 PM fire drill is real...",
        "Post Text": "Default text here",
        "Publish Date": "2026-04-02T14:00:00Z",
        "Platforms": ["linkedin", "twitter", "instagram"],
        "Clip URL": "https://r2.example.com/clip.mp4",
        "Thumbnail URL": "https://r2.example.com/thumb.png",
    }

    blocks = build_preview_blocks(row)

    block_texts = [_extract_text(b) for b in blocks]
    all_text = " ".join(block_texts)

    assert "LinkedIn" in all_text
    assert "X / Twitter" in all_text
    assert "Instagram" in all_text
    assert "POV: You're working" in all_text
    assert "Finance teams" in all_text
    # YouTube should be absent (empty text)
    assert "YouTube" not in all_text


def test_build_preview_blocks_shows_warning_without_transcript():
    """build_preview_blocks should show a warning callout when has_transcript=False."""
    from social_body_renderer import build_preview_blocks

    row = {
        "Title": "Test",
        "Post Text": "Hello",
        "Publish Date": "2026-04-02",
        "Platforms": ["linkedin"],
        "LinkedIn Text": "Some text",
        "Clip URL": "",
        "Thumbnail URL": "",
    }

    blocks = build_preview_blocks(row, has_transcript=False)
    first_block = blocks[0]
    assert first_block["type"] == "callout"
    callout_text = first_block["callout"]["rich_text"][0]["text"]["content"]
    assert "WARNING" in callout_text
    assert "WITHOUT" in callout_text


def test_build_preview_blocks_no_warning_with_transcript():
    """build_preview_blocks should NOT show warning when has_transcript=True."""
    from social_body_renderer import build_preview_blocks

    row = {
        "Title": "Test",
        "Post Text": "Hello",
        "Publish Date": "2026-04-02",
        "Platforms": ["linkedin"],
        "LinkedIn Text": "Some text",
        "Clip URL": "",
        "Thumbnail URL": "",
    }

    blocks = build_preview_blocks(row, has_transcript=True)
    first_block = blocks[0]
    assert first_block["type"] == "heading_1"


def test_build_preview_blocks_shows_media():
    """build_preview_blocks should include image/video embed blocks."""
    from social_body_renderer import build_preview_blocks

    row = {
        "Title": "Test",
        "Post Text": "Hello",
        "Publish Date": "2026-04-02",
        "Platforms": ["linkedin"],
        "LinkedIn Text": "",
        "Clip URL": "",
        "Thumbnail URL": "https://r2.example.com/thumb.png",
    }

    blocks = build_preview_blocks(row)
    block_types = [b["type"] for b in blocks]
    assert "image" in block_types
