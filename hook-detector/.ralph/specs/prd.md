# Live Call Hook Assistant — Phase 0 MVP Specification

**Version:** 2.0
**Date:** January 30, 2026
**Author:** Trevor Longino / CrowdTamers
**Status:** Ready for Development

---

## What We're Building Tonight

A local Python application that:
1. Captures audio from a live call via BlackHole virtual audio device
2. Transcribes speech in real-time using Whisper
3. Generates hook ideas every 10 seconds using Qwen3:30b
4. Displays results in a minimal web UI
5. Saves everything to a Markdown file

**Runtime environment:** Trevor's Mac Mini M4 (64GB RAM), macOS, Qwen3:30b already running via Ollama.

---

## Technology Decisions (Final)

| Component | Choice | Rationale |
|-----------|--------|-----------|
| Language | Python 3.11+ | Best ecosystem for audio + ML |
| Audio capture | BlackHole 2ch | Already standard for Mac audio routing |
| Audio library | PyAudio | Simple, well-documented |
| STT | `whisper.cpp` via `pywhispercpp` | Fast on Apple Silicon, ~150MB RAM |
| Whisper model | `base.en` | English-only, fast, accurate enough for clear audio |
| LLM | Qwen3:30b via Ollama REST API | Already running, 60 tok/s |
| Web UI | FastAPI + vanilla HTML/JS | Simple, no build step |
| Storage | Markdown files | Obsidian-compatible |

---

## System Requirements

**Pre-installed (verify before starting):**
- [ ] Ollama running with Qwen3:30b loaded (`ollama run qwen3:30b`)
- [ ] BlackHole 2ch installed and configured as audio output
- [ ] Python 3.11+ with pip
- [ ] Homebrew (for whisper.cpp dependencies)

**Directory structure:**
```
~/Projects/hook-assistant/
├── main.py              # Entry point
├── audio_capture.py     # BlackHole → buffer
├── transcriber.py       # Whisper integration
├── hook_generator.py    # Ollama API calls
├── web_server.py        # FastAPI server
├── templates/
│   └── index.html       # Simple UI
├── output/
│   └── [call files]     # Markdown outputs
├── config.py            # Settings
└── requirements.txt
```

---

## Functional Specification

### 1. Audio Capture

**Input:** BlackHole 2ch virtual audio device
**Output:** 10-second audio chunks (16kHz, mono, 16-bit PCM)

**Behavior:**
- Continuously capture audio from BlackHole device
- Buffer in 10-second chunks
- Pass each chunk to transcriber
- Handle device disconnection gracefully (log error, retry)

**Configuration:**
```python
AUDIO_DEVICE = "BlackHole 2ch"
SAMPLE_RATE = 16000
CHUNK_DURATION_SECONDS = 10
```

### 2. Transcription

**Input:** 10-second audio chunks
**Output:** Text transcript with timestamp

**Behavior:**
- Transcribe each audio chunk immediately upon receipt
- Append to rolling transcript buffer (keep last 180 seconds / 18 chunks)
- Return transcribed text with start timestamp

**Configuration:**
```python
WHISPER_MODEL = "base.en"
TRANSCRIPT_BUFFER_SECONDS = 180
```

### 3. Hook Generation

**Input:** Rolling transcript buffer (last 180 seconds)
**Output:** 2-3 hook suggestions

**Behavior:**
- Every 10 seconds, send transcript buffer to Ollama
- Include previously generated hooks in prompt to avoid repetition
- Parse response into individual hooks
- Add timestamp to each hook
- Append to hooks list

**Prompt (exact):**
```
You are a podcast content expert. Generate 2-3 punchy, memorable hooks from this conversation snippet.

Rules:
- Each hook must be under 12 words
- Hooks should be quotable soundbites, not summaries
- Avoid generic phrases like "the future of" or "game-changer"
- Each hook should capture a distinct insight

Already suggested (don't repeat similar ideas):
{previous_hooks}

Recent conversation:
{transcript}

Return only the hooks, one per line, no numbering or bullets.
```

**Configuration:**
```python
OLLAMA_MODEL = "qwen3:30b"
OLLAMA_URL = "http://localhost:11434/api/generate"
GENERATION_INTERVAL_SECONDS = 10
MAX_HOOKS_IN_CONTEXT = 20
```

### 4. Web UI

**Endpoint:** `http://localhost:8000`

**Features:**
- Display hooks in reverse chronological order (newest first)
- Show timestamp for each hook
- Auto-refresh every 2 seconds (poll `/api/hooks`)
- Show connection status (green = working, red = error)
- Show transcript buffer (collapsible)

**API Endpoints:**
```
GET  /                  → HTML page
GET  /api/hooks         → JSON list of all hooks
GET  /api/transcript    → Current transcript buffer
GET  /api/status        → System status (audio, whisper, ollama)
POST /api/start         → Begin capture
POST /api/stop          → End capture, save file
```

**UI Layout (320px wide):**
```
┌────────────────────────┐
│ ● Recording  00:14:32  │
├────────────────────────┤
│ [Stop & Save]          │
├────────────────────────┤
│                        │
│ 14:28                  │
│ "You believe in AI.    │
│  You just don't know   │
│  where to point it."   │
│                        │
│ ─────────────────────  │
│                        │
│ 14:18                  │
│ "The gap isn't         │
│  belief—it's action."  │
│                        │
│ ─────────────────────  │
│                        │
│ [Show Transcript ▼]    │
│                        │
└────────────────────────┘
```

### 5. File Output

**Trigger:** User clicks "Stop & Save" or process terminated

**Output path:** `~/Projects/hook-assistant/output/YYYY-MM-DD-HHMMSS.md`

**File format:**
```markdown
---
date: 2026-01-30
start_time: 14:00:00
duration_minutes: 45
---

# Call Recording — 2026-01-30 14:00

## Hooks Generated

- [00:14:28] "You believe in AI. You just don't know where to point it."
- [00:14:18] "The gap isn't belief—it's action."
- [00:12:05] "AI won't fix a broken business. It'll scale what's working."

## Full Transcript

[00:00:00] Speaker: So I've been thinking about how agencies can actually use AI...

[00:00:15] Speaker: Right, and the problem is everyone's bought in but nobody knows where to start...

[continued...]
```

---

## Test Cases

The build is complete when ALL of the following pass:

### Test 1: Audio Capture
**Setup:** Play a YouTube video through system audio, route to BlackHole
**Action:** Run `python audio_capture.py --test`
**Expected:** Console prints "Captured 10s chunk" every 10 seconds
**Pass criteria:** 6 consecutive chunks captured without error (60 seconds)

### Test 2: Transcription
**Setup:** Pre-recorded 30-second audio file of clear English speech
**Action:** Run `python transcriber.py --test --file test_audio.wav`
**Expected:** Transcription printed to console within 5 seconds
**Pass criteria:** Transcription is >80% accurate (spot check 10 words)

### Test 3: Whisper + Audio Integration
**Setup:** Play a podcast clip through BlackHole
**Action:** Run `python main.py --transcribe-only`
**Expected:** Live transcription appears in console as audio plays
**Pass criteria:** Transcription updates every 10 seconds, readable text output

### Test 4: Hook Generation (Isolated)
**Setup:** None (uses Ollama)
**Action:** Run `python hook_generator.py --test`
**Input:** Hardcoded sample transcript (provided below)
**Expected:** 2-3 hooks printed, each under 12 words
**Pass criteria:** Hooks are relevant to input, not generic platitudes

**Sample transcript for Test 4:**
```
The problem isn't that people don't believe in AI. Everyone's bought in at this point.
The real issue is they don't know where to point it. They've got this powerful tool
and they're using it to write emails. Meanwhile their business is drowning in
manual processes that could be automated in an afternoon.
```

### Test 5: Hook Generation (No Repetition)
**Setup:** None
**Action:** Run `python hook_generator.py --test-dedup`
**Input:** Same transcript, but with previous hooks list populated
**Expected:** New hooks that are distinct from the "previous" list
**Pass criteria:** No hook is semantically identical to the previous hooks

### Test 6: Web UI Loads
**Setup:** None
**Action:** Run `python web_server.py`, open `http://localhost:8000`
**Expected:** Page loads with "Ready" status, empty hooks list
**Pass criteria:** No console errors, page renders correctly

### Test 7: Full Pipeline (Manual)
**Setup:**
1. Open OpenPhone in browser, join a test call (or play a podcast)
2. Route audio through BlackHole
3. Run `python main.py`
4. Open `http://localhost:8000`

**Action:** Let it run for 2 minutes
**Expected:**
- Hooks appear in UI every ~10 seconds after first 20 seconds
- Hooks are relevant to what's being said
- UI updates without manual refresh

**Pass criteria:** At least 6 hooks generated, at least 3 are usable quality

### Test 8: Save Output
**Setup:** Complete Test 7
**Action:** Click "Stop & Save" in UI
**Expected:**
- Markdown file created in `output/` directory
- File contains frontmatter, hooks section, transcript section
- Hooks have timestamps

**Pass criteria:** File opens correctly in Obsidian, all sections present

---

## Error Handling

| Error | Behavior |
|-------|----------|
| BlackHole not found | Exit with clear error message: "BlackHole not detected. Install from..." |
| Ollama not running | Exit with error: "Ollama not responding. Run `ollama serve`" |
| Ollama model not loaded | Exit with error: "Model qwen3:30b not found. Run `ollama pull qwen3:30b`" |
| Audio dropout (>5s silence) | Log warning, continue listening |
| Whisper fails on chunk | Log error, skip chunk, continue |
| Ollama timeout (>30s) | Log error, skip this generation cycle, continue |

---

## Files to Create

1. **requirements.txt**
```
fastapi==0.109.0
uvicorn==0.27.0
pyaudio==0.2.14
pywhispercpp==1.2.0
requests==2.31.0
jinja2==3.1.3
```

2. **config.py** — All constants from this spec

3. **audio_capture.py** — BlackHole capture with chunking

4. **transcriber.py** — Whisper wrapper with buffer management

5. **hook_generator.py** — Ollama API wrapper with prompt

6. **web_server.py** — FastAPI app with endpoints

7. **templates/index.html** — Minimal UI

8. **main.py** — Orchestration, ties everything together

---

## Out of Scope (Do Not Build Tonight)

- USED/GOOD/BAD tagging
- Semantic deduplication (accept some repeats)
- Topic flagging (hooks only)
- Follow-up question generation
- Pre-call research
- Calendar integration
- Pretty styling
- Mobile responsiveness
- Any cloud services

---

## Definition of Done

Phase 0 is complete when:

1. ✅ All 8 test cases pass
2. ✅ Trevor has used it on one real call
3. ✅ At least 5 hooks from that call are "usable quality" (Trevor's judgment)
4. ✅ Markdown output file is saved and readable in Obsidian
5. ✅ No crashes during a 45-minute call

---

## Future Phases (Reference Only)

**Phase 1:** UI polish, USED/GOOD/BAD tagging, deduplication
**Phase 2:** Calendar integration, pre-call research
**Phase 3:** Hosted beta at AltaNora, Twilio dial-in
**Phase 4:** Commercial launch ($19/$49/$99 tiers)

See `live-call-hook-assistant-prd-full.md` for complete commercial roadmap.

---

*End of Phase 0 Specification*
