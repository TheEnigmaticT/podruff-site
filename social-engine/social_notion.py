"""Notion API client for querying and updating social media content database rows."""

import json
import logging
import requests

log = logging.getLogger("social_poller")

NOTION_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

# Fields to extract from Notion pages by type
_RICH_TEXT_FIELDS = [
    "Post Text", "LinkedIn Text", "YouTube Text", "X Text",
    "Instagram Text", "TikTok Text", "Threads Text", "Facebook Text",
    "Reddit Text", "Bluesky Text", "Pinterest Text", "Google Business Text",
    "External Post IDs", "Posting Errors", "Description", "Headline",
    "Hook Sentence",
]

_URL_FIELDS = ["Clip URL", "Thumbnail URL", "Source Video", "Short URL"]


class NotionClient:
    """Wraps the Notion REST API for querying and updating database rows."""

    def __init__(self, api_key):
        self.api_key = api_key
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Notion-Version": NOTION_VERSION,
        }

    # ── Query helpers ────────────────────────────────────────────────────

    def _paginated_query(self, database_id, body):
        """Run a paginated POST /databases/{id}/query, return all pages."""
        url = f"{NOTION_BASE}/databases/{database_id}/query"
        all_results = []
        while True:
            resp = requests.post(url, headers=self.headers, data=json.dumps(body), timeout=30)
            resp.raise_for_status()
            data = resp.json()
            all_results.extend(data.get("results", []))
            if not data.get("has_more"):
                break
            body["start_cursor"] = data["next_cursor"]
        return all_results

    def create_page(self, database_id, properties):
        """POST /pages — create a new page in a Notion database."""
        url = f"{NOTION_BASE}/pages"
        body = {
            "parent": {"database_id": database_id},
            "properties": properties,
        }
        resp = requests.post(
            url, headers=self.headers,
            data=json.dumps(body),
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def query_by_title_and_type(self, database_id, title, post_type):
        """Query pages by exact Title and Post Type — used for idempotency checks."""
        body = {
            "filter": {
                "and": [
                    {"property": "Title", "title": {"equals": title}},
                    {"property": "Post Type", "select": {"equals": post_type}},
                ]
            }
        }
        return self._paginated_query(database_id, body)

    def query_by_status(self, database_id, status):
        """Query pages filtered by Status select field."""
        body = {
            "filter": {
                "property": "Status",
                "select": {"equals": status},
            }
        }
        return self._paginated_query(database_id, body)

    def query_with_external_ids(self, database_id):
        """Query pages where External Post IDs is not empty."""
        body = {
            "filter": {
                "property": "External Post IDs",
                "rich_text": {"is_not_empty": True},
            }
        }
        return self._paginated_query(database_id, body)

    # ── Update helpers ───────────────────────────────────────────────────

    def update_page(self, page_id, properties):
        """PATCH /pages/{page_id} with a properties dict."""
        url = f"{NOTION_BASE}/pages/{page_id}"
        resp = requests.patch(
            url, headers=self.headers,
            data=json.dumps({"properties": properties}),
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def set_status(self, page_id, status):
        """Set the Status select field."""
        self.update_page(page_id, {"Status": {"select": {"name": status}}})

    def set_external_ids(self, page_id, ids_dict):
        """Write a JSON string to the External Post IDs rich_text field."""
        self.update_page(page_id, {
            "External Post IDs": {
                "rich_text": [{"text": {"content": json.dumps(ids_dict)}}]
            }
        })

    def set_posting_error(self, page_id, error_msg):
        """Write error to Posting Errors and set Status to 'To Review'."""
        self.update_page(page_id, {
            "Posting Errors": {
                "rich_text": [{"text": {"content": error_msg}}]
            },
            "Status": {"select": {"name": "To Review"}},
        })

    def set_last_scheduled_date(self, page_id, date_str):
        """Write a date string to Last Scheduled Date."""
        self.update_page(page_id, {
            "Last Scheduled Date": {"date": {"start": date_str}}
        })

    def clear_scheduling_fields(self, page_id):
        """Clear External Post IDs, Last Scheduled Date, and Posting Errors."""
        self.update_page(page_id, {
            "External Post IDs": {"rich_text": []},
            "Last Scheduled Date": {"date": None},
            "Posting Errors": {"rich_text": []},
        })

    # ── Blocks API ───────────────────────────────────────────────────────

    def get_page_children(self, page_id):
        """GET /blocks/{page_id}/children — returns all child blocks."""
        url = f"{NOTION_BASE}/blocks/{page_id}/children?page_size=100"
        all_blocks = []
        while url:
            resp = requests.get(url, headers=self.headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            all_blocks.extend(data.get("results", []))
            if data.get("has_more"):
                url = f"{NOTION_BASE}/blocks/{page_id}/children?page_size=100&start_cursor={data['next_cursor']}"
            else:
                url = None
        return all_blocks

    def delete_block(self, block_id):
        """DELETE /blocks/{block_id}."""
        url = f"{NOTION_BASE}/blocks/{block_id}"
        resp = requests.delete(url, headers=self.headers, timeout=30)
        resp.raise_for_status()

    def append_blocks(self, page_id, children):
        """PATCH /blocks/{page_id}/children — append child blocks."""
        url = f"{NOTION_BASE}/blocks/{page_id}/children"
        resp = requests.patch(
            url, headers=self.headers,
            data=json.dumps({"children": children}),
            timeout=30,
        )
        resp.raise_for_status()

    def replace_page_body(self, page_id, new_blocks):
        """Delete all existing child blocks, then append new ones.

        Rate-limits deletions to stay under Notion's ~3 req/s limit.
        Batches appends into chunks of 100 (Notion API limit).
        """
        import time
        existing = self.get_page_children(page_id)
        for i, block in enumerate(existing):
            self.delete_block(block["id"])
            if (i + 1) % 3 == 0:
                time.sleep(1.1)
        if new_blocks:
            for i in range(0, len(new_blocks), 100):
                self.append_blocks(page_id, new_blocks[i:i+100])

    def set_last_rendered(self, page_id):
        """Set Last Rendered to current UTC time."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        self.update_page(page_id, {
            "Last Rendered": {"date": {"start": now}}
        })

    def get_page(self, page_id):
        """GET /pages/{page_id} and return extracted row dict."""
        url = f"{NOTION_BASE}/pages/{page_id}"
        resp = requests.get(url, headers=self.headers, timeout=30)
        resp.raise_for_status()
        return self.extract_row(resp.json())

    def get_parent_video_id(self, page_id):
        """Get the Parent Video relation page ID from a clip card."""
        url = f"{NOTION_BASE}/pages/{page_id}"
        resp = requests.get(url, headers=self.headers, timeout=30)
        resp.raise_for_status()
        props = resp.json().get("properties", {})
        relation = props.get("Parent Video", {}).get("relation", [])
        return relation[0]["id"] if relation else None

    # ── Extraction ───────────────────────────────────────────────────────

    @staticmethod
    def extract_row(page):
        """Extract a Notion page into a flat dict."""
        props = page.get("properties", {})
        row = {"id": page["id"]}

        # Title
        title_parts = props.get("Title", {}).get("title", [])
        row["Title"] = title_parts[0]["plain_text"] if title_parts else ""

        # Status
        select_val = props.get("Status", {}).get("select")
        row["Status"] = select_val["name"] if select_val else ""

        # Post Type (select)
        post_type_val = props.get("Post Type", {}).get("select")
        row["Post Type"] = post_type_val["name"] if post_type_val else ""

        # Generation Status (select)
        gen_status_val = props.get("Generation Status", {}).get("select")
        row["Generation Status"] = gen_status_val["name"] if gen_status_val else ""

        # Rich text fields
        for field in _RICH_TEXT_FIELDS:
            if field in row:
                continue
            parts = props.get(field, {}).get("rich_text", [])
            row[field] = parts[0]["plain_text"] if parts else ""

        # URL fields
        for field in _URL_FIELDS:
            row[field] = props.get(field, {}).get("url") or ""

        # Multi-select: Platforms
        row["Platforms"] = [
            p["name"] for p in props.get("Platforms", {}).get("multi_select", [])
        ]

        # Date fields
        for field in ("Publish Date", "Last Scheduled Date", "Last Rendered"):
            date_val = props.get(field, {}).get("date")
            row[field] = date_val["start"] if date_val else ""

        # Last edited by (ID string — used to detect bot vs human edits)
        edited_by = props.get("Last edited by", {}).get("last_edited_by", {})
        row["Last edited by"] = edited_by.get("id", "")

        # Last edited time
        row["Last edited time"] = props.get("Last edited time", {}).get("last_edited_time", "")

        return row
