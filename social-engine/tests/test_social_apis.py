"""Tests for ZernioClient social media API wrapper."""

import json
from unittest.mock import patch, MagicMock
from social_apis import ZernioClient


def test_late_schedule_post_text_only():
    """ZernioClient.schedule_post sends correct payload for text-only post."""
    client = ZernioClient(api_key="sk_test")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"post": {"_id": "late_post_123"}}

    with patch("social_apis.requests.post", return_value=mock_resp) as mock_post:
        result = client.schedule_post(
            text="Hello world",
            account_id="acc_1",
            scheduled_at="2026-03-10T12:00:00Z",
        )

    mock_post.assert_called_once()
    call_kwargs = mock_post.call_args
    body = json.loads(call_kwargs.kwargs.get("data") or call_kwargs[1].get("data"))
    assert body["content"] == "Hello world"
    assert body["platforms"][0]["accountId"] == "acc_1"
    assert body["scheduledFor"] == "2026-03-10T12:00:00Z"
    assert result == "late_post_123"


def test_late_schedule_post_with_media():
    """ZernioClient.schedule_post handles presigned upload flow for media."""
    client = ZernioClient(api_key="sk_test")

    presign_resp = MagicMock()
    presign_resp.status_code = 200
    presign_resp.json.return_value = {
        "uploadUrl": "https://upload.example.com/abc",
        "publicUrl": "https://cdn.example.com/abc.mp4",
    }

    download_resp = MagicMock()
    download_resp.status_code = 200
    download_resp.content = b"fakevideobytes"
    download_resp.headers = {"content-type": "video/mp4"}

    upload_resp = MagicMock()
    upload_resp.status_code = 200

    post_resp = MagicMock()
    post_resp.status_code = 200
    post_resp.json.return_value = {"post": {"_id": "late_post_456"}}

    with patch("social_apis.requests.post") as mock_post, \
         patch("social_apis.requests.get", return_value=download_resp), \
         patch("social_apis.requests.put", return_value=upload_resp):
        mock_post.side_effect = [presign_resp, post_resp]
        result = client.schedule_post(
            text="Check this out",
            account_id="acc_1",
            scheduled_at="2026-03-10T12:00:00Z",
            media_url="https://r2.example.com/video.mp4",
        )

    assert result == "late_post_456"
    assert mock_post.call_count == 2
    # Verify presign request includes filename
    presign_call = mock_post.call_args_list[0]
    presign_body = json.loads(presign_call.kwargs.get("data", presign_call[1].get("data", "{}")))
    assert presign_body["filename"] == "video.mp4"
    assert "contentType" in presign_body


def test_late_delete_post():
    """ZernioClient.delete_post calls DELETE endpoint."""
    client = ZernioClient(api_key="sk_test")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"message": "Post deleted successfully"}

    with patch("social_apis.requests.delete", return_value=mock_resp) as mock_del:
        client.delete_post("late_post_123")

    mock_del.assert_called_once()
    assert "late_post_123" in mock_del.call_args[0][0]


def test_late_get_post_status():
    """ZernioClient.get_post_status returns post status string."""
    client = ZernioClient(api_key="sk_test")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"id": "late_post_123", "status": "published"}

    with patch("social_apis.requests.get", return_value=mock_resp):
        status = client.get_post_status("late_post_123")

    assert status == "published"


# --- Post Bridge tests ---

from social_apis import PostBridgeClient


def test_postbridge_schedule_post_text_only():
    """PostBridgeClient.schedule_post sends correct payload."""
    client = PostBridgeClient(api_key="pb_test")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "id": "pb_post_123",
        "status": "processing",
        "is_draft": False,
    }

    with patch("social_apis.requests.post", return_value=mock_resp) as mock_post:
        result = client.schedule_post(
            text="Hello X",
            account_id=48695,
            scheduled_at="2026-03-10T12:00:00Z",
        )

    call_kwargs = mock_post.call_args
    body = json.loads(call_kwargs.kwargs.get("data") or call_kwargs[1].get("data"))
    assert body["caption"] == "Hello X"
    assert body["social_accounts"] == [48695]
    assert body["scheduled_at"] == "2026-03-10T12:00:00Z"
    assert result == "pb_post_123"


def test_postbridge_schedule_post_immediate():
    """PostBridgeClient.schedule_post with no scheduled_at posts immediately."""
    client = PostBridgeClient(api_key="pb_test")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"id": "pb_post_789", "status": "processing"}

    with patch("social_apis.requests.post", return_value=mock_resp) as mock_post:
        result = client.schedule_post(text="Now!", account_id=48695)

    body = json.loads(mock_post.call_args.kwargs.get("data") or mock_post.call_args[1].get("data"))
    assert "scheduled_at" not in body or body["scheduled_at"] is None
    assert result == "pb_post_789"


def test_postbridge_delete_post():
    """PostBridgeClient.delete_post calls DELETE endpoint."""
    client = PostBridgeClient(api_key="pb_test")
    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with patch("social_apis.requests.delete", return_value=mock_resp) as mock_del:
        client.delete_post("pb_post_123")

    assert "pb_post_123" in mock_del.call_args[0][0]


def test_postbridge_get_post_status():
    """PostBridgeClient.get_post_status returns status string."""
    client = PostBridgeClient(api_key="pb_test")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"id": "pb_post_123", "status": "posted"}

    with patch("social_apis.requests.get", return_value=mock_resp):
        status = client.get_post_status("pb_post_123")

    assert status == "posted"


# --- Zernio Profile & Account management tests ---


def test_create_profile():
    """ZernioClient.create_profile sends correct payload and returns profile."""
    client = ZernioClient(api_key="sk_test")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "message": "Profile created successfully",
        "profile": {"_id": "prof_123", "name": "Acme Corp", "color": "#ffeda0"},
    }

    with patch("social_apis.requests.post", return_value=mock_resp) as mock_post:
        result = client.create_profile("Acme Corp", description="Test", color="#ffeda0")

    body = json.loads(mock_post.call_args.kwargs.get("data") or mock_post.call_args[1].get("data"))
    assert body["name"] == "Acme Corp"
    assert body["description"] == "Test"
    assert result["_id"] == "prof_123"


def test_list_profiles():
    """ZernioClient.list_profiles returns list of profiles."""
    client = ZernioClient(api_key="sk_test")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "profiles": [
            {"_id": "prof_1", "name": "Default"},
            {"_id": "prof_2", "name": "Client X"},
        ]
    }

    with patch("social_apis.requests.get", return_value=mock_resp):
        profiles = client.list_profiles()

    assert len(profiles) == 2
    assert profiles[1]["name"] == "Client X"


def test_update_profile():
    """ZernioClient.update_profile sends PATCH with kwargs."""
    client = ZernioClient(api_key="sk_test")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"message": "Profile updated"}

    with patch("social_apis.requests.patch", return_value=mock_resp) as mock_patch:
        client.update_profile("prof_123", name="New Name", color="#ff0000")

    assert "prof_123" in mock_patch.call_args[0][0]
    body = json.loads(mock_patch.call_args.kwargs.get("data") or mock_patch.call_args[1].get("data"))
    assert body["name"] == "New Name"
    assert body["color"] == "#ff0000"


def test_delete_profile():
    """ZernioClient.delete_profile calls DELETE endpoint."""
    client = ZernioClient(api_key="sk_test")
    mock_resp = MagicMock()

    with patch("social_apis.requests.delete", return_value=mock_resp) as mock_del:
        client.delete_profile("prof_123")

    assert "prof_123" in mock_del.call_args[0][0]


def test_list_accounts():
    """ZernioClient.list_accounts returns account list."""
    client = ZernioClient(api_key="sk_test")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "accounts": [
            {"_id": "acc_1", "platform": "linkedin"},
            {"_id": "acc_2", "platform": "instagram"},
        ]
    }

    with patch("social_apis.requests.get", return_value=mock_resp):
        accounts = client.list_accounts()

    assert len(accounts) == 2
    assert accounts[0]["platform"] == "linkedin"


def test_get_account_health_single():
    """ZernioClient.get_account_health with ID hits single-account endpoint."""
    client = ZernioClient(api_key="sk_test")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"healthy": True}

    with patch("social_apis.requests.get", return_value=mock_resp) as mock_get:
        client.get_account_health("acc_1")

    assert "acc_1/health" in mock_get.call_args[0][0]


def test_get_account_health_all():
    """ZernioClient.get_account_health without ID hits all-accounts endpoint."""
    client = ZernioClient(api_key="sk_test")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"accounts": []}

    with patch("social_apis.requests.get", return_value=mock_resp) as mock_get:
        client.get_account_health()

    assert mock_get.call_args[0][0].endswith("/accounts/health")


def test_get_connect_url():
    """ZernioClient.get_connect_url passes profileId as query param."""
    client = ZernioClient(api_key="sk_test")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "authUrl": "https://linkedin.com/oauth?...",
        "state": "some-state",
    }

    with patch("social_apis.requests.get", return_value=mock_resp) as mock_get:
        result = client.get_connect_url("linkedin", "prof_123")

    assert "connect/linkedin" in mock_get.call_args[0][0]
    assert mock_get.call_args.kwargs["params"]["profileId"] == "prof_123"
    assert result["authUrl"] == "https://linkedin.com/oauth?..."


def test_get_connect_urls_multiple():
    """ZernioClient.get_connect_urls returns dict of platform->authUrl."""
    client = ZernioClient(api_key="sk_test")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"authUrl": "https://example.com/auth", "state": "s"}

    with patch("social_apis.requests.get", return_value=mock_resp):
        urls = client.get_connect_urls("prof_123", ["linkedin", "youtube"])

    assert "linkedin" in urls
    assert "youtube" in urls
    assert urls["linkedin"] == "https://example.com/auth"


def test_get_connect_urls_skips_failures():
    """ZernioClient.get_connect_urls skips platforms that return errors."""
    client = ZernioClient(api_key="sk_test")
    import requests as req

    ok_resp = MagicMock()
    ok_resp.json.return_value = {"authUrl": "https://example.com/auth", "state": "s"}

    fail_resp = MagicMock()
    fail_resp.raise_for_status.side_effect = req.HTTPError("400")

    with patch("social_apis.requests.get", side_effect=[ok_resp, fail_resp]):
        urls = client.get_connect_urls("prof_123", ["linkedin", "badplatform"])

    assert "linkedin" in urls
    assert "badplatform" not in urls


def test_connect_bluesky():
    """ZernioClient.connect_bluesky sends credentials payload."""
    client = ZernioClient(api_key="sk_test")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"message": "Connected"}

    with patch("social_apis.requests.post", return_value=mock_resp) as mock_post:
        result = client.connect_bluesky("prof_123", "user.bsky.social", "app-pw-123")

    body = json.loads(mock_post.call_args.kwargs.get("data") or mock_post.call_args[1].get("data"))
    assert body["profileId"] == "prof_123"
    assert body["handle"] == "user.bsky.social"
    assert body["appPassword"] == "app-pw-123"
    assert result["message"] == "Connected"
