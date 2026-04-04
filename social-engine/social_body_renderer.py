"""Render Social DB metadata fields into Notion page body blocks for client review."""

from social_config import PLATFORM_TEXT_FIELDS

PLATFORM_DISPLAY = {
    "linkedin": "LinkedIn",
    "twitter": "X / Twitter",
    "youtube": "YouTube",
    "instagram": "Instagram",
    "tiktok": "TikTok",
    "threads": "Threads",
    "facebook": "Facebook",
    "reddit": "Reddit",
    "bluesky": "Bluesky",
    "pinterest": "Pinterest",
    "google_business": "Google Business",
}


def _heading(level, text):
    key = f"heading_{level}"
    return {"type": key, key: {"rich_text": [{"type": "text", "text": {"content": text}}]}}


def _paragraph(text):
    chunks = [text[i:i+2000] for i in range(0, len(text), 2000)]
    return {"type": "paragraph", "paragraph": {
        "rich_text": [{"type": "text", "text": {"content": c}} for c in chunks]
    }}


def _divider():
    return {"type": "divider", "divider": {}}


def _image(url):
    return {"type": "image", "image": {"type": "external", "external": {"url": url}}}


def _callout(text, emoji="\U0001f4cb"):
    return {"type": "callout", "callout": {
        "icon": {"type": "emoji", "emoji": emoji},
        "rich_text": [{"type": "text", "text": {"content": text}}],
    }}


def build_preview_blocks(row, has_transcript=True):
    """Build Notion blocks for client preview from a Social DB row dict."""
    blocks = []

    if not has_transcript:
        blocks.append(_callout(
            "WARNING: This preview was generated WITHOUT a video transcript. "
            "Copy may be based on limited context (description/hook only). "
            "Review carefully before sending to client.",
            emoji="\u26a0\ufe0f",
        ))

    blocks.append(_heading(1, "Content Preview"))

    publish_date = row.get("Publish Date", "Not set")
    platforms = row.get("Platforms", [])
    platform_names = ", ".join(PLATFORM_DISPLAY.get(p, p) for p in platforms)
    blocks.append(_callout(
        f"Publish: {publish_date}\nPlatforms: {platform_names}",
        emoji="\U0001f4c5",
    ))

    thumb = row.get("Thumbnail URL", "")
    if thumb:
        blocks.append(_image(thumb))

    blocks.append(_divider())

    for platform in platforms:
        field_name = PLATFORM_TEXT_FIELDS.get(platform, "")
        text = row.get(field_name, "") if field_name else ""
        if not text:
            text = row.get("Post Text", "")
        if not text:
            continue

        display = PLATFORM_DISPLAY.get(platform, platform)
        blocks.append(_heading(2, display))
        blocks.append(_paragraph(text))
        blocks.append(_divider())

    blocks.append(_callout(
        "Review the content above. When approved, change status to 'Approved' and the post will be scheduled automatically.",
        emoji="\u2705",
    ))

    return blocks


def render_to_notion(page_id, row, notion_client, has_transcript=True):
    """Build preview blocks and write them to the Notion page body."""
    blocks = build_preview_blocks(row, has_transcript=has_transcript)
    notion_client.replace_page_body(page_id, blocks)
