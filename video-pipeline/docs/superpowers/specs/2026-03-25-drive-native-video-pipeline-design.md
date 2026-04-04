# Drive-Native Video Pipeline — Design Spec

**Date:** 2026-03-25
**Status:** Draft
**Replaces:** Descript-centered workflow + R2 storage

## Overview

Migrate the CrowdTamers video pipeline from a Descript-centered workflow to a fully automated, Google Drive-native pipeline. Zencastr replaces Descript for recording, Google Drive replaces R2 for storage, FCP 7 XML replaces KDenLive XML for editorial interchange, and last-mile automation handles branding, subtitles, and Notion delivery.

**Net cost change:** Drop Descript ($140/mo), add Zencastr ($30/mo) = $110/mo saved.

## System Architecture

```
Zencastr (recording, $30/mo)
    │ auto-upload to Google Drive
    ▼
Google Drive: Apps/Zencastr/[session-name]/
    │ polling cron on Mac Mini (every 15 min)
    ▼
Mac Mini Pipeline
    ├─ 1. Download source video locally
    ├─ 2. Transcribe (Parakeet-MLX)
    ├─ 3. LLM transcript repair
    │     ├─ High confidence: auto-fix silently
    │     ├─ Medium confidence: auto-fix, log in summary
    │     └─ Low confidence: flag for human review
    ├─ 4. Upload repaired SRT to Drive
    ├─ 5. Create Google Doc from transcript (non-blocking reference copy)
    ├─ 6. Post repair summary → Slack via Botty
    ├─ 7. Editorial 3-pass (outline → stories → EDL)
    ├─ 8. Generate FCP 7 XML per clip
    ├─ 9. Upload source video + XMLs to client Drive folder
    ├─ 10. Post clip summary → Slack via Botty
    ├─ 11. Clean up local working files
    │
    │   ── Jude edits in Premiere, exports XML to done/ ──
    │
    ├─ 12. Detect new XMLs in done/ (polling cron)
    ├─ 13. Render final video from Jude's XML + source video
    ├─ 14. Apply subtitles (client styling + optional gradient)
    ├─ 15. Optional logo overlay
    ├─ 16. Crossfade to client outro clip (3-5s)
    ├─ 17. Upload final MP4s to Drive final/ folder
    ├─ 18. Push to Notion with metadata
    ├─ 19. Post completion → Slack via Botty
    └─ 20. Clean up local working files
```

## Google Drive Folder Structure

```
My Drive/CrowdTamers/Clients/Active Clients/[Client]/
  _assets/
    outro.mp4                          ← 3-5s outro clip, reused across sessions
    logo.png                           ← optional logo overlay
  Video/
    [Client] Content Call M3 S1/
      source-client.mp4                 ← client track from Zencastr
      source-interviewer.mp4             ← interviewer/host track (or additional participants)
      transcript.srt                   ← repaired transcript
      transcript-doc-link.txt          ← URL to Google Doc version
      clips/
        01-[slug].xml                  ← AI-proposed short cut (FCP 7 XML)
        01-[slug]-long.xml             ← longer version
        02-[slug].xml
        ...
      editorial/
        outline.json                   ← Pass 1 output
        stories.json                   ← Pass 2 scored moments
      done/
        01-[slug].xml                  ← Jude's edited FCP 7 XML
        ...
      final/
        01-[slug]-final.mp4            ← branded, subtitled, ready for distribution
        ...
```

## Component Details

### 1. Automated Ingest

**Trigger:** launchd cron on Mac Mini, every 15 minutes, polls `Apps/Zencastr/` on Google Drive for new video files.

**Client detection:** Zencastr sessions are named using the convention `[ClientName] Content Call M# S#` (matching calendar naming). Pipeline matches the client name portion against a `client-map.json` config file that maps client name aliases to Drive folder IDs. Falls back to fuzzy-match against `Active Clients/` folder list + Slack confirmation on ambiguous matches.

**State tracking:** `.processed.json` file tracks already-seen sessions to avoid reprocessing. Each session records its processing state (which steps completed) so the pipeline can resume from the last successful step on failure rather than restarting from scratch.

**Local cleanup:** Source video is deleted from Mac Mini after all outputs are uploaded to Drive. Drive holds the permanent copy. On pipeline failure, local files are retained for retry. A daily cleanup cron removes local files older than 48 hours.

**Concurrent processing:** The Mac Mini has limited disk space. The ingest poller processes one session at a time (FIFO queue). If multiple sessions land simultaneously, they are queued and processed sequentially to avoid filling the disk with multiple 7GB downloads.

### 2. LLM Transcript Repair

Runs immediately after Parakeet-MLX transcription, before editorial passes.

**Confidence tiers:**
- **High confidence** — contextually obvious errors (e.g., "reverie" → "revenue" in a business context). Auto-fix silently, log the change.
- **Medium confidence** — likely errors but some ambiguity. Auto-fix, include in Slack summary for review.
- **Low confidence** — genuinely ambiguous (names, jargon, unclear words). Do not fix. Flag in Slack with suggested alternatives.

**Google Doc:** Pipeline creates a Google Doc from the repaired transcript. This is a non-blocking reference copy — pipeline proceeds immediately. Anyone can make corrections in the Doc at any time for future re-renders.

**Slack summary (Botty):**
```
Transcript cleanup for [Client] — M3 S1
Auto-corrected (8 words): "reverie→revenue", "compliant→complaint", ...
Needs review (2 words):
• Line 47 (02:31): "We launched the [barding/boarding/branding] campaign" — which one?
• Line 112 (05:44): "The [thresh/fresh/thrash] approach" — unclear from context
```

### 3. Session Modes: Interview vs Podcast

Each client has a default session mode configured in their soul doc. Can be overridden per-session via a field in `client-map.json` or a Slack command.

**Interview mode (default for most clients):**
- Two tracks: CrowdTamers interviewer + client
- Editorial pipeline analyzes the **combined transcript** (both speakers) for context
- EDL timestamps map **only to the client's track** — interviewer audio/video is excluded from clips
- Interviewer cues are used as engagement signals: "that was great," "say more about that," "can you repeat that?" indicate high-value client moments and retake boundaries
- FCP 7 XML includes **both tracks** so Jude can hear the full conversation while editing, but the render pipeline uses only the client track for final output

**Podcast mode:**
- Two (or more) tracks: all speakers are co-equal content
- Editorial pipeline analyzes the combined transcript
- EDL timestamps cover **all tracks** — clips include all speakers
- FCP 7 XML includes all tracks; render pipeline composites them

### 4. Editorial Pipeline ("Find Interesting Moments")

Upgrades the existing 3-pass editorial system to be the default path for /bshort.

**Pass 1 — Outline:** LLM structures transcript into hierarchical sections. Receives the combined transcript (all speakers, labeled by speaker). No changes from current implementation.

**Pass 2 — Story Extraction:** LLM identifies self-contained moments with engagement scores (1-10). Scoring criteria tuned for:
- Score higher: surprising claims, concrete examples/stories, emotional peaks, humor, counterintuitive insights, quotable one-liners, strong hook potential
- Score lower: generic intros, rambling context-setting, repetitive points, logistics
- Sub-score for hook quality: does the moment have a strong cold-open line?
- Minimum score threshold: 7 (configurable)
- In interview mode: interviewer reactions ("wow," "that's a great insight," laughter) boost the score of the client's preceding statements
- In interview mode: interviewer cues like "let's try that again" or "can you repeat that?" signal that the *next* client segment is the good take and the previous one should be trimmed

**Pass 3 — Editorial Cut:** For each qualifying story, generates EDL with hook, body segments, trims. Produces "short" (<55s) and "long" (90-600s) versions. In interview mode, all EDL timestamps reference the client track only.

### 4. FCP 7 XML Generation

Replaces `generate_kdenlive_xml()` in `edl.py` with `generate_fcp7_xml()`.

**Format:** FCP 7 XML (.xml) — chosen because Premiere natively imports and exports this format (`File > Export > Final Cut Pro XML`). DaVinci Resolve also supports it.

**Contents per clip XML:**
- Source video tracks as media references (relative path to files in parent folder)
- In interview mode: client track on V1/A1, interviewer track on V2/A2 (Jude has both for context; render uses V1/A1 only)
- In podcast mode: all participant tracks included
- Sequence with in/out points referencing the source
- Track layout for video and audio

**Output:** One XML file per clip, named `[##]-[slug].xml` (short version) and `[##]-[slug]-long.xml`.

**Supported subset for parsing Jude's edited XMLs:** The pipeline will parse only basic editorial decisions from Jude's exported FCP 7 XML:
- In/out points on the source video
- Clip ordering on the timeline
- Track assignments (video + audio)

Premiere-specific effects, transitions, color grades, and references to external media are ignored. If the parser encounters unsupported elements, it logs a warning and proceeds with what it can extract. The Slack notification to Botty includes a note if any elements were skipped.

### 5. Collaborative Editing Workflow

**Jude's workflow:**
1. Gets Slack notification from Botty: clips ready, Drive folder link
2. Opens client's Drive folder (synced locally via Google Drive for Desktop)
3. Opens any `.xml` in Premiere — source video auto-links from same parent folder
4. Fine-tunes cuts (adjust in/out points, extend, reorder, grab other sections of source)
5. `File > Export > Final Cut Pro XML` → saves to `done/` subfolder
6. Moves to next client

**AM workflow:** Same as Jude but for lighter tweaks. Can use Premiere or DaVinci Resolve (free).

**Key property:** Nondestructive. One ~7GB source video transfers once. All edit decisions are tiny XML files. Jude has full access to the entire source recording for any adjustments.

### 6. Last-Mile Automation

**Trigger:** Polling cron detects new XML files in any client's `done/` folder. To avoid processing files mid-write, the poller only picks up files whose last-modified time is >60 seconds old.

**Per-client assets (from `_assets/` folder):**
- `outro.mp4` — 3-5s outro clip, crossfaded onto the end
- `logo.png` — optional logo overlay
- `style.json` — subtitle styling config:
  ```json
  {
    "font": "Raleway",
    "primary_color": "#1FC2F9",
    "outline_color": "#000000",
    "font_size": 24,
    "gradient_background": false,
    "logo_position": "bottom-right",
    "logo_opacity": 0.8
  }
  ```
  Falls back to CrowdTamers defaults if not present.

**Render pipeline:**
1. Parse Jude's FCP 7 XML → extract edit decisions
2. Render against source video (FFmpeg)
3. Crop/scale: 1080x1920 vertical (85% of output) or 1920x1080 horizontal (15%), with face detection for vertical crops
4. Burn subtitles with client styling (+ optional gradient backing)
5. Apply logo overlay if configured
6. Crossfade to client outro clip
7. Export final MP4 to `final/` subfolder

**Delivery:**
- Push to Notion with metadata (client, date, title, description from editorial pass, Drive link)
- Post completion to client Slack channel via Botty
- Clean up local working files

### 7. Slack Notifications (Botty)

All notifications post to the relevant client channel from the Botty account.

| Event | Message |
|-------|---------|
| New recording detected | "New recording detected for [Client] M# S#. Processing started." |
| Transcript repair complete | Repair summary with auto-corrections and flagged words |
| Clips ready for editing | "Found [N] clips. Top moment: '[hook line]' (score: 9/10). Drive: [link]" |
| Final videos ready | "Final versions for [Client] M# S# ready. Notion: [link]" |

### 8. Manual Overrides (Slack)

| Command | Action |
|---------|--------|
| `/bshort [drive-url]` | Re-run clip selection on a specific video |
| `/btranslate [drive-url]` | Translate subtitles for a specific video |
| `/bshort-refresh [session]` | Re-pull Google Doc transcript, re-generate subtitles, re-render |

## Infrastructure

### Services

| Service | Purpose | Cost |
|---------|---------|------|
| Zencastr (Grow plan) | Recording with local capture + Google Drive auto-upload | $30/mo |
| Google Drive | Storage for all video assets, XMLs, final outputs | Already paid (4TB) |
| Google Workspace MCP | Drive file operations, Google Doc creation | Already configured |

### Google Drive Authentication

The pipeline Python code authenticates to Google Drive via **Google service account** with domain-wide delegation, scoped to the CrowdTamers workspace. Credentials stored at `~/.config/google/service-account.json` (excluded from git). The `drive.py` module wraps the Google Drive API v3 using the `google-api-python-client` library. This is separate from the Google Workspace MCP tools (which are used by OpenClaw/Botty for Slack-triggered operations).
| Mac Mini | Local processing (transcription, LLM, FFmpeg rendering) | Already owned |
| Slack (Botty) | Notifications and manual overrides | Already configured |
| Notion | Final delivery destination | Already configured |

### Dropped Services

| Service | Was Used For | Savings |
|---------|-------------|---------|
| Descript ($140/mo) | Recording, editing, storage | $140/mo |
| R2 | Clip and thumbnail storage | Minor |

**Net savings: ~$110/mo ($1,320/yr)**

### Scheduling (OpenClaw Heartbeat Dispatch)

Polling is triggered by OpenClaw's heartbeat system via the `video-poll` skill, dispatched every 15 minutes. A single `poll-all` CLI command handles ingest polling, done-folder polling, and local file cleanup in one invocation. This avoids macOS TCC/privacy issues with launchd plists for arbitrary scripts.

| Skill | Schedule | Purpose |
|-------|----------|---------|
| `video-poll` | Every 15 min (heartbeat dispatch) | Ingest + done polling + cleanup |

## Deprecations

The following existing modules are superseded by the editorial pipeline and should be deprecated (kept but not called from the default path):
- `pipeline/segment.py` — topic segmentation (replaced by editorial Pass 1+2)
- `pipeline/hooks.py` — hook selection (replaced by editorial Pass 3)
- `pipeline/headlines.py` — headline generation (replaced by editorial story metadata)
- `pipeline/storage.py` — R2 upload (replaced by `drive.py`)

The existing Notion-based ingest path in `poller.py` (`poll_and_process()`) is replaced by the Drive-based ingest poller. Kept as dead code initially; remove after migration is validated.

## Existing Code Changes

| File | Change |
|------|--------|
| `pipeline/edl.py` | Replace `generate_kdenlive_xml()` with `generate_fcp7_xml()` |
| `pipeline/edl.py` | Add FCP 7 XML parsing for reading Jude's edited XMLs |
| `pipeline/editorial.py` | Tune Pass 2 engagement scoring prompts |
| `pipeline/poller.py` | Add Google Drive upload/download (replace R2) |
| `pipeline/cli.py` | Wire editorial pipeline as default for `short` command; replace `yt-dlp` download path with Google Drive download; accept Drive URLs |
| New: `pipeline/transcript_repair.py` | LLM transcript repair with confidence tiers |
| New: `pipeline/drive.py` | Google Drive operations (upload, download, poll, Google Doc creation) |
| New: `pipeline/branding.py` | Last-mile rendering (subtitles, outro, logo overlay) |
| New: `scripts/ingest-poll.sh` | Cron script for Zencastr ingest polling |
| New: `scripts/done-poll.sh` | Cron script for done folder polling |
| `~/.openclaw/scripts/bshort.sh` | Accept Google Drive URLs, use editorial pipeline (file exists at `~/.openclaw/scripts/`) |
| `~/.openclaw/scripts/btranslate.sh` | Accept Google Drive URLs (file exists at `~/.openclaw/scripts/`) |
| New: `~/.openclaw/scripts/bshort-refresh.sh` | Re-render from updated Google Doc transcript |

## Obsidian Documentation Deliverables

Location: `work/CrowdTamers/Tools/`

| Doc | Audience | Content |
|-----|----------|---------|
| `Video Pipeline Overview.md` | Whole team | System diagram, architecture, how it all fits together |
| `Video Pipeline - Editor Guide.md` | Jude | His specific workflow: Drive folder structure, Premiere import/export, done/ convention |

## Zencastr Output Format

Zencastr outputs separate tracks per participant. The pipeline must handle:
- Multiple video files per session (one per participant) — use the host track as primary, or composite if needed
- Audio tracks (WAV or MP3 per participant)
- The auto-upload folder structure in `Apps/Zencastr/` may vary — the ingest poller should detect video files by extension (`.mp4`, `.webm`, `.mkv`) rather than assuming a specific filename

If Zencastr outputs non-MP4 formats, the pipeline normalizes to H.264 MP4 before proceeding.

## `/bshort-refresh` Behavior

When triggered, `/bshort-refresh [session]`:
1. Re-downloads the Google Doc transcript
2. Re-generates SRT with original Parakeet timing (timestamps come from Parakeet, not the Doc — the Doc only corrects words)
3. Re-renders only the `final/` outputs that have corresponding XMLs in `done/` (Jude's edits)
4. Does NOT re-run the editorial pipeline or regenerate clip proposals
5. Posts updated files to Slack via Botty

## Drive Storage Growth

At ~7GB per session, 16 clients, ~2-4 sessions/month per client: 224-448 GB/month of source video accumulates on Drive. At max flow (60 clients), this could reach ~1.5 TB/month. The 4TB Drive allocation covers roughly 3-9 months at current scale. An archival policy (move sessions older than 6 months to a cold storage bucket or compressed archive) should be implemented separately once storage approaches 75% capacity.

## Out of Scope

- Bulk creation of Drive folder structures for existing clients (one-time migration, separate task)
- Audio/video analysis for moment detection (visual energy, applause detection, etc.)
- A/B testing of hooks against engagement metrics
- HeyGen API integration for AI dubbing
- Zencastr ↔ Google Calendar integration for auto-naming sessions
- Drive storage archival policy (flagged for future implementation)
