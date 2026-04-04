#!/usr/bin/env python3
"""Onboard a new client: create Zernio profile, generate OAuth connect links,
and create a Notion page with clickable links for the client to connect their accounts.

Usage:
    python3 social_onboard_client.py "Client Name" --page-id PAGE_ID           # append to existing page
    python3 social_onboard_client.py "Client Name" --notion-db DATABASE_ID      # create new page in DB
    python3 social_onboard_client.py "Client Name" --all-platforms --page-id ID # all platforms
    python3 social_onboard_client.py "Client Name" -p linkedin,youtube --dry-run
"""

import argparse
import json
import logging
import os
import sys

from social_apis import ZernioClient
from social_config import load_config
from social_notion import NotionClient

log = logging.getLogger("social_onboard")

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "social_config.json")

# Default platforms for new clients (most common set)
DEFAULT_PLATFORMS = ["linkedin", "youtube", "tiktok", "instagram", "threads", "facebook"]

# Human-readable names and emoji for Notion page
PLATFORM_DISPLAY = {
    "linkedin": ("LinkedIn", "\U0001f4bc"),
    "instagram": ("Instagram", "\U0001f4f7"),
    "facebook": ("Facebook", "\U0001f30d"),
    "threads": ("Threads", "\U0001f9f5"),
    "tiktok": ("TikTok", "\U0001f3b5"),
    "youtube": ("YouTube", "\U0001f3ac"),
    "pinterest": ("Pinterest", "\U0001f4cc"),
    "reddit": ("Reddit", "\U0001f4e2"),
    "bluesky": ("Bluesky", "\U0001f98b"),
    "googlebusiness": ("Google Business", "\U0001f3e2"),
}


def _build_connect_blocks(client_name, profile_id, connect_urls):
    """Build Notion blocks for the connect links section."""
    blocks = [
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [{"type": "text", "text": {"content": "Connect Your Social Accounts"}}]
            },
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {
                    "content": "Click each link below to connect your social media accounts. "
                               "You'll be redirected to each platform to authorize access."
                }}]
            },
        },
        {
            "object": "block",
            "type": "divider",
            "divider": {},
        },
    ]

    for platform, auth_url in connect_urls.items():
        display_name, emoji = PLATFORM_DISPLAY.get(platform, (platform.title(), "\U0001f517"))
        blocks.append({
            "object": "block",
            "type": "bookmark",
            "bookmark": {
                "url": auth_url,
                "caption": [{"type": "text", "text": {
                    "content": f"{emoji} Connect {display_name}"
                }}],
            },
        })

    blocks.extend([
        {
            "object": "block",
            "type": "divider",
            "divider": {},
        },
        {
            "object": "block",
            "type": "callout",
            "callout": {
                "rich_text": [{"type": "text", "text": {
                    "content": f"Zernio Profile ID: {profile_id}"
                }}],
                "icon": {"type": "emoji", "emoji": "\u2699\ufe0f"},
            },
        },
    ])
    return blocks


def append_to_existing_page(notion, page_id, client_name, profile_id, connect_urls):
    """Append connect link blocks to an existing Notion page."""
    blocks = _build_connect_blocks(client_name, profile_id, connect_urls)
    notion.append_blocks(page_id, blocks)
    return f"https://www.notion.so/{page_id.replace('-', '')}"


def create_notion_onboarding_page(notion, database_id, client_name, profile_id, connect_urls):
    """Create a new Notion page with OAuth connect links for the client."""
    blocks = _build_connect_blocks(client_name, profile_id, connect_urls)

    properties = {
        "title": {
            "title": [{"text": {"content": f"{client_name} — Social Account Setup"}}]
        },
    }

    page = notion.create_page(database_id, properties)
    notion.append_blocks(page["id"], blocks)
    return page["url"]


def main():
    parser = argparse.ArgumentParser(description="Onboard a new social media client")
    parser.add_argument("client_name", help="Display name for the client")
    parser.add_argument(
        "--platforms", "-p",
        help="Comma-separated platforms (default: linkedin,youtube,tiktok,instagram,threads,facebook)",
    )
    parser.add_argument("--all-platforms", action="store_true", help="Generate links for all supported platforms")
    parser.add_argument("--page-id", help="Existing Notion page ID to append connect links to")
    parser.add_argument("--notion-db", help="Notion database ID to create a new onboarding page in")
    parser.add_argument("--color", default="#ffeda0", help="Profile color hex (default: #ffeda0)")
    parser.add_argument("--description", default="", help="Profile description")
    parser.add_argument("--dry-run", action="store_true", help="Print URLs without creating Notion page")
    parser.add_argument("--config", default=CONFIG_PATH, help="Path to social_config.json")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    config = load_config(args.config)
    zernio = ZernioClient(config["late_api_key"])
    notion = NotionClient(config["notion_api_key"])

    # Resolve platforms
    if args.all_platforms:
        platforms = list(PLATFORM_DISPLAY.keys())
    elif args.platforms:
        platforms = [p.strip().lower() for p in args.platforms.split(",")]
    else:
        platforms = DEFAULT_PLATFORMS

    # 1. Create Zernio profile
    log.info("Creating Zernio profile for '%s'...", args.client_name)
    profile = zernio.create_profile(
        name=args.client_name,
        description=args.description or f"Social media accounts for {args.client_name}",
        color=args.color,
    )
    profile_id = profile["_id"]
    log.info("Created profile: %s (ID: %s)", profile["name"], profile_id)

    # 2. Generate connect URLs
    log.info("Generating connect links for: %s", ", ".join(platforms))
    connect_urls = zernio.get_connect_urls(profile_id, platforms)

    if not connect_urls:
        log.error("Failed to generate any connect URLs. Check API key and platform names.")
        sys.exit(1)

    log.info("Generated %d/%d connect links", len(connect_urls), len(platforms))

    # 3. Print URLs
    print(f"\n{'='*60}")
    print(f"  {args.client_name} — Connect Links")
    print(f"  Profile ID: {profile_id}")
    print(f"{'='*60}\n")
    for platform, url in connect_urls.items():
        display_name, emoji = PLATFORM_DISPLAY.get(platform, (platform.title(), "\U0001f517"))
        print(f"  {emoji} {display_name}:")
        print(f"    {url}\n")

    if args.dry_run:
        print("(dry run — skipping Notion page creation)")
        return

    # 4. Write to Notion — either append to existing page or create new one
    if args.page_id:
        log.info("Appending connect links to existing Notion page...")
        page_url = append_to_existing_page(notion, args.page_id, args.client_name, profile_id, connect_urls)
        print(f"\n  Notion page: {page_url}\n")
    else:
        db_id = args.notion_db
        if not db_id:
            client_key = args.client_name.lower().replace(" ", "")
            client_cfg = config.get("clients", {}).get(client_key, {})
            db_id = client_cfg.get("notion_database_id")

        if not db_id:
            log.warning("No Notion target specified. Use --page-id or --notion-db.")
            print("\nTo add links later, run:")
            print(f"  python3 social_onboard_client.py \"{args.client_name}\" --page-id PAGE_ID")
            return

        log.info("Creating Notion onboarding page...")
        page_url = create_notion_onboarding_page(notion, db_id, args.client_name, profile_id, connect_urls)
        print(f"\n  Notion page: {page_url}\n")

    # 5. Output config snippet
    print(f"Add to social_config.json clients section:\n")
    client_key = args.client_name.lower().replace(" ", "")
    snippet = {
        client_key: {
            "notion_database_id": db_id,
            "zernio_profile_id": profile_id,
            "platforms": {},
        }
    }
    print(json.dumps(snippet, indent=4))
    print("\n(Fill in account_ids after the client connects their accounts)")


if __name__ == "__main__":
    main()
