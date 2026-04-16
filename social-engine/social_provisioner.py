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

# Default number of Text Insight posts to render as quote cards (speaker still +
# pull-quote) rather than generative risograph images. Override via CLI
# (--quote-cards N) or per-client config (client.quote_card_count).
DEFAULT_QUOTE_CARD_COUNT = 7


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


def provision_text_insights(stories, target_count, database_id, default_platforms, notion_client,
                             session_folder=None, clips_by_story_id=None,
                             quote_card_count=0, brand_attribution="",
                             force=False):
    """Phase 2: Create Social DB cards for text insight posts.

    Returns a list of dicts: {page_id, style, context}
      - style is "quote_card" (top `quote_card_count` stories) or "risograph" (rest)
      - context (for quote_card only) carries clip_path, quote, attribution, variant
        so Phase 3 can render without reconstructing from Notion
    """
    selected = select_text_insights(stories, target_count)
    created = []
    variants = ("dark", "light")

    for idx, story in enumerate(selected):
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

        # Decide graphic style: top `quote_card_count` stories (by engagement,
        # already sorted descending by select_text_insights) get quote cards.
        style = "quote_card" if idx < quote_card_count else "risograph"
        context = {}
        if style == "quote_card":
            story_id = story.get("id", "")
            clip = (clips_by_story_id or {}).get(story_id)
            if clip and session_folder:
                clip_path = os.path.join(session_folder, "final", clip.get("filename", ""))
                if os.path.exists(clip_path):
                    context = {
                        "clip_path": clip_path,
                        "quote": hook or title,
                        "attribution": brand_attribution,
                        "variant": variants[idx % len(variants)],
                    }
                else:
                    log.warning(
                        "Quote card for story %s requested but clip file missing: %s. "
                        "Falling back to risograph.", story_id, clip_path,
                    )
                    style = "risograph"
            else:
                log.warning(
                    "Quote card for story %s requested but no matching clip in manifest. "
                    "Falling back to risograph.", story_id,
                )
                style = "risograph"

        created.append({"page_id": page_id, "style": style, "context": context})
        log.info("Created Text Insight card [%s]: %s (%s)", style, title, page_id)
        time.sleep(0.35)

    return created


def generate_content_for_cards(cards, notion_client, config):
    """Phase 3: Generate copy (and graphics for text insights) for all new cards.

    `cards` is a list of dicts: {page_id, style?, context?}.
    For backwards compat, bare page_id strings are also accepted.
    """
    for card in cards:
        if isinstance(card, str):
            card = {"page_id": card, "style": "", "context": {}}
        page_id = card["page_id"]
        style = card.get("style", "")
        context = card.get("context", {})

        row = notion_client.get_page(page_id)
        gen_status = row.get("Generation Status", "")

        if gen_status in ("Complete", ""):
            log.info("Skipping %s — Generation Status is '%s'", page_id, gen_status)
            continue

        try:
            generate_social_copy(page_id, notion_client, config)

            if row.get("Post Type", "") == "Text Insight":
                if style == "quote_card" and context.get("clip_path"):
                    generate_quote_card_for_card(page_id, row, notion_client, config, context)
                else:
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


def generate_quote_card_for_card(page_id, row, notion_client, config, context):
    """Generate a quote card via social_quotecard (face-detected, brand-palette-driven).

    context = {clip_path, quote, attribution, variant}
    """
    import sys
    import tempfile
    from datetime import datetime
    from pathlib import Path

    # social_quotecard lives in this same directory
    from social_quotecard import (
        load_brand, extract_candidate_frames, pick_best_frame,
        auto_crop_params, render_card,
    )
    # storage lives in video-pipeline
    sys.path.insert(0, os.path.expanduser("~/dev/podruff-site/video-pipeline"))
    from pipeline.storage import upload_file  # type: ignore

    client_name = config.get("client_name", "")
    try:
        brand = load_brand(client_name)
    except (KeyError, ValueError) as e:
        log.error("No brand config for %s: %s — skipping quote card for %s",
                  client_name, e, page_id)
        return

    clip_path = Path(context["clip_path"])
    quote = context.get("quote", "")
    attribution = context.get("attribution", "")
    variant = context.get("variant", "light")
    if not quote:
        log.warning("Quote card has no quote text for %s", page_id); return
    if not attribution:
        log.warning("Quote card has no attribution for %s — using client name", page_id)
        attribution = client_name.replace("-", " ").title()

    subtitle_trim = float(config.get("clients", {}).get(client_name, {})
                          .get("brand", {}).get("subtitle_trim", 0.65))

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        frames = extract_candidate_frames(clip_path, tmp / "frames", count=5)
        best = pick_best_frame(frames, subtitle_trim=subtitle_trim)
        params = auto_crop_params(best, subtitle_trim, 1200, 630)

        out_path = tmp / f"card_{page_id[:8]}.png"
        render_card(brand, best, quote, attribution,
                    variant=variant, aspect="landscape",
                    out_path=out_path, logo_width=200,
                    subtitle_trim=subtitle_trim,
                    **params)

        date_prefix = datetime.now().strftime("%Y/%m/%d")
        remote_key = f"social-posts/{date_prefix}/quote-card-{page_id[:8]}.png"
        url = upload_file(str(out_path), remote_key)

    if url:
        notion_client.update_page(page_id, {"Thumbnail URL": {"url": url}})
        log.info("Uploaded quote card for %s: %s", page_id, url)


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


def provision_session(session_folder, client_name, config, force=False, quote_card_count=None):
    """Main entry point: provision all social posts for a completed session.

    quote_card_count: how many of the Text Insight posts to render as quote cards
        (speaker still + pull-quote). The rest go through the risograph / Gemini path.
        Resolution order: explicit arg → client_cfg.quote_card_count → DEFAULT_QUOTE_CARD_COUNT.
    """
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

    # Resolve quote-card split
    if quote_card_count is None:
        quote_card_count = client_cfg.get("quote_card_count", DEFAULT_QUOTE_CARD_COUNT)
    brand_attribution = client_cfg.get("brand", {}).get("attribution_default", "")
    clips_by_story_id = {c.get("story_id", ""): c for c in clips}

    text_cards = provision_text_insights(
        stories, len(clips), database_id, default_platforms, notion,
        session_folder=session_folder,
        clips_by_story_id=clips_by_story_id,
        quote_card_count=quote_card_count,
        brand_attribution=brand_attribution,
        force=force,
    )
    log.info("Phase 2 complete: %d text insight cards created (%d quote_card, %d risograph)",
             len(text_cards),
             sum(1 for c in text_cards if c["style"] == "quote_card"),
             sum(1 for c in text_cards if c["style"] == "risograph"))

    all_cards = [{"page_id": vid, "style": "video_clip", "context": {}} for vid in video_ids]
    all_cards.extend(text_cards)
    if all_cards:
        generate_content_for_cards(all_cards, notion, config)
        log.info("Phase 3 complete: content generated for %d cards", len(all_cards))
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
    parser.add_argument("--quote-cards", type=int, default=None,
                        help="Number of Text Insight posts to render as quote cards "
                             "(speaker still + pull-quote). Default: client.quote_card_count "
                             f"or {DEFAULT_QUOTE_CARD_COUNT}. Remaining insights use risograph.")
    args = parser.parse_args()

    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "social_config.json")
    with open(config_path) as f:
        config = json.load(f)

    provision_session(args.session_folder, args.client, config,
                      force=args.force, quote_card_count=args.quote_cards)


if __name__ == "__main__":
    main()
