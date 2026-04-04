import pytest
from unittest.mock import patch, MagicMock
from pipeline.transcribe import transcribe_video
import pipeline.transcribe as transcribe_module


@pytest.fixture(autouse=True)
def reset_model_cache():
    """Reset the cached model between tests."""
    transcribe_module._model = None
    yield
    transcribe_module._model = None


def _make_segment(start, end, text):
    seg = MagicMock()
    seg.start = start
    seg.end = end
    seg.text = text
    return seg


def test_transcribe_returns_timestamped_segments():
    mock_model = MagicMock()
    mock_model.transcribe.return_value = (
        [_make_segment(0.0, 5.0, "Hello world"), _make_segment(5.0, 10.0, "Second segment")],
        MagicMock(language="en", language_probability=0.99),
    )
    with patch("pipeline.transcribe.WhisperModel", return_value=mock_model):
        result = transcribe_video("/tmp/test.mp4")
        assert len(result) == 2
        assert result[0]["start"] == 0.0
        assert result[0]["end"] == 5.0
        assert result[0]["text"] == "Hello world"
        assert result[1]["text"] == "Second segment"


def test_transcribe_segments_have_required_keys():
    mock_model = MagicMock()
    mock_model.transcribe.return_value = (
        [_make_segment(0.0, 3.0, "Test")],
        MagicMock(language="en", language_probability=0.99),
    )
    with patch("pipeline.transcribe.WhisperModel", return_value=mock_model):
        result = transcribe_video("/tmp/test.mp4")
        assert all(k in result[0] for k in ["start", "end", "text"])
