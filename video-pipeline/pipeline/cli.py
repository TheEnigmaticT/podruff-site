import json
import logging
import os
import click
from pipeline.poller import process_video, publish_scheduled, poll_and_process

logger = logging.getLogger(__name__)


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
    from pipeline.fcp7 import generate_fcp7_xml

    if output_dir is None:
        output_dir = os.path.expanduser(f"~/Documents/editorial-{url.split('/')[-1]}")

    os.makedirs(output_dir, exist_ok=True)
    cache_dir = os.path.join(output_dir, "cache")
    os.makedirs(cache_dir, exist_ok=True)

    if os.path.isfile(url):
        click.echo(f"Using local file: {url}")
        video_path = url
    else:
        click.echo("Downloading video...")
        import subprocess as sp
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

            # FCP 7 XML project for Premiere users
            fcp7_xml = generate_fcp7_xml(
                version, video_path, sequence_name=f"{story_id}-{version_name}",
            )
            fcp7_path = os.path.join(projects_dir, f"{story_id}-{version_name}.xml")
            with open(fcp7_path, "w") as f:
                f.write(fcp7_xml)

    click.echo(f"\nDone! Output in {output_dir}")


# ---------------------------------------------------------------------------
# Drive-based ingest helpers
# ---------------------------------------------------------------------------

def _process_session(drive, session: dict, client_slug: str, state) -> None:
    """Process one Zencastr Drive session through the full editorial pipeline.

    Steps (each is skipped if already completed per state):
    1. Download source video
    2. Transcribe with Parakeet
    3. Run editorial pipeline (LLM 3-pass)
    4. Generate FCP 7 XML for each story/version
    5. Upload video + XMLs to client Drive folder
    6. Notify Slack
    7. Mark complete in state
    """
    from pipeline.drive import DriveClient
    from pipeline.client_config import load_soul, get_drive_folder
    from pipeline.config import WORK_DIR
    from pipeline.transcribe import transcribe_video
    from pipeline.editorial import run_editorial_pipeline
    from pipeline.fcp7 import generate_fcp7_xml
    from pipeline.notify import post_message

    folder_id = session["folder_id"]
    folder_name = session["folder_name"]
    resume_step = state.get_step(folder_id)

    work_dir = os.path.join(WORK_DIR, folder_id)
    cache_dir = os.path.join(work_dir, "cache")
    clips_dir = os.path.join(work_dir, "clips")
    os.makedirs(cache_dir, exist_ok=True)
    os.makedirs(clips_dir, exist_ok=True)

    # ------------------------------------------------------------------ #
    # Step 1: Download source video
    # ------------------------------------------------------------------ #
    video_file = session["video_files"][0]
    video_name = video_file.get("name", f"{folder_id}.mp4")
    video_path = os.path.join(cache_dir, video_name)

    if resume_step < "downloaded" or not os.path.exists(video_path):
        click.echo(f"  Downloading {video_name}...")
        drive.download_file(video_file["id"], video_path)
        state.mark_step(folder_id, "downloaded")
    else:
        click.echo(f"  Video already downloaded: {video_name}")

    # ------------------------------------------------------------------ #
    # Step 2: Transcribe
    # ------------------------------------------------------------------ #
    transcript_path = os.path.join(cache_dir, "transcript.json")

    if resume_step < "transcribed" or not os.path.exists(transcript_path):
        click.echo("  Transcribing with Parakeet...")
        transcript = transcribe_video(video_path)
        with open(transcript_path, "w") as f:
            json.dump(transcript, f, ensure_ascii=False, indent=2)
        state.mark_step(folder_id, "transcribed")
    else:
        click.echo("  Loading cached transcript...")
        with open(transcript_path) as f:
            transcript = json.load(f)

    # ------------------------------------------------------------------ #
    # Step 3: Editorial pipeline
    # ------------------------------------------------------------------ #
    click.echo("  Running editorial pipeline...")
    edls = run_editorial_pipeline(transcript, video_path, work_dir, min_score=7)
    state.mark_step(folder_id, "editorial")

    # ------------------------------------------------------------------ #
    # Step 4: Generate FCP 7 XMLs
    # ------------------------------------------------------------------ #
    xml_paths = []
    for edl in edls:
        story_id = edl["story_id"]
        for version_name, version in edl["versions"].items():
            fcp7_xml = generate_fcp7_xml(
                version, video_path, sequence_name=f"{story_id}-{version_name}",
            )
            xml_filename = f"{story_id}-{version_name}.xml"
            xml_path = os.path.join(clips_dir, xml_filename)
            with open(xml_path, "w") as f:
                f.write(fcp7_xml)
            xml_paths.append(xml_path)
            click.echo(f"  Generated XML: {xml_filename}")
    state.mark_step(folder_id, "fcp7")

    # ------------------------------------------------------------------ #
    # Step 5: Upload to client Drive folder
    # ------------------------------------------------------------------ #
    client_drive_folder = get_drive_folder(client_slug)
    session_folder_id = drive.create_folder(folder_name, client_drive_folder)
    clips_folder_id = drive.create_folder("clips", session_folder_id)

    click.echo(f"  Uploading video to Drive...")
    drive.upload_file(video_path, session_folder_id)

    for xml_path in xml_paths:
        click.echo(f"  Uploading {os.path.basename(xml_path)}...")
        drive.upload_file(xml_path, clips_folder_id)

    state.mark_step(folder_id, "uploaded")

    # ------------------------------------------------------------------ #
    # Step 6: Notify Slack
    # ------------------------------------------------------------------ #
    soul = load_soul(client_slug)
    slack_channel = soul.get("slack_channel", "")
    n_clips = len(xml_paths)
    message = (
        f"Found {n_clips} clips for {folder_name}. "
        "XMLs uploaded to Drive — ready for editing."
    )
    if slack_channel:
        try:
            post_message(message, channel=slack_channel)
            click.echo(f"  Notified Slack channel {slack_channel}")
        except Exception as exc:
            logger.warning("Slack notification failed (non-blocking): %s", exc)
    else:
        click.echo(f"  No Slack channel configured for {client_slug}, skipping notification")

    # ------------------------------------------------------------------ #
    # Step 7: Mark complete
    # ------------------------------------------------------------------ #
    state.mark_complete(folder_id)
    click.echo(f"  Session {folder_name} complete.")


@cli.command("poll-all")
@click.option("--zencastr-folder", default=None, help="Zencastr root folder ID in Drive")
@click.option("--dry-run", is_flag=True, help="Show what would be processed without doing it")
def poll_all(zencastr_folder, dry_run):
    """Scan Zencastr Drive folder and process all new sessions."""
    from pipeline.drive import DriveClient
    from pipeline.drive_poller import ProcessingState, scan_zencastr_sessions
    from pipeline.client_config import match_client
    from pipeline.config import ZENCASTR_FOLDER_ID, WORK_DIR

    folder_id = zencastr_folder or ZENCASTR_FOLDER_ID
    if not folder_id:
        click.echo("ERROR: ZENCASTR_FOLDER_ID not set. Pass --zencastr-folder or set env var.", err=True)
        raise SystemExit(1)

    click.echo(f"Initializing Drive client...")
    drive = DriveClient()

    state_path = os.path.join(WORK_DIR, "processing_state.json")
    os.makedirs(WORK_DIR, exist_ok=True)
    state = ProcessingState(state_path)

    click.echo(f"Scanning Zencastr folder {folder_id}...")
    sessions = scan_zencastr_sessions(drive, folder_id, state)
    click.echo(f"Found {len(sessions)} unprocessed session(s).")

    for session in sessions:
        folder_name = session["folder_name"]
        client_slug = match_client(folder_name)

        if not client_slug:
            click.echo(f"  WARNING: No client match for session '{folder_name}', skipping.")
            continue

        if dry_run:
            n_videos = len(session["video_files"])
            click.echo(
                f"  [dry-run] Would process '{folder_name}' "
                f"({n_videos} video file(s)) for client '{client_slug}'"
            )
            continue

        click.echo(f"\nProcessing session '{folder_name}' for client '{client_slug}'...")
        _process_session(drive, session, client_slug, state)
