"""Transcription module — wraps whisper.cpp via pywhispercpp for real-time STT."""

import time
import logging
import tempfile
import wave
import threading
from dataclasses import dataclass, field
from typing import List, Optional

from pywhispercpp.model import Model as WhisperModel

import config

logger = logging.getLogger(__name__)


@dataclass
class TranscriptSegment:
    """A single transcribed chunk with timestamp."""
    text: str
    timestamp: float  # wall-clock time when chunk started


class Transcriber:
    """Manages Whisper model and a rolling transcript buffer."""

    def __init__(self):
        self._model: Optional[WhisperModel] = None
        self._segments: List[TranscriptSegment] = []
        self._lock = threading.Lock()

    def load_model(self) -> None:
        """Load the Whisper model. Call once at startup."""
        logger.info("Loading Whisper model '%s'...", config.WHISPER_MODEL)
        self._model = WhisperModel(config.WHISPER_MODEL)
        logger.info("Whisper model loaded.")

    def transcribe_chunk(self, audio_bytes: bytes, chunk_start: float) -> str:
        """
        Transcribe a raw PCM audio chunk and add to the rolling buffer.

        Args:
            audio_bytes: Raw 16-bit PCM mono audio at SAMPLE_RATE.
            chunk_start: Wall-clock timestamp for the start of this chunk.

        Returns:
            The transcribed text for this chunk.
        """
        if self._model is None:
            raise RuntimeError("Whisper model not loaded. Call load_model() first.")

        # pywhispercpp expects a wav file path — write a temp file
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
                with wave.open(tmp.name, "wb") as wf:
                    wf.setnchannels(config.CHANNELS)
                    wf.setsampwidth(config.SAMPLE_WIDTH)
                    wf.setframerate(config.SAMPLE_RATE)
                    wf.writeframes(audio_bytes)

                segments = self._model.transcribe(tmp.name)
                text = " ".join(seg.text.strip() for seg in segments if seg.text.strip())
        except Exception as e:
            logger.error("Whisper transcription failed on chunk, skipping: %s", e)
            return ""

        if text:
            segment = TranscriptSegment(text=text, timestamp=chunk_start)
            with self._lock:
                self._segments.append(segment)
                self._trim_buffer()
            logger.debug("Transcribed: %s", text[:80])

        return text

    def _trim_buffer(self) -> None:
        """Remove segments older than TRANSCRIPT_BUFFER_SECONDS."""
        cutoff = time.time() - config.TRANSCRIPT_BUFFER_SECONDS
        self._segments = [s for s in self._segments if s.timestamp >= cutoff]

    def get_buffer_text(self) -> str:
        """Return the full rolling transcript buffer as a single string."""
        with self._lock:
            return "\n".join(s.text for s in self._segments)

    def get_segments(self) -> List[TranscriptSegment]:
        """Return a copy of current transcript segments."""
        with self._lock:
            return list(self._segments)

    def get_full_transcript(self) -> List[TranscriptSegment]:
        """Return all segments (used at save time — does NOT trim)."""
        with self._lock:
            return list(self._segments)


# ---------- CLI test mode ----------
if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true")
    parser.add_argument("--file", type=str, help="Path to a .wav file to transcribe")
    args = parser.parse_args()

    if args.test:
        if not args.file:
            print("Usage: python transcriber.py --test --file <path_to_wav>")
            sys.exit(1)

        logging.basicConfig(level=logging.INFO)
        t = Transcriber()
        t.load_model()

        # Read the wav file
        with wave.open(args.file, "rb") as wf:
            audio_bytes = wf.readframes(wf.getnframes())

        start = time.time()
        text = t.transcribe_chunk(audio_bytes, time.time())
        elapsed = time.time() - start
        print(f"Transcription ({elapsed:.1f}s):\n{text}")
        sys.exit(0)
