"""Re-render Jonathan Brill's 4 shorts from actual Kdenlive edits (v3).

Reads the Kdenlive project files edited on 2026-04-14, parses the cut points,
and produces v3 MP4s that match what the editor sees in Kdenlive.

Usage:
    .venv/bin/python scripts/rerender_jb_from_kdenlive.py
"""

import json
import logging
import os
import subprocess
import sys
import tempfile

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SOURCE_VIDEO = "/Users/ct-mac-mini/Documents/editorial-brill/cache/I7X4Lrkj2IU.mp4"
TRANSCRIPT_PATH = "/Users/ct-mac-mini/Documents/editorial-brill/cache/transcript.json"
LOGO_PATH = "/Users/ct-mac-mini/Downloads/JB_Horz_BLK_ORG.png"
DRAFTS_DIR = "/Users/ct-mac-mini/Documents/editorial-brill/drafts"
PROJECTS_DIR = "/Users/ct-mac-mini/Documents/editorial-brill/projects"

FFMPEG = "/opt/homebrew/bin/ffmpeg"
FFPROBE = "/opt/homebrew/bin/ffprobe"

CLIPS = [
    "agi-is-science-fiction-here-s-my-proof-short",
    "usain-bolt-wombat-rocket-short",
    "truck-driver-job-not-task-short",
    "octopus-organization-model-short",
]

SUBTITLE_STYLE = {
    "highlight_color": "#E38533",
    "font": "Inter",
    "size": 120,
}

# ---------------------------------------------------------------------------
# Pipeline imports
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.mlt import parse_kdenlive_mlt
from pipeline.edl import render_edl_version, generate_clip_subtitles
from pipeline.editor import _detect_face_center
from pipeline.branding import generate_end_card


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _probe_duration(path: str) -> float:
    """Return video duration in seconds via ffprobe."""
    result = subprocess.run(
        [
            FFPROBE, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path,
        ],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


def _concat_videos(main: str, end_card: str, output: str) -> None:
    """Concatenate main video and end card using ffmpeg concat demuxer."""
    # End card has no audio — add silent audio track first
    silent_end_card = tempfile.mktemp(suffix="_ec_silent.mp4")
    concat_list = tempfile.mktemp(suffix="_concat.txt")
    try:
        logger.info("Adding silent audio to end card...")
        subprocess.run(
            [
                FFMPEG, "-y",
                "-i", end_card,
                "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                "-shortest",
                "-c:v", "copy",
                "-c:a", "aac", "-b:a", "192k",
                silent_end_card,
            ],
            check=True, capture_output=True,
        )

        with open(concat_list, "w") as f:
            f.write(f"file '{main}'\nfile '{silent_end_card}'\n")

        logger.info("Concatenating main + end card -> %s", output)
        subprocess.run(
            [
                FFMPEG, "-y",
                "-f", "concat", "-safe", "0",
                "-i", concat_list,
                "-c", "copy",
                output,
            ],
            check=True, capture_output=True,
        )
    finally:
        for tmp in (silent_end_card, concat_list):
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass


# ---------------------------------------------------------------------------
# Main render loop
# ---------------------------------------------------------------------------

def main():
    # Verify source files exist
    for path in (SOURCE_VIDEO, TRANSCRIPT_PATH, LOGO_PATH):
        if not os.path.exists(path):
            logger.error("Required file missing: %s", path)
            sys.exit(1)

    # Load transcript
    logger.info("Loading transcript...")
    with open(TRANSCRIPT_PATH) as f:
        transcript = json.load(f)

    # Detect face once (same source video for all clips)
    logger.info("Detecting face center in source video...")
    face_pos = _detect_face_center(SOURCE_VIDEO)
    logger.info("Face position: %s", face_pos)

    os.makedirs(DRAFTS_DIR, exist_ok=True)

    # Temp directory for intermediate files
    work_dir = tempfile.mkdtemp(prefix="jb_v3_")
    logger.info("Work dir: %s", work_dir)

    results = []

    for slug in CLIPS:
        logger.info("\n=== Processing: %s ===", slug)

        kdenlive_path = os.path.join(PROJECTS_DIR, f"{slug}.kdenlive")
        if not os.path.exists(kdenlive_path):
            logger.error("Kdenlive file not found: %s", kdenlive_path)
            continue

        # Step 1: Parse Kdenlive → time ranges
        logger.info("Parsing Kdenlive: %s", kdenlive_path)
        time_ranges = parse_kdenlive_mlt(kdenlive_path)
        if not time_ranges:
            logger.error("No time ranges parsed from %s — skipping", kdenlive_path)
            continue
        logger.info("Parsed %d segment(s): %s", len(time_ranges), time_ranges)

        # Step 2: Wrap into EDL version dict
        edl_version = {
            "segments": [{"type": "body", "start": s, "end": e} for s, e in time_ranges],
            "trims": [],
        }

        # Step 3: Render draft (no subtitles) to get actual durations
        draft_path = os.path.join(work_dir, f"{slug}-draft.mp4")
        logger.info("Rendering draft (no subs): %s", draft_path)
        actual_durations = render_edl_version(
            edl_version,
            SOURCE_VIDEO,
            draft_path,
            crop_mode="vertical",
            face_pos=face_pos,
        )
        logger.info("Actual segment durations: %s", actual_durations)

        # Step 4: Generate subtitles
        sub_path = os.path.join(work_dir, f"{slug}.ass")
        logger.info("Generating karaoke subtitles: %s", sub_path)
        generate_clip_subtitles(
            edl_version,
            transcript,
            sub_path,
            style="karaoke",
            subtitle_style=SUBTITLE_STYLE,
            actual_durations=actual_durations,
        )

        # Step 5: Re-render with subtitles burned in
        subtitled_path = os.path.join(work_dir, f"{slug}-subtitled.mp4")
        logger.info("Burning subtitles: %s", subtitled_path)
        render_edl_version(
            edl_version,
            SOURCE_VIDEO,
            subtitled_path,
            crop_mode="vertical",
            face_pos=face_pos,
            subtitle_path=sub_path,
        )

        # Step 6: Generate end card
        end_card_path = os.path.join(work_dir, f"{slug}-end-card.mp4")
        logger.info("Generating end card: %s", end_card_path)
        generate_end_card(
            logo_path=LOGO_PATH,
            cta_text="Visit JonathanBrill.com for more",
            output_path=end_card_path,
            duration=2,
            fps=30,
        )

        # Step 7: Concat main video + end card
        final_path = os.path.join(DRAFTS_DIR, f"{slug}-en-v3.mp4")
        logger.info("Concatenating -> %s", final_path)
        _concat_videos(subtitled_path, end_card_path, final_path)

        # Verify output
        if os.path.exists(final_path):
            duration = _probe_duration(final_path)
            size_mb = os.path.getsize(final_path) / 1_000_000
            logger.info(
                "DONE: %s  (%.1f s, %.1f MB)",
                os.path.basename(final_path), duration, size_mb,
            )
            results.append({
                "slug": slug,
                "path": final_path,
                "duration_s": round(duration, 2),
                "size_mb": round(size_mb, 1),
            })
        else:
            logger.error("Output not found after concat: %s", final_path)

    # Summary
    print("\n" + "=" * 60)
    print("RENDER SUMMARY")
    print("=" * 60)
    for r in results:
        print(f"  {r['slug']}-en-v3.mp4")
        print(f"    duration: {r['duration_s']} s   size: {r['size_mb']} MB")
        print(f"    path:     {r['path']}")
    print(f"\n{len(results)}/{len(CLIPS)} clips rendered successfully.")

    import shutil
    shutil.rmtree(work_dir, ignore_errors=True)
    logger.info("Cleaned up work dir: %s", work_dir)


if __name__ == "__main__":
    main()
