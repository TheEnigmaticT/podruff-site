"""Post-call analysis — generates summary, reconciles suggestions against transcript."""

import logging
import time
from typing import Dict, List, Optional

import requests

import config
from suggestion_engine import Suggestion, SuggestionStatus, SuggestionType

logger = logging.getLogger(__name__)

POST_CALL_PROMPT = """You are analyzing a completed podcast recording. Given the full transcript and the suggestions that were generated during the call, produce a structured analysis.

Full transcript:
{transcript}

Suggestions generated during the call:
{suggestions}

Provide:
1. TOPICS DISCUSSED: List the main topics that were covered (one per line)
2. TOPICS NOT DISCUSSED: Topics that were raised but not explored (one per line)
3. SAID HOOKS: Suggestions from the list above that the speaker actually said or closely paraphrased (list the suggestion ID and the matching transcript text)
4. FOLLOW-UPS FOR NEXT TIME: Interesting threads that weren't pursued — good starting points for a follow-up conversation (one per line)

Format each section with the header on its own line, then items below it."""


def generate_post_call_analysis(
    transcript_text: str,
    suggestions: List[Suggestion],
    start_time: float,
) -> Dict:
    """
    Run post-call analysis against the full transcript.

    Returns a dict with:
        topics_discussed: List[str]
        topics_not_discussed: List[str]
        said_ids: List[str]  (suggestion IDs that appear in transcript)
        followups_next_time: List[str]
        raw_analysis: str
    """
    # Format suggestions for the prompt
    suggestion_lines = []
    for s in suggestions:
        elapsed = _format_elapsed(s.timestamp - start_time)
        suggestion_lines.append(f"[{elapsed}] ({s.id}) [{s.type.value}] {s.text} — status: {s.status.value}")

    prompt = POST_CALL_PROMPT.format(
        transcript=transcript_text,
        suggestions="\n".join(suggestion_lines) or "(no suggestions generated)",
    )

    try:
        resp = requests.post(
            config.OLLAMA_URL,
            json={"model": config.OLLAMA_MODEL, "prompt": prompt, "stream": False},
            timeout=60,  # longer timeout for full analysis
        )
        resp.raise_for_status()
        raw = resp.json().get("response", "")
    except Exception as e:
        logger.error("Post-call analysis failed: %s", e)
        return {
            "topics_discussed": [],
            "topics_not_discussed": [],
            "said_ids": [],
            "followups_next_time": [],
            "raw_analysis": f"Analysis failed: {e}",
        }

    return _parse_analysis(raw, suggestions)


def _parse_analysis(raw: str, suggestions: List[Suggestion]) -> Dict:
    """Parse the LLM analysis response into structured sections."""
    result = {
        "topics_discussed": [],
        "topics_not_discussed": [],
        "said_ids": [],
        "followups_next_time": [],
        "raw_analysis": raw,
    }

    current_section = None
    for line in raw.split("\n"):
        line_lower = line.strip().lower()

        if "topics discussed" in line_lower and "not" not in line_lower:
            current_section = "discussed"
            continue
        elif "topics not discussed" in line_lower or "not discussed" in line_lower:
            current_section = "not_discussed"
            continue
        elif "said" in line_lower and ("hook" in line_lower or "suggestion" in line_lower):
            current_section = "said"
            continue
        elif "next time" in line_lower or "follow-up" in line_lower:
            current_section = "next_time"
            continue

        cleaned = line.strip().strip("-•*").strip()
        if not cleaned:
            continue

        if current_section == "discussed":
            result["topics_discussed"].append(cleaned)
        elif current_section == "not_discussed":
            result["topics_not_discussed"].append(cleaned)
        elif current_section == "said":
            # Try to extract suggestion IDs mentioned
            for s in suggestions:
                if s.id in cleaned:
                    result["said_ids"].append(s.id)
        elif current_section == "next_time":
            result["followups_next_time"].append(cleaned)

    return result


def format_markdown_output(
    suggestions: List[Suggestion],
    transcript_segments: List,
    start_time: float,
    analysis: Optional[Dict] = None,
) -> str:
    """
    Format the complete call output as Markdown matching PRD Appendix C.

    Args:
        suggestions: All suggestions from the call.
        transcript_segments: List of dicts with 'text' and 'timestamp'.
        start_time: Wall-clock time when recording started.
        analysis: Optional post-call analysis dict.
    """
    from datetime import datetime
    now = datetime.now()
    duration_minutes = (time.time() - start_time) / 60

    lines = [
        "---",
        f"date: {now.strftime('%Y-%m-%d')}",
        f"start_time: {datetime.fromtimestamp(start_time).strftime('%H:%M:%S')}",
        f"duration_minutes: {int(duration_minutes)}",
        "---",
        "",
        f"# Call Recording — {now.strftime('%Y-%m-%d %H:%M')}",
        "",
    ]

    # Group suggestions by status
    used = [s for s in suggestions if s.status == SuggestionStatus.USED]
    good = [s for s in suggestions if s.status == SuggestionStatus.GOOD]
    bad = [s for s in suggestions if s.status == SuggestionStatus.BAD]
    new = [s for s in suggestions if s.status == SuggestionStatus.NEW]

    # Hooks section
    hooks = [s for s in suggestions if s.type == SuggestionType.HOOK]
    if hooks:
        lines.append("## Hooks Generated")
        lines.append("")
        hooks_used = [s for s in hooks if s.status in (SuggestionStatus.USED, SuggestionStatus.GOOD)]
        hooks_unused = [s for s in hooks if s.status == SuggestionStatus.NEW]
        hooks_rejected = [s for s in hooks if s.status == SuggestionStatus.BAD]

        if hooks_used:
            lines.append("### Used")
            for h in hooks_used:
                elapsed = _format_elapsed(h.timestamp - start_time)
                mark = "USED" if h.status == SuggestionStatus.USED else "GOOD"
                lines.append(f'- [{elapsed}] "{h.text}" {mark}')
            lines.append("")

        if hooks_unused:
            lines.append("### Unused")
            for h in hooks_unused:
                elapsed = _format_elapsed(h.timestamp - start_time)
                lines.append(f'- [{elapsed}] "{h.text}"')
            lines.append("")

        if hooks_rejected:
            lines.append("### Rejected")
            for h in hooks_rejected:
                elapsed = _format_elapsed(h.timestamp - start_time)
                lines.append(f'- [{elapsed}] "{h.text}"')
            lines.append("")

    # Topics section
    topics = [s for s in suggestions if s.type == SuggestionType.TOPIC]
    if topics:
        lines.append("## Topics Flagged")
        lines.append("")
        for t in topics:
            elapsed = _format_elapsed(t.timestamp - start_time)
            status_mark = f" [{t.status.value.upper()}]" if t.status != SuggestionStatus.NEW else ""
            lines.append(f"- [{elapsed}] {t.text}{status_mark}")
        lines.append("")

    # Follow-ups section
    followups = [s for s in suggestions if s.type == SuggestionType.FOLLOWUP]
    if followups:
        lines.append("## Follow-up Questions")
        lines.append("")
        for f in followups:
            elapsed = _format_elapsed(f.timestamp - start_time)
            status_mark = f" [{f.status.value.upper()}]" if f.status != SuggestionStatus.NEW else ""
            lines.append(f"- [{elapsed}] {f.text}{status_mark}")
        lines.append("")

    # Post-call analysis sections
    if analysis:
        if analysis.get("topics_discussed"):
            lines.append("## Topics Discussed")
            lines.append("")
            for t in analysis["topics_discussed"]:
                lines.append(f"- {t}")
            lines.append("")

        if analysis.get("topics_not_discussed"):
            lines.append("## Topics Not Discussed")
            lines.append("")
            for t in analysis["topics_not_discussed"]:
                lines.append(f"- {t}")
            lines.append("")

        if analysis.get("followups_next_time"):
            lines.append("## Follow-ups for Next Time")
            lines.append("")
            for f in analysis["followups_next_time"]:
                lines.append(f"- {f}")
            lines.append("")

    # Full transcript
    lines.append("## Full Transcript")
    lines.append("")
    for seg in transcript_segments:
        elapsed = _format_elapsed(seg["timestamp"] - start_time)
        lines.append(f"[{elapsed}] {seg['text']}")
        lines.append("")

    return "\n".join(lines)


def _format_elapsed(seconds: float) -> str:
    s = max(0, int(seconds))
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    return f"{h:02d}:{m:02d}:{sec:02d}"
