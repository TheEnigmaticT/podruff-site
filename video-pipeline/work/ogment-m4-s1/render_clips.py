"""Render draft clips + subtitles + Kdenlive XML for Ogment M4 S1."""
import json
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from pipeline.edl import render_edl_version, generate_clip_subtitles, generate_kdenlive_xml
from pipeline.editor import _detect_face_center

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

work_dir = os.path.dirname(__file__)
editorial_dir = os.path.join(work_dir, "editorial")
source_video = os.path.join(work_dir, "teo.mov")

# Load transcript (dual-speaker, but subtitle gen only needs GUEST segments)
with open(os.path.join(work_dir, "transcript_dual.json")) as f:
    transcript = json.load(f)

# Use only GUEST segments for subtitle generation
guest_transcript = [s for s in transcript if s.get("speaker") == "GUEST"]

# Detect face position for vertical crop
print("Detecting face position...")
face_pos = _detect_face_center(source_video)
print(f"Face position: {face_pos}")

# Load all EDLs
edits_dir = os.path.join(editorial_dir, "edits")
edl_files = sorted(f for f in os.listdir(edits_dir) if f.endswith(".json"))

drafts_dir = os.path.join(work_dir, "drafts")
subs_dir = os.path.join(work_dir, "subs")
projects_dir = os.path.join(work_dir, "projects")
os.makedirs(drafts_dir, exist_ok=True)
os.makedirs(subs_dir, exist_ok=True)
os.makedirs(projects_dir, exist_ok=True)

for edl_file in edl_files:
    with open(os.path.join(edits_dir, edl_file)) as f:
        edl = json.load(f)

    story_id = edl["story_id"]
    print(f"\n=== Rendering: {story_id} ===")

    for version_name in ("short", "long"):
        version = edl["versions"][version_name]
        if not version["segments"]:
            print(f"  {version_name}: no segments, skipping")
            continue

        crop_mode = "vertical" if version_name == "short" else "horizontal"
        sub_style = "karaoke" if version_name == "short" else "srt"
        ext = ".ass" if sub_style == "karaoke" else ".srt"

        # Generate subtitles first
        sub_path = os.path.join(subs_dir, f"{story_id}-{version_name}{ext}")
        print(f"  Generating {version_name} subtitles...")
        generate_clip_subtitles(version, guest_transcript, sub_path, style=sub_style)

        # Render video
        out_path = os.path.join(drafts_dir, f"{story_id}-{version_name}.mp4")
        print(f"  Rendering {version_name} ({version['estimated_duration']:.0f}s)...")
        try:
            actual_durations = render_edl_version(
                version,
                source_video,
                out_path,
                crop_mode=crop_mode,
                face_pos=face_pos if crop_mode == "vertical" else None,
                subtitle_path=sub_path,
            )

            # Re-generate subtitles with actual durations for better sync
            generate_clip_subtitles(version, guest_transcript, sub_path,
                                   style=sub_style, actual_durations=actual_durations)

            print(f"  {version_name} rendered: {out_path}")
        except Exception as e:
            print(f"  ERROR rendering {version_name}: {e}")

    # Generate Kdenlive XML project for each version
    for version_name in ("short", "long"):
        version = edl["versions"][version_name]
        if not version["segments"]:
            continue
        profile = "vertical" if version_name == "short" else "horizontal"
        sub_ext = ".ass" if version_name == "short" else ".srt"
        sub_path = os.path.join(subs_dir, f"{story_id}-{version_name}{sub_ext}")
        draft_path = os.path.join(drafts_dir, f"{story_id}-{version_name}.mp4")

        xml = generate_kdenlive_xml(
            version,
            source_video,
            profile=profile,
            subtitle_path=sub_path if os.path.exists(sub_path) else None,
            face_pos=face_pos if profile == "vertical" else None,
            draft_video=draft_path if os.path.exists(draft_path) else None,
        )
        xml_path = os.path.join(projects_dir, f"{story_id}-{version_name}.kdenlive")
        with open(xml_path, "w") as f:
            f.write(xml)

print("\n=== Done! ===")
print(f"Drafts:   {drafts_dir}")
print(f"Subs:     {subs_dir}")
print(f"Projects: {projects_dir}")
