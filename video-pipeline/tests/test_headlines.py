import json
from unittest.mock import patch, MagicMock
from pipeline.headlines import generate_headline

SAMPLE_TOPIC = {
    "topic": "Pricing Strategy",
    "segments": [
        {"start": 0.0, "end": 30.0, "text": "Today we're going to talk about pricing."},
        {"start": 30.0, "end": 60.0, "text": "Most founders underprice by 10x."},
    ],
}

MOCK_RESPONSE = json.dumps({
    "headline": "Why You're Leaving Money on the Table",
    "description": "Most early-stage founders dramatically underprice their product. Here's how to fix it.",
})


def test_generate_headline_returns_headline_and_description():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"response": MOCK_RESPONSE}

    with patch("pipeline.headlines.requests.post", return_value=mock_resp):
        result = generate_headline(SAMPLE_TOPIC)
        assert result["headline"] == "Why You're Leaving Money on the Table"
        assert "underprice" in result["description"]
