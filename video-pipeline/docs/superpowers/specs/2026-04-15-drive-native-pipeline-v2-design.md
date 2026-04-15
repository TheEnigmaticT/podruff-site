# Drive-Native Video Pipeline v2 — Design Spec

**Date:** 2026-04-15
**Status:** Approved
**Supersedes:** `2026-03-25-drive-native-video-pipeline-design.md`
**Changes from v1:** Updated based on production experience (2026-04-14 manual run for Jonathan Brill)

## Overview

Automate the CrowdTamers video pipeline end-to-end: Zencastr recordings land in Google Drive, a poller picks them up, the editorial pipeline extracts clips, editors tweak in Premiere or Kdenlive via FCP 7 XML, and the pipeline renders branded finals with subtitles and end cards, uploads to R2 for social scheduling, and pushes metadata to Notion.

**Storage split:** Google Drive for working files (source video, XMLs, transcripts, editorial cache). R2 for published clips (public URLs that Zernio downloads from). R2 bucket has a 90-day lifecycle rule to auto-delete old files.

## System Architecture

```
Zencastr (recording)
    │ auto-upload to Google Drive
    ▼
Google Drive: Apps/Zencastr/[session-name]/
    │ OpenClaw heartbeat dispatches video-poll every 15 min
    ▼
Mac Mini Pipeline
    ├─ 1. Download source video locally
    ├─ 2. Transcribe (Parakeet-MLX)
    ├─ 3. LLM transcript repair (confidence tiers)
    ├─ 4. Upload repaired SRT to Drive
    ├─ 5. Create Google Doc from transcript
    ├─ 6. Post repair summary → Slack
    ├─ 7. Editorial 3-pass (outline → stories → EDL, all qwen3:8b)
    ├─ 8. Generate FCP 7 XML per clip
    ├─ 9. Upload source video + XMLs to client Drive folder
    ├─ 10. Post clip summary → Slack
    ├─ 11. Clean up local working files
    │
    │   ── Editor tweaks in Premiere/Kdenlive, exports XML to done/ ──
    │
    ├─ 12. Detect new XMLs in done/ (polling)
    ├─ 13. Render final video from editor's XML + source video
    ├─ 14. Apply client-branded subtitles (from SOUL.md)
    ├─ 15. Append end card (logo + CTA from SOUL.md)
    ├─ 16. Upload final MP4s to R2 (public URLs)
    ├─ 17. Upload final MP4s to Drive final/ folder (archival)
    ├─ 18. Push to Notion with metadata + embedded video
    ├─ 19. Post completion → Slack
    └─ 20. Clean up local working files
```

## Google Drive Folder Structure

```
My Drive/CrowdTamers/Clients/Active Clients/[Client]/
  _assets/
    outro.mp4                          ← 3-5s outro clip (optional)
    logo.png                           ← logo for end card
  Video/
    [Client] Content Call M3 S1/
      source-client.mp4                 ← client track from Zencastr
      source-interviewer.mp4            ← interviewer track
      transcript.srt                   ← repaired transcript
      transcript-doc-link.txt          ← URL to Google Doc version
      clips/
        01-[slug].xml                  ← AI-proposed short cut (FCP 7 XML)
        01-[slug]-long.xml             ← longer version
      editorial/
        outline.json                   ← Pass 1 output
        stories.json                   ← Pass 2 scored moments
      done/
        01-[slug].xml                  ← Editor's tweaked FCP 7 XML
      final/
        01-[slug]-final.mp4            ← branded, subtitled, end card
```

## Component Details

### 1. Automated Ingest

**Trigger:** OpenClaw heartbeat dispatches `video-poll` skill every 15 minutes. The skill runs a single `poll-all` CLI command that handles ingest polling, done-folder polling, and local file cleanup.

**Client detection:** Zencastr sessions follow the naming convention `[ClientName] Content Call M# S#`. Pipeline matches the client name against `client-map.json` which maps aliases to Drive folder IDs. Falls back to fuzzy-match against `Active Clients/` folder list + Slack confirmation on ambiguous matches.

**State tracking:** `.processed.json` tracks already-seen sessions and their processing state (which steps completed). The pipeline resumes from the last successful step on failure.

**Local cleanup:** Source video deleted from Mac Mini after all outputs upload to Drive. On failure, local files retained for retry. Daily cleanup cron removes local files older than 48 hours.

**Sequential processing:** One session at a time (FIFO queue) to avoid filling disk with multiple 7GB downloads.

### 2. LLM Transcript Repair

Runs after Parakeet-MLX transcription, before editorial passes.

**Confidence tiers:**
- **High confidence** — contextually obvious errors. Auto-fix silently.
- **Medium confidence** — likely errors, some ambiguity. Auto-fix, include in Slack summary.
- **Low confidence** — genuinely ambiguous. Flag in Slack with alternatives.

**Google Doc:** Created from repaired transcript. Non-blocking reference copy. Corrections in the Doc feed into future re-renders via `/bshort-refresh`.

**Slack summary:**
```
Transcript cleanup for [Client] — M3 S1
Auto-corrected (8 words): "reverie→revenue", "compliant→complaint", ...
Needs review (2 words):
• Line 47 (02:31): "We launched the [barding/boarding/branding] campaign"
• Line 112 (05:44): "The [thresh/fresh/thrash] approach"
```

### 3. Session Modes: Interview vs Podcast

Configured in client SOUL.md. Can be overridden per-session.

**Interview mode (default):**
- Two tracks: CrowdTamers interviewer + client
- Editorial analyzes combined transcript for context
- EDL timestamps map only to client's track
- Interviewer cues ("that was great", "say more") boost engagement scores
- FCP 7 XML includes both tracks; render uses client track only

**Podcast mode:**
- All speakers are co-equal content
- EDL timestamps cover all tracks
- FCP 7 XML and render include all tracks

### 4. Editorial Pipeline

All three passes use **qwen3:8b** via local Ollama (fast, sufficient quality). Configurable via environment variables per the existing `editorial_config.py`.

**Pass 1 — Outline:** Hierarchical sections from transcript.
**Pass 2 — Story Extraction:** Self-contained moments scored 1-10. Minimum threshold: 7.
**Pass 3 — Editorial Cut:** EDL per story with hook + body segments. Short (<55s) and long (90-600s) versions.

**Graceful failures:** If a story fails EDL validation after 3 retries, it's skipped and the pipeline continues with remaining stories.

**Caching:** Outline, stories, and per-story EDLs are cached on disk. Re-running the same session reuses cached results.

### 5. FCP 7 XML Generation

Replaces the existing `generate_kdenlive_xml()` with `generate_fcp7_xml()`.

**Format:** FCP 7 XML — imports into Premiere, Kdenlive, and DaVinci Resolve.

**Contents per clip XML:**
- Source video tracks as media references (relative path)
- Interview mode: client on V1/A1, interviewer on V2/A2
- Podcast mode: all participant tracks
- Sequence with in/out points

**Parsing editor's XMLs:** Pipeline reads basic editorial decisions from exported FCP 7 XML: in/out points, clip ordering, track assignments. Premiere/Kdenlive-specific effects and transitions are ignored with a logged warning.

### 6. Client Branding (from SOUL.md)

Each client has a SOUL.md (in `~/.openclaw/workspaces/<client>/SOUL.md` and mirrored in Obsidian) that defines branding:

**Subtitle styling:**
- `highlight_color` — karaoke highlight color (e.g., #E38533 for Jonathan Brill)
- `font` — subtitle font (e.g., Inter)
- Style: karaoke word-by-word for shorts, SRT for long-form

**End card (2 seconds, appended to every short):**
- Logo from `_assets/logo.png` in client's Drive folder (or path in SOUL.md)
- CTA text (e.g., "Visit JonathanBrill.com for more")
- White background, logo centered, CTA below

**Outro clip (optional):**
- `_assets/outro.mp4` — crossfaded onto the end if present

The pipeline reads SOUL.md at render time. No separate `style.json` needed.

### 7. Storage: Drive + R2

**Google Drive** holds:
- Source video (permanent archive)
- Transcripts (SRT + Google Doc)
- FCP 7 XMLs (AI-proposed + editor's done/ versions)
- Editorial cache (outline, stories)
- Final rendered MP4s (archival copy)

**Cloudflare R2** holds:
- Final rendered MP4s (public URLs for Zernio social scheduling)
- R2 bucket lifecycle rule: auto-delete objects older than 90 days

The pipeline uploads finals to both Drive (archival) and R2 (distribution).

### 8. Notion Integration

Each final clip gets a page in the client's Content Automation Pipeline database:
- `Title` — clip title from editorial story metadata
- `Clip URL` — R2 public URL (what the social scheduler reads)
- `Source Video` — YouTube or Drive URL of the original recording
- Video embedded in page body via `<video>` tag
- Per-platform text fields populated by social copy generation (separate step, may be manual or automated)
- `Status`: Draft
- `Generation Status`: Drafted
- `Publish Date`: set based on scheduling cadence

### 9. Slack Notifications

All notifications post to the client's internal Slack channel (from SOUL.md).

| Event | Message |
|-------|---------|
| New recording detected | "New recording for [Client] M# S#. Processing started." |
| Transcript repair complete | Repair summary with corrections and flagged words |
| Clips ready for editing | "Found [N] clips. Top moment: '[hook line]' (score: 9/10). Drive: [link]" |
| Final videos ready | "Final versions for [Client] M# S# ready. Notion: [link]" |

### 10. Scheduling (OpenClaw Heartbeat)

Polling triggered by OpenClaw heartbeat via `video-poll` skill, dispatched every 15 minutes.

| Skill | Schedule | Purpose |
|-------|----------|---------|
| `video-poll` | Every 15 min | Ingest + done polling + cleanup |

## Existing Code Changes

| File | Change |
|------|--------|
| `pipeline/edl.py` | Add `generate_fcp7_xml()`, keep `generate_kdenlive_xml()` as fallback |
| `pipeline/edl.py` | Add FCP 7 XML parsing for reading editor's XMLs |
| `pipeline/cli.py` | Add `poll-all` command; accept Drive URLs and local paths |
| `pipeline/cli.py` | Wire end card generation into editorial render loop |
| `pipeline/branding.py` | Read SOUL.md, generate end card, apply subtitle styling |
| `pipeline/editorial_config.py` | Already updated: all passes default to qwen3:8b |
| `pipeline/editorial.py` | Already updated: graceful skip on EDL failure |

## New Files

| File | Purpose |
|------|---------|
| `pipeline/drive.py` | Google Drive API wrapper (list, download, upload, create folder) |
| `pipeline/transcript_repair.py` | LLM transcript repair with confidence tiers |
| `pipeline/fcp7.py` | FCP 7 XML generation and parsing |
| `pipeline/drive_poller.py` | Zencastr ingest polling + done-folder polling + state tracking |
| `tests/test_drive.py` | Drive client tests |
| `tests/test_fcp7.py` | FCP 7 XML generation/parsing tests |
| `tests/test_drive_poller.py` | Poller state machine tests |
| `tests/test_transcript_repair.py` | Repair confidence tier tests |

## Google Drive Authentication

Pipeline authenticates via OAuth credentials stored at `~/.google_workspace_mcp/credentials/tlongino@crowdtamers.com.json` (same credentials the Google Workspace MCP uses). The `drive.py` module wraps Google Drive API v3 using `google-api-python-client`.

## Deprecations

These modules are superseded but kept as dead code until migration is validated:
- `pipeline/segment.py` — replaced by editorial Pass 1+2
- `pipeline/hooks.py` — replaced by editorial Pass 3
- `pipeline/headlines.py` — replaced by editorial story metadata

`pipeline/storage.py` (R2 upload) is NOT deprecated — it remains the distribution layer for public clip URLs.

`generate_kdenlive_xml()` in `pipeline/edl.py` is kept as a fallback but the default path generates FCP 7 XML.

## Out of Scope

- Speaker diarization (pyannote) — rare multi-speaker edge case, solve later if needed
- Bulk creation of Drive folder structures for existing clients
- Audio/video analysis for moment detection
- A/B testing of hooks against engagement metrics
- HeyGen API integration for AI dubbing
- Drive storage archival policy (separate from R2 lifecycle)
