import os
import subprocess
import tempfile

import cv2
import numpy as np

# CrowdTamers blue (#1B98F5) in ASS BGR format
_CT_BLUE = "&H00F5981B"
_WHITE = "&H00FFFFFF"
_BLACK_BOX = "&H00000000"

_ASS_HEADER = """\
[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Raleway,120,{primary},{secondary},{back},{back},-1,0,0,0,100,100,0,0,3,15,0,2,40,40,320,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""".format(primary=_CT_BLUE, secondary=_WHITE, back=_BLACK_BOX)


def _format_ass_time(seconds: float) -> str:
    """Format seconds as ASS timestamp H:MM:SS.cc (centiseconds)."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int(round((seconds % 1) * 100))
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _write_ass(
    segments: list[dict],
    offset: float,
    path: str,
    max_words: int = 8,
) -> None:
    """Write an ASS subtitle file with word-level highlighting.

    Each segment is split into chunks of at most *max_words* so that
    no more than ~2 lines of text appear on screen at once.  Words
    highlight one at a time (\\k tags) from white to CrowdTamers blue,
    with timing weighted by character length for natural speech sync.

    Args:
        segments: Whisper segments with 'start', 'end', 'text' keys.
        offset: Time offset to add to all timestamps (accounts for
                topic_start subtraction and hook prepend).
        path: Output .ass file path.
        max_words: Max words per subtitle event (keeps text to ~2 lines).
    """
    lines = []
    for seg in segments:
        text = seg["text"].strip()
        if not text:
            continue

        seg_start = seg["start"] + offset
        seg_end = seg["end"] + offset
        if seg_start < 0:
            continue

        word_data = seg.get("words", [])

        if word_data:
            # Use real word-level timestamps from Whisper
            all_words = [w["word"] for w in word_data]
            # Duration = time from this word's start to next word's start
            # (last word uses its own end time)
            all_starts = [w["start"] + offset for w in word_data]
            all_ends = [w["end"] + offset for w in word_data]
            all_durations = []
            for j in range(len(word_data)):
                if j + 1 < len(word_data):
                    dur = word_data[j + 1]["start"] - word_data[j]["start"]
                else:
                    dur = word_data[j]["end"] - word_data[j]["start"]
                all_durations.append(max(1, int(round(dur * 100))))
        else:
            # Fallback: distribute by character length
            all_words = text.split()
            if not all_words:
                continue
            seg_duration_cs = int(round((seg_end - seg_start) * 100))
            total_chars = sum(len(w) for w in all_words)
            all_durations = []
            for w in all_words:
                wd = max(1, int(round(seg_duration_cs * len(w) / total_chars)))
                all_durations.append(wd)
            diff = seg_duration_cs - sum(all_durations)
            if diff and all_durations:
                all_durations[-1] = max(1, all_durations[-1] + diff)
            # Synthesize start times for chunking
            cursor = seg_start
            all_starts = []
            all_ends = []
            for d in all_durations:
                all_starts.append(cursor)
                all_ends.append(cursor + d / 100.0)
                cursor += d / 100.0

        if not all_words:
            continue

        # Split into chunks of max_words for 2-line limit
        for i in range(0, len(all_words), max_words):
            chunk_words = all_words[i:i + max_words]
            chunk_durs = all_durations[i:i + max_words]
            chunk_start = all_starts[i]
            chunk_end = all_ends[min(i + max_words - 1, len(all_ends) - 1)]

            tagged = "".join(
                f"{{\\k{d}}}{w} " for w, d in zip(chunk_words, chunk_durs)
            ).rstrip()

            start_ts = _format_ass_time(chunk_start)
            end_ts = _format_ass_time(chunk_end)
            lines.append(
                f"Dialogue: 0,{start_ts},{end_ts},Default,,0,0,0,,{tagged}"
            )

    with open(path, "w", encoding="utf-8") as f:
        f.write(_ASS_HEADER)
        f.write("\n".join(lines))
        f.write("\n")


def get_clip_duration(path: str) -> float:
    """Get duration of a media file in seconds via ffprobe."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True,
    )
    return float(result.stdout.strip())


def _run_ffmpeg(args: list[str]) -> None:
    result = subprocess.run(
        ["ffmpeg", "-y"] + args,
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr}")


def extract_segment(input_path: str, start: float, end: float, output_path: str) -> None:
    _run_ffmpeg([
        "-ss", str(start),
        "-i", input_path,
        "-to", str(end - start),
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        output_path,
    ])


def extract_frame(input_path: str, timestamp: float, output_path: str) -> None:
    _run_ffmpeg([
        "-ss", str(timestamp),
        "-i", input_path,
        "-frames:v", "1",
        output_path,
    ])


def prepend_hook(hook_path: str, segment_path: str, output_path: str) -> None:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write(f"file '{hook_path}'\nfile '{segment_path}'\n")
        concat_file = f.name
    try:
        _run_ffmpeg([
            "-f", "concat", "-safe", "0",
            "-i", concat_file,
            "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            output_path,
        ])
    finally:
        os.unlink(concat_file)


def _detect_face_center(video_path: str, sample_count: int = 5) -> tuple[float, float] | None:
    """Sample frames from the video and detect the dominant face position.

    Returns (x_center, y_center) as fractions of video dimensions (0.0–1.0),
    or None if no face is detected.
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return None

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total_frames < 1:
        cap.release()
        return None

    face_cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )

    face_centers = []
    for i in range(sample_count):
        frame_idx = int(total_frames * (i + 1) / (sample_count + 1))
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = cap.read()
        if not ret:
            continue

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(50, 50))

        if len(faces) > 0:
            # Pick the largest face
            largest = max(faces, key=lambda f: f[2] * f[3])
            x, y, w, h = largest
            cx = (x + w / 2) / frame.shape[1]
            cy = (y + h / 2) / frame.shape[0]
            face_centers.append((cx, cy))

    cap.release()

    if not face_centers:
        return None

    # Average the detected positions for stability
    avg_cx = sum(c[0] for c in face_centers) / len(face_centers)
    avg_cy = sum(c[1] for c in face_centers) / len(face_centers)
    return (avg_cx, avg_cy)


def create_short(
    input_path: str,
    output_path: str,
    max_duration: float = 59.0,
    segments: list[dict] | None = None,
    topic_start: float = 0.0,
    hook_duration: float = 0.0,
) -> None:
    """Create a 9:16 vertical short, cropping around detected face if possible.

    When *segments* are provided, burned-in subtitles with karaoke-style
    word highlighting are added via an ASS filter.
    """
    face_pos = _detect_face_center(input_path)

    if face_pos:
        # Get video dimensions
        cap = cv2.VideoCapture(input_path)
        vid_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        vid_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap.release()

        # Crop width for 9:16 from full height
        crop_w = int(vid_h * 9 / 16)
        crop_h = vid_h

        # Center crop on face X position, clamp to bounds
        face_x_px = int(face_pos[0] * vid_w)
        crop_x = max(0, min(face_x_px - crop_w // 2, vid_w - crop_w))

        crop_filter = f"crop={crop_w}:{crop_h}:{crop_x}:0,scale=1080:1920"
    else:
        # Fallback to center crop
        crop_filter = "crop=ih*9/16:ih,scale=1080:1920"

    ass_path = None
    try:
        if segments:
            # Segment timestamps are in original-video space.
            # Shift to final.mp4 space: subtract topic_start, add hook_duration.
            offset = -topic_start + hook_duration
            ass_path = tempfile.mktemp(suffix=".ass")
            _write_ass(segments, offset, ass_path)
            # Escape colons in path for ffmpeg filter syntax
            escaped = ass_path.replace("\\", "\\\\").replace(":", "\\:")
            vf = f"{crop_filter},ass={escaped}"
        else:
            vf = crop_filter

        _run_ffmpeg([
            "-i", input_path,
            "-t", str(max_duration),
            "-vf", vf,
            "-c:a", "copy",
            output_path,
        ])
    finally:
        if ass_path and os.path.exists(ass_path):
            os.unlink(ass_path)
