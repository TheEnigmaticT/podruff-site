"""Tests for pipeline.branding — subtitle styling, end card, and branded render."""

import os
import tempfile
from unittest.mock import call, patch, MagicMock

import pytest

from pipeline.branding import get_subtitle_style, generate_end_card, render_branded_short


# ---------------------------------------------------------------------------
# get_subtitle_style
# ---------------------------------------------------------------------------

def test_get_subtitle_style_from_soul():
    soul = {
        "subtitle_highlight": "#FF6900",
        "subtitle_font": "Raleway",
    }
    style = get_subtitle_style(soul)
    assert style["highlight_color"] == "#FF6900"
    assert style["font"] == "Raleway"
    assert style["font_size"] == 120


def test_get_subtitle_style_defaults():
    style = get_subtitle_style({})
    assert style["highlight_color"] == "#1FC2F9"
    assert style["font"] == "Inter"
    assert style["font_size"] == 120


def test_get_subtitle_style_none_values_use_defaults():
    soul = {"subtitle_highlight": None, "subtitle_font": None}
    style = get_subtitle_style(soul)
    assert style["highlight_color"] == "#1FC2F9"
    assert style["font"] == "Inter"


# ---------------------------------------------------------------------------
# generate_end_card
# ---------------------------------------------------------------------------

def test_generate_end_card(tmp_path):
    """Integration test — requires ffmpeg at /opt/homebrew/bin/ffmpeg."""
    ffmpeg_path = "/opt/homebrew/bin/ffmpeg"
    if not os.path.exists(ffmpeg_path):
        pytest.skip("ffmpeg not found at /opt/homebrew/bin/ffmpeg")

    # Create a minimal 1x1 PNG as a stand-in logo
    logo_path = str(tmp_path / "logo.png")
    output_path = str(tmp_path / "end_card.mp4")

    # Generate a tiny white PNG using ffmpeg itself
    result = __import__("subprocess").run(
        [ffmpeg_path, "-y", "-f", "lavfi", "-i", "color=c=white:s=1x1:d=0.1",
         "-frames:v", "1", logo_path],
        capture_output=True,
    )
    if result.returncode != 0:
        pytest.skip("Could not create test logo PNG")

    generate_end_card(
        logo_path=logo_path,
        cta_text="Visit Test.com for more",
        output_path=output_path,
        duration=2,
        fps=30,
    )

    assert os.path.exists(output_path), "Output file was not created"
    assert os.path.getsize(output_path) > 0, "Output file is empty"


def test_generate_end_card_calls_ffmpeg_with_expected_args():
    with patch("pipeline.branding.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        generate_end_card(
            logo_path="/tmp/logo.png",
            cta_text="Visit Example.com",
            output_path="/tmp/end_card.mp4",
        )
        assert mock_run.called
        cmd = mock_run.call_args[0][0]
        cmd_str = " ".join(str(a) for a in cmd)
        assert "/opt/homebrew/bin/ffmpeg" in cmd_str
        assert "logo" in cmd_str
        assert "scale=800" in cmd_str
        assert "drawtext" in cmd_str
        assert "Visit Example.com" in cmd_str


def test_generate_end_card_raises_on_ffmpeg_failure():
    with patch("pipeline.branding.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=1,
            stderr="x" * 600,
        )
        with pytest.raises(RuntimeError) as exc_info:
            generate_end_card("/tmp/logo.png", "CTA", "/tmp/out.mp4")
        # Last 500 chars of stderr should be in the message
        assert "x" * 500 in str(exc_info.value)


# ---------------------------------------------------------------------------
# render_branded_short
# ---------------------------------------------------------------------------

def test_render_branded_short_calls_ffmpeg_for_each_stage():
    with patch("pipeline.branding.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        with patch("os.unlink"):
            render_branded_short(
                draft_video="/tmp/draft.mp4",
                subtitle_path="/tmp/subs.ass",
                end_card_path="/tmp/end_card.mp4",
                output_path="/tmp/final.mp4",
            )
        # At least 3 ffmpeg calls: burn subs, add silent audio, concat
        assert mock_run.call_count >= 3


def test_render_branded_short_detects_srt_extension():
    """SRT subtitle path should use subtitles= filter (not ass=)."""
    with patch("pipeline.branding.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        with patch("os.unlink"):
            render_branded_short(
                draft_video="/tmp/draft.mp4",
                subtitle_path="/tmp/subs.srt",
                end_card_path="/tmp/end_card.mp4",
                output_path="/tmp/final.mp4",
            )
        # First ffmpeg call should use subtitles= filter for SRT
        first_call_args = " ".join(str(a) for a in mock_run.call_args_list[0][0][0])
        assert "subtitles=" in first_call_args


def test_render_branded_short_detects_ass_extension():
    """ASS subtitle path should use ass= filter."""
    with patch("pipeline.branding.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        with patch("os.unlink"):
            render_branded_short(
                draft_video="/tmp/draft.mp4",
                subtitle_path="/tmp/subs.ass",
                end_card_path="/tmp/end_card.mp4",
                output_path="/tmp/final.mp4",
            )
        first_call_args = " ".join(str(a) for a in mock_run.call_args_list[0][0][0])
        assert "ass=" in first_call_args
