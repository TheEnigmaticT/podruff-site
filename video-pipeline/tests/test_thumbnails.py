from unittest.mock import patch, MagicMock, mock_open
from pipeline.thumbnails import generate_thumbnail


def test_generate_thumbnail_uses_nano_banana_pro_with_frame():
    mock_client = MagicMock()

    # Simulate generate_content response with an image part
    mock_part = MagicMock()
    mock_part.inline_data.mime_type = "image/png"
    mock_part.inline_data.data = b"fake_image_data"

    mock_candidate = MagicMock()
    mock_candidate.content.parts = [mock_part]
    mock_response = MagicMock()
    mock_response.candidates = [mock_candidate]
    mock_client.models.generate_content.return_value = mock_response

    with patch("pipeline.thumbnails._get_client", return_value=mock_client):
        m = mock_open(read_data=b"fake_frame_bytes")
        with patch("builtins.open", m):
            generate_thumbnail(
                frame_path="/tmp/frame.png",
                headline="Why You're Leaving Money on the Table",
                output_path="/tmp/thumbnail.png",
            )

    # Verify it called generate_content (not generate_images)
    assert mock_client.models.generate_content.called
    assert not mock_client.models.generate_images.called

    # Verify model name
    call_kwargs = mock_client.models.generate_content.call_args
    assert call_kwargs[1]["model"] == "nano-banana-pro-preview"
