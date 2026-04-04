import re
from typing import List


class ReconciliationError(Exception):
    pass


# Values that are almost certainly years, months, days, or small ordinals — not business metrics
_EXCLUDE_VALUES = set(range(2020, 2031)) | set(range(1, 13)) | set(range(1, 32))


def extract_numbers(text: str) -> List[float]:
    """Extract all numeric values from text, excluding dates/years/small ordinals."""
    # Match: numbers with commas (1,234), decimals (3.4), plain integers (56)
    raw = re.findall(r'\b\d{1,3}(?:,\d{3})*(?:\.\d+)?|\b\d+\.\d+\b', text)
    result = []
    for r in raw:
        val = float(r.replace(",", ""))
        if val not in _EXCLUDE_VALUES and val > 0:
            result.append(val)
    return result


def _pct_diff(a: float, b: float) -> float:
    if a == 0 and b == 0:
        return 0.0
    if a == 0:
        return 1.0
    return abs(a - b) / abs(a)


def compare_reports(report_a: str, report_b: str, threshold: float = 0.05) -> str:
    """
    Compare numeric values between two independent analysis passes.
    Raises ReconciliationError if any number deviates by more than threshold.
    Returns report_a as the canonical version if checks pass.
    """
    nums_a = extract_numbers(report_a)
    nums_b = extract_numbers(report_b)

    # Check count mismatch first
    if abs(len(nums_a) - len(nums_b)) > 1:
        raise ReconciliationError(
            f"Hallucination check failed — pass A has {len(nums_a)} numbers, "
            f"pass B has {len(nums_b)} numbers (mismatch suggests fabricated metrics)"
        )

    # For each number in A, find closest match in B and check deviation
    violations = []
    nums_b_remaining = sorted(nums_b)
    for a in sorted(nums_a):
        if not nums_b_remaining:
            break
        # Find closest value in B
        closest_idx = min(range(len(nums_b_remaining)), key=lambda i: abs(nums_b_remaining[i] - a))
        b = nums_b_remaining.pop(closest_idx)
        diff = _pct_diff(a, b)
        if diff > threshold:
            violations.append(f"  A={a:,.1f} vs B={b:,.1f} → {diff:.1%} deviation")

    if violations:
        detail = "\n".join(violations)
        raise ReconciliationError(
            f"Hallucination check failed — {len(violations)} number(s) deviate >{threshold:.0%}:\n{detail}"
        )

    return report_a
