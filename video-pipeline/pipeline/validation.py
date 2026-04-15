"""JSON schema validation for editorial pipeline pass outputs."""


class ValidationError(Exception):
    """Raised when pass output fails validation."""
    pass


def _require(data: dict, key: str, context: str = ""):
    if key not in data:
        raise ValidationError(f"Missing required field '{key}'{' in ' + context if context else ''}")


def validate_outline(data: dict) -> None:
    """Validate Pass 1 (outline) output."""
    _require(data, "title")
    _require(data, "sections")
    if not isinstance(data["sections"], list) or len(data["sections"]) < 1:
        raise ValidationError("Outline must have at least 1 section")
    for i, section in enumerate(data["sections"]):
        ctx = f"section {i}"
        _require(section, "heading", ctx)
        _require(section, "start", ctx)
        _require(section, "end", ctx)
        _require(section, "points", ctx)
        if section["end"] <= section["start"]:
            raise ValidationError(f"Section end must be after start in {ctx}")


def validate_stories(data: dict) -> None:
    """Validate Pass 2 (story extraction) output."""
    _require(data, "stories")
    for i, story in enumerate(data["stories"]):
        ctx = f"story {i}"
        for key in ("id", "title", "start", "end", "engagement_score",
                     "standalone_rationale", "format", "hook_candidates"):
            _require(story, key, ctx)
        if story["format"] not in ("short", "long", "both"):
            raise ValidationError(f"Invalid format '{story['format']}' in {ctx} — must be short/long/both")
        if story["engagement_score"] < 1 or story["engagement_score"] > 10:
            raise ValidationError(f"engagement_score must be 1-10 in {ctx}")
        if not story["hook_candidates"]:
            raise ValidationError(f"hook_candidates must be non-empty in {ctx}")
        for j, hook in enumerate(story["hook_candidates"]):
            for key in ("text", "start", "end"):
                _require(hook, key, f"{ctx} hook {j}")


def validate_edl(data: dict) -> None:
    """Validate Pass 3 (editorial cut) EDL output."""
    _require(data, "story_id")
    _require(data, "versions")
    for version_name, version in data["versions"].items():
        ctx = f"version '{version_name}'"
        _require(version, "segments", ctx)
        _require(version, "trims", ctx)
        _require(version, "estimated_duration", ctx)
        if not version["segments"]:
            raise ValidationError(f"segments must be non-empty in {ctx}")
        for k, seg in enumerate(version["segments"]):
            seg_ctx = f"{ctx} segment {k}"
            _require(seg, "type", seg_ctx)
            _require(seg, "start", seg_ctx)
            _require(seg, "end", seg_ctx)
            duration = seg["end"] - seg["start"]
            if seg["type"] != "hook" and duration < 7.0:
                raise ValidationError(f"Segment duration {duration:.1f}s below 7 second minimum in {seg_ctx}")
            if seg["type"] == "hook":
                _require(seg, "narrative_bridge", seg_ctx)
        if version_name == "short":
            if version.get("target_duration") is not None and version["estimated_duration"] > version["target_duration"]:
                raise ValidationError(
                    f"Short estimated_duration {version['estimated_duration']:.1f}s exceeds "
                    f"target {version['target_duration']}s"
                )
