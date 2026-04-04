from unittest.mock import patch, MagicMock
from pipeline.storage import upload_file, get_public_url


def test_upload_file_calls_s3_upload():
    mock_client = MagicMock()
    with patch("pipeline.storage._get_client", return_value=mock_client):
        with patch("pipeline.storage.R2_PUBLIC_URL", "https://pub.r2.dev"):
            url = upload_file("/tmp/clip.mp4", "clips/topic-1.mp4")
            mock_client.upload_file.assert_called_once_with(
                "/tmp/clip.mp4", "video-pipeline", "clips/topic-1.mp4"
            )


def test_get_public_url_returns_expected_format():
    with patch("pipeline.storage.R2_PUBLIC_URL", "https://pub.r2.dev"):
        url = get_public_url("clips/topic-1.mp4")
        assert url == "https://pub.r2.dev/clips/topic-1.mp4"
