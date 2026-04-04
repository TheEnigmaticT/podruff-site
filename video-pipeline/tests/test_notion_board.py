from unittest.mock import patch, MagicMock
from pipeline.notion_board import (
    get_ingest_cards,
    create_clip_card,
    update_card_status,
    get_scheduled_cards,
)


def test_get_ingest_cards_filters_by_status():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"results": [{"id": "card-1"}]}
    mock_resp.raise_for_status = MagicMock()

    with patch("pipeline.notion_board.requests.post", return_value=mock_resp) as mock_post:
        cards = get_ingest_cards()
        body = mock_post.call_args[1]["json"]
        assert body["filter"]["property"] == "Status"
        assert body["filter"]["select"]["equals"] == "Ingest"
        assert len(cards) == 1


def test_create_clip_card_includes_required_properties():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"id": "new-card"}
    mock_resp.raise_for_status = MagicMock()

    with patch("pipeline.notion_board.requests.post", return_value=mock_resp) as mock_post:
        card_id = create_clip_card(
            headline="Test Headline",
            topic_name="Test Topic",
            hook_sentence="Best hook ever.",
            clip_url="https://r2.example.com/clip.mp4",
            short_url="https://r2.example.com/short.mp4",
            thumbnail_url="https://r2.example.com/thumb.png",
            description="A test description.",
            duration="4:32",
            parent_card_id="parent-1",
        )
        assert card_id == "new-card"
        body = mock_post.call_args[1]["json"]
        assert "properties" in body


def test_update_card_status():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()

    with patch("pipeline.notion_board.requests.patch", return_value=mock_resp) as mock_patch:
        update_card_status("card-1", "Published")
        body = mock_patch.call_args[1]["json"]
        assert body["properties"]["Status"]["select"]["name"] == "Published"


def test_get_scheduled_cards_filters_by_on_or_before():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"results": []}
    mock_resp.raise_for_status = MagicMock()

    with patch("pipeline.notion_board.requests.post", return_value=mock_resp) as mock_post:
        cards = get_scheduled_cards("2026-03-05T10:00:00+00:00")
        body = mock_post.call_args[1]["json"]
        filters = body["filter"]["and"]
        assert any("Scheduled" in str(f) for f in filters)
        assert any("on_or_before" in str(f) for f in filters)
