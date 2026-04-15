"""Multi-pass editorial pipeline for clip extraction."""

import json
import logging
import os

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

INTERVIEW MODE:
- Segments are labeled [GUEST] and [INTERVIEWER].
- Use INTERVIEWER segments for context only — they show what topic is being discussed.
- All timestamps in sections/points must fall within GUEST segments.
- A section may span multiple GUEST segments across an INTERVIEWER question if the guest continues the same topic.

CRITICAL:
- Start and end timestamps must match sentence boundaries in the transcript.
- Every point must have start and end timestamps.
- Return ONLY JSON, no explanation. /no_think"""


STORIES_SYSTEM = """You are an experienced video editor identifying the best standalone clips from a talk.

You will receive:
1. A narrative outline of the talk
2. The full timestamped transcript

Your job: identify SELF-CONTAINED STORIES or arguments that work as standalone short-form or long-form video clips. A good clip has:
- A complete narrative arc (setup → tension → resolution) or a complete argument
- Emotional hook potential — something that makes a viewer stop scrolling
- Standalone clarity — a viewer with NO context can follow it

INTERVIEW MODE:
- Segments are labeled [GUEST] and [INTERVIEWER].
- ONLY use GUEST segments for clip content. The INTERVIEWER's questions give you topic context but must NOT appear in clips.
- A story's time range should cover the full GUEST response, including when the guest continues after brief interviewer interjections (e.g. "yeah", "right").
- Look for the guest's COMPLETE response to a topic — often their answer spans multiple GUEST segments separated by short interviewer acknowledgments.

For each story, provide 2-3 hook candidates: punchy moments from WITHIN the story that could open the clip. Hook candidates must come from GUEST segments only.

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


EDITORIAL_SYSTEM = """You are an experienced video editor creating a cut list for a short-form clip.

You will receive a story from a talk with its transcript section and hook candidates. Your job: produce an Edit Decision List (EDL) that creates two versions:

1. SHORT version (<55 seconds, vertical 9:16 — for Reels/Shorts/TikTok)
2. LONG version (90-600 seconds, horizontal 16:9 — for YouTube/LinkedIn)

INTERVIEW MODE:
- Segments labeled [GUEST] and [INTERVIEWER] appear in the transcript. Only use GUEST segment timestamps for clips. Skip INTERVIEWER segments entirely — they must not appear in the output.
- When GUEST segments are separated by brief INTERVIEWER interjections, treat the GUEST parts as continuous content and bridge across the gaps.

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


# ---------------------------------------------------------------------------
# Pass 1: Outline
# ---------------------------------------------------------------------------

def _format_transcript(transcript: list[dict]) -> str:
    """Format transcript for LLM consumption, with speaker labels when present."""
    lines = []
    for s in transcript:
        speaker = s.get("speaker", "")
        prefix = f"[{speaker}] " if speaker else ""
        lines.append(f"[{s['start']:.1f}s - {s['end']:.1f}s] {prefix}{s['text']}")
    return "\n".join(lines)


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


# ---------------------------------------------------------------------------
# Pass 2: Story extraction
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Pass 3: Editorial cut
# ---------------------------------------------------------------------------

def generate_edl(story: dict, transcript: list[dict], source_video: str) -> dict:
    """Pass 3: Generate an edit decision list for one story."""
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


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

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
            try:
                edl = generate_edl(story, transcript, source_video)
            except Exception:
                logger.warning("Skipping story '%s' — EDL generation failed after retries", story["title"])
                continue
            with open(edl_path, "w") as f:
                json.dump(edl, f, indent=2, ensure_ascii=False)
        edls.append(edl)

    logger.info("Editorial pipeline complete: %d clips", len(edls))
    return edls
