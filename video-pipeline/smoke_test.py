#!/usr/bin/env python3
"""Smoke test: download a YouTube video, create 9:16 shorts with karaoke subtitles,
then translate and burn in subtitles for French, Spanish, BR Portuguese, and Chinese.

Usage:
    .venv/bin/python3.14 smoke_test.py "https://youtu.be/I7X4Lrkj2IU" --max-clips 2

Combines:
  - pipeline/ (shorts: topic segmentation, hooks, face crop, karaoke ASS)
  - video_pipeline_v2.py (translation: SRT generation, two-pass LLM translation, burn-in)
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import concurrent.futures

# ---------------------------------------------------------------------------
# Ensure pipeline/ is importable
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline.segment import segment_topics
from pipeline.hooks import select_hook
from pipeline.editor import (
    extract_segment,
    prepend_hook,
    get_clip_duration,
    _detect_face_center,
    _format_ass_time,
    _run_ffmpeg,
)

import openai

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
FFMPEG = "/opt/homebrew/bin/ffmpeg"

LITELLM_URL = "http://localhost:13668/v1"
LITELLM_KEY = "sk-litellm-98976c4db5c793dfac352ba1877254f7d3465e975d55155e"

OLLAMA_URL = "http://localhost:11434/v1"
OLLAMA_KEY = "ollama"  # Ollama doesn't need a real key

# Model routing
ROMANCE_MODEL_URL = LITELLM_URL
ROMANCE_MODEL_KEY = LITELLM_KEY
ROMANCE_MODEL = "botty-claude"  # Sonnet via LiteLLM

CHINESE_MODEL_URL = OLLAMA_URL
CHINESE_MODEL_KEY = OLLAMA_KEY
CHINESE_MODEL = "qwen3:30b"

# Client brand colors — Jonathan Brill
# ASS uses BGR format: &HBBGGRR
HIGHLIGHT_COLOR = "&H000A82FF"   # #FF820A orange in BGR
DEFAULT_COLOR = "&H00FFFFFF"     # #FFFFFF white
OUTLINE_COLOR = "&H00000000"     # black background box

# Font config
FONT_NAME = "Helvetica Neue"
FONT_SIZE = 120

# Languages to translate
LANGUAGES = {
    "fr": {"name": "French", "register": "Metropolitan French, professional yet warm", "font": "Helvetica Neue"},
    "es": {"name": "Spanish", "register": "Neutral Latin-American Spanish, conversational and clear", "font": "Helvetica Neue"},
    "pt-BR": {"name": "Brazilian Portuguese", "register": "Brazilian Portuguese, conversational and engaging", "font": "Helvetica Neue"},
    "zh-CN": {"name": "Simplified Chinese", "register": "Mainland Mandarin, professional and direct", "font": "Noto Sans CJK SC"},
}


# ---------------------------------------------------------------------------
# ASS subtitle generation (customized from editor.py)
# ---------------------------------------------------------------------------

def _ass_header(font=FONT_NAME, size=FONT_SIZE, highlight=HIGHLIGHT_COLOR,
                default=DEFAULT_COLOR, outline=OUTLINE_COLOR):
    return f"""\
[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{size},{highlight},{default},{outline},{outline},-1,0,0,0,100,100,0,0,3,15,0,2,40,40,320,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def write_karaoke_ass(segments, offset, path, max_words=8, max_duration=None):
    """Write ASS with per-word karaoke highlighting.

    max_duration: if set, drop any subtitle lines that start after this time.
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
            all_words = [w["word"] for w in word_data]
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
            cursor = seg_start
            all_starts = []
            all_ends = []
            for d in all_durations:
                all_starts.append(cursor)
                all_ends.append(cursor + d / 100.0)
                cursor += d / 100.0

        if not all_words:
            continue

        for i in range(0, len(all_words), max_words):
            chunk_words = all_words[i:i + max_words]
            chunk_durs = all_durations[i:i + max_words]
            chunk_start = all_starts[i]
            chunk_end = all_ends[min(i + max_words - 1, len(all_ends) - 1)]

            # Skip subtitle lines past the clip end
            if max_duration is not None and chunk_start >= max_duration:
                continue

            tagged = "".join(
                f"{{\\k{d}}}{w} " for w, d in zip(chunk_words, chunk_durs)
            ).rstrip()

            start_ts = _format_ass_time(chunk_start)
            end_ts = _format_ass_time(chunk_end)
            lines.append(f"Dialogue: 0,{start_ts},{end_ts},Default,,0,0,0,,{tagged}")

    with open(path, "w", encoding="utf-8") as f:
        f.write(_ass_header())
        f.write("\n".join(lines))
        f.write("\n")


# ---------------------------------------------------------------------------
# 9:16 crop with karaoke subtitles
# ---------------------------------------------------------------------------

def create_short_with_karaoke(input_path, output_path, segments=None,
                               topic_start=0.0, hook_duration=0.0, max_duration=59.0):
    """Create a 9:16 short with face-centered crop and karaoke subtitles."""
    import cv2

    face_pos = _detect_face_center(input_path)

    if face_pos:
        cap = cv2.VideoCapture(input_path)
        vid_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        vid_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap.release()
        crop_w = int(vid_h * 9 / 16)
        crop_h = vid_h
        face_x_px = int(face_pos[0] * vid_w)
        crop_x = max(0, min(face_x_px - crop_w // 2, vid_w - crop_w))
        crop_filter = f"crop={crop_w}:{crop_h}:{crop_x}:0,scale=1080:1920"
    else:
        crop_filter = "crop=ih*9/16:ih,scale=1080:1920"

    ass_path = None
    try:
        if segments:
            offset = -topic_start + hook_duration
            ass_path = tempfile.mktemp(suffix=".ass")
            write_karaoke_ass(segments, offset, ass_path, max_duration=max_duration)
            escaped = ass_path.replace("\\", "\\\\").replace(":", "\\:")
            vf = f"{crop_filter},ass={escaped}"
        else:
            vf = crop_filter

        _run_ffmpeg([
            "-i", input_path,
            "-t", str(max_duration),
            "-vf", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
            output_path,
        ])
    finally:
        if ass_path and os.path.exists(ass_path):
            os.unlink(ass_path)


# ---------------------------------------------------------------------------
# SRT generation (from video_pipeline_v2.py)
# ---------------------------------------------------------------------------

def _format_srt_time(seconds):
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def generate_srt(segments, output_path, offset=0.0):
    """Generate SRT from Whisper segments with offset adjustment."""
    MAX_CHARS = 42
    all_words = []
    for seg in segments:
        for w in seg.get("words", []):
            all_words.append(w)

    if not all_words:
        srt_lines = []
        for i, seg in enumerate(segments, 1):
            start_ts = _format_srt_time(max(0, seg["start"] + offset))
            end_ts = _format_srt_time(max(0, seg["end"] + offset))
            srt_lines.extend([str(i), f"{start_ts} --> {end_ts}", seg["text"].strip(), ""])
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(srt_lines))
        return output_path

    subtitle_entries = []
    current_words = []
    current_text = ""

    for word in all_words:
        word_text = word["word"].strip()
        if not word_text:
            continue
        candidate = (current_text + " " + word_text).strip() if current_text else word_text
        if current_text and len(candidate) > MAX_CHARS:
            subtitle_entries.append({
                "start": current_words[0]["start"] + offset,
                "end": current_words[-1]["end"] + offset,
                "text": current_text,
            })
            current_words = [word]
            current_text = word_text
        else:
            current_words.append(word)
            current_text = candidate
        if current_text and re.search(r'[.!?]$', word_text) and len(current_words) >= 1:
            subtitle_entries.append({
                "start": current_words[0]["start"] + offset,
                "end": current_words[-1]["end"] + offset,
                "text": current_text,
            })
            current_words = []
            current_text = ""

    if current_words:
        subtitle_entries.append({
            "start": current_words[0]["start"] + offset,
            "end": current_words[-1]["end"] + offset,
            "text": current_text,
        })

    srt_lines = []
    for i, entry in enumerate(subtitle_entries, 1):
        start_ts = _format_srt_time(max(0, entry["start"]))
        end_ts = _format_srt_time(max(0, entry["end"]))
        srt_lines.extend([str(i), f"{start_ts} --> {end_ts}", entry["text"], ""])

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(srt_lines))
    return output_path


# ---------------------------------------------------------------------------
# Translation (from video_pipeline_v2.py, with model routing)
# ---------------------------------------------------------------------------

def _parse_srt(srt_content):
    blocks = re.split(r"\n\n+", srt_content.strip())
    entries = []
    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) >= 3:
            entries.append((lines[0].strip(), lines[1].strip(), "\n".join(lines[2:])))
    return entries


def _reassemble_srt(entries):
    return "\n\n".join(f"{idx}\n{ts}\n{txt}" for idx, ts, txt in entries) + "\n"


def _parse_numbered_lines(text):
    result = {}
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        match = re.match(r"^(\d+)\.\s*(.+)$", line)
        if match:
            result[int(match.group(1))] = match.group(2)
    return result


def translate_srt(srt_path, lang_code, output_dir):
    """Two-pass translation with model routing based on language."""
    lang_cfg = LANGUAGES[lang_code]
    language_name = lang_cfg["name"]
    register = lang_cfg["register"]

    # Route Chinese to Ollama/Qwen, romance languages to LiteLLM
    if lang_code == "zh-CN":
        client = openai.OpenAI(base_url=CHINESE_MODEL_URL, api_key=CHINESE_MODEL_KEY, timeout=300)
        model = CHINESE_MODEL
    else:
        client = openai.OpenAI(base_url=ROMANCE_MODEL_URL, api_key=ROMANCE_MODEL_KEY, timeout=180)
        model = ROMANCE_MODEL

    with open(srt_path, "r", encoding="utf-8") as f:
        srt_content = f.read()

    entries = _parse_srt(srt_content)
    numbered_lines = [f"{i+1}. {text}" for i, (_, _, text) in enumerate(entries)]
    text_block = "\n".join(numbered_lines)
    num_lines = len(entries)

    # Pass 1 — Accuracy
    pass1_system = (
        f"You are a professional translator specializing in {language_name}. "
        f"Translate each numbered line from English to {language_name}. "
        f"Use a {register} register — write as a native {language_name} speaker would naturally say it, "
        f"not as a word-for-word translation. Adapt idioms and phrasing to sound natural. "
        f"CRITICAL: You must return EXACTLY {num_lines} numbered lines (1 through {num_lines}). "
        f"Each line must start with its number followed by a period (e.g. '1. translated text'). "
        f"Do not merge, split, skip, or reorder any lines."
    )
    print(f"    [{lang_code}] Pass 1 (accuracy) via {model}...")
    pass1_response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": pass1_system},
            {"role": "user", "content": text_block},
        ],
    )
    pass1_output = pass1_response.choices[0].message.content

    # Pass 2 — Fluency
    pass2_system = (
        f"You are a native {language_name} speaker and professional translation editor. "
        f"Below you will see the original English text and a {language_name} translation. "
        f"Review the translation for natural flow and fluency. "
        f"Fix stiff or literal phrasing. Maintain a {register} register. "
        f"CRITICAL: Return EXACTLY {num_lines} numbered lines (1 through {num_lines}). "
        f"Each line must start with its number followed by a period. "
        f"Do not merge, split, skip, or reorder any lines."
    )
    pass2_user = f"ORIGINAL ENGLISH:\n{text_block}\n\nTRANSLATION TO REVIEW:\n{pass1_output}"
    print(f"    [{lang_code}] Pass 2 (fluency) via {model}...")
    pass2_response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": pass2_system},
            {"role": "user", "content": pass2_user},
        ],
    )
    pass2_output = pass2_response.choices[0].message.content

    translated_map = _parse_numbered_lines(pass2_output)

    # Retry missing lines
    missing = [i + 1 for i in range(num_lines) if (i + 1) not in translated_map]
    if missing:
        print(f"    [{lang_code}] Retrying {len(missing)} missing lines...")
        missing_lines = "\n".join(f"{n}. {entries[n - 1][2]}" for n in missing)
        retry_response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": f"Translate these numbered lines to {language_name}. Keep original numbering."},
                {"role": "user", "content": missing_lines},
            ],
        )
        translated_map.update(_parse_numbered_lines(retry_response.choices[0].message.content))

    translated_entries = []
    for i, (index, timestamp, original_text) in enumerate(entries):
        line_num = i + 1
        translated_entries.append((index, timestamp, translated_map.get(line_num, original_text)))

    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"subtitles_{lang_code}.srt")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(_reassemble_srt(translated_entries))

    return output_path


# ---------------------------------------------------------------------------
# Subtitle burn-in for translations (SRT, not ASS)
# ---------------------------------------------------------------------------

def burn_translated_srt(video_path, srt_path, lang_code, output_path):
    """Burn SRT subtitles onto the already-cropped 9:16 short."""
    font = LANGUAGES[lang_code]["font"]
    escaped_srt = str(srt_path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    force_style = (
        f"FontName={font},FontSize=48,PrimaryColour=&H00FFFFFF,"
        f"OutlineColour=&H80000000,BorderStyle=3,Outline=2,Shadow=1,MarginV=120"
    )
    subtitle_filter = f"subtitles='{escaped_srt}':force_style='{force_style}'"
    _run_ffmpeg([
        "-i", video_path,
        "-vf", subtitle_filter,
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-c:a", "copy",
        output_path,
    ])


# ---------------------------------------------------------------------------
# Video download
# ---------------------------------------------------------------------------

def _find_hook_segments(transcript, hook):
    """Find transcript segments that overlap with the hook time range."""
    segments = []
    for seg in transcript:
        mid = (seg["start"] + seg["end"]) / 2
        if mid >= hook["start"] and mid <= hook["end"]:
            segments.append(seg)
    # If no exact match, find the single best overlapping segment
    if not segments:
        for seg in transcript:
            if seg["start"] < hook["end"] and seg["end"] > hook["start"]:
                segments.append(seg)
                break
    return segments


def _offset_segment(seg, offset):
    """Return a copy of a segment with all timestamps shifted by offset."""
    new_seg = {
        "start": max(0, seg["start"] + offset),
        "end": max(0, seg["end"] + offset),
        "text": seg["text"],
    }
    if seg.get("words"):
        new_seg["words"] = [
            {"word": w["word"], "start": max(0, w["start"] + offset), "end": max(0, w["end"] + offset)}
            for w in seg["words"]
        ]
    return new_seg


def download_video(url, output_dir):
    """Download video via yt-dlp."""
    os.makedirs(output_dir, exist_ok=True)
    output_template = os.path.join(output_dir, "%(id)s.%(ext)s")
    result = subprocess.run(
        ["yt-dlp", "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
         "-o", output_template, "--merge-output-format", "mp4", url],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp failed: {result.stderr}")
    for f in os.listdir(output_dir):
        if f.endswith(".mp4"):
            return os.path.join(output_dir, f)
    raise RuntimeError("No mp4 found after download")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Smoke test: shorts + translations")
    parser.add_argument("url", help="YouTube URL")
    parser.add_argument("--max-clips", type=int, default=2, help="Max clips to produce")
    parser.add_argument("--output-dir", default=os.path.expanduser("~/Documents/smoke-test-shorts"),
                        help="Output directory")
    args = parser.parse_args()

    output_dir = args.output_dir
    os.makedirs(output_dir, exist_ok=True)

    # Step 1: Download
    print(f"[1/6] Downloading video...")
    cache_dir = os.path.join(output_dir, "cache")
    video_path = download_video(args.url, cache_dir)
    print(f"  → {video_path}")

    # Step 2: Transcribe (use cache or OpenAI Whisper API)
    transcript_path = os.path.join(output_dir, "transcript.json")
    if os.path.exists(transcript_path):
        print(f"[2/6] Loading cached transcript...")
        with open(transcript_path) as f:
            transcript = json.load(f)
        print(f"  → {len(transcript)} segments from cache")
    else:
        print(f"[2/6] Transcribing with OpenAI Whisper API...")
        audio_path = os.path.join(cache_dir, "audio.mp3")
        subprocess.run([
            FFMPEG, "-y", "-i", video_path,
            "-vn", "-ar", "16000", "-ac", "1", "-b:a", "32k", audio_path,
        ], check=True, capture_output=True)
        oai_client = openai.OpenAI()  # uses OPENAI_API_KEY env var
        with open(audio_path, "rb") as af:
            resp = oai_client.audio.transcriptions.create(
                model="whisper-1", file=af, response_format="verbose_json",
                timestamp_granularities=["word", "segment"],
            )
        transcript = []
        for seg in resp.segments:
            transcript.append({
                "start": seg.start, "end": seg.end, "text": seg.text,
                "words": [{"word": w.word, "start": w.start, "end": w.end}
                          for w in (seg.words or [])],
            })
        # If words aren't on segments, pull from top-level
        if transcript and not transcript[0].get("words"):
            all_words = [{"word": w.word, "start": w.start, "end": w.end}
                         for w in (resp.words or [])]
            for seg_data in transcript:
                seg_data["words"] = [
                    w for w in all_words
                    if w["start"] >= seg_data["start"] and w["end"] <= seg_data["end"] + 0.1
                ]
        with open(transcript_path, "w") as f:
            json.dump(transcript, f, ensure_ascii=False, indent=2)
        print(f"  → {len(transcript)} segments, saved to transcript.json")

    # Step 3: Segment topics (use cache or LLM)
    topics_path = os.path.join(output_dir, "topics.json")
    if os.path.exists(topics_path):
        print(f"[3/6] Loading cached topics...")
        with open(topics_path) as f:
            topics = json.load(f)
    else:
        print(f"[3/6] Segmenting topics via LLM...")
        topics = segment_topics(transcript)
        with open(topics_path, "w") as f:
            json.dump(topics, f, ensure_ascii=False, indent=2)
    print(f"  → Found {len(topics)} topics")
    for i, t in enumerate(topics):
        dur = t["end"] - t["start"]
        print(f"    {i+1}. [{dur:.0f}s] {t['topic']}")

    # Limit to max-clips
    topics = topics[:args.max_clips]

    # Step 4: Create shorts
    print(f"[4/6] Creating {len(topics)} shorts (face crop + English karaoke)...")
    shorts = []
    for i, topic in enumerate(topics):
        topic_dir = os.path.join(output_dir, f"clip-{i+1}")
        os.makedirs(topic_dir, exist_ok=True)

        print(f"  Clip {i+1}: {topic['topic']}")

        # Find hook
        print(f"    Finding hook sentence...")
        hook = select_hook(topic)
        print(f"    → Hook: \"{hook['sentence']}\"")

        # Extract segment
        segment_path = os.path.join(topic_dir, "segment.mp4")
        extract_segment(video_path, topic["start"], topic["end"], segment_path)

        # Extract hook clip
        hook_clip_path = os.path.join(topic_dir, "hook.mp4")
        extract_segment(video_path, hook["start"], hook["end"], hook_clip_path)

        # Prepend hook
        final_path = os.path.join(topic_dir, "final.mp4")
        prepend_hook(hook_clip_path, segment_path, final_path)
        hook_dur = get_clip_duration(hook_clip_path)

        # Build combined segments: hook words (placed at t=0) + topic words (shifted)
        # This ensures subtitles cover the entire clip from the first frame.
        hook_segments = _find_hook_segments(transcript, hook)
        topic_offset = -topic["start"] + hook_dur
        hook_offset = -hook["start"]  # shift hook words to start at t=0

        combined_segments = []
        # Hook segments first (shifted to start of clip)
        for seg in hook_segments:
            combined_segments.append(_offset_segment(seg, hook_offset))
        # Topic segments after (shifted to account for hook prepend)
        for seg in topic.get("segments", []):
            combined_segments.append(_offset_segment(seg, topic_offset))

        # Create 9:16 short with English karaoke
        short_en_path = os.path.join(topic_dir, "short_en_karaoke.mp4")
        print(f"    Creating 9:16 short with karaoke subtitles...")
        create_short_with_karaoke(
            final_path, short_en_path,
            segments=combined_segments,
            topic_start=0.0,  # segments are already offset
            hook_duration=0.0,  # already accounted for
        )
        print(f"    → {short_en_path}")

        # Generate English SRT for translation base (also using combined segments)
        srt_en_path = os.path.join(topic_dir, "subtitles_en.srt")
        generate_srt(combined_segments, srt_en_path, offset=0.0)

        shorts.append({
            "topic": topic["topic"],
            "dir": topic_dir,
            "short_en": short_en_path,
            "srt_en": srt_en_path,
            "final_video": final_path,
            "hook_dur": hook_dur,
            "topic_start": topic["start"],
        })

    # Step 5: Translate
    print(f"[5/6] Translating subtitles to {len(LANGUAGES)} languages...")
    for clip in shorts:
        print(f"  Clip: {clip['topic']}")
        # Translate all languages (romance in parallel, Chinese separately)
        romance_langs = ["fr", "es", "pt-BR"]
        clip["translated_srts"] = {}

        # Romance languages in parallel
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            futures = {
                executor.submit(translate_srt, clip["srt_en"], lang, clip["dir"]): lang
                for lang in romance_langs
            }
            for future in concurrent.futures.as_completed(futures):
                lang = futures[future]
                clip["translated_srts"][lang] = future.result()
                print(f"    → {lang}: done")

        # Chinese (Qwen via Ollama — sequential, big model)
        clip["translated_srts"]["zh-CN"] = translate_srt(clip["srt_en"], "zh-CN", clip["dir"])
        print(f"    → zh-CN: done")

    # Step 6: Burn translated subtitles
    print(f"[6/6] Burning translated subtitles...")

    # We need a 9:16 crop WITHOUT English karaoke to burn translations onto
    for clip in shorts:
        print(f"  Clip: {clip['topic']}")

        # Create a clean 9:16 crop (no subtitles) for translated versions
        clean_short = os.path.join(clip["dir"], "short_clean.mp4")
        create_short_with_karaoke(
            clip["final_video"], clean_short,
            segments=None,  # no subtitles
            topic_start=clip["topic_start"],
            hook_duration=clip["hook_dur"],
        )

        for lang, srt_path in clip["translated_srts"].items():
            output_path = os.path.join(clip["dir"], f"short_{lang}.mp4")
            print(f"    Burning {lang}...")
            burn_translated_srt(clean_short, srt_path, lang, output_path)
            print(f"    → {output_path}")

        # Clean up intermediate clean short
        os.remove(clean_short)

    # Summary
    print("\n" + "=" * 60)
    print("SMOKE TEST COMPLETE")
    print("=" * 60)
    for clip in shorts:
        print(f"\n{clip['topic']}:")
        print(f"  English (karaoke): {clip['short_en']}")
        for lang in ["fr", "es", "pt-BR", "zh-CN"]:
            path = os.path.join(clip["dir"], f"short_{lang}.mp4")
            if os.path.exists(path):
                print(f"  {LANGUAGES[lang]['name']}: {path}")


if __name__ == "__main__":
    main()
