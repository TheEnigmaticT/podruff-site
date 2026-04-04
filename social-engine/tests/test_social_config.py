import json
import os
import tempfile
from social_config import load_config, resolve_text, validate_post


def test_load_config_reads_json():
    cfg = {
        "polling_interval_seconds": 300,
        "late_api_key": "sk_test",
        "postbridge_api_key": "pb_test",
        "clients": {
            "testclient": {
                "notion_database_id": "db_123",
                "platforms": {
                    "linkedin": {"provider": "late", "account_id": "acc_li"},
                    "twitter": {"provider": "postbridge", "account_id": 12345},
                },
            }
        },
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(cfg, f)
        path = f.name
    try:
        result = load_config(path)
        assert result["late_api_key"] == "sk_test"
        assert "testclient" in result["clients"]
        assert result["clients"]["testclient"]["platforms"]["twitter"]["provider"] == "postbridge"
    finally:
        os.unlink(path)


def test_resolve_text_platform_specific():
    row = {"Post Text": "generic", "LinkedIn Text": "linkedin specific"}
    assert resolve_text(row, "linkedin") == "linkedin specific"


def test_resolve_text_fallback_to_post_text():
    row = {"Post Text": "generic", "LinkedIn Text": ""}
    assert resolve_text(row, "linkedin") == "generic"


def test_resolve_text_fallback_to_post_text_missing_field():
    row = {"Post Text": "generic"}
    assert resolve_text(row, "tiktok") == "generic"


def test_resolve_text_returns_none_when_all_empty():
    row = {"Post Text": "", "LinkedIn Text": ""}
    assert resolve_text(row, "linkedin") is None


def test_validate_post_passes_valid():
    row = {
        "Post Text": "Hello world",
        "Platforms": ["linkedin", "twitter"],
        "Publish Date": "2026-03-10T12:00:00Z",
    }
    client_platforms = {"linkedin": {}, "twitter": {}}
    errors = validate_post(row, client_platforms)
    assert errors == []


def test_validate_post_no_text():
    row = {"Post Text": "", "Platforms": ["linkedin"], "Publish Date": "2026-03-10"}
    errors = validate_post(row, {"linkedin": {}})
    assert any("No post text" in e for e in errors)


def test_validate_post_no_platforms():
    row = {"Post Text": "Hello", "Platforms": [], "Publish Date": "2026-03-10"}
    errors = validate_post(row, {"linkedin": {}})
    assert any("No platforms" in e for e in errors)


def test_validate_post_no_date():
    row = {"Post Text": "Hello", "Platforms": ["linkedin"], "Publish Date": ""}
    errors = validate_post(row, {"linkedin": {}})
    assert any("No publish date" in e for e in errors)


def test_validate_post_x_too_long():
    row = {
        "Post Text": "x" * 281,
        "Platforms": ["twitter"],
        "Publish Date": "2026-03-10",
    }
    errors = validate_post(row, {"twitter": {}})
    assert any("280" in e for e in errors)


def test_validate_post_x_uses_platform_text_for_length():
    row = {
        "Post Text": "x" * 500,
        "X Text": "short tweet",
        "Platforms": ["twitter"],
        "Publish Date": "2026-03-10",
    }
    errors = validate_post(row, {"twitter": {}})
    assert errors == []


def test_validate_post_unconfigured_platform_warning():
    row = {
        "Post Text": "Hello",
        "Platforms": ["linkedin", "tiktok"],
        "Publish Date": "2026-03-10",
    }
    errors = validate_post(row, {"linkedin": {}})
    assert any("tiktok" in e.lower() and "not configured" in e.lower() for e in errors)
