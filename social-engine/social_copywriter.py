"""Generate platform-specific social post text from video clip transcripts using Claude.

Prompt assembly loads external markdown files from social_prompts/:
  copywriting.md   -- banned words, style rules ("Quit Writing Like AI")
  hooks.md         -- 100 Top Hooks reference list
  platforms.md     -- platform-specific output format, CTAs, critical instructions
  clients/{name}.md -- client-specific voice/audience (optional)

These files are the source of truth for all copywriting guidance.
The Python code handles only assembly and API plumbing.
"""

import json
import logging
import os
import re

import anthropic

from social_config import PLATFORM_TEXT_FIELDS

log = logging.getLogger("social_copywriter")

PROMPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "social_prompts")


def _load_prompt_file(filename):
    """Load a markdown file from social_prompts/. Returns empty string if not found."""
    path = os.path.join(PROMPTS_DIR, filename)
    try:
        with open(path, "r") as f:
            return f.read()
    except OSError:
        log.warning("Prompt file not found: %s", path)
        return ""


def load_prompt_context(client_name=None):
    """Load and concatenate all prompt context files."""
    parts = []

    copywriting = _load_prompt_file("copywriting.md")
    if copywriting:
        parts.append(copywriting)

    hooks = _load_prompt_file("hooks.md")
    if hooks:
        parts.append(hooks)

    platforms = _load_prompt_file("platforms.md")
    if platforms:
        parts.append(platforms)

    if client_name:
        client_file = _load_prompt_file(f"clients/{client_name}.md")
        if client_file:
            parts.append(client_file)

    return "\n\n---\n\n".join(parts)


def build_copy_prompt(clip_transcript, full_transcript, platforms, hook, title,
                      client_name=None):
    """Build the Claude prompt for social copy generation."""
    return f"""## Video Context (full transcript for reference)
{full_transcript}

## Clip Transcript (this is what the video clip actually says)
{clip_transcript}

## Clip Info
- Title: {title}
- Hook: {hook}

## Task
Generate one social media post for EACH of these platforms: {", ".join(platforms)}

Rules:
- Extract and repurpose the speaker's words -- don't invent content
- Write from the perspective of someone sharing this insight, NOT summarizing a video
- The text should stand alone -- never reference "this video" or "this clip"
- Use the hook as inspiration but don't copy it verbatim
- Each platform version should feel native to that platform
- Follow the copywriting guidelines, hooks reference, and platform formats provided in your instructions
- X/Twitter MUST be 280 characters or fewer
- Bluesky MUST be 300 characters or fewer

## Output Format
Return ONLY a JSON object with platform names as keys and post text as values.
No markdown wrapping, no explanation, no preamble.

{{"linkedin": "...", "twitter": "...", "instagram": "..."}}"""


def parse_copy_response(response_text):
    """Parse Claude's JSON response into a dict of platform -> text."""
    text = response_text.strip()
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace == -1 or last_brace == -1 or last_brace <= first_brace:
        log.warning("No JSON object found in Claude response: %s", text[:200])
        return {}
    json_str = text[first_brace:last_brace + 1]
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        log.warning("Failed to parse extracted JSON: %s", json_str[:200])
        return {}


DEFAULT_MODEL = "claude-sonnet-4-5-20250929"


def call_claude(system_prompt, user_prompt, api_key, model=None):
    """Call Claude API with system + user prompts. Returns response text."""
    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model=model or DEFAULT_MODEL,
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return message.content[0].text


def fetch_transcript(session_folder_id, google_creds_path):
    """Fetch transcript.srt from a Drive session folder. Returns plain text."""
    try:
        from call_task_crawler import refresh_google_token
    except ImportError:
        raise ImportError(
            "fetch_transcript requires call_task_crawler.refresh_google_token. "
            "Install openclaw-kanban or provide the function separately."
        )
    import urllib.request
    import urllib.parse

    token = refresh_google_token(google_creds_path)

    query = f"'{session_folder_id}' in parents and name = 'transcript.srt'"
    params = urllib.parse.urlencode({"q": query, "fields": "files(id,name)"})
    req = urllib.request.Request(
        f"https://www.googleapis.com/drive/v3/files?{params}",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        files = json.loads(resp.read()).get("files", [])

    if not files:
        return None

    file_id = files[0]["id"]
    dl_url = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media"
    req = urllib.request.Request(dl_url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8")


DRIVE_ID_RE = re.compile(
    r"(?:folders/|file/d/|open\?id=|id=)([a-zA-Z0-9_-]+)"
)


def extract_drive_id(url_or_id):
    """Extract a bare Google Drive file/folder ID from a URL or pass through a bare ID."""
    if not url_or_id:
        return None
    match = DRIVE_ID_RE.search(url_or_id)
    if match:
        return match.group(1)
    if "/" not in url_or_id and len(url_or_id) > 10:
        return url_or_id
    return None


SRT_TIMESTAMP_RE = re.compile(r"^\d{2}:\d{2}:\d{2}[,.]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[,.]\d{3}")
SRT_SEQUENCE_RE = re.compile(r"^\d+$")


def parse_srt_to_text(srt_content):
    """Extract plain text from SRT format, stripping timestamps and sequence numbers."""
    lines = []
    prev_was_timestamp = False
    for line in srt_content.splitlines():
        stripped = line.strip()
        if not stripped:
            prev_was_timestamp = False
            continue
        if SRT_TIMESTAMP_RE.match(stripped):
            prev_was_timestamp = True
            continue
        if SRT_SEQUENCE_RE.match(stripped) and not prev_was_timestamp:
            continue
        prev_was_timestamp = False
        lines.append(stripped)
    return " ".join(lines)


def extract_clip_segment(full_text, hook_sentence, topic_name, clip_start=None, clip_end=None):
    """Extract the clip's transcript segment (~2500 chars around the hook.

    If clip_start/clip_end are provided (character positions), uses those directly.
    Otherwise falls back to hook-based search, then topic name, then beginning of text.
    """
    if clip_start is not None and clip_end is not None:
        # Widen slightly for context
        start = max(0, int(clip_start) - 500)
        end = min(len(full_text), int(clip_end) + 500)
        return full_text[start:end]
    if hook_sentence and hook_sentence in full_text:
        idx = full_text.index(hook_sentence)
        start = max(0, idx - 500)
        end = min(len(full_text), idx + 2000)
        return full_text[start:end]
    if topic_name:
        for word in topic_name.split()[:3]:
            if len(word) > 4 and word.lower() in full_text.lower():
                idx = full_text.lower().index(word.lower())
                start = max(0, idx - 500)
                end = min(len(full_text), idx + 2000)
                return full_text[start:end]
    return full_text[:3000]


def extract_surrounding_context(full_text, hook_sentence, context_chars=12000):
    """Extract a wider window around the clip for full video context."""
    if hook_sentence and hook_sentence in full_text:
        idx = full_text.index(hook_sentence)
        half = context_chars // 2
        start = max(0, idx - half)
        end = min(len(full_text), idx + half)
        return full_text[start:end]
    return full_text[:context_chars]


def generate_social_copy(page_id, notion_client, config, force=True):
    """Main entry point: generate social copy for a clip and write to Notion."""
    row = notion_client.get_page(page_id)

    platforms = row.get("Platforms", [])
    if not platforms:
        log.warning("No platforms set for %s, skipping", page_id)
        return

    if not force:
        for platform in platforms:
            field = PLATFORM_TEXT_FIELDS.get(platform, "")
            if field and row.get(field, ""):
                log.info("Skipping %s -- existing platform text found (use force=True to overwrite)",
                         row.get("Title", page_id))
                return

    transcript_text = None
    has_transcript = False
    parent_id = notion_client.get_parent_video_id(page_id)
    google_creds = os.path.expanduser(config.get("google_creds_path", ""))
    if parent_id and google_creds and os.path.exists(google_creds):
        parent_row = notion_client.get_page(parent_id)
        source_url = parent_row.get("Source Video", "")
        folder_id = extract_drive_id(source_url) if source_url else None
        if folder_id:
            try:
                srt = fetch_transcript(folder_id, google_creds)
                if srt:
                    transcript_text = parse_srt_to_text(srt)
                    has_transcript = True
            except Exception:
                log.exception("Failed to fetch transcript for parent %s", parent_id)

    if not has_transcript:
        log.warning("No transcript found for %s -- using fallback context", row.get("Title", page_id))

    raw_text = transcript_text or row.get("Description", "") or "No transcript available."
    hook = row.get("Hook Sentence", "")
    topic = row.get("Topic Name", "")

    # Parse timestamp tag from description if present
    clip_start = None
    clip_end = None
    desc = row.get("Description", "")
    clip_tag = re.search(r"\[clip:([\d.]+)-([\d.]+)\]", desc)
    if clip_tag:
        clip_start = float(clip_tag.group(1))
        clip_end = float(clip_tag.group(2))

    clip_text = extract_clip_segment(raw_text, hook, topic, clip_start=clip_start, clip_end=clip_end)
    full_text = extract_surrounding_context(raw_text, hook)

    client_name = config.get("client_name")
    system_prompt = load_prompt_context(client_name)
    user_prompt = build_copy_prompt(
        clip_transcript=clip_text,
        full_transcript=full_text,
        platforms=platforms,
        hook=hook,
        title=row.get("Title", ""),
        client_name=client_name,
    )

    api_key = config.get("anthropic_api_key", os.environ.get("ANTHROPIC_API_KEY", ""))
    if not api_key:
        log.error("No Anthropic API key configured")
        return

    model = config.get("anthropic_model")
    response = call_claude(system_prompt, user_prompt, api_key, model=model)
    platform_texts = parse_copy_response(response)

    if not platform_texts:
        log.warning("Claude returned no usable platform texts for %s", page_id)
        return

    properties = {}
    for platform, text in platform_texts.items():
        field_name = PLATFORM_TEXT_FIELDS.get(platform)
        if field_name:
            chunks = [text[i:i+2000] for i in range(0, len(text), 2000)]
            properties[field_name] = {
                "rich_text": [{"text": {"content": c}} for c in chunks]
            }

    if not has_transcript:
        properties["Posting Errors"] = {
            "rich_text": [{"text": {"content": "GENERATED_WITHOUT_TRANSCRIPT"}}]
        }

    if properties:
        notion_client.update_page(page_id, properties)
        log.info("Wrote %d platform texts for %s (transcript: %s)",
                 len(platform_texts), row.get("Title", page_id),
                 "yes" if has_transcript else "NO -- fallback context used")
