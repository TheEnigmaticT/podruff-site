#!/usr/bin/env python3
"""Fetch a YouTube video transcript and print it as plain text.

Usage: python3 social_youtube_transcript.py VIDEO_URL_OR_ID

Accepts:
  - Full URL: https://www.youtube.com/watch?v=dQw4w9WgXcQ
  - Short URL: https://youtu.be/dQw4w9WgXcQ
  - Just the ID: dQw4w9WgXcQ
"""

import re
import sys

from youtube_transcript_api import YouTubeTranscriptApi


def extract_video_id(url_or_id):
    """Extract video ID from a YouTube URL or return the ID as-is."""
    # Already just an ID
    if re.match(r'^[A-Za-z0-9_-]{11}$', url_or_id):
        return url_or_id

    # youtube.com/watch?v=ID
    m = re.search(r'[?&]v=([A-Za-z0-9_-]{11})', url_or_id)
    if m:
        return m.group(1)

    # youtu.be/ID
    m = re.search(r'youtu\.be/([A-Za-z0-9_-]{11})', url_or_id)
    if m:
        return m.group(1)

    # youtube.com/embed/ID or /v/ID
    m = re.search(r'youtube\.com/(?:embed|v)/([A-Za-z0-9_-]{11})', url_or_id)
    if m:
        return m.group(1)

    return url_or_id


def fetch_transcript(video_id):
    """Fetch transcript and return as plain text string."""
    api = YouTubeTranscriptApi()
    transcript = api.fetch(video_id)
    lines = [snippet.text for snippet in transcript]
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 social_youtube_transcript.py VIDEO_URL_OR_ID", file=sys.stderr)
        sys.exit(1)

    video_id = extract_video_id(sys.argv[1])
    try:
        text = fetch_transcript(video_id)
        print(text)
    except Exception as exc:
        print(f"ERROR: Could not fetch transcript for {video_id}: {exc}", file=sys.stderr)
        sys.exit(1)
