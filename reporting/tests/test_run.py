import pytest
import json
from unittest.mock import patch, MagicMock
from run import get_week_dates, run_client

def test_get_week_dates_returns_monday_to_sunday():
    start, end = get_week_dates("2026-W10")
    assert start == "2026-03-02"
    assert end == "2026-03-08"

def test_run_client_halts_on_reconciliation_failure(tmp_path):
    config = tmp_path / "client.json"
    config.write_text(json.dumps({
        "name": "Test", "slug": "test", "credential": "analytics",
        "channels": ["ga4"], "ga4_property": "properties/123",
        "notion_page_id": "abc", "slack_channel": "#ct-reporting",
        "impressflow_theme": "Crowd Tamers", "active": True,
    }))
    from reconcile import ReconciliationError
    with patch("run.fetch_all", return_value={"ga4": {}}), \
         patch("run.analyze", return_value=("report A: 1000", "report B: 9999")), \
         patch("run.compare_reports", side_effect=ReconciliationError("mismatch deviation")), \
         patch("run.post_alert") as mock_alert, \
         patch("run.write_to_obsidian") as mock_obsidian, \
         patch("run.push_to_notion") as mock_notion, \
         patch("run.create_impressflow_deck") as mock_deck, \
         patch("run.post_to_slack") as mock_slack:
        run_client(str(config), "2026-W10")
    mock_alert.assert_called_once()
    mock_obsidian.assert_not_called()
    mock_notion.assert_not_called()
    mock_deck.assert_not_called()
    mock_slack.assert_not_called()
