import pytest
from reconcile import extract_numbers, compare_reports, ReconciliationError

def test_extract_numbers_finds_integers():
    text = "We had 1,234 sessions and 56 conversions."
    nums = extract_numbers(text)
    assert 1234 in nums
    assert 56 in nums

def test_extract_numbers_finds_decimals():
    text = "CTR was 3.4% and engagement rate was 67.2%."
    nums = extract_numbers(text)
    assert 3.4 in nums
    assert 67.2 in nums

def test_extract_numbers_ignores_years_and_dates():
    text = "In 2026, week 10, we saw growth."
    nums = extract_numbers(text)
    assert 2026 not in nums
    assert 10 not in nums

def test_compare_reports_passes_when_numbers_agree():
    a = "Sessions: 1,500. Conversion rate: 3.2%. Engaged sessions: 900."
    b = "Sessions: 1,500. Conversion rate: 3.2%. Engaged sessions: 900."
    compare_reports(a, b, threshold=0.05)

def test_compare_reports_passes_within_threshold():
    # 1500 vs 1510 = 0.67% difference — within 5%
    a = "Sessions: 1,500."
    b = "Sessions: 1,510."
    compare_reports(a, b, threshold=0.05)

def test_compare_reports_raises_outside_threshold():
    # 1500 vs 2000 = 33% difference — outside 5%
    a = "Sessions: 1,500."
    b = "Sessions: 2,000."
    with pytest.raises(ReconciliationError, match="deviation"):
        compare_reports(a, b, threshold=0.05)

def test_compare_reports_returns_string():
    a = "Sessions: 1,500. Conversion rate: 3.2%."
    b = "Sessions: 1,500. Conversion rate: 3.2%."
    result = compare_reports(a, b, threshold=0.05)
    assert isinstance(result, str)
    assert len(result) > 0

def test_compare_reports_raises_on_count_mismatch():
    # A has 2 numbers, B has 4 — suspicious
    a = "Sessions: 1,500. CTR: 3.2%."
    b = "Sessions: 1,500. CTR: 3.2%. Conversions: 45. Bounce rate: 67%."
    with pytest.raises(ReconciliationError):
        compare_reports(a, b, threshold=0.05)
