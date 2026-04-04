# Podruff — Complete Implementation Plan

**Based on:** Full PRD v1.0 (Jan 30, 2026)
**Current state:** Phase 0 MVP complete (hooks only, local, terminal + basic web UI)
**Goal:** Track all work from Phase 0 cleanup through Phase 4 commercial launch.

---

## What's Built (Phase 0)

Working pipeline: BlackHole audio capture → Whisper transcription → Qwen3:30b hook generation → FastAPI web UI → Markdown export.

**Files:** `audio_capture.py`, `transcriber.py`, `hook_generator.py`, `web_server.py`, `main.py`, `config.py`, `templates/index.html`

**Limitations of current build:**
- Hooks only — no topics, no follow-up questions
- No USED/GOOD/BAD tagging
- No semantic deduplication (prompt-based only)
- No filter toggles
- No post-call summary
- No pre-call research
- No calendar integration
- No speaker diarization
- Single audio device (BlackHole hardcoded)
- No authentication, no CORS
- UI polls every 2s (not streaming)

---

## Phase 1: Personal Tool (1-2 weeks)

Goal: daily-driver quality for Trevor. Three suggestion types, tagging, dedup, post-call summary.

### 1.1 Add Topic Flagging

**What:** New suggestion type — flags interesting threads worth exploring.

**Changes:**
- [ ] Create `topic_flagger.py` — new module, same pattern as `hook_generator.py`
- [ ] Prompt template from PRD: system prompt for identifying discussion threads, user prompt with transcript + existing topics
- [ ] Output: topic text + timestamp, stored separately from hooks
- [ ] Config: `TOPIC_GENERATION_INTERVAL_SECONDS = 10`, `MAX_TOPICS_IN_CONTEXT = 10`
- [ ] Wire into `main.py` pipeline — runs on same cadence as hook generation (or staggered 5s offset to spread load)
- [ ] Add `/api/topics` endpoint to `web_server.py`
- [ ] UI: green-coded cards in the timeline

### 1.2 Add Follow-up Question Generation

**What:** Suggested questions to ask the guest, based on transcript + guest context.

**Changes:**
- [ ] Create `followup_generator.py` — same pattern
- [ ] Prompt template from PRD: system prompt for insightful follow-ups, user prompt with guest info + transcript + covered topics
- [ ] Output: question text + timestamp
- [ ] Config: `FOLLOWUP_GENERATION_INTERVAL_SECONDS = 10`, `MAX_FOLLOWUPS_IN_CONTEXT = 10`
- [ ] Wire into `main.py` — stagger timing so hooks, topics, and follow-ups don't all hit Ollama simultaneously. Suggested cadence: hooks at t+0s, topics at t+3s, follow-ups at t+6s within each 10s window.
- [ ] Add `/api/followups` endpoint
- [ ] UI: orange-coded cards in the timeline

### 1.3 Unified Suggestion Engine

**What:** Refactor the three generators into one suggestion engine with a shared interface.

**Changes:**
- [ ] Create `suggestion_engine.py` — abstract base with `generate(transcript, previous, context) → list[Suggestion]`
- [ ] `Suggestion` dataclass: `id`, `type` (hook/topic/followup), `text`, `timestamp`, `status` (new/used/good/bad)
- [ ] Move all three generators behind this interface
- [ ] Single `/api/suggestions` endpoint returns all types, filterable by `?type=hook,topic`
- [ ] Keep individual endpoints as aliases for backwards compat

### 1.4 USED / GOOD / BAD Tagging

**What:** Let Trevor mark suggestions during the call.

**Changes:**
- [ ] Add `status` field to suggestion dataclass (default: `new`)
- [ ] `POST /api/suggestions/{id}/tag` — body: `{"status": "used"|"good"|"bad"}`
- [ ] When marked BAD: trigger replacement generation (one new suggestion of same type)
- [ ] UI: click/tap to mark USED (highlight), swipe or button for GOOD/BAD
- [ ] Store tag state in memory, persist to Markdown on save
- [ ] Update prompt context to include tags so LLM knows what was accepted/rejected

### 1.5 Semantic Deduplication

**What:** Prevent "basically the same hook" every cycle. PRD says >80% similarity = suppress.

**Changes:**
- [ ] Option A (simple, no extra dependency): Use Ollama embeddings endpoint (`/api/embeddings`) with the same Qwen model to get vectors, cosine similarity check
- [ ] Option B (lighter): Use a small embedding model via Ollama (e.g., `nomic-embed-text`) for faster similarity checks
- [ ] Threshold: 0.80 cosine similarity → suppress
- [ ] Apply to all three suggestion types independently
- [ ] Add to config: `DEDUP_SIMILARITY_THRESHOLD = 0.80`, `DEDUP_MODEL = "nomic-embed-text"`

### 1.6 Filter Toggles

**What:** Show only hooks, only topics, only questions, or all.

**Changes:**
- [ ] UI: toggle buttons at top of timeline — `[Hooks] [Topics] [Q's] [All]`
- [ ] Client-side filtering (no API change needed)
- [ ] Persist filter state in localStorage
- [ ] Color coding: hooks = green (`#00665E`), topics = yellow (`#D4960A`), follow-ups = dark/charcoal

### 1.7 Post-Call Summary

**What:** After "Stop & Save," run a full analysis pass.

**Changes:**
- [ ] Create `post_call.py` — takes full transcript + all suggestions + tags
- [ ] LLM call: summarize main topics, key quotes, suggested clips
- [ ] Reconciliation: compare suggestions against transcript (fuzzy match). Auto-mark suggestions that appear in transcript as "SAID" even if user forgot.
- [ ] Generate "For next time" section: unused good ideas, unexplored topics
- [ ] Update Markdown output format to match PRD Appendix C:
  - `## Hooks Generated` → split into Used, Good (Unused), Rejected
  - `## Topics Discussed` / `## Topics Not Discussed`
  - `## Follow-ups for Next Time`
- [ ] Show summary in UI before final save (allow edits?)

### 1.8 UI Overhaul

**What:** Rebuild `templates/index.html` to match PRD wireframe — sidebar layout, color-coded cards, tagging interactions.

**Changes:**
- [ ] Sidebar-width layout (max ~380px, dockable alongside recording app)
- [ ] Header: call duration, guest name (if known), connection status, active filters
- [ ] Suggestion cards: color-coded border/badge, timestamp, text, [Used] [Good] [Bad] buttons
- [ ] Newest at top, older suggestions fade slightly
- [ ] Replace 2s polling with SSE (Server-Sent Events) for instant updates
- [ ] Add SSE endpoint: `GET /api/stream` — pushes new suggestions as they're generated

### 1.9 Tests for Phase 1

- [ ] Unit tests for topic_flagger.py (same pattern as test_hook_generator.py)
- [ ] Unit tests for followup_generator.py
- [ ] Unit tests for semantic dedup (mock embeddings, test similarity threshold)
- [ ] Unit tests for tagging (status transitions, replacement generation)
- [ ] Integration test: full pipeline with all three suggestion types
- [ ] UI smoke test: page loads, filters work, tagging works

---

## Phase 2: Pre-Call Intelligence (2-4 weeks)

Goal: automate show prep. Calendar integration, guest research, cross-call context.

### 2.1 Google Calendar Integration

- [ ] Create `calendar_integration.py`
- [ ] OAuth2 setup for Google Calendar API
- [ ] Detect content calls by configurable keywords in title (e.g., "podcast", "recording", "interview")
- [ ] Pull guest name, company, and any notes from calendar event
- [ ] Config: `CALENDAR_KEYWORDS = ["podcast", "recording", "interview", "content call"]`
- [ ] Cron-style check: poll calendar every 5 minutes, or on-demand before a call

### 2.2 Guest Research Automation

- [ ] Create `research.py`
- [ ] Input: guest name + company
- [ ] Web search (via SerpAPI, Tavily, or Brave Search API) for recent news, LinkedIn, company info
- [ ] Compile into structured brief: name, title, company, recent news, notable quotes, social links
- [ ] Store in guest file (Obsidian vault: `/Guests/{guest-name}.md`)
- [ ] Surface in UI header during call

### 2.3 Guest Files & History

- [ ] Create `/Guests/` directory structure in Obsidian vault
- [ ] Guest file format: name, company, all past calls, cumulative topics discussed, unused hooks
- [ ] On call start: if guest has been on before, load previous call context
- [ ] Surface in UI: "Last time you didn't get to discuss: X, Y, Z"
- [ ] Unused hooks from previous calls shown in a "From last time" section

### 2.4 Pre-Call Brief Generation

- [ ] Before call starts, generate a 1-page brief combining:
  - Guest research (2.2)
  - Previous call context (2.3)
  - Suggested opening questions
  - Topics to revisit from last time
- [ ] Display in UI before recording starts
- [ ] Save to `/Research/{guest-name}-prep.md`

### 2.5 "Last Time" Context Surfacing

- [ ] When a returning guest is detected, automatically load their guest file
- [ ] Show unused hooks from previous calls as "resurface" suggestions
- [ ] Show unexplored topics from previous calls
- [ ] Include in follow-up question generation prompt context

---

## Phase 3: Hosted Beta (4-8 weeks)

Goal: 10-20 beta users running on colocated Mac Minis at AltaNora.

### 3.1 Infrastructure Setup

- [ ] Provision 3x Mac Mini M4 (64GB) at AltaNora (Montreal)
- [ ] Each Mini runs: Ollama + Qwen3:30b, Whisper.cpp, Python app
- [ ] Load balancer / routing layer to assign users to Minis
- [ ] Monitoring: uptime, resource usage, Ollama health per Mini
- [ ] Remote management: SSH, VNC, or similar for maintenance

### 3.2 Supabase Backend

- [ ] Set up Supabase project
- [ ] Schema: users, calls, suggestions, guests, research
- [ ] Migrate from local Markdown storage to Supabase (keep Markdown export as option)
- [ ] Row-level security: users can only see their own data
- [ ] Real-time subscriptions for live suggestion updates

### 3.3 User Authentication

- [ ] Signup/login flow (email + password, or OAuth via Google)
- [ ] JWT-based auth for API endpoints
- [ ] Session management
- [ ] Rate limiting per user
- [ ] CORS configuration for cross-origin access

### 3.4 Twilio Integration for Dial-In

- [ ] Twilio phone number provisioning (one per user or shared pool)
- [ ] User provides meeting phone number + PIN
- [ ] System dials in via Twilio, captures audio stream
- [ ] Alternative: headless browser join for Zoom/Meet/Teams (Puppeteer/Playwright)
- [ ] Audio routing: Twilio stream → Whisper pipeline

### 3.5 Browser-Based Sidebar

- [ ] Rebuild UI as standalone web app (not just localhost)
- [ ] Works on any device with a browser
- [ ] Responsive sidebar layout (dockable, or full-screen on phone)
- [ ] WebSocket or SSE for real-time suggestion streaming
- [ ] Offline-capable: cache recent suggestions in localStorage

### 3.6 Post-Call Email Summary

- [ ] After call ends, generate summary (reuse post_call.py logic)
- [ ] Send via email (SendGrid, Resend, or similar)
- [ ] Contents: main topics, used hooks, unused hooks worth revisiting, full transcript link
- [ ] User preference: opt-in/out, email frequency

### 3.7 Usage Dashboard

- [ ] Basic analytics: calls per week, hooks generated, hooks used, topics covered
- [ ] Trend lines over time
- [ ] "Your most productive call" / "Most hooks used" highlights

### 3.8 Beta Pricing

- [ ] Free tier or $19/month to validate willingness to pay
- [ ] Stripe integration for payment
- [ ] Usage tracking: calls per month, enforce limits

---

## Phase 4: Commercial Launch

Goal: real product, real pricing, acquisition engine.

### 4.1 Pricing Tiers

| Tier | Price | Calls/Month | Features |
|------|-------|-------------|----------|
| Starter | $19/mo | 10 | Live hooks, basic research, email summaries |
| Pro | $49/mo | 30 | + Calendar sync, guest history, full archive |
| Studio | $99/mo | Unlimited | + Team access (10 seats), custom prompts, API |
| Enterprise | Custom | Unlimited | + Dedicated infra, SSO, custom integrations |

### 4.2 Onboarding Flow

- [ ] Connect calendar (Google Calendar OAuth)
- [ ] Set up dial-in (Twilio number or browser extension)
- [ ] Choose content call keywords
- [ ] Test call (short demo recording to verify pipeline works)
- [ ] Welcome email with tips

### 4.3 Searchable Archive

- [ ] Full-text search across all suggestions, all calls
- [ ] Filter by: date range, guest, type, status (used/unused)
- [ ] "Show me all unused hooks about AI productivity"
- [ ] Powered by Supabase full-text search or pgvector for semantic search

### 4.4 Content Calendar Suggestions

- [ ] Aggregate unused hooks across calls
- [ ] "You've got 47 unused hooks about AI productivity"
- [ ] Suggest content pieces based on hook clusters
- [ ] Clip/short recommendations with timestamps

### 4.5 Team Features (Studio Tier)

- [ ] Team workspace: shared guest files, shared hook archive
- [ ] Up to 10 seats per team
- [ ] Permissions: admin, editor, viewer
- [ ] Custom prompt templates per team

### 4.6 API Access (Studio Tier)

- [ ] REST API for programmatic access to suggestions, calls, guests
- [ ] Webhook support: fire events on new suggestion, call end, etc.
- [ ] Zapier/Make integration for CRM sync

### 4.7 Usage Analytics

- [ ] Hooks used vs. generated over time
- [ ] Topics covered vs. not covered
- [ ] Guest frequency and engagement
- [ ] "Your best hooks" leaderboard

### 4.8 Scale Infrastructure

- [ ] Add Mac Minis as needed (up to 10 at AltaNora)
- [ ] Capacity: ~50-100 users on 3 units based on concurrency
- [ ] Auto-routing: assign users to least-loaded Mini
- [ ] Health monitoring and auto-restart

---

## Future Possibilities (Post-Launch)

Not planned, but noted for reference:

- Mobile app (phone as second screen)
- Browser extension for Zoom/Meet (no dial-in needed)
- AI-generated show notes / blog posts from calls
- Guest booking suggestions based on topic gaps
- Podcast network features (share hooks across shows)
- Speaker diarization (who said what)
- Multiple LLM support (specialized models per suggestion type)
- Hotkey mode: manual "grab last 60s and give me hooks NOW"

---

## Unit Economics Reference

**Per-user COGS at 50 users on 3 Mac Minis:**

| Component | Monthly |
|-----------|---------|
| Hardware amortization (12mo) | $10 |
| Colo/electricity | $1.20 |
| Twilio (20 calls x $0.75) | $15 |
| Cloud STT (Deepgram, 15 hrs) | $4 |
| Supabase (shared) | $0.50 |
| **Total** | **~$31** |

**Break-even:** ~40 users at Pro tier, or 20 at Studio.

---

## Open Questions

1. **Audio routing for hosted version:** Twilio stream direct to Whisper, or record-then-process?
2. **Speaker diarization:** Worth adding in Phase 1 or defer? Adds complexity but improves follow-up question quality.
3. **Hotkey mode:** Manual "give me hooks for the last 60 seconds" — Phase 1 nice-to-have?
4. **Client calls vs. podcast:** Different prompt templates? Different storage paths?
5. **Multiple LLMs:** One model for hooks, one for questions, one for topics? Or keep single model with different prompts?
6. **Concurrency model for hosted:** One Ollama instance per user, or queue requests?

---

*Last updated: February 7, 2026*
