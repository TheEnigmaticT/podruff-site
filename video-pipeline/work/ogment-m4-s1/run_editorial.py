"""Manual editorial pipeline run for Ogment M4 S1 — dual-speaker version."""
import json
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

# Force all passes to use qwen3:8b
os.environ["EDITORIAL_OUTLINE_MODEL"] = "qwen3:8b"
os.environ["EDITORIAL_STORIES_MODEL"] = "qwen3:8b"
os.environ["EDITORIAL_CUT_MODEL"] = "qwen3:8b"

from pipeline.editorial import run_editorial_pipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

work_dir = os.path.dirname(__file__)
transcript_path = os.path.join(work_dir, "transcript_dual.json")
output_dir = os.path.join(work_dir, "editorial")

with open(transcript_path) as f:
    transcript = json.load(f)

guest_segs = [s for s in transcript if s.get("speaker") == "GUEST"]
interviewer_segs = [s for s in transcript if s.get("speaker") == "INTERVIEWER"]
print(f"Loaded {len(transcript)} segments ({len(guest_segs)} GUEST, {len(interviewer_segs)} INTERVIEWER)")
print(f"Duration: {transcript[-1]['end']:.0f}s (~{transcript[-1]['end']/60:.0f} min)")

# Source video placeholder — we'll use the actual .mov from Drive for rendering later
source_video = os.path.join(work_dir, "teo.mp3")

edls = run_editorial_pipeline(
    transcript=transcript,
    source_video=source_video,
    output_dir=output_dir,
    min_score=6,
)

print(f"\n=== Results: {len(edls)} clips selected ===")
for edl in edls:
    short_dur = edl['versions']['short']['estimated_duration']
    long_dur = edl['versions']['long']['estimated_duration']
    print(f"  {edl['story_id']}: short={short_dur:.0f}s, long={long_dur:.0f}s")
