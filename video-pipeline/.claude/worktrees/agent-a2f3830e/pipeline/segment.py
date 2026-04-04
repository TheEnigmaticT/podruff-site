import json
import requests
from pipeline.config import OLLAMA_MODEL

OLLAMA_URL = "http://localhost:11434/api/generate"

SEGMENTATION_PROMPT = """You are analyzing a video transcript to identify distinct topic segments.

Here is the timestamped transcript:

{transcript}

Identify the distinct topics discussed. For each topic, provide:
- "topic": a short descriptive name (3-6 words)
- "start": the start timestamp (float seconds)
- "end": the end timestamp (float seconds)

Return ONLY a JSON array. No explanation. Example:
[{{"topic": "Topic Name", "start": 0.0, "end": 60.0}}]"""


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
    })
    resp.raise_for_status()
    topics = json.loads(resp.json()["response"])

    for topic in topics:
        topic["segments"] = [
            s for s in transcript
            if s["start"] >= topic["start"] and s["end"] <= topic["end"]
        ]
    return topics
