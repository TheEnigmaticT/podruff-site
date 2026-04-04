"""Config loader and validation for the social media poller."""

import json

PLATFORM_TEXT_FIELDS = {
    "linkedin": "LinkedIn Text",
    "youtube": "YouTube Text",
    "twitter": "X Text",
    "instagram": "Instagram Text",
    "tiktok": "TikTok Text",
    "threads": "Threads Text",
    "facebook": "Facebook Text",
    "reddit": "Reddit Text",
    "bluesky": "Bluesky Text",
    "pinterest": "Pinterest Text",
    "google_business": "Google Business Text",
}


def load_config(path):
    """Read and return JSON config from a file path."""
    with open(path, "r") as f:
        return json.load(f)


def resolve_text(row, platform):
    """Resolve which text to use for a platform.

    Checks platform-specific field first, falls back to Post Text.
    Returns None if both are empty.
    """
    platform_field = PLATFORM_TEXT_FIELDS.get(platform)
    if platform_field:
        platform_text = row.get(platform_field, "")
        if platform_text:
            return platform_text

    post_text = row.get("Post Text", "")
    if post_text:
        return post_text

    return None


def validate_post(row, client_platforms):
    """Validate a post row. Returns a list of error strings (empty = valid)."""
    errors = []

    # Check if any text is available at all
    post_text = row.get("Post Text", "")
    has_any_text = bool(post_text)
    if not has_any_text:
        for field in PLATFORM_TEXT_FIELDS.values():
            if row.get(field, ""):
                has_any_text = True
                break
    if not has_any_text:
        errors.append("No post text available")

    # Check platforms
    platforms = row.get("Platforms", [])
    if not platforms:
        errors.append("No platforms selected")

    # Check publish date
    if not row.get("Publish Date", ""):
        errors.append("No publish date set")

    # Check X/Twitter character limit
    if "twitter" in platforms:
        twitter_text = resolve_text(row, "twitter")
        if twitter_text and len(twitter_text) > 280:
            errors.append(
                f"X text exceeds 280 characters ({len(twitter_text)} chars)"
            )

    # Check unconfigured platforms
    for platform in platforms:
        if platform not in client_platforms:
            errors.append(f"Platform {platform} not configured for this client")

    return errors
