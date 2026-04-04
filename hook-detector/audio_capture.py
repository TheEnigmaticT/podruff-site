"""Audio capture module — captures audio from BlackHole virtual audio device."""

import sys
import time
import struct
import array
import threading
import logging
from typing import Callable, Optional

import pyaudio

import config

logger = logging.getLogger(__name__)


def find_blackhole_device(pa: pyaudio.PyAudio) -> Optional[int]:
    """Find the BlackHole 2ch audio device index."""
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        if config.AUDIO_DEVICE in info["name"] and info["maxInputChannels"] > 0:
            return i
    return None


class AudioCapture:
    """Captures audio from BlackHole in fixed-duration chunks."""

    def __init__(self, on_chunk: Callable[[bytes, float], None]):
        """
        Args:
            on_chunk: Callback receiving (audio_bytes, start_timestamp) for each chunk.
        """
        self.on_chunk = on_chunk
        self._pa: Optional[pyaudio.PyAudio] = None
        self._stream = None
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start capturing audio in a background thread."""
        if self._running:
            return
        self._pa = pyaudio.PyAudio()
        device_index = find_blackhole_device(self._pa)
        if device_index is None:
            self._pa.terminate()
            raise RuntimeError(
                f"BlackHole not detected. Install from https://existential.audio/blackhole/ "
                f"and configure it as an audio output device."
            )

        frames_per_chunk = int(config.SAMPLE_RATE * config.CHUNK_DURATION_SECONDS)
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, args=(device_index, frames_per_chunk), daemon=True)
        self._thread.start()
        logger.info("Audio capture started on device index %d", device_index)

    def _capture_loop(self, device_index: int, frames_per_chunk: int) -> None:
        """Continuously read audio chunks and pass them to the callback."""
        # Use a smaller read size and accumulate to frames_per_chunk
        read_size = 1024
        try:
            self._stream = self._pa.open(
                format=pyaudio.paInt16,
                channels=config.CHANNELS,
                rate=config.SAMPLE_RATE,
                input=True,
                input_device_index=device_index,
                frames_per_buffer=read_size,
            )
        except Exception as e:
            logger.error("Failed to open audio stream: %s", e)
            self._running = False
            return

        buffer = bytearray()
        chunk_start = time.time()
        silence_start = None

        while self._running:
            try:
                data = self._stream.read(read_size, exception_on_overflow=False)
                buffer.extend(data)

                # Silence detection: compute RMS of this read
                samples = array.array("h", data)
                rms = (sum(s * s for s in samples) / len(samples)) ** 0.5 if samples else 0
                if rms < config.SILENCE_THRESHOLD:
                    if silence_start is None:
                        silence_start = time.time()
                    elif time.time() - silence_start >= config.SILENCE_WARNING_SECONDS:
                        logger.warning("Audio dropout: silence detected for >%ds", config.SILENCE_WARNING_SECONDS)
                        silence_start = time.time()  # Reset to avoid spamming
                else:
                    silence_start = None

                # Each frame = CHANNELS * SAMPLE_WIDTH bytes
                bytes_per_chunk = frames_per_chunk * config.CHANNELS * config.SAMPLE_WIDTH
                if len(buffer) >= bytes_per_chunk:
                    chunk_bytes = bytes(buffer[:bytes_per_chunk])
                    buffer = buffer[bytes_per_chunk:]
                    try:
                        self.on_chunk(chunk_bytes, chunk_start)
                    except Exception as e:
                        logger.error("Error in chunk callback: %s", e)
                    chunk_start = time.time()
            except IOError as e:
                logger.warning("Audio read error (device disconnection?): %s — retrying", e)
                time.sleep(0.5)

        if self._stream:
            self._stream.stop_stream()
            self._stream.close()

    def stop(self) -> None:
        """Stop capturing audio."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        if self._pa:
            self._pa.terminate()
        logger.info("Audio capture stopped")

    @property
    def is_running(self) -> bool:
        return self._running


# ---------- CLI test mode ----------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true", help="Run capture test (prints a message per chunk)")
    args = parser.parse_args()

    if args.test:
        logging.basicConfig(level=logging.INFO)
        chunks_received = 0

        def _test_callback(audio_bytes: bytes, ts: float):
            global chunks_received
            chunks_received += 1
            duration = len(audio_bytes) / (config.SAMPLE_RATE * config.CHANNELS * config.SAMPLE_WIDTH)
            print(f"Captured {duration:.1f}s chunk (#{chunks_received})")

        cap = AudioCapture(on_chunk=_test_callback)
        try:
            cap.start()
            # Run for 65 seconds to capture 6 chunks
            time.sleep(65)
        except KeyboardInterrupt:
            pass
        finally:
            cap.stop()
        print(f"Total chunks captured: {chunks_received}")
        sys.exit(0 if chunks_received >= 6 else 1)
