"""Tests for transcript_repair module — format_repair_summary only (LLM not tested directly)."""

from pipeline.transcript_repair import format_repair_summary


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

HIGH_REPAIR = {"original": "scrumptious", "corrected": "scrum", "confidence": "high"}
MEDIUM_REPAIR = {
    "original": "agille",
    "corrected": "agile",
    "confidence": "medium",
    "timestamp": "01:23",
    "line": 5,
}
LOW_REPAIR_WITH_SUGGESTIONS = {
    "original": "kanb",
    "corrected": None,
    "confidence": "low",
    "suggestions": ["kanban", "can't"],
    "timestamp": "02:45",
    "line": 12,
}
LOW_REPAIR_NO_SUGGESTIONS = {
    "original": "xyz",
    "corrected": None,
    "confidence": "low",
    "timestamp": "03:10",
    "line": 7,
}


# ---------------------------------------------------------------------------
# test_format_summary_auto_corrected
# ---------------------------------------------------------------------------

def test_format_summary_auto_corrected():
    """High and medium confidence repairs appear in the auto-corrected section."""
    repairs = [HIGH_REPAIR, MEDIUM_REPAIR]
    result = format_repair_summary(repairs, "Acme Corp", "Episode 12")

    assert "Transcript cleanup for Acme Corp" in result
    assert "Episode 12" in result
    assert "Auto-corrected" in result
    assert "scrumptious→scrum" in result
    assert "agille→agile" in result
    # Should NOT appear in needs-review section
    assert "Needs review" not in result


def test_format_summary_auto_corrected_count():
    """Auto-corrected count matches number of high/medium repairs."""
    repairs = [HIGH_REPAIR, MEDIUM_REPAIR]
    result = format_repair_summary(repairs, "Acme", "S01E01")
    # "Auto-corrected (2 words)" expected
    assert "Auto-corrected (2 words)" in result


# ---------------------------------------------------------------------------
# test_format_summary_needs_review
# ---------------------------------------------------------------------------

def test_format_summary_needs_review():
    """Low confidence repairs appear in the needs-review section with suggestions."""
    repairs = [LOW_REPAIR_WITH_SUGGESTIONS]
    result = format_repair_summary(repairs, "Acme Corp", "Episode 12")

    assert "Needs review" in result
    assert "kanb" in result
    assert "kanban" in result
    assert "can't" in result
    # Should NOT appear in auto-corrected section
    assert "Auto-corrected" not in result


def test_format_summary_needs_review_bullet_format():
    """Needs-review items are formatted as bullet lines with timestamp and line."""
    repairs = [LOW_REPAIR_WITH_SUGGESTIONS]
    result = format_repair_summary(repairs, "Client", "Session")

    # Bullet point present
    assert "•" in result
    # Line number present
    assert "12" in result
    # Timestamp present
    assert "02:45" in result


def test_format_summary_needs_review_no_suggestions():
    """Low confidence repair without suggestions still appears in needs-review."""
    repairs = [LOW_REPAIR_NO_SUGGESTIONS]
    result = format_repair_summary(repairs, "Client", "Session")

    assert "Needs review" in result
    assert "xyz" in result


# ---------------------------------------------------------------------------
# test_format_summary_mixed
# ---------------------------------------------------------------------------

def test_format_summary_mixed():
    """Mixed repairs: auto-corrected section and needs-review section both present."""
    repairs = [HIGH_REPAIR, MEDIUM_REPAIR, LOW_REPAIR_WITH_SUGGESTIONS]
    result = format_repair_summary(repairs, "Beta Inc", "Pilot")

    assert "Auto-corrected" in result
    assert "Needs review" in result
    # Auto-corrected count = 2 (high + medium)
    assert "Auto-corrected (2 words)" in result
    # Both sections populated
    assert "scrumptious→scrum" in result
    assert "kanb" in result


def test_format_summary_empty():
    """Empty repair list produces a message with zero corrections."""
    result = format_repair_summary([], "Client", "Session")
    assert "Client" in result
    assert "Session" in result
    # No auto-correct or review sections when nothing to report
    assert "Auto-corrected" not in result
    assert "Needs review" not in result


def test_format_summary_header_format():
    """Header line matches expected template."""
    result = format_repair_summary([], "My Client", "My Session")
    assert result.startswith("Transcript cleanup for My Client — My Session")
