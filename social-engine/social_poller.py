#!/usr/bin/env python3
"""Social media poller — polls Notion for approved posts and schedules them.

Ties together social_apis, social_config, and social_notion into a main
polling loop that processes approved posts, tracks scheduled posts, and
confirms publication status.
"""

import json
import logging
import os
import sys
import time

import requests

from social_apis import ZernioClient, PostBridgeClient
from social_config import load_config, resolve_text, validate_post
from social_body_renderer import render_to_notion
from social_notion import NotionClient

# ── Logging ──────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("social_poller")


# ── Helpers ──────────────────────────────────────────────────────────

def _get_media_url(row):
    """Prefer Clip URL over Thumbnail URL. Returns None if neither set."""
    clip = row.get("Clip URL", "")
    if clip:
        return clip
    thumb = row.get("Thumbnail URL", "")
    if thumb:
        return thumb
    return None


def _check_media_reachable(url):
    """HEAD-check a URL. Returns error string if unreachable, None if OK."""
    try:
        resp = requests.head(url, timeout=10, allow_redirects=True)
        if resp.status_code >= 400:
            return f"Media URL unreachable (HTTP {resp.status_code}): {url}"
    except Exception as exc:
        return f"Media URL unreachable ({exc}): {url}"
    return None


def _delete_external_posts(external_ids, client_platforms, late, postbridge):
    """Delete all external posts. Skip entries starting with 'ERROR'."""
    for platform, post_id in external_ids.items():
        if isinstance(post_id, str) and post_id.startswith("ERROR"):
            continue
        plat_cfg = client_platforms.get(platform, {})
        provider = plat_cfg.get("provider", "")
        try:
            if provider == "late":
                late.delete_post(post_id)
            elif provider == "postbridge":
                postbridge.delete_post(post_id)
            else:
                log.warning("Unknown provider %s for platform %s", provider, platform)
        except Exception:
            log.exception("Failed to delete %s post %s", platform, post_id)


# ── Core processing functions ────────────────────────────────────────

def process_approved(row, client_cfg, notion, late, postbridge):
    """Process a row with Status='Approved': validate and schedule."""
    client_platforms = client_cfg["platforms"]

    # 1. Validate
    errors = validate_post(row, client_platforms)
    if errors:
        notion.set_posting_error(row["id"], "; ".join(errors))
        return

    # 2. Check media reachability
    media_url = _get_media_url(row)
    if media_url:
        media_err = _check_media_reachable(media_url)
        if media_err:
            notion.set_posting_error(row["id"], media_err)
            return

    # 3. Schedule on each platform
    external_ids = {}
    for platform in row["Platforms"]:
        if platform not in client_platforms:
            continue
        plat_cfg = client_platforms[platform]
        provider = plat_cfg["provider"]
        account_id = plat_cfg["account_id"]
        text = resolve_text(row, platform)
        post_media = media_url  # same media for all platforms

        try:
            if provider == "late":
                post_id = late.schedule_post(
                    text=text, account_id=account_id,
                    scheduled_at=row["Publish Date"], media_url=post_media,
                    platform=platform,
                )
            elif provider == "postbridge":
                post_id = postbridge.schedule_post(
                    text=text, account_id=account_id,
                    scheduled_at=row["Publish Date"], media_url=post_media,
                )
            else:
                log.warning("Unknown provider %s for %s", provider, platform)
                continue
            external_ids[platform] = post_id
        except Exception:
            log.exception("Failed to schedule %s for %s", platform, row["Title"])
            external_ids[platform] = f"ERROR: scheduling failed"

    # 4. Update Notion
    successes = {k: v for k, v in external_ids.items() if not str(v).startswith("ERROR")}
    if successes:
        notion.set_external_ids(row["id"], external_ids)
        notion.set_last_scheduled_date(row["id"], row["Publish Date"])
        notion.set_status(row["id"], "Scheduled")
    else:
        notion.set_posting_error(row["id"], "All platforms failed to schedule")


def process_scheduled(row, client_cfg, notion, late, postbridge):
    """Process a row that has External Post IDs (detect reverts / date changes)."""
    client_platforms = client_cfg["platforms"]
    raw_ids = row.get("External Post IDs", "")
    if not raw_ids:
        return

    try:
        external_ids = json.loads(raw_ids)
    except json.JSONDecodeError:
        log.warning("Bad External Post IDs JSON for %s: %s", row["id"], raw_ids)
        return

    # Status reverted — delete all external posts and clear fields
    if row["Status"] != "Scheduled":
        _delete_external_posts(external_ids, client_platforms, late, postbridge)
        notion.clear_scheduling_fields(row["id"])
        return

    # Date changed — delete and re-schedule
    if row["Publish Date"] != row.get("Last Scheduled Date", ""):
        _delete_external_posts(external_ids, client_platforms, late, postbridge)
        process_approved(row, client_cfg, notion, late, postbridge)
        return


def process_confirmations(row, client_cfg, notion, late, postbridge):
    """Check publication status for scheduled posts with past publish dates."""
    client_platforms = client_cfg["platforms"]
    raw_ids = row.get("External Post IDs", "")
    if not raw_ids:
        return

    try:
        external_ids = json.loads(raw_ids)
    except json.JSONDecodeError:
        log.warning("Bad External Post IDs JSON for %s: %s", row["id"], raw_ids)
        return

    all_published = True
    for platform, post_id in external_ids.items():
        if isinstance(post_id, str) and post_id.startswith("ERROR"):
            continue

        plat_cfg = client_platforms.get(platform, {})
        provider = plat_cfg.get("provider", "")

        try:
            if provider == "late":
                status = late.get_post_status(post_id)
                if status == "published":
                    continue
                elif status in ("failed", "error"):
                    notion.set_posting_error(row["id"], f"{platform} post failed: {post_id}")
                    return
                else:
                    all_published = False
            elif provider == "postbridge":
                status = postbridge.get_post_status(post_id)
                if status == "posted":
                    continue
                elif status in ("failed", "error"):
                    notion.set_posting_error(row["id"], f"{platform} post failed: {post_id}")
                    return
                else:
                    all_published = False
        except Exception:
            log.exception("Failed to check status for %s post %s", platform, post_id)
            all_published = False

    if all_published:
        notion.set_status(row["id"], "Published")


# ── Poll cycle ───────────────────────────────────────────────────────

def poll_cycle(config, notion, late, postbridge):
    """Run one full poll cycle across all clients."""
    from datetime import datetime, timezone

    for client_name, client_cfg in config.get("clients", {}).items():
        db_id = client_cfg.get("notion_database_id", "")
        if not db_id:
            continue

        log.info("Polling client %s (db %s)", client_name, db_id)

        # 1. Process approved rows
        try:
            approved_pages = notion.query_by_status(db_id, "Approved")
            for page in approved_pages:
                row = NotionClient.extract_row(page)
                try:
                    process_approved(row, client_cfg, notion, late, postbridge)
                except Exception:
                    log.exception("Error processing approved row %s", row.get("id"))
        except Exception:
            log.exception("Error querying approved rows for %s", db_id)

        # 2. Process rows with external IDs (detect reverts/date changes)
        try:
            ext_pages = notion.query_with_external_ids(db_id)
            for page in ext_pages:
                row = NotionClient.extract_row(page)
                if row["Status"] == "Published":
                    continue
                try:
                    process_scheduled(row, client_cfg, notion, late, postbridge)
                except Exception:
                    log.exception("Error processing scheduled row %s", row.get("id"))
        except Exception:
            log.exception("Error querying external ID rows for %s", db_id)

        # 3. Process confirmations for scheduled rows with past publish date
        try:
            sched_pages = notion.query_by_status(db_id, "Scheduled")
            now = datetime.now(timezone.utc).isoformat()
            for page in sched_pages:
                row = NotionClient.extract_row(page)
                if row.get("Publish Date", "") and row["Publish Date"] < now:
                    try:
                        process_confirmations(row, client_cfg, notion, late, postbridge)
                    except Exception:
                        log.exception("Error confirming row %s", row.get("id"))
        except Exception:
            log.exception("Error querying scheduled rows for %s", db_id)

        # 4. Render body preview for "Sent to Client" rows
        # Uses "Last Rendered" date property to avoid re-render fights with humans.
        # Only renders if:
        #   a) Last Rendered is empty (never rendered), OR
        #   b) Metadata was edited after last render AND the editor was a human
        #      (not the bot itself — prevents infinite render loop)
        try:
            sent_pages = notion.query_by_status(db_id, "Sent to Client")
            for page in sent_pages:
                row = NotionClient.extract_row(page)
                try:
                    last_rendered = row.get("Last Rendered", "")
                    last_edited = row.get("Last edited time", "")
                    last_edited_by = row.get("Last edited by", "")

                    needs_render = False
                    if not last_rendered:
                        needs_render = True
                    elif last_edited and last_edited > last_rendered:
                        bot_id = config.get("notion_bot_id", "")
                        if bot_id and last_edited_by == bot_id:
                            needs_render = False
                        else:
                            needs_render = True

                    if needs_render:
                        fresh_row = notion.get_page(row["id"])
                        has_transcript = fresh_row.get("Posting Errors", "") != "GENERATED_WITHOUT_TRANSCRIPT"
                        render_to_notion(row["id"], fresh_row, notion, has_transcript=has_transcript)
                        notion.set_last_rendered(row["id"])
                        log.info("Rendered client preview for %s", row.get("Title"))
                except Exception:
                    log.exception("Error rendering preview for %s", row.get("id"))
        except Exception:
            log.exception("Error querying 'Sent to Client' rows for %s", db_id)


# ── Main ─────────────────────────────────────────────────────────────

def main():
    """Load config, create clients, and run the poll loop forever."""
    default_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "social_config.json")
    config_path = os.environ.get("SOCIAL_CONFIG_PATH", default_path)
    config = load_config(config_path)

    notion_key = config.get("notion_api_key") or os.environ.get("NOTION_API_KEY", "")
    if not notion_key:
        log.error("No Notion API key found in config or NOTION_API_KEY env var")
        sys.exit(1)

    late_key = config.get("late_api_key", "")
    postbridge_key = config.get("postbridge_api_key", "")

    notion = NotionClient(notion_key)
    late = ZernioClient(late_key) if late_key else None
    postbridge = PostBridgeClient(postbridge_key) if postbridge_key else None

    interval = config.get("polling_interval_seconds", 60)
    log.info("Social poller starting — interval %ds", interval)

    try:
        while True:
            try:
                poll_cycle(config, notion, late, postbridge)
            except Exception:
                log.exception("Error in poll cycle")
            time.sleep(interval)
    except KeyboardInterrupt:
        log.info("Shutting down")


if __name__ == "__main__":
    main()
