"""Tests for YouTube URL detection, video frame extraction, and photo loading."""

import os
import unittest
from unittest.mock import MagicMock, patch

import pytest

# Add project root to path so we can import social_bgraphic
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from social_bgraphic import (
    CACHE_BASE,
    download_video,
    extract_frames,
    extract_video_id,
    get_cache_dir,
    is_youtube_url,
    load_founder_photos,
)


# ── Task 2: YouTube URL Detection + Video ID Extraction ──────────────


class TestIsYoutubeUrl:
    def test_is_youtube_url_full(self):
        assert is_youtube_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ") is True

    def test_is_youtube_url_short(self):
        assert is_youtube_url("https://youtu.be/dQw4w9WgXcQ") is True

    def test_is_youtube_url_file_path(self):
        assert is_youtube_url("/Users/me/photos/photo.jpg") is False


class TestExtractVideoId:
    def test_extract_video_id_full_url(self):
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        assert extract_video_id(url) == "dQw4w9WgXcQ"

    def test_extract_video_id_short_url(self):
        url = "https://youtu.be/dQw4w9WgXcQ"
        assert extract_video_id(url) == "dQw4w9WgXcQ"

    def test_extract_video_id_with_params(self):
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s&list=PL123"
        assert extract_video_id(url) == "dQw4w9WgXcQ"


class TestGetCacheDir:
    def test_cache_dir_path(self):
        result = get_cache_dir("dQw4w9WgXcQ")
        assert result == os.path.join(CACHE_BASE, "dQw4w9WgXcQ")


# ── Task 3: Video Download + Frame Extraction ─────────────────────────


class TestDownloadVideo:
    def test_download_video_uses_cache(self, tmp_path):
        """Returns cached path without calling yt-dlp when file already exists."""
        video_id = "dQw4w9WgXcQ"
        cache_dir = tmp_path / video_id
        cache_dir.mkdir()
        video_file = cache_dir / "video.mp4"
        video_file.write_bytes(b"fake video data")

        url = f"https://www.youtube.com/watch?v={video_id}"

        with patch("social_bgraphic.get_cache_dir", return_value=str(cache_dir)):
            with patch("social_bgraphic.subprocess.run") as mock_run:
                result = download_video(url)
                mock_run.assert_not_called()
                assert result == str(video_file)

    def test_download_video_calls_ytdlp(self, tmp_path):
        """Calls yt-dlp when no cached file exists."""
        video_id = "dQw4w9WgXcQ"
        cache_dir = tmp_path / video_id
        url = f"https://www.youtube.com/watch?v={video_id}"

        expected_video_path = str(cache_dir / "video.mp4")

        mock_result = MagicMock()
        mock_result.returncode = 0

        # Track calls so that first exists() call (cache check) returns False,
        # and subsequent call (after download) returns True.
        exists_calls = []

        def fake_exists(p):
            exists_calls.append(p)
            # First call is the pre-download cache check — return False
            if len(exists_calls) == 1:
                return False
            # Subsequent calls (post-download verification) — return True
            return p == expected_video_path

        with patch("social_bgraphic.get_cache_dir", return_value=str(cache_dir)):
            with patch("social_bgraphic.subprocess.run", return_value=mock_result) as mock_run:
                with patch("social_bgraphic.os.path.exists", side_effect=fake_exists):
                    result = download_video(url)

                mock_run.assert_called_once()
                call_args = mock_run.call_args[0][0]
                assert "yt-dlp" in call_args
                assert url in call_args
                assert result == expected_video_path


class TestExtractFrames:
    def test_extract_frames_creates_jpgs(self, tmp_path):
        """Mocks ffprobe+ffmpeg and returns frame paths."""
        video_path = str(tmp_path / "video.mp4")
        frames_dir = str(tmp_path / "frames")

        # Create fake frame files as if ffmpeg produced them
        os.makedirs(frames_dir, exist_ok=True)
        for i in range(1, 4):
            open(os.path.join(frames_dir, f"frame-{i:03d}.jpg"), "w").close()

        ffprobe_result = MagicMock()
        ffprobe_result.stdout = "120.0\n"
        ffprobe_result.returncode = 0

        ffmpeg_result = MagicMock()
        ffmpeg_result.returncode = 0

        def fake_run(cmd, **kwargs):
            if cmd[0] == "ffprobe":
                return ffprobe_result
            return ffmpeg_result

        with patch("social_bgraphic.subprocess.run", side_effect=fake_run):
            frames = extract_frames(video_path, frames_dir, target_count=20)

        assert len(frames) == 3
        assert all(f.endswith(".jpg") for f in frames)
        assert frames == sorted(frames)


# ── Task 4: Photo Booth Fallback Photo Loading ───────────────────────


class TestLoadFounderPhotos:
    def test_load_founder_photos_filters_by_date(self, tmp_path):
        """Returns only photos matching the date prefix."""
        (tmp_path / "2026-03-10 photo1.jpg").write_bytes(b"")
        (tmp_path / "2026-03-10 photo2.jpg").write_bytes(b"")
        (tmp_path / "2026-03-09 photo3.jpg").write_bytes(b"")
        (tmp_path / "2026-03-09 photo4.png").write_bytes(b"")

        result = load_founder_photos(str(tmp_path), date_prefix="2026-03-10")
        assert len(result) == 2
        assert all("2026-03-10" in p for p in result)

    def test_load_founder_photos_returns_all_jpgs_if_no_filter(self, tmp_path):
        """Returns all image files when no date filter is given."""
        (tmp_path / "photo1.jpg").write_bytes(b"")
        (tmp_path / "photo2.jpeg").write_bytes(b"")
        (tmp_path / "photo3.png").write_bytes(b"")
        (tmp_path / "notes.txt").write_bytes(b"")

        result = load_founder_photos(str(tmp_path))
        assert len(result) == 3
        assert all(p.endswith((".jpg", ".jpeg", ".png")) for p in result)

    def test_load_founder_photos_caps_at_8(self, tmp_path):
        """Returns at most max_photos (default 8) photos."""
        for i in range(12):
            (tmp_path / f"photo{i:02d}.jpg").write_bytes(b"")

        result = load_founder_photos(str(tmp_path))
        assert len(result) == 8


# ── Task 5: Content Validation ────────────────────────────────────────


class TestValidateContentItem:
    def test_valid_illustrated_scene(self):
        from social_bgraphic import validate_content_item
        item = {
            "type": "bold_statement",
            "generation_path": "illustrated_scene",
            "headline_text": "AI DOES BUSY WORK",
            "subject": "retro tin robot",
            "object": "desk with sticky notes",
            "metaphor_reason": "robot = AI doing busywork",
            "image_prompt": "Risograph style...",
            "caption": "Stop wasting time on tasks AI can handle.",
        }
        assert validate_content_item(item) is True

    def test_valid_founder_photo(self):
        from social_bgraphic import validate_content_item
        item = {
            "type": "quote_card",
            "generation_path": "founder_photo",
            "headline_text": "YOUR FUNNEL IS BROKEN",
            "image_prompt": "Founder photo with risograph...",
            "caption": "Most founders have the same problem.",
        }
        assert validate_content_item(item) is True

    def test_invalid_missing_generation_path(self):
        from social_bgraphic import validate_content_item
        item = {
            "type": "quote_card",
            "image_prompt": "...",
            "caption": "...",
        }
        assert validate_content_item(item) is False

    def test_invalid_illustrated_missing_subject(self):
        from social_bgraphic import validate_content_item
        item = {
            "type": "bold_statement",
            "generation_path": "illustrated_scene",
            "headline_text": "TEST",
            "image_prompt": "...",
            "caption": "...",
        }
        assert validate_content_item(item) is False


# ── Task 6: Frame Selection ───────────────────────────────────────────


class TestSelectFrames:
    def test_select_frames_returns_mapping(self, tmp_path):
        from social_bgraphic import select_frames

        # Create real tiny JPEG files so PIL.Image.open works
        try:
            from PIL import Image as PILImage
            for i in range(1, 21):
                img = PILImage.new("RGB", (10, 10), color=(i * 10, 0, 0))
                img.save(str(tmp_path / f"frame-{i:03d}.jpg"))
            frame_paths = [str(tmp_path / f"frame-{i:03d}.jpg") for i in range(1, 21)]
        except ImportError:
            pytest.skip("PIL not available")

        content_items = [
            {"type": "quote_card", "generation_path": "founder_photo",
             "headline_text": "YOUR FUNNEL IS BROKEN", "caption": "..."},
            {"type": "bold_statement", "generation_path": "illustrated_scene",
             "headline_text": "STOP GUESSING", "caption": "..."},
        ]

        mock_response = MagicMock()
        mock_response.text = '[{"content_index": 0, "frame_number": 5, "reason": "good expression"}]'

        with patch("social_bgraphic._get_api_key", return_value="fake-key"), \
             patch("social_bgraphic.genai") as mock_genai:
            mock_client = MagicMock()
            mock_genai.Client.return_value = mock_client
            mock_client.models.generate_content.return_value = mock_response

            result = select_frames(content_items, frame_paths)

        assert result[0] == frame_paths[4]  # frame_number 5 = index 4
        assert 1 not in result  # illustrated_scene item not in mapping

    def test_select_frames_empty_when_no_founder_photos(self):
        from social_bgraphic import select_frames

        content_items = [
            {"type": "bold_statement", "generation_path": "illustrated_scene",
             "headline_text": "STOP", "caption": "..."},
        ]
        frame_paths = ["/tmp/frame-001.jpg"]

        result = select_frames(content_items, frame_paths)
        assert result == {}


# ── Task 7: generate_image with optional input photo ──────────────────


class TestGenerateImage:
    def test_generate_image_with_input_photo(self, tmp_path):
        """generate_image accepts an optional input_image_path for style transfer."""
        from social_bgraphic import generate_image
        from PIL import Image as PILImage

        output_path = str(tmp_path / "output.png")
        input_photo = str(tmp_path / "input.jpg")

        # Create a tiny valid JPEG
        img = PILImage.new("RGB", (100, 100), color="blue")
        img.save(input_photo)

        # Mock Gemini response
        mock_response = MagicMock()
        mock_part = MagicMock()
        mock_part.inline_data = True
        mock_part.as_image.return_value = PILImage.new("RGB", (1024, 1024), color="red")
        mock_response.parts = [mock_part]

        with patch("social_bgraphic._get_api_key", return_value="fake-key"), \
             patch("social_bgraphic.genai") as mock_genai:
            mock_client = MagicMock()
            mock_genai.Client.return_value = mock_client
            mock_client.models.generate_content.return_value = mock_response

            result = generate_image("crowdtamers", "Apply risograph...", output_path,
                                    input_image_path=input_photo)

        assert result is True
        assert os.path.exists(output_path)

    def test_generate_image_without_input_photo(self, tmp_path):
        """generate_image works without input photo (existing behavior)."""
        from social_bgraphic import generate_image

        output_path = str(tmp_path / "output.png")

        mock_response = MagicMock()
        mock_part = MagicMock()
        mock_part.inline_data = True
        mock_image = MagicMock()
        mock_part.as_image.return_value = mock_image
        mock_response.parts = [mock_part]

        with patch("social_bgraphic._get_api_key", return_value="fake-key"), \
             patch("social_bgraphic.genai") as mock_genai:
            mock_client = MagicMock()
            mock_genai.Client.return_value = mock_client
            mock_client.models.generate_content.return_value = mock_response

            result = generate_image("crowdtamers", "Risograph illustration...", output_path)

        assert result is True
        # Verify contents was just a string (no image)
        call_args = mock_client.models.generate_content.call_args
        contents = call_args[1].get("contents", call_args[0][0] if call_args[0] else None)
        assert isinstance(contents, list)
        assert len(contents) == 1  # just the prompt string


# ── Task 8: Prompt template builders ─────────────────────────────────


class TestPromptBuilders:
    def test_build_founder_photo_prompt(self):
        from social_bgraphic import build_founder_photo_prompt
        item = {
            "headline_text": "YOUR FUNNEL IS BROKEN",
            "image_prompt": "Some LLM-generated prompt",
        }
        prompt = build_founder_photo_prompt(item)
        assert "YOUR FUNNEL IS BROKEN" in prompt
        assert "risograph" in prompt.lower()
        assert "1:1" in prompt
        assert "#1a2b8a" in prompt

    def test_build_illustrated_scene_prompt_from_scratch(self):
        from social_bgraphic import build_illustrated_scene_prompt
        item = {
            "headline_text": "AI DOES BUSY WORK",
            "subject": "retro tin robot",
            "object": "desk with sticky notes",
            "image_prompt": "Generic prompt without keywords",
        }
        prompt = build_illustrated_scene_prompt(item)
        assert "AI DOES BUSY WORK" in prompt
        assert "retro tin robot" in prompt
        assert "desk with sticky notes" in prompt
        assert "risograph" in prompt.lower()
        assert "1:1" in prompt

    def test_build_illustrated_scene_prompt_uses_llm_prompt_when_detailed(self):
        from social_bgraphic import build_illustrated_scene_prompt
        item = {
            "headline_text": "STOP GUESSING",
            "subject": "owl",
            "object": "crystal ball",
            "image_prompt": "Risograph style illustration of an owl peering into a crystal ball on a wooden table, deep blue background",
        }
        prompt = build_illustrated_scene_prompt(item)
        # Should use the LLM's prompt but enforce headline
        assert "owl peering into a crystal ball" in prompt
        assert 'MUST be exactly: "STOP GUESSING"' in prompt
