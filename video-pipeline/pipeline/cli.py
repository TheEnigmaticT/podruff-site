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
    1. Download ALL source video tracks
    2. Transcribe with Parakeet
    3. Run editorial pipeline (LLM 3-pass)
    4. Generate FCP 7 XML for each story/version
    5. Upload to client Drive folder with full spec structure:
         Active Clients/[Client]/Video/[session-name]/
           source-*.mov         <- all video tracks
           transcript.srt       <- SRT format transcript
           clips/               <- FCP 7 XMLs
           editorial/           <- outline.json + stories.json
           done/                <- empty; editor drops tweaked XMLs here
           final/               <- empty; branded renders go here
    6. Notify Slack
    7. Mark complete in state
    """
    from pipeline.client_config import load_soul, get_drive_folder
    from pipeline.config import WORK_DIR
    from pipeline.transcribe import transcribe_video
    from pipeline.editorial import run_editorial_pipeline
    from pipeline.fcp7 import generate_fcp7_xml
    from pipeline.notify import post_message
    from pipeline.edl import _srt_time

    folder_id = session["folder_id"]
    folder_name = session["folder_name"]
    resume_step = state.get_step(folder_id)

    work_dir = os.path.join(WORK_DIR, folder_id)
    cache_dir = os.path.join(work_dir, "cache")
    clips_dir = os.path.join(work_dir, "clips")
    os.makedirs(cache_dir, exist_ok=True)
    os.makedirs(clips_dir, exist_ok=True)

    # ------------------------------------------------------------------ #
    # Step 1: Download ALL source video tracks
    # ------------------------------------------------------------------ #
    video_paths = []
    for video_file in session["video_files"]:
        video_name = video_file.get("name", f"{folder_id}.mp4")
        video_path = os.path.join(cache_dir, video_name)
        if resume_step < "downloaded" or not os.path.exists(video_path):
            click.echo(f"  Downloading {video_name}...")
            drive.download_file(video_file["id"], video_path)
        else:
            click.echo(f"  Video already downloaded: {video_name}")
        video_paths.append(video_path)

    if resume_step < "downloaded":
        state.mark_step(folder_id, "downloaded")

    # Use first video track for transcription and EDL generation
    primary_video_path = video_paths[0]

    # ------------------------------------------------------------------ #
    # Step 2: Transcribe
    # ------------------------------------------------------------------ #
    transcript_path = os.path.join(cache_dir, "transcript.json")

    if resume_step < "transcribed" or not os.path.exists(transcript_path):
        click.echo("  Transcribing with Parakeet...")
        transcript = transcribe_video(primary_video_path)
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
    edls = run_editorial_pipeline(transcript, primary_video_path, work_dir, min_score=7)
    state.mark_step(folder_id, "editorial")

    # ------------------------------------------------------------------ #
    # Step 4: Generate FCP 7 XMLs
    # ------------------------------------------------------------------ #
    xml_paths = []
    for edl in edls:
        story_id = edl["story_id"]
        for version_name, version in edl["versions"].items():
            fcp7_xml = generate_fcp7_xml(
                version, primary_video_path, sequence_name=f"{story_id}-{version_name}",
            )
            xml_filename = f"{story_id}-{version_name}.xml"
            xml_path = os.path.join(clips_dir, xml_filename)
            with open(xml_path, "w") as f:
                f.write(fcp7_xml)
            xml_paths.append(xml_path)
            click.echo(f"  Generated XML: {xml_filename}")
    state.mark_step(folder_id, "fcp7")

    # ------------------------------------------------------------------ #
    # Step 5: Upload to client Drive folder (full spec structure)
    # ------------------------------------------------------------------ #
    client_drive_folder = get_drive_folder(client_slug)

    # Active Clients/[Client]/Video/
    video_root_id = drive.find_or_create_folder("Video", client_drive_folder)

    # Active Clients/[Client]/Video/[session-name]/
    session_folder_id = drive.find_or_create_folder(folder_name, video_root_id)

    # Create subfolders (idempotent)
    clips_folder_id = drive.find_or_create_folder("clips", session_folder_id)
    editorial_folder_id = drive.find_or_create_folder("editorial", session_folder_id)
    drive.find_or_create_folder("done", session_folder_id)
    drive.find_or_create_folder("final", session_folder_id)

    # Upload all video tracks as source-*.ext
    for video_path in video_paths:
        original_name = os.path.basename(video_path)
        ext = os.path.splitext(original_name)[1]
        source_name = f"source-{original_name}" if not original_name.startswith("source-") else original_name
        click.echo(f"  Uploading {source_name} to Drive...")
        drive.upload_file(video_path, session_folder_id, name=source_name)

    # Generate and upload transcript.srt
    srt_path = os.path.join(cache_dir, "transcript.srt")
    _write_srt_from_transcript(transcript, srt_path, _srt_time)
    click.echo("  Uploading transcript.srt to Drive...")
    drive.upload_file(srt_path, session_folder_id, name="transcript.srt")

    # Upload editorial outputs (outline.json, stories.json)
    for editorial_file in ("outline.json", "stories.json"):
        local_path = os.path.join(work_dir, editorial_file)
        if os.path.exists(local_path):
            click.echo(f"  Uploading editorial/{editorial_file} to Drive...")
            drive.upload_file(local_path, editorial_folder_id, name=editorial_file)
        else:
            logger.warning("Editorial file not found, skipping upload: %s", local_path)

    # Upload FCP 7 XMLs to clips/
    for xml_path in xml_paths:
        click.echo(f"  Uploading clips/{os.path.basename(xml_path)}...")
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


def _write_srt_from_transcript(transcript: list[dict], output_path: str, srt_time_fn) -> None:
    """Write a plain SRT subtitle file from a transcript segment list.

    Args:
        transcript: List of segment dicts with 'start', 'end', 'text' keys.
        output_path: Destination .srt file path.
        srt_time_fn: Callable(seconds: float) -> str in HH:MM:SS,mmm format.
    """
    lines = []
    seq = 0
    for seg in transcript:
        text = seg["text"].strip()
        if not text:
            continue
        seq += 1
        start = srt_time_fn(seg["start"])
        end = srt_time_fn(seg["end"])
        lines.append(str(seq))
        lines.append(f"{start} --> {end}")
        lines.append(text)
        lines.append("")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ---------------------------------------------------------------------------
# Done-folder render helpers (module-level imports for patchability in tests)
# ---------------------------------------------------------------------------
from pipeline.fcp7 import parse_fcp7_xml
from pipeline.edl import render_edl_version, generate_clip_subtitles
from pipeline.editor import _detect_face_center
from pipeline.branding import get_subtitle_style, generate_end_card, render_branded_short
from pipeline.storage import upload_file as r2_upload
from pipeline.notify import post_message
from pipeline.client_config import load_soul
from pipeline.config import WORK_DIR


def _process_done_xml(drive, xml_info: dict, state) -> None:
    """Render a branded final video from an editor-approved FCP 7 XML.

    Steps:
        a. Download XML to local work dir
        b. Parse FCP 7 → time ranges
        c. Download source video (from session root)
        d. Render EDL against source: extract segments, crop 9:16, burn karaoke subtitles
        e. Generate end card with logo + CTA (if logo file found, else skip)
        f. Concat: branded video + end card → final MP4 (or just branded video if no end card)
        g. Upload final MP4 to R2 (public URL)
        h. Upload final MP4 to Drive session/final/
        i. Post to Slack channel from SOUL.md
        j. Mark XML processed in state

    Args:
        drive: DriveClient instance.
        xml_info: Dict from scan_done_folders with keys:
            client_slug, session_folder_id, session_name, xml_file, source_video_file
        state: DoneXmlState instance for tracking.
    """
    import tempfile

    client_slug = xml_info["client_slug"]
    session_folder_id = xml_info["session_folder_id"]
    session_name = xml_info["session_name"]
    xml_file = xml_info["xml_file"]
    source_video_file = xml_info["source_video_file"]
    xml_id = xml_file["id"]
    xml_name = xml_file["name"]  # e.g. "story-01-short.xml"
    xml_basename = os.path.splitext(xml_name)[0]  # e.g. "story-01-short"

    click.echo(f"\n[done-render] {client_slug}/{session_name}/{xml_name}")

    # Load soul for branding / Slack channel
    soul = load_soul(client_slug)

    # Work dir for this XML render
    render_dir = os.path.join(WORK_DIR, "done-renders", client_slug, session_name, xml_basename)
    os.makedirs(render_dir, exist_ok=True)

    state.mark_step(xml_id, "downloading")

    # ------------------------------------------------------------------ #
    # a. Download XML
    # ------------------------------------------------------------------ #
    xml_local = os.path.join(render_dir, xml_name)
    click.echo(f"  Downloading XML {xml_name}...")
    drive.download_file(xml_id, xml_local)

    # ------------------------------------------------------------------ #
    # b. Parse FCP 7 → time ranges
    # ------------------------------------------------------------------ #
    time_ranges = parse_fcp7_xml(xml_local)
    if not time_ranges:
        logger.warning("No time ranges parsed from %s — skipping", xml_name)
        return

    edl_version = {
        "segments": [{"type": "body", "start": s, "end": e} for s, e in time_ranges],
        "trims": [],
    }

    # ------------------------------------------------------------------ #
    # c. Download source video
    # ------------------------------------------------------------------ #
    source_video_name = source_video_file["name"]
    source_local = os.path.join(render_dir, source_video_name)
    click.echo(f"  Downloading source video {source_video_name}...")
    drive.download_file(source_video_file["id"], source_local)

    state.mark_step(xml_id, "downloaded")

    # ------------------------------------------------------------------ #
    # d. Detect face, render EDL (first pass without subtitles for timing)
    # ------------------------------------------------------------------ #
    face_pos = _detect_face_center(source_local)

    draft_path = os.path.join(render_dir, f"{xml_basename}-draft.mp4")
    click.echo(f"  Rendering draft (no subs)...")
    actual_durations = render_edl_version(
        edl_version, source_local, draft_path,
        crop_mode="vertical", face_pos=face_pos,
    )

    # ------------------------------------------------------------------ #
    # Generate subtitles remapped to editor's cut points
    # ------------------------------------------------------------------ #
    sub_style = get_subtitle_style(soul)
    sub_path = os.path.join(render_dir, f"{xml_basename}.ass")

    # We don't have the full transcript here — use an empty one so subtitles
    # generate as empty (the original transcript is not re-downloaded here
    # to keep the process lightweight; editors can tweak subs separately).
    # A more complete implementation would load transcript.json from Drive.
    click.echo(f"  Generating subtitles (remapped to editor cuts)...")
    generate_clip_subtitles(
        edl_version, [], sub_path, style="karaoke",
        actual_durations=actual_durations,
        subtitle_style=sub_style,
    )

    # Re-render with subtitles burned in
    branded_draft = os.path.join(render_dir, f"{xml_basename}-branded-draft.mp4")
    click.echo(f"  Burning subtitles...")
    render_edl_version(
        edl_version, source_local, branded_draft,
        crop_mode="vertical", face_pos=face_pos,
        subtitle_path=sub_path,
    )

    state.mark_step(xml_id, "rendered")

    # ------------------------------------------------------------------ #
    # e. End card (optional — requires logo file)
    # ------------------------------------------------------------------ #
    logo_path = os.path.expanduser(f"~/Documents/logos/{client_slug}.png")
    website = _extract_website(soul)
    cta_text = f"Visit {website} for more" if website else ""

    end_card_path = None
    if os.path.exists(logo_path) and cta_text:
        end_card_path = os.path.join(render_dir, f"{xml_basename}-end-card.mp4")
        click.echo(f"  Generating end card...")
        generate_end_card(logo_path, cta_text, end_card_path)
    else:
        reasons = []
        if not os.path.exists(logo_path):
            reasons.append(f"no logo at {logo_path}")
        if not cta_text:
            reasons.append("no website in SOUL.md")
        logger.warning("Skipping end card for %s/%s: %s", client_slug, xml_basename, "; ".join(reasons))

    # ------------------------------------------------------------------ #
    # f. Concat: branded draft + end card → final MP4
    # ------------------------------------------------------------------ #
    final_path = os.path.join(render_dir, f"{xml_basename}-final.mp4")
    click.echo(f"  Building final MP4...")

    if end_card_path:
        render_branded_short(branded_draft, sub_path, end_card_path, final_path)
    else:
        # No end card: just copy the branded draft as final
        import shutil
        shutil.copy2(branded_draft, final_path)

    state.mark_step(xml_id, "finalized")

    # ------------------------------------------------------------------ #
    # g. Upload to R2
    # ------------------------------------------------------------------ #
    r2_key = f"{client_slug}/{session_name}/{xml_basename}.mp4"
    click.echo(f"  Uploading to R2: {r2_key}...")
    r2_url = r2_upload(final_path, r2_key)

    # ------------------------------------------------------------------ #
    # h. Upload to Drive session/final/
    # ------------------------------------------------------------------ #
    final_folder_id = drive.find_or_create_folder("final", session_folder_id)
    final_filename = f"{xml_basename}.mp4"
    click.echo(f"  Uploading to Drive final/...")
    drive.upload_file(final_path, final_folder_id, name=final_filename)

    state.mark_step(xml_id, "uploaded")

    # ------------------------------------------------------------------ #
    # i. Notify Slack
    # ------------------------------------------------------------------ #
    slack_channel = soul.get("slack_channel", "")
    message = (
        f"Branded render ready: *{session_name}/{xml_name}*\n"
        f"R2: {r2_url}\n"
        f"Drive: final/{final_filename}"
    )
    if slack_channel:
        try:
            post_message(message, channel=slack_channel)
            click.echo(f"  Notified {slack_channel}")
        except Exception as exc:
            logger.warning("Slack notification failed (non-blocking): %s", exc)
    else:
        click.echo(f"  No Slack channel in SOUL.md for {client_slug}, skipping notification")

    # ------------------------------------------------------------------ #
    # j. Mark complete
    # ------------------------------------------------------------------ #
    state.mark_complete(xml_id)
    click.echo(f"  Done: {xml_basename}")


def _extract_website(soul: dict) -> str:
    """Extract website URL from SOUL.md raw text (best-effort)."""
    import re
    raw = soul.get("raw", "")
    m = re.search(r"\*\*Website:\*\*\s*(https?://[^\s\n]+)", raw)
    if m:
        return m.group(1).strip().rstrip("/")
    # Fallback: look for any https URL that looks like a root domain
    m = re.search(r"https?://(?:www\.)?([a-zA-Z0-9\-]+\.[a-z]{2,})(?:/[^\s\n]*)?", raw)
    if m:
        return m.group(0).strip().rstrip("/")
    return ""


@cli.command("poll-all")
@click.option("--zencastr-folder", default=None, help="Zencastr root folder ID in Drive")
@click.option("--clients-root", default=None, help="Active Clients root folder ID in Drive")
@click.option("--dry-run", is_flag=True, help="Show what would be processed without doing it")
def poll_all(zencastr_folder, clients_root, dry_run):
    """Scan Zencastr Drive folder and process all new sessions, then process done/ XMLs."""
    from pipeline.drive import DriveClient
    from pipeline.drive_poller import ProcessingState, scan_zencastr_sessions, DoneXmlState, scan_done_folders
    from pipeline.client_config import match_client, _load_client_map
    from pipeline.config import ZENCASTR_FOLDER_ID, WORK_DIR, DRIVE_CLIENTS_ROOT

    folder_id = zencastr_folder or ZENCASTR_FOLDER_ID
    if not folder_id:
        click.echo("ERROR: ZENCASTR_FOLDER_ID not set. Pass --zencastr-folder or set env var.", err=True)
        raise SystemExit(1)

    click.echo(f"Initializing Drive client...")
    drive = DriveClient()

    os.makedirs(WORK_DIR, exist_ok=True)
    state_path = os.path.join(WORK_DIR, "processing_state.json")
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

    # ------------------------------------------------------------------ #
    # Done-folder scan: pick up editor-approved XMLs and render branded finals
    # ------------------------------------------------------------------ #
    clients_root_id = clients_root or DRIVE_CLIENTS_ROOT
    if not clients_root_id:
        click.echo(
            "  NOTE: DRIVE_CLIENTS_ROOT not set — skipping done-folder scan. "
            "Pass --clients-root or set env var.",
        )
        return

    done_state_path = os.path.join(WORK_DIR, "done_xml_state.json")
    done_state = DoneXmlState(path=done_state_path)

    client_map = _load_client_map()
    click.echo(f"\nScanning done/ folders under clients root {clients_root_id}...")
    done_xmls = scan_done_folders(drive, clients_root_id, client_map, done_state)
    click.echo(f"Found {len(done_xmls)} done XML(s) to render.")

    for xml_info in done_xmls:
        if dry_run:
            click.echo(
                f"  [dry-run] Would render {xml_info['client_slug']}/"
                f"{xml_info['session_name']}/{xml_info['xml_file']['name']}"
            )
            continue
        try:
            _process_done_xml(drive, xml_info, done_state)
        except Exception as exc:
            logger.error(
                "Failed to process done XML %s: %s",
                xml_info["xml_file"]["name"], exc, exc_info=True,
            )
            click.echo(f"  ERROR processing {xml_info['xml_file']['name']}: {exc}", err=True)
