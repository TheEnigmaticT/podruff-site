# Podruff

Marketing ops monorepo for CrowdTamers. Handles social media scheduling, video pipeline, hook detection, reporting, and the public site.

## Structure

- `site/` — Public marketing site (static HTML landing page)
- `video-pipeline/` — Drive-native video processing, short clip extraction, subtitle generation, engagement scoring (replacing Descript)
- `social-engine/` — Social content generation, copywriting, image rendering, Notion integration for client onboarding
- `social-scheduler/` — Polls approved posts from Notion, schedules to Later/PostBridge platforms
- `hook-detector/` — Live call hook assistant, real-time transcription, hook/topic/followup suggestion generation during recordings
- `heygen-pipeline/` — HeyGen avatar video generation, node-based workflow with templates
- `orchestrator/` — Pipeline coordination, workflow routing, agent dispatcher for multi-step tasks
- `reporting/` — Client reporting with analytics aggregation (Google Ads, Meta Ads, LinkedIn integration stubs)

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
8. **Draft social copy**: Create Notion pages in the client's Content Automation Pipeline database. Put video URL in `Clip URL` field (this is what the scheduler reads). Embed video in page body with `<video>` tag. Write per-platform copy following Quit Writing Like AI guidelines and the client's SOUL.md.
9. **Schedule**: Social scheduler polls Notion for approved posts, pushes to Zernio (video platforms) and PostBridge (X/Twitter). Zernio handles presign-and-upload from the R2 URL.

### Client Branding

Each client has a SOUL.md in `~/Documents/Obsidian/CrowdTamers Obsidian Vault/work/CrowdTamers/Clients/<Client>/SOUL.md` with brand colors, logo, subtitle styling, voice guidelines, and social copy rules. Always read it before generating copy or rendering branded clips.

### Copywriting Rules

Always follow `~/Documents/Obsidian/CrowdTamers Obsidian Vault/_meta/claude-outputs/AI Tasks & Prompts/Quit Writing Like AI.md` for all social copy. Key rules: no hashtags, no emoji, no banned AI words, short declarative sentences, write in the client's voice (first person on LinkedIn).

### Notion Database Fields

- `Clip URL`: R2 URL of the rendered video (scheduler reads this for Zernio)
- `Short URL`: Public link to the published short (populated after posting)
- `Thumbnail URL`: Fallback if no Clip URL
- Per-platform text fields: `LinkedIn Text`, `YouTube Text`, `Instagram Text`, `TikTok Text`, `Facebook Text`, `Threads Text`, `X Text`, `Bluesky Text`, `Reddit Text`
- `Publish Date`: When to schedule (datetime)
- `Status`: Draft → To Review → Approved → Scheduled → Published
- `Generation Status`: Drafted → Sent to Client → Approved → Published

### Zernio (Social Scheduling API)

- API client: `social-scheduler/social_apis.py` (ZernioClient)
- Each client has a Zernio profile with connected social accounts
- Profile IDs and account IDs are per-client; check SOUL.md
- OAuth connect URLs generated via `ZernioClient.get_connect_urls(profile_id, platforms)`
- PostBridge used separately for X/Twitter

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

Another example with more metadata:
```
- [ ] Add aspect ratio variants to clip export #podruff/video [added::2026-04-06] [priority::high] [due::2026-04-20] [done-when::Video clips export in 1:1, 9:16, and 16:9 aspect ratios with smart face-centered crop; tests pass; clients can select variant in Notion form]
```
