"""Tests for social_poller — the main polling loop and processing functions."""

import json
from unittest.mock import patch, MagicMock, call
from social_poller import process_approved, process_scheduled, process_confirmations, poll_cycle


def _make_row(overrides=None):
    row = {
        "id": "page_1",
        "Title": "Test Post",
        "Status": "Approved",
        "Post Text": "Hello world",
        "LinkedIn Text": "",
        "X Text": "",
        "Instagram Text": "",
        "TikTok Text": "",
        "Threads Text": "",
        "Platforms": ["linkedin"],
        "Publish Date": "2026-03-10T12:00:00.000Z",
        "Last Scheduled Date": "",
        "Clip URL": "",
        "Thumbnail URL": "",
        "External Post IDs": "",
        "Posting Errors": "",
    }
    if overrides:
        row.update(overrides)
    return row


def test_process_approved_schedules_post():
    row = _make_row()
    notion = MagicMock()
    late = MagicMock()
    late.schedule_post.return_value = "late_123"
    postbridge = MagicMock()
    client_cfg = {"platforms": {"linkedin": {"provider": "late", "account_id": "acc_li"}}}

    process_approved(row, client_cfg, notion, late, postbridge)

    late.schedule_post.assert_called_once_with(
        text="Hello world", account_id="acc_li",
        scheduled_at="2026-03-10T12:00:00.000Z", media_url=None,
        platform="linkedin",
    )
    notion.set_status.assert_called_once_with("page_1", "Scheduled")
    notion.set_last_scheduled_date.assert_called_once_with("page_1", "2026-03-10T12:00:00.000Z")


def test_process_approved_uses_postbridge_for_twitter():
    row = _make_row({"Platforms": ["twitter"], "X Text": "Short tweet"})
    notion = MagicMock()
    late = MagicMock()
    postbridge = MagicMock()
    postbridge.schedule_post.return_value = "pb_123"
    client_cfg = {"platforms": {"twitter": {"provider": "postbridge", "account_id": 48695}}}

    process_approved(row, client_cfg, notion, late, postbridge)

    postbridge.schedule_post.assert_called_once_with(
        text="Short tweet", account_id=48695,
        scheduled_at="2026-03-10T12:00:00.000Z", media_url=None,
    )
    late.schedule_post.assert_not_called()


def test_process_approved_validation_failure():
    row = _make_row({"Post Text": "", "Platforms": ["linkedin"]})
    notion = MagicMock()
    late = MagicMock()
    postbridge = MagicMock()
    client_cfg = {"platforms": {"linkedin": {"provider": "late", "account_id": "acc_li"}}}

    process_approved(row, client_cfg, notion, late, postbridge)

    notion.set_posting_error.assert_called_once()
    late.schedule_post.assert_not_called()


def test_process_approved_multi_platform():
    row = _make_row({"Platforms": ["linkedin", "twitter"], "X Text": "Tweet version"})
    notion = MagicMock()
    late = MagicMock()
    late.schedule_post.return_value = "late_456"
    postbridge = MagicMock()
    postbridge.schedule_post.return_value = "pb_789"
    client_cfg = {
        "platforms": {
            "linkedin": {"provider": "late", "account_id": "acc_li"},
            "twitter": {"provider": "postbridge", "account_id": 48695},
        },
    }

    process_approved(row, client_cfg, notion, late, postbridge)

    assert late.schedule_post.call_count == 1
    assert postbridge.schedule_post.call_count == 1
    ids_arg = notion.set_external_ids.call_args[0][1]
    assert "linkedin" in ids_arg
    assert "twitter" in ids_arg


def test_process_scheduled_date_change():
    row = _make_row({
        "Status": "Scheduled",
        "Publish Date": "2026-03-12T12:00:00.000Z",
        "Last Scheduled Date": "2026-03-10T12:00:00.000Z",
        "External Post IDs": '{"linkedin": "late_123"}',
    })
    notion = MagicMock()
    late = MagicMock()
    late.schedule_post.return_value = "late_new"
    postbridge = MagicMock()
    client_cfg = {"platforms": {"linkedin": {"provider": "late", "account_id": "acc_li"}}}

    process_scheduled(row, client_cfg, notion, late, postbridge)

    late.delete_post.assert_called_once_with("late_123")
    late.schedule_post.assert_called_once()
    notion.set_external_ids.assert_called()
    notion.set_last_scheduled_date.assert_called()


def test_process_scheduled_status_reverted():
    row = _make_row({
        "Status": "To Review",
        "External Post IDs": '{"linkedin": "late_123", "twitter": "pb_456"}',
    })
    notion = MagicMock()
    late = MagicMock()
    postbridge = MagicMock()
    client_cfg = {
        "platforms": {
            "linkedin": {"provider": "late", "account_id": "acc_li"},
            "twitter": {"provider": "postbridge", "account_id": 48695},
        },
    }

    process_scheduled(row, client_cfg, notion, late, postbridge)

    late.delete_post.assert_called_once_with("late_123")
    postbridge.delete_post.assert_called_once_with("pb_456")
    notion.clear_scheduling_fields.assert_called_once_with("page_1")


def test_process_confirmations_marks_published():
    row = _make_row({
        "Status": "Scheduled",
        "External Post IDs": '{"linkedin": "late_123"}',
        "Publish Date": "2026-03-08T12:00:00.000Z",
    })
    notion = MagicMock()
    late = MagicMock()
    late.get_post_status.return_value = "published"
    postbridge = MagicMock()
    client_cfg = {"platforms": {"linkedin": {"provider": "late", "account_id": "acc_li"}}}

    process_confirmations(row, client_cfg, notion, late, postbridge)

    notion.set_status.assert_called_once_with("page_1", "Published")


def test_poll_cycle_renders_sent_to_client_rows():
    """poll_cycle should render body preview for rows with Status='Sent to Client'."""
    config = {
        "clients": {
            "test": {
                "notion_database_id": "db-123",
                "platforms": {"linkedin": {"provider": "late", "account_id": "acc1"}},
            }
        }
    }

    # Build a page object that extract_row can parse
    sent_page = {"id": "page-sent", "properties": {
        "Title": {"title": [{"plain_text": "Test Post"}]},
        "Status": {"select": {"name": "Sent to Client"}},
        "LinkedIn Text": {"rich_text": [{"plain_text": "Post content"}]},
        "Platforms": {"multi_select": [{"name": "linkedin"}]},
        "Publish Date": {"date": {"start": "2026-04-01"}},
        "Last Rendered": {"date": None},
        "Last edited by": {"last_edited_by": {"id": "user-123"}},
        "Last edited time": {"last_edited_time": "2026-04-01T10:00:00Z"},
    }}

    mock_notion = MagicMock()
    # query_by_status: return sent_page for "Sent to Client", empty for others
    def query_side_effect(db, status):
        if status == "Sent to Client":
            return [sent_page]
        return []
    mock_notion.query_by_status.side_effect = query_side_effect
    mock_notion.query_with_external_ids.return_value = []

    # get_page returns a fresh row for the re-read
    fresh_row = {
        "id": "page-sent", "Title": "Test Post", "Status": "Sent to Client",
        "LinkedIn Text": "Post content", "Platforms": ["linkedin"],
        "Publish Date": "2026-04-01", "Post Text": "", "X Text": "",
        "Posting Errors": "", "Clip URL": "", "Thumbnail URL": "",
    }
    mock_notion.get_page.return_value = fresh_row

    with patch("social_poller.render_to_notion") as mock_render:
        poll_cycle(config, mock_notion, None, None)

    mock_render.assert_called_once()
    mock_notion.set_last_rendered.assert_called_once_with("page-sent")


def test_poll_cycle_skips_render_when_bot_edited():
    """poll_cycle should skip render if the last editor was the bot."""
    config = {
        "notion_bot_id": "bot-user-999",
        "clients": {
            "test": {
                "notion_database_id": "db-123",
                "platforms": {},
            }
        }
    }

    sent_page = {"id": "page-sent", "properties": {
        "Title": {"title": [{"plain_text": "Test"}]},
        "Status": {"select": {"name": "Sent to Client"}},
        "Platforms": {"multi_select": []},
        "Publish Date": {"date": None},
        "Last Rendered": {"date": {"start": "2026-04-01T09:00:00Z"}},
        "Last edited by": {"last_edited_by": {"id": "bot-user-999"}},
        "Last edited time": {"last_edited_time": "2026-04-01T09:01:00Z"},
    }}

    mock_notion = MagicMock()
    mock_notion.query_by_status.side_effect = lambda db, status: [sent_page] if status == "Sent to Client" else []
    mock_notion.query_with_external_ids.return_value = []

    with patch("social_poller.render_to_notion") as mock_render:
        poll_cycle(config, mock_notion, None, None)

    mock_render.assert_not_called()
