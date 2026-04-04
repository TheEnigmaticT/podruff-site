import json
import logging
import os
import requests
from pipeline.config import SLACK_BOT_TOKEN, SLACK_CHANNEL

logger = logging.getLogger(__name__)


def post_message(text: str, channel: str = "", thread_ts: str = "") -> dict:
    """Post a text message to Slack. Returns API response."""
    channel = channel or SLACK_CHANNEL
    payload = {"channel": channel, "text": text}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    resp = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"},
        json=payload,
    )
    resp.raise_for_status()
    return resp.json()


def post_review_card(topic_name: str, short_url: str, thumbnail_url: str, card_id: str, channel: str = "", thread_ts: str = "") -> dict:
    """Post a review card with approve/reject buttons."""
    channel = channel or SLACK_CHANNEL
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*New clip ready for review:* {topic_name}\n<{short_url}|Watch short> | <{thumbnail_url}|View thumbnail>"}},
        {"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "Approve"}, "style": "primary", "action_id": "approve_clip", "value": card_id},
            {"type": "button", "text": {"type": "plain_text", "text": "Reject"}, "style": "danger", "action_id": "reject_clip", "value": card_id},
        ]},
    ]
    payload = {"channel": channel, "text": f"Review: {topic_name}", "blocks": blocks}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    resp = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"},
        json=payload,
    )
    resp.raise_for_status()
    return resp.json()


def upload_file(file_path: str, channel: str = "", thread_ts: str = "") -> None:
    """Upload a file to Slack using the files.upload API."""
    channel = channel or SLACK_CHANNEL
    filename = os.path.basename(file_path)
    file_size = os.path.getsize(file_path)

    # Step 1: Get upload URL
    resp = requests.post(
        "https://slack.com/api/files.getUploadURLExternal",
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
        data={"filename": filename, "length": file_size},
    )
    resp.raise_for_status()
    data = resp.json()
    upload_url = data["upload_url"]
    file_id = data["file_id"]

    # Step 2: Upload file
    with open(file_path, "rb") as f:
        requests.post(upload_url, files={"file": f})

    # Step 3: Complete upload
    complete_payload = {
        "files": [{"id": file_id, "title": filename}],
        "channel_id": channel,
    }
    if thread_ts:
        complete_payload["thread_ts"] = thread_ts
    requests.post(
        "https://slack.com/api/files.completeUploadExternal",
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"},
        json=complete_payload,
    )
