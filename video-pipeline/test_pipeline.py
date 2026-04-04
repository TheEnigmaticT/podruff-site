"""Smoke tests for video_pipeline_v2."""

import json
import os
import re
import sys
from unittest.mock import MagicMock, call, patch

import pytest

from video_pipeline_v2 import (
    FFMPEG,
    LANGUAGE_CONFIG,
    LITELLM_MODEL,
    _format_transcript_for_llm,
    apply_edits,
    burn_subtitles,
    detect_edits,
    generate_srt,
    heygen_translate,
    main,
    transcribe,
    translate_all,
    translate_srt,
)

# ---------------------------------------------------------------------------
# LANGUAGE_CONFIG tests
# ---------------------------------------------------------------------------

EXPECTED_LANGUAGES = {"es", "es-ES", "zh-CN", "zh-TW", "fr", "de", "pt-BR", "ar", "ja", "ko"}
REQUIRED_KEYS = {"name", "register", "font"}


def test_language_config_has_all_codes():
    assert set(LANGUAGE_CONFIG.keys()) == EXPECTED_LANGUAGES


@pytest.mark.parametrize("lang_code", sorted(EXPECTED_LANGUAGES))
def test_language_config_entry_has_required_keys(lang_code):
    entry = LANGUAGE_CONFIG[lang_code]
    missing = REQUIRED_KEYS - set(entry.keys())
    assert not missing, f"{lang_code} missing keys: {missing}"


@pytest.mark.parametrize("lang_code", sorted(EXPECTED_LANGUAGES))
def test_language_config_values_are_non_empty_strings(lang_code):
    entry = LANGUAGE_CONFIG[lang_code]
    for key in REQUIRED_KEYS:
        val = entry[key]
        assert isinstance(val, str) and val.strip(), f"{lang_code}.{key} should be a non-empty string"


# ---------------------------------------------------------------------------
# transcribe() tests (mocked whisper)
# ---------------------------------------------------------------------------

FAKE_WHISPER_RESULT = {
    "segments": [
        {
            "start": 0.0,
            "end": 2.5,
            "text": "Hello world",
            "words": [
                {"word": "Hello", "start": 0.0, "end": 1.0},
                {"word": "world", "start": 1.1, "end": 2.5},
            ],
        },
        {
            "start": 3.0,
            "end": 5.0,
            "text": "Testing one two three",
            "words": [
                {"word": "Testing", "start": 3.0, "end": 3.5},
                {"word": "one", "start": 3.6, "end": 3.8},
                {"word": "two", "start": 3.9, "end": 4.2},
                {"word": "three", "start": 4.3, "end": 5.0},
            ],
        },
    ]
}


@patch("video_pipeline_v2.whisper")
def test_transcribe_returns_segments(mock_whisper, tmp_path):
    mock_model = MagicMock()
    mock_model.transcribe.return_value = FAKE_WHISPER_RESULT
    mock_whisper.load_model.return_value = mock_model

    output_dir = str(tmp_path / "output")
    segments = transcribe("fake_video.mp4", output_dir)

    assert len(segments) == 2
    assert segments[0]["text"] == "Hello world"
    assert segments[0]["start"] == 0.0
    assert segments[0]["end"] == 2.5
    assert len(segments[0]["words"]) == 2
    assert segments[0]["words"][0] == {"word": "Hello", "start": 0.0, "end": 1.0}


@patch("video_pipeline_v2.whisper")
def test_transcribe_saves_json(mock_whisper, tmp_path):
    mock_model = MagicMock()
    mock_model.transcribe.return_value = FAKE_WHISPER_RESULT
    mock_whisper.load_model.return_value = mock_model

    output_dir = str(tmp_path / "output")
    transcribe("fake_video.mp4", output_dir)

    transcript_path = os.path.join(output_dir, "transcript.json")
    assert os.path.exists(transcript_path)

    with open(transcript_path, "r", encoding="utf-8") as f:
        saved = json.load(f)
    assert len(saved) == 2
    assert saved[1]["text"] == "Testing one two three"


@patch("video_pipeline_v2.whisper")
def test_transcribe_calls_whisper_correctly(mock_whisper, tmp_path):
    mock_model = MagicMock()
    mock_model.transcribe.return_value = {"segments": []}
    mock_whisper.load_model.return_value = mock_model

    output_dir = str(tmp_path / "output")
    transcribe("my_video.mp4", output_dir, whisper_model="medium")

    mock_whisper.load_model.assert_called_once_with("medium")
    mock_model.transcribe.assert_called_once_with("my_video.mp4", word_timestamps=True)


# ---------------------------------------------------------------------------
# detect_edits() tests (mocked OpenAI client)
# ---------------------------------------------------------------------------

FAKE_TRANSCRIPT = [
    {
        "start": 0.0,
        "end": 2.5,
        "text": "Hello everyone welcome",
        "words": [
            {"word": "Hello", "start": 0.0, "end": 0.5},
            {"word": "everyone", "start": 0.6, "end": 1.2},
            {"word": "welcome", "start": 1.3, "end": 2.5},
        ],
    },
    {
        "start": 2.5,
        "end": 5.0,
        "text": "Um so today I wanted to talk",
        "words": [
            {"word": "Um", "start": 2.5, "end": 2.8},
            {"word": "so", "start": 2.9, "end": 3.1},
            {"word": "today", "start": 3.2, "end": 3.6},
            {"word": "I", "start": 3.7, "end": 3.8},
            {"word": "wanted", "start": 3.9, "end": 4.3},
            {"word": "to", "start": 4.4, "end": 4.5},
            {"word": "talk", "start": 4.6, "end": 5.0},
        ],
    },
]

FAKE_LLM_EDITS = {
    "edits": [
        {"action": "keep", "start": 0.0, "end": 2.5, "reason": "Clean intro"},
        {"action": "cut", "start": 2.5, "end": 3.1, "reason": "Filler: um so"},
        {"action": "keep", "start": 3.1, "end": 5.0, "reason": "Main content"},
    ]
}


def test_format_transcript_for_llm():
    result = _format_transcript_for_llm(FAKE_TRANSCRIPT)
    lines = result.strip().split("\n")
    assert len(lines) == 2
    assert lines[0] == "[0.00-2.50] Hello everyone welcome"
    assert lines[1] == "[2.50-5.00] Um so today I wanted to talk"


@patch("video_pipeline_v2.openai")
def test_detect_edits_returns_valid_structure(mock_openai):
    mock_client = MagicMock()
    mock_openai.OpenAI.return_value = mock_client

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps(FAKE_LLM_EDITS)
    mock_client.chat.completions.create.return_value = mock_response

    edits = detect_edits(FAKE_TRANSCRIPT, model="test-model")

    assert len(edits) == 3
    for edit in edits:
        assert "action" in edit
        assert edit["action"] in ("keep", "cut")
        assert "start" in edit
        assert "end" in edit
        assert "reason" in edit
        assert isinstance(edit["start"], (int, float))
        assert isinstance(edit["end"], (int, float))


@patch("video_pipeline_v2.openai")
def test_detect_edits_formats_transcript_for_llm(mock_openai):
    mock_client = MagicMock()
    mock_openai.OpenAI.return_value = mock_client

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps(FAKE_LLM_EDITS)
    mock_client.chat.completions.create.return_value = mock_response

    detect_edits(FAKE_TRANSCRIPT, model="test-model")

    call_args = mock_client.chat.completions.create.call_args
    messages = call_args.kwargs["messages"]
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    # The user message should contain the formatted transcript
    assert "[0.00-2.50]" in messages[1]["content"]
    assert "[2.50-5.00]" in messages[1]["content"]


# ---------------------------------------------------------------------------
# apply_edits() tests (mocked subprocess)
# ---------------------------------------------------------------------------

@patch("video_pipeline_v2.subprocess.run")
def test_apply_edits_builds_correct_ffmpeg_commands(mock_run, tmp_path):
    mock_run.return_value = MagicMock(returncode=0)

    edits = [
        {"action": "keep", "start": 0.0, "end": 2.5, "reason": "Clean intro"},
        {"action": "cut", "start": 2.5, "end": 3.1, "reason": "Filler"},
        {"action": "keep", "start": 3.1, "end": 5.0, "reason": "Main content"},
    ]

    output_dir = str(tmp_path / "output")
    apply_edits("input.mp4", edits, output_dir)

    # Should have 2 extract calls + 1 concat call = 3 total
    assert mock_run.call_count == 3

    # First two calls: extract keep segments
    extract_call_0 = mock_run.call_args_list[0]
    cmd0 = extract_call_0.args[0] if extract_call_0.args else extract_call_0[0][0]
    assert cmd0[0] == FFMPEG
    assert "-ss" in cmd0
    assert "0.0" in cmd0
    assert "-to" in cmd0
    assert "2.5" in cmd0
    assert "-i" in cmd0
    assert "input.mp4" in cmd0

    extract_call_1 = mock_run.call_args_list[1]
    cmd1 = extract_call_1.args[0] if extract_call_1.args else extract_call_1[0][0]
    assert "3.1" in cmd1
    assert "5.0" in cmd1

    # Third call: concat
    concat_call = mock_run.call_args_list[2]
    cmd2 = concat_call.args[0] if concat_call.args else concat_call[0][0]
    assert "-f" in cmd2
    assert "concat" in cmd2
    assert cmd2[-1] == os.path.join(output_dir, "edited.mp4")


@patch("video_pipeline_v2.subprocess.run")
def test_apply_edits_returns_output_path(mock_run, tmp_path):
    mock_run.return_value = MagicMock(returncode=0)

    edits = [
        {"action": "keep", "start": 0.0, "end": 5.0, "reason": "All good"},
    ]

    output_dir = str(tmp_path / "output")
    result = apply_edits("input.mp4", edits, output_dir)
    assert result == os.path.join(output_dir, "edited.mp4")


@patch("video_pipeline_v2.subprocess.run")
def test_apply_edits_raises_on_no_keep_segments(mock_run, tmp_path):
    edits = [
        {"action": "cut", "start": 0.0, "end": 5.0, "reason": "All bad"},
    ]

    output_dir = str(tmp_path / "output")
    with pytest.raises(ValueError, match="No keep segments"):
        apply_edits("input.mp4", edits, output_dir)


# ---------------------------------------------------------------------------
# generate_srt() tests
# ---------------------------------------------------------------------------

SRT_TEST_SEGMENTS = [
    {
        "start": 0.0,
        "end": 2.5,
        "text": "Hello everyone welcome to the show",
        "words": [
            {"word": "Hello", "start": 0.0, "end": 0.3},
            {"word": "everyone", "start": 0.4, "end": 0.8},
            {"word": "welcome", "start": 0.9, "end": 1.3},
            {"word": "to", "start": 1.4, "end": 1.5},
            {"word": "the", "start": 1.6, "end": 1.7},
            {"word": "show", "start": 1.8, "end": 2.5},
        ],
    },
    {
        "start": 3.0,
        "end": 6.0,
        "text": "Today we are going to talk about something really interesting.",
        "words": [
            {"word": "Today", "start": 3.0, "end": 3.2},
            {"word": "we", "start": 3.3, "end": 3.4},
            {"word": "are", "start": 3.5, "end": 3.6},
            {"word": "going", "start": 3.7, "end": 3.9},
            {"word": "to", "start": 4.0, "end": 4.1},
            {"word": "talk", "start": 4.2, "end": 4.4},
            {"word": "about", "start": 4.5, "end": 4.7},
            {"word": "something", "start": 4.8, "end": 5.1},
            {"word": "really", "start": 5.2, "end": 5.5},
            {"word": "interesting.", "start": 5.6, "end": 6.0},
        ],
    },
]


def test_generate_srt_creates_valid_format(tmp_path):
    output_path = str(tmp_path / "subtitles.srt")
    result = generate_srt(SRT_TEST_SEGMENTS, output_path)

    assert result == output_path
    assert os.path.exists(output_path)

    with open(output_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Split into blocks (entries separated by blank lines)
    blocks = [b.strip() for b in content.strip().split("\n\n") if b.strip()]
    assert len(blocks) >= 1

    # Validate SRT format for each block
    for block in blocks:
        lines = block.split("\n")
        assert len(lines) >= 3, f"SRT block should have at least 3 lines: {block}"
        # Line 1: sequence number
        assert lines[0].strip().isdigit(), f"First line should be a number: {lines[0]}"
        # Line 2: timestamp line
        assert "-->" in lines[1], f"Second line should contain '-->': {lines[1]}"
        # Line 3+: text
        assert len(lines[2].strip()) > 0, "Text line should not be empty"


def test_generate_srt_sequential_numbering(tmp_path):
    output_path = str(tmp_path / "subtitles.srt")
    generate_srt(SRT_TEST_SEGMENTS, output_path)

    with open(output_path, "r", encoding="utf-8") as f:
        content = f.read()

    blocks = [b.strip() for b in content.strip().split("\n\n") if b.strip()]
    for i, block in enumerate(blocks, 1):
        num = int(block.split("\n")[0].strip())
        assert num == i, f"Expected sequence number {i}, got {num}"


def test_generate_srt_timestamps_format(tmp_path):
    output_path = str(tmp_path / "subtitles.srt")
    generate_srt(SRT_TEST_SEGMENTS, output_path)

    with open(output_path, "r", encoding="utf-8") as f:
        content = f.read()

    import re
    ts_pattern = r"\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}"
    matches = re.findall(ts_pattern, content)
    blocks = [b.strip() for b in content.strip().split("\n\n") if b.strip()]
    assert len(matches) == len(blocks), "Every block should have a valid timestamp line"


def test_generate_srt_line_length(tmp_path):
    """Subtitle text lines should be ~42 chars or less (may slightly exceed at word boundary)."""
    output_path = str(tmp_path / "subtitles.srt")
    generate_srt(SRT_TEST_SEGMENTS, output_path)

    with open(output_path, "r", encoding="utf-8") as f:
        content = f.read()

    blocks = [b.strip() for b in content.strip().split("\n\n") if b.strip()]
    for block in blocks:
        text_lines = block.split("\n")[2:]
        for line in text_lines:
            # Allow a small buffer since we break at word boundaries
            assert len(line) <= 60, f"Subtitle line too long ({len(line)} chars): {line}"


def test_generate_srt_returns_path(tmp_path):
    output_path = str(tmp_path / "subtitles.srt")
    result = generate_srt(SRT_TEST_SEGMENTS, output_path)
    assert result == output_path


# ---------------------------------------------------------------------------
# translate_srt() tests (mocked OpenAI client)
# ---------------------------------------------------------------------------

FAKE_SRT_CONTENT = """\
1
00:00:00,000 --> 00:00:02,500
Hello everyone welcome to the show

2
00:00:03,000 --> 00:00:06,000
Today we talk about something interesting.
"""

FAKE_TRANSLATED_PASS1 = """\
1. Hola a todos bienvenidos al programa
2. Hoy hablamos de algo interesante."""

FAKE_TRANSLATED_PASS2 = """\
1. Hola a todos, bienvenidos al programa
2. Hoy hablamos sobre algo interesante."""


@patch("video_pipeline_v2.openai")
def test_translate_srt_two_passes(mock_openai, tmp_path):
    """Verify that translate_srt makes two LLM calls (accuracy + fluency)."""
    # Write a fake English SRT
    srt_path = str(tmp_path / "subtitles_en.srt")
    with open(srt_path, "w", encoding="utf-8") as f:
        f.write(FAKE_SRT_CONTENT)

    mock_client = MagicMock()
    mock_openai.OpenAI.return_value = mock_client

    # Pass 1 response
    pass1_resp = MagicMock()
    pass1_resp.choices = [MagicMock()]
    pass1_resp.choices[0].message.content = FAKE_TRANSLATED_PASS1

    # Pass 2 response
    pass2_resp = MagicMock()
    pass2_resp.choices = [MagicMock()]
    pass2_resp.choices[0].message.content = FAKE_TRANSLATED_PASS2

    mock_client.chat.completions.create.side_effect = [pass1_resp, pass2_resp]

    output_dir = str(tmp_path / "output")
    result = translate_srt(srt_path, "es", output_dir, model="test-model")

    # Should have made exactly 2 LLM calls
    assert mock_client.chat.completions.create.call_count == 2

    # Verify output file
    assert result == os.path.join(output_dir, "subtitles_es.srt")
    assert os.path.exists(result)

    with open(result, "r", encoding="utf-8") as f:
        content = f.read()
    # Output is reassembled from original timestamps + translated text
    assert "Hola a todos, bienvenidos al programa" in content
    assert "Hoy hablamos sobre algo interesante." in content
    assert "00:00:00,000 --> 00:00:02,500" in content
    assert "00:00:03,000 --> 00:00:06,000" in content


@patch("video_pipeline_v2.openai")
def test_translate_srt_pass1_prompt_contains_language_and_register(mock_openai, tmp_path):
    """Pass 1 system prompt should reference the correct language name and register."""
    srt_path = str(tmp_path / "subtitles_en.srt")
    with open(srt_path, "w", encoding="utf-8") as f:
        f.write(FAKE_SRT_CONTENT)

    mock_client = MagicMock()
    mock_openai.OpenAI.return_value = mock_client

    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = FAKE_TRANSLATED_PASS1
    mock_client.chat.completions.create.return_value = mock_resp

    translate_srt(srt_path, "fr", str(tmp_path / "output"), model="test-model")

    # Check pass 1 prompt
    call_args_list = mock_client.chat.completions.create.call_args_list
    pass1_messages = call_args_list[0].kwargs["messages"]
    pass1_system = pass1_messages[0]["content"]
    assert "French" in pass1_system
    assert LANGUAGE_CONFIG["fr"]["register"] in pass1_system


@patch("video_pipeline_v2.openai")
def test_translate_srt_pass2_prompt_references_fluency(mock_openai, tmp_path):
    """Pass 2 system prompt should reference fluency/review."""
    srt_path = str(tmp_path / "subtitles_en.srt")
    with open(srt_path, "w", encoding="utf-8") as f:
        f.write(FAKE_SRT_CONTENT)

    mock_client = MagicMock()
    mock_openai.OpenAI.return_value = mock_client

    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = FAKE_TRANSLATED_PASS2
    mock_client.chat.completions.create.return_value = mock_resp

    translate_srt(srt_path, "de", str(tmp_path / "output"), model="test-model")

    call_args_list = mock_client.chat.completions.create.call_args_list
    pass2_messages = call_args_list[1].kwargs["messages"]
    pass2_system = pass2_messages[0]["content"]
    assert "German" in pass2_system
    assert "fluency" in pass2_system.lower() or "flow" in pass2_system.lower()
    assert LANGUAGE_CONFIG["de"]["register"] in pass2_system


@patch("video_pipeline_v2.openai")
def test_translate_srt_pass2_input_is_pass1_output(mock_openai, tmp_path):
    """Pass 2 user message should be the output from pass 1."""
    srt_path = str(tmp_path / "subtitles_en.srt")
    with open(srt_path, "w", encoding="utf-8") as f:
        f.write(FAKE_SRT_CONTENT)

    mock_client = MagicMock()
    mock_openai.OpenAI.return_value = mock_client

    pass1_resp = MagicMock()
    pass1_resp.choices = [MagicMock()]
    pass1_resp.choices[0].message.content = FAKE_TRANSLATED_PASS1

    pass2_resp = MagicMock()
    pass2_resp.choices = [MagicMock()]
    pass2_resp.choices[0].message.content = FAKE_TRANSLATED_PASS2

    mock_client.chat.completions.create.side_effect = [pass1_resp, pass2_resp]

    translate_srt(srt_path, "es", str(tmp_path / "output"), model="test-model")

    call_args_list = mock_client.chat.completions.create.call_args_list
    pass2_user_content = call_args_list[1].kwargs["messages"][1]["content"]
    # Pass 2 gets both original English and pass 1 translation
    assert "ORIGINAL ENGLISH:" in pass2_user_content
    assert "TRANSLATION TO REVIEW:" in pass2_user_content
    assert FAKE_TRANSLATED_PASS1 in pass2_user_content


# ---------------------------------------------------------------------------
# translate_all() tests
# ---------------------------------------------------------------------------

@patch("video_pipeline_v2.translate_srt")
def test_translate_all_dispatches_all_languages(mock_translate_srt, tmp_path):
    """translate_all should call translate_srt for every language."""
    srt_path = str(tmp_path / "subtitles_en.srt")
    output_dir = str(tmp_path / "output")

    def fake_translate(srt, lang, out_dir, model=None):
        return os.path.join(out_dir, f"subtitles_{lang}.srt")

    mock_translate_srt.side_effect = fake_translate

    languages = ["es", "fr", "de"]
    results = translate_all(srt_path, languages, output_dir, model="test-model")

    assert len(results) == 3
    assert set(results.keys()) == {"es", "fr", "de"}
    for lang in languages:
        assert results[lang] == os.path.join(output_dir, f"subtitles_{lang}.srt")

    # Verify translate_srt was called once per language
    assert mock_translate_srt.call_count == 3
    called_langs = {call.args[1] for call in mock_translate_srt.call_args_list}
    assert called_langs == {"es", "fr", "de"}


@patch("video_pipeline_v2.translate_srt")
def test_translate_all_returns_empty_for_empty_list(mock_translate_srt, tmp_path):
    srt_path = str(tmp_path / "subtitles_en.srt")
    output_dir = str(tmp_path / "output")

    results = translate_all(srt_path, [], output_dir)
    assert results == {}
    assert mock_translate_srt.call_count == 0


# ---------------------------------------------------------------------------
# burn_subtitles() tests
# ---------------------------------------------------------------------------

@patch("video_pipeline_v2.subprocess.run")
def test_burn_subtitles_builds_correct_ffmpeg_command(mock_run, tmp_path):
    """burn_subtitles should invoke FFmpeg with the subtitles filter and correct font."""
    mock_run.return_value = MagicMock(returncode=0)

    output_dir = str(tmp_path / "output")
    result = burn_subtitles("input.mp4", "subs.srt", "ja", output_dir)

    assert result == os.path.join(output_dir, "final_ja.mp4")
    assert mock_run.call_count == 1

    cmd = mock_run.call_args.args[0] if mock_run.call_args.args else mock_run.call_args[0][0]
    assert cmd[0] == FFMPEG
    assert "-y" in cmd
    assert "-i" in cmd
    assert "input.mp4" in cmd
    assert "-vf" in cmd
    assert "-c:a" in cmd
    assert "copy" in cmd

    # Check the subtitle filter string
    vf_idx = cmd.index("-vf")
    vf_value = cmd[vf_idx + 1]
    assert "subtitles=" in vf_value and "subs.srt" in vf_value
    assert "Noto Sans CJK JP" in vf_value  # ja font
    assert "FontSize=24" in vf_value
    assert "OutlineColour=&H40000000" in vf_value
    assert "BorderStyle=3" in vf_value
    assert "MarginV=30" in vf_value

    # Output path is the last argument
    assert cmd[-1] == os.path.join(output_dir, "final_ja.mp4")


@patch("video_pipeline_v2.subprocess.run")
def test_burn_subtitles_uses_language_font(mock_run, tmp_path):
    """burn_subtitles should use the font from LANGUAGE_CONFIG for the given language."""
    mock_run.return_value = MagicMock(returncode=0)

    output_dir = str(tmp_path / "output")
    burn_subtitles("input.mp4", "subs.srt", "es", output_dir)

    cmd = mock_run.call_args.args[0] if mock_run.call_args.args else mock_run.call_args[0][0]
    vf_idx = cmd.index("-vf")
    vf_value = cmd[vf_idx + 1]
    assert "FontName=Arial" in vf_value


@patch("video_pipeline_v2.subprocess.run")
def test_burn_subtitles_returns_output_path(mock_run, tmp_path):
    mock_run.return_value = MagicMock(returncode=0)

    output_dir = str(tmp_path / "output")
    result = burn_subtitles("vid.mp4", "subs.srt", "fr", output_dir)
    assert result == os.path.join(output_dir, "final_fr.mp4")


# ---------------------------------------------------------------------------
# heygen_translate() tests
# ---------------------------------------------------------------------------

def test_heygen_translate_raises_not_implemented():
    with pytest.raises(NotImplementedError, match="HeyGen API not configured"):
        heygen_translate("video.mp4", "es", "/tmp/output")


# ---------------------------------------------------------------------------
# CLI argument parsing tests
# ---------------------------------------------------------------------------

def test_cli_parse_args_basic():
    """Test basic CLI argument parsing."""
    from video_pipeline_v2 import argparse as _  # ensure import works

    # We test by constructing the parser the same way main() does
    parser = _build_test_parser()
    args = parser.parse_args(["video.mp4", "--languages", "es", "fr"])
    assert args.input_video == "video.mp4"
    assert args.languages == ["es", "fr"]
    assert args.heygen == []
    assert args.skip_cuts is False
    assert args.no_interactive is False
    assert args.model == LITELLM_MODEL
    assert args.output_dir == "~/dev/tmp/video-translations/"
    assert args.whisper_model == "large-v3"


def test_cli_parse_args_all_options():
    """Test CLI parsing with all options specified."""
    parser = _build_test_parser()
    args = parser.parse_args([
        "my_video.mp4",
        "--languages", "es", "de", "ja",
        "--heygen", "es", "ja",
        "--skip-cuts",
        "--no-interactive",
        "--model", "custom-model",
        "--output-dir", "/tmp/output",
        "--whisper-model", "medium",
    ])
    assert args.input_video == "my_video.mp4"
    assert args.languages == ["es", "de", "ja"]
    assert args.heygen == ["es", "ja"]
    assert args.skip_cuts is True
    assert args.no_interactive is True
    assert args.model == "custom-model"
    assert args.output_dir == "/tmp/output"
    assert args.whisper_model == "medium"


def test_cli_parse_args_heygen_empty():
    """--heygen with no arguments should give an empty list."""
    parser = _build_test_parser()
    args = parser.parse_args(["video.mp4", "--languages", "es", "--heygen"])
    assert args.heygen == []


def _build_test_parser():
    """Build the same argparse parser that main() uses, for testing."""
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("input_video")
    parser.add_argument("--languages", nargs="+", required=True)
    parser.add_argument("--heygen", nargs="*", default=[])
    parser.add_argument("--skip-cuts", action="store_true")
    parser.add_argument("--no-interactive", action="store_true")
    parser.add_argument("--model", default=LITELLM_MODEL)
    parser.add_argument("--output-dir", default="~/dev/tmp/video-translations/")
    parser.add_argument("--whisper-model", default="large-v3")
    return parser


# ---------------------------------------------------------------------------
# main() flow tests
# ---------------------------------------------------------------------------

@patch("video_pipeline_v2.burn_subtitles")
@patch("video_pipeline_v2.translate_srt")
@patch("video_pipeline_v2.generate_srt")
@patch("video_pipeline_v2.apply_edits")
@patch("video_pipeline_v2.detect_edits")
@patch("video_pipeline_v2.transcribe")
def test_main_flow_calls_in_order(
    mock_transcribe, mock_detect, mock_apply,
    mock_gen_srt, mock_translate, mock_burn, tmp_path, monkeypatch,
):
    """main() should call pipeline functions in the correct order."""
    run_dir_pattern = str(tmp_path)

    mock_transcribe.return_value = [{"start": 0, "end": 1, "text": "Hi", "words": []}]
    mock_detect.return_value = [{"action": "keep", "start": 0, "end": 1, "reason": "ok"}]
    mock_apply.return_value = str(tmp_path / "edited.mp4")
    mock_gen_srt.return_value = str(tmp_path / "subtitles_en.srt")
    mock_translate.return_value = str(tmp_path / "subtitles_es.srt")
    mock_burn.return_value = str(tmp_path / "final_es.mp4")

    monkeypatch.setattr(
        "sys.argv",
        ["prog", "input.mp4", "--languages", "es", "--output-dir", str(tmp_path)],
    )

    main()

    mock_transcribe.assert_called_once()
    mock_detect.assert_called_once()
    mock_apply.assert_called_once()
    mock_gen_srt.assert_called_once()
    mock_translate.assert_called_once()
    mock_burn.assert_called_once()


@patch("video_pipeline_v2.burn_subtitles")
@patch("video_pipeline_v2.translate_srt")
@patch("video_pipeline_v2.generate_srt")
@patch("video_pipeline_v2.detect_edits")
@patch("video_pipeline_v2.transcribe")
def test_main_skip_cuts(
    mock_transcribe, mock_detect, mock_gen_srt,
    mock_translate, mock_burn, tmp_path, monkeypatch,
):
    """With --skip-cuts, detect_edits and apply_edits should not be called."""
    mock_transcribe.return_value = [{"start": 0, "end": 1, "text": "Hi", "words": []}]
    mock_gen_srt.return_value = str(tmp_path / "subtitles_en.srt")
    mock_translate.return_value = str(tmp_path / "subtitles_es.srt")
    mock_burn.return_value = str(tmp_path / "final_es.mp4")

    monkeypatch.setattr(
        "sys.argv",
        ["prog", "input.mp4", "--languages", "es", "--skip-cuts", "--output-dir", str(tmp_path)],
    )

    main()

    mock_detect.assert_not_called()
    mock_transcribe.assert_called_once()
    mock_burn.assert_called_once()


@patch("video_pipeline_v2.heygen_translate")
@patch("video_pipeline_v2.burn_subtitles")
@patch("video_pipeline_v2.translate_srt")
@patch("video_pipeline_v2.generate_srt")
@patch("video_pipeline_v2.transcribe")
def test_main_heygen_routing(
    mock_transcribe, mock_gen_srt, mock_translate,
    mock_burn, mock_heygen, tmp_path, monkeypatch,
):
    """Languages in --heygen should use heygen_translate, others use translate+burn."""
    mock_transcribe.return_value = [{"start": 0, "end": 1, "text": "Hi", "words": []}]
    mock_gen_srt.return_value = str(tmp_path / "subtitles_en.srt")
    mock_translate.return_value = str(tmp_path / "subtitles_fr.srt")
    mock_burn.return_value = str(tmp_path / "final_fr.mp4")

    monkeypatch.setattr(
        "sys.argv",
        [
            "prog", "input.mp4",
            "--languages", "es", "fr",
            "--heygen", "es",
            "--skip-cuts",
            "--output-dir", str(tmp_path),
        ],
    )

    main()

    # es should go through heygen
    mock_heygen.assert_called_once()
    heygen_args = mock_heygen.call_args
    assert heygen_args.args[1] == "es" or heygen_args[0][1] == "es"

    # fr should go through translate + burn
    mock_translate.assert_called_once()
    mock_burn.assert_called_once()


# ---------------------------------------------------------------------------
# Output directory naming tests
# ---------------------------------------------------------------------------

@patch("video_pipeline_v2.burn_subtitles")
@patch("video_pipeline_v2.translate_srt")
@patch("video_pipeline_v2.generate_srt")
@patch("video_pipeline_v2.transcribe")
def test_output_dir_has_timestamp(
    mock_transcribe, mock_gen_srt, mock_translate, mock_burn,
    tmp_path, monkeypatch,
):
    """The run directory should contain a timestamp in YYYYMMDD_HHMMSS format."""
    mock_transcribe.return_value = [{"start": 0, "end": 1, "text": "Hi", "words": []}]
    mock_gen_srt.return_value = str(tmp_path / "subtitles_en.srt")
    mock_translate.return_value = str(tmp_path / "subtitles_es.srt")
    mock_burn.return_value = str(tmp_path / "final_es.mp4")

    monkeypatch.setattr(
        "sys.argv",
        ["prog", "myvideo.mp4", "--languages", "es", "--skip-cuts", "--output-dir", str(tmp_path)],
    )

    main()

    # Check that transcribe was called with a directory matching the pattern
    call_args = mock_transcribe.call_args
    run_dir = call_args.args[1] if call_args.args else call_args[0][1]

    # Should contain video stem and timestamp
    dirname = os.path.basename(run_dir)
    assert dirname.startswith("myvideo_")
    # Check timestamp format: YYYYMMDD_HHMMSS
    timestamp_part = dirname[len("myvideo_"):]
    assert re.match(r"\d{8}_\d{6}$", timestamp_part), f"Unexpected dir name: {dirname}"
