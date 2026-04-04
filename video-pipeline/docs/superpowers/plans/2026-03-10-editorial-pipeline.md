# Multi-Pass Editorial Pipeline Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace single-pass topic segmentation with a 3-pass editorial pipeline that produces EDL JSON, Kdenlive XML projects, and draft renders for short-form and long-form clips.

**Architecture:** Three LLM passes (outline → story extraction → editorial cut) with JSON schema validation between passes and timestamp snapping to transcript boundaries. EDL output drives both Kdenlive XML generation and FFmpeg draft rendering.

**Tech Stack:** Python 3.14, openai SDK (for Ollama/LiteLLM/cloud), pytest, ffmpeg, MLT XML (Kdenlive)

**Spec:** `docs/superpowers/specs/2026-03-10-editorial-pipeline-design.md`

---

## Chunk 1: Foundation — Config, Timestamp Snapping, JSON Validation

### Task 1: Editorial Config

**Files:**
- Create: `pipeline/editorial_config.py`
- Create: `tests/test_editorial_config.py`

- [ ] **Step 1: Write test for default config loading**

```python
# tests/test_editorial_config.py
from pipeline.editorial_config import get_pass_config


def test_default_outline_config():
    cfg = get_pass_config("outline")
    assert cfg["model"] == "qwen3:8b"
    assert "base_url" in cfg
    assert "api_key" in cfg
    assert cfg["timeout"] == 600


def test_default_stories_config():
    cfg = get_pass_config("stories")
    assert cfg["model"] == "qwen3:30b"


def test_default_editorial_config():
    cfg = get_pass_config("editorial")
    assert cfg["model"] == "qwen3:30b"


def test_unknown_pass_raises():
    import pytest
    with pytest.raises(KeyError):
        get_pass_config("nonexistent")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/dev/video-pipeline && .venv/bin/python3.14 -m pytest tests/test_editorial_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline.editorial_config'`

- [ ] **Step 3: Implement editorial_config.py**

```python
# pipeline/editorial_config.py
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
        "timeout": 600,
        "num_ctx": 32768,
    },
    "stories": {
        "base_url": OLLAMA_URL,
        "api_key": OLLAMA_KEY,
        "model": os.environ.get("EDITORIAL_STORIES_MODEL", "qwen3:30b"),
        "timeout": 600,
        "num_ctx": 32768,
    },
    "editorial": {
        "base_url": OLLAMA_URL,
        "api_key": OLLAMA_KEY,
        "model": os.environ.get("EDITORIAL_CUT_MODEL", "qwen3:30b"),
        "timeout": 600,
        "num_ctx": 16384,
    },
}


def get_pass_config(pass_name: str) -> dict:
    """Get model config for a given editorial pass."""
    return dict(_DEFAULTS[pass_name])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/dev/video-pipeline && .venv/bin/python3.14 -m pytest tests/test_editorial_config.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
cd ~/dev/video-pipeline
git add pipeline/editorial_config.py tests/test_editorial_config.py
git commit -m "feat: add editorial pipeline config with per-pass model routing"
```

---

### Task 2: Timestamp Snapping

**Files:**
- Create: `pipeline/timestamp.py`
- Create: `tests/test_timestamp.py`

- [ ] **Step 1: Write tests for timestamp snapping**

```python
# tests/test_timestamp.py
from pipeline.timestamp import snap_to_boundaries, snap_timestamp


def test_snap_to_exact_boundary():
    boundaries = [0.0, 5.0, 10.0, 15.0, 20.0]
    assert snap_timestamp(5.0, boundaries) == 5.0


def test_snap_to_nearest_boundary():
    boundaries = [0.0, 5.0, 10.0, 15.0, 20.0]
    assert snap_timestamp(5.3, boundaries) == 5.0
    assert snap_timestamp(7.8, boundaries) == 10.0


def test_snap_within_tolerance():
    boundaries = [0.0, 10.0, 20.0]
    # 1.5 is within 2.0 tolerance of 0.0
    assert snap_timestamp(1.5, boundaries, tolerance=2.0) == 0.0


def test_snap_beyond_tolerance_still_snaps():
    """Snap even beyond tolerance (with warning), as spec says."""
    boundaries = [0.0, 10.0]
    assert snap_timestamp(5.0, boundaries, tolerance=2.0) == 10.0


def test_snap_outline_section():
    transcript = [
        {"start": 3.5, "end": 10.2, "text": "Hello"},
        {"start": 10.2, "end": 18.5, "text": "World"},
        {"start": 18.5, "end": 37.2, "text": "Goodbye"},
    ]
    section = {"heading": "Intro", "start": 3.8, "end": 37.0, "points": []}
    snapped = snap_to_boundaries(section, transcript, keys=["start", "end"])
    assert snapped["start"] == 3.5
    assert snapped["end"] == 37.2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/dev/video-pipeline && .venv/bin/python3.14 -m pytest tests/test_timestamp.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement timestamp.py**

```python
# pipeline/timestamp.py
"""Snap LLM-generated timestamps to transcript sentence boundaries."""

import logging

logger = logging.getLogger(__name__)


def snap_timestamp(ts: float, boundaries: list[float], tolerance: float = 2.0) -> float:
    """Snap a timestamp to the nearest value in boundaries.

    Logs a warning if the nearest boundary is beyond tolerance.
    """
    if not boundaries:
        return ts
    nearest = min(boundaries, key=lambda b: abs(b - ts))
    if abs(nearest - ts) > tolerance:
        logger.warning("Timestamp %.2f snapped to %.2f (beyond %.1fs tolerance)", ts, nearest, tolerance)
    return nearest


def get_boundaries(transcript: list[dict]) -> list[float]:
    """Extract all unique start/end timestamps from transcript."""
    boundaries = set()
    for seg in transcript:
        boundaries.add(seg["start"])
        boundaries.add(seg["end"])
    return sorted(boundaries)


def snap_to_boundaries(data: dict, transcript: list[dict],
                       keys: list[str] = ("start", "end"),
                       tolerance: float = 2.0) -> dict:
    """Snap specified timestamp keys in a dict to transcript boundaries.

    Returns a new dict with snapped values. Does not modify the original.
    """
    boundaries = get_boundaries(transcript)
    result = dict(data)
    for key in keys:
        if key in result and isinstance(result[key], (int, float)):
            result[key] = snap_timestamp(float(result[key]), boundaries, tolerance)
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/dev/video-pipeline && .venv/bin/python3.14 -m pytest tests/test_timestamp.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
cd ~/dev/video-pipeline
git add pipeline/timestamp.py tests/test_timestamp.py
git commit -m "feat: add timestamp snapping to transcript boundaries"
```

---

### Task 3: JSON Validation Helpers

**Files:**
- Create: `pipeline/validation.py`
- Create: `tests/test_validation.py`

- [ ] **Step 1: Write tests for validation**

```python
# tests/test_validation.py
import pytest
from pipeline.validation import (
    validate_outline,
    validate_stories,
    validate_edl,
    ValidationError,
)


def test_valid_outline():
    outline = {
        "title": "Test Talk",
        "sections": [
            {
                "heading": "Intro",
                "start": 0.0,
                "end": 30.0,
                "points": [
                    {"text": "Hello world", "start": 0.0, "end": 15.0},
                    {"text": "Goodbye world", "start": 15.0, "end": 30.0},
                ],
            }
        ],
    }
    validate_outline(outline)  # should not raise


def test_outline_missing_sections():
    with pytest.raises(ValidationError, match="sections"):
        validate_outline({"title": "Test"})


def test_outline_empty_sections():
    with pytest.raises(ValidationError, match="at least 1"):
        validate_outline({"title": "Test", "sections": []})


def test_outline_section_missing_heading():
    with pytest.raises(ValidationError, match="heading"):
        validate_outline({
            "title": "Test",
            "sections": [{"start": 0.0, "end": 30.0, "points": []}],
        })


def test_valid_stories():
    stories = {
        "stories": [
            {
                "id": "test",
                "title": "Test Story",
                "start": 0.0,
                "end": 60.0,
                "engagement_score": 8,
                "standalone_rationale": "Works standalone",
                "format": "short",
                "hook_candidates": [
                    {"text": "Amazing line", "start": 10.0, "end": 13.0},
                ],
            }
        ]
    }
    validate_stories(stories)  # should not raise


def test_stories_invalid_format():
    stories = {
        "stories": [
            {
                "id": "test",
                "title": "Test",
                "start": 0.0,
                "end": 60.0,
                "engagement_score": 8,
                "standalone_rationale": "ok",
                "format": "invalid",
                "hook_candidates": [{"text": "x", "start": 0.0, "end": 1.0}],
            }
        ]
    }
    with pytest.raises(ValidationError, match="format"):
        validate_stories(stories)


def test_stories_empty_hooks():
    stories = {
        "stories": [
            {
                "id": "test",
                "title": "Test",
                "start": 0.0,
                "end": 60.0,
                "engagement_score": 8,
                "standalone_rationale": "ok",
                "format": "short",
                "hook_candidates": [],
            }
        ]
    }
    with pytest.raises(ValidationError, match="hook_candidates"):
        validate_stories(stories)


def test_valid_edl():
    edl = {
        "story_id": "test",
        "versions": {
            "short": {
                "target_duration": 55,
                "segments": [
                    {"type": "hook", "start": 10.0, "end": 15.0, "narrative_bridge": "sets up tension"},
                    {"type": "body", "start": 0.0, "end": 10.0},
                ],
                "trims": [],
                "estimated_duration": 15.0,
            }
        },
    }
    validate_edl(edl)  # should not raise


def test_edl_short_segment():
    edl = {
        "story_id": "test",
        "versions": {
            "short": {
                "target_duration": 55,
                "segments": [
                    {"type": "body", "start": 0.0, "end": 5.0},  # only 5s < 7s min
                ],
                "trims": [],
                "estimated_duration": 5.0,
            }
        },
    }
    with pytest.raises(ValidationError, match="7 second"):
        validate_edl(edl)


def test_edl_too_long_short():
    edl = {
        "story_id": "test",
        "versions": {
            "short": {
                "target_duration": 55,
                "segments": [
                    {"type": "body", "start": 0.0, "end": 60.0},
                ],
                "trims": [],
                "estimated_duration": 60.0,
            }
        },
    }
    with pytest.raises(ValidationError, match="55"):
        validate_edl(edl)


def test_edl_hook_needs_narrative_bridge():
    edl = {
        "story_id": "test",
        "versions": {
            "short": {
                "target_duration": 55,
                "segments": [
                    {"type": "hook", "start": 10.0, "end": 17.0},  # missing narrative_bridge
                ],
                "trims": [],
                "estimated_duration": 7.0,
            }
        },
    }
    with pytest.raises(ValidationError, match="narrative_bridge"):
        validate_edl(edl)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/dev/video-pipeline && .venv/bin/python3.14 -m pytest tests/test_validation.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement validation.py**

```python
# pipeline/validation.py
"""JSON schema validation for editorial pipeline pass outputs."""


class ValidationError(Exception):
    """Raised when pass output fails validation."""
    pass


def _require(data: dict, key: str, context: str = ""):
    if key not in data:
        raise ValidationError(f"Missing required field '{key}'{' in ' + context if context else ''}")


def validate_outline(data: dict) -> None:
    """Validate Pass 1 (outline) output."""
    _require(data, "title")
    _require(data, "sections")
    if not isinstance(data["sections"], list) or len(data["sections"]) < 1:
        raise ValidationError("Outline must have at least 1 section")
    prev_end = -1.0
    for i, section in enumerate(data["sections"]):
        ctx = f"section {i}"
        _require(section, "heading", ctx)
        _require(section, "start", ctx)
        _require(section, "end", ctx)
        _require(section, "points", ctx)
        if section["start"] < prev_end - 0.5:
            raise ValidationError(f"Sections not in chronological order at {ctx}: start {section['start']} < prev end {prev_end}")
        if section["end"] <= section["start"]:
            raise ValidationError(f"Section end must be after start in {ctx}")
        prev_end = section["end"]


def validate_stories(data: dict) -> None:
    """Validate Pass 2 (story extraction) output."""
    _require(data, "stories")
    for i, story in enumerate(data["stories"]):
        ctx = f"story {i}"
        for key in ("id", "title", "start", "end", "engagement_score",
                     "standalone_rationale", "format", "hook_candidates"):
            _require(story, key, ctx)
        if story["format"] not in ("short", "long", "both"):
            raise ValidationError(f"Invalid format '{story['format']}' in {ctx} — must be short/long/both")
        if story["engagement_score"] < 1 or story["engagement_score"] > 10:
            raise ValidationError(f"engagement_score must be 1-10 in {ctx}")
        if not story["hook_candidates"]:
            raise ValidationError(f"hook_candidates must be non-empty in {ctx}")
        for j, hook in enumerate(story["hook_candidates"]):
            for key in ("text", "start", "end"):
                _require(hook, key, f"{ctx} hook {j}")


def validate_edl(data: dict) -> None:
    """Validate Pass 3 (editorial cut) EDL output."""
    _require(data, "story_id")
    _require(data, "versions")
    for version_name, version in data["versions"].items():
        ctx = f"version '{version_name}'"
        _require(version, "segments", ctx)
        _require(version, "trims", ctx)
        _require(version, "estimated_duration", ctx)
        if not version["segments"]:
            raise ValidationError(f"segments must be non-empty in {ctx}")
        for k, seg in enumerate(version["segments"]):
            seg_ctx = f"{ctx} segment {k}"
            _require(seg, "type", seg_ctx)
            _require(seg, "start", seg_ctx)
            _require(seg, "end", seg_ctx)
            duration = seg["end"] - seg["start"]
            if duration < 7.0:
                raise ValidationError(f"Segment duration {duration:.1f}s below 7 second minimum in {seg_ctx}")
            if seg["type"] == "hook":
                _require(seg, "narrative_bridge", seg_ctx)
        if version_name == "short":
            if version.get("target_duration") is not None and version["estimated_duration"] > version["target_duration"]:
                raise ValidationError(
                    f"Short estimated_duration {version['estimated_duration']:.1f}s exceeds "
                    f"target {version['target_duration']}s"
                )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/dev/video-pipeline && .venv/bin/python3.14 -m pytest tests/test_validation.py -v`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
cd ~/dev/video-pipeline
git add pipeline/validation.py tests/test_validation.py
git commit -m "feat: add JSON validation for editorial pipeline pass outputs"
```

---

## Chunk 2: Three-Pass Editorial Pipeline

### Task 4: LLM Client Helper

**Files:**
- Create: `pipeline/llm.py`
- Create: `tests/test_llm.py`

This provides a thin wrapper around the openai SDK that handles JSON extraction, retry on validation failure, and per-pass config.

- [ ] **Step 1: Write tests**

```python
# tests/test_llm.py
from unittest.mock import patch, MagicMock
from pipeline.llm import llm_json_call


def _mock_response(content):
    """Create a mock OpenAI chat completion response."""
    mock = MagicMock()
    mock.choices = [MagicMock()]
    mock.choices[0].message.content = content
    return mock


@patch("pipeline.llm.openai.OpenAI")
def test_llm_json_call_returns_parsed_json(mock_client_cls):
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.chat.completions.create.return_value = _mock_response(
        '{"title": "Test", "sections": []}'
    )

    result = llm_json_call(
        pass_name="outline",
        system="You are a test.",
        user="Test input",
    )
    assert result == {"title": "Test", "sections": []}


@patch("pipeline.llm.openai.OpenAI")
def test_llm_json_call_strips_thinking_tags(mock_client_cls):
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.chat.completions.create.return_value = _mock_response(
        '<think>reasoning here</think>\n{"result": "clean"}'
    )

    result = llm_json_call(pass_name="outline", system="test", user="test")
    assert result == {"result": "clean"}


@patch("pipeline.llm.openai.OpenAI")
def test_llm_json_call_with_validator_retries(mock_client_cls):
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    # First call returns invalid, second returns valid
    mock_client.chat.completions.create.side_effect = [
        _mock_response('{"bad": true}'),
        _mock_response('{"title": "Fixed", "sections": [{"heading": "A", "start": 0, "end": 10, "points": []}]}'),
    ]

    from pipeline.validation import validate_outline
    result = llm_json_call(
        pass_name="outline",
        system="test",
        user="test",
        validator=validate_outline,
    )
    assert result["title"] == "Fixed"
    assert mock_client.chat.completions.create.call_count == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/dev/video-pipeline && .venv/bin/python3.14 -m pytest tests/test_llm.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement llm.py**

```python
# pipeline/llm.py
"""Thin LLM client wrapper with JSON extraction and validation retry."""

import json
import logging
import re
import time

import openai

from pipeline.editorial_config import get_pass_config
from pipeline.validation import ValidationError

logger = logging.getLogger(__name__)

# Cache clients by (base_url, api_key) to avoid re-creating connections
_client_cache: dict[tuple[str, str], openai.OpenAI] = {}


def _get_client(cfg: dict) -> openai.OpenAI:
    """Get or create a cached OpenAI client for the given config."""
    key = (cfg["base_url"], cfg["api_key"])
    if key not in _client_cache:
        _client_cache[key] = openai.OpenAI(
            base_url=cfg["base_url"],
            api_key=cfg["api_key"],
            timeout=cfg["timeout"],
        )
    return _client_cache[key]


def _extract_json(text: str) -> dict:
    """Strip thinking tags, extract and parse JSON from LLM response."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    # Try to find JSON object or array
    for match in re.finditer(r"[\[{]", text):
        try:
            candidate = text[match.start():]
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    raise ValueError(f"No valid JSON found in response: {text[:200]}")


def _call_with_http_retry(client, max_http_retries: int = 3, **kwargs):
    """Call chat.completions.create with exponential backoff on HTTP errors."""
    for attempt in range(1, max_http_retries + 1):
        try:
            return client.chat.completions.create(**kwargs)
        except (openai.APIConnectionError, openai.APITimeoutError, openai.InternalServerError) as e:
            if attempt == max_http_retries:
                raise
            delay = 2.0 * (2 ** (attempt - 1))
            logger.warning("HTTP error (attempt %d/%d): %s. Retrying in %.1fs", attempt, max_http_retries, e, delay)
            time.sleep(delay)


def llm_json_call(
    pass_name: str,
    system: str,
    user: str,
    validator=None,
    max_retries: int = 3,
) -> dict:
    """Call an LLM and parse the response as JSON.

    Args:
        pass_name: Key into editorial_config (outline/stories/editorial).
        system: System prompt.
        user: User prompt.
        validator: Optional callable that raises ValidationError on invalid output.
        max_retries: Max attempts if validation fails.

    Returns:
        Parsed JSON dict.
    """
    cfg = get_pass_config(pass_name)
    client = _get_client(cfg)

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    # Build extra kwargs for Ollama-specific options
    extra_body = {}
    if cfg.get("num_ctx"):
        extra_body["options"] = {"num_ctx": cfg["num_ctx"]}

    for attempt in range(1, max_retries + 1):
        logger.info("[%s] LLM call attempt %d/%d via %s", pass_name, attempt, max_retries, cfg["model"])

        call_kwargs = dict(
            model=cfg["model"],
            messages=messages,
        )
        # Use response_format when available (OpenAI-compatible)
        try:
            call_kwargs["response_format"] = {"type": "json_object"}
        except Exception:
            pass
        if extra_body:
            call_kwargs["extra_body"] = extra_body

        response = _call_with_http_retry(client, **call_kwargs)
        raw = response.choices[0].message.content
        try:
            result = _extract_json(raw)
        except ValueError as e:
            if attempt == max_retries:
                raise
            logger.warning("[%s] JSON parse failed: %s. Retrying.", pass_name, e)
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content": f"Your response was not valid JSON. Please return ONLY valid JSON. Error: {e}"})
            continue

        if validator:
            try:
                validator(result)
            except ValidationError as e:
                if attempt == max_retries:
                    raise
                logger.warning("[%s] Validation failed: %s. Retrying.", pass_name, e)
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content": f"Your JSON output had validation errors: {e}. Please fix and return corrected JSON only."})
                continue

        return result

    raise RuntimeError(f"[{pass_name}] Failed after {max_retries} attempts")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/dev/video-pipeline && .venv/bin/python3.14 -m pytest tests/test_llm.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
cd ~/dev/video-pipeline
git add pipeline/llm.py tests/test_llm.py
git commit -m "feat: add LLM client wrapper with JSON extraction and validation retry"
```

---

### Task 5: Pass 1 — Outline

**Files:**
- Create: `pipeline/editorial.py`
- Create: `tests/test_editorial.py`

- [ ] **Step 1: Write tests for outline pass**

```python
# tests/test_editorial.py
import json
from unittest.mock import patch, MagicMock
from pipeline.editorial import generate_outline


SAMPLE_TRANSCRIPT = [
    {"start": 0.0, "end": 10.0, "text": "Hello everyone, welcome.", "words": []},
    {"start": 10.0, "end": 25.0, "text": "Today I want to talk about AI.", "words": []},
    {"start": 25.0, "end": 40.0, "text": "It started with a simple idea.", "words": []},
]

VALID_OUTLINE = {
    "title": "Test Talk",
    "sections": [
        {
            "heading": "Introduction",
            "start": 0.0,
            "end": 10.0,
            "points": [{"text": "Welcome", "start": 0.0, "end": 10.0}],
        },
        {
            "heading": "AI Discussion",
            "start": 10.0,
            "end": 40.0,
            "points": [
                {"text": "AI overview", "start": 10.0, "end": 25.0},
                {"text": "Simple idea", "start": 25.0, "end": 40.0},
            ],
        },
    ],
}


@patch("pipeline.editorial.llm_json_call")
def test_generate_outline_calls_llm(mock_llm):
    mock_llm.return_value = VALID_OUTLINE
    result = generate_outline(SAMPLE_TRANSCRIPT)
    assert result["title"] == "Test Talk"
    assert len(result["sections"]) == 2
    mock_llm.assert_called_once()
    call_kwargs = mock_llm.call_args
    assert call_kwargs.kwargs["pass_name"] == "outline"


@patch("pipeline.editorial.llm_json_call")
def test_generate_outline_snaps_timestamps(mock_llm):
    # Return outline with slightly off timestamps
    outline = {
        "title": "Test",
        "sections": [
            {
                "heading": "Intro",
                "start": 0.3,  # should snap to 0.0
                "end": 9.8,    # should snap to 10.0
                "points": [{"text": "Hi", "start": 0.3, "end": 9.8}],
            },
        ],
    }
    mock_llm.return_value = outline
    result = generate_outline(SAMPLE_TRANSCRIPT)
    assert result["sections"][0]["start"] == 0.0
    assert result["sections"][0]["end"] == 10.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/dev/video-pipeline && .venv/bin/python3.14 -m pytest tests/test_editorial.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement generate_outline in editorial.py**

```python
# pipeline/editorial.py
"""Multi-pass editorial pipeline for clip extraction."""

import json
import logging

from pipeline.llm import llm_json_call
from pipeline.validation import validate_outline, validate_stories, validate_edl
from pipeline.timestamp import snap_to_boundaries

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

OUTLINE_SYSTEM = """You are analyzing a video transcript to produce a hierarchical narrative outline.

Identify the major sections and sub-points of this talk. Think about the narrative arc:
- What stories does the speaker tell?
- What arguments do they make?
- What examples do they give?

For each section, provide a descriptive heading and the key points within it.

Return ONLY a JSON object with this structure:
{
  "title": "Talk Title - Speaker Name",
  "sections": [
    {
      "heading": "Section Name (descriptive, 3-8 words)",
      "start": <float timestamp>,
      "end": <float timestamp>,
      "points": [
        {"text": "Brief description of this point", "start": <float>, "end": <float>}
      ]
    }
  ]
}

CRITICAL:
- Start and end timestamps must match sentence boundaries in the transcript.
- Every point must have start and end timestamps.
- Sections must be in chronological order with no gaps or overlaps.
- Return ONLY JSON, no explanation. /no_think"""


# ---------------------------------------------------------------------------
# Pass 1: Outline
# ---------------------------------------------------------------------------

def _format_transcript(transcript: list[dict]) -> str:
    """Format transcript for LLM consumption."""
    return "\n".join(
        f"[{s['start']:.1f}s - {s['end']:.1f}s] {s['text']}"
        for s in transcript
    )


def generate_outline(transcript: list[dict]) -> dict:
    """Pass 1: Generate a hierarchical narrative outline from a transcript."""
    transcript_text = _format_transcript(transcript)

    outline = llm_json_call(
        pass_name="outline",
        system=OUTLINE_SYSTEM,
        user=transcript_text,
        validator=validate_outline,
    )

    # Snap all timestamps to transcript boundaries
    for section in outline["sections"]:
        snapped = snap_to_boundaries(section, transcript)
        section["start"] = snapped["start"]
        section["end"] = snapped["end"]
        for point in section.get("points", []):
            snapped_pt = snap_to_boundaries(point, transcript)
            point["start"] = snapped_pt["start"]
            point["end"] = snapped_pt["end"]

    return outline
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/dev/video-pipeline && .venv/bin/python3.14 -m pytest tests/test_editorial.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
cd ~/dev/video-pipeline
git add pipeline/editorial.py tests/test_editorial.py
git commit -m "feat: add Pass 1 (outline) to editorial pipeline"
```

---

### Task 6: Pass 2 — Story Extraction

**Files:**
- Modify: `pipeline/editorial.py`
- Modify: `tests/test_editorial.py`

- [ ] **Step 1: Add tests for story extraction**

Append to `tests/test_editorial.py`:

```python
from pipeline.editorial import extract_stories

VALID_STORIES = {
    "stories": [
        {
            "id": "riptide",
            "title": "Fighting the Riptide",
            "outline_section": "How This Got Started",
            "start": 37.0,
            "end": 120.0,
            "engagement_score": 9,
            "standalone_rationale": "Complete narrative arc.",
            "format": "both",
            "hook_candidates": [
                {"text": "The harder I swam", "start": 62.0, "end": 67.0},
            ],
        },
    ],
}


@patch("pipeline.editorial.llm_json_call")
def test_extract_stories_calls_llm_with_outline(mock_llm):
    mock_llm.return_value = VALID_STORIES
    result = extract_stories(VALID_OUTLINE, SAMPLE_TRANSCRIPT)
    assert len(result["stories"]) == 1
    assert result["stories"][0]["id"] == "riptide"
    call_kwargs = mock_llm.call_args
    assert call_kwargs.kwargs["pass_name"] == "stories"
    # Verify outline is included in the user prompt
    assert "Introduction" in call_kwargs.kwargs["user"]


@patch("pipeline.editorial.llm_json_call")
def test_extract_stories_filters_low_engagement(mock_llm):
    stories = {
        "stories": [
            {**VALID_STORIES["stories"][0], "engagement_score": 9},
            {**VALID_STORIES["stories"][0], "id": "boring", "engagement_score": 4},
        ],
    }
    mock_llm.return_value = stories
    result = extract_stories(VALID_OUTLINE, SAMPLE_TRANSCRIPT, min_score=7)
    assert len(result["stories"]) == 1
    assert result["stories"][0]["id"] == "riptide"
```

- [ ] **Step 2: Run tests to verify new tests fail**

Run: `cd ~/dev/video-pipeline && .venv/bin/python3.14 -m pytest tests/test_editorial.py -v`
Expected: 2 new FAIL, 2 existing PASS

- [ ] **Step 3: Implement extract_stories in editorial.py**

Append to `pipeline/editorial.py`:

```python
STORIES_SYSTEM = """You are an experienced video editor identifying the best standalone clips from a talk.

You will receive:
1. A narrative outline of the talk
2. The full timestamped transcript

Your job: identify SELF-CONTAINED STORIES or arguments that work as standalone short-form or long-form video clips. A good clip has:
- A complete narrative arc (setup → tension → resolution) or a complete argument
- Emotional hook potential — something that makes a viewer stop scrolling
- Standalone clarity — a viewer with NO context can follow it

For each story, provide 2-3 hook candidates: punchy moments from WITHIN the story that could open the clip.

Target ratio: ~3x more shorts than long-form clips.
- "short": works as a <55 second vertical clip
- "long": works as a 90-600 second horizontal clip
- "both": can be edited into either format

Return ONLY a JSON object:
{
  "stories": [
    {
      "id": "kebab-case-identifier",
      "title": "Descriptive Title (3-8 words)",
      "outline_section": "Name of the outline section this comes from",
      "start": <float>,
      "end": <float>,
      "engagement_score": <int 1-10>,
      "standalone_rationale": "Why this works as a standalone clip",
      "format": "short|long|both",
      "hook_candidates": [
        {"text": "exact words from transcript", "start": <float>, "end": <float>}
      ]
    }
  ]
}

CRITICAL:
- engagement_score must be an integer 1-10
- hook_candidates must use EXACT text from the transcript with accurate timestamps
- Do NOT include generic introductions, thank-yous, or housekeeping as stories
- Return ONLY JSON, no explanation. /no_think"""


def extract_stories(outline: dict, transcript: list[dict],
                    min_score: int = 7) -> dict:
    """Pass 2: Extract self-contained stories from the outline."""
    outline_text = json.dumps(outline, indent=2)
    transcript_text = _format_transcript(transcript)

    user_prompt = f"OUTLINE:\n{outline_text}\n\nFULL TRANSCRIPT:\n{transcript_text}"

    result = llm_json_call(
        pass_name="stories",
        system=STORIES_SYSTEM,
        user=user_prompt,
        validator=validate_stories,
    )

    # Snap timestamps
    for story in result["stories"]:
        snapped = snap_to_boundaries(story, transcript)
        story["start"] = snapped["start"]
        story["end"] = snapped["end"]
        for hook in story.get("hook_candidates", []):
            snapped_hook = snap_to_boundaries(hook, transcript)
            hook["start"] = snapped_hook["start"]
            hook["end"] = snapped_hook["end"]

    # Filter by engagement score
    result["stories"] = [s for s in result["stories"] if s["engagement_score"] >= min_score]

    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/dev/video-pipeline && .venv/bin/python3.14 -m pytest tests/test_editorial.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
cd ~/dev/video-pipeline
git add pipeline/editorial.py tests/test_editorial.py
git commit -m "feat: add Pass 2 (story extraction) to editorial pipeline"
```

---

### Task 7: Pass 3 — Editorial Cut

**Files:**
- Modify: `pipeline/editorial.py`
- Modify: `tests/test_editorial.py`

- [ ] **Step 1: Add tests for editorial cut**

Append to `tests/test_editorial.py`:

```python
from pipeline.editorial import generate_edl

SAMPLE_STORY = {
    "id": "riptide",
    "title": "Fighting the Riptide",
    "start": 37.0,
    "end": 120.0,
    "engagement_score": 9,
    "format": "both",
    "hook_candidates": [
        {"text": "The harder I swam", "start": 62.0, "end": 67.0},
    ],
}

STORY_TRANSCRIPT = [
    {"start": 37.0, "end": 55.0, "text": "Swimming in Thailand.", "words": []},
    {"start": 55.0, "end": 70.0, "text": "The harder I swam the harder I got pulled.", "words": []},
    {"start": 70.0, "end": 85.0, "text": "I had a minute left.", "words": []},
    {"start": 85.0, "end": 120.0, "text": "That's when I learned about riptides.", "words": []},
]

VALID_EDL = {
    "story_id": "riptide",
    "versions": {
        "short": {
            "target_duration": 55,
            "segments": [
                {"type": "hook", "start": 62.0, "end": 70.0, "narrative_bridge": "Core tension"},
                {"type": "body", "start": 37.0, "end": 55.0},
            ],
            "trims": [],
            "estimated_duration": 26.0,
        },
        "long": {
            "target_duration": None,
            "segments": [
                {"type": "hook", "start": 62.0, "end": 70.0, "narrative_bridge": "Core tension"},
                {"type": "body", "start": 37.0, "end": 62.0},
                {"type": "body", "start": 70.0, "end": 120.0},
            ],
            "trims": [],
            "estimated_duration": 91.0,
        },
    },
}


@patch("pipeline.editorial.llm_json_call")
def test_generate_edl_calls_llm(mock_llm):
    mock_llm.return_value = VALID_EDL
    result = generate_edl(SAMPLE_STORY, STORY_TRANSCRIPT, "video.mp4")
    assert result["story_id"] == "riptide"
    assert result["source_video"] == "video.mp4"
    assert "short" in result["versions"]
    call_kwargs = mock_llm.call_args
    assert call_kwargs.kwargs["pass_name"] == "editorial"


@patch("pipeline.editorial.llm_json_call")
def test_generate_edl_injects_source_video(mock_llm):
    mock_llm.return_value = VALID_EDL
    result = generate_edl(SAMPLE_STORY, STORY_TRANSCRIPT, "/path/to/source.mp4")
    assert result["source_video"] == "/path/to/source.mp4"
```

- [ ] **Step 2: Run tests to verify new tests fail**

Run: `cd ~/dev/video-pipeline && .venv/bin/python3.14 -m pytest tests/test_editorial.py -v`
Expected: 2 new FAIL, 4 existing PASS

- [ ] **Step 3: Implement generate_edl in editorial.py**

Append to `pipeline/editorial.py`:

```python
EDITORIAL_SYSTEM = """You are an experienced video editor creating a cut list for a short-form clip.

You will receive a story from a talk with its transcript section and hook candidates. Your job: produce an Edit Decision List (EDL) that creates two versions:

1. SHORT version (<55 seconds, vertical 9:16 — for Reels/Shorts/TikTok)
2. LONG version (90-600 seconds, horizontal 16:9 — for YouTube/LinkedIn)

EDITING RULES:
- The HOOK is placed at the very start of the clip. It must come from the hook_candidates provided.
- The BODY plays after the hook. Skip the hook's original position in the body to avoid repetition, UNLESS you judge that repeating it adds rhetorical value.
- For the SHORT version: trim setup, filler, or redundant context to fit <55 seconds. Leave 4 seconds at the end for a title card (not your concern — just keep the content under 55s).
- NEVER create a segment shorter than 7 seconds. If trimming would create a segment under 7s, don't make that trim.
- Prefer trimming setup/context over trimming the payoff or emotional peak.
- Trims must include a reason (for debugging).
- The hook segment needs a narrative_bridge field explaining why it flows into the body.

Return ONLY a JSON object:
{{
  "story_id": "{story_id}",
  "versions": {{
    "short": {{
      "target_duration": 55,
      "segments": [
        {{"type": "hook", "start": <float>, "end": <float>, "narrative_bridge": "why this opens well"}},
        {{"type": "body", "start": <float>, "end": <float>}},
        ...
      ],
      "trims": [
        {{"start": <float>, "end": <float>, "reason": "why this was cut"}}
      ],
      "estimated_duration": <float>
    }},
    "long": {{
      "target_duration": null,
      "segments": [...],
      "trims": [],
      "estimated_duration": <float>
    }}
  }}
}}

CRITICAL:
- estimated_duration for short MUST be <= 55 seconds
- No segment shorter than 7 seconds
- segments are played in order — the first segment is the hook, followed by body segments
- Return ONLY JSON, no explanation. /no_think"""


def generate_edl(story: dict, transcript: list[dict], source_video: str) -> dict:
    """Pass 3: Generate an edit decision list for one story."""
    # Get transcript segments within this story's range (timestamps are already snapped)
    story_segments = [
        s for s in transcript
        if s["start"] >= story["start"] - 0.5 and s["end"] <= story["end"] + 0.5
    ]

    story_text = _format_transcript(story_segments)
    hooks_text = json.dumps(story["hook_candidates"], indent=2)

    user_prompt = (
        f"STORY: {story['title']}\n"
        f"Time range: {story['start']:.1f}s - {story['end']:.1f}s "
        f"(total: {story['end'] - story['start']:.0f}s)\n"
        f"Format: {story['format']}\n\n"
        f"HOOK CANDIDATES:\n{hooks_text}\n\n"
        f"TRANSCRIPT:\n{story_text}"
    )

    edl = llm_json_call(
        pass_name="editorial",
        system=EDITORIAL_SYSTEM.format(story_id=story["id"]),
        user=user_prompt,
        validator=validate_edl,
    )

    # Snap timestamps to transcript boundaries
    for version in edl["versions"].values():
        for seg in version["segments"]:
            snapped = snap_to_boundaries(seg, transcript)
            seg["start"] = snapped["start"]
            seg["end"] = snapped["end"]
        for trim in version.get("trims", []):
            snapped = snap_to_boundaries(trim, transcript)
            trim["start"] = snapped["start"]
            trim["end"] = snapped["end"]

    # Inject source video (orchestrator's job, not the LLM's)
    edl["source_video"] = source_video

    return edl
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/dev/video-pipeline && .venv/bin/python3.14 -m pytest tests/test_editorial.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
cd ~/dev/video-pipeline
git add pipeline/editorial.py tests/test_editorial.py
git commit -m "feat: add Pass 3 (editorial cut) to editorial pipeline"
```

---

### Task 8: Pipeline Orchestrator

**Files:**
- Modify: `pipeline/editorial.py`
- Modify: `tests/test_editorial.py`

- [ ] **Step 1: Add test for full pipeline orchestration**

Append to `tests/test_editorial.py`:

```python
import os
import tempfile
from pipeline.editorial import run_editorial_pipeline


@patch("pipeline.editorial.llm_json_call")
def test_run_editorial_pipeline_saves_all_json(mock_llm):
    mock_llm.side_effect = [
        VALID_OUTLINE,  # Pass 1
        VALID_STORIES,  # Pass 2
        VALID_EDL,      # Pass 3 (one story)
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        result = run_editorial_pipeline(
            transcript=SAMPLE_TRANSCRIPT,
            source_video="test.mp4",
            output_dir=tmpdir,
        )

        assert os.path.exists(os.path.join(tmpdir, "outline.json"))
        assert os.path.exists(os.path.join(tmpdir, "stories.json"))
        assert os.path.exists(os.path.join(tmpdir, "edits", "riptide.json"))

        assert len(result) == 1
        assert result[0]["story_id"] == "riptide"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/dev/video-pipeline && .venv/bin/python3.14 -m pytest tests/test_editorial.py::test_run_editorial_pipeline_saves_all_json -v`
Expected: FAIL

- [ ] **Step 3: Implement run_editorial_pipeline**

Append to `pipeline/editorial.py`:

```python
import os


def run_editorial_pipeline(
    transcript: list[dict],
    source_video: str,
    output_dir: str,
    min_score: int = 7,
) -> list[dict]:
    """Run the full 3-pass editorial pipeline.

    Args:
        transcript: List of transcript segments with word timing.
        source_video: Path to the source video file.
        output_dir: Directory to save all intermediate and final outputs.
        min_score: Minimum engagement score for story selection.

    Returns:
        List of EDL dicts, one per selected story.
    """
    os.makedirs(output_dir, exist_ok=True)
    edits_dir = os.path.join(output_dir, "edits")
    os.makedirs(edits_dir, exist_ok=True)

    # Pass 1: Outline
    # Pass 1: Outline
    outline_path = os.path.join(output_dir, "outline.json")
    if os.path.exists(outline_path):
        logger.info("Loading cached outline from %s", outline_path)
        with open(outline_path) as f:
            outline = json.load(f)
    else:
        logger.info("Pass 1: Generating outline...")
        outline = generate_outline(transcript)
        with open(outline_path, "w") as f:
            json.dump(outline, f, indent=2, ensure_ascii=False)
        logger.info("Outline saved: %d sections", len(outline["sections"]))

    # Cascade validation: outline must have sections
    validate_outline(outline)

    # Pass 2: Story extraction
    stories_path = os.path.join(output_dir, "stories.json")
    if os.path.exists(stories_path):
        logger.info("Loading cached stories from %s", stories_path)
        with open(stories_path) as f:
            stories_data = json.load(f)
    else:
        logger.info("Pass 2: Extracting stories...")
        stories_data = extract_stories(outline, transcript, min_score=min_score)
        with open(stories_path, "w") as f:
            json.dump(stories_data, f, indent=2, ensure_ascii=False)
        logger.info("Stories saved: %d stories (score >= %d)", len(stories_data["stories"]), min_score)

    # Cascade validation: must have qualifying stories
    if not stories_data["stories"]:
        logger.warning("No stories with engagement_score >= %d found. Pipeline complete with 0 clips.", min_score)
        return []

    # Pass 3: Editorial cut (per story)
    edls = []
    for story in stories_data["stories"]:
        edl_path = os.path.join(edits_dir, f"{story['id']}.json")
        if os.path.exists(edl_path):
            logger.info("Loading cached EDL for '%s'", story["id"])
            with open(edl_path) as f:
                edl = json.load(f)
        else:
            logger.info("Pass 3: Editorial cut for '%s'...", story["title"])
            edl = generate_edl(story, transcript, source_video)
            with open(edl_path, "w") as f:
                json.dump(edl, f, indent=2, ensure_ascii=False)
        edls.append(edl)

    logger.info("Editorial pipeline complete: %d clips", len(edls))
    return edls
```

- [ ] **Step 4: Run all tests**

Run: `cd ~/dev/video-pipeline && .venv/bin/python3.14 -m pytest tests/test_editorial.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
cd ~/dev/video-pipeline
git add pipeline/editorial.py tests/test_editorial.py
git commit -m "feat: add pipeline orchestrator with caching for editorial pipeline"
```

---

## Chunk 3: EDL Rendering — FFmpeg + Kdenlive XML

### Task 9: EDL to FFmpeg Rendering

**Files:**
- Create: `pipeline/edl.py`
- Create: `tests/test_edl.py`

- [ ] **Step 1: Write tests for EDL segment resolution and FFmpeg rendering**

```python
# tests/test_edl.py
from unittest.mock import patch, MagicMock, call
from pipeline.edl import resolve_segments, render_edl_version


def test_resolve_segments_simple():
    """Body segments with no trims should pass through."""
    segments = [
        {"type": "hook", "start": 60.0, "end": 67.0, "narrative_bridge": "tension"},
        {"type": "body", "start": 30.0, "end": 60.0},
    ]
    resolved = resolve_segments(segments, trims=[])
    assert len(resolved) == 2
    assert resolved[0] == (60.0, 67.0)
    assert resolved[1] == (30.0, 60.0)


def test_resolve_segments_with_trim():
    """A trim inside a body segment should split it."""
    segments = [
        {"type": "body", "start": 30.0, "end": 60.0},
    ]
    trims = [{"start": 40.0, "end": 48.0, "reason": "filler"}]
    resolved = resolve_segments(segments, trims)
    assert len(resolved) == 2
    assert resolved[0] == (30.0, 40.0)
    assert resolved[1] == (48.0, 60.0)


def test_resolve_segments_trim_at_start():
    """Trim at the start of a segment."""
    segments = [{"type": "body", "start": 30.0, "end": 60.0}]
    trims = [{"start": 30.0, "end": 38.0, "reason": "intro"}]
    resolved = resolve_segments(segments, trims)
    assert len(resolved) == 1
    assert resolved[0] == (38.0, 60.0)


def test_resolve_segments_trim_at_end():
    """Trim at the end of a segment."""
    segments = [{"type": "body", "start": 30.0, "end": 60.0}]
    trims = [{"start": 52.0, "end": 60.0, "reason": "trailing"}]
    resolved = resolve_segments(segments, trims)
    assert len(resolved) == 1
    assert resolved[0] == (30.0, 52.0)


@patch("pipeline.edl._run_ffmpeg")
def test_render_edl_version_concatenates_segments(mock_ffmpeg):
    edl_version = {
        "segments": [
            {"type": "hook", "start": 60.0, "end": 67.0, "narrative_bridge": "x"},
            {"type": "body", "start": 30.0, "end": 55.0},
        ],
        "trims": [],
        "target_duration": 55,
        "estimated_duration": 32.0,
    }
    render_edl_version(
        edl_version,
        source_video="/path/video.mp4",
        output_path="/path/out.mp4",
        crop_mode="vertical",
    )
    # Should have called ffmpeg at least once
    assert mock_ffmpeg.called
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/dev/video-pipeline && .venv/bin/python3.14 -m pytest tests/test_edl.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement edl.py**

```python
# pipeline/edl.py
"""EDL rendering: convert edit decision lists to FFmpeg commands and Kdenlive XML."""

import logging
import os
import subprocess
import tempfile

logger = logging.getLogger(__name__)

FFMPEG = "/opt/homebrew/bin/ffmpeg"


def _run_ffmpeg(args: list[str]) -> None:
    """Run ffmpeg with error handling."""
    cmd = [FFMPEG, "-y"] + args
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr[-500:]}")


def resolve_segments(segments: list[dict], trims: list[dict]) -> list[tuple[float, float]]:
    """Resolve EDL segments and trims into a flat list of (start, end) time ranges.

    Trims are subtractive: if a trim falls within a segment, that segment is
    split into sub-segments around the trim.
    """
    # First, collect raw time ranges in playback order
    raw_ranges = [(seg["start"], seg["end"]) for seg in segments]

    if not trims:
        return raw_ranges

    # Apply trims: for each trim, split any range that contains it
    for trim in trims:
        t_start, t_end = trim["start"], trim["end"]
        new_ranges = []
        for r_start, r_end in raw_ranges:
            if t_start >= r_end or t_end <= r_start:
                # Trim doesn't overlap this range
                new_ranges.append((r_start, r_end))
            else:
                # Trim overlaps — split
                if r_start < t_start:
                    new_ranges.append((r_start, t_start))
                if t_end < r_end:
                    new_ranges.append((t_end, r_end))
        raw_ranges = new_ranges

    # Filter out any zero-length or near-zero segments
    return [(s, e) for s, e in raw_ranges if e - s > 0.1]


def render_edl_version(
    edl_version: dict,
    source_video: str,
    output_path: str,
    crop_mode: str = "vertical",
    face_pos: tuple[float, float] | None = None,
    subtitle_path: str | None = None,
) -> None:
    """Render one version (short or long) of an EDL to a video file.

    Args:
        edl_version: EDL version dict with segments, trims, etc.
        source_video: Path to source video.
        output_path: Path for output video.
        crop_mode: "vertical" (9:16) or "horizontal" (keep original).
        face_pos: Optional (x, y) face position for vertical crop (0.0-1.0).
        subtitle_path: Optional path to ASS/SRT subtitle file to burn in.
    """
    time_ranges = resolve_segments(edl_version["segments"], edl_version.get("trims", []))

    if not time_ranges:
        logger.warning("No segments to render for %s", output_path)
        return

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    # Extract each segment to a temp file, then concatenate
    tmp_dir = tempfile.mkdtemp(prefix="edl_render_")
    segment_files = []

    try:
        for i, (start, end) in enumerate(time_ranges):
            seg_path = os.path.join(tmp_dir, f"seg_{i:03d}.mp4")
            _run_ffmpeg([
                "-ss", str(start),
                "-to", str(end),
                "-i", source_video,
                "-c", "copy",  # stream copy — no re-encode on extraction
                seg_path,
            ])
            segment_files.append(seg_path)

        # Write concat list
        concat_path = os.path.join(tmp_dir, "concat.txt")
        with open(concat_path, "w") as f:
            for seg_path in segment_files:
                f.write(f"file '{seg_path}'\n")

        # Build filter chain
        vf_filters = []

        if crop_mode == "vertical":
            if face_pos:
                # Read dimensions from first segment
                import cv2
                cap = cv2.VideoCapture(segment_files[0])
                vid_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                vid_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                cap.release()
                crop_w = int(vid_h * 9 / 16)
                face_x_px = int(face_pos[0] * vid_w)
                crop_x = max(0, min(face_x_px - crop_w // 2, vid_w - crop_w))
                vf_filters.append(f"crop={crop_w}:{vid_h}:{crop_x}:0")
            else:
                vf_filters.append("crop=ih*9/16:ih")
            vf_filters.append("scale=1080:1920")

        if subtitle_path:
            escaped = subtitle_path.replace("\\", "\\\\").replace(":", "\\:")
            vf_filters.append(f"ass={escaped}")

        # Concatenate and apply filters
        filter_args = []
        if vf_filters:
            filter_args = ["-vf", ",".join(vf_filters)]

        _run_ffmpeg([
            "-f", "concat", "-safe", "0", "-i", concat_path,
            *filter_args,
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
            output_path,
        ])

    finally:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/dev/video-pipeline && .venv/bin/python3.14 -m pytest tests/test_edl.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
cd ~/dev/video-pipeline
git add pipeline/edl.py tests/test_edl.py
git commit -m "feat: add EDL segment resolution and FFmpeg rendering"
```

---

### Task 10: Kdenlive XML Generation

**Files:**
- Modify: `pipeline/edl.py`
- Modify: `tests/test_edl.py`

- [ ] **Step 1: Add tests for Kdenlive XML generation**

Append to `tests/test_edl.py`:

```python
import xml.etree.ElementTree as ET
from pipeline.edl import generate_kdenlive_xml


def test_kdenlive_xml_structure():
    edl_version = {
        "segments": [
            {"type": "hook", "start": 60.0, "end": 67.0, "narrative_bridge": "tension"},
            {"type": "body", "start": 30.0, "end": 55.0},
        ],
        "trims": [],
        "estimated_duration": 32.0,
        "target_duration": 55,
    }
    xml_str = generate_kdenlive_xml(
        edl_version,
        source_video="/path/video.mp4",
        profile="vertical",
    )
    root = ET.fromstring(xml_str)
    assert root.tag == "mlt"

    # Should have a producer for the source video
    producers = root.findall(".//producer")
    assert len(producers) >= 1

    # Should have a playlist with entries
    playlists = root.findall(".//playlist")
    assert len(playlists) >= 1

    # Check that entries exist with in/out attributes
    entries = root.findall(".//entry")
    assert len(entries) == 2  # hook + body


def test_kdenlive_xml_vertical_profile():
    edl_version = {
        "segments": [{"type": "body", "start": 0.0, "end": 30.0}],
        "trims": [],
        "estimated_duration": 30.0,
        "target_duration": 55,
    }
    xml_str = generate_kdenlive_xml(edl_version, "/path/video.mp4", profile="vertical")
    root = ET.fromstring(xml_str)
    profile = root.find(".//profile")
    assert profile.get("width") == "1080"
    assert profile.get("height") == "1920"


def test_kdenlive_xml_horizontal_profile():
    edl_version = {
        "segments": [{"type": "body", "start": 0.0, "end": 120.0}],
        "trims": [],
        "estimated_duration": 120.0,
        "target_duration": None,
    }
    xml_str = generate_kdenlive_xml(edl_version, "/path/video.mp4", profile="horizontal")
    root = ET.fromstring(xml_str)
    profile = root.find(".//profile")
    assert profile.get("width") == "1920"
    assert profile.get("height") == "1080"
```

- [ ] **Step 2: Run tests to verify new tests fail**

Run: `cd ~/dev/video-pipeline && .venv/bin/python3.14 -m pytest tests/test_edl.py -v`
Expected: 3 new FAIL, 5 existing PASS

- [ ] **Step 3: Implement generate_kdenlive_xml**

Append to `pipeline/edl.py`:

```python
def _seconds_to_timecode(seconds: float, fps: int = 30) -> str:
    """Convert seconds to HH:MM:SS.mmm timecode."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def generate_kdenlive_xml(
    edl_version: dict,
    source_video: str,
    profile: str = "vertical",
    subtitle_path: str | None = None,
    fps: int = 30,
) -> str:
    """Generate Kdenlive/MLT XML project from an EDL version.

    Args:
        edl_version: EDL version dict with segments, trims.
        source_video: Path to source video.
        profile: "vertical" (1080x1920) or "horizontal" (1920x1080).
        subtitle_path: Optional subtitle file to include as a track.
        fps: Frames per second (default 30).

    Returns:
        XML string for a .kdenlive project file.
    """
    time_ranges = resolve_segments(edl_version["segments"], edl_version.get("trims", []))

    if profile == "vertical":
        width, height = 1080, 1920
    else:
        width, height = 1920, 1080

    def to_frame(seconds: float) -> int:
        return int(round(seconds * fps))

    # Build MLT XML
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        f'<mlt LC_NUMERIC="C" version="7.0.0" producer="main_bin">',
        f'  <profile description="{profile}" width="{width}" height="{height}" '
        f'progressive="1" sample_aspect_num="1" sample_aspect_den="1" '
        f'display_aspect_num="{width}" display_aspect_den="{height}" '
        f'frame_rate_num="{fps}" frame_rate_den="1" colorspace="709"/>',
        '',
        f'  <producer id="source" in="00:00:00.000" out="{_seconds_to_timecode(3600)}">',
        f'    <property name="resource">{source_video}</property>',
        f'    <property name="mlt_service">avformat</property>',
        '  </producer>',
        '',
        '  <playlist id="video_track">',
    ]

    for i, (start, end) in enumerate(time_ranges):
        in_tc = _seconds_to_timecode(start)
        out_tc = _seconds_to_timecode(end)
        lines.append(f'    <entry producer="source" in="{in_tc}" out="{out_tc}"/>')

    lines.append('  </playlist>')

    if subtitle_path:
        lines.extend([
            '',
            f'  <producer id="subtitles">',
            f'    <property name="resource">{subtitle_path}</property>',
            f'    <property name="mlt_service">avformat</property>',
            '  </producer>',
            '',
            '  <playlist id="subtitle_track">',
            f'    <entry producer="subtitles"/>',
            '  </playlist>',
        ])

    lines.extend([
        '',
        '  <tractor id="tractor0">',
        '    <multitrack>',
        '      <track producer="video_track"/>',
    ])

    if subtitle_path:
        lines.append('      <track producer="subtitle_track"/>')

    lines.extend([
        '    </multitrack>',
        '  </tractor>',
        '</mlt>',
    ])

    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/dev/video-pipeline && .venv/bin/python3.14 -m pytest tests/test_edl.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
cd ~/dev/video-pipeline
git add pipeline/edl.py tests/test_edl.py
git commit -m "feat: add Kdenlive XML generation from EDL"
```

---

## Chunk 4: Integration — CLI + Smoke Test

### Task 11: CLI Command

**Files:**
- Modify: `pipeline/cli.py`
- The CLI gets an `editorial` command that runs the full pipeline.

- [ ] **Step 1: Read current cli.py**

Read `pipeline/cli.py` to understand existing Click command patterns.

- [ ] **Step 2: Add the `editorial` command**

Add to `pipeline/cli.py`:

```python
@cli.command()
@click.argument("url")
@click.option("--output-dir", default=None, help="Output directory")
@click.option("--max-clips", type=int, default=6, help="Maximum clips to produce")
@click.option("--min-score", type=int, default=7, help="Minimum engagement score")
def editorial(url, output_dir, max_clips, min_score):
    """Run the multi-pass editorial pipeline on a YouTube video."""
    import json
    from pipeline.transcribe import transcribe_video
    from pipeline.editorial import run_editorial_pipeline
    from pipeline.edl import render_edl_version, generate_kdenlive_xml
    from pipeline.editor import _detect_face_center

    if output_dir is None:
        output_dir = os.path.expanduser(f"~/Documents/editorial-{url.split('/')[-1]}")

    os.makedirs(output_dir, exist_ok=True)
    cache_dir = os.path.join(output_dir, "cache")

    # Download via yt-dlp
    click.echo(f"Downloading video...")
    import subprocess as sp
    os.makedirs(cache_dir, exist_ok=True)
    output_template = os.path.join(cache_dir, "%(id)s.%(ext)s")
    sp.run(["yt-dlp", "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "-o", output_template, "--merge-output-format", "mp4", url],
           check=True, capture_output=True)
    video_path = next(f for f in [os.path.join(cache_dir, x) for x in os.listdir(cache_dir)] if f.endswith(".mp4"))

    # Transcribe
    transcript_path = os.path.join(output_dir, "cache", "transcript.json")
    if os.path.exists(transcript_path):
        click.echo("Loading cached transcript...")
        with open(transcript_path) as f:
            transcript = json.load(f)
    else:
        click.echo("Transcribing with Parakeet...")
        transcript = transcribe_video(video_path)
        os.makedirs(os.path.dirname(transcript_path), exist_ok=True)
        with open(transcript_path, "w") as f:
            json.dump(transcript, f, ensure_ascii=False, indent=2)

    # Editorial pipeline
    click.echo("Running editorial pipeline...")
    edls = run_editorial_pipeline(transcript, video_path, output_dir, min_score)
    edls = edls[:max_clips]

    # Detect face for vertical crop
    face_pos = _detect_face_center(video_path)

    # Render drafts and Kdenlive projects
    drafts_dir = os.path.join(output_dir, "drafts")
    projects_dir = os.path.join(output_dir, "projects")
    os.makedirs(drafts_dir, exist_ok=True)
    os.makedirs(projects_dir, exist_ok=True)

    for edl in edls:
        story_id = edl["story_id"]
        for version_name, version in edl["versions"].items():
            crop = "vertical" if version_name == "short" else "horizontal"
            profile = crop

            # Draft render
            draft_path = os.path.join(drafts_dir, f"{story_id}-{version_name}-en.mp4")
            click.echo(f"Rendering {story_id} ({version_name})...")
            render_edl_version(version, video_path, draft_path, crop_mode=crop, face_pos=face_pos)

            # Kdenlive project
            xml = generate_kdenlive_xml(version, video_path, profile=profile)
            project_path = os.path.join(projects_dir, f"{story_id}-{version_name}.kdenlive")
            with open(project_path, "w") as f:
                f.write(xml)

    click.echo(f"\nDone! Output in {output_dir}")
```

- [ ] **Step 3: Test CLI manually**

Run: `cd ~/dev/video-pipeline && .venv/bin/python3.14 -m pipeline.cli editorial "https://youtu.be/I7X4Lrkj2IU" --max-clips 2 --output-dir ~/Documents/editorial-test`

Expected: Pipeline runs, produces outline.json, stories.json, edits/*.json, drafts/*.mp4, projects/*.kdenlive

- [ ] **Step 4: Commit**

```bash
cd ~/dev/video-pipeline
git add pipeline/cli.py
git commit -m "feat: add editorial CLI command for multi-pass pipeline"
```

---

### Task 12: Run All Tests

- [ ] **Step 1: Run the full test suite**

Run: `cd ~/dev/video-pipeline && .venv/bin/python3.14 -m pytest tests/ -v`

Expected: All tests pass (existing + new). Fix any failures.

- [ ] **Step 2: Final commit if needed**

```bash
cd ~/dev/video-pipeline
git add -A
git commit -m "fix: resolve any test issues from editorial pipeline integration"
```
