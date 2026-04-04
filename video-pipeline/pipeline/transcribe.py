"""Transcription using Parakeet-MLX (NVIDIA Parakeet TDT 0.6B v3 on Apple Silicon).

Falls back to OpenAI Whisper API if parakeet-mlx is unavailable.
"""

import os
import subprocess
import tempfile

_model = None
PARAKEET_MODEL_ID = "mlx-community/parakeet-tdt-0.6b-v3"
CHUNK_DURATION = 300  # 5-minute chunks for long audio


def _get_parakeet():
    global _model
    if _model is None:
        from parakeet_mlx import from_pretrained
        _model = from_pretrained(PARAKEET_MODEL_ID)
    return _model


def _merge_tokens_to_words(tokens) -> list[dict]:
    """Merge BPE subword tokens into full words with timing."""
    words = []
    current_text = ""
    current_start = None
    current_end = None

    for tok in tokens:
        text = tok.text
        # Token starting with space = new word boundary
        if text.startswith(" ") and current_text:
            words.append({
                "word": current_text.strip(),
                "start": current_start,
                "end": current_end,
            })
            current_text = text
            current_start = tok.start
            current_end = tok.end
        else:
            if current_start is None:
                current_start = tok.start
            current_text += text
            current_end = tok.end

    if current_text.strip():
        words.append({
            "word": current_text.strip(),
            "start": current_start,
            "end": current_end,
        })

    return [w for w in words if w["word"]]


def _extract_audio(video_path: str) -> str:
    """Extract audio to mono 16kHz MP3 for transcription."""
    audio_path = tempfile.mktemp(suffix=".mp3")
    subprocess.run([
        "/opt/homebrew/bin/ffmpeg", "-y", "-i", video_path,
        "-vn", "-ar", "16000", "-ac", "1", "-b:a", "32k", audio_path,
    ], check=True, capture_output=True)
    return audio_path


def transcribe_video(video_path: str) -> list[dict]:
    """Transcribe a video file and return timestamped segments with word timing.

    Uses Parakeet-MLX for fast local transcription on Apple Silicon.
    Returns list of dicts with keys: start, end, text, words.
    Each word dict has: word, start, end.
    """
    model = _get_parakeet()

    # Extract audio if input is a video file
    ext = os.path.splitext(video_path)[1].lower()
    if ext in (".mp3", ".wav", ".flac", ".ogg", ".m4a"):
        audio_path = video_path
        cleanup = False
    else:
        audio_path = _extract_audio(video_path)
        cleanup = True

    try:
        result = model.transcribe(
            audio_path,
            chunk_duration=CHUNK_DURATION,
            overlap_duration=15.0,
        )
    finally:
        if cleanup and os.path.exists(audio_path):
            os.unlink(audio_path)

    segments = []
    for sentence in result.sentences:
        words = _merge_tokens_to_words(sentence.tokens)
        segments.append({
            "start": sentence.start,
            "end": sentence.end,
            "text": sentence.text.strip(),
            "words": words,
        })

    return segments
