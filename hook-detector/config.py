"""Configuration constants for the Live Call Hook Assistant."""

import os

# Audio capture settings
AUDIO_DEVICE = "BlackHole 2ch"
SAMPLE_RATE = 16000
CHUNK_DURATION_SECONDS = 10
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit PCM
SILENCE_THRESHOLD = 100  # RMS amplitude below which audio is considered silent
SILENCE_WARNING_SECONDS = 5  # Log warning after this many seconds of silence

# Whisper transcription settings
WHISPER_MODEL = "base.en"
TRANSCRIPT_BUFFER_SECONDS = 180

# Ollama settings (shared across all generators)
OLLAMA_MODEL = "qwen3:30b"
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_TIMEOUT_SECONDS = 30

# Generation cadence — staggered within each 10s window
GENERATION_INTERVAL_SECONDS = 10
HOOK_OFFSET_SECONDS = 0     # hooks fire at t+0
TOPIC_OFFSET_SECONDS = 3    # topics fire at t+3
FOLLOWUP_OFFSET_SECONDS = 6 # follow-ups fire at t+6

# Context limits (how many previous items to include in prompt)
MAX_HOOKS_IN_CONTEXT = 20
MAX_TOPICS_IN_CONTEXT = 10
MAX_FOLLOWUPS_IN_CONTEXT = 10

# Web server settings
WEB_HOST = "0.0.0.0"
WEB_PORT = 8000

# Output settings
OUTPUT_DIR = os.path.join(os.path.expanduser("~"), "Projects", "hook-assistant", "output")

# --- Prompt templates ---

HOOK_PROMPT_TEMPLATE = """You are a podcast content expert. Generate 2-3 punchy, memorable hooks from this conversation snippet.

Rules:
- Each hook must be under 12 words
- Hooks should be quotable soundbites, not summaries
- Avoid generic phrases like "the future of" or "game-changer"
- Each hook should capture a distinct insight

Already suggested (don't repeat similar ideas):
{previous_hooks}

Recent conversation:
{transcript}

Return only the hooks, one per line, no numbering or bullets."""

TOPIC_PROMPT_TEMPLATE = """You identify interesting discussion threads worth exploring in podcast conversations.

Rules:
- Flag threads the host should dig into before moving on
- Each topic should be a concise label (under 15 words)
- Look for: tensions, comparisons, unresolved claims, recurring themes
- Don't flag things already thoroughly discussed

Already flagged topics (don't repeat):
{previous_topics}

Recent conversation:
{transcript}

Return only the topic flags, one per line, no numbering or bullets."""

FOLLOWUP_PROMPT_TEMPLATE = """You suggest insightful follow-up questions for podcast interviews. Questions should dig deeper, not repeat what's been discussed.

Rules:
- Each question should be a single, direct question
- Target moments where the guest dropped something interesting and moved on
- Avoid yes/no questions — ask "how" and "why" questions
- Be specific to what was actually said

Already suggested questions (don't repeat):
{previous_followups}

Recent conversation:
{transcript}

Return only the questions, one per line, no numbering or bullets."""
