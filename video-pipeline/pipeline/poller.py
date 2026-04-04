import hashlib
import logging
import os
import time
from pipeline.config import WORK_DIR
from pipeline.ingest import download_video
from pipeline.transcribe import transcribe_video
from pipeline.segment import segment_topics
from pipeline.hooks import select_hook
from pipeline.headlines import generate_headline
from pipeline.editor import extract_segment, extract_frame, prepend_hook, create_short, get_clip_duration
from pipeline.thumbnails import generate_thumbnail
from pipeline.storage import upload_file
from pipeline.notify import post_message, post_review_card
from pipeline.notion_board import (
    get_ingest_cards,
    create_clip_card,
    update_card_status,
    get_scheduled_cards,
)
from pipeline.publisher import publish_clip

logger = logging.getLogger(__name__)


def _get_rich_text(props: dict, key: str) -> str | None:
    """Safely extract plain text from a Notion rich_text property."""
    prop = props.get(key, {})
    parts = prop.get("rich_text", [])
    if parts:
        return parts[0].get("plain_text", "")
    return None


def _notify(func, *args, **kwargs):
    """Call a notification function, swallowing errors so they don't break the pipeline."""
    try:
        return func(*args, **kwargs)
    except Exception:
        logger.warning("Notification failed: %s", func.__name__, exc_info=True)


def process_video(url: str, parent_card_id: str) -> list[str]:
    """Process a single video URL through the full pipeline. Returns list of created card IDs."""
    _notify(post_message, f"Processing video: {url}")

    work_dir = os.path.join(WORK_DIR, str(int(time.time())))
    os.makedirs(work_dir, exist_ok=True)

    # Dedup: reuse cached download if same URL was processed before
    cache_key = hashlib.md5(url.encode()).hexdigest()
    cache_dir = os.path.join(WORK_DIR, f"cache-{cache_key}")
    cached_video = os.path.join(cache_dir, "source.mp4")
    if os.path.exists(cached_video):
        video_path = cached_video
    else:
        os.makedirs(cache_dir, exist_ok=True)
        video_path = download_video(url, cache_dir)

    try:
        transcript = transcribe_video(video_path)
        topics = segment_topics(transcript)

        card_ids = []
        for i, topic in enumerate(topics):
            topic_dir = os.path.join(work_dir, f"topic-{i}")
            os.makedirs(topic_dir, exist_ok=True)

            hook = select_hook(topic)
            meta = generate_headline(topic)

            segment_path = os.path.join(topic_dir, "segment.mp4")
            extract_segment(video_path, topic["start"], topic["end"], segment_path)

            hook_clip_path = os.path.join(topic_dir, "hook.mp4")
            extract_segment(video_path, hook["start"], hook["end"], hook_clip_path)

            final_path = os.path.join(topic_dir, "final.mp4")
            prepend_hook(hook_clip_path, segment_path, final_path)

            hook_dur = get_clip_duration(hook_clip_path)

            frame_path = os.path.join(topic_dir, "frame.png")
            extract_frame(video_path, topic["start"], frame_path)

            thumbnail_path = os.path.join(topic_dir, "thumbnail.png")
            generate_thumbnail(frame_path, meta["headline"], thumbnail_path)

            short_path = os.path.join(topic_dir, "short.mp4")
            create_short(
                final_path,
                short_path,
                segments=topic.get("segments"),
                topic_start=topic["start"],
                hook_duration=hook_dur,
            )

            slug = f"clips/{int(time.time())}-topic-{i}"
            clip_url = upload_file(final_path, f"{slug}/clip.mp4")
            short_url = upload_file(short_path, f"{slug}/short.mp4")
            thumbnail_url = upload_file(thumbnail_path, f"{slug}/thumbnail.png")

            duration_secs = topic["end"] - topic["start"]
            minutes = int(duration_secs // 60)
            seconds = int(duration_secs % 60)

            card_id = create_clip_card(
                headline=meta["headline"],
                topic_name=topic["topic"],
                hook_sentence=hook["sentence"],
                clip_url=clip_url,
                short_url=short_url,
                thumbnail_url=thumbnail_url,
                description=meta["description"],
                duration=f"{minutes}:{seconds:02d}",
                parent_card_id=parent_card_id,
            )
            card_ids.append(card_id)

            _notify(post_message, f"Clip {i + 1}/{len(topics)} ready: {topic['topic']}")
            _notify(post_review_card,
                    topic_name=topic["topic"],
                    short_url=short_url,
                    thumbnail_url=thumbnail_url,
                    card_id=card_id)

        _notify(post_message, f"Done! {len(topics)} clips ready for review")
        return card_ids

    except Exception as e:
        _notify(post_message, f"Pipeline failed for {url}: {e}")
        raise


def publish_scheduled(before: str | None = None) -> int:
    """Publish all clips whose scheduled time has arrived. Returns count published."""
    cards = get_scheduled_cards(before)
    published = 0

    for card in cards:
        props = card["properties"]
        clip_url = props["Clip URL"]["url"]
        headline = props["Headline"]["rich_text"][0]["plain_text"]
        platforms = [p["name"] for p in props["Platforms"]["multi_select"]]

        # Get post text — default and per-platform overrides
        post_text = _get_rich_text(props, "Post Text") or _get_rich_text(props, "Description") or ""
        custom_content = {}
        yt_text = _get_rich_text(props, "YouTube Text")
        li_text = _get_rich_text(props, "LinkedIn Text")
        if yt_text:
            custom_content["youtube"] = yt_text
        if li_text:
            custom_content["linkedin"] = li_text

        publish_clip(
            video_url=clip_url,
            thumbnail_url=props.get("Thumbnail URL", {}).get("url", ""),
            title=headline,
            description=post_text,
            platforms=platforms,
            custom_content=custom_content or None,
        )
        update_card_status(card["id"], "Published")
        published += 1

    return published


def poll_and_process() -> None:
    """Poll Notion for new ingest cards and process them."""
    cards = get_ingest_cards()
    for card in cards:
        card_id = card["id"]
        url_prop = card["properties"].get("Source Video", {})
        url = url_prop.get("url")
        if not url:
            continue

        update_card_status(card_id, "Processing")
        try:
            process_video(url, card_id)
            update_card_status(card_id, "To Review")
        except Exception as e:
            update_card_status(card_id, "Ingest")
            raise
