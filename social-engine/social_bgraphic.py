#!/usr/bin/env python3
"""Generate branded social graphics from a transcript or YouTube video.

Usage:
    python3 social_bgraphic.py TRANSCRIPT_SOURCE CLIENT_NAME CHANNEL_ID THREAD_TS [NUM_IMAGES]

    TRANSCRIPT_SOURCE: path to a plain text transcript file, or a YouTube URL
    CLIENT_NAME: client workspace name (e.g., "crowdtamers")
    CHANNEL_ID: Slack channel ID
    THREAD_TS: Slack thread timestamp
    NUM_IMAGES: number of images to generate (default: 5, max: 10)

The script:
  1. Reads style context (SOUL.md + global styleguides)
  2. If YouTube URL: downloads video, extracts frames for founder photo selection
  3. Uses Gemini to extract content and choose generation paths (founder photo or illustrated scene)
  4. For founder photos: LLM selects best frame, applies risograph style transfer
  5. For illustrated scenes: generates from visual vocabulary (subjects + objects)
  6. Uploads to R2 and posts to Slack thread
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime

import requests
from google import genai

# ── Config ────────────────────────────────────────────────────────────

_DIR = os.path.dirname(os.path.abspath(__file__))
SLACK_TOKEN = "xoxb-41124765216-9085008978405-Ix79KEGJDtRCAQEp1TFHzqRu"
IMAGE_MODEL = "gemini-3.1-flash-image-preview"
TEXT_MODEL = "gemini-2.5-flash"

SOUL_DIR = os.path.expanduser("~/.openclaw")
WRITING_GUIDE = os.path.expanduser(
    "~/Documents/Obsidian/CrowdTamers Obsidian Vault/"
    "_meta/claude-outputs/AI Tasks & Prompts/Quit Writing Like AI.md"
)
DESIGN_GUIDE = os.path.expanduser(
    "~/Documents/Obsidian/CrowdTamers Obsidian Vault/"
    "_meta/claude-outputs/AI Tasks & Prompts/Quit Designing Like AI.md"
)


def _get_api_key():
    key = os.environ.get("GEMINI_API_KEY", "")
    if key:
        return key
    try:
        with open(os.path.join(_DIR, "social_config.json")) as f:
            cfg = json.load(f)
        key = cfg.get("gemini_api_key", "")
        if key:
            return key
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    env_path = os.path.expanduser("~/video-pipeline/.env")
    try:
        with open(env_path) as f:
            for line in f:
                if line.strip().startswith("GEMINI_API_KEY="):
                    return line.strip().split("=", 1)[1]
    except FileNotFoundError:
        pass
    return ""


# ── Slack helpers ─────────────────────────────────────────────────────

def slack_post(channel, thread_ts, text):
    """Post a text message to a Slack thread."""
    requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {SLACK_TOKEN}"},
        json={"channel": channel, "thread_ts": thread_ts, "text": text},
        timeout=15,
    )


def slack_upload_image(channel, thread_ts, filepath, title="", comment=""):
    """Upload an image to a Slack thread."""
    # Step 1: get upload URL
    filesize = os.path.getsize(filepath)
    filename = os.path.basename(filepath)
    resp = requests.post(
        "https://slack.com/api/files.getUploadURLExternal",
        headers={"Authorization": f"Bearer {SLACK_TOKEN}"},
        data={"filename": filename, "length": filesize},
        timeout=15,
    )
    data = resp.json()
    if not data.get("ok"):
        slack_post(channel, thread_ts, f"Failed to get upload URL: {data.get('error')}")
        return None

    upload_url = data["upload_url"]
    file_id = data["file_id"]

    # Step 2: upload file
    with open(filepath, "rb") as f:
        requests.post(upload_url, files={"file": (filename, f)}, timeout=120)

    # Step 3: complete upload and share to channel/thread
    requests.post(
        "https://slack.com/api/files.completeUploadExternal",
        headers={
            "Authorization": f"Bearer {SLACK_TOKEN}",
            "Content-Type": "application/json",
        },
        json={
            "files": [{"id": file_id, "title": title or filename}],
            "channel_id": channel,
            "thread_ts": thread_ts,
            "initial_comment": comment,
        },
        timeout=15,
    )
    return file_id


# ── Style loading ─────────────────────────────────────────────────────

def load_soul(client_name):
    """Load client SOUL.md content."""
    paths = [
        os.path.join(SOUL_DIR, f"workspace-{client_name}", "SOUL.md"),
        os.path.join(SOUL_DIR, f"workspace-{client_name}", "soul.md"),
    ]
    for p in paths:
        try:
            with open(p) as f:
                return f.read()
        except FileNotFoundError:
            continue
    return ""


def load_guide(path):
    """Load a styleguide file."""
    try:
        with open(path) as f:
            return f.read()
    except FileNotFoundError:
        return ""


# ── YouTube / video frame helpers ────────────────────────────────────

CACHE_BASE = "/tmp/bgraphic-cache"


def is_youtube_url(s):
    """Check if a string looks like a YouTube URL."""
    return bool(re.search(r'(youtube\.com/watch|youtu\.be/)', s))


def extract_video_id(url):
    """Extract video ID from a YouTube URL."""
    m = re.search(r'[?&]v=([A-Za-z0-9_-]{11})', url)
    if m:
        return m.group(1)
    m = re.search(r'youtu\.be/([A-Za-z0-9_-]{11})', url)
    if m:
        return m.group(1)
    m = re.search(r'youtube\.com/(?:embed|v)/([A-Za-z0-9_-]{11})', url)
    if m:
        return m.group(1)
    return url


def get_cache_dir(video_id):
    """Return cache directory path for a video ID."""
    return os.path.join(CACHE_BASE, video_id)


def download_video(youtube_url, max_height=480):
    """Download YouTube video to cache. Returns path to video file, or None on failure."""
    video_id = extract_video_id(youtube_url)
    cache_dir = get_cache_dir(video_id)
    video_path = os.path.join(cache_dir, "video.mp4")

    if os.path.exists(video_path):
        return video_path

    os.makedirs(cache_dir, exist_ok=True)
    try:
        result = subprocess.run(
            [
                "yt-dlp",
                "-f", f"bestvideo[height<={max_height}]+bestaudio/best[height<={max_height}]",
                "--merge-output-format", "mp4",
                "-o", video_path,
                youtube_url,
            ],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0 or not os.path.exists(video_path):
            return None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    return video_path


def extract_frames(video_path, frames_dir, target_count=20):
    """Extract frames at regular intervals. Returns list of frame file paths."""
    os.makedirs(frames_dir, exist_ok=True)

    # Get video duration via ffprobe
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, timeout=30,
        )
        duration = float(result.stdout.strip())
    except (subprocess.TimeoutExpired, ValueError, FileNotFoundError):
        duration = 60.0  # fallback guess

    interval = max(duration / target_count, 1.0)

    try:
        subprocess.run(
            ["ffmpeg", "-i", video_path, "-vf", f"fps=1/{interval}",
             "-q:v", "2", os.path.join(frames_dir, "frame-%03d.jpg")],
            capture_output=True, text=True, timeout=120,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    frames = sorted(
        [os.path.join(frames_dir, f) for f in os.listdir(frames_dir) if f.endswith(".jpg")]
    )
    return frames


def load_founder_photos(photo_dir, date_prefix=None, max_photos=8):
    """Load founder photos from a directory, optionally filtering by date prefix.
    Returns list of file paths, capped at max_photos."""
    photo_dir = os.path.expanduser(photo_dir)
    if not os.path.isdir(photo_dir):
        return []

    photos = []
    for f in sorted(os.listdir(photo_dir)):
        if not f.lower().endswith((".jpg", ".jpeg", ".png")):
            continue
        if date_prefix and date_prefix not in f:
            continue
        photos.append(os.path.join(photo_dir, f))

    return photos[:max_photos]


# ── Content extraction via Gemini ─────────────────────────────────────

def validate_content_item(item):
    """Validate a content extraction item has required fields."""
    required = ["type", "generation_path", "headline_text", "image_prompt", "caption"]
    if not all(k in item for k in required):
        return False
    if item["generation_path"] not in ("founder_photo", "illustrated_scene"):
        return False
    if item["generation_path"] == "illustrated_scene":
        if not all(k in item for k in ("subject", "object", "metaphor_reason")):
            return False
    return True


def build_founder_photo_prompt(item):
    """Build the style transfer prompt for a founder photo graphic.
    Uses headline_text as source of truth for the headline."""
    headline = item.get("headline_text", "")
    return (
        f"Transform this photo into a risograph/screen-print style social media graphic. "
        f"Apply halftone dot pattern texture. Limit to 2-3 colors from this palette: "
        f"deep royal blue (#1a2b8a), golden yellow (#f5c518), hot pink/magenta (#d4467a). "
        f"Add bold condensed all-caps stacked headline: \"{headline}\" "
        f"in golden yellow (#f5c518), left-aligned, taking up 40-60% of the frame. "
        f"Visible grain, thick outlines on the figure, slight color layer misregistration. "
        f"Square format 1:1."
    )


def build_illustrated_scene_prompt(item):
    """Build the generation prompt for an illustrated scene graphic.
    Uses headline_text as source of truth, subject/object from visual vocabulary."""
    headline = item.get("headline_text", "")
    subject = item.get("subject", "a character")
    obj = item.get("object", "a vintage object")
    base_prompt = item.get("image_prompt", "")

    # If the LLM's image_prompt is detailed and includes our keywords, use it
    # but enforce headline_text as authoritative
    if "risograph" in base_prompt.lower() and subject.lower() in base_prompt.lower():
        prompt = f'{base_prompt}\nThe headline text MUST be exactly: "{headline}"'
    else:
        # Build from scratch using structured fields
        prompt = (
            f"Risograph style illustration of {subject} interacting with {obj}. "
            f"Deep royal blue (#1a2b8a) background. Subject in hot pink (#d4467a) and "
            f"orange (#e8732a). Bold condensed stacked all-caps headline: \"{headline}\" "
            f"in golden yellow (#f5c518), left-aligned, taking up 40-60% of the frame. "
            f"Halftone texture, thick outlines, visible grain, slight color misregistration, "
            f"screen print aesthetic, vintage print feel. Square format 1:1."
        )
    return prompt


def select_frames(content_items, frame_paths):
    """Use Gemini to pick the best video frame for each founder_photo content item.
    Returns dict mapping content_index -> frame_file_path."""
    # Filter to only items that need frames
    photo_items = [
        (i, item) for i, item in enumerate(content_items)
        if item.get("generation_path") == "founder_photo"
    ]
    if not photo_items or not frame_paths:
        return {}

    api_key = _get_api_key()
    gemini = genai.Client(api_key=api_key)

    # Build content descriptions for the LLM
    descriptions = []
    for idx, (i, item) in enumerate(photo_items):
        descriptions.append(
            f'{idx + 1}. "{item.get("headline_text", "")}" — {item.get("type", "graphic")}'
        )

    prompt_text = f"""Here are {len(frame_paths)} frames extracted from a video (numbered 1 to {len(frame_paths)}).
For each content piece below, pick the frame number where the speaker's expression
or gesture best matches the content's tone and energy.

Content pieces needing frames:
{chr(10).join(descriptions)}

Return ONLY a valid JSON array:
[{{"content_index": 0, "frame_number": 7, "reason": "brief reason"}}]
Use 0-based content_index matching the order above. frame_number is 1-based."""

    # Load frame images as PIL
    from PIL import Image
    contents = []
    for fp in frame_paths:
        try:
            contents.append(Image.open(fp))
        except Exception:
            continue
    contents.append(prompt_text)

    try:
        response = gemini.models.generate_content(
            model=TEXT_MODEL,
            contents=contents,
        )
        raw = response.text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()
        selections = json.loads(raw)
    except Exception:
        # Fallback: assign first frame to all
        return {i: frame_paths[0] for i, _ in photo_items}

    # Build mapping: original content_index -> frame path
    mapping = {}
    for sel in selections:
        photo_idx = sel.get("content_index", 0)
        frame_num = sel.get("frame_number", 1)
        if 0 <= photo_idx < len(photo_items):
            orig_idx = photo_items[photo_idx][0]
            frame_file_idx = max(0, min(frame_num - 1, len(frame_paths) - 1))
            mapping[orig_idx] = frame_paths[frame_file_idx]

    # Fill in any missing items with first frame
    for i, _ in photo_items:
        if i not in mapping:
            mapping[i] = frame_paths[0]

    return mapping


def extract_content(client, transcript, soul, writing_guide, design_guide, num_images,
                    has_photos=False):
    """Use Gemini to extract key content and generate image prompts + captions."""
    api_key = _get_api_key()
    gemini = genai.Client(api_key=api_key)

    if has_photos:
        photo_context = (
            "\nYou have access to founder photos. For content that is a direct quote or personal take, "
            "you MAY choose generation_path \"founder_photo\" — the photo will be processed into the "
            "brand's visual style with the headline overlaid. For conceptual content, stats, or "
            "general takeaways, prefer \"illustrated_scene\"."
        )
    else:
        photo_context = (
            "\nNo founder photos are available. Use generation_path \"illustrated_scene\" for ALL items."
        )

    system_prompt = f"""You are a social media content strategist. You extract the best content from
transcripts and create social media posts with branded graphics.

## Client Brand (from SOUL.md):
{soul[:6000] if soul else "No specific brand info — use clean, professional defaults."}

## Photo Availability
{photo_context}

## Writing Rules:
- No banned AI words (leverage, innovative, comprehensive, utilize, delve, etc.)
- Short sentences. Declarative tone. Specific examples.
- Sound human: contractions, sentence fragments, first person, slang where appropriate
- No "In this article..." or "Let's dive into..." or "Master the art of..."

## Design Rules:
- No trendy teal/purple/blue gradients
- No glass/neumorphism effects
- No bento grids or Inter/Geist/Satoshi fonts
- Bold typography. High contrast. Readable text.
- Square format (1:1 aspect ratio) for social media
"""

    user_prompt = f"""Extract the {num_images} best pieces of content from this transcript for social media posts.

For each piece, return a JSON object with these fields:

1. "type": "quote_card", "takeaway", "stat_callout", or "bold_statement"
2. "generation_path": "founder_photo" or "illustrated_scene"
3. "headline_text": The exact text for the image headline (max 8-10 words, punchy, all-caps style)
4. "image_prompt": Detailed prompt for generating the graphic, following the SOUL's visual identity.
   - MUST include the headline_text
   - MUST include the client's style keywords and color palette
   - MUST specify "Square format 1:1"
   For founder_photo: describe the risograph/halftone treatment to apply to the photo
   For illustrated_scene: describe the full illustrated scene
5. "caption": Ready-to-post social media caption (under 280 chars preferred, max 500)
   - Hook in the first line. No hashtags. Stands alone without the image.

For "illustrated_scene" items, also include:
6. "subject": The character/creature from the Visual Vocabulary (e.g., "retro tin robot")
7. "object": The prop/device from the Visual Vocabulary (e.g., "old-fashioned radio")
8. "metaphor_reason": One sentence explaining why this subject+object combo fits the content

Return ONLY a valid JSON array. No markdown, no explanation.

TRANSCRIPT:
{transcript[:15000]}"""

    response = gemini.models.generate_content(
        model=TEXT_MODEL,
        contents=[{"role": "user", "parts": [{"text": system_prompt + "\n\n" + user_prompt}]}],
    )

    raw = response.text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]
    raw = raw.strip()

    items = json.loads(raw)

    # Validate and fix items — force override if no photos available
    validated = []
    for item in items:
        if not has_photos and item.get("generation_path") == "founder_photo":
            item["generation_path"] = "illustrated_scene"
            # Add default visual vocabulary fields so validation passes
            item.setdefault("subject", "a vintage object")
            item.setdefault("object", "a retro device")
            item.setdefault("metaphor_reason", "Visual metaphor for the topic")
        if validate_content_item(item):
            validated.append(item)

    return validated


# ── Image generation ──────────────────────────────────────────────────

def generate_image(client, prompt, output_path, input_image_path=None):
    """Generate a single image via Gemini image model.
    If input_image_path is provided, sends it alongside the prompt for style transfer."""
    api_key = _get_api_key()
    gemini = genai.Client(api_key=api_key)

    if input_image_path:
        from PIL import Image
        input_img = Image.open(input_image_path)
        contents = [prompt, input_img]
    else:
        contents = [prompt]

    response = gemini.models.generate_content(
        model=IMAGE_MODEL,
        contents=contents,
    )

    for part in response.parts:
        if part.inline_data is not None:
            image = part.as_image()
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
            image.save(output_path)
            return True
    return False


# ── R2 upload ─────────────────────────────────────────────────────────

def upload_to_r2(local_path, remote_key):
    """Upload to R2 via social_upload.py."""
    result = subprocess.run(
        [
            "/opt/homebrew/opt/python@3.11/bin/python3.11",
            os.path.join(_DIR, "social_upload.py"),
            local_path,
            remote_key,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


# ── Main pipeline ─────────────────────────────────────────────────────

def run(transcript_source, client_name, channel, thread_ts, num_images=5):
    """Run the full bgraphic pipeline.
    transcript_source can be a file path or YouTube URL."""
    num_images = min(max(num_images, 2), 10)

    slack_post(channel, thread_ts,
               f"Generating {num_images} branded graphics. This takes 2-5 minutes...")

    # ── Determine input type and load transcript ──
    youtube_url = None
    if is_youtube_url(transcript_source):
        youtube_url = transcript_source
        video_id = extract_video_id(youtube_url)
        slack_post(channel, thread_ts, f"Fetching transcript from YouTube ({video_id})...")
        try:
            from social_youtube_transcript import fetch_transcript, extract_video_id as yt_extract_id
            transcript = fetch_transcript(yt_extract_id(youtube_url))
        except Exception as exc:
            slack_post(channel, thread_ts, f"Failed to fetch YouTube transcript: {exc}")
            return
    else:
        with open(transcript_source) as f:
            transcript = f.read()

    if not transcript.strip():
        slack_post(channel, thread_ts, "Transcript is empty — nothing to extract.")
        return

    # ── Load style context ──
    soul = load_soul(client_name)
    writing_guide = load_guide(WRITING_GUIDE)
    design_guide = load_guide(DESIGN_GUIDE)

    # ── Extract video frames (if YouTube) ──
    frame_paths = []
    if youtube_url:
        slack_post(channel, thread_ts, "Downloading video and extracting frames...")
        video_path = download_video(youtube_url)
        if video_path:
            video_id = extract_video_id(youtube_url)
            frames_dir = os.path.join(get_cache_dir(video_id), "frames")
            frame_paths = extract_frames(video_path, frames_dir)
            if frame_paths:
                slack_post(channel, thread_ts, f"Extracted {len(frame_paths)} frames from video.")
            else:
                slack_post(channel, thread_ts, "Frame extraction failed — using illustrated scenes only.")
        else:
            slack_post(channel, thread_ts, "Video download failed — using illustrated scenes only.")

    # ── Load fallback founder photos if no video frames ──
    founder_photos = []
    if not frame_paths:
        founder_photos = load_founder_photos(
            "~/Pictures/Photo Booth Library/Pictures/",
            date_prefix="2026-02",
        )

    has_photos = bool(frame_paths or founder_photos)

    # ── Extract content ──
    slack_post(channel, thread_ts, "Reading transcript and extracting key content...")
    try:
        content_items = extract_content(
            client_name, transcript, soul, writing_guide, design_guide,
            num_images, has_photos=has_photos,
        )
    except Exception as exc:
        slack_post(channel, thread_ts, f"Failed to extract content: {exc}")
        return

    if not content_items:
        slack_post(channel, thread_ts, "Couldn't find usable content in the transcript.")
        return

    # ── Select frames for founder_photo items ──
    frame_mapping = {}
    photo_source = frame_paths or founder_photos
    if photo_source:
        any_founder = any(
            item.get("generation_path") == "founder_photo" for item in content_items
        )
        if any_founder:
            slack_post(channel, thread_ts, "Selecting best frames for founder photo graphics...")
            frame_mapping = select_frames(content_items, photo_source)

    # ── Generate images, upload, and post ──
    datestamp = datetime.now().strftime("%Y/%m/%d")
    tmpdir = tempfile.mkdtemp(prefix="bgraphic-")
    succeeded = 0
    r2_urls = []

    for i, item in enumerate(content_items):
        num = i + 1
        img_type = item.get("type", "graphic")
        gen_path = item.get("generation_path", "illustrated_scene")
        caption = item.get("caption", "")

        # Build prompt and determine input image
        input_image_path = None
        if gen_path == "founder_photo" and i in frame_mapping:
            prompt = build_founder_photo_prompt(item)
            input_image_path = frame_mapping[i]
        else:
            prompt = build_illustrated_scene_prompt(item)

        if not prompt:
            continue

        # Generate image
        filename = f"bgraphic-{num}-{img_type}.png"
        local_path = os.path.join(tmpdir, filename)

        try:
            ok = generate_image(client_name, prompt, local_path,
                                input_image_path=input_image_path)
        except Exception as exc:
            slack_post(channel, thread_ts, f"Image {num} failed: {exc}")
            # If founder_photo failed, retry as illustrated_scene
            if gen_path == "founder_photo":
                try:
                    prompt = build_illustrated_scene_prompt(item)
                    ok = generate_image(client_name, prompt, local_path)
                except Exception:
                    continue
            else:
                continue

        if not ok:
            # If founder_photo returned no image, retry as illustrated_scene
            if gen_path == "founder_photo":
                try:
                    prompt = build_illustrated_scene_prompt(item)
                    ok = generate_image(client_name, prompt, local_path)
                except Exception:
                    pass
            if not ok:
                slack_post(channel, thread_ts, f"Image {num} — no image returned, skipping.")
                continue

        # Upload to R2
        remote_key = f"social-posts/{datestamp}/{filename}"
        r2_url = upload_to_r2(local_path, remote_key)
        if not r2_url:
            slack_post(channel, thread_ts, f"Image {num} — R2 upload failed, skipping.")
            continue

        r2_urls.append({"num": num, "url": r2_url, "caption": caption, "type": img_type,
                        "generation_path": gen_path})

        # Post image + caption to Slack
        path_label = "Photo" if gen_path == "founder_photo" else "Illustrated"
        comment = f"*{num}.* _{img_type.replace('_', ' ').title()}_ ({path_label})\n\n{caption}"
        slack_upload_image(channel, thread_ts, local_path,
                           title=f"bgraphic-{num}", comment=comment)
        succeeded += 1

        if i < len(content_items) - 1:
            time.sleep(2)

    # ── Final summary ──
    if succeeded == 0:
        slack_post(channel, thread_ts,
                   "All image generations failed. Try again or tag @trevor.")
    else:
        lines = [f"Generated *{succeeded}/{len(content_items)}* graphics.\n"]
        for item in r2_urls:
            lines.append(
                f"*{item['num']}.* _{item['type'].replace('_', ' ').title()}_"
                f" ({item['generation_path'].replace('_', ' ').title()})\n"
                f"Caption: {item['caption']}\n"
                f"Image URL: {item['url']}\n"
            )
        lines.append(
            "Want me to `/bpost` any of these? "
            "Tell me which numbers, where to post, and when.\n"
            "Example: _\"Post 1 and 3 to LinkedIn tomorrow at 9am ET\"_"
        )
        slack_post(channel, thread_ts, "\n".join(lines))

    print(json.dumps(r2_urls, indent=2))


if __name__ == "__main__":
    if len(sys.argv) < 5:
        print(
            "Usage: python3 social_bgraphic.py TRANSCRIPT_SOURCE CLIENT_NAME "
            "CHANNEL_ID THREAD_TS [NUM_IMAGES]",
            file=sys.stderr,
        )
        sys.exit(1)

    num = int(sys.argv[5]) if len(sys.argv) > 5 else 5
    run(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], num)
