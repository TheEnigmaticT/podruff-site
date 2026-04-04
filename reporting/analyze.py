import os
import json
from pathlib import Path
from typing import List, Tuple
import anthropic

VAULT = Path(os.environ.get(
    "OBSIDIAN_VAULT",
    str(Path.home() / "Documents/Obsidian/CrowdTamers Obsidian Vault")
))
SKILLS_BASE = VAULT / "_meta/skills/marketing"

# IMPORTANT: web-search for current model ID before changing this
MODEL = "claude-sonnet-4-6"

SKILL_MAP = {
    "ga4": "analytics-analysis",
    "search_console": "search-console-analysis",
    "youtube": "youtube-shorts-analysis",
    "google_ads": "paid-ads",
}


def load_skills(skill_names: List[str]) -> str:
    """Load and concatenate Obsidian skill files as system context."""
    parts = []
    for name in skill_names:
        path = SKILLS_BASE / f"{name}.md"
        if not path.exists():
            raise FileNotFoundError(f"Skill not found: {path}")
        parts.append(path.read_text())
    return "\n\n---\n\n".join(parts)


def _format_data(raw_data: dict, channel_order: List[str]) -> str:
    """Format raw API data as readable markdown sections."""
    sections = []
    for channel in channel_order:
        if channel not in raw_data:
            continue
        data = raw_data[channel]
        label = channel.upper().replace("_", " ")
        # Truncate to avoid context overflow
        data_str = json.dumps(data, indent=2)[:3000]
        sections.append(f"## {label}\n\n```json\n{data_str}\n```")
    return "\n\n".join(sections)


def build_prompt(raw_data: dict, skill_names: List[str], reverse: bool = False) -> str:
    """Build the user prompt. reverse=True changes channel section order for Pass B."""
    channels = list(raw_data.keys())
    if reverse:
        channels = list(reversed(channels))
    data_section = _format_data(raw_data, channels)
    return (
        "Analyze the following marketing data and produce a complete weekly report.\n\n"
        f"{data_section}\n\n"
        "Be specific — use the actual numbers from the data. "
        "Do not add numbers that are not in the data."
    )


def run_analysis_pass(user_prompt: str, system_prompt: str) -> str:
    """Call Claude API for one analysis pass. Returns the text response."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set. Add it to .env.")
    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return response.content[0].text


def analyze(raw_data: dict, client_config: dict) -> Tuple[str, str]:
    """
    Run two independent analysis passes (Pass A and Pass B).
    Returns (pass_a_text, pass_b_text) for reconciliation.
    """
    channels = client_config.get("channels", [])
    skill_names = [SKILL_MAP[c] for c in channels if c in SKILL_MAP]
    system_prompt = load_skills(skill_names)

    prompt_a = build_prompt(raw_data, skill_names, reverse=False)
    prompt_b = build_prompt(raw_data, skill_names, reverse=True)

    pass_a = run_analysis_pass(prompt_a, system_prompt)
    pass_b = run_analysis_pass(prompt_b, system_prompt)

    return pass_a, pass_b
