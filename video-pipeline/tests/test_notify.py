import json
from unittest.mock import patch, MagicMock
from pipeline.notify import post_message, post_review_card


def test_post_message_sends_to_slack():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"ok": True, "ts": "1234.5678"}

    with patch("pipeline.notify.requests.post", return_value=mock_resp) as mock_post:
        result = post_message("Hello", channel="C123")
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        payload = call_kwargs.kwargs["json"]
        assert payload["channel"] == "C123"
        assert payload["text"] == "Hello"
        assert "thread_ts" not in payload


def test_post_message_with_thread():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"ok": True}

    with patch("pipeline.notify.requests.post", return_value=mock_resp) as mock_post:
        post_message("Reply", channel="C123", thread_ts="111.222")
        payload = mock_post.call_args.kwargs["json"]
        assert payload["thread_ts"] == "111.222"


def test_post_review_card_has_buttons():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"ok": True}

    with patch("pipeline.notify.requests.post", return_value=mock_resp) as mock_post:
        post_review_card(
            topic_name="Pricing Strategy",
            short_url="https://r2.example.com/short.mp4",
            thumbnail_url="https://r2.example.com/thumb.png",
            card_id="card-123",
            channel="C123",
        )
        payload = mock_post.call_args.kwargs["json"]
        assert payload["channel"] == "C123"

        # Verify blocks contain approve/reject buttons
        blocks = payload["blocks"]
        assert len(blocks) == 2
        actions = blocks[1]
        assert actions["type"] == "actions"
        buttons = actions["elements"]
        assert len(buttons) == 2
        assert buttons[0]["action_id"] == "approve_clip"
        assert buttons[0]["value"] == "card-123"
        assert buttons[1]["action_id"] == "reject_clip"
        assert buttons[1]["value"] == "card-123"


def test_post_review_card_includes_links():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"ok": True}

    with patch("pipeline.notify.requests.post", return_value=mock_resp) as mock_post:
        post_review_card(
            topic_name="Test Topic",
            short_url="https://example.com/short.mp4",
            thumbnail_url="https://example.com/thumb.png",
            card_id="card-1",
            channel="C123",
        )
        blocks = mock_post.call_args.kwargs["json"]["blocks"]
        section_text = blocks[0]["text"]["text"]
        assert "Test Topic" in section_text
        assert "https://example.com/short.mp4" in section_text
        assert "https://example.com/thumb.png" in section_text
