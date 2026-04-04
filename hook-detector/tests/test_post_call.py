"""Tests for post_call module — output formatting and analysis parsing."""

import time

from post_call import format_markdown_output, _parse_analysis
from suggestion_engine import Suggestion, SuggestionStatus, SuggestionType


def _make_suggestion(type, text, status=SuggestionStatus.NEW, offset=0):
    return Suggestion(
        id=Suggestion.make_id(),
        type=type,
        text=text,
        timestamp=time.time() + offset,
        status=status,
    )


class TestFormatMarkdown:
    def test_basic_output_has_frontmatter(self):
        suggestions = [_make_suggestion(SuggestionType.HOOK, "Test hook")]
        segments = [{"text": "Some transcript", "timestamp": time.time()}]
        result = format_markdown_output(suggestions, segments, time.time())
        assert "---" in result
        assert "date:" in result
        assert "## Hooks Generated" in result
        assert "## Full Transcript" in result

    def test_groups_hooks_by_status(self):
        start = time.time()
        suggestions = [
            _make_suggestion(SuggestionType.HOOK, "Used hook", SuggestionStatus.USED),
            _make_suggestion(SuggestionType.HOOK, "New hook", SuggestionStatus.NEW),
            _make_suggestion(SuggestionType.HOOK, "Bad hook", SuggestionStatus.BAD),
        ]
        result = format_markdown_output(suggestions, [], start)
        assert "### Used" in result
        assert "### Unused" in result
        assert "### Rejected" in result

    def test_includes_topics_and_followups(self):
        start = time.time()
        suggestions = [
            _make_suggestion(SuggestionType.TOPIC, "Interesting thread"),
            _make_suggestion(SuggestionType.FOLLOWUP, "Why did that happen?"),
        ]
        result = format_markdown_output(suggestions, [], start)
        assert "## Topics Flagged" in result
        assert "## Follow-up Questions" in result

    def test_includes_analysis_sections(self):
        start = time.time()
        analysis = {
            "topics_discussed": ["AI tools", "Pricing"],
            "topics_not_discussed": ["Enterprise sales"],
            "followups_next_time": ["Ask about cloud vs local"],
            "said_ids": [],
            "raw_analysis": "",
        }
        result = format_markdown_output([], [], start, analysis)
        assert "## Topics Discussed" in result
        assert "## Topics Not Discussed" in result
        assert "## Follow-ups for Next Time" in result

    def test_transcript_segments_formatted(self):
        start = time.time()
        segments = [
            {"text": "First chunk", "timestamp": start + 5},
            {"text": "Second chunk", "timestamp": start + 15},
        ]
        result = format_markdown_output([], segments, start)
        assert "First chunk" in result
        assert "Second chunk" in result
        assert "[00:00:05]" in result


class TestParseAnalysis:
    def test_parses_discussed_topics(self):
        raw = """TOPICS DISCUSSED:
- AI tools
- Pricing models

TOPICS NOT DISCUSSED:
- Enterprise sales

SAID HOOKS:
- (abc123) matched something

FOLLOW-UPS FOR NEXT TIME:
- Ask about cloud migration"""

        result = _parse_analysis(raw, [])
        assert "AI tools" in result["topics_discussed"]
        assert "Enterprise sales" in result["topics_not_discussed"]
        assert "Ask about cloud migration" in result["followups_next_time"]

    def test_handles_empty_analysis(self):
        result = _parse_analysis("", [])
        assert result["topics_discussed"] == []
        assert result["topics_not_discussed"] == []
