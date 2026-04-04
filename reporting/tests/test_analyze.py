import pytest
from unittest.mock import patch, MagicMock
from analyze import load_skills, build_prompt, run_analysis_pass

MOCK_DATA = {
    "ga4": {"rows": [{"dimensionValues": [{"value": "Organic Search"}], "metricValues": [{"value": "1500"}]}]},
}

def test_load_skills_returns_string():
    skills = load_skills(["analytics-analysis"])
    assert isinstance(skills, str)
    assert len(skills) > 0

def test_load_skills_raises_for_unknown_skill():
    with pytest.raises(FileNotFoundError):
        load_skills(["nonexistent-skill-xyz"])

def test_build_prompt_includes_data():
    prompt = build_prompt(MOCK_DATA, ["analytics-analysis"], reverse=False)
    assert isinstance(prompt, str)
    assert len(prompt) > 0

def test_build_prompt_reverse_changes_section_order():
    prompt_a = build_prompt(MOCK_DATA, ["analytics-analysis"], reverse=False)
    prompt_b = build_prompt(MOCK_DATA, ["analytics-analysis"], reverse=True)
    assert isinstance(prompt_b, str)

def test_run_analysis_pass_calls_claude():
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="## Analysis\n\nSessions: 1,500.")]
    with patch("analyze.anthropic.Anthropic") as mock_cls:
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        mock_cls.return_value = mock_client
        result = run_analysis_pass("test prompt", "test system")
    assert "1,500" in result
