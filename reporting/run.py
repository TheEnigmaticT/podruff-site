#!/usr/bin/env python3
import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from collect import fetch_all
from analyze import analyze
from reconcile import compare_reports, ReconciliationError
from deliver import write_to_obsidian, push_to_notion, create_impressflow_deck, post_to_slack

VAULT_DIR = Path(__file__).parent / "vault"


def get_week_dates(iso_week: str):
    """Convert '2026-W10' to (monday_date, sunday_date) as YYYY-MM-DD strings."""
    year, week = iso_week.split("-W")
    monday = datetime.strptime(f"{year}-W{int(week):02d}-1", "%G-W%V-%u")
    sunday = monday + timedelta(days=6)
    return monday.strftime("%Y-%m-%d"), sunday.strftime("%Y-%m-%d")


def post_alert(message: str) -> None:
    """Post a warning to #ct-ops Slack and stderr."""
    webhook = os.environ.get("SLACK_WEBHOOK_URL", "")
    if webhook:
        import requests
        requests.post(
            webhook,
            json={
                "text": f":warning: *Reporting alert*\n{message}",
                "channel": "#ct-ops",
            },
        )
    print(f"ALERT: {message}", file=sys.stderr)


def _save_raw(client_slug: str, week: str, raw_data: dict) -> None:
    out = VAULT_DIR / client_slug / week / "raw"
    out.mkdir(parents=True, exist_ok=True)
    for channel, data in raw_data.items():
        (out / f"{channel}.json").write_text(json.dumps(data, indent=2))


def run_client(config_path: str, week: str) -> None:
    client = json.loads(Path(config_path).read_text())
    if not client.get("active", True):
        print(f"Skipping {client['name']} (inactive)")
        return

    print(f"Running report for {client['name']} — {week}")
    start_date, end_date = get_week_dates(week)

    # 1. Collect
    raw_data = fetch_all(client, start_date, end_date)
    _save_raw(client["slug"], week, raw_data)

    # 2. Analyze (dual pass)
    pass_a, pass_b = analyze(raw_data, client)

    week_dir = VAULT_DIR / client["slug"] / week
    week_dir.mkdir(parents=True, exist_ok=True)
    (week_dir / "analysis-a.md").write_text(pass_a)
    (week_dir / "analysis-b.md").write_text(pass_b)

    # 2b. Minority report check
    try:
        final_report = compare_reports(pass_a, pass_b)
    except ReconciliationError as e:
        post_alert(f"{client['name']} ({week}): {e}")
        return  # Halt — do not deliver anything

    (week_dir / "final-report.md").write_text(final_report)

    # 3a. Obsidian + Notion
    write_to_obsidian(final_report, client, week)
    notion_url = push_to_notion(final_report, client, week)

    # 3b. ImpressFlow deck
    deck_url = create_impressflow_deck(final_report, client)

    # 3c. Slack
    post_to_slack(client, week, notion_url, deck_url)
    print(f"Done: {client['name']} — {week}")


def main():
    parser = argparse.ArgumentParser(description="Run weekly report for a client")
    parser.add_argument("--client", required=True, help="Path to client JSON config")
    parser.add_argument(
        "--week",
        default=datetime.now().strftime("%G-W%V"),
        help="ISO week, e.g. 2026-W10",
    )
    args = parser.parse_args()
    run_client(args.client, args.week)


if __name__ == "__main__":
    main()
