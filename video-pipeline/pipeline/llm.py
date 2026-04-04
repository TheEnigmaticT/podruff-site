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
    """Strip thinking tags, code fences, and extract JSON from LLM response."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    # Strip markdown code fences (```json ... ``` or ``` ... ```)
    text = re.sub(r"^```(?:json)?\s*\n?", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n?```\s*$", "", text, flags=re.MULTILINE)
    text = text.strip()
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
