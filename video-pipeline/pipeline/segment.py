import json
import re
import requests
from pipeline.config import OLLAMA_MODEL

OLLAMA_URL = "http://localhost:11434/api/generate"


def _extract_json(text: str) -> str:
    """Strip thinking tags and extract JSON from LLM response."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    # Try to find JSON array or object
    for match in re.finditer(r"[\[{]", text):
        try:
            candidate = text[match.start():]
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            continue
    return text

SEGMENTATION_PROMPT = """You are analyzing a video transcript to identify distinct topic segments for short-form video clips.

Here is the timestamped transcript:

{transcript}

Identify the distinct topics discussed. For each topic, provide:
- "topic": a short descriptive name (3-6 words)
- "start": the start timestamp (float seconds) — must be at the START of a sentence
- "end": the end timestamp (float seconds) — must be at the END of a complete sentence. Never cut off mid-thought.

CRITICAL: Each topic MUST start and end at a natural sentence boundary. The "end" timestamp must be AFTER the last word of the final sentence in the topic, not before it.

Return ONLY a JSON array. No explanation. No thinking. Example:
[{{"topic": "Topic Name", "start": 0.0, "end": 60.0}}] /no_think"""


def segment_topics(transcript: list[dict]) -> list[dict]:
    """Identify topic boundaries in a transcript using Ollama."""
    transcript_text = "\n".join(
        f"[{s['start']:.1f}s - {s['end']:.1f}s] {s['text']}"
        for s in transcript
    )
    resp = requests.post(OLLAMA_URL, json={
        "model": OLLAMA_MODEL,
        "prompt": SEGMENTATION_PROMPT.format(transcript=transcript_text),
        "stream": False,
        "options": {"num_ctx": 32768},
    })
    resp.raise_for_status()
    topics = json.loads(_extract_json(resp.json()["response"]))

    # Assign transcript segments to topics using overlap rather than strict containment.
    # A segment belongs to a topic if its midpoint falls within the topic range.
    for topic in topics:
        topic["segments"] = [
            s for s in transcript
            if (s["start"] + s["end"]) / 2 >= topic["start"]
            and (s["start"] + s["end"]) / 2 <= topic["end"]
        ]
        # Extend topic end to cover the full final segment
        if topic["segments"]:
            topic["end"] = max(topic["end"], topic["segments"][-1]["end"])
            topic["start"] = min(topic["start"], topic["segments"][0]["start"])
    return topics
