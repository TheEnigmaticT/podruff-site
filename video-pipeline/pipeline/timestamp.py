"""Snap LLM-generated timestamps to transcript sentence boundaries."""

import logging

logger = logging.getLogger(__name__)


def snap_timestamp(ts: float, boundaries: list[float], tolerance: float = 2.0) -> float:
    """Snap a timestamp to the nearest value in boundaries.

    Logs a warning if the nearest boundary is beyond tolerance.
    """
    if not boundaries:
        return ts
    nearest = min(boundaries, key=lambda b: (abs(b - ts), -b))
    if abs(nearest - ts) > tolerance:
        logger.warning("Timestamp %.2f snapped to %.2f (beyond %.1fs tolerance)", ts, nearest, tolerance)
    return nearest


def get_boundaries(transcript: list[dict]) -> list[float]:
    """Extract all unique start/end timestamps from transcript."""
    boundaries = set()
    for seg in transcript:
        boundaries.add(seg["start"])
        boundaries.add(seg["end"])
    return sorted(boundaries)


def snap_to_boundaries(data: dict, transcript: list[dict],
                       keys: list[str] = ("start", "end"),
                       tolerance: float = 2.0) -> dict:
    """Snap specified timestamp keys in a dict to transcript boundaries.

    Returns a new dict with snapped values. Does not modify the original.
    """
    boundaries = get_boundaries(transcript)
    result = dict(data)
    for key in keys:
        if key in result and isinstance(result[key], (int, float)):
            result[key] = snap_timestamp(float(result[key]), boundaries, tolerance)
    return result
