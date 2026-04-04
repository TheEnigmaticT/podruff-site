import requests
from pipeline.config import LATE_API_KEY
from pipeline.retry import retry

LATE_URL = "https://zernio.com/api/v1"
HEADERS = {
    "Authorization": f"Bearer {LATE_API_KEY}",
    "Content-Type": "application/json",
}

# Map platform names to Late account IDs
ACCOUNT_IDS = {
    "linkedin": "693b3060f43160a0bc99af56",
    "facebook": "693b3541f43160a0bc99af6f",
    "instagram": "693b31cef43160a0bc99af5c",
    "threads": "693b31f8f43160a0bc99af5e",
    "reddit": "693b3206f43160a0bc99af5f",
    "tiktok": "693b3509f43160a0bc99af6d",
    "youtube": "69a0c480dc8cab9432a8d305",
}


def publish_clip(
    video_url: str,
    thumbnail_url: str,
    title: str,
    description: str,
    platforms: list[str],
    scheduled_for: str | None = None,
    custom_content: dict[str, str] | None = None,
) -> dict:
    """Publish a clip via the Late API.

    Args:
        custom_content: Optional per-platform text overrides, e.g.
            {"youtube": "YouTube-specific description", "linkedin": "LinkedIn post text"}
    """
    platform_objects = []
    for p in platforms:
        account_id = ACCOUNT_IDS.get(p)
        if not account_id:
            continue
        platform_objects.append({"platform": p, "accountId": account_id})

    if not platform_objects:
        raise ValueError(f"No valid accounts for platforms: {platforms}")

    body = {
        "content": f"{title}\n\n{description}",
        "platforms": platform_objects,
        "mediaUrls": [video_url],
    }

    if custom_content:
        body["customContent"] = {
            p: {"content": text} for p, text in custom_content.items()
        }

    if scheduled_for:
        body["scheduledFor"] = scheduled_for
    else:
        body["publishNow"] = True

    return _post_to_late(body)


@retry(max_attempts=3, exceptions=(requests.RequestException,))
def _post_to_late(body: dict) -> dict:
    resp = requests.post(f"{LATE_URL}/posts", headers=HEADERS, json=body)
    resp.raise_for_status()
    return resp.json()
