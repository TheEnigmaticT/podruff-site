"""Tests for pipeline.mlt — parse_kdenlive_mlt."""
import os
import textwrap
import pytest

from pipeline.mlt import parse_kdenlive_mlt, _parse_mlt_time


# ---------------------------------------------------------------------------
# _parse_mlt_time unit tests
# ---------------------------------------------------------------------------

class TestParseMltTime:
    def test_hh_mm_ss_mmm(self):
        """Standard HH:MM:SS.mmm format."""
        assert _parse_mlt_time("00:20:11.633") == pytest.approx(20 * 60 + 11.633, abs=1e-6)

    def test_hours(self):
        """Hours are correctly accounted for."""
        assert _parse_mlt_time("01:00:00.000") == pytest.approx(3600.0, abs=1e-6)

    def test_mm_ss(self):
        """MM:SS format (no hours component)."""
        assert _parse_mlt_time("05:30.500") == pytest.approx(5 * 60 + 30.5, abs=1e-6)

    def test_plain_seconds(self):
        """Plain float string falls back to float()."""
        assert _parse_mlt_time("45.2") == pytest.approx(45.2, abs=1e-6)

    def test_zero(self):
        assert _parse_mlt_time("00:00:00.000") == pytest.approx(0.0, abs=1e-6)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _write_kdenlive(tmp_path, xml_content: str) -> str:
    """Write XML content to a temp .kdenlive file and return the path."""
    path = str(tmp_path / "test.kdenlive")
    with open(path, "w") as f:
        f.write(xml_content)
    return path


# ---------------------------------------------------------------------------
# parse_kdenlive_mlt tests
# ---------------------------------------------------------------------------

BASIC_MLT_XML = textwrap.dedent("""\
    <?xml version='1.0' encoding='utf-8'?>
    <mlt version="7.37.0">
      <chain id="chain0" out="00:43:52.700">
        <property name="resource">/path/to/source.mp4</property>
        <property name="mlt_service">avformat-novalidate</property>
      </chain>
      <producer id="producer0">
        <property name="resource">black</property>
      </producer>
      <playlist id="playlist0">
        <entry in="00:20:11.633" out="00:20:16.833" producer="chain0"/>
        <entry in="00:20:08.033" out="00:20:11.633" producer="chain0"/>
        <entry in="00:20:37.533" out="00:20:55.100" producer="chain0"/>
      </playlist>
      <playlist id="playlist6">
        <entry in="00:20:11.633" out="00:20:16.833" producer="chain1"/>
      </playlist>
    </mlt>
""")


class TestParseKdenliveMltBasic:
    def test_returns_three_time_ranges(self, tmp_path):
        """Parses all three entries from playlist0 into (start, end) tuples."""
        path = _write_kdenlive(tmp_path, BASIC_MLT_XML)
        ranges = parse_kdenlive_mlt(path)
        assert len(ranges) == 3

    def test_first_entry_values(self, tmp_path):
        """First entry: in=00:20:11.633 out=00:20:16.833."""
        path = _write_kdenlive(tmp_path, BASIC_MLT_XML)
        ranges = parse_kdenlive_mlt(path)
        start, end = ranges[0]
        assert start == pytest.approx(20 * 60 + 11.633, abs=1e-3)
        assert end == pytest.approx(20 * 60 + 16.833, abs=1e-3)

    def test_second_entry_values(self, tmp_path):
        """Second entry: in=00:20:08.033 out=00:20:11.633."""
        path = _write_kdenlive(tmp_path, BASIC_MLT_XML)
        ranges = parse_kdenlive_mlt(path)
        start, end = ranges[1]
        assert start == pytest.approx(20 * 60 + 8.033, abs=1e-3)
        assert end == pytest.approx(20 * 60 + 11.633, abs=1e-3)

    def test_third_entry_values(self, tmp_path):
        """Third entry: in=00:20:37.533 out=00:20:55.100."""
        path = _write_kdenlive(tmp_path, BASIC_MLT_XML)
        ranges = parse_kdenlive_mlt(path)
        start, end = ranges[2]
        assert start == pytest.approx(20 * 60 + 37.533, abs=1e-3)
        assert end == pytest.approx(20 * 60 + 55.100, abs=1e-3)

    def test_return_type(self, tmp_path):
        """Each element is a (float, float) tuple."""
        path = _write_kdenlive(tmp_path, BASIC_MLT_XML)
        ranges = parse_kdenlive_mlt(path)
        for item in ranges:
            assert isinstance(item, tuple)
            assert len(item) == 2
            assert isinstance(item[0], float)
            assert isinstance(item[1], float)

    def test_skips_non_video_playlist(self, tmp_path):
        """playlist6 (referencing chain1, a non-video chain) is not returned."""
        path = _write_kdenlive(tmp_path, BASIC_MLT_XML)
        # playlist6 references chain1 which is not defined => non-video chain
        # so only playlist0's 3 entries are returned, not 4
        ranges = parse_kdenlive_mlt(path)
        assert len(ranges) == 3


ENTRIES_WITHOUT_PRODUCER_XML = textwrap.dedent("""\
    <?xml version='1.0' encoding='utf-8'?>
    <mlt version="7.37.0">
      <chain id="chain0" out="00:10:00.000">
        <property name="resource">/path/to/video.mp4</property>
      </chain>
      <playlist id="playlist0">
        <entry in="00:00:10.000" out="00:00:20.000" producer="chain0"/>
        <blank length="00:00:05.000"/>
        <entry in="00:00:25.000" out="00:00:35.000"/>
        <entry in="00:00:40.000" out="00:00:50.000" producer="chain0"/>
      </playlist>
    </mlt>
""")


class TestParseKdenliveMltSkipsEmptyEntries:
    def test_entry_without_producer_is_skipped(self, tmp_path):
        """<entry> tags with no producer= attribute are skipped."""
        path = _write_kdenlive(tmp_path, ENTRIES_WITHOUT_PRODUCER_XML)
        ranges = parse_kdenlive_mlt(path)
        # Only 2 valid entries (the entry with no producer is skipped)
        assert len(ranges) == 2

    def test_blank_elements_ignored(self, tmp_path):
        """<blank> elements are not counted as entries."""
        path = _write_kdenlive(tmp_path, ENTRIES_WITHOUT_PRODUCER_XML)
        ranges = parse_kdenlive_mlt(path)
        assert len(ranges) == 2

    def test_valid_entries_correct_values(self, tmp_path):
        """Correct values returned for the two valid entries."""
        path = _write_kdenlive(tmp_path, ENTRIES_WITHOUT_PRODUCER_XML)
        ranges = parse_kdenlive_mlt(path)
        assert ranges[0] == pytest.approx((10.0, 20.0), abs=1e-3)
        assert ranges[1] == pytest.approx((40.0, 50.0), abs=1e-3)


NON_VIDEO_PRODUCER_XML = textwrap.dedent("""\
    <?xml version='1.0' encoding='utf-8'?>
    <mlt version="7.37.0">
      <chain id="chain0" out="00:10:00.000">
        <property name="resource">/path/to/video.mp4</property>
      </chain>
      <producer id="producer0">
        <property name="resource">black</property>
      </producer>
      <playlist id="playlist0">
        <entry in="00:00:10.000" out="00:00:20.000" producer="chain0"/>
        <entry in="00:00:25.000" out="00:00:35.000" producer="producer0"/>
      </playlist>
    </mlt>
""")


class TestParseKdenliveMltSkipsNonVideoProducers:
    def test_black_producer_entries_skipped(self, tmp_path):
        """Entries referencing non-video producers (e.g. black) are skipped."""
        path = _write_kdenlive(tmp_path, NON_VIDEO_PRODUCER_XML)
        ranges = parse_kdenlive_mlt(path)
        # Only chain0 is a video chain; producer0 (black) is not
        assert len(ranges) == 1
        assert ranges[0] == pytest.approx((10.0, 20.0), abs=1e-3)


# ---------------------------------------------------------------------------
# Real file test
# ---------------------------------------------------------------------------

REAL_KDENLIVE_PATH = os.path.expanduser(
    "/Users/ct-mac-mini/Documents/editorial-brill/projects/"
    "agi-is-science-fiction-here-s-my-proof-short.kdenlive"
)


@pytest.mark.skipif(
    not os.path.exists(REAL_KDENLIVE_PATH),
    reason="Real .kdenlive file not present on this machine",
)
class TestParseKdenliveMltRealFile:
    def test_returns_three_ranges(self):
        """Real file has 3 <entry> tags in playlist0."""
        ranges = parse_kdenlive_mlt(REAL_KDENLIVE_PATH)
        assert len(ranges) == 3

    def test_first_entry_matches_file(self):
        """First entry: in=00:20:11.633 out=00:20:16.833."""
        ranges = parse_kdenlive_mlt(REAL_KDENLIVE_PATH)
        start, end = ranges[0]
        assert start == pytest.approx(20 * 60 + 11.633, abs=1e-3)
        assert end == pytest.approx(20 * 60 + 16.833, abs=1e-3)

    def test_second_entry_matches_file(self):
        """Second entry: in=00:20:08.033 out=00:20:11.633."""
        ranges = parse_kdenlive_mlt(REAL_KDENLIVE_PATH)
        start, end = ranges[1]
        assert start == pytest.approx(20 * 60 + 8.033, abs=1e-3)
        assert end == pytest.approx(20 * 60 + 11.633, abs=1e-3)

    def test_third_entry_matches_file(self):
        """Third entry: in=00:20:37.533 out=00:20:55.100."""
        ranges = parse_kdenlive_mlt(REAL_KDENLIVE_PATH)
        start, end = ranges[2]
        assert start == pytest.approx(20 * 60 + 37.533, abs=1e-3)
        assert end == pytest.approx(20 * 60 + 55.100, abs=1e-3)

    def test_start_less_than_end(self):
        """All ranges have start < end (sanity check)."""
        ranges = parse_kdenlive_mlt(REAL_KDENLIVE_PATH)
        for start, end in ranges:
            assert start < end

    def test_all_times_positive(self):
        """All timestamps are positive values."""
        ranges = parse_kdenlive_mlt(REAL_KDENLIVE_PATH)
        for start, end in ranges:
            assert start >= 0.0
            assert end > 0.0
