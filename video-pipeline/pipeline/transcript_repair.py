"""Transcript repair: LLM-based speech-to-text error correction with confidence tiers."""

import logging

from pipeline.llm import llm_json_call

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a transcript proofreader. Review the supplied transcript text and identify \
likely speech-to-text transcription errors (mangled proper nouns, homophones, \
garbled words). Return ONLY valid JSON — no prose, no code fences.

Output schema:
{
  "repairs": [
    {
      "original": "<word or short phrase as it appears>",
      "corrected": "<your best fix, or null if unsure>",
      "confidence": "high|medium|low",
      "timestamp": "MM:SS"
    }
  ]
}

Confidence guide:
- high   — you are certain of the correct word
- medium — very likely correct, minor doubt
- low    — possible error but you have two or more competing fixes

Only flag genuine errors; do not flag stylistic choices or filler words.
"""


def _repairs_validator(data: dict) -> None:
    """Validate the LLM response has the expected shape."""
    from pipeline.validation import ValidationError

    if not isinstance(data, dict) or "repairs" not in data:
        raise ValidationError("Response must be a dict with a 'repairs' key")
    if not isinstance(data["repairs"], list):
        raise ValidationError("'repairs' must be a list")
    for item in data["repairs"]:
        if "original" not in item or "confidence" not in item:
            raise ValidationError(f"Repair entry missing required fields: {item}")
        if item["confidence"] not in ("high", "medium", "low"):
            raise ValidationError(f"Invalid confidence value: {item['confidence']}")


def repair_transcript(transcript: str, context_hint: str = "") -> list[dict]:
    """Call the LLM to identify and correct likely speech-to-text errors.

    Args:
        transcript: Raw transcript text to inspect.
        context_hint: Optional hint about the subject matter (improves accuracy).

    Returns:
        List of repair dicts, each containing:
          - original (str)
          - corrected (str | None)
          - confidence ("high" | "medium" | "low")
          - timestamp (str, optional)
          - suggestions (list[str], optional)
    """
    user_content = transcript
    if context_hint:
        user_content = f"Context: {context_hint}\n\nTranscript:\n{transcript}"

    try:
        result = llm_json_call(
            pass_name="outline",
            system=_SYSTEM_PROMPT,
            user=user_content,
            validator=_repairs_validator,
        )
        return result.get("repairs", [])
    except Exception as exc:
        logger.warning("repair_transcript failed: %s", exc)
        return []


def format_repair_summary(repairs: list[dict], client_name: str, session_name: str) -> str:
    """Format a list of repair dicts into a human-readable Slack message.

    High and medium confidence repairs are listed as "Auto-corrected".
    Low confidence repairs are listed under "Needs review" with suggestions.

    Args:
        repairs: List of repair dicts from repair_transcript (or hand-crafted).
        client_name: Client display name.
        session_name: Session/episode display name.

    Returns:
        Formatted multi-line string ready to post to Slack.
    """
    header = f"Transcript cleanup for {client_name} — {session_name}"

    auto_repairs = [r for r in repairs if r.get("confidence") in ("high", "medium")]
    review_repairs = [r for r in repairs if r.get("confidence") == "low"]

    lines = [header]

    if auto_repairs:
        pairs = ", ".join(
            f'"{r["original"]}→{r["corrected"]}"'
            for r in auto_repairs
            if r.get("corrected")
        )
        count = len(auto_repairs)
        word = "word" if count == 1 else "words"
        lines.append(f"Auto-corrected ({count} {word}): {pairs}")

    if review_repairs:
        lines.append(f"Needs review ({len(review_repairs)} words):")
        for r in review_repairs:
            original = r.get("original", "?")
            timestamp = r.get("timestamp", "")
            line_no = r.get("line", "")
            suggestions = r.get("suggestions", [])

            # Build location string
            location_parts = []
            if line_no:
                location_parts.append(f"Line {line_no}")
            if timestamp:
                location_parts.append(f"({timestamp})")
            location = " ".join(location_parts) if location_parts else "unknown"

            if suggestions:
                suggestion_str = "/".join(suggestions)
                bullet = f'• {location}: "{original}" — [{suggestion_str}]?'
            else:
                bullet = f'• {location}: "{original}"'

            lines.append(bullet)

    return "\n".join(lines)
