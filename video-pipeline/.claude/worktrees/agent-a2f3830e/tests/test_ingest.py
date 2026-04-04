import os
import pytest
from unittest.mock import patch, MagicMock
from pipeline.ingest import download_video, extract_video_info


def test_extract_video_info_returns_title_and_duration():
    with patch("pipeline.ingest.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"title": "Test Video", "duration": 300, "id": "abc123"}'
        )
        info = extract_video_info("https://youtube.com/watch?v=abc123")
        assert info["title"] == "Test Video"
        assert info["duration"] == 300
        assert info["id"] == "abc123"


def test_download_video_calls_ytdlp_with_correct_args():
    with patch("pipeline.ingest.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        with patch("os.listdir", return_value=["abc123.mp4"]):
            result = download_video("https://youtube.com/watch?v=abc123", "/tmp/out")
        args = mock_run.call_args[0][0]
        assert "yt-dlp" in args[0]
        assert "https://youtube.com/watch?v=abc123" in args


def test_download_video_raises_on_failure():
    with patch("pipeline.ingest.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stderr="Error")
        with pytest.raises(RuntimeError):
            download_video("https://youtube.com/watch?v=bad", "/tmp/out")
