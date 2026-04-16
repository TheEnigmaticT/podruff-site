"""Tests for pipeline.cli helper functions."""
import os
import pytest

from pipeline.cli import _write_srt_from_transcript
from pipeline.edl import _srt_time


# ---------------------------------------------------------------------------
# _write_srt_from_transcript
# ---------------------------------------------------------------------------

class TestWriteSrtFromTranscript:
    def _srt_time(self, seconds):
        return _srt_time(seconds)

    def test_writes_srt_file(self, tmp_path):
        transcript = [
            {"start": 0.0, "end": 3.5, "text": "Hello world"},
            {"start": 3.5, "end": 7.2, "text": "This is a test"},
        ]
        srt_path = str(tmp_path / "out.srt")
        _write_srt_from_transcript(transcript, srt_path, self._srt_time)

        assert os.path.exists(srt_path)
        content = open(srt_path).read()
        assert "1\n" in content
        assert "2\n" in content
        assert "Hello world" in content
        assert "This is a test" in content

    def test_srt_contains_timestamps(self, tmp_path):
        transcript = [
            {"start": 0.0, "end": 2.5, "text": "Opening line"},
        ]
        srt_path = str(tmp_path / "out.srt")
        _write_srt_from_transcript(transcript, srt_path, self._srt_time)

        content = open(srt_path).read()
        # Timestamps are in HH:MM:SS,mmm --> HH:MM:SS,mmm format
        assert "-->" in content
        assert "00:00:00,000 --> 00:00:02,500" in content

    def test_skips_empty_text_segments(self, tmp_path):
        transcript = [
            {"start": 0.0, "end": 1.0, "text": "  "},  # whitespace only
            {"start": 1.0, "end": 3.0, "text": "Real text"},
        ]
        srt_path = str(tmp_path / "out.srt")
        _write_srt_from_transcript(transcript, srt_path, self._srt_time)

        content = open(srt_path).read()
        # Only one entry should be present (the non-empty one)
        assert "Real text" in content
        # Should not have a sequence number 2 for the empty segment
        lines = [l.strip() for l in content.split("\n") if l.strip()]
        seq_numbers = [l for l in lines if l.isdigit()]
        assert seq_numbers == ["1"]

    def test_empty_transcript_produces_empty_file(self, tmp_path):
        srt_path = str(tmp_path / "out.srt")
        _write_srt_from_transcript([], srt_path, self._srt_time)
        assert os.path.exists(srt_path)
        assert open(srt_path).read() == ""

    def test_timestamps_use_comma_decimal(self, tmp_path):
        """SRT standard uses comma as decimal separator (not period)."""
        transcript = [
            {"start": 61.123, "end": 122.456, "text": "Mid section"},
        ]
        srt_path = str(tmp_path / "out.srt")
        _write_srt_from_transcript(transcript, srt_path, self._srt_time)

        content = open(srt_path).read()
        # Should contain commas, not dots, in timestamps
        assert "01:01,123" in content or "00:01:01,123" in content
        assert "02:02,456" in content or "00:02:02,456" in content

    def test_strips_leading_trailing_whitespace_from_text(self, tmp_path):
        transcript = [
            {"start": 0.0, "end": 2.0, "text": "  padded text  "},
        ]
        srt_path = str(tmp_path / "out.srt")
        _write_srt_from_transcript(transcript, srt_path, self._srt_time)

        content = open(srt_path).read()
        assert "padded text" in content
        # Should not have leading/trailing spaces around the text line
        for line in content.split("\n"):
            if "padded text" in line:
                assert line == "padded text"
