# Podruff

Marketing ops monorepo for CrowdTamers. Handles social media scheduling, video pipeline, hook detection, reporting, and the public site.

## Skill Routing — "I want to..."

Find the right tool fast. Each row is a discrete capability in the monorepo.

### Content Creation

| Task | Module | How to run |
|------|--------|-----------|
| Generate **quote cards** (speaker still + pull-quote) from video clips | `social-engine/social_quotecard.py` | `python -m social_quotecard render --client <name> --frame <f.png> --quote "..." --attrib "..." --variant light\|dark --out-dir <dir> --stem <name>` |
| Pick the **best speaker frame** from a video clip (face detection + sharpness scoring) | `social-engine/social_quotecard.py` | `python -m social_quotecard pick-best-frame <video.mp4> --out <dir> --count 5` |
| Generate **risograph / illustrated graphics** from transcript (Gemini / Nano Banana) | `social-engine/social_bgraphic.py` | `python3 social_bgraphic.py <transcript_or_url> <client_name> <slack_channel> <thread_ts> [num_images]` |
| Generate a **single AI image** from a prompt (Gemini Flash) | `social-engine/social_image_gen.py` | `python3 social_image_gen.py "prompt" output.png` |
| Write **per-platform social copy** (Claude) for a Notion row | `social-engine/social_generate_cli.py` | `python3 social_generate_cli.py <page_id> [--render] [--status Draft]` |
| **Provision all posts** from a completed video session (clips → Notion rows + copy + graphics) | `social-engine/social_provisioner.py` | `python3 -m social_provisioner <session_folder> --client <name> [--quote-cards 7]` |

### Publishing & Scheduling

| Task | Module | How to run |
|------|--------|-----------|
| **Poll Notion → push to Zernio / PostBridge** (the scheduler daemon) | `social-scheduler/social_poller.py` | LaunchAgent `com.openclaw.social-poller` (auto on login). Manual: `python3 social_poller.py` |
| **Onboard a new client** on Zernio (create profile, generate OAuth URLs) | `social-engine/social_onboard_client.py` | `python3 social_onboard_client.py "Client Name" -p linkedin,youtube [--dry-run]` |

### Video Processing

| Task | Module | How to run |
|------|--------|-----------|
| **Ingest + transcribe + editorial extraction** from a video URL or file | `video-pipeline/pipeline/cli.py` | `python3 -m pipeline.cli editorial <url_or_path>` |
| **Render** segments with subtitles + client branding | `video-pipeline/pipeline/cli.py` | `python3 -m pipeline.cli render <session_folder>` |
| **Upload** files to R2 | `video-pipeline/pipeline/storage.py` | `from pipeline.storage import upload_file; upload_file(local_path, remote_key)` |
| **Transcribe** any audio/video with Parakeet-MLX | `video-pipeline/pipeline/transcribe.py` | `from pipeline.transcribe import transcribe_video; segments = transcribe_video(path)` |
| **Poll Drive** for new video sessions to process | `video-pipeline/pipeline/cli.py` | `python3 -m pipeline.cli poll-all`. LaunchAgent `com.crowdtamers.video-poll` (auto on login) |

### Other

| Task | Module | How to run |
|------|--------|-----------|
| **Live call hook detection** (real-time transcription + suggestions) | `hook-detector/main.py` | `python3 main.py` (starts FastAPI + audio capture) |
| **Client reporting** (weekly analytics aggregation) | `reporting/run.py` | `python3 run.py <client_config> <iso_week>` |
| **HeyGen avatar video** generation | `heygen-pipeline/` | Scaffolding; not a standalone CLI yet |

---

## Per-Client Configuration

### social_config.json

Lives at `social-engine/social_config.json` (social-scheduler symlinks to it). Per-client block structure:

```json
"<client-name>": {
    "notion_database_id": "...",
    "platforms": {
        "linkedin": { "provider": "late", "account_id": "..." },
        "instagram": { "provider": "late", "account_id": "..." }
    },
    "brand": {
        "fonts": { "serif_italic": "Fraunces-Italic.ttf", "sans": "SpaceGrotesk.ttf" },
        "light":  { "bg": "#...", "quote": "#...", "rule": "#...", "attrib": "#...", "logo": "~/path/logo-dark.png" },
        "dark":   { "bg": "#...", "quote": "#...", "rule": "#...", "attrib": "#...", "logo": "~/path/logo-light.png" },
        "wash":   { "color": "#...", "alpha": 0.12 },
        "attribution_default": "Name, Title"
    },
    "quote_card_count": 7
}
```

The `brand` block is used by `social_quotecard.py`. Fonts auto-download from Google Fonts if not at the given path. Known presets: Fraunces, Fraunces-Italic, SpaceGrotesk, Inter, Inter-Italic.

### SOUL.md

Each client has a SOUL.md at `~/Documents/Obsidian/CrowdTamers Obsidian Vault/work/CrowdTamers/Clients/<Client>/SOUL.md` with voice guidelines, brand colors, logo refs, subtitle styling, platform rules, and copywriting rules. **Always read it before generating copy or rendering branded content.**

### Logo files

Stage logo PNGs in the client's vault folder at `brand/logo-dark.png` (for use on light bg) and `brand/logo-light.png` (for dark bg). The `social_quotecard` module reads from the path in the `brand` block.

---

## Notion Database Fields — What's Live

The scheduler/poller reads these fields; other fields are human-only or dead:

| Field | Read by | Notes |
|---|---|---|
| `Status` | Poller | Triggers on `Approved`. Values vary per client DB (some use `Draft`, others `Review Needed`). |
| `Platforms` | Poller | Multi-select → which Zernio/PostBridge accounts to post to |
| `Publish Date` | Poller | `scheduledFor` sent to Zernio |
| `LinkedIn Text`, `X Text`, etc. | Poller | `resolve_text()` picks platform-specific field, falls back to `Post Text` |
| `Clip URL` → `Thumbnail URL` | Poller | Media URL for Zernio presign+upload. Clip URL preferred; Thumbnail is fallback |
| `External Post IDs` | Poller writes | JSON dict of `{platform: zernio_post_id}` |
| `Posting Errors` | Poller writes | Error text if scheduling fails |

**Dead fields** (not read by any operational code): `Headline`, `Description`, `Hook Sentence`. Don't populate them in automation scripts — they're for human browsing only.

---

## Video-to-Social Pipeline (End-to-End)

The full workflow from raw video to scheduled social posts:

1. **Ingest**: Download video via yt-dlp (URL) or accept a local file path. The `pipeline editorial` CLI command handles both.
2. **Transcribe**: Parakeet-MLX produces word-level timestamps. No speaker diarization (flat word stream).
3. **Editorial (3-pass LLM)**: All passes use `qwen3:8b` via local Ollama.
   - Pass 1: Generate hierarchical outline from transcript
   - Pass 2: Extract self-contained stories with engagement scores (threshold: 7+)
   - Pass 3: Generate EDL (edit decision list) per story with short (<55s) and long versions
   - Failed EDLs are skipped gracefully; cached results reused on rerun
4. **Render**: FFmpeg extracts segments, crops to 9:16 (face-centered), burns karaoke subtitles. Also generates Kdenlive XML projects for manual editing.
5. **Manual edit** (optional): Open Kdenlive project, tweak cuts. V1/A1 tracks must be moved together (group or chain-link them).
6. **Re-render with client branding**: Update the `.ass` subtitle file with client colors/font, generate an end slide (logo + CTA), re-render with FFmpeg, concat end slide.
7. **Upload**: Push rendered clips to R2 (`pipeline/storage.py`).
8. **Provision social posts**: Run `social_provisioner.py` on the session folder. Creates Video Clip rows + Text Insight rows in Notion. Top-N insights (default 7) get **quote cards** (speaker still + pull-quote); rest get **risograph graphics**. All get Claude-generated per-platform copy.
9. **Schedule**: Social scheduler polls Notion for approved posts, pushes to Zernio (video platforms) and PostBridge (X/Twitter). Zernio handles presign-and-upload from the R2 URL.

### Copywriting Rules

Always follow `~/Documents/Obsidian/CrowdTamers Obsidian Vault/_meta/claude-outputs/AI Tasks & Prompts/Quit Writing Like AI.md` for all social copy. Key rules: no hashtags, no emoji, no banned AI words, short declarative sentences, write in the client's voice (first person on LinkedIn).

### Zernio (Social Scheduling API)

- API client: `social-scheduler/social_apis.py` (ZernioClient)
- Each client has a Zernio profile with connected social accounts
- Profile IDs and account IDs are per-client; check SOUL.md or `social_config.json`
- OAuth connect URLs generated via `ZernioClient.get_connect_urls(profile_id, platforms)`
- PostBridge used separately for X/Twitter
- **Instagram aspect ratio**: images must have aspect ≤ 1.91. Landscape cards are 1200×630 (1.905) — not 1200×628 (1.911, rejected).

---

## LaunchAgents (auto-running daemons)

| Plist | What it does | Restart |
|---|---|---|
| `com.openclaw.social-poller` | Polls Notion → pushes approved posts to Zernio/PostBridge | `launchctl kickstart -k gui/501/com.openclaw.social-poller` |
| `com.crowdtamers.video-poll` | Polls Drive for new video sessions | `launchctl kickstart -k gui/501/com.crowdtamers.video-poll` |
| `com.openclaw.call-crawler` | Triggers hook detection on new recordings | `launchctl kickstart -k gui/501/com.openclaw.call-crawler` |

Config changes to `social_config.json` require a poller restart (it loads config once at startup).

---

## Vault Skill Doc

The full runbook for the quote-card skill (tuning knobs, gotchas, per-client onboarding, batch patterns) lives in the Obsidian vault: `_meta/skills/marketing/quote-card-from-video.md`. The marketing skill router at `_meta/skills/marketing/_router.md` points to it.

---

## Structure

- `site/` — Public marketing site (static HTML landing page)
- `video-pipeline/` — Drive-native video processing, short clip extraction, subtitle generation, engagement scoring
- `social-engine/` — Social content generation, copywriting, image rendering, Notion integration, client onboarding
- `social-scheduler/` — Polls approved posts from Notion, schedules to Zernio/PostBridge
- `hook-detector/` — Live call hook assistant, real-time transcription, hook/topic/followup suggestions
- `heygen-pipeline/` — HeyGen avatar video generation (scaffolding)
- `orchestrator/` — Pipeline coordination, workflow routing, agent dispatcher
- `reporting/` — Client reporting with analytics aggregation

## Backlog

Uses a unified BACKLOG.md with `#podruff/<subproject>` tags to route items to sub-projects (e.g., `#podruff/video`, `#podruff/site`, `#podruff/hooks`, `#podruff/social-engine`, `#podruff/social-scheduler`, `#podruff/reporting`, `#podruff/orchestrator`, `#podruff/heygen`).

## Backlog Convention

When adding items to BACKLOG.md, always include:

- Clear description of what's wrong or what's needed
- The relevant `#podruff/<subproject>` tag (one of: site, video, social-engine, social-scheduler, hooks, heygen, orchestrator, reporting)
- `[done-when::...]` with specific, verifiable acceptance criteria
- `[priority::low|medium|high]` if not medium (default is medium)
- `[needs-ui::true]` if it involves visible UI changes
- `[due::YYYY-MM-DD]` if there's a deadline
- `[added::YYYY-MM-DD]` to track when the item was created

Example:
```
- [ ] Social poller skips posts with missing image URLs #podruff/social-scheduler [added::2026-04-06] [done-when::Poller handles missing images gracefully, logs warning, still schedules text-only post] [priority::medium]
```
