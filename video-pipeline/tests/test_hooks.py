import json
from unittest.mock import patch, MagicMock
from pipeline.hooks import select_hook, HOOK_PROMPT

SAMPLE_TOPIC = {
    "topic": "Pricing Strategy",
    "start": 0.0,
    "end": 60.0,
    "segments": [
        {"start": 0.0, "end": 30.0, "text": "Today we're going to talk about pricing."},
        {"start": 30.0, "end": 60.0, "text": "Most founders underprice by 10x."},
    ],
}

MOCK_RESPONSE = json.dumps({
    "sentence": "Most founders underprice by 10x.",
    "start": 30.0,
    "end": 60.0,
})


def test_select_hook_returns_sentence_and_timestamps():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"response": MOCK_RESPONSE}

    with patch("pipeline.hooks.requests.post", return_value=mock_resp):
        hook = select_hook(SAMPLE_TOPIC)
        assert hook["sentence"] == "Most founders underprice by 10x."
        assert hook["start"] == 30.0
        assert hook["end"] == 60.0


def test_hook_prompt_searches_anywhere():
    """Verify the improved prompt instructs LLM to search the entire transcript."""
    assert "ANYWHERE" in HOOK_PROMPT
    assert "CUT" in HOOK_PROMPT
    assert "PLACED AT THE VERY BEGINNING" in HOOK_PROMPT
    assert "1-3 second" in HOOK_PROMPT
