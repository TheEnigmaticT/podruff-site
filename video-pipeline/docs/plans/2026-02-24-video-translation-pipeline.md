# Video Translation Pipeline — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a video translation pipeline that transcribes, edits, translates, and subtitle-burns videos — triggered via Slack through OpenClaw's btranslate skill.

**Architecture:** Local Whisper transcribes with word-level timestamps. LLM (via LiteLLM proxy) detects filler/retakes and performs two-pass translation. FFmpeg handles all video operations (cutting, stitching, subtitle burning). A shell script wraps the pipeline for Slack integration.

**Tech Stack:** Python 3.9, openai-whisper (local), OpenAI SDK (pointed at LiteLLM), FFmpeg 8, bash (skill script)

---

## Task 1: Project Skeleton and Whisper Transcription

**Files:**
- Create: `~/dev/video-pipeline/video_pipeline_v2.py`
- Create: `~/dev/video-pipeline/test_pipeline.py`

**Step 1: Create the project with config and transcription**

Create `video_pipeline_v2.py` with:
- `LANGUAGE_CONFIG` dict covering: `es`, `es-ES`, `zh-CN`, `zh-TW`, `fr`, `de`, `pt-BR`, `ar`, `ja`, `ko`
- Each entry: `name`, `register` (tone description for translator), `font` (for subtitles — CJK needs different fonts)
- `LITELLM_URL` and `LITELLM_KEY` constants (from existing scripts: `http://localhost:13668/v1`, `sk-litellm-...`)
- `LITELLM_MODEL` default: `botty-default` (can override via `--model` flag)
- `transcribe(video_path)` function:
  - Runs local whisper via `whisper` Python module
  - Uses `large-v3` model, `--word_timestamps True`
  - Returns: list of segments with `{start, end, text, words[{word, start, end}]}`
  - Saves raw transcript JSON to `{output_dir}/transcript.json`

**Step 2: Write a basic smoke test**

Create `test_pipeline.py`:
- Test that `LANGUAGE_CONFIG` has all expected language codes
- Test that each config entry has required keys (`name`, `register`, `font`)
- Test `transcribe()` function signature exists (mock whisper)

**Step 3: Run test**

```bash
cd ~/dev/video-pipeline && python3 -m pytest test_pipeline.py -v
```

**Step 4: Commit**

```bash
cd ~/dev/video-pipeline && git init && git add -A && git commit -m "feat: project skeleton with whisper transcription"
```

---

## Task 2: Edit Detection (Filler/Retake Removal)

**Files:**
- Modify: `~/dev/video-pipeline/video_pipeline_v2.py`
- Modify: `~/dev/video-pipeline/test_pipeline.py`

**Step 1: Add LLM-based edit detection**

Add `detect_edits(transcript, video_path)` function:
- Sends transcript text to LLM via OpenAI SDK (pointed at LiteLLM proxy)
- System prompt asks the LLM to identify:
  - Filler words/phrases that should be trimmed
  - False starts and retakes (keep only the best take)
  - Long pauses (>2s) that should be tightened
- Returns: list of `{action: "cut"|"keep", start, end, reason}` segments
- Uses word-level timestamps from Whisper for precise cuts
- In `--no-interactive` mode (default for bot): auto-approves all suggestions
- In interactive mode: prints suggestions and asks for confirmation (stdin)

LLM call config:
```python
client = openai.OpenAI(base_url=LITELLM_URL, api_key=LITELLM_KEY)
response = client.chat.completions.create(
    model=model,
    messages=[system_prompt, {"role": "user", "content": transcript_text}],
    response_format={"type": "json_object"}
)
```

**Step 2: Add FFmpeg cut/stitch**

Add `apply_edits(video_path, edits, output_dir)` function:
- Takes the edit list and creates an FFmpeg concat filter
- Extracts "keep" segments, concatenates them
- Output: `{output_dir}/edited.mp4`
- Uses `-c copy` for speed when cuts align on keyframes, re-encodes otherwise

**Step 3: Write tests**

- Test `detect_edits` returns valid structure (mock the LLM call)
- Test `apply_edits` builds correct FFmpeg command (mock subprocess)

**Step 4: Run tests and commit**

```bash
cd ~/dev/video-pipeline && python3 -m pytest test_pipeline.py -v
git add -A && git commit -m "feat: LLM edit detection and FFmpeg cut/stitch"
```

---

## Task 3: Two-Pass Translation and SRT Generation

**Files:**
- Modify: `~/dev/video-pipeline/video_pipeline_v2.py`
- Modify: `~/dev/video-pipeline/test_pipeline.py`

**Step 1: Add SRT generation from transcript**

Add `generate_srt(segments, output_path)` function:
- Converts Whisper segments to SRT format
- Groups words into subtitle lines (max ~42 chars for readability, ~15 chars for CJK)
- Respects sentence boundaries
- Output: `{output_dir}/subtitles_en.srt`

**Step 2: Add two-pass translation**

Add `translate_srt(srt_path, language_code, output_dir)` function:
- **Pass 1 — Accuracy:** Translates each subtitle line faithfully
  - System prompt: "Translate accurately. Preserve meaning. Use {register} register."
  - Input: full SRT content
  - Output: translated SRT maintaining timestamp structure
- **Pass 2 — Fluency:** Reviews and polishes translation
  - System prompt: "Review this translation for natural flow. Fix awkward phrasing. Maintain {register} register. Keep timestamps unchanged."
  - Input: Pass 1 output
  - Output: final `{output_dir}/subtitles_{lang}.srt`
- Uses `LANGUAGE_CONFIG[code]['register']` for tone guidance

**Step 3: Add parallel translation**

Add `translate_all(srt_path, languages, output_dir)` function:
- Uses `concurrent.futures.ThreadPoolExecutor` to translate all languages in parallel
- Returns dict of `{lang_code: srt_path}`

**Step 4: Write tests and commit**

- Test SRT format output is valid
- Test two-pass translation sends correct prompts (mock LLM)
- Test parallel translation dispatches all languages

```bash
cd ~/dev/video-pipeline && python3 -m pytest test_pipeline.py -v
git add -A && git commit -m "feat: SRT generation and two-pass parallel translation"
```

---

## Task 4: Subtitle Burn-In and CLI

**Files:**
- Modify: `~/dev/video-pipeline/video_pipeline_v2.py`
- Modify: `~/dev/video-pipeline/test_pipeline.py`

**Step 1: Add subtitle burn-in**

Add `burn_subtitles(video_path, srt_path, language_code, output_dir)` function:
- FFmpeg `subtitles` filter with styling:
  - Default font: Arial (Latin), Noto Sans CJK (zh-CN, zh-TW, ja, ko), Noto Sans Arabic (ar)
  - Font size: 24, outline: 2, shadow: 1, bottom margin: 30
  - `LANGUAGE_CONFIG[code]['font']` provides the font family
- Output: `{output_dir}/final_{lang}.mp4`

**Step 2: Add HeyGen stub**

Add `heygen_translate(video_path, language_code, output_dir)` function:
- For now: raises `NotImplementedError("HeyGen API not configured — upgrade plan first")`
- Placeholder for when the HeyGen Scale API key is available
- Accept the language code and pass through to eventual API call

**Step 3: Wire up the CLI with argparse**

```
python3 video_pipeline_v2.py INPUT_VIDEO \
    --languages es zh-CN \
    --heygen es \
    --skip-cuts \
    --no-interactive \
    --model botty-default \
    --output-dir ~/dev/tmp/video-translations/
    --whisper-model large-v3
```

Main flow:
1. Create timestamped output dir: `{output_dir}/{video_stem}_{timestamp}/`
2. Transcribe (Whisper)
3. If not `--skip-cuts`: detect edits + apply cuts → use edited video going forward
4. Generate English SRT
5. For each language: if in `--heygen` list → `heygen_translate()`, else → `translate_srt()` + `burn_subtitles()`
6. Print summary of output files

**Step 4: Write tests and commit**

- Test CLI argument parsing
- Test main flow calls functions in correct order (mock all heavy functions)
- Test output directory naming

```bash
cd ~/dev/video-pipeline && python3 -m pytest test_pipeline.py -v
git add -A && git commit -m "feat: subtitle burn-in and CLI interface"
```

---

## Task 5: btranslate.sh Skill Script

**Files:**
- Create: `~/.openclaw/scripts/btranslate.sh`

**Step 1: Write the skill script**

Following the pattern from `render-ads.sh` and `slack-file-download.sh`:

```bash
#!/bin/bash
# btranslate.sh — Translate and subtitle a video file
# Usage: btranslate.sh "VIDEO_URL" "CHANNEL_ID" "THREAD_TS" [LANGUAGES...]
```

Flow:
1. Parse args: `VIDEO_URL`, `CHANNEL_ID`, `THREAD_TS`, optional `LANGUAGES...`
2. Post progress: "Processing video for translation..."
3. If no languages provided:
   - Find client soul doc by matching channel ID → agent ID in openclaw.json bindings
   - Read the soul doc's `## Video Translation` section
   - Parse `default_languages` and `heygen_languages`
   - If `enabled` is not `true`: post decline message and exit
4. Download video from Slack using `slack-file-download.sh`
5. Run `video_pipeline_v2.py` with `--no-interactive --languages {langs}`
6. Upload each output video to Slack thread using the same upload pattern as `render-ads.sh`
7. Post completion summary

Key details:
- Slack token: same as other scripts (`xoxb-41124765216-9085008978405-Ix79KEGJDtRCAQEp1TFHzqRu`)
- Soul docs path: `~/Documents/Obsidian/CrowdTamers Obsidian Vault/_meta/agents/clients/`
- Agent-to-channel mapping: parse from `~/.openclaw/openclaw.json` bindings
- Output dir: `~/dev/tmp/video-translations/`

**Step 2: Make executable and test manually**

```bash
chmod +x ~/.openclaw/scripts/btranslate.sh
# Dry-run test (will fail at download but validates arg parsing)
~/.openclaw/scripts/btranslate.sh "https://example.com/test.mp4" "C0TEST" "1234567890.123456"
```

**Step 3: Commit**

```bash
cd ~/.openclaw && git add scripts/btranslate.sh && git commit -m "feat: add btranslate video translation skill"
```
(If not a git repo, just note the file is created.)

---

## Task 6: Update crowdtamers.md Soul Doc

**Files:**
- Modify: `~/Documents/Obsidian/CrowdTamers Obsidian Vault/_meta/agents/clients/crowdtamers.md`

**Step 1: Add Video Translation section**

Add before `## Promises`:
```markdown
## Video Translation
- **enabled:** true
- **default_languages:** [es, zh-CN, fr, de, pt-BR]
- **heygen_languages:** []
- **notes:** Internal use — all languages available. Test new languages here first.
```

**Step 2: Verify notis.md and brightway.md already have sections**

Both already confirmed to have `## Video Translation` with correct configs.

---

## Task 7: End-to-End Smoke Test

**Step 1: Test pipeline standalone with a short video**

```bash
# Create a 5-second test video with FFmpeg
ffmpeg -f lavfi -i testsrc=duration=5:size=1280x720:rate=30 \
       -f lavfi -i sine=frequency=440:duration=5 \
       -c:v libx264 -c:a aac -shortest \
       ~/dev/tmp/test-video.mp4

# Run pipeline (skip cuts since test video has no speech)
cd ~/dev/video-pipeline
python3 video_pipeline_v2.py ~/dev/tmp/test-video.mp4 \
    --languages es --skip-cuts --no-interactive \
    --output-dir ~/dev/tmp/video-translations/
```

**Step 2: Verify outputs exist**

- Check for `subtitles_en.srt`
- Check for `subtitles_es.srt`
- Check for `final_es.mp4`

**Step 3: Test btranslate.sh arg parsing**

```bash
~/.openclaw/scripts/btranslate.sh --help
```

---

## Notes

- **LiteLLM proxy** at `http://localhost:13668/v1` — all LLM calls go through this (NOT direct OpenAI)
- **Whisper model:** `large-v3` default, configurable via `--whisper-model`
- **Font fallback:** If CJK fonts aren't installed, FFmpeg will use system default — may need `brew install font-noto-cjk` if Chinese subtitles render as boxes
- **HeyGen:** Stubbed out. When API key is available, implement `heygen_translate()` with their video translation endpoint
- **Idempotent:** Safe to re-run on same video — uses timestamped output dirs
