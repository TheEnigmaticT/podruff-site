#!/usr/bin/env python3
"""Video translation pipeline v2 — Whisper transcription + LLM translation + subtitle burn-in."""

import argparse
import concurrent.futures
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

import openai
import whisper

# ---------------------------------------------------------------------------
# LiteLLM proxy configuration
# ---------------------------------------------------------------------------
LITELLM_URL = "http://localhost:13668/v1"
LITELLM_KEY = "sk-litellm-98976c4db5c793dfac352ba1877254f7d3465e975d55155e"
LITELLM_MODEL = "botty-gpt4.1-mini"

# ---------------------------------------------------------------------------
# Language configuration
# ---------------------------------------------------------------------------
LANGUAGE_CONFIG = {
    "es": {
        "name": "Spanish",
        "register": "Neutral Latin-American Spanish, conversational and clear",
        "font": "Arial",
    },
    "es-ES": {
        "name": "European Spanish",
        "register": "Castilian Spanish, professional yet approachable",
        "font": "Arial",
    },
    "zh-CN": {
        "name": "Simplified Chinese",
        "register": "Mainland Mandarin, professional and direct",
        "font": "Noto Sans CJK SC",
    },
    "zh-TW": {
        "name": "Traditional Chinese",
        "register": "Taiwanese Mandarin, professional and polished",
        "font": "Noto Sans CJK TC",
    },
    "fr": {
        "name": "French",
        "register": "Metropolitan French, professional yet warm",
        "font": "Arial",
    },
    "de": {
        "name": "German",
        "register": "Standard High German, professional and precise",
        "font": "Arial",
    },
    "pt-BR": {
        "name": "Brazilian Portuguese",
        "register": "Brazilian Portuguese, conversational and engaging",
        "font": "Arial",
    },
    "ar": {
        "name": "Arabic",
        "register": "Modern Standard Arabic, clear and professional",
        "font": "Noto Sans Arabic",
    },
    "ja": {
        "name": "Japanese",
        "register": "Standard Japanese, polite and professional (desu/masu style)",
        "font": "Noto Sans CJK JP",
    },
    "ko": {
        "name": "Korean",
        "register": "Standard Korean, polite and professional (hapshoche style)",
        "font": "Noto Sans CJK KR",
    },
}

# ---------------------------------------------------------------------------
# Whisper transcription
# ---------------------------------------------------------------------------

def transcribe(video_path, output_dir, whisper_model="large-v3"):
    """Transcribe a video file using local Whisper.

    Args:
        video_path: Path to the video file.
        output_dir: Directory where transcript.json will be saved.
        whisper_model: Whisper model size (default: large-v3).

    Returns:
        List of segment dicts, each with keys: start, end, text, words.
        Each word dict has keys: word, start, end.
    """
    model = whisper.load_model(whisper_model)
    result = model.transcribe(str(video_path), word_timestamps=True)

    segments = []
    for seg in result.get("segments", []):
        words = []
        for w in seg.get("words", []):
            words.append({
                "word": w["word"],
                "start": w["start"],
                "end": w["end"],
            })
        segments.append({
            "start": seg["start"],
            "end": seg["end"],
            "text": seg["text"],
            "words": words,
        })

    os.makedirs(output_dir, exist_ok=True)
    transcript_path = os.path.join(output_dir, "transcript.json")
    with open(transcript_path, "w", encoding="utf-8") as f:
        json.dump(segments, f, ensure_ascii=False, indent=2)

    return segments


# ---------------------------------------------------------------------------
# FFMPEG path
# ---------------------------------------------------------------------------
FFMPEG = "/opt/homebrew/bin/ffmpeg"


# ---------------------------------------------------------------------------
# Edit detection via LLM
# ---------------------------------------------------------------------------

EDIT_DETECTION_SYSTEM_PROMPT = """\
You are a professional video editor. You will receive a transcript with timestamps \
from a video recording. Your job is to identify segments that should be CUT to \
produce a clean, tight edit.

Look for:
- Filler words/phrases (um, uh, like, you know, so, basically) that should be trimmed
- False starts and retakes — when the speaker restarts a sentence, keep only the best take
- Long pauses (>2 seconds of silence between words) that should be tightened

Return a JSON object with a single key "edits" containing a list of segments that \
together cover the ENTIRE timeline of the video from start to finish. Each segment \
must have:
- "action": "keep" or "cut"
- "start": start time in seconds (float)
- "end": end time in seconds (float)
- "reason": brief explanation

The segments must be contiguous (no gaps, no overlaps) and sorted by start time. \
Every moment of the video must be accounted for. If nothing needs cutting, return \
the entire video as a single "keep" segment.

Return ONLY valid JSON, no markdown fences or extra text."""


def _format_transcript_for_llm(transcript):
    """Format transcript segments with timestamps for the LLM.

    Args:
        transcript: List of segment dicts from transcribe().

    Returns:
        A string with one line per segment, e.g.:
        [0.00-2.50] Hello everyone, welcome to...
    """
    lines = []
    for seg in transcript:
        start = seg["start"]
        end = seg["end"]
        text = seg["text"].strip()
        lines.append(f"[{start:.2f}-{end:.2f}] {text}")
    return "\n".join(lines)


def detect_edits(transcript, model=None):
    """Use an LLM to detect filler, retakes, and pauses that should be cut.

    Args:
        transcript: List of segment dicts from transcribe().
        model: LiteLLM model name (defaults to LITELLM_MODEL).

    Returns:
        List of dicts, each with keys: action, start, end, reason.
    """
    if model is None:
        model = LITELLM_MODEL

    formatted = _format_transcript_for_llm(transcript)

    client = openai.OpenAI(base_url=LITELLM_URL, api_key=LITELLM_KEY, timeout=120)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": EDIT_DETECTION_SYSTEM_PROMPT},
            {"role": "user", "content": formatted},
        ],
        response_format={"type": "json_object"},
    )

    result = json.loads(response.choices[0].message.content)
    return result["edits"]


# ---------------------------------------------------------------------------
# FFmpeg cut / stitch
# ---------------------------------------------------------------------------

def apply_edits(video_path, edits, output_dir):
    """Extract keep segments and concatenate them into an edited video.

    Args:
        video_path: Path to the source video file.
        edits: List of edit dicts (from detect_edits).
        output_dir: Directory where edited.mp4 will be saved.

    Returns:
        Path to the edited video file.
    """
    os.makedirs(output_dir, exist_ok=True)

    keep_segments = [e for e in edits if e["action"] == "keep"]
    if not keep_segments:
        raise ValueError("No keep segments found in edit list")

    with tempfile.TemporaryDirectory() as tmp_dir:
        segment_files = []
        for i, seg in enumerate(keep_segments):
            seg_path = os.path.join(tmp_dir, f"seg_{i:04d}.mp4")
            segment_files.append(seg_path)
            subprocess.run(
                [
                    FFMPEG, "-y",
                    "-ss", str(seg["start"]),
                    "-to", str(seg["end"]),
                    "-i", video_path,
                    "-c", "copy",
                    seg_path,
                ],
                check=True,
                capture_output=True,
            )

        concat_path = os.path.join(tmp_dir, "concat.txt")
        with open(concat_path, "w") as f:
            for seg_path in segment_files:
                f.write(f"file '{seg_path}'\n")

        output_path = os.path.join(output_dir, "edited.mp4")
        subprocess.run(
            [
                FFMPEG, "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", concat_path,
                "-c", "copy",
                output_path,
            ],
            check=True,
            capture_output=True,
        )

    return output_path


# ---------------------------------------------------------------------------
# SRT generation from transcript
# ---------------------------------------------------------------------------

def _format_srt_time(seconds):
    """Convert seconds (float) to SRT timestamp format HH:MM:SS,mmm."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def generate_srt(segments, output_path):
    """Convert Whisper transcript segments to an SRT subtitle file.

    Groups words into subtitle lines of approximately 42 characters max,
    respecting sentence boundaries where possible.

    Args:
        segments: List of segment dicts from transcribe(), each with
                  keys: start, end, text, words.
        output_path: Path where the SRT file will be written.

    Returns:
        The output_path.
    """
    # Flatten all words from all segments
    all_words = []
    for seg in segments:
        for w in seg.get("words", []):
            all_words.append(w)

    if not all_words:
        # If no word-level data, fall back to segment-level subtitles
        lines = []
        for i, seg in enumerate(segments, 1):
            start_ts = _format_srt_time(seg["start"])
            end_ts = _format_srt_time(seg["end"])
            lines.append(f"{i}")
            lines.append(f"{start_ts} --> {end_ts}")
            lines.append(seg["text"].strip())
            lines.append("")
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return output_path

    # Group words into subtitle entries (~42 chars max per line)
    MAX_CHARS = 42
    subtitle_entries = []
    current_words = []
    current_text = ""

    for word in all_words:
        word_text = word["word"].strip()
        if not word_text:
            continue

        candidate = (current_text + " " + word_text).strip() if current_text else word_text

        # Check if adding this word exceeds the limit
        if current_text and len(candidate) > MAX_CHARS:
            # Flush current group
            subtitle_entries.append({
                "start": current_words[0]["start"],
                "end": current_words[-1]["end"],
                "text": current_text,
            })
            current_words = [word]
            current_text = word_text
        else:
            current_words.append(word)
            current_text = candidate

        # Also break at sentence boundaries (., !, ?) even if under limit
        if current_text and re.search(r'[.!?]$', word_text) and len(current_words) >= 1:
            subtitle_entries.append({
                "start": current_words[0]["start"],
                "end": current_words[-1]["end"],
                "text": current_text,
            })
            current_words = []
            current_text = ""

    # Flush remaining words
    if current_words:
        subtitle_entries.append({
            "start": current_words[0]["start"],
            "end": current_words[-1]["end"],
            "text": current_text,
        })

    # Write SRT
    srt_lines = []
    for i, entry in enumerate(subtitle_entries, 1):
        start_ts = _format_srt_time(entry["start"])
        end_ts = _format_srt_time(entry["end"])
        srt_lines.append(f"{i}")
        srt_lines.append(f"{start_ts} --> {end_ts}")
        srt_lines.append(entry["text"])
        srt_lines.append("")

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(srt_lines))

    return output_path


# ---------------------------------------------------------------------------
# Two-pass translation
# ---------------------------------------------------------------------------

def _parse_srt(srt_content):
    """Parse SRT content into a list of (index, timestamp, text) tuples."""
    blocks = re.split(r"\n\n+", srt_content.strip())
    entries = []
    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) >= 3:
            index = lines[0].strip()
            timestamp = lines[1].strip()
            text = "\n".join(lines[2:])
            entries.append((index, timestamp, text))
    return entries


def _reassemble_srt(entries):
    """Reassemble SRT from (index, timestamp, text) tuples."""
    blocks = []
    for index, timestamp, text in entries:
        blocks.append(f"{index}\n{timestamp}\n{text}")
    return "\n\n".join(blocks) + "\n"


def _parse_numbered_lines(text):
    """Parse 'N. translated text' lines into a {line_number: text} dict."""
    result = {}
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        match = re.match(r"^(\d+)\.\s*(.+)$", line)
        if match:
            result[int(match.group(1))] = match.group(2)
    return result


def translate_srt(srt_path, language_code, output_dir, model=None):
    """Translate an English SRT file to another language using two LLM passes.

    Extracts only the text lines for translation, then reassembles with
    original timestamps to prevent the LLM from corrupting them.

    Pass 1 (Accuracy): Faithful translation.
    Pass 2 (Fluency): Review and polish for natural flow.

    Args:
        srt_path: Path to the English SRT file.
        language_code: Target language code (key in LANGUAGE_CONFIG).
        output_dir: Directory where the translated SRT will be saved.
        model: LiteLLM model name (defaults to LITELLM_MODEL).

    Returns:
        Path to the translated SRT file.
    """
    if model is None:
        model = LITELLM_MODEL

    lang_cfg = LANGUAGE_CONFIG[language_code]
    language_name = lang_cfg["name"]
    register = lang_cfg["register"]

    with open(srt_path, "r", encoding="utf-8") as f:
        srt_content = f.read()

    # Parse SRT and extract only text lines for translation
    entries = _parse_srt(srt_content)
    numbered_lines = [f"{i+1}. {text}" for i, (_, _, text) in enumerate(entries)]
    text_block = "\n".join(numbered_lines)

    client = openai.OpenAI(base_url=LITELLM_URL, api_key=LITELLM_KEY, timeout=180)

    # Pass 1 — Accuracy
    num_lines = len(entries)
    pass1_system = (
        f"You are a professional translator specializing in {language_name}. "
        f"Translate each numbered line from English to {language_name}. "
        f"Use a {register} register — write as a native {language_name} speaker would naturally say it, "
        f"not as a word-for-word translation. Adapt idioms and phrasing to sound natural. "
        f"CRITICAL: You must return EXACTLY {num_lines} numbered lines (1 through {num_lines}). "
        f"Each line must start with its number followed by a period (e.g. '1. translated text'). "
        f"Do not merge, split, skip, or reorder any lines."
    )
    pass1_response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": pass1_system},
            {"role": "user", "content": text_block},
        ],
    )
    pass1_output = pass1_response.choices[0].message.content

    # Pass 2 — Fluency (gets both English original and Pass 1 translation)
    pass2_system = (
        f"You are a native {language_name} speaker and professional translation editor. "
        f"Below you will see the original English text and a {language_name} translation. "
        f"Review the translation for natural flow and fluency. The translation should sound "
        f"like it was originally written in {language_name} by a native speaker — not like "
        f"a translation. Fix stiff or literal phrasing. Maintain a {register} register. "
        f"CRITICAL: Return EXACTLY {num_lines} numbered lines (1 through {num_lines}). "
        f"Each line must start with its number followed by a period. "
        f"Do not merge, split, skip, or reorder any lines."
    )
    pass2_user = (
        f"ORIGINAL ENGLISH:\n{text_block}\n\n"
        f"TRANSLATION TO REVIEW:\n{pass1_output}"
    )
    pass2_response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": pass2_system},
            {"role": "user", "content": pass2_user},
        ],
    )
    pass2_output = pass2_response.choices[0].message.content

    # Parse translated lines by number and reassemble with original timestamps
    translated_map = _parse_numbered_lines(pass2_output)

    # Retry missing lines up to 2 times
    missing = [i + 1 for i in range(num_lines) if (i + 1) not in translated_map]
    retries = 0
    while missing and retries < 2:
        retries += 1
        missing_lines = "\n".join(
            f"{n}. {entries[n - 1][2]}" for n in missing
        )
        retry_system = (
            f"You are a professional translator specializing in {language_name}. "
            f"Translate ONLY the following numbered English lines to {language_name}. "
            f"Use a {register} register — write as a native speaker would naturally say it. "
            f"Return each line with its ORIGINAL number prefix (e.g. '22. translated text'). "
            f"Do not skip, merge, or renumber any lines."
        )
        retry_response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": retry_system},
                {"role": "user", "content": missing_lines},
            ],
        )
        retry_map = _parse_numbered_lines(retry_response.choices[0].message.content)
        translated_map.update(retry_map)
        missing = [i + 1 for i in range(num_lines) if (i + 1) not in translated_map]

    # Reassemble: use original timestamps, translated text matched by number
    translated_entries = []
    for i, (index, timestamp, original_text) in enumerate(entries):
        line_num = i + 1
        if line_num in translated_map:
            translated_entries.append((index, timestamp, translated_map[line_num]))
        else:
            # Fallback: keep original text if translation missing for this line
            translated_entries.append((index, timestamp, original_text))

    output_srt = _reassemble_srt(translated_entries)

    # Save translated SRT
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"subtitles_{language_code}.srt")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(output_srt)

    return output_path


# ---------------------------------------------------------------------------
# Parallel translation
# ---------------------------------------------------------------------------

def translate_all(srt_path, languages, output_dir, model=None):
    """Translate an SRT file into multiple languages in parallel.

    Args:
        srt_path: Path to the English SRT file.
        languages: List of language codes (keys in LANGUAGE_CONFIG).
        output_dir: Directory where translated SRT files will be saved.
        model: LiteLLM model name (defaults to LITELLM_MODEL).

    Returns:
        Dict mapping language_code to the translated SRT file path.
    """
    results = {}

    def _do_translate(lang_code):
        return lang_code, translate_srt(srt_path, lang_code, output_dir, model=model)

    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = [executor.submit(_do_translate, lc) for lc in languages]
        for future in concurrent.futures.as_completed(futures):
            lang_code, path = future.result()
            results[lang_code] = path

    return results


# ---------------------------------------------------------------------------
# Subtitle burn-in
# ---------------------------------------------------------------------------

def burn_subtitles(video_path, srt_path, language_code, output_dir):
    """Burn subtitles into a video using FFmpeg's subtitles filter.

    Args:
        video_path: Path to the input video file.
        srt_path: Path to the SRT subtitle file.
        language_code: Language code (key in LANGUAGE_CONFIG) for font selection.
        output_dir: Directory where the output video will be saved.

    Returns:
        Path to the output video with burned-in subtitles.
    """
    font = LANGUAGE_CONFIG[language_code]["font"]
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"final_{language_code}.mp4")

    # FFmpeg subtitles filter requires escaping : \ ' in the filename
    escaped_srt = str(srt_path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    force_style = (
        f"FontName={font},FontSize=24,OutlineColour=&H40000000,"
        f"BorderStyle=3,Outline=2,Shadow=1,MarginV=30"
    )
    subtitle_filter = f"subtitles='{escaped_srt}':force_style='{force_style}'"

    subprocess.run(
        [
            FFMPEG, "-y",
            "-i", video_path,
            "-vf", subtitle_filter,
            "-c:a", "copy",
            output_path,
        ],
        check=True,
        capture_output=True,
    )

    return output_path


# ---------------------------------------------------------------------------
# HeyGen stub
# ---------------------------------------------------------------------------

def heygen_translate(video_path, language_code, output_dir):
    """Translate a video using HeyGen API (not yet configured).

    Args:
        video_path: Path to the input video file.
        language_code: Target language code.
        output_dir: Directory where the output would be saved.

    Raises:
        NotImplementedError: Always, until HeyGen API is configured.
    """
    raise NotImplementedError("HeyGen API not configured — upgrade plan first")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    """CLI entry point for the video translation pipeline."""
    parser = argparse.ArgumentParser(
        description="Video translation pipeline: transcribe, edit, translate, burn subtitles."
    )
    parser.add_argument("input_video", help="Path to the input video file")
    parser.add_argument(
        "--languages", nargs="+", required=True,
        help="Language codes to translate into (e.g. es fr de)",
    )
    parser.add_argument(
        "--heygen", nargs="*", default=[],
        help="Language codes to use HeyGen tier for",
    )
    parser.add_argument(
        "--skip-cuts", action="store_true",
        help="Skip edit detection and cutting",
    )
    parser.add_argument(
        "--no-interactive", action="store_true",
        help="Auto-approve edits (default for bot use)",
    )
    parser.add_argument(
        "--model", default=LITELLM_MODEL,
        help=f"LLM model to use (default: {LITELLM_MODEL})",
    )
    parser.add_argument(
        "--output-dir", default="~/dev/tmp/video-translations/",
        help="Base output directory",
    )
    parser.add_argument(
        "--whisper-model", default="large-v3",
        help="Whisper model size (default: large-v3)",
    )

    args = parser.parse_args()

    # Expand and create timestamped output directory
    base_dir = os.path.expanduser(args.output_dir)
    video_stem = Path(args.input_video).stem
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = os.path.join(base_dir, f"{video_stem}_{timestamp}")
    os.makedirs(run_dir, exist_ok=True)

    # Step 1: Transcribe
    print(f"Transcribing {args.input_video}...")
    transcript = transcribe(args.input_video, run_dir, args.whisper_model)

    # Step 2: Edit detection and cutting
    if not args.skip_cuts:
        print("Detecting edits...")
        edits = detect_edits(transcript, args.model)
        print("Applying edits...")
        video = apply_edits(args.input_video, edits, run_dir)
    else:
        video = args.input_video

    # Step 3: Generate English SRT
    srt_path = generate_srt(transcript, os.path.join(run_dir, "subtitles_en.srt"))

    # Step 4: Process each language
    output_files = {"srt_en": srt_path}

    for lang in args.languages:
        if lang in args.heygen:
            print(f"HeyGen translate: {lang}...")
            heygen_translate(video, lang, run_dir)
        else:
            print(f"Translating subtitles: {lang}...")
            translated = translate_srt(srt_path, lang, run_dir, args.model)
            print(f"Burning subtitles: {lang}...")
            final_video = burn_subtitles(video, translated, lang, run_dir)
            output_files[f"srt_{lang}"] = translated
            output_files[f"video_{lang}"] = final_video

    # Summary
    print("\n=== Output Summary ===")
    for label, path in sorted(output_files.items()):
        print(f"  {label}: {path}")
    print(f"  run_dir: {run_dir}")


if __name__ == "__main__":
    main()
