import requests
from pipeline.config import NOTION_API_KEY, NOTION_DATABASE_ID
from pipeline.retry import retry

NOTION_URL = "https://api.notion.com/v1"
HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}


@retry(max_attempts=3, exceptions=(requests.RequestException,))
def get_ingest_cards() -> list[dict]:
    """Get all cards with Status = Ingest."""
    resp = requests.post(
        f"{NOTION_URL}/databases/{NOTION_DATABASE_ID}/query",
        headers=HEADERS,
        json={
            "filter": {
                "property": "Status",
                "select": {"equals": "Ingest"},
            }
        },
    )
    resp.raise_for_status()
    return resp.json()["results"]


@retry(max_attempts=3, exceptions=(requests.RequestException,))
def create_clip_card(
    headline: str,
    topic_name: str,
    hook_sentence: str,
    clip_url: str,
    short_url: str,
    thumbnail_url: str,
    description: str,
    duration: str,
    parent_card_id: str,
) -> str:
    """Create a new clip card in Notion and return its ID."""
    properties = {
        "Title": {"title": [{"text": {"content": headline}}]},
        "Status": {"select": {"name": "To Review"}},
        "Topic Name": {"rich_text": [{"text": {"content": topic_name}}]},
        "Hook Sentence": {"rich_text": [{"text": {"content": hook_sentence}}]},
        "Clip URL": {"url": clip_url},
        "Short URL": {"url": short_url},
        "Headline": {"rich_text": [{"text": {"content": headline}}]},
        "Description": {"rich_text": [{"text": {"content": description}}]},
        "Duration": {"rich_text": [{"text": {"content": duration}}]},
    }
    if parent_card_id and parent_card_id != "manual":
        properties["Parent Video"] = {"relation": [{"id": parent_card_id}]}

    resp = requests.post(
        f"{NOTION_URL}/pages",
        headers=HEADERS,
        json={
            "parent": {"database_id": NOTION_DATABASE_ID},
            "properties": properties,
            "children": [
                {
                    "object": "block",
                    "type": "image",
                    "image": {
                        "type": "external",
                        "external": {"url": thumbnail_url},
                    },
                }
            ],
        },
    )
    resp.raise_for_status()
    return resp.json()["id"]


@retry(max_attempts=3, exceptions=(requests.RequestException,))
def update_card_status(card_id: str, status: str, **extra_props) -> None:
    """Update a card's status and optional extra properties."""
    properties = {"Status": {"select": {"name": status}}}
    for key, value in extra_props.items():
        properties[key] = {"url": value}

    requests.patch(
        f"{NOTION_URL}/pages/{card_id}",
        headers=HEADERS,
        json={"properties": properties},
    ).raise_for_status()


@retry(max_attempts=3, exceptions=(requests.RequestException,))
def get_scheduled_cards(before: str | None = None) -> list[dict]:
    """Get cards with Status = Scheduled and Publish Date on or before the given ISO datetime.

    If no datetime is provided, uses the current time. This means the cron job
    picks up any card whose scheduled time has arrived.
    """
    if before is None:
        from datetime import datetime, timezone
        before = datetime.now(timezone.utc).isoformat()

    resp = requests.post(
        f"{NOTION_URL}/databases/{NOTION_DATABASE_ID}/query",
        headers=HEADERS,
        json={
            "filter": {
                "and": [
                    {"property": "Status", "select": {"equals": "Scheduled"}},
                    {"property": "Publish Date", "date": {"on_or_before": before}},
                ]
            }
        },
    )
    resp.raise_for_status()
    return resp.json()["results"]
