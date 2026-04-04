from unittest.mock import patch, MagicMock, call
from pipeline.edl import resolve_segments, render_edl_version
import xml.etree.ElementTree as ET
from pipeline.edl import generate_kdenlive_xml


def test_resolve_segments_simple():
    """Body segments with no trims should pass through."""
    segments = [
        {"type": "hook", "start": 60.0, "end": 67.0, "narrative_bridge": "tension"},
        {"type": "body", "start": 30.0, "end": 60.0},
    ]
    resolved = resolve_segments(segments, trims=[])
    assert len(resolved) == 2
    assert resolved[0] == (60.0, 67.0)
    assert resolved[1] == (30.0, 60.0)


def test_resolve_segments_with_trim():
    """A trim inside a body segment should split it."""
    segments = [
        {"type": "body", "start": 30.0, "end": 60.0},
    ]
    trims = [{"start": 40.0, "end": 48.0, "reason": "filler"}]
    resolved = resolve_segments(segments, trims)
    assert len(resolved) == 2
    assert resolved[0] == (30.0, 40.0)
    assert resolved[1] == (48.0, 60.0)


def test_resolve_segments_trim_at_start():
    """Trim at the start of a segment."""
    segments = [{"type": "body", "start": 30.0, "end": 60.0}]
    trims = [{"start": 30.0, "end": 38.0, "reason": "intro"}]
    resolved = resolve_segments(segments, trims)
    assert len(resolved) == 1
    assert resolved[0] == (38.0, 60.0)


def test_resolve_segments_trim_at_end():
    """Trim at the end of a segment."""
    segments = [{"type": "body", "start": 30.0, "end": 60.0}]
    trims = [{"start": 52.0, "end": 60.0, "reason": "trailing"}]
    resolved = resolve_segments(segments, trims)
    assert len(resolved) == 1
    assert resolved[0] == (30.0, 52.0)


@patch("pipeline.edl.os.makedirs")
@patch("pipeline.edl.tempfile.mkdtemp", return_value="/tmp/edl_render_test")
@patch("builtins.open", new_callable=MagicMock)
@patch("pipeline.edl._probe_duration", return_value=7.0)
@patch("pipeline.edl._run_ffmpeg")
def test_render_edl_version_concatenates_segments(mock_ffmpeg, mock_probe, mock_open, mock_mkdtemp, mock_makedirs):
    edl_version = {
        "segments": [
            {"type": "hook", "start": 60.0, "end": 67.0, "narrative_bridge": "x"},
            {"type": "body", "start": 30.0, "end": 55.0},
        ],
        "trims": [],
        "target_duration": 55,
        "estimated_duration": 32.0,
    }
    durations = render_edl_version(
        edl_version,
        source_video="/path/video.mp4",
        output_path="/path/out.mp4",
        crop_mode="vertical",
    )
    assert mock_ffmpeg.called
    assert len(durations) == 2


def test_kdenlive_xml_structure():
    edl_version = {
        "segments": [
            {"type": "hook", "start": 60.0, "end": 67.0, "narrative_bridge": "tension"},
            {"type": "body", "start": 30.0, "end": 55.0},
        ],
        "trims": [],
        "estimated_duration": 32.0,
        "target_duration": 55,
    }
    xml_str = generate_kdenlive_xml(
        edl_version,
        source_video="/path/video.mp4",
        profile="vertical",
    )
    root = ET.fromstring(xml_str)
    assert root.tag == "mlt"

    # main_bin must be the root producer
    assert root.get("producer") == "main_bin"

    # Must have main_bin playlist
    playlist_ids = {pl.get("id") for pl in root.findall(".//playlist")}
    assert "main_bin" in playlist_ids

    # Bin chain must have kdenlive:control_uuid and kdenlive:id
    bin_chain = root.find(".//chain[@id='chain2']")
    assert bin_chain is not None
    prop_names = {p.get("name") for p in bin_chain.findall("property")}
    assert "kdenlive:control_uuid" in prop_names
    assert "kdenlive:id" in prop_names

    # Sequence tractor (tractor4) must exist with producer_type=17
    seq_tractor = root.find(".//tractor[@id='tractor4']")
    assert seq_tractor is not None
    seq_props = {p.get("name"): p.text for p in seq_tractor.findall("property")}
    assert seq_props.get("kdenlive:producer_type") == "17"
    assert "kdenlive:uuid" in seq_props
    assert "kdenlive:control_uuid" in seq_props

    # Wrapper tractor (tractor5) with projectTractor
    wrapper = root.find(".//tractor[@id='tractor5']")
    assert wrapper is not None
    wrapper_props = {p.get("name"): p.text for p in wrapper.findall("property")}
    assert wrapper_props.get("kdenlive:projectTractor") == "1"

    # Should have 4 track tractors (tractor0-3) for 2 audio + 2 video
    for tid in ["tractor0", "tractor1", "tractor2", "tractor3"]:
        assert root.find(f".//tractor[@id='{tid}']") is not None


def test_kdenlive_xml_vertical_profile():
    edl_version = {
        "segments": [{"type": "body", "start": 0.0, "end": 30.0}],
        "trims": [],
        "estimated_duration": 30.0,
        "target_duration": 55,
    }
    xml_str = generate_kdenlive_xml(edl_version, "/path/video.mp4", profile="vertical")
    root = ET.fromstring(xml_str)
    profile = root.find(".//profile")
    assert profile.get("width") == "1080"
    assert profile.get("height") == "1920"


def test_kdenlive_xml_horizontal_profile():
    edl_version = {
        "segments": [{"type": "body", "start": 0.0, "end": 120.0}],
        "trims": [],
        "estimated_duration": 120.0,
        "target_duration": None,
    }
    xml_str = generate_kdenlive_xml(edl_version, "/path/video.mp4", profile="horizontal")
    root = ET.fromstring(xml_str)
    profile = root.find(".//profile")
    assert profile.get("width") == "1920"
    assert profile.get("height") == "1080"
