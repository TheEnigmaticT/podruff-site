# Live Call Hook Assistant

A local Python application that captures audio from live calls, transcribes speech in real-time, and generates punchy hook ideas using AI.

## Quick Start

### Prerequisites

- Python 3.11+
- [BlackHole 2ch](https://existential.audio/blackhole/) installed and set as audio output
- [Ollama](https://ollama.ai/) running with Qwen3:30b: `ollama run qwen3:30b`

### Setup

```bash
pip install -r requirements.txt
```

### Run

```bash
python main.py
```

Then open http://localhost:8000 — click **Start Recording** to begin.

### Test individual modules

```bash
# Audio capture test (60 seconds)
python audio_capture.py --test

# Transcribe a wav file
python transcriber.py --test --file test_audio.wav

# Live transcription only (no hooks, no UI)
python main.py --transcribe-only

# Hook generation from sample transcript
python hook_generator.py --test

# Hook deduplication test
python hook_generator.py --test-dedup

# Web UI only (no pipeline)
python web_server.py
```

### Run unit tests

```bash
pytest tests/
```

## Architecture

| Module | Purpose |
|--------|---------|
| `config.py` | All configuration constants |
| `audio_capture.py` | Captures audio from BlackHole in 10-second chunks |
| `transcriber.py` | Whisper transcription with 180-second rolling buffer |
| `hook_generator.py` | Ollama API calls to generate 2-3 hooks per cycle |
| `web_server.py` | FastAPI server with REST API and HTML UI |
| `main.py` | Orchestrates all modules, entry point |

## Output

Recordings are saved to `output/YYYY-MM-DD-HHMMSS.md` with frontmatter, hooks, and full transcript. Compatible with Obsidian.
