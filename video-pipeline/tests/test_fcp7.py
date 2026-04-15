"""Tests for FCP 7 XML generation and parsing."""

import xml.etree.ElementTree as ET
import tempfile
import os
import pytest

from pipeline.fcp7 import generate_fcp7_xml, parse_fcp7_xml


def _make_edl_version(segments, trims=None):
    return {
        "segments": segments,
        "trims": trims or [],
        "estimated_duration": sum(s["end"] - s["start"] for s in segments),
        "target_duration": 55,
    }


# ---------------------------------------------------------------------------
# test_generate_basic_fcp7
# ---------------------------------------------------------------------------

def test_generate_basic_fcp7():
    """2-segment EDL produces valid FCP 7 XML with correct structure."""
    edl_version = _make_edl_version([
        {"type": "hook", "start": 10.0, "end": 20.0},
        {"type": "body", "start": 30.0, "end": 50.0},
    ])

    xml_str = generate_fcp7_xml(edl_version, source_video="/path/to/video.mp4")

    # Must parse without errors
    root = ET.fromstring(xml_str)

    # Root must be <xmeml version="5">
    assert root.tag == "xmeml"
    assert root.get("version") == "5"

    # Must contain a <sequence>
    seq = root.find("sequence")
    assert seq is not None

    # Must have a <name>
    assert seq.find("name") is not None

    # Must have <rate><timebase>
    rate = seq.find("rate")
    assert rate is not None
    assert rate.find("timebase") is not None
    assert rate.find("timebase").text == "30"
    assert rate.find("ntsc").text == "FALSE"

    # Total duration = (20-10) + (50-30) = 30 seconds = 30*30 = 900 frames
    duration_el = seq.find("duration")
    assert duration_el is not None
    assert int(duration_el.text) == 900

    # Media section
    media = seq.find("media")
    assert media is not None

    video = media.find("video")
    assert video is not None

    track = video.find("track")
    assert track is not None

    clipitems = track.findall("clipitem")
    assert len(clipitems) == 2

    # First clip: source in=300 (10s*30), out=600 (20s*30)
    first = clipitems[0]
    assert int(first.find("in").text) == 300
    assert int(first.find("out").text) == 600

    # First clip timeline start=0, end=300 (10s duration)
    assert int(first.find("start").text) == 0
    assert int(first.find("end").text) == 300

    # Second clip: source in=900 (30s*30), out=1500 (50s*30)
    second = clipitems[1]
    assert int(second.find("in").text) == 900
    assert int(second.find("out").text) == 1500

    # Second clip timeline start=300, end=900 (cursor advanced by 300)
    assert int(second.find("start").text) == 300
    assert int(second.find("end").text) == 900

    # Each clipitem must have a <file> with <pathurl>
    for item in clipitems:
        file_el = item.find("file")
        assert file_el is not None
        assert file_el.find("pathurl") is not None
        assert file_el.find("pathurl").text == "/path/to/video.mp4"

    # XML declaration must be present
    assert xml_str.startswith("<?xml")


# ---------------------------------------------------------------------------
# test_fcp7_has_audio_track
# ---------------------------------------------------------------------------

def test_fcp7_has_audio_track():
    """Output must include both video and audio tracks."""
    edl_version = _make_edl_version([
        {"type": "body", "start": 0.0, "end": 15.0},
    ])

    xml_str = generate_fcp7_xml(edl_version, source_video="/path/to/video.mp4")
    root = ET.fromstring(xml_str)

    media = root.find("sequence/media")
    assert media is not None

    video = media.find("video")
    audio = media.find("audio")

    assert video is not None, "Must have <video> element"
    assert audio is not None, "Must have <audio> element"

    # Both tracks must have the same number of clipitems
    video_items = video.find("track").findall("clipitem")
    audio_items = audio.find("track").findall("clipitem")
    assert len(video_items) == len(audio_items)
    assert len(video_items) == 1

    # Audio clip item mirrors video: same in/out/start/end
    v = video_items[0]
    a = audio_items[0]
    for tag in ("in", "out", "start", "end"):
        assert v.find(tag).text == a.find(tag).text, f"Mismatch in <{tag}>"


# ---------------------------------------------------------------------------
# test_parse_fcp7_roundtrip
# ---------------------------------------------------------------------------

def test_parse_fcp7_roundtrip():
    """Generate then parse recovers the same time ranges."""
    segments = [
        {"type": "hook", "start": 5.0, "end": 12.0},
        {"type": "body", "start": 20.0, "end": 45.0},
    ]
    edl_version = _make_edl_version(segments)

    xml_str = generate_fcp7_xml(edl_version, source_video="/path/to/video.mp4", fps=30)

    # Write to temp file and parse
    with tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False) as f:
        f.write(xml_str)
        tmp_path = f.name

    try:
        recovered = parse_fcp7_xml(tmp_path)
    finally:
        os.unlink(tmp_path)

    # Should recover 2 ranges
    assert len(recovered) == 2

    # Segment 1: (5.0, 12.0)
    assert abs(recovered[0][0] - 5.0) < 0.01
    assert abs(recovered[0][1] - 12.0) < 0.01

    # Segment 2: (20.0, 45.0)
    assert abs(recovered[1][0] - 20.0) < 0.01
    assert abs(recovered[1][1] - 45.0) < 0.01


# ---------------------------------------------------------------------------
# Additional edge cases
# ---------------------------------------------------------------------------

def test_generate_fcp7_custom_fps():
    """Custom fps parameter propagates to timebase and frame math."""
    edl_version = _make_edl_version([
        {"type": "body", "start": 0.0, "end": 10.0},
    ])
    xml_str = generate_fcp7_xml(edl_version, source_video="/path/video.mp4", fps=25)
    root = ET.fromstring(xml_str)

    timebase = root.find("sequence/rate/timebase")
    assert timebase.text == "25"

    # duration = 10s * 25fps = 250 frames
    duration_el = root.find("sequence/duration")
    assert int(duration_el.text) == 250

    # clip out = 10s * 25fps = 250 frames
    clip = root.find("sequence/media/video/track/clipitem")
    assert int(clip.find("out").text) == 250


def test_generate_fcp7_sequence_name():
    """Custom sequence_name is reflected in <name>."""
    edl_version = _make_edl_version([{"type": "body", "start": 0.0, "end": 5.0}])
    xml_str = generate_fcp7_xml(
        edl_version, source_video="/path/video.mp4", sequence_name="My Clip"
    )
    root = ET.fromstring(xml_str)
    name_el = root.find("sequence/name")
    assert name_el.text == "My Clip"
