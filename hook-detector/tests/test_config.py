"""Tests for config module — verify all required constants exist."""

import config


def test_audio_constants():
    assert config.AUDIO_DEVICE == "BlackHole 2ch"
    assert config.SAMPLE_RATE == 16000
    assert config.CHUNK_DURATION_SECONDS == 10
    assert config.CHANNELS == 1


def test_whisper_constants():
    assert config.WHISPER_MODEL == "base.en"
    assert config.TRANSCRIPT_BUFFER_SECONDS == 180


def test_ollama_constants():
    assert config.OLLAMA_MODEL == "qwen3:30b"
    assert "localhost" in config.OLLAMA_URL
    assert config.GENERATION_INTERVAL_SECONDS == 10
    assert config.MAX_HOOKS_IN_CONTEXT == 20


def test_generation_offsets():
    assert config.HOOK_OFFSET_SECONDS == 0
    assert config.TOPIC_OFFSET_SECONDS == 3
    assert config.FOLLOWUP_OFFSET_SECONDS == 6
    # Offsets should all fit within the generation interval
    assert config.FOLLOWUP_OFFSET_SECONDS < config.GENERATION_INTERVAL_SECONDS


def test_context_limits():
    assert config.MAX_HOOKS_IN_CONTEXT == 20
    assert config.MAX_TOPICS_IN_CONTEXT == 10
    assert config.MAX_FOLLOWUPS_IN_CONTEXT == 10


def test_prompt_templates():
    assert "{previous_hooks}" in config.HOOK_PROMPT_TEMPLATE
    assert "{transcript}" in config.HOOK_PROMPT_TEMPLATE
    assert "{previous_topics}" in config.TOPIC_PROMPT_TEMPLATE
    assert "{previous_followups}" in config.FOLLOWUP_PROMPT_TEMPLATE
