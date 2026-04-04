# Phase 1 Testing Plan

Run through these tomorrow morning. Should take ~15 minutes total.

---

## Pre-flight (2 min)

1. Make sure Ollama is running with Qwen3:30b loaded:
   ```
   ollama run qwen3:30b
   ```
   (If it's already running, just confirm with `ollama list`)

2. Make sure BlackHole 2ch is your system audio output (System Settings → Sound → Output)

3. Have a podcast or YouTube video ready to play through system audio

---

## Test 1: Unit tests still pass (30 sec)

```
cd ~/dev/hook-assistant-prd
python3 -m pytest tests/ -v
```

**Expected:** 39 passed, 0 failed.

---

## Test 2: Start the server (1 min)

```
python3 main.py
```

**Expected:** You see:
- "Loading Whisper model..."
- "Checking Ollama..."
- "Starting web server on http://localhost:8000"

No errors. If Ollama isn't running you'll get a clear error message.

---

## Test 3: UI loads correctly (1 min)

Open `http://localhost:8000` in your browser.

**Check:**
- [ ] Sidebar layout, dark background, max ~380px wide
- [ ] Yellow "Ready" dot in header
- [ ] Timer showing 00:00:00
- [ ] "Start recording" button (green)
- [ ] Filter pills: All, Hooks, Topics, Follow-ups
- [ ] "Show transcript" toggle at bottom
- [ ] Empty state message: "Suggestions will stream here once recording starts."

---

## Test 4: Full pipeline — live recording (5 min)

1. Start playing a podcast/YouTube through system audio (routed to BlackHole)
2. Click **Start recording** in the UI
3. Let it run for **2 minutes**

**Check during recording:**
- [ ] Status dot turns green, label says "Live"
- [ ] Timer counts up
- [ ] After ~20 seconds, suggestion cards start appearing
- [ ] You see **three types**: green "Hook" badges, yellow "Topic" badges, purple "Follow-up" badges
- [ ] Cards show timestamps and text
- [ ] New cards appear at the top (reverse chronological)
- [ ] Cards stream in without page refresh (SSE, not polling)

---

## Test 5: Filter toggles (1 min)

While recording is running:

1. Click **Hooks** filter — only green hook cards visible
2. Click **Topics** — only yellow topic cards
3. Click **Follow-ups** — only purple follow-up cards
4. Click **All** — everything back

**Check:**
- [ ] Filtering is instant (client-side)
- [ ] Reloading the page remembers your last filter choice

---

## Test 6: Tagging (2 min)

While recording is running:

1. Find a hook you like → click **Used** button
   - [ ] Card shows "USED" badge, button highlights green
2. Find a topic worth keeping → click **Good**
   - [ ] Card shows "GOOD" badge, button highlights yellow
3. Find a bad suggestion → click **Bad**
   - [ ] Card fades out (opacity drops)
   - [ ] After a few seconds, a replacement suggestion of the same type should appear
4. Click a tag button again on the same card to toggle it off
   - [ ] Status resets

---

## Test 7: Transcript viewer (30 sec)

1. Click **Show transcript** at the bottom
   - [ ] Transcript text appears, updating every 3 seconds
2. Click **Hide transcript** to collapse it

---

## Test 8: Stop & save (2 min)

1. Click **Stop & save**
2. Status should show "Saving..." briefly while post-call analysis runs
3. An alert pops up with the saved file path

**Check the saved file:**
```
ls ~/Projects/hook-assistant/output/
cat ~/Projects/hook-assistant/output/[latest file].md
```

**The Markdown file should have:**
- [ ] YAML frontmatter (date, start_time, duration_minutes)
- [ ] `## Hooks Generated` section split into Used / Unused / Rejected
- [ ] `## Topics Flagged` section
- [ ] `## Follow-up Questions` section
- [ ] `## Topics Discussed` (from post-call analysis)
- [ ] `## Topics Not Discussed` (from post-call analysis)
- [ ] `## Follow-ups for Next Time`
- [ ] `## Full Transcript` at the bottom
- [ ] All items have timestamps in [HH:MM:SS] format

---

## Known limitations

- **No semantic dedup yet** — you may get similar suggestions across cycles. Prompt-based dedup only.
- **Post-call analysis quality** depends on how much transcript was captured. Short recordings produce thin analysis.
- **Tagging toggle** sends a new API call each time — if you're on a slow network (you won't be, it's localhost) there's a brief lag.

---

## If something breaks

- Check the terminal where `main.py` is running for error logs
- Common issues:
  - "BlackHole not detected" → check System Settings audio output
  - "Ollama not responding" → `ollama serve` in another terminal
  - Port 8000 in use → kill whatever's on it (`lsof -i :8000`)
