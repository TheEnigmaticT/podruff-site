"""Tests for hook_generator module."""

import pytest
from unittest.mock import patch, MagicMock
from hook_generator import HookGenerator
from suggestion_engine import BaseGenerator


class TestParseHooks:
    """Test the static response parsing logic."""

    def test_plain_lines(self):
        raw = "AI won't fix broken businesses\nPoint the tool before you fire"
        hooks = BaseGenerator._parse_response(raw)
        assert len(hooks) == 2
        assert hooks[0] == "AI won't fix broken businesses"

    def test_strips_bullets(self):
        raw = "- Hook one\n- Hook two\n- Hook three"
        hooks = BaseGenerator._parse_response(raw)
        assert len(hooks) == 3
        assert hooks[0] == "Hook one"

    def test_strips_numbering(self):
        raw = "1. First hook\n2. Second hook"
        hooks = BaseGenerator._parse_response(raw)
        assert len(hooks) == 2
        assert hooks[0] == "First hook"

    def test_strips_multi_digit_numbering(self):
        raw = "10. Tenth hook\n11) Eleventh hook"
        hooks = BaseGenerator._parse_response(raw)
        assert len(hooks) == 2
        assert hooks[0] == "Tenth hook"
        assert hooks[1] == "Eleventh hook"

    def test_strips_quotes(self):
        raw = '"Everyone believes but nobody acts"'
        hooks = BaseGenerator._parse_response(raw)
        assert hooks[0] == "Everyone believes but nobody acts"

    def test_empty_input(self):
        assert BaseGenerator._parse_response("") == []
        assert BaseGenerator._parse_response("   \n  \n") == []


class TestGenerateHooks:
    """Test hook generation with mocked Ollama."""

    @patch("suggestion_engine.requests.post")
    def test_generates_hooks_from_transcript(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "response": "Everyone's bought in but lost\nAutomate before you're drowning"
        }
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        gen = HookGenerator()
        hooks = gen.generate_hooks("Some transcript text about AI and automation")

        assert len(hooks) == 2
        assert mock_post.called
        # Hooks should also be stored internally
        assert len(gen.get_hooks()) == 2

    @patch("suggestion_engine.requests.post")
    def test_empty_transcript_returns_nothing(self, mock_post):
        gen = HookGenerator()
        hooks = gen.generate_hooks("")
        assert hooks == []
        assert not mock_post.called

    @patch("suggestion_engine.requests.post")
    def test_ollama_timeout_returns_empty(self, mock_post):
        import requests as req
        mock_post.side_effect = req.Timeout("timeout")
        gen = HookGenerator()
        hooks = gen.generate_hooks("Some transcript")
        assert hooks == []

    @patch("suggestion_engine.requests.post")
    def test_previous_hooks_included_in_prompt(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"response": "New hook here"}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        gen = HookGenerator()
        gen.generate_hooks("First transcript")
        gen.generate_hooks("Second transcript")

        # The second call should include previous hooks in the prompt
        second_call_body = mock_post.call_args_list[1][1]["json"]["prompt"]
        assert "New hook here" in second_call_body


class TestCheckOllama:
    """Test Ollama health checks."""

    @patch("suggestion_engine.requests.get")
    def test_raises_if_not_running(self, mock_get):
        import requests as req
        mock_get.side_effect = req.ConnectionError("refused")
        gen = HookGenerator()
        with pytest.raises(RuntimeError, match="Ollama not responding"):
            gen.check_ollama()

    @patch("suggestion_engine.requests.get")
    def test_raises_if_model_missing(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"models": [{"name": "llama3:8b"}]}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        gen = HookGenerator()
        with pytest.raises(RuntimeError, match="not found"):
            gen.check_ollama()

    @patch("suggestion_engine.requests.get")
    def test_passes_if_model_present(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"models": [{"name": "qwen3:30b"}]}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        gen = HookGenerator()
        gen.check_ollama()  # Should not raise
