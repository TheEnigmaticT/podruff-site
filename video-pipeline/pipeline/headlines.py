import json
import re
import requests
from pipeline.config import OLLAMA_MODEL
from pipeline.retry import retry

OLLAMA_URL = "http://localhost:11434/api/generate"


def _extract_json(text: str) -> str:
    """Strip thinking tags and extract JSON from LLM response."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    for match in re.finditer(r"[\[{]", text):
        try:
            candidate = text[match.start():]
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            continue
    return text

HEADLINE_PROMPT = """Generate a YouTube headline and short description for this video topic.

Topic: {topic_name}

Transcript:
{transcript}

The headline should be punchy, under 60 characters, and make people want to click. The description should be 1-2 sentences for social media.

Return ONLY JSON: {{"headline": "...", "description": "..."}} /no_think"""


@retry(max_attempts=3, exceptions=(requests.RequestException,))
def generate_headline(topic: dict) -> dict:
    transcript_text = "\n".join(s["text"] for s in topic["segments"])
    resp = requests.post(OLLAMA_URL, json={
        "model": OLLAMA_MODEL,
        "prompt": HEADLINE_PROMPT.format(topic_name=topic["topic"], transcript=transcript_text),
        "stream": False,
        "options": {"num_ctx": 16384},
    })
    resp.raise_for_status()
    return json.loads(_extract_json(resp.json()["response"]))
