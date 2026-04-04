"""Social Post Provisioner — creates Social DB cards from completed video sessions.

Given a session folder with final/ videos and editorial/stories.json, creates:
- Video Clip cards for each final video
- Text Insight cards for high-scoring transcript moments
- Generates platform copy (Claude) and branded graphics (Nano Banana) for each
"""

import glob
import json
import logging
import os
import re
import time

from social_copywriter import generate_social_copy
from social_notion import NotionClient

log = logging.getLogger("social_provisioner")

MIN_ENGAGEMENT_SCORE = 7

POST_TYPES = {
    "video_clip": {
        "has_video": True,
        "media_source": "upstream",
        "needs_graphic": False,
        "copy_context": "clip",
    },
    "text_insight": {
        "has_video": False,
        "media_source": "generated",
        "needs_graphic": True,
        "copy_context": "moment",
    },
}


def load_manifest(session_folder):
    """Load final/manifest.json or fall back to scanning *.mp4 files.

    Returns a list of clip dicts with keys: filename, story_id, clip_url, thumbnail_url, short_url.
    """
    manifest_path = os.path.join(session_folder, "final", "manifest.json")
    if os.path.exists(manifest_path):
        with open(manifest_path) as f:
            data = json.load(f)
        return data.get("clips", [])

    log.warning("No manifest.json found, falling back to mp4 scan")
    mp4s = sorted(glob.glob(os.path.join(session_folder, "final", "*.mp4")))
    clips = []
    for path in mp4s:
        fname = os.path.basename(path)
        story_id = slug_from_filename(fname)
        clips.append({
            "filename": fname,
            "story_id": story_id,
            "clip_url": "",
            "thumbnail_url": "",
            "short_url": "",
        })
    return clips


def slug_from_filename(filename):
    """Extract story ID from a final video filename.

    Expected formats:
      01-revenue-dropped-short-en.mp4 -> revenue-dropped
      02-growth-long-en.mp4 -> growth
      custom-name.mp4 -> custom-name
    """
    name = os.path.splitext(filename)[0]
    name = re.sub(r"^\d+-", "", name)
    name = re.sub(r"-(short|long)(-[a-z]{2})?$", "", name)
    return name


def load_stories(session_folder):
    """Load editorial/stories.json. Returns list of story dicts or empty list."""
    stories_path = os.path.join(session_folder, "editorial", "stories.json")
    if not os.path.exists(stories_path):
        log.warning("No stories.json found at %s", stories_path)
        return []
    with open(stories_path) as f:
        data = json.load(f)
    return data.get("stories", [])


def select_text_insights(stories, target_count):
    """Select top N stories with engagement_score >= MIN_ENGAGEMENT_SCORE.

    Args:
        stories: List of story dicts from stories.json.
        target_count: Target number of text insight posts.

    Returns:
        List of story dicts, sorted by score descending, up to target_count.
    """
    qualified = [s for s in stories if s.get("engagement_score", 0) >= MIN_ENGAGEMENT_SCORE]
    qualified.sort(key=lambda s: s.get("engagement_score", 0), reverse=True)
    return qualified[:target_count]


def build_card_properties(title, post_type, hook="", description="",
                          platforms=None, clip_url=None, thumbnail_url=None,
                          short_url=None, clip_start=None, clip_end=None):
    """Build a Notion properties dict for a Social DB page."""
    desc_text = description or ""
    if clip_start is not None and clip_end is not None:
        desc_text = f"{desc_text}\n\n[clip:{clip_start}-{clip_end}]".strip()

    props = {
        "Title": {"title": [{"text": {"content": title}}]},
        "Status": {"select": {"name": "Draft"}},
        "Post Type": {"select": {"name": post_type}},
        "Generation Status": {"select": {"name": "Pending"}},
        "Hook Sentence": {"rich_text": [{"text": {"content": hook}}] if hook else []},
        "Description": {"rich_text": [{"text": {"content": desc_text}}] if desc_text else []},
        "Platforms": {"multi_select": [{"name": p} for p in (platforms or [])]},
        "Clip URL": {"url": clip_url or None},
        "Thumbnail URL": {"url": thumbnail_url or None},
        "Short URL": {"url": short_url or None},
    }
    return props


def _find_story(stories, story_id):
    """Find a story dict by ID. Returns None if not found."""
    for s in stories:
        if s.get("id") == story_id:
            return s
    return None


def provision_video_clips(clips, stories, database_id, default_platforms, notion_client, force=False):
    """Phase 1: Create Social DB cards for each final video clip."""
    created_ids = []
    for clip in clips:
        story = _find_story(stories, clip.get("story_id", ""))
        title = story["title"] if story else clip.get("filename", "Untitled Clip")
        hook = ""
        description = ""
        if story:
            hooks = story.get("hook_candidates", [])
            hook = hooks[0]["text"] if hooks else ""
            description = story.get("standalone_rationale", "")

        if not force:
            existing = notion_client.query_by_title_and_type(database_id, title, "Video Clip")
            if existing:
                log.info("Skipping duplicate Video Clip: %s", title)
                continue

        props = build_card_properties(
            title=title,
            post_type="Video Clip",
            hook=hook,
            description=description,
            platforms=default_platforms,
            clip_url=clip.get("clip_url") or None,
            thumbnail_url=clip.get("thumbnail_url") or None,
            short_url=clip.get("short_url") or None,
        )
        result = notion_client.create_page(database_id, props)
        page_id = result["id"]
        created_ids.append(page_id)
        log.info("Created Video Clip card: %s (%s)", title, page_id)
        time.sleep(0.35)

    return created_ids


def provision_text_insights(stories, target_count, database_id, default_platforms, notion_client, force=False):
    """Phase 2: Create Social DB cards for text insight posts."""
    selected = select_text_insights(stories, target_count)
    created_ids = []

    for story in selected:
        title = story.get("title", "Untitled Insight")
        hooks = story.get("hook_candidates", [])
        hook = hooks[0]["text"] if hooks else ""
        description = story.get("standalone_rationale", "")

        if not force:
            existing = notion_client.query_by_title_and_type(database_id, title, "Text Insight")
            if existing:
                log.info("Skipping duplicate Text Insight: %s", title)
                continue

        props = build_card_properties(
            title=title,
            post_type="Text Insight",
            hook=hook,
            description=description,
            platforms=default_platforms,
            clip_start=story.get("start"),
            clip_end=story.get("end"),
        )
        result = notion_client.create_page(database_id, props)
        page_id = result["id"]
        created_ids.append(page_id)
        log.info("Created Text Insight card: %s (%s)", title, page_id)
        time.sleep(0.35)

    return created_ids


def generate_content_for_cards(page_ids, notion_client, config):
    """Phase 3: Generate copy (and graphics for text insights) for all new cards."""
    for page_id in page_ids:
        row = notion_client.get_page(page_id)
        gen_status = row.get("Generation Status", "")

        if gen_status in ("Complete", ""):
            log.info("Skipping %s — Generation Status is '%s'", page_id, gen_status)
            continue

        try:
            generate_social_copy(page_id, notion_client, config)

            if row.get("Post Type", "") == "Text Insight":
                generate_graphic_for_card(page_id, row, notion_client, config)

            notion_client.update_page(page_id, {
                "Generation Status": {"select": {"name": "Complete"}},
            })
            log.info("Generated content for %s", page_id)
        except Exception:
            log.exception("Failed to generate content for %s", page_id)
            notion_client.update_page(page_id, {
                "Generation Status": {"select": {"name": "Failed"}},
            })

        time.sleep(1)


def generate_graphic_for_card(page_id, row, notion_client, config):
    """Generate a branded graphic for a text insight card using social_bgraphic."""
    from social_bgraphic import extract_content, generate_image, upload_to_r2
    from google import genai
    import tempfile

    hook = row.get("Hook Sentence", "")
    description = row.get("Description", "")
    transcript_text = f"{hook}\n\n{description}"

    api_key = os.environ.get("GOOGLE_API_KEY", "")
    if not api_key:
        log.warning("No GOOGLE_API_KEY set, skipping graphic generation for %s", page_id)
        return

    gemini = genai.Client(api_key=api_key)

    # Load soul context
    soul_dir = os.path.expanduser("~/.openclaw")
    client_name = config.get("client_name", "crowdtamers")
    soul_path = os.path.join(soul_dir, f"workspace-{client_name}", "SOUL.md")
    soul = ""
    if os.path.exists(soul_path):
        with open(soul_path) as f:
            soul = f.read()

    writing_guide_path = os.path.expanduser(
        "~/Documents/Obsidian/CrowdTamers Obsidian Vault/"
        "_meta/claude-outputs/AI Tasks & Prompts/Quit Writing Like AI.md"
    )
    design_guide_path = os.path.expanduser(
        "~/Documents/Obsidian/CrowdTamers Obsidian Vault/"
        "_meta/claude-outputs/AI Tasks & Prompts/Quit Designing Like AI.md"
    )
    writing_guide = ""
    design_guide = ""
    if os.path.exists(writing_guide_path):
        with open(writing_guide_path) as f:
            writing_guide = f.read()
    if os.path.exists(design_guide_path):
        with open(design_guide_path) as f:
            design_guide = f.read()

    content_items = extract_content(gemini, transcript_text, soul, writing_guide, design_guide, num_images=1)
    if not content_items:
        log.warning("No content items generated for %s", page_id)
        return

    item = content_items[0]
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        success = generate_image(gemini, item.get("prompt", ""), tmp_path)
        if not success:
            log.warning("Image generation failed for %s", page_id)
            return

        from datetime import datetime
        date_prefix = datetime.now().strftime("%Y/%m/%d")
        remote_key = f"social-posts/{date_prefix}/text-insight-{page_id[:8]}.png"
        url = upload_to_r2(tmp_path, remote_key)

        if url:
            notion_client.update_page(page_id, {
                "Thumbnail URL": {"url": url},
            })
            log.info("Uploaded graphic for %s: %s", page_id, url)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def provision_session(session_folder, client_name, config, force=False):
    """Main entry point: provision all social posts for a completed session."""
    client_cfg = config.get("clients", {}).get(client_name, {})
    database_id = client_cfg.get("notion_database_id", "")
    if not database_id:
        log.error("No notion_database_id for client %s", client_name)
        return

    default_platforms = list(client_cfg.get("platforms", {}).keys())

    notion_key = config.get("notion_api_key", os.environ.get("NOTION_API_KEY", ""))
    notion = NotionClient(notion_key)

    config["client_name"] = client_name

    clips = load_manifest(session_folder)
    stories = load_stories(session_folder)

    log.info("Session %s: %d clips, %d stories", session_folder, len(clips), len(stories))

    video_ids = provision_video_clips(clips, stories, database_id, default_platforms, notion, force=force)
    log.info("Phase 1 complete: %d video clip cards created", len(video_ids))

    text_ids = provision_text_insights(stories, len(clips), database_id, default_platforms, notion, force=force)
    log.info("Phase 2 complete: %d text insight cards created", len(text_ids))

    all_ids = video_ids + text_ids
    if all_ids:
        generate_content_for_cards(all_ids, notion, config)
        log.info("Phase 3 complete: content generated for %d cards", len(all_ids))
    else:
        log.info("No new cards to generate content for")


def main():
    """CLI entry point for manual provisioning."""
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Provision social posts from a video session")
    parser.add_argument("session_folder", help="Path to session folder (must contain final/)")
    parser.add_argument("--client", required=True, help="Client name from social_config.json")
    parser.add_argument("--force", action="store_true", help="Skip idempotency checks")
    args = parser.parse_args()

    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "social_config.json")
    with open(config_path) as f:
        config = json.load(f)

    provision_session(args.session_folder, args.client, config, force=args.force)


if __name__ == "__main__":
    main()
