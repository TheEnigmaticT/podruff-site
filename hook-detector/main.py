"""Main orchestrator — ties audio capture, transcription, all generators, and web UI together."""

import os
import signal
import sys
import time
import logging
import threading
from datetime import datetime
from typing import Dict, Optional

import uvicorn

import config
from audio_capture import AudioCapture
from transcriber import Transcriber
from hook_generator import HookGenerator
from topic_flagger import TopicFlagger
from followup_generator import FollowupGenerator
from suggestion_engine import SuggestionStore
from post_call import generate_post_call_analysis, format_markdown_output
from web_server import app, set_components, _app_state, broadcast_suggestion

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Shared instances
transcriber = Transcriber()
hook_generator = HookGenerator()
topic_flagger = TopicFlagger()
followup_generator = FollowupGenerator()
store = SuggestionStore()
store.register(hook_generator)
store.register(topic_flagger)
store.register(followup_generator)

audio_capture: Optional[AudioCapture] = None

# Pipeline state
_pipeline_running = False
_generation_thread: Optional[threading.Thread] = None
_all_segments = []  # Full transcript for saving (not trimmed)


def on_audio_chunk(audio_bytes: bytes, chunk_start: float) -> None:
    """Called by AudioCapture for each 10-second chunk."""
    text = transcriber.transcribe_chunk(audio_bytes, chunk_start)
    if text:
        _all_segments.append({"text": text, "timestamp": chunk_start})
        logger.info("Transcribed: %s", text[:60])


def _generation_loop() -> None:
    """
    Background loop that generates all suggestion types on a staggered schedule.
    Within each GENERATION_INTERVAL_SECONDS window:
      - Hooks fire at offset +0s
      - Topics fire at offset +3s
      - Follow-ups fire at offset +6s
    """
    global _pipeline_running
    while _pipeline_running:
        cycle_start = time.time()
        transcript = transcriber.get_buffer_text()

        if transcript.strip():
            # Hooks at t+0
            _generate_and_broadcast(hook_generator, transcript, "hooks")

            # Topics at t+3
            _sleep_until(cycle_start + config.TOPIC_OFFSET_SECONDS)
            if not _pipeline_running:
                break
            transcript = transcriber.get_buffer_text()  # refresh
            _generate_and_broadcast(topic_flagger, transcript, "topics")

            # Follow-ups at t+6
            _sleep_until(cycle_start + config.FOLLOWUP_OFFSET_SECONDS)
            if not _pipeline_running:
                break
            transcript = transcriber.get_buffer_text()  # refresh
            _generate_and_broadcast(followup_generator, transcript, "follow-ups")

        # Sleep until next cycle
        _sleep_until(cycle_start + config.GENERATION_INTERVAL_SECONDS)


def _generate_and_broadcast(generator, transcript, label):
    """Run generation and broadcast results via SSE."""
    suggestions = generator.generate(transcript)
    if suggestions:
        logger.info("Generated %d %s: %s", len(suggestions), label,
                     [s.text[:40] for s in suggestions])
        for s in suggestions:
            broadcast_suggestion(s)


def _sleep_until(target_time):
    """Sleep until target_time, checking _pipeline_running."""
    remaining = target_time - time.time()
    if remaining > 0:
        time.sleep(remaining)


def start_pipeline() -> None:
    """Start audio capture and suggestion generation."""
    global audio_capture, _pipeline_running, _generation_thread, _all_segments

    if _pipeline_running:
        return

    _all_segments = []
    audio_capture = AudioCapture(on_chunk=on_audio_chunk)
    audio_capture.start()

    _pipeline_running = True
    _generation_thread = threading.Thread(target=_generation_loop, daemon=True)
    _generation_thread.start()

    _app_state["audio_capture"] = audio_capture
    logger.info("Pipeline started — hooks, topics, and follow-ups active")


def stop_pipeline() -> Dict:
    """Stop audio capture, run post-call analysis, save output, return result dict."""
    global _pipeline_running, audio_capture

    _pipeline_running = False

    if audio_capture:
        audio_capture.stop()

    start_time = _app_state.get("start_time") or time.time()
    all_suggestions = store.get_all()

    # Build full transcript text for analysis
    full_transcript = "\n".join(seg["text"] for seg in _all_segments)

    # Run post-call analysis
    analysis = None
    if full_transcript.strip() and all_suggestions:
        logger.info("Running post-call analysis...")
        analysis = generate_post_call_analysis(full_transcript, all_suggestions, start_time)

        # Auto-mark suggestions that appeared in transcript
        if analysis and analysis.get("said_ids"):
            for sid in analysis["said_ids"]:
                from suggestion_engine import SuggestionStatus
                store.tag(sid, SuggestionStatus.USED)
            logger.info("Auto-tagged %d suggestions as SAID", len(analysis["said_ids"]))

    # Save output
    filepath = _save_output(all_suggestions, start_time, analysis)
    logger.info("Pipeline stopped. Output saved to %s", filepath)

    return {"filepath": filepath, "analysis": analysis}


def _save_output(suggestions, start_time, analysis) -> str:
    """Save all suggestions and transcript to a Markdown file."""
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    now = datetime.now()
    filename = now.strftime("%Y-%m-%d-%H%M%S") + ".md"
    filepath = os.path.join(config.OUTPUT_DIR, filename)

    content = format_markdown_output(
        suggestions=suggestions,
        transcript_segments=_all_segments,
        start_time=start_time,
        analysis=analysis,
    )

    with open(filepath, "w") as f:
        f.write(content)

    return filepath


def _shutdown_handler(signum, frame):
    """Handle Ctrl+C: save output if recording, then exit."""
    logger.info("Shutdown signal received")
    if _pipeline_running:
        result = stop_pipeline()
        logger.info("Recording saved before shutdown: %s", result.get("filepath"))
    sys.exit(0)


def main():
    """Entry point — initialize components and start the web server."""
    signal.signal(signal.SIGINT, _shutdown_handler)
    signal.signal(signal.SIGTERM, _shutdown_handler)

    # Load Whisper model
    logger.info("Loading Whisper model...")
    transcriber.load_model()

    # Check Ollama connectivity
    logger.info("Checking Ollama...")
    hook_generator.check_ollama()

    # Register shared components with the web server
    set_components(transcriber, hook_generator, store)

    logger.info("Starting web server on http://localhost:%d", config.WEB_PORT)
    uvicorn.run(app, host=config.WEB_HOST, port=config.WEB_PORT, log_level="info")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Live Call Hook Assistant")
    parser.add_argument("--transcribe-only", action="store_true",
                       help="Run transcription without generation or web UI")
    args = parser.parse_args()

    if args.transcribe_only:
        logger.info("Loading Whisper model...")
        transcriber.load_model()

        def _console_callback(audio_bytes, ts):
            text = transcriber.transcribe_chunk(audio_bytes, ts)
            if text:
                elapsed = time.time() - _start
                from post_call import _format_elapsed
                print(f"[{_format_elapsed(elapsed)}] {text}")

        _start = time.time()
        cap = AudioCapture(on_chunk=_console_callback)
        try:
            cap.start()
            logger.info("Listening... Press Ctrl+C to stop.")
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            cap.stop()
    else:
        main()
