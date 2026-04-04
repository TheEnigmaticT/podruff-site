import pytest
from unittest.mock import patch, MagicMock
from collect import fetch_ga4, fetch_search_console, fetch_youtube, fetch_google_ads, fetch_all

MOCK_CLIENT = {
    "name": "Test Client",
    "slug": "test-client",
    "credential": "analytics",
    "channels": ["ga4"],
    "ga4_property": "properties/123456",
    "gsc_url": "https://test.com/",
    "yt_channel_id": "UCtest",
    "ads_customer_id": "123-456-7890",
}

def test_fetch_all_only_fetches_configured_channels():
    mock_creds = MagicMock()
    with patch("collect.get_credentials", return_value=mock_creds), \
         patch("collect.fetch_ga4", return_value={"rows": []}) as mock_ga4, \
         patch("collect.fetch_search_console") as mock_gsc, \
         patch("collect.fetch_youtube") as mock_yt, \
         patch("collect.fetch_google_ads") as mock_ads:
        result = fetch_all(MOCK_CLIENT, "2026-02-23", "2026-03-01")
    mock_ga4.assert_called_once()
    mock_gsc.assert_not_called()
    mock_yt.assert_not_called()
    mock_ads.assert_not_called()
    assert "ga4" in result

def test_fetch_all_returns_dict_keyed_by_channel():
    mock_creds = MagicMock()
    client = {**MOCK_CLIENT, "channels": ["ga4", "search_console"]}
    with patch("collect.get_credentials", return_value=mock_creds), \
         patch("collect.fetch_ga4", return_value={"rows": []}), \
         patch("collect.fetch_search_console", return_value={"rows": []}):
        result = fetch_all(client, "2026-02-23", "2026-03-01")
    assert set(result.keys()) == {"ga4", "search_console"}
