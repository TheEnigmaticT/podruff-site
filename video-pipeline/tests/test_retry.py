import time
from unittest.mock import patch
import pytest
from pipeline.retry import retry


def test_succeeds_first_try():
    calls = []

    @retry(max_attempts=3, base_delay=0.01)
    def good():
        calls.append(1)
        return "ok"

    assert good() == "ok"
    assert len(calls) == 1


def test_succeeds_on_second_try():
    calls = []

    @retry(max_attempts=3, base_delay=0.01)
    def flaky():
        calls.append(1)
        if len(calls) < 2:
            raise ValueError("boom")
        return "ok"

    assert flaky() == "ok"
    assert len(calls) == 2


def test_raises_after_max_attempts():
    calls = []

    @retry(max_attempts=3, base_delay=0.01)
    def bad():
        calls.append(1)
        raise ValueError("always fails")

    with pytest.raises(ValueError, match="always fails"):
        bad()
    assert len(calls) == 3


def test_only_catches_specified_exceptions():
    @retry(max_attempts=3, base_delay=0.01, exceptions=(ValueError,))
    def wrong_error():
        raise TypeError("not caught")

    with pytest.raises(TypeError):
        wrong_error()


def test_exponential_backoff_timing():
    calls = []

    @retry(max_attempts=3, base_delay=0.05)
    def slow_fail():
        calls.append(time.monotonic())
        raise ValueError("fail")

    with pytest.raises(ValueError):
        slow_fail()

    assert len(calls) == 3
    # First retry delay: 0.05s, second: 0.10s
    gap1 = calls[1] - calls[0]
    gap2 = calls[2] - calls[1]
    assert gap1 >= 0.04  # base_delay * 2^0 = 0.05
    assert gap2 >= 0.08  # base_delay * 2^1 = 0.10
    assert gap2 > gap1   # exponential growth
