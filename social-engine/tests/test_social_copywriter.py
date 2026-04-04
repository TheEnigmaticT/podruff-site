import json
from unittest.mock import patch, MagicMock


def test_build_copy_prompt_includes_transcript_and_platforms():
    from social_copywriter import build_copy_prompt

    clip_transcript = "Finance teams scramble every month before board meetings..."
    full_transcript = "Welcome to the show... [longer context] ...Finance teams scramble..."
    platforms = ["linkedin", "twitter", "instagram", "tiktok"]
    hook = "POV: You're working until 11 PM"
    title = "The 11 PM Fire Drill"

    prompt = build_copy_prompt(
        clip_transcript=clip_transcript,
        full_transcript=full_transcript,
        platforms=platforms,
        hook=hook,
        title=title,
    )

    assert "Finance teams scramble" in prompt
    assert "linkedin" in prompt
    assert "twitter" in prompt
    assert "tiktok" in prompt
    assert hook in prompt
    assert "280" in prompt


def test_load_prompt_context_assembles_files(tmp_path):
    from social_copywriter import load_prompt_context
    import social_copywriter as sc

    prompts_dir = tmp_path / "social_prompts"
    prompts_dir.mkdir()
    (prompts_dir / "copywriting.md").write_text("BANNED WORDS: leverage, synergy")
    (prompts_dir / "hooks.md").write_text("1. POV: You [scenario]")
    (prompts_dir / "platforms.md").write_text("LinkedIn: 300-500 words")
    clients_dir = prompts_dir / "clients"
    clients_dir.mkdir()
    (clients_dir / "acme.md").write_text("ACME voice: bold, direct")

    with patch.object(sc, "PROMPTS_DIR", str(prompts_dir)):
        result = load_prompt_context("acme")

    assert "BANNED WORDS" in result
    assert "POV" in result
    assert "LinkedIn" in result
    assert "ACME voice" in result


def test_load_prompt_context_works_without_client_file(tmp_path):
    from social_copywriter import load_prompt_context
    import social_copywriter as sc

    prompts_dir = tmp_path / "social_prompts"
    prompts_dir.mkdir()
    (prompts_dir / "copywriting.md").write_text("Style rules here")

    with patch.object(sc, "PROMPTS_DIR", str(prompts_dir)):
        result = load_prompt_context("nonexistent-client")

    assert "Style rules" in result


def test_parse_copy_response_extracts_platform_texts():
    from social_copywriter import parse_copy_response

    response = json.dumps({
        "linkedin": "Long LinkedIn post about finance teams...",
        "twitter": "Finance teams don't control their data. That's the real problem.",
        "instagram": "The 11 PM fire drill is real. Here's why...",
        "tiktok": "POV: 11 PM before board meeting",
    })

    result = parse_copy_response(response)
    assert result["linkedin"].startswith("Long LinkedIn")
    assert "tiktok" in result


def test_parse_srt_to_text_strips_formatting():
    from social_copywriter import parse_srt_to_text

    srt = "1\n00:00:03,500 --> 00:00:10,200\nExcited to work with team\n\n2\n00:00:10,200 --> 00:00:18,500\nSales orgs get excited\n"
    result = parse_srt_to_text(srt)
    assert result == "Excited to work with team Sales orgs get excited"


def test_parse_srt_to_text_handles_empty():
    from social_copywriter import parse_srt_to_text
    assert parse_srt_to_text("") == ""


def test_extract_clip_segment_finds_hook():
    from social_copywriter import extract_clip_segment

    full = "A" * 1000 + "The hook sentence is here" + "B" * 2000 + "C" * 1000
    result = extract_clip_segment(full, "The hook sentence is here", "")
    assert "The hook sentence is here" in result
    assert len(result) < len(full)


def test_extract_clip_segment_falls_back():
    from social_copywriter import extract_clip_segment

    full = "Some text that does not contain the hook at all" * 100
    result = extract_clip_segment(full, "missing hook", "")
    assert result == full[:3000]


def test_extract_drive_id_from_folder_url():
    from social_copywriter import extract_drive_id
    assert extract_drive_id("https://drive.google.com/drive/folders/1ABC123def") == "1ABC123def"


def test_extract_drive_id_from_file_url():
    from social_copywriter import extract_drive_id
    assert extract_drive_id("https://drive.google.com/file/d/1XYZ789/view") == "1XYZ789"


def test_extract_drive_id_bare_id():
    from social_copywriter import extract_drive_id
    assert extract_drive_id("1ABC123defGHI456") == "1ABC123defGHI456"


def test_extract_drive_id_none():
    from social_copywriter import extract_drive_id
    assert extract_drive_id("") is None
    assert extract_drive_id(None) is None


def test_parse_copy_response_handles_preamble():
    from social_copywriter import parse_copy_response
    response = 'Here is the JSON:\n\n{"linkedin": "Post text", "twitter": "Short"}\n\nHope this helps!'
    result = parse_copy_response(response)
    assert result["linkedin"] == "Post text"
    assert result["twitter"] == "Short"


def test_generate_social_copy_writes_to_notion():
    from social_copywriter import generate_social_copy

    row = {
        "id": "page-123",
        "Title": "The Fire Drill",
        "Platforms": ["linkedin", "twitter"],
        "Hook Sentence": "POV: You're working until 11 PM",
        "Post Text": "",
        "LinkedIn Text": "",
        "X Text": "",
        "Description": "Finance context for fallback",
        "Topic Name": "",
    }

    mock_notion = MagicMock()
    mock_notion.get_page.return_value = row

    config = {
        "anthropic_api_key": "sk-test",
        "google_creds_path": "",
    }

    mock_claude_response = json.dumps({
        "linkedin": "LinkedIn version of the post",
        "twitter": "Short X version",
    })

    with patch("social_copywriter.fetch_transcript", return_value=None), \
         patch("social_copywriter.load_prompt_context", return_value="System prompt context"), \
         patch("social_copywriter.call_claude", return_value=mock_claude_response):
        generate_social_copy("page-123", mock_notion, config)

    mock_notion.update_page.assert_called_once()
    props = mock_notion.update_page.call_args[0][1]
    assert "LinkedIn Text" in props
    assert "X Text" in props


def test_generate_social_copy_skips_existing_text():
    from social_copywriter import generate_social_copy

    row = {
        "id": "page-123",
        "Title": "Already Written",
        "Platforms": ["linkedin"],
        "Hook Sentence": "Hook",
        "Post Text": "",
        "LinkedIn Text": "Existing AM-edited text",
        "Description": "",
        "Topic Name": "",
    }

    mock_notion = MagicMock()
    mock_notion.get_page.return_value = row

    config = {"anthropic_api_key": "sk-test", "google_creds_path": ""}

    with patch("social_copywriter.call_claude") as mock_claude:
        generate_social_copy("page-123", mock_notion, config, force=False)

    mock_claude.assert_not_called()


def test_extract_clip_segment_uses_timestamp_tag():
    """extract_clip_segment should use [clip:start-end] tag when present."""
    from social_copywriter import extract_clip_segment

    full = "AAAA " * 200 + "TARGET SEGMENT HERE " * 50 + "BBBB " * 200
    result = extract_clip_segment(full, "", "", clip_start=1000, clip_end=1250)
    assert "TARGET SEGMENT" in result
    assert len(result) < len(full)


def test_extract_clip_segment_timestamp_takes_priority():
    """Timestamp tag should take priority over hook-based search."""
    from social_copywriter import extract_clip_segment

    full = "Hook is here at the start. " + "X" * 5000
    result = extract_clip_segment(full, "Hook is here", "", clip_start=3000, clip_end=4000)
    assert "Hook is here" not in result
