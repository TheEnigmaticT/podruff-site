from unittest.mock import patch, MagicMock
from pipeline.editor import extract_segment, extract_frame, prepend_hook, create_short


def test_extract_segment_calls_ffmpeg_with_timestamps():
    with patch("pipeline.editor.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        extract_segment("/tmp/input.mp4", 30.0, 90.0, "/tmp/segment.mp4")
        args = mock_run.call_args[0][0]
        assert "ffmpeg" in args[0]
        assert "-ss" in args


def test_extract_frame_produces_png():
    with patch("pipeline.editor.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        extract_frame("/tmp/input.mp4", 30.0, "/tmp/frame.png")
        args = mock_run.call_args[0][0]
        assert "-frames:v" in args


def test_prepend_hook_concats_two_clips():
    with patch("pipeline.editor.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        prepend_hook("/tmp/hook.mp4", "/tmp/segment.mp4", "/tmp/output.mp4")
        assert mock_run.called


def test_create_short_uses_9_16_crop():
    with patch("pipeline.editor.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        create_short("/tmp/input.mp4", "/tmp/short.mp4")
        args = " ".join(str(a) for a in mock_run.call_args[0][0])
        assert "crop=" in args
