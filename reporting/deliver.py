import os
import requests
from pathlib import Path
from typing import Optional

VAULT_DEFAULT = os.environ.get(
    "OBSIDIAN_VAULT",
    str(Path.home() / "Documents/Obsidian/CrowdTamers Obsidian Vault")
)


def write_to_obsidian(report: str, client: dict, week: str, vault_path: Optional[str] = None) -> str:
    """Write the final report markdown to the Obsidian vault. Returns the file path."""
    vault = vault_path or VAULT_DEFAULT
    out_dir = Path(vault) / "work" / "reporting" / client["slug"]
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{week}-report.md"
    path.write_text(report)
    return str(path)


def push_to_notion(report: str, client: dict, week: str, token: Optional[str] = None) -> str:
    """Create a child page under the client's Notion page. Returns the page URL."""
    token = token or os.environ.get("NOTION_TOKEN")
    if not token:
        raise RuntimeError("NOTION_TOKEN is not set. Add it to .env.")
    content = report
    if len(report) > 2000:
        import sys
        print(f"WARNING: Notion report truncated from {len(report)} to 2000 chars for {client['name']}", file=sys.stderr)
        content = report[:1997] + "..."
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }
    body = {
        "parent": {"page_id": client["notion_page_id"]},
        "properties": {
            "title": {"title": [{"text": {"content": f"Weekly Report — {week}"}}]}
        },
        "children": [
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": content}}]
                },
            }
        ],
    }
    response = requests.post(
        "https://api.notion.com/v1/pages",
        headers=headers,
        json=body,
    )
    response.raise_for_status()
    return response.json().get("url", "")


def create_impressflow_deck(
    report: str,
    client: dict,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> str:
    """POST the report markdown to ImpressFlow and return the hosted deck URL."""
    api_key = api_key or os.environ.get("IMPRESSFLOW_API_KEY")
    if not api_key:
        raise RuntimeError("IMPRESSFLOW_API_KEY is not set. Add it to .env.")
    base_url = base_url or os.environ.get("IMPRESSFLOW_BASE_URL")
    if not base_url:
        raise RuntimeError("IMPRESSFLOW_BASE_URL is not set. Add it to .env.")
    response = requests.post(
        f"{base_url}/create-report-deck",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "markdown": report,
            "theme": client.get("impressflow_theme", "Crowd Tamers"),
            "layout": "Grid",
            "client_name": client["name"],
        },
    )
    response.raise_for_status()
    return response.json()["url"]


def post_to_slack(
    client: dict,
    week: str,
    notion_url: str,
    deck_url: str,
    webhook_url: Optional[str] = None,
) -> None:
    """Post report links to the internal Slack channel via webhook."""
    webhook_url = webhook_url or os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook_url:
        raise RuntimeError("SLACK_WEBHOOK_URL is not set. Add it to .env.")
    text = (
        f"*{client['name']} — Week {week}*\n"
        f"<{notion_url}|Full Report> · <{deck_url}|Slide Deck>"
    )
    response = requests.post(
        webhook_url,
        json={"text": text, "channel": client.get("slack_channel", "#ct-reporting")},
    )
    response.raise_for_status()
