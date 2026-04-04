from unittest.mock import patch, MagicMock
from pipeline.publisher import publish_clip


def test_publish_clip_sends_platform_objects_with_account_ids():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"id": "post-1", "status": "scheduled"}
    mock_resp.raise_for_status = MagicMock()

    with patch("pipeline.publisher.requests.post", return_value=mock_resp) as mock_post:
        result = publish_clip(
            video_url="https://r2.example.com/clip.mp4",
            thumbnail_url="https://r2.example.com/thumb.png",
            title="Test Title",
            description="Test description",
            platforms=["linkedin", "threads"],
            scheduled_for="2026-03-05T10:00:00Z",
        )
        assert result["id"] == "post-1"
        body = mock_post.call_args[1]["json"]
        # Platforms should be objects with platform + accountId
        assert isinstance(body["platforms"][0], dict)
        platform_names = [p["platform"] for p in body["platforms"]]
        assert "linkedin" in platform_names
        assert "threads" in platform_names
        assert all("accountId" in p for p in body["platforms"])


def test_publish_clip_immediate():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"id": "post-2", "status": "published"}
    mock_resp.raise_for_status = MagicMock()

    with patch("pipeline.publisher.requests.post", return_value=mock_resp):
        result = publish_clip(
            video_url="https://r2.example.com/clip.mp4",
            thumbnail_url="https://r2.example.com/thumb.png",
            title="Test Title",
            description="Test description",
            platforms=["linkedin"],
        )
        assert result["status"] == "published"


def test_publish_clip_with_custom_content():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"id": "post-3", "status": "published"}
    mock_resp.raise_for_status = MagicMock()

    with patch("pipeline.publisher.requests.post", return_value=mock_resp) as mock_post:
        publish_clip(
            video_url="https://r2.example.com/clip.mp4",
            thumbnail_url="",
            title="Test",
            description="Default text",
            platforms=["linkedin"],
            custom_content={"linkedin": "Custom LinkedIn text"},
        )
        body = mock_post.call_args[1]["json"]
        assert body["customContent"]["linkedin"]["content"] == "Custom LinkedIn text"
