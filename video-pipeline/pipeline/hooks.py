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

HOOK_PROMPT = """You are selecting a HOOK for a short-form video clip. The hook will be CUT from its position in the video and PLACED AT THE VERY BEGINNING as the intro, before the main content plays. It can come from ANYWHERE in the transcript — beginning, middle, or end.

Topic: {topic_name}

Transcript:
{transcript}

A great hook is ONE of:
- A bold/controversial claim ("Most people get this completely wrong")
- A surprising fact or stat that creates curiosity
- A direct challenge to the viewer ("Stop — don't do X until you hear this")
- A teaser that creates an information gap ("There's one thing nobody tells you about X")
- A provocative question that demands an answer

Search the ENTIRE transcript — the best hook is often buried in the middle or near the end, not at the start. Pick the 1-3 second segment (NOT a full sentence — just the punchiest phrase) that would make a viewer stop scrolling. Shorter is better.

Return ONLY JSON: {{"sentence": "the exact words", "start": <float>, "end": <float>}} /no_think"""


@retry(max_attempts=3, exceptions=(requests.RequestException,))
def select_hook(topic: dict) -> dict:
    transcript_text = "\n".join(
        f"[{s['start']:.1f}s - {s['end']:.1f}s] {s['text']}"
        for s in topic["segments"]
    )
    resp = requests.post(OLLAMA_URL, json={
        "model": OLLAMA_MODEL,
        "prompt": HOOK_PROMPT.format(topic_name=topic["topic"], transcript=transcript_text),
        "stream": False,
        "options": {"num_ctx": 16384},
    })
    resp.raise_for_status()
    return json.loads(_extract_json(resp.json()["response"]))
