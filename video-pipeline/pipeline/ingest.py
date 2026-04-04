import json
import os
import shutil
import subprocess
from pipeline.retry import retry


def extract_video_info(url: str) -> dict:
    """Extract metadata from a video URL using yt-dlp."""
    result = subprocess.run(
        ["yt-dlp", "--dump-json", "--no-download", url],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp info extraction failed: {result.stderr}")
    return json.loads(result.stdout)


@retry(max_attempts=2, exceptions=(RuntimeError,))
def download_video(url: str, output_dir: str) -> str:
    """Download video to output_dir, or copy if local file. Returns path."""
    os.makedirs(output_dir, exist_ok=True)

    # Local file - just copy it
    if os.path.isfile(url):
        dest = os.path.join(output_dir, os.path.basename(url))
        shutil.copy2(url, dest)
        return dest

    # URL - use yt-dlp
    output_template = os.path.join(output_dir, "%(id)s.%(ext)s")
    result = subprocess.run(
        [
            "yt-dlp",
            "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "-o", output_template,
            "--merge-output-format", "mp4",
            url,
        ],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp download failed: {result.stderr}")
    for f in os.listdir(output_dir):
        if f.endswith(".mp4"):
            return os.path.join(output_dir, f)
    raise RuntimeError("Download completed but no mp4 file found")
