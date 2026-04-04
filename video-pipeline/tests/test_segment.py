import json
from unittest.mock import patch, MagicMock
from pipeline.segment import segment_topics


SAMPLE_TRANSCRIPT = [
    {"start": 0.0, "end": 30.0, "text": "Today we're going to talk about pricing."},
    {"start": 30.0, "end": 60.0, "text": "Most founders underprice by 10x."},
    {"start": 60.0, "end": 90.0, "text": "Let me switch gears to hiring."},
    {"start": 90.0, "end": 120.0, "text": "Your first engineer matters more than anything."},
]

MOCK_LLM_RESPONSE = json.dumps([
    {"topic": "Pricing Strategy", "start": 0.0, "end": 60.0},
    {"topic": "Hiring Your First Engineer", "start": 60.0, "end": 120.0},
])


def test_segment_topics_returns_topic_boundaries():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"response": MOCK_LLM_RESPONSE}

    with patch("pipeline.segment.requests.post", return_value=mock_resp):
        topics = segment_topics(SAMPLE_TRANSCRIPT)
        assert len(topics) == 2
        assert topics[0]["topic"] == "Pricing Strategy"
        assert topics[0]["start"] == 0.0
        assert topics[0]["end"] == 60.0
        assert topics[1]["topic"] == "Hiring Your First Engineer"


def test_segment_topics_includes_transcript_text():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"response": MOCK_LLM_RESPONSE}

    with patch("pipeline.segment.requests.post", return_value=mock_resp):
        topics = segment_topics(SAMPLE_TRANSCRIPT)
        assert len(topics[0]["segments"]) == 2
        assert topics[0]["segments"][0]["text"] == "Today we're going to talk about pricing."
