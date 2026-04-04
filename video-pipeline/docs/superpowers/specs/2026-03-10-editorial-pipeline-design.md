# Multi-Pass Editorial Pipeline — Design Spec

**Goal:** Replace single-pass topic segmentation with a multi-pass editorial pipeline that identifies self-contained stories, selects hooks with narrative coherence, and produces edit decisions for both short-form (<55s vertical) and long-form (90-600s horizontal) clips.

**Owner:** Trevor Longino

**Date:** 2026-03-10

---

## Problem

The current pipeline segments transcripts by "topic" — a shallow categorization that doesn't align with how humans identify good clips. Topics lump intro chitchat with compelling stories, produce mid-thought cutoffs, and pair hooks with the wrong content. The result: clips that are technically correct (crop, subtitles, translation all work) but editorially weak.

A good clip is a **self-contained story or argument with narrative closure**, not a topic chunk. Identifying these requires structural understanding of the full transcript, editorial judgment about standalone potential, and careful cut-point selection.

## Approach

Three-pass LLM pipeline, each pass focused on one editorial task. Model-agnostic (any OpenAI-compatible API). Start with local Ollama models, upgrade individual passes to cloud models if quality requires it.

All intermediate outputs are JSON saved to disk, so any pass can be inspected, edited, or re-run independently.

## Architecture

```
Transcript (from Parakeet-MLX, ~37s for 44-min video)
    │
    ▼
Pass 1: OUTLINE
    │   Full transcript → hierarchical narrative structure
    │   Model: qwen3:8b (fast, structural task)
    │
    ▼
Pass 2: STORY EXTRACTION
    │   Outline + transcript → ranked self-contained stories
    │   Model: qwen3:30b (editorial judgment)
    │
    ▼
Pass 3: EDITORIAL CUT (per story)
    │   Single story's transcript → edit decision list
    │   Model: qwen3:30b (editorial, small context per call)
    │
    ▼
EDL → Kdenlive XML + Draft Render (FFmpeg)
```

---

## Pass Specifications

### Pass 1: OUTLINE

**Purpose:** Produce a hierarchical narrative structure. Forces the LLM to understand the talk's structure before any clip decisions are made.

**Input:** Timestamped transcript (sentence-level from Parakeet).

**Output:**
```json
{
  "title": "Jonathan Brill - Future-Proofing Your Business",
  "sections": [
    {
      "heading": "Introduction",
      "start": 3.5, "end": 37.2,
      "points": [
        {"text": "Excited to work with international team", "start": 3.5, "end": 10.2},
        {"text": "Sales orgs get excited", "start": 10.2, "end": 18.5},
        {"text": "Who is Jonathan / futurist role", "start": 18.5, "end": 37.2}
      ]
    },
    {
      "heading": "How This Got Started - Riptide Story",
      "start": 37.2, "end": 120.0,
      "points": [
        {"text": "Swimming in Thailand", "start": 37.2, "end": 55.0},
        {"text": "Riptide metaphor - fighting harder makes it worse", "start": 55.0, "end": 85.0},
        {"text": "How organizations survive rogue waves", "start": 85.0, "end": 120.0}
      ]
    }
  ]
}
```

**Model:** qwen3:8b — comprehension/summarization task, not editorial judgment.

**Saved to:** `{output_dir}/outline.json`

### Pass 2: STORY EXTRACTION

**Purpose:** Identify which sections work as self-contained stories for clips. Score for standalone engagement. Suggest hook candidates.

**Input:** Outline from Pass 1 + full transcript.

**Output:**
```json
{
  "stories": [
    {
      "id": "riptide",
      "title": "Fighting the Riptide",
      "outline_section": "How This Got Started - Riptide Story",
      "start": 37.2, "end": 120.0,
      "engagement_score": 9,
      "standalone_rationale": "Complete narrative arc with universal metaphor. Works without context.",
      "format": "both",
      "hook_candidates": [
        {"text": "The harder I swam, the harder I got pulled back to sea", "start": 62.3, "end": 67.1},
        {"text": "I had maybe a minute of energy left", "start": 71.0, "end": 74.2}
      ]
    }
  ]
}
```

**Fields:**
- `engagement_score`: 1-10, how compelling as a standalone clip.
- `standalone_rationale`: Why this works (or doesn't) without surrounding context.
- `format`: `"short"` (<55s), `"long"` (90-600s), or `"both"`.
- `hook_candidates`: 2-3 punchy moments from within the story that could open the clip.

**Selection criteria:** Stories with engagement_score >= 7 proceed to Pass 3. Target ~3x more shorts than long-forms (e.g., 6 shorts + 2 long-forms from a 44-min talk).

**Model:** qwen3:30b — requires editorial judgment about standalone potential.

**Saved to:** `{output_dir}/stories.json`

### Pass 3: EDITORIAL CUT (per story)

**Purpose:** For each selected story, produce a complete edit decision list (EDL): hook choice, body arrangement, trims for the short version, cut points.

**Input:** Single story's transcript section + hook candidates from Pass 2.

**Output:**
```json
{
  "story_id": "riptide",
  "source_video": "I7X4Lrkj2IU.mp4",
  "versions": {
    "short": {
      "target_duration": 55,
      "segments": [
        {
          "type": "hook",
          "start": 62.3, "end": 67.1,
          "narrative_bridge": "Opens with the core tension the story resolves"
        },
        {"type": "body", "start": 37.2, "end": 62.3},
        {"type": "body", "start": 67.1, "end": 98.5}
      ],
      "trims": [
        {"start": 42.0, "end": 49.0, "reason": "Redundant scene-setting, tightens pacing"}
      ],
      "estimated_duration": 53.2
    },
    "long": {
      "target_duration": null,
      "segments": [
        {
          "type": "hook",
          "start": 62.3, "end": 67.1,
          "narrative_bridge": "Opens with the core tension the story resolves"
        },
        {"type": "body", "start": 37.2, "end": 120.0}
      ],
      "trims": [],
      "estimated_duration": 87.6
    }
  }
}
```

**Hook rules:**
- Hook must come from within the selected story.
- The LLM must provide a `narrative_bridge` explaining why this hook flows into the body.
- The hook's original position in the body is skipped (no repetition) unless the LLM judges repetition adds value.

**Trimming constraints:**
- No segment shorter than 7 seconds (avoids choppy cuts).
- Short version target: <55 seconds (4s reserved for end card).
- Trims must include a reason (debuggable).
- Prefer trimming setup/context over trimming the payoff.

**Model:** qwen3:30b — editorial judgment, small context (one story at a time).

**Saved to:** `{output_dir}/edits/{story_id}.json`

---

## Validation & Error Handling

### JSON Schema Enforcement

Each pass uses OpenAI-compatible `response_format: { type: "json_object" }` when available. All pass outputs are validated against a JSON Schema before being accepted. Schemas are defined in code (Python dicts or Pydantic models) — not in this spec, but the example outputs above are canonical.

**Validation per pass:**
- Pass 1: Every section must have `heading`, `start`, `end`, `points`. Every point must have `text`, `start`, `end`. Timestamps must be monotonically increasing.
- Pass 2: Every story must have `id`, `title`, `start`, `end`, `engagement_score` (int 1-10), `format` (enum: short/long/both), `hook_candidates` (non-empty list). Each hook candidate must have `text`, `start`, `end`.
- Pass 3: Every version must have `segments` (non-empty), `estimated_duration`. Segments must have `type` (hook/body), `start`, `end`. Hook segments must have `narrative_bridge`. No segment shorter than 7s. Short version estimated_duration must be <= 55s.

**On validation failure:** Retry the LLM call up to 3 times with the validation error appended to the prompt ("Your output had these errors: ..."). If all retries fail, log the error and skip that story/pass, continuing with remaining items.

### Timestamp Snapping

LLMs approximate timestamps from the transcript text. All timestamps in pass outputs are **snapped to the nearest transcript sentence boundary** in a post-processing step:

- Each LLM-generated timestamp is matched to the closest `start` or `end` value from the transcript's sentence list.
- This happens after each pass, before the output is saved to disk.
- The snap tolerance is 2.0 seconds. If the nearest boundary is >2s away, log a warning but use the snapped value anyway.

This ensures clips always cut on sentence boundaries, never mid-word.

### Pass Cascade Validation

Between passes, a lightweight check confirms the upstream output is consumable:
- Before Pass 2: outline.json exists and has >=1 section.
- Before Pass 3: stories.json exists and has >=1 story with engagement_score >= 7.
- Before rendering: EDL JSON exists and all referenced timestamps fall within the source video duration.

### Retry & Timeout

All Ollama calls use a 600-second timeout (qwen3:30b can be slow on long contexts). HTTP failures retry up to 3 times with exponential backoff (existing `pipeline/retry.py` pattern).

---

## EDL Segment & Trim Semantics

**Segments** define the playback order — a list of time ranges from the source video, played in sequence. The renderer concatenates these segments.

**Trims** are subtractive regions *within* segments. When a trim falls inside a segment's time range, the renderer splits that segment into two sub-segments around the trim. Example:

```
Segment: body 37.2 - 62.3
Trim:    42.0 - 49.0
Result:  play 37.2-42.0, then play 49.0-62.3
```

**Hook deduplication:** The LLM produces body segments pre-split around the hook's original position. The renderer does NOT auto-split — it plays exactly the segments listed. If the LLM wants to include the hook's original occurrence, it includes that time range in the body segments. If not, it splits the body around it (as shown in the Pass 3 example).

---

## Long-Form Rendering

Long-form clips (90-600s) are rendered at **16:9, original resolution** (no crop). Subtitles are standard SRT-style (not karaoke) burned in with a readable font at ~48px. Face-centered cropping is NOT applied — long-form assumes a landscape viewing context.

Long-form Kdenlive projects use the same EDL-to-XML generation as shorts, just without the crop filter.

---

## Kdenlive XML Generation

Kdenlive uses MLT XML format. The generator produces a minimal valid project:

- One `<producer>` for the source video file.
- One `<playlist>` (video track) containing `<entry>` elements for each EDL segment with `in`/`out` attributes.
- One `<playlist>` (subtitle track) referencing an ASS file (for shorts with karaoke) or SRT file (for long-form).
- Profile set to 1080x1920@30fps for shorts, source resolution@source-fps for long-form.

The Kdenlive XML format is well-documented MLT — no third-party library needed, just template string generation from the EDL.

---

## Source Video Reference

The `source_video` field in Pass 3 output is injected by the orchestrator (the code that calls Pass 3), not by the LLM. The orchestrator knows the video path from the pipeline's input arguments and adds it to each EDL after the LLM returns the edit decisions.

---

## Output & Rendering

Each story's EDL produces three outputs:

### 1. Kdenlive XML Project

Generated from the EDL. One project file per clip version (short, long). Contains:
- Source video on timeline with cuts placed per EDL segments.
- Subtitle track as a separate editable element (not burned in).
- Human opens in Kdenlive to review, tweak timing, adjust cuts.

**Saved to:** `{output_dir}/projects/{story_id}-{short|long}.kdenlive`

### 2. Draft Render (FFmpeg)

Auto-rendered from the same EDL for immediate review:
- Shorts: face-centered 9:16 crop, 1080x1920, karaoke subtitles burned in.
- Long-form: 16:9, original resolution, standard subtitles burned in.
- Translations: applied to draft renders only (not Kdenlive projects).

**Saved to:** `{output_dir}/drafts/{story_id}-{short|long}-{lang}.mp4`

### 3. Workflow

```
AI generates EDL
    ├── Auto-render draft (for quick review)
    └── Generate Kdenlive project (for manual tweaks)

Human reviews draft:
    ├── 80% case: draft is good → approve, publish
    └── 20% case: open Kdenlive project → tweak → re-render
```

Goal: 80% of clips need no manual intervention over time.

---

## File Structure

```
{output_dir}/
├── outline.json              # Pass 1 output
├── stories.json              # Pass 2 output
├── edits/
│   ├── riptide.json          # Pass 3 EDL per story
│   └── ai-evolution.json
├── projects/
│   ├── riptide-short.kdenlive
│   ├── riptide-long.kdenlive
│   └── ...
├── drafts/
│   ├── riptide-short-en.mp4
│   ├── riptide-short-fr.mp4
│   ├── riptide-long-en.mp4
│   └── ...
└── cache/
    ├── video.mp4
    ├── audio.mp3
    └── transcript.json
```

---

## Model Configuration

All passes use OpenAI-compatible API. Model routing is configurable per pass:

```python
PASS_CONFIG = {
    "outline": {"base_url": "http://localhost:11434/v1", "model": "qwen3:8b"},
    "stories": {"base_url": "http://localhost:11434/v1", "model": "qwen3:30b"},
    "editorial": {"base_url": "http://localhost:11434/v1", "model": "qwen3:30b"},
    "translation_romance": {"base_url": "http://localhost:13668/v1", "model": "botty-claude"},
    "translation_chinese": {"base_url": "http://localhost:11434/v1", "model": "qwen3:30b"},
}
```

Any pass can be pointed at LiteLLM, OpenAI, Anthropic, or any OpenAI-compatible endpoint by changing the config.

`PASS_CONFIG` is defined in a new `pipeline/editorial_config.py` file, separate from the existing `pipeline/config.py` (which handles env vars for Notion, R2, etc.). Editorial config values can be overridden via environment variables (e.g., `EDITORIAL_OUTLINE_MODEL=qwen3:8b`) but defaults are hardcoded. Translation config stays in `smoke_test.py` / future orchestrator — it is not part of the editorial pipeline.

---

## Constraints

- Short clips: <55 seconds (4s reserved for end card).
- Long-form clips: 90-600 seconds.
- Minimum segment length: 7 seconds (no choppy cuts).
- Target ratio: ~3x more shorts than long-forms.
- All intermediate JSON preserved on disk for inspection and re-run.
- Kdenlive XML always generated alongside draft renders.

---

## Integration with Existing Pipeline

This replaces `pipeline/segment.py` and `pipeline/hooks.py`. The downstream pipeline (face crop, karaoke ASS, translation, subtitle burn-in) remains unchanged — it just receives clip specs from the EDL instead of from topic segmentation.

The existing `pipeline/transcribe.py` (now using Parakeet-MLX) feeds into Pass 1.

```
transcribe.py (Parakeet) → editorial.py (Passes 1-3) → editor.py (crop, karaoke, render)
```
