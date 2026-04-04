"""Per-pass model routing for the editorial pipeline."""

import os

OLLAMA_URL = "http://localhost:11434/v1"
OLLAMA_KEY = "ollama"

LITELLM_URL = os.environ.get("LITELLM_URL", "http://localhost:13668/v1")
LITELLM_KEY = os.environ.get("LITELLM_KEY", "")

_DEFAULTS = {
    "outline": {
        "base_url": OLLAMA_URL,
        "api_key": OLLAMA_KEY,
        "model": os.environ.get("EDITORIAL_OUTLINE_MODEL", "qwen3:8b"),
        "timeout": 1800,
        "num_ctx": 32768,
    },
    "stories": {
        "base_url": OLLAMA_URL,
        "api_key": OLLAMA_KEY,
        "model": os.environ.get("EDITORIAL_STORIES_MODEL", "qwen3:30b"),
        "timeout": 1800,
        "num_ctx": 32768,
    },
    "editorial": {
        "base_url": OLLAMA_URL,
        "api_key": OLLAMA_KEY,
        "model": os.environ.get("EDITORIAL_CUT_MODEL", "qwen3:30b"),
        "timeout": 1800,
        "num_ctx": 16384,
    },
}


def get_pass_config(pass_name: str) -> dict:
    """Get model config for a given editorial pass."""
    return dict(_DEFAULTS[pass_name])
