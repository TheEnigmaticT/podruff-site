"""EDL rendering: convert edit decision lists to FFmpeg commands, Kdenlive XML, and FCP 7 XML."""

import json
import logging
import os
import subprocess
import tempfile
import urllib.parse
import uuid

logger = logging.getLogger(__name__)

FFMPEG = "/opt/homebrew/bin/ffmpeg"
FFPROBE = "/opt/homebrew/bin/ffprobe"


def _probe_duration(path: str) -> float:
    """Get actual duration of a media file via ffprobe."""
    result = subprocess.run(
        [FFPROBE, "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", path],
        capture_output=True, text=True,
    )
    return float(result.stdout.strip())


def _run_ffmpeg(args: list[str]) -> None:
    """Run ffmpeg with error handling."""
    cmd = [FFMPEG, "-y"] + args
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr[-500:]}")


def resolve_segments(segments: list[dict], trims: list[dict]) -> list[tuple[float, float]]:
    """Resolve EDL segments and trims into a flat list of (start, end) time ranges.

    Trims are subtractive: if a trim falls within a segment, that segment is
    split into sub-segments around the trim.
    """
    raw_ranges = [(seg["start"], seg["end"]) for seg in segments]

    if not trims:
        return raw_ranges

    for trim in trims:
        t_start, t_end = trim["start"], trim["end"]
        new_ranges = []
        for r_start, r_end in raw_ranges:
            if t_start >= r_end or t_end <= r_start:
                new_ranges.append((r_start, r_end))
            else:
                if r_start < t_start:
                    new_ranges.append((r_start, t_start))
                if t_end < r_end:
                    new_ranges.append((t_end, r_end))
        raw_ranges = new_ranges

    return [(s, e) for s, e in raw_ranges if e - s > 0.1]


def render_edl_version(
    edl_version: dict,
    source_video: str,
    output_path: str,
    crop_mode: str = "vertical",
    face_pos: tuple[float, float] | None = None,
    subtitle_path: str | None = None,
) -> list[float]:
    """Render one version (short or long) of an EDL to a video file.

    Args:
        edl_version: EDL version dict with segments, trims, etc.
        source_video: Path to source video.
        output_path: Path for output video.
        crop_mode: "vertical" (9:16) or "horizontal" (keep original).
        face_pos: Optional (x, y) face position for vertical crop (0.0-1.0).
        subtitle_path: Optional path to ASS/SRT subtitle file to burn in.

    Returns:
        List of actual segment durations (from ffprobe), for subtitle sync.
    """
    time_ranges = resolve_segments(edl_version["segments"], edl_version.get("trims", []))

    if not time_ranges:
        logger.warning("No segments to render for %s", output_path)
        return []

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    tmp_dir = tempfile.mkdtemp(prefix="edl_render_")
    segment_files = []
    actual_durations = []

    try:
        for i, (start, end) in enumerate(time_ranges):
            seg_path = os.path.join(tmp_dir, f"seg_{i:03d}.mp4")
            _run_ffmpeg([
                "-ss", str(start),
                "-to", str(end),
                "-i", source_video,
                "-c", "copy",
                seg_path,
            ])
            segment_files.append(seg_path)
            actual_durations.append(_probe_duration(seg_path))

        concat_path = os.path.join(tmp_dir, "concat.txt")
        with open(concat_path, "w") as f:
            for seg_path in segment_files:
                f.write(f"file '{seg_path}'\n")

        vf_filters = []

        if crop_mode == "vertical":
            if face_pos:
                import cv2
                cap = cv2.VideoCapture(segment_files[0])
                vid_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                vid_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                cap.release()
                crop_w = int(vid_h * 9 / 16)
                face_x_px = int(face_pos[0] * vid_w)
                crop_x = max(0, min(face_x_px - crop_w // 2, vid_w - crop_w))
                vf_filters.append(f"crop={crop_w}:{vid_h}:{crop_x}:0")
            else:
                vf_filters.append("crop=ih*9/16:ih")
            vf_filters.append("scale=1080:1920")

        if subtitle_path:
            escaped = subtitle_path.replace("\\", "\\\\").replace(":", "\\:")
            if subtitle_path.endswith(".ass"):
                vf_filters.append(f"ass={escaped}")
            else:
                vf_filters.append(f"subtitles={escaped}")

        filter_args = []
        if vf_filters:
            filter_args = ["-vf", ",".join(vf_filters)]

        _run_ffmpeg([
            "-f", "concat", "-safe", "0", "-i", concat_path,
            *filter_args,
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-r", "30", "-vsync", "cfr",
            "-c:a", "aac", "-b:a", "192k",
            output_path,
        ])

        return actual_durations

    finally:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)


def generate_clip_subtitles(
    edl_version: dict,
    transcript: list[dict],
    output_path: str,
    style: str = "karaoke",
    actual_durations: list[float] | None = None,
    subtitle_style: dict | None = None,
) -> str:
    """Generate subtitle file for a rendered clip, with timestamps remapped to clip time.

    The EDL rearranges source video (hook first, then body), so we walk the
    resolved time ranges in playback order and map transcript segments into
    clip-relative time starting at t=0.

    Args:
        edl_version: EDL version dict with segments, trims.
        transcript: Full transcript with word timing.
        output_path: Path to write the subtitle file (.ass or .srt).
        style: "karaoke" for word-level ASS highlighting, "srt" for plain SRT.
        actual_durations: If provided, use these actual segment durations (from
            ffprobe) instead of theoretical (end-start) for clip cursor advancement.
            This prevents timing drift caused by keyframe-aligned cuts.

    Returns:
        The output_path written.
    """
    time_ranges = resolve_segments(edl_version["segments"], edl_version.get("trims", []))

    # Build clip-relative transcript segments by walking each time range
    clip_segments = []
    clip_cursor = 0.0

    for idx, (r_start, r_end) in enumerate(time_ranges):
        range_dur = r_end - r_start
        # Find transcript segments whose midpoint falls within this range
        for seg in transcript:
            mid = (seg["start"] + seg["end"]) / 2
            if mid >= r_start and mid <= r_end:
                # Offset: how far into this range does this segment start?
                seg_clip_start = clip_cursor + max(0, seg["start"] - r_start)
                seg_clip_end = clip_cursor + min(range_dur, seg["end"] - r_start)

                new_seg = {
                    "start": seg_clip_start,
                    "end": seg_clip_end,
                    "text": seg["text"],
                }
                # Remap word timestamps too
                if seg.get("words"):
                    new_seg["words"] = []
                    range_clip_end = clip_cursor + range_dur
                    for w in seg["words"]:
                        w_start = clip_cursor + max(0, w["start"] - r_start)
                        w_end = clip_cursor + min(range_dur, w["end"] - r_start)
                        if w_start <= range_clip_end:
                            new_seg["words"].append({
                                "word": w["word"],
                                "start": w_start,
                                "end": w_end,
                            })
                clip_segments.append(new_seg)
        # Advance cursor by actual duration if available, else theoretical
        if actual_durations and idx < len(actual_durations):
            clip_cursor += actual_durations[idx]
        else:
            clip_cursor += range_dur

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    if style == "karaoke":
        _write_karaoke_ass(clip_segments, output_path, subtitle_style=subtitle_style)
    else:
        _write_srt(clip_segments, output_path)

    return output_path


_WHITE = "&H00FFFFFF"
_BLACK_BOX = "&H00000000"

# Default subtitle style — overridden by client branding
DEFAULT_SUBTITLE_STYLE = {
    "font": "Inter",
    "size": 120,
    "highlight_color": "#0119FF",  # Used as ASS PrimaryColour (karaoke highlight)
}


def _hex_to_ass_bgr(hex_color: str) -> str:
    """Convert #RRGGBB hex to ASS &H00BBGGRR format."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"&H00{b:02X}{g:02X}{r:02X}"


def _build_ass_header(style: dict | None = None) -> str:
    """Build ASS subtitle header with client branding."""
    s = {**DEFAULT_SUBTITLE_STYLE, **(style or {})}
    primary = _hex_to_ass_bgr(s["highlight_color"])
    return f"""\
[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{s['font']},{s['size']},{primary},{_WHITE},{_BLACK_BOX},{_BLACK_BOX},-1,0,0,0,100,100,0,0,3,15,0,2,40,40,320,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _format_ass_time(seconds: float) -> str:
    """Format seconds as ASS timestamp H:MM:SS.cc."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int(round((seconds % 1) * 100))
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _align_punctuation(raw_words: list[str], punctuated_text: str) -> list[str]:
    """Align punctuated text tokens back onto raw word list from ASR.

    The ASR word array often strips punctuation (commas, periods) that exists
    in the segment text. This walks both sequences and attaches trailing
    punctuation from the text tokens to the corresponding raw words.
    """
    import re
    # Split punctuated text into tokens, preserving punctuation attached to words
    text_tokens = punctuated_text.strip().split()
    result = list(raw_words)  # copy

    def _norm(s: str) -> str:
        """Normalize word for comparison: strip all non-alpha, lowercase."""
        return re.sub(r'[^a-z]', '', s.lower())

    ti = 0
    for wi in range(len(result)):
        if ti >= len(text_tokens):
            break
        raw_n = _norm(result[wi])
        # Walk text_tokens to find matching word (skip pure-punctuation tokens)
        scan_limit = ti + 3  # don't skip too far ahead on mismatch
        while ti < len(text_tokens) and ti <= scan_limit:
            token_n = _norm(text_tokens[ti])
            if not token_n:
                ti += 1
                scan_limit += 1
                continue
            if token_n == raw_n:
                result[wi] = text_tokens[ti]
                ti += 1
                break
            else:
                ti += 1

    return result


def _write_karaoke_ass(segments: list[dict], path: str, max_words: int = 8,
                       subtitle_style: dict | None = None) -> None:
    """Write karaoke ASS subtitle file with word-level highlighting."""

    lines = []
    for seg in segments:
        text = seg["text"].strip()
        if not text:
            continue

        word_data = seg.get("words", [])
        if word_data:
            all_words = _align_punctuation([w["word"] for w in word_data], text)
            all_starts = [w["start"] for w in word_data]
            all_ends = [w["end"] for w in word_data]
            all_durations = []
            for j in range(len(word_data)):
                if j + 1 < len(word_data):
                    dur = word_data[j + 1]["start"] - word_data[j]["start"]
                else:
                    dur = word_data[j]["end"] - word_data[j]["start"]
                all_durations.append(max(1, int(round(dur * 100))))
        else:
            all_words = text.split()
            if not all_words:
                continue
            seg_duration_cs = int(round((seg["end"] - seg["start"]) * 100))
            total_chars = sum(len(w) for w in all_words)
            all_durations = [max(1, int(round(seg_duration_cs * len(w) / total_chars))) for w in all_words]
            cursor = seg["start"]
            all_starts = []
            all_ends = []
            for d in all_durations:
                all_starts.append(cursor)
                all_ends.append(cursor + d / 100.0)
                cursor += d / 100.0

        for i in range(0, len(all_words), max_words):
            chunk_words = all_words[i:i + max_words]
            chunk_durs = all_durations[i:i + max_words]
            chunk_start = all_starts[i]
            chunk_end = all_ends[min(i + max_words - 1, len(all_ends) - 1)]

            tagged = "".join(f"{{\\k{d}}}{w} " for w, d in zip(chunk_words, chunk_durs)).rstrip()
            start_ts = _format_ass_time(chunk_start)
            end_ts = _format_ass_time(chunk_end)
            lines.append(f"Dialogue: 0,{start_ts},{end_ts},Default,,0,0,0,,{tagged}")

    with open(path, "w", encoding="utf-8") as f:
        f.write(_build_ass_header(subtitle_style))
        f.write("\n".join(lines))
        f.write("\n")


def _write_srt(segments: list[dict], path: str) -> None:
    """Write plain SRT subtitle file."""
    lines = []
    for i, seg in enumerate(segments, 1):
        text = seg["text"].strip()
        if not text:
            continue
        start = seg["start"]
        end = seg["end"]
        lines.append(str(i))
        lines.append(f"{_srt_time(start)} --> {_srt_time(end)}")
        lines.append(text)
        lines.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _srt_time(seconds: float) -> str:
    """Format seconds as SRT timestamp HH:MM:SS,mmm."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds % 1) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _seconds_to_timecode(seconds: float, fps: int = 30) -> str:
    """Convert seconds to HH:MM:SS.mmm timecode."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def generate_kdenlive_xml(
    edl_version: dict,
    source_video: str,
    profile: str = "vertical",
    subtitle_path: str | None = None,
    fps: int = 30,
    face_pos: tuple[float, float] | None = None,
    draft_video: str | None = None,
) -> str:
    """Generate Kdenlive/MLT XML project from an EDL version.

    Matches the structure Kdenlive 24.x produces: chain-based clips, per-track
    tractors with A/B playlists, sequence tractor, wrapper tractor, and proper
    transitions/filters.

    For vertical (short) projects, pass draft_video to use the already-cropped
    rendered clip instead of the raw source, so the project opens correctly
    in the vertical profile without needing MLT crop filters.

    Args:
        edl_version: EDL version dict with segments, trims.
        source_video: Path to source video (used for horizontal/long projects).
        profile: "vertical" (1080x1920) or "horizontal" (1920x1080).
        subtitle_path: Optional subtitle file to include as a track.
        fps: Frames per second (default 30).
        face_pos: Optional (x, y) face position (0.0-1.0) — unused, kept for API compat.
        draft_video: Optional path to already-rendered draft clip. When provided,
            the project uses this as a single timeline clip instead of source
            segments. Use for vertical shorts that are already cropped.

    Returns:
        XML string for a .kdenlive project file.
    """
    if profile == "vertical":
        width, height = 1080, 1920
    else:
        width, height = 1920, 1080

    video_file = source_video
    time_ranges_for_timeline = resolve_segments(
        edl_version["segments"], edl_version.get("trims", []))
    try:
        clip_dur = _probe_duration(source_video)
    except (ValueError, FileNotFoundError):
        clip_dur = 3600.0
    clip_tc = _seconds_to_timecode(clip_dur)

    clip_name = os.path.basename(video_file)
    source_uuid = str(uuid.uuid4())
    seq_uuid = str(uuid.uuid4())
    doc_id = str(int(uuid.uuid4().int % 10**13))

    # Compute total timeline duration
    total_dur = sum(end - start for start, end in time_ranges_for_timeline)
    total_tc = _seconds_to_timecode(total_dur)
    long_tc = _seconds_to_timecode(max(clip_dur, 340))

    # Kdenlive clip IDs (integers)
    source_kid = "4"
    seq_kid = "3"

    L = []  # output lines

    # -- Root --
    L.append("<?xml version='1.0' encoding='utf-8'?>")
    L.append(f'<mlt LC_NUMERIC="C" producer="main_bin" version="7.37.0">')
    L.append(f' <profile colorspace="709" description="{width}x{height} {fps}fps"'
             f' display_aspect_den="{height}" display_aspect_num="{width}"'
             f' frame_rate_den="1" frame_rate_num="{fps}"'
             f' height="{height}" progressive="1"'
             f' sample_aspect_den="1" sample_aspect_num="1" width="{width}"/>')

    # -- Bin chain (clip in project bin) --
    L.append(f' <chain id="chain2" out="{clip_tc}">')
    L.append(f'  <property name="resource">{video_file}</property>')
    L.append(f'  <property name="mlt_service">avformat-novalidate</property>')
    L.append(f'  <property name="kdenlive:control_uuid">{{{source_uuid}}}</property>')
    L.append(f'  <property name="kdenlive:id">{source_kid}</property>')
    L.append(f'  <property name="kdenlive:clip_type">0</property>')
    L.append(f'  <property name="kdenlive:clipname">{clip_name}</property>')
    L.append(f'  <property name="kdenlive:folderid">-1</property>')
    L.append(' </chain>')

    # -- Black track producer --
    L.append(f' <producer id="producer0" in="00:00:00.000" out="{long_tc}">')
    L.append('  <property name="length">2147483647</property>')
    L.append('  <property name="eof">continue</property>')
    L.append('  <property name="resource">black</property>')
    L.append('  <property name="aspect_ratio">1</property>')
    L.append('  <property name="mlt_service">color</property>')
    L.append('  <property name="kdenlive:playlistid">black_track</property>')
    L.append('  <property name="mlt_image_format">rgba</property>')
    L.append('  <property name="set.test_audio">0</property>')
    L.append(' </producer>')

    # -- Timeline chain (same clip, for timeline use) --
    L.append(f' <chain id="chain0" out="{clip_tc}">')
    L.append(f'  <property name="resource">{video_file}</property>')
    L.append(f'  <property name="mlt_service">avformat-novalidate</property>')
    L.append(f'  <property name="kdenlive:control_uuid">{{{source_uuid}}}</property>')
    L.append(f'  <property name="kdenlive:id">{source_kid}</property>')
    L.append(f'  <property name="kdenlive:clip_type">0</property>')
    L.append(f'  <property name="kdenlive:folderid">-1</property>')
    L.append(' </chain>')

    # -- Audio track 1 (A/B playlists + tractor) --
    L.append(' <playlist id="playlist0">')
    L.append('  <property name="kdenlive:audio_track">1</property>')
    for start, end in time_ranges_for_timeline:
        L.append(f'  <entry in="{_seconds_to_timecode(start)}" out="{_seconds_to_timecode(end)}" producer="chain0">')
        L.append(f'   <property name="kdenlive:id">{source_kid}</property>')
        L.append('  </entry>')
    L.append(' </playlist>')
    L.append(' <playlist id="playlist1">')
    L.append('  <property name="kdenlive:audio_track">1</property>')
    L.append(' </playlist>')
    L.append(f' <tractor id="tractor0" in="00:00:00.000" out="{total_tc}">')
    L.append('  <property name="kdenlive:audio_track">1</property>')
    L.append('  <property name="kdenlive:trackheight">57</property>')
    L.append('  <property name="kdenlive:timeline_active">1</property>')
    L.append('  <property name="kdenlive:collapsed">0</property>')
    L.append('  <track hide="video" producer="playlist0"/>')
    L.append('  <track hide="video" producer="playlist1"/>')
    L.append('  <filter id="filter0">')
    L.append('   <property name="window">75</property>')
    L.append('   <property name="max_gain">20dB</property>')
    L.append('   <property name="mlt_service">volume</property>')
    L.append('   <property name="internal_added">237</property>')
    L.append('   <property name="disable">1</property>')
    L.append('  </filter>')
    L.append('  <filter id="filter1">')
    L.append('   <property name="channel">-1</property>')
    L.append('   <property name="mlt_service">panner</property>')
    L.append('   <property name="internal_added">237</property>')
    L.append('   <property name="start">0.5</property>')
    L.append('   <property name="disable">1</property>')
    L.append('  </filter>')
    L.append('  <filter id="filter2">')
    L.append('   <property name="iec_scale">0</property>')
    L.append('   <property name="mlt_service">audiolevel</property>')
    L.append('   <property name="dbpeak">1</property>')
    L.append('   <property name="disable">1</property>')
    L.append('  </filter>')
    L.append(' </tractor>')

    # -- Audio track 2 (empty, for user editing room) --
    L.append(' <playlist id="playlist2">')
    L.append('  <property name="kdenlive:audio_track">1</property>')
    L.append(' </playlist>')
    L.append(' <playlist id="playlist3">')
    L.append('  <property name="kdenlive:audio_track">1</property>')
    L.append(' </playlist>')
    L.append(' <tractor id="tractor1" in="00:00:00.000">')
    L.append('  <property name="kdenlive:audio_track">1</property>')
    L.append('  <property name="kdenlive:trackheight">57</property>')
    L.append('  <property name="kdenlive:timeline_active">1</property>')
    L.append('  <property name="kdenlive:collapsed">0</property>')
    L.append('  <track hide="video" producer="playlist2"/>')
    L.append('  <track hide="video" producer="playlist3"/>')
    L.append('  <filter id="filter3">')
    L.append('   <property name="window">75</property>')
    L.append('   <property name="max_gain">20dB</property>')
    L.append('   <property name="mlt_service">volume</property>')
    L.append('   <property name="internal_added">237</property>')
    L.append('   <property name="disable">1</property>')
    L.append('  </filter>')
    L.append('  <filter id="filter4">')
    L.append('   <property name="channel">-1</property>')
    L.append('   <property name="mlt_service">panner</property>')
    L.append('   <property name="internal_added">237</property>')
    L.append('   <property name="start">0.5</property>')
    L.append('   <property name="disable">1</property>')
    L.append('  </filter>')
    L.append('  <filter id="filter5">')
    L.append('   <property name="iec_scale">0</property>')
    L.append('   <property name="mlt_service">audiolevel</property>')
    L.append('   <property name="dbpeak">1</property>')
    L.append('   <property name="disable">1</property>')
    L.append('  </filter>')
    L.append(' </tractor>')

    # -- Video track 1 (empty, for user editing room) --
    L.append(' <playlist id="playlist4"/>')
    L.append(' <playlist id="playlist5"/>')
    L.append(' <tractor id="tractor2" in="00:00:00.000">')
    L.append('  <property name="kdenlive:trackheight">57</property>')
    L.append('  <property name="kdenlive:timeline_active">1</property>')
    L.append('  <property name="kdenlive:collapsed">0</property>')
    L.append('  <track hide="audio" producer="playlist4"/>')
    L.append('  <track hide="audio" producer="playlist5"/>')
    L.append(' </tractor>')

    # -- Video track 2 (main video with clips) --
    # Timeline chain for video track (separate instance)
    L.append(f' <chain id="chain1" out="{clip_tc}">')
    L.append(f'  <property name="resource">{video_file}</property>')
    L.append(f'  <property name="mlt_service">avformat-novalidate</property>')
    L.append(f'  <property name="kdenlive:control_uuid">{{{source_uuid}}}</property>')
    L.append(f'  <property name="kdenlive:id">{source_kid}</property>')
    L.append(f'  <property name="kdenlive:clip_type">0</property>')
    L.append(f'  <property name="kdenlive:folderid">-1</property>')
    L.append(' </chain>')
    # Compute pan_zoom rect for vertical profile (applied per-entry)
    panzoom_rect = None
    if profile == "vertical":
        # Kdenlive "Position and Zoom" effect (pan_zoom / affine filter)
        # Coordinate system: profile pixels (1080x1920)
        # Default fit: source scaled to fill profile width → height = width * src_h/src_w
        # We need to scale up so source fills the full profile height
        # Rect format: "TIMECODE=X Y W H" (space-separated, timecode-prefixed)
        # Rect W:H matches profile aspect; scale factor controls zoom
        default_h = width  # for 16:9 source in 1080-wide profile: 1080 * (9/16) = 607.5
        zoom_scale = height / default_h  # 1920 / 607.5 = 3.16
        rect_w = int(width * zoom_scale)
        rect_h = int(height * zoom_scale)
        if face_pos:
            face_px = float(face_pos[0]) * rect_w
            rect_x = int(width / 2 - face_px)
        else:
            rect_x = -(rect_w - width) // 2
        rect_y = (height - rect_h) // 2
        panzoom_rect = (rect_x, rect_y, rect_w, rect_h)

    filter_counter = [6]  # mutable counter for unique filter IDs

    L.append(' <playlist id="playlist6">')
    for start, end in time_ranges_for_timeline:
        in_tc = _seconds_to_timecode(start)
        L.append(f'  <entry in="{in_tc}" out="{_seconds_to_timecode(end)}" producer="chain1">')
        L.append(f'   <property name="kdenlive:id">{source_kid}</property>')
        if panzoom_rect:
            rx, ry, rw, rh = panzoom_rect
            fid = filter_counter[0]
            filter_counter[0] += 1
            L.append(f'   <filter id="filter{fid}">')
            L.append('    <property name="background">colour:0</property>')
            L.append('    <property name="mlt_service">affine</property>')
            L.append('    <property name="kdenlive_id">pan_zoom</property>')
            L.append(f'    <property name="transition.rect">{in_tc}={rx} {ry} {rw} {rh}</property>')
            L.append('    <property name="transition.distort">0</property>')
            L.append('    <property name="use_normalised">0</property>')
            L.append('    <property name="producer.resource">0x00000000</property>')
            L.append('    <property name="transition.repeat_off">1</property>')
            L.append('    <property name="transition.mirror_off">1</property>')
            L.append('    <property name="kdenlive:collapsed">0</property>')
            L.append(f'   </filter>')
        L.append('  </entry>')
    L.append(' </playlist>')
    L.append(' <playlist id="playlist7"/>')
    L.append(f' <tractor id="tractor3" in="00:00:00.000" out="{total_tc}">')
    L.append('  <property name="kdenlive:trackheight">57</property>')
    L.append('  <property name="kdenlive:timeline_active">1</property>')
    L.append('  <property name="kdenlive:collapsed">0</property>')
    L.append('  <track hide="audio" producer="playlist6"/>')
    L.append('  <track hide="audio" producer="playlist7"/>')
    L.append(' </tractor>')

    # -- Sequence tractor (tractor4) --
    L.append(f' <tractor id="tractor4" in="00:00:00.000" out="{total_tc}">')
    L.append(f'  <property name="kdenlive:duration">{total_tc}</property>')
    L.append(f'  <property name="kdenlive:clipname">Sequence 1</property>')
    L.append(f'  <property name="kdenlive:description"/>')
    L.append(f'  <property name="kdenlive:uuid">{{{seq_uuid}}}</property>')
    L.append(f'  <property name="kdenlive:producer_type">17</property>')
    L.append(f'  <property name="kdenlive:control_uuid">{{{seq_uuid}}}</property>')
    L.append(f'  <property name="kdenlive:id">{seq_kid}</property>')
    L.append(f'  <property name="kdenlive:clip_type">0</property>')
    L.append(f'  <property name="kdenlive:folderid">2</property>')
    L.append(f'  <property name="kdenlive:sequenceproperties.activeTrack">3</property>')
    L.append(f'  <property name="kdenlive:sequenceproperties.audioTarget">1</property>')
    L.append(f'  <property name="kdenlive:sequenceproperties.disablepreview">0</property>')
    L.append(f'  <property name="kdenlive:sequenceproperties.documentuuid">{{{seq_uuid}}}</property>')
    L.append(f'  <property name="kdenlive:sequenceproperties.hasAudio">1</property>')
    L.append(f'  <property name="kdenlive:sequenceproperties.hasVideo">1</property>')
    L.append(f'  <property name="kdenlive:sequenceproperties.position">0</property>')
    L.append(f'  <property name="kdenlive:sequenceproperties.scrollPos">0</property>')
    L.append(f'  <property name="kdenlive:sequenceproperties.tracks">4</property>')
    L.append(f'  <property name="kdenlive:sequenceproperties.tracksCount">4</property>')
    L.append(f'  <property name="kdenlive:sequenceproperties.verticalzoom">1</property>')
    L.append(f'  <property name="kdenlive:sequenceproperties.videoTarget">2</property>')
    L.append(f'  <property name="kdenlive:sequenceproperties.zonein">0</property>')
    L.append(f'  <property name="kdenlive:sequenceproperties.zoneout">75</property>')
    L.append(f'  <property name="kdenlive:sequenceproperties.zoom">8</property>')
    L.append(f'  <property name="kdenlive:sequenceproperties.guides">[]</property>')
    L.append('  <track producer="producer0"/>')
    L.append('  <track producer="tractor0"/>')
    L.append('  <track producer="tractor1"/>')
    L.append('  <track producer="tractor2"/>')
    L.append('  <track producer="tractor3"/>')
    # Audio mix transitions
    L.append('  <transition id="transition0">')
    L.append('   <property name="a_track">0</property>')
    L.append('   <property name="b_track">1</property>')
    L.append('   <property name="mlt_service">mix</property>')
    L.append('   <property name="kdenlive_id">mix</property>')
    L.append('   <property name="internal_added">237</property>')
    L.append('   <property name="always_active">1</property>')
    L.append('   <property name="accepts_blanks">1</property>')
    L.append('   <property name="sum">1</property>')
    L.append('  </transition>')
    L.append('  <transition id="transition1">')
    L.append('   <property name="a_track">0</property>')
    L.append('   <property name="b_track">2</property>')
    L.append('   <property name="mlt_service">mix</property>')
    L.append('   <property name="kdenlive_id">mix</property>')
    L.append('   <property name="internal_added">237</property>')
    L.append('   <property name="always_active">1</property>')
    L.append('   <property name="accepts_blanks">1</property>')
    L.append('   <property name="sum">1</property>')
    L.append('  </transition>')
    # Video compositing transitions
    L.append('  <transition id="transition2">')
    L.append('   <property name="a_track">0</property>')
    L.append('   <property name="b_track">3</property>')
    L.append('   <property name="compositing">0</property>')
    L.append('   <property name="distort">0</property>')
    L.append('   <property name="mlt_service">qtblend</property>')
    L.append('   <property name="kdenlive_id">qtblend</property>')
    L.append('   <property name="internal_added">237</property>')
    L.append('   <property name="always_active">1</property>')
    L.append('  </transition>')
    L.append('  <transition id="transition3">')
    L.append('   <property name="a_track">0</property>')
    L.append('   <property name="b_track">4</property>')
    L.append('   <property name="compositing">0</property>')
    L.append('   <property name="distort">0</property>')
    L.append('   <property name="mlt_service">qtblend</property>')
    L.append('   <property name="kdenlive_id">qtblend</property>')
    L.append('   <property name="internal_added">237</property>')
    L.append('   <property name="always_active">1</property>')
    L.append('  </transition>')
    # Master filters
    L.append('  <filter id="filter6">')
    L.append('   <property name="window">75</property>')
    L.append('   <property name="max_gain">20dB</property>')
    L.append('   <property name="mlt_service">volume</property>')
    L.append('   <property name="internal_added">237</property>')
    L.append('   <property name="disable">1</property>')
    L.append('  </filter>')
    L.append('  <filter id="filter7">')
    L.append('   <property name="channel">-1</property>')
    L.append('   <property name="mlt_service">panner</property>')
    L.append('   <property name="internal_added">237</property>')
    L.append('   <property name="start">0.5</property>')
    L.append('   <property name="disable">1</property>')
    L.append('  </filter>')
    L.append(' </tractor>')

    # -- main_bin (project bin) --
    L.append(' <playlist id="main_bin">')
    L.append(f'  <property name="kdenlive:folder.-1.2">Sequences</property>')
    L.append(f'  <property name="kdenlive:sequenceFolder">2</property>')
    L.append(f'  <property name="kdenlive:docproperties.activetimeline">{{{seq_uuid}}}</property>')
    L.append(f'  <property name="kdenlive:docproperties.audioChannels">2</property>')
    L.append(f'  <property name="kdenlive:docproperties.documentid">{doc_id}</property>')
    L.append(f'  <property name="kdenlive:docproperties.version">1.1</property>')
    L.append(f'  <property name="kdenlive:docproperties.profile">{width}x{height} {fps}fps</property>')
    L.append(f'  <property name="kdenlive:docproperties.enableproxy">0</property>')
    L.append(f'  <property name="kdenlive:docproperties.generateproxy">0</property>')
    L.append(f'  <property name="xml_retain">1</property>')
    L.append(f'  <entry in="00:00:00.000" out="{clip_tc}" producer="chain2"/>')
    L.append(f'  <entry in="00:00:00.000" out="{total_tc}" producer="tractor4"/>')
    L.append(' </playlist>')

    # -- Wrapper tractor (project root) --
    L.append(f' <tractor id="tractor5" in="00:00:00.000" out="{total_tc}">')
    L.append('  <property name="kdenlive:projectTractor">1</property>')
    L.append(f'  <track in="00:00:00.000" out="{total_tc}" producer="tractor4"/>')
    L.append(' </tractor>')
    L.append('</mlt>')

    return "\n".join(L)


def generate_fcp7_xml(
    edl_version: dict,
    source_video: str,
    sequence_name: str = "RoughCut",
    fps: int = 30,
) -> str:
    """Generate FCP 7 XML (XMEML) for Premiere Pro import.

    Creates a sequence with segments from the source video placed on the
    timeline. No subtitle or effects — just the raw edit for Jude to refine.

    Args:
        edl_version: EDL version dict with segments, trims.
        source_video: Absolute path to source video file.
        sequence_name: Name for the sequence in Premiere.
        fps: Frames per second.

    Returns:
        XMEML string for a .xml file importable by Premiere Pro.
    """
    time_ranges = resolve_segments(edl_version["segments"], edl_version.get("trims", []))
    if not time_ranges:
        return ""

    # Probe source video for duration and dimensions
    try:
        src_duration_s = _probe_duration(source_video)
    except (ValueError, FileNotFoundError):
        src_duration_s = 3600.0

    src_dur_frames = int(round(src_duration_s * fps))

    # Probe video dimensions
    try:
        result = subprocess.run(
            [FFPROBE, "-v", "quiet", "-select_streams", "v:0",
             "-show_entries", "stream=width,height",
             "-of", "csv=p=0", source_video],
            capture_output=True, text=True,
        )
        w, h = result.stdout.strip().split(",")
        src_width, src_height = int(w), int(h)
    except Exception:
        src_width, src_height = 1280, 720

    # Emit just the filename — Premiere will look next to the XML file first.
    # Jude's workflow: drop the XML and the source video in the same folder.
    file_name = os.path.basename(source_video)
    path_url = "file://localhost/" + urllib.parse.quote(file_name)

    # Convert time ranges to frame numbers and compute timeline positions
    clips = []
    timeline_cursor = 0
    for start_s, end_s in time_ranges:
        in_frame = int(round(start_s * fps))
        out_frame = int(round(end_s * fps))
        clip_len = out_frame - in_frame
        clips.append({
            "in": in_frame,
            "out": out_frame,
            "start": timeline_cursor,
            "end": timeline_cursor + clip_len,
        })
        timeline_cursor += clip_len

    total_frames = timeline_cursor

    L = []
    L.append('<?xml version="1.0" encoding="UTF-8"?>')
    L.append('<!DOCTYPE xmeml>')
    L.append('<xmeml version="5">')
    L.append(f'  <sequence id="seq-1">')
    L.append(f'    <name>{sequence_name}</name>')
    L.append(f'    <duration>{total_frames}</duration>')
    L.append(f'    <rate><timebase>{fps}</timebase><ntsc>FALSE</ntsc></rate>')
    L.append(f'    <timecode>')
    L.append(f'      <string>00:00:00:00</string>')
    L.append(f'      <frame>0</frame>')
    L.append(f'      <displayformat>NDF</displayformat>')
    L.append(f'      <rate><timebase>{fps}</timebase><ntsc>FALSE</ntsc></rate>')
    L.append(f'    </timecode>')
    L.append(f'    <media>')

    # Video track
    L.append(f'      <video>')
    L.append(f'        <track>')
    for i, clip in enumerate(clips):
        clip_id = f"clip-v{i+1}"
        L.append(f'          <clipitem id="{clip_id}">')
        L.append(f'            <name>Segment {i+1}</name>')
        L.append(f'            <duration>{src_dur_frames}</duration>')
        L.append(f'            <rate><timebase>{fps}</timebase><ntsc>FALSE</ntsc></rate>')
        L.append(f'            <start>{clip["start"]}</start>')
        L.append(f'            <end>{clip["end"]}</end>')
        L.append(f'            <in>{clip["in"]}</in>')
        L.append(f'            <out>{clip["out"]}</out>')
        if i == 0:
            # Full file definition on first reference
            L.append(f'            <file id="file-1">')
            L.append(f'              <name>{file_name}</name>')
            L.append(f'              <pathurl>{path_url}</pathurl>')
            L.append(f'              <duration>{src_dur_frames}</duration>')
            L.append(f'              <rate><timebase>{fps}</timebase><ntsc>FALSE</ntsc></rate>')
            L.append(f'              <media>')
            L.append(f'                <video>')
            L.append(f'                  <samplecharacteristics>')
            L.append(f'                    <width>{src_width}</width>')
            L.append(f'                    <height>{src_height}</height>')
            L.append(f'                  </samplecharacteristics>')
            L.append(f'                </video>')
            L.append(f'                <audio>')
            L.append(f'                  <samplecharacteristics>')
            L.append(f'                    <depth>16</depth>')
            L.append(f'                    <samplerate>48000</samplerate>')
            L.append(f'                  </samplecharacteristics>')
            L.append(f'                </audio>')
            L.append(f'              </media>')
            L.append(f'            </file>')
        else:
            L.append(f'            <file id="file-1"/>')
        L.append(f'          </clipitem>')
    L.append(f'        </track>')
    L.append(f'      </video>')

    # Audio track (mirrors video)
    L.append(f'      <audio>')
    L.append(f'        <track>')
    for i, clip in enumerate(clips):
        clip_id = f"clip-a{i+1}"
        L.append(f'          <clipitem id="{clip_id}">')
        L.append(f'            <name>Segment {i+1}</name>')
        L.append(f'            <duration>{src_dur_frames}</duration>')
        L.append(f'            <rate><timebase>{fps}</timebase><ntsc>FALSE</ntsc></rate>')
        L.append(f'            <start>{clip["start"]}</start>')
        L.append(f'            <end>{clip["end"]}</end>')
        L.append(f'            <in>{clip["in"]}</in>')
        L.append(f'            <out>{clip["out"]}</out>')
        L.append(f'            <file id="file-1"/>')
        L.append(f'            <sourcetrack>')
        L.append(f'              <mediatype>audio</mediatype>')
        L.append(f'              <trackindex>1</trackindex>')
        L.append(f'            </sourcetrack>')
        L.append(f'          </clipitem>')
    L.append(f'        </track>')
    L.append(f'      </audio>')

    L.append(f'    </media>')
    L.append(f'  </sequence>')
    L.append(f'</xmeml>')

    return "\n".join(L)
