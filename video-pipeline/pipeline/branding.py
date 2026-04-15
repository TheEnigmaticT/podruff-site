"""Client branding: subtitle styling, end card generation, and branded render."""

import logging
import os
import re
import subprocess
import tempfile

logger = logging.getLogger(__name__)

FFMPEG = "/opt/homebrew/bin/ffmpeg"

_OPENCLAW_DIR = os.path.expanduser("~/.openclaw")

# Defaults applied when SOUL.md fields are missing
_DEFAULTS = {
    "primary_color": "#FFFFFF",
    "subtitle_font": "Inter",
    "subtitle_size": 120,
    "highlight_color": None,  # Falls back to primary_color
    "logo_position": "bottom-right",
    "logo_opacity": 0.8,
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _run_ffmpeg(args: list[str]) -> None:
    """Run ffmpeg with full error handling (last 500 chars of stderr on failure)."""
    cmd = [FFMPEG, "-y"] + args
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr[-500:]}")


# ---------------------------------------------------------------------------
# load_branding (legacy helper — reads SOUL.md via openclaw workspace path)
# ---------------------------------------------------------------------------

def load_branding(client_name: str) -> dict:
    """Load branding config from a client's SOUL.md.

    Parses the ## Branding section for key-value pairs like:
        - **primary_color:** #0119FF

    Returns a dict with all _DEFAULTS filled in.
    """
    soul_paths = [
        os.path.join(_OPENCLAW_DIR, f"workspace-{client_name}", "SOUL.md"),
        os.path.join(_OPENCLAW_DIR, f"workspace-{client_name}", "soul.md"),
    ]

    soul_text = None
    for p in soul_paths:
        try:
            with open(p) as f:
                soul_text = f.read()
            break
        except FileNotFoundError:
            continue

    config = dict(_DEFAULTS)

    if soul_text:
        branding_match = re.search(
            r"^## Branding\s*\n(.*?)(?=^## |\Z)",
            soul_text,
            re.MULTILINE | re.DOTALL,
        )
        if branding_match:
            section = branding_match.group(1)
            for match in re.finditer(
                r"^\s*-\s+\*\*(\w+):\*\*\s*(.+)$", section, re.MULTILINE
            ):
                key = match.group(1).strip()
                value = match.group(2).strip()
                if value.lower() == "tbd":
                    continue
                try:
                    if "." in value:
                        value = float(value)
                    elif value.isdigit():
                        value = int(value)
                except (ValueError, AttributeError):
                    pass
                config[key] = value

    if config.get("highlight_color") is None:
        config["highlight_color"] = config["primary_color"]

    return config


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_subtitle_style(soul: dict) -> dict:
    """Return subtitle styling config derived from a soul dict.

    Args:
        soul: Dict returned by ``client_config.load_soul`` (or any dict with
              optional ``subtitle_highlight`` and ``subtitle_font`` keys).

    Returns:
        {
            "highlight_color": soul.get("subtitle_highlight") or "#1FC2F9",
            "font": soul.get("subtitle_font") or "Inter",
            "font_size": 120,
        }
    """
    return {
        "highlight_color": soul.get("subtitle_highlight") or "#1FC2F9",
        "font": soul.get("subtitle_font") or "Inter",
        "font_size": 120,
    }


def generate_end_card(
    logo_path: str,
    cta_text: str,
    output_path: str,
    duration: int = 2,
    fps: int = 30,
) -> None:
    """Generate a 2-second 1080x1920 white end-card video with logo and CTA.

    The logo is scaled to 800 px wide and centered horizontally, placed
    slightly above vertical center.  CTA text is rendered below the logo
    using a drawtext filter.

    Args:
        logo_path:   Path to the client logo image (PNG/JPG).
        cta_text:    Call-to-action text, e.g. "Visit JonathanBrill.com for more".
        output_path: Destination .mp4 path.
        duration:    Clip length in seconds (default 2).
        fps:         Frame rate (default 30).
    """
    logger.info("Generating end card: %s", output_path)
    filter_complex = (
        "[1:v]scale=800:-1[logo];"
        "[0:v][logo]overlay=(W-w)/2:(H-h)/2-100[bg];"
        f"[bg]drawtext=text='{cta_text}'"
        ":fontfile=/System/Library/Fonts/Helvetica.ttc"
        ":fontsize=48"
        ":fontcolor=0x333333"
        ":x=(w-text_w)/2"
        ":y=(h/2)+200"
    )
    _run_ffmpeg([
        "-f", "lavfi",
        "-i", f"color=c=white:s=1080x1920:d={duration}:r={fps}",
        "-i", logo_path,
        "-filter_complex", filter_complex,
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-r", str(fps),
        "-t", str(duration),
        output_path,
    ])
    logger.info("End card written: %s", output_path)


def render_branded_short(
    draft_video: str,
    subtitle_path: str,
    end_card_path: str,
    output_path: str,
) -> None:
    """Full branded render: burn subtitles, append end card, write final output.

    Steps:
      1. Burn subtitles into draft video (ASS or SRT detected by extension).
      2. Add a silent audio track to the end card for concat compatibility.
      3. Concat main video + end card into the final output.
      4. Clean up temp files.

    Args:
        draft_video:   Path to the input short clip (no subtitles yet).
        subtitle_path: Path to .ass or .srt subtitle file.
        end_card_path: Path to end-card .mp4 generated by generate_end_card.
        output_path:   Destination path for the finished branded clip.
    """
    ext = os.path.splitext(subtitle_path)[1].lower()
    if ext == ".ass":
        escaped = subtitle_path.replace("\\", "\\\\").replace(":", "\\:")
        sub_filter = f"ass={escaped}"
    else:
        escaped = subtitle_path.replace("\\", "\\\\").replace(":", "\\:")
        sub_filter = f"subtitles={escaped}"

    subtitled = tempfile.mktemp(suffix="_subtitled.mp4")
    silent_end_card = tempfile.mktemp(suffix="_end_card_silent.mp4")
    concat_list = tempfile.mktemp(suffix="_concat.txt")

    try:
        # Step 1: burn subtitles
        logger.info("Burning subtitles (%s) into %s", ext, draft_video)
        _run_ffmpeg([
            "-i", draft_video,
            "-vf", sub_filter,
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
            subtitled,
        ])

        # Step 2: add silent audio to end card
        logger.info("Adding silent audio to end card")
        _run_ffmpeg([
            "-i", end_card_path,
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
            "-shortest",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            silent_end_card,
        ])

        # Step 3: concat
        logger.info("Concatenating main + end card -> %s", output_path)
        with open(concat_list, "w") as f:
            f.write(f"file '{subtitled}'\nfile '{silent_end_card}'\n")

        _run_ffmpeg([
            "-f", "concat", "-safe", "0",
            "-i", concat_list,
            "-c", "copy",
            output_path,
        ])

        logger.info("Branded short written: %s", output_path)
    finally:
        for tmp in (subtitled, silent_end_card, concat_list):
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
