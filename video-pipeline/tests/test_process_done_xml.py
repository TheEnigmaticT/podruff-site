"""Tests for _process_done_xml end-to-end flow."""
import json
import os
import pytest
from unittest.mock import MagicMock, patch, call

from pipeline.drive_poller import DoneXmlState


def _make_xml_info(
    client_slug="test-client",
    session_folder_id="sess-1",
    session_name="Great Episode",
    xml_id="xml-1",
    xml_name="my-clip.xml",
    source_video_id="vid-1",
    source_video_name="source.mp4",
):
    return {
        "client_slug": client_slug,
        "session_folder_id": session_folder_id,
        "session_name": session_name,
        "xml_file": {"id": xml_id, "name": xml_name},
        "source_video_file": {"id": source_video_id, "name": source_video_name},
    }


def _make_drive_mock(transcript_segments=None, include_transcript=True):
    """Build a DriveClient mock that returns a session file listing.

    Args:
        transcript_segments: List of segment dicts to return as the transcript.
            If None, uses a minimal single-segment transcript.
        include_transcript: If False, list_files returns no transcript.json
            (simulates sessions where the file wasn't uploaded yet).
    """
    mock_drive = MagicMock()
    if include_transcript:
        session_files = [{"id": "transcript-file-id", "name": "transcript.json"}]
        mock_drive.list_files.return_value = session_files
        if transcript_segments is None:
            transcript_segments = [{"start": 0.0, "end": 5.0, "text": "Hello world"}]
        mock_drive._transcript_segments = transcript_segments
    else:
        mock_drive.list_files.return_value = []
        mock_drive._transcript_segments = None
    return mock_drive


def _patch_open_for_transcript(transcript_segments):
    """Return a context manager that patches builtins.open so that opening a
    path ending in 'transcript.json' returns a StringIO with the JSON payload,
    while all other open() calls are passed through to the real implementation.

    Usage::
        with _patch_open_for_transcript(segments):
            _process_done_xml(...)
    """
    import io
    import builtins

    _real_open = builtins.open
    transcript_json_str = json.dumps(transcript_segments)

    def patched_open(path, *args, **kwargs):
        if str(path).endswith("transcript.json"):
            return io.StringIO(transcript_json_str)
        return _real_open(path, *args, **kwargs)

    return patch("builtins.open", side_effect=patched_open)


class TestProcessDoneXml:
    """End-to-end test: mock everything, verify the full flow executes."""

    def test_full_flow_happy_path(self, tmp_path):
        """process_done_xml downloads, renders, uploads, notifies, marks complete."""
        from pipeline.cli import _process_done_xml

        state_file = str(tmp_path / "done_state.json")
        state = DoneXmlState(path=state_file)

        xml_info = _make_xml_info()

        # Mock drive client — returns transcript.json in session file listing
        mock_drive = _make_drive_mock()

        # Mock soul — include website so end card CTA is generated
        mock_soul = {
            "slack_channel": "#test-internal",
            "subtitle_highlight": "#FF0000",
            "subtitle_font": "Helvetica",
            "raw": "**Website:** https://example.com",
            "brand_color": "#FF0000",
            "notion_db": "",
        }

        time_ranges = [(0.0, 10.0), (15.0, 25.0)]
        actual_durations = [10.0, 10.0]
        sub_path = str(tmp_path / "sub.ass")

        transcript_segments = mock_drive._transcript_segments

        with (
            patch("pipeline.cli.load_soul", return_value=mock_soul) as mock_load_soul,
            patch("pipeline.cli.parse_fcp7_xml", return_value=time_ranges),
            patch("pipeline.cli.render_edl_version", return_value=actual_durations) as mock_render_edl,
            patch("pipeline.cli.generate_clip_subtitles", return_value=sub_path) as mock_gen_subs,
            patch("pipeline.cli.get_subtitle_style", return_value={"highlight_color": "#FF0000", "font": "Helvetica", "font_size": 120}),
            patch("pipeline.cli.generate_end_card") as mock_end_card,
            patch("pipeline.cli.render_branded_short") as mock_branded,
            patch("pipeline.cli.r2_upload", return_value="https://r2.example.com/test-client/Great Episode/my-clip.mp4") as mock_r2,
            patch("pipeline.cli.post_message") as mock_post,
            patch("pipeline.cli._detect_face_center", return_value=(0.5, 0.3)),
            patch("pipeline.cli.WORK_DIR", str(tmp_path)),
            _patch_open_for_transcript(transcript_segments),  # intercept transcript.json read
            patch("os.path.exists", return_value=True),  # logo exists; makedirs not called (os.makedirs still real)
            patch("os.makedirs"),  # no-op to prevent real dir creation
            patch("shutil.copy2"),  # concat step no-op
        ):
            _process_done_xml(mock_drive, xml_info, state)

        # Verify drive downloads were called (XML + transcript.json + source video)
        assert mock_drive.download_file.call_count >= 2

        # Verify list_files was called to look up transcript.json
        mock_drive.list_files.assert_called_once_with("sess-1")

        # Verify render was called with the EDL-shaped version
        mock_render_edl.assert_called()
        render_call_args = mock_render_edl.call_args_list[0][0]
        edl_version = render_call_args[0]
        assert "segments" in edl_version
        assert len(edl_version["segments"]) == 2

        # Verify subtitle generation was called with the real transcript (not [])
        mock_gen_subs.assert_called_once()
        gen_subs_call = mock_gen_subs.call_args
        passed_transcript = gen_subs_call[0][1]
        assert len(passed_transcript) == 1  # one segment from _make_drive_mock default
        assert passed_transcript[0]["text"] == "Hello world"

        # Verify R2 upload was called
        mock_r2.assert_called_once()
        r2_key = mock_r2.call_args[0][1]
        assert r2_key.startswith("test-client/")
        assert r2_key.endswith(".mp4")

        # Verify Drive final upload
        mock_drive.find_or_create_folder.assert_called()
        mock_drive.upload_file.assert_called()

        # Verify Slack notification
        mock_post.assert_called_once()
        slack_args = mock_post.call_args
        assert "#test-internal" in str(slack_args)

        # Verify state marked complete
        assert state.is_processed("xml-1") is True

    def test_falls_back_to_empty_transcript_when_not_in_drive(self, tmp_path):
        """If transcript.json is missing from Drive, subtitles are generated with [] and a warning is logged."""
        from pipeline.cli import _process_done_xml
        import logging

        state = DoneXmlState(path=str(tmp_path / "state.json"))
        xml_info = _make_xml_info(xml_id="xml-no-transcript")

        # Drive has no transcript.json in the session folder
        mock_drive = _make_drive_mock(include_transcript=False)

        mock_soul = {
            "slack_channel": "",
            "subtitle_highlight": "",
            "subtitle_font": "",
            "raw": "",
            "brand_color": "#FFFFFF",
            "notion_db": "",
        }

        captured_gen_subs_calls = []

        def capture_gen_subs(edl_version, transcript, *args, **kwargs):
            captured_gen_subs_calls.append(transcript)

        with (
            patch("pipeline.cli.load_soul", return_value=mock_soul),
            patch("pipeline.cli.parse_fcp7_xml", return_value=[(0.0, 5.0)]),
            patch("pipeline.cli.render_edl_version", return_value=[5.0]),
            patch("pipeline.cli.generate_clip_subtitles", side_effect=capture_gen_subs),
            patch("pipeline.cli.get_subtitle_style", return_value={"highlight_color": "#FFFFFF", "font": "Inter", "font_size": 120}),
            patch("pipeline.cli.generate_end_card"),
            patch("pipeline.cli.render_branded_short"),
            patch("pipeline.cli.r2_upload", return_value="https://r2.example.com/x"),
            patch("pipeline.cli.post_message"),
            patch("pipeline.cli._detect_face_center", return_value=None),
            patch("pipeline.cli.WORK_DIR", str(tmp_path)),
            patch("os.makedirs"),
            patch("os.path.exists", return_value=False),
            patch("shutil.copy2"),
        ):
            # Should not raise even though transcript.json is missing from Drive
            _process_done_xml(mock_drive, xml_info, state)

        # generate_clip_subtitles was still called, but with an empty transcript
        assert len(captured_gen_subs_calls) == 1
        assert captured_gen_subs_calls[0] == []

    def test_skips_end_card_when_no_logo(self, tmp_path):
        """If no logo found, end card is skipped and branded render uses no end card."""
        from pipeline.cli import _process_done_xml

        state = DoneXmlState(path=str(tmp_path / "state.json"))
        xml_info = _make_xml_info(xml_id="xml-no-logo")

        mock_drive = _make_drive_mock()

        mock_soul = {
            "slack_channel": "",
            "subtitle_highlight": "",
            "subtitle_font": "",
            "raw": "",
            "brand_color": "#FFFFFF",
            "notion_db": "",
        }

        with (
            patch("pipeline.cli.load_soul", return_value=mock_soul),
            patch("pipeline.cli.parse_fcp7_xml", return_value=[(0.0, 5.0)]),
            patch("pipeline.cli.render_edl_version", return_value=[5.0]),
            patch("pipeline.cli.generate_clip_subtitles", return_value=str(tmp_path / "sub.ass")),
            patch("pipeline.cli.get_subtitle_style", return_value={"highlight_color": "#FFFFFF", "font": "Inter", "font_size": 120}),
            patch("pipeline.cli.generate_end_card") as mock_end_card,
            patch("pipeline.cli.render_branded_short") as mock_branded,
            patch("pipeline.cli.r2_upload", return_value="https://r2.example.com/x"),
            patch("pipeline.cli.post_message"),
            patch("pipeline.cli._detect_face_center", return_value=None),
            patch("pipeline.cli.WORK_DIR", str(tmp_path)),
            _patch_open_for_transcript(mock_drive._transcript_segments),
            patch("os.makedirs"),
            patch("os.path.exists", return_value=False),  # no logo file
            patch("shutil.copy2"),  # don't actually copy files
        ):
            _process_done_xml(mock_drive, xml_info, state)

        # End card should NOT be generated when logo is missing
        mock_end_card.assert_not_called()

    def test_marks_complete_even_when_slack_fails(self, tmp_path):
        """Slack failure is non-blocking — state is still marked complete."""
        from pipeline.cli import _process_done_xml

        state = DoneXmlState(path=str(tmp_path / "state.json"))
        xml_info = _make_xml_info(xml_id="xml-slack-fail")

        mock_drive = _make_drive_mock()

        mock_soul = {
            "slack_channel": "#channel",
            "subtitle_highlight": "",
            "subtitle_font": "",
            "raw": "",
            "brand_color": "#FFFFFF",
            "notion_db": "",
        }

        with (
            patch("pipeline.cli.load_soul", return_value=mock_soul),
            patch("pipeline.cli.parse_fcp7_xml", return_value=[(0.0, 5.0)]),
            patch("pipeline.cli.render_edl_version", return_value=[5.0]),
            patch("pipeline.cli.generate_clip_subtitles", return_value=str(tmp_path / "sub.ass")),
            patch("pipeline.cli.get_subtitle_style", return_value={"highlight_color": "#FFFFFF", "font": "Inter", "font_size": 120}),
            patch("pipeline.cli.generate_end_card"),
            patch("pipeline.cli.render_branded_short"),
            patch("pipeline.cli.r2_upload", return_value="https://r2.example.com/x"),
            patch("pipeline.cli.post_message", side_effect=Exception("Slack is down")),
            patch("pipeline.cli._detect_face_center", return_value=None),
            patch("pipeline.cli.WORK_DIR", str(tmp_path)),
            _patch_open_for_transcript(mock_drive._transcript_segments),
            patch("os.makedirs"),
            patch("os.path.exists", return_value=False),
            patch("shutil.copy2"),
        ):
            # Should not raise
            _process_done_xml(mock_drive, xml_info, state)

        assert state.is_processed("xml-slack-fail") is True

    def test_r2_key_format(self, tmp_path):
        """R2 key follows {client_slug}/{session_name}/{xml_basename}.mp4 format."""
        from pipeline.cli import _process_done_xml

        state = DoneXmlState(path=str(tmp_path / "state.json"))
        xml_info = _make_xml_info(
            client_slug="jonathan-brill",
            session_name="agi-is-science-fiction",
            xml_name="my-clip.xml",
            xml_id="xml-r2-key",
        )

        captured_r2_calls = []

        def capture_r2(local_path, remote_key):
            captured_r2_calls.append(remote_key)
            return f"https://r2.example.com/{remote_key}"

        mock_soul = {
            "slack_channel": "",
            "subtitle_highlight": "",
            "subtitle_font": "",
            "raw": "",
            "brand_color": "#FFFFFF",
            "notion_db": "",
        }

        mock_drive = _make_drive_mock()

        with (
            patch("pipeline.cli.load_soul", return_value=mock_soul),
            patch("pipeline.cli.parse_fcp7_xml", return_value=[(0.0, 5.0)]),
            patch("pipeline.cli.render_edl_version", return_value=[5.0]),
            patch("pipeline.cli.generate_clip_subtitles", return_value=str(tmp_path / "sub.ass")),
            patch("pipeline.cli.get_subtitle_style", return_value={"highlight_color": "#FFFFFF", "font": "Inter", "font_size": 120}),
            patch("pipeline.cli.generate_end_card"),
            patch("pipeline.cli.render_branded_short"),
            patch("pipeline.cli.r2_upload", side_effect=capture_r2),
            patch("pipeline.cli.post_message"),
            patch("pipeline.cli._detect_face_center", return_value=None),
            patch("pipeline.cli.WORK_DIR", str(tmp_path)),
            _patch_open_for_transcript(mock_drive._transcript_segments),
            patch("os.makedirs"),
            patch("os.path.exists", return_value=False),
            patch("shutil.copy2"),
        ):
            _process_done_xml(mock_drive, xml_info, state)

        assert len(captured_r2_calls) == 1
        key = captured_r2_calls[0]
        assert key == "jonathan-brill/agi-is-science-fiction/my-clip.mp4"
