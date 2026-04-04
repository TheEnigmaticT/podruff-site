import os
import tempfile
from unittest.mock import patch, MagicMock
from pipeline.editor import (
    extract_segment, extract_frame, prepend_hook, create_short,
    _write_ass, _format_ass_time, get_clip_duration,
)


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


def test_create_short_uses_9_16_crop_with_face_detection():
    with patch("pipeline.editor._detect_face_center", return_value=(0.3, 0.5)):
        with patch("pipeline.editor.cv2") as mock_cv2:
            mock_cap = MagicMock()
            mock_cap.get.side_effect = lambda prop: {3: 1920, 4: 1080}.get(prop, 0)
            mock_cv2.VideoCapture.return_value = mock_cap
            mock_cv2.CAP_PROP_FRAME_WIDTH = 3
            mock_cv2.CAP_PROP_FRAME_HEIGHT = 4
            with patch("pipeline.editor.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                create_short("/tmp/input.mp4", "/tmp/short.mp4")
                args = " ".join(str(a) for a in mock_run.call_args[0][0])
                assert "crop=" in args
                assert "scale=1080:1920" in args


def test_create_short_fallback_center_crop():
    with patch("pipeline.editor._detect_face_center", return_value=None):
        with patch("pipeline.editor.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            create_short("/tmp/input.mp4", "/tmp/short.mp4")
            args = " ".join(str(a) for a in mock_run.call_args[0][0])
            assert "crop=ih*9/16:ih" in args
            assert "scale=1080:1920" in args


def test_format_ass_time():
    assert _format_ass_time(0.0) == "0:00:00.00"
    assert _format_ass_time(5.5) == "0:00:05.50"
    assert _format_ass_time(65.25) == "0:01:05.25"
    assert _format_ass_time(3661.0) == "1:01:01.00"


def test_write_ass_generates_valid_ass_with_offset():
    segments = [
        {"start": 100.0, "end": 103.0, "text": "Hello world test"},
        {"start": 103.0, "end": 106.0, "text": "Second line here"},
    ]
    # topic_start=90, hook_duration=5 → offset = -90 + 5 = -85
    offset = -85.0

    with tempfile.NamedTemporaryFile(suffix=".ass", delete=False, mode="w") as f:
        ass_path = f.name

    try:
        _write_ass(segments, offset, ass_path)
        with open(ass_path) as f:
            content = f.read()

        # Check ASS structure
        assert "[Script Info]" in content
        assert "PlayResX: 1080" in content
        assert "PlayResY: 1920" in content
        assert "Raleway" in content
        assert "MarginV" in content and "320" in content
        assert "BorderStyle" in content and "3" in content

        # Check dialogue lines exist with karaoke tags
        assert "Dialogue:" in content
        assert "\\k" in content

        # First segment: start=100-85=15.0, end=103-85=18.0
        assert "0:00:15.00" in content
        assert "0:00:18.00" in content

        # Second segment: start=103-85=18.0, end=106-85=21.0
        assert "0:00:21.00" in content
    finally:
        os.unlink(ass_path)


def test_write_ass_skips_segments_before_zero():
    segments = [
        {"start": 2.0, "end": 5.0, "text": "Too early"},
        {"start": 10.0, "end": 13.0, "text": "Visible words"},
    ]
    offset = -8.0  # first segment goes to -6, should be skipped

    with tempfile.NamedTemporaryFile(suffix=".ass", delete=False, mode="w") as f:
        ass_path = f.name

    try:
        _write_ass(segments, offset, ass_path)
        with open(ass_path) as f:
            content = f.read()

        assert "Too early" not in content
        assert "Visible" in content
    finally:
        os.unlink(ass_path)


def test_create_short_with_segments_appends_ass_filter():
    segments = [
        {"start": 10.0, "end": 13.0, "text": "Hello world"},
    ]
    with patch("pipeline.editor._detect_face_center", return_value=None):
        with patch("pipeline.editor.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            create_short(
                "/tmp/input.mp4", "/tmp/short.mp4",
                segments=segments, topic_start=5.0, hook_duration=3.0,
            )
            args = " ".join(str(a) for a in mock_run.call_args[0][0])
            assert "ass=" in args
            assert "scale=1080:1920" in args


def test_create_short_without_segments_no_ass_filter():
    with patch("pipeline.editor._detect_face_center", return_value=None):
        with patch("pipeline.editor.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            create_short("/tmp/input.mp4", "/tmp/short.mp4")
            args = " ".join(str(a) for a in mock_run.call_args[0][0])
            assert "ass=" not in args


def test_get_clip_duration():
    with patch("pipeline.editor.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="12.345\n", stderr="")
        duration = get_clip_duration("/tmp/clip.mp4")
        assert duration == 12.345
        args = mock_run.call_args[0][0]
        assert "ffprobe" in args[0]
