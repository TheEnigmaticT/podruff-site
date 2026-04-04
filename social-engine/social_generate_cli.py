#!/usr/bin/env python3
"""Generate social copy for a Notion clip card.

Usage:
    python3 social_generate_cli.py <notion_page_id> [--render]
    python3 social_generate_cli.py --status "Draft" [--render] [--client crowdtamers]

Options:
    <notion_page_id>    Generate copy for a specific page
    --status STATUS     Generate copy for all pages with this status
    --render            Also render the client preview body after generating
    --client NAME       Client name from social_config.json (default: first client)
"""

import argparse
import logging
import os
import sys

from social_config import load_config
from social_copywriter import generate_social_copy
from social_body_renderer import render_to_notion
from social_notion import NotionClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("social_generate")


def main():
    parser = argparse.ArgumentParser(description="Generate social copy from clip transcripts")
    parser.add_argument("page_id", nargs="?", help="Notion page ID")
    parser.add_argument("--status", help="Process all pages with this status")
    parser.add_argument("--render", action="store_true", help="Also render client preview")
    parser.add_argument("--force", action="store_true", help="Overwrite existing platform text")
    parser.add_argument("--client", default=None, help="Client name from config")
    args = parser.parse_args()

    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "social_config.json")
    config = load_config(config_path)
    notion = NotionClient(config["notion_api_key"])

    if args.client:
        config["client_name"] = args.client

    if args.page_id:
        log.info("Generating copy for page %s", args.page_id)
        generate_social_copy(args.page_id, notion, config, force=args.force)
        if args.render:
            row = notion.get_page(args.page_id)
            has_transcript = row.get("Posting Errors", "") != "GENERATED_WITHOUT_TRANSCRIPT"
            render_to_notion(args.page_id, row, notion, has_transcript=has_transcript)
            log.info("Rendered client preview")

    elif args.status:
        client_name = args.client or next(iter(config.get("clients", {})))
        client_cfg = config["clients"][client_name]
        db_id = client_cfg["notion_database_id"]
        pages = notion.query_by_status(db_id, args.status)
        log.info("Found %d pages with status '%s'", len(pages), args.status)
        for page in pages:
            row = NotionClient.extract_row(page)
            log.info("Processing: %s", row.get("Title"))
            generate_social_copy(row["id"], notion, config, force=args.force)
            if args.render:
                fresh_row = notion.get_page(row["id"])
                has_transcript = fresh_row.get("Posting Errors", "") != "GENERATED_WITHOUT_TRANSCRIPT"
                render_to_notion(row["id"], fresh_row, notion, has_transcript=has_transcript)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
