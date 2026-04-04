import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
from deliver import write_to_obsidian, push_to_notion, create_impressflow_deck, post_to_slack

MOCK_CLIENT = {
    "name": "Test Client",
    "slug": "test-client",
    "notion_page_id": "abc-123",
    "slack_channel": "#ct-reporting",
    "impressflow_theme": "Crowd Tamers",
}

def test_write_to_obsidian_creates_file(tmp_path):
    report = "# Test Report\n\nSessions: 1,500."
    path = write_to_obsidian(report, MOCK_CLIENT, "2026-W10", vault_path=str(tmp_path))
    assert Path(path).exists()
    assert "2026-W10" in path
    assert "test-client" in path

def test_push_to_notion_calls_api():
    with patch("deliver.requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"url": "https://notion.so/abc"})
        result = push_to_notion("# Report", MOCK_CLIENT, "2026-W10", token="fake-token")
    mock_post.assert_called_once()
    assert isinstance(result, str)

def test_create_impressflow_deck_returns_url():
    with patch("deliver.requests.post") as mock_post:
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"url": "https://impressflow.com/deck/abc123"}
        )
        url = create_impressflow_deck("# Report", MOCK_CLIENT, api_key="fake", base_url="https://example.com")
    assert url == "https://impressflow.com/deck/abc123"

def test_post_to_slack_sends_message():
    with patch("deliver.requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        post_to_slack(
            client=MOCK_CLIENT,
            week="2026-W10",
            notion_url="https://notion.so/abc",
            deck_url="https://impressflow.com/deck/xyz",
            webhook_url="https://hooks.slack.com/fake"
        )
    mock_post.assert_called_once()
    call_kwargs = mock_post.call_args[1]
    body = str(call_kwargs.get("json", ""))
    assert "Test Client" in body
    assert "https://notion.so/abc" in body
