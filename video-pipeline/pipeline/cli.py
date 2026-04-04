import os
import click
from pipeline.poller import process_video, publish_scheduled, poll_and_process


@click.group()
def cli():
    """Video pipeline: ingest, segment, publish."""
    pass


@cli.command()
@click.argument("url")
def ingest(url):
    """Download and process a single video by URL."""
    click.echo(f"Processing: {url}")
    card_ids = process_video(url, parent_card_id="manual")
    click.echo(f"Created {len(card_ids)} clips")


@cli.command()
def poll():
    """Poll Notion for new ingest cards and process them."""
    click.echo("Polling Notion for new cards...")
    poll_and_process()
    click.echo("Done.")


@cli.command()
@click.argument("source")
@click.option("--output-dir", "-o", default=None, help="Output directory (default: ~/Documents/shorts)")
def short(source, output_dir):
    """Process a single video into shorts. SOURCE can be a URL or local file path."""
    if output_dir is None:
        output_dir = os.path.expanduser("~/Documents/shorts")
    os.makedirs(output_dir, exist_ok=True)
    click.echo(f"Processing: {source}")
    card_ids = process_video(source, parent_card_id="cli")
    click.echo(f"Created {len(card_ids)} clips")


@cli.command()
@click.option("--dry-run", is_flag=True, help="Show what would be published.")
def publish(dry_run):
    """Publish clips whose scheduled time has arrived."""
    if dry_run:
        from pipeline.notion_board import get_scheduled_cards
        cards = get_scheduled_cards()
        for card in cards:
            title = card["properties"]["Headline"]["rich_text"][0]["plain_text"]
            pub_date = card["properties"].get("Publish Date", {}).get("date", {}).get("start", "?")
            click.echo(f"  Would publish: {title} (scheduled: {pub_date})")
        click.echo(f"Total: {len(cards)} clips ready")
    else:
        count = publish_scheduled()
        click.echo(f"Published {count} clips")


@cli.command()
@click.argument("url")
@click.option("--output-dir", default=None, help="Output directory")
@click.option("--max-clips", type=int, default=6, help="Maximum clips to produce")
@click.option("--min-score", type=int, default=7, help="Minimum engagement score")
def editorial(url, output_dir, max_clips, min_score):
    """Run the multi-pass editorial pipeline on a YouTube video."""
    import json
    from pipeline.transcribe import transcribe_video
    from pipeline.editorial import run_editorial_pipeline
    from pipeline.edl import render_edl_version, generate_kdenlive_xml, generate_clip_subtitles
    from pipeline.editor import _detect_face_center

    if output_dir is None:
        output_dir = os.path.expanduser(f"~/Documents/editorial-{url.split('/')[-1]}")

    os.makedirs(output_dir, exist_ok=True)
    cache_dir = os.path.join(output_dir, "cache")

    # Download via yt-dlp
    click.echo("Downloading video...")
    import subprocess as sp
    os.makedirs(cache_dir, exist_ok=True)
    output_template = os.path.join(cache_dir, "%(id)s.%(ext)s")
    sp.run(["yt-dlp", "-f", "bestvideo+bestaudio/best",
            "-o", output_template, "--merge-output-format", "mp4", url],
           check=True, capture_output=True)
    video_path = next(f for f in [os.path.join(cache_dir, x) for x in os.listdir(cache_dir)] if f.endswith(".mp4"))

    # Transcribe
    transcript_path = os.path.join(cache_dir, "transcript.json")
    if os.path.exists(transcript_path):
        click.echo("Loading cached transcript...")
        with open(transcript_path) as f:
            transcript = json.load(f)
    else:
        click.echo("Transcribing with Parakeet...")
        transcript = transcribe_video(video_path)
        with open(transcript_path, "w") as f:
            json.dump(transcript, f, ensure_ascii=False, indent=2)

    # Editorial pipeline
    click.echo("Running editorial pipeline...")
    edls = run_editorial_pipeline(transcript, video_path, output_dir, min_score)
    edls = edls[:max_clips]

    # Detect face for vertical crop
    face_pos = _detect_face_center(video_path)

    # Render drafts and Kdenlive projects
    drafts_dir = os.path.join(output_dir, "drafts")
    projects_dir = os.path.join(output_dir, "projects")
    subs_dir = os.path.join(output_dir, "subs")
    os.makedirs(drafts_dir, exist_ok=True)
    os.makedirs(projects_dir, exist_ok=True)
    os.makedirs(subs_dir, exist_ok=True)

    for edl in edls:
        story_id = edl["story_id"]
        for version_name, version in edl["versions"].items():
            crop = "vertical" if version_name == "short" else "horizontal"
            profile = crop
            sub_style = "karaoke" if version_name == "short" else "srt"
            sub_ext = ".ass" if sub_style == "karaoke" else ".srt"

            # Render without subtitles first to get actual segment durations
            draft_path = os.path.join(drafts_dir, f"{story_id}-{version_name}-en.mp4")
            click.echo(f"Rendering {story_id} ({version_name})...")
            actual_durations = render_edl_version(
                version, video_path, draft_path, crop_mode=crop, face_pos=face_pos,
            )

            # Generate subtitles using actual durations for timing sync
            sub_path = os.path.join(subs_dir, f"{story_id}-{version_name}{sub_ext}")
            generate_clip_subtitles(
                version, transcript, sub_path, style=sub_style,
                actual_durations=actual_durations,
            )

            # Re-render with burned-in subtitles
            click.echo(f"Burning subtitles for {story_id} ({version_name})...")
            render_edl_version(
                version, video_path, draft_path, crop_mode=crop,
                face_pos=face_pos, subtitle_path=sub_path,
            )

            # Kdenlive project — uses source video with EDL segment in/out points;
            # vertical shorts get an affine filter for face-centered crop
            xml = generate_kdenlive_xml(
                version, video_path, profile=profile, subtitle_path=sub_path,
                face_pos=face_pos,
            )
            project_path = os.path.join(projects_dir, f"{story_id}-{version_name}.kdenlive")
            with open(project_path, "w") as f:
                f.write(xml)

    click.echo(f"\nDone! Output in {output_dir}")
