"""Thin API clients for Zernio (zernio.com, formerly Late/getlate.dev) and Post Bridge social media posting."""

import json
import logging
import os
import requests

log = logging.getLogger("social_poller")

ZERNIO_BASE = "https://zernio.com/api/v1"


class ZernioClient:
    """Client for the Zernio (zernio.com) unified social media API."""

    def __init__(self, api_key):
        self.api_key = api_key
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def _presign_and_upload(self, media_url):
        """Download media from URL, upload via Late presigned URL flow.
        Returns the public URL for use in post creation."""
        dl = requests.get(media_url, timeout=120)
        dl.raise_for_status()
        content_type = dl.headers.get("content-type", "video/mp4")

        filename = os.path.basename(media_url.split("?")[0]) or "upload"
        presign = requests.post(
            f"{ZERNIO_BASE}/media/presign",
            headers=self.headers,
            data=json.dumps({"filename": filename, "contentType": content_type}),
            timeout=30,
        )
        presign.raise_for_status()
        presign_data = presign.json()

        requests.put(
            presign_data["uploadUrl"],
            data=dl.content,
            headers={"Content-Type": content_type},
            timeout=120,
        ).raise_for_status()

        return presign_data["publicUrl"]

    def schedule_post(self, text, account_id, scheduled_at, media_url=None, platform="linkedin"):
        """Schedule a post. Returns the Late post ID."""
        body = {
            "content": text,
            "platforms": [{"platform": platform, "accountId": account_id}],
            "scheduledFor": scheduled_at,
        }

        if media_url:
            public_url = self._presign_and_upload(media_url)
            body["mediaItems"] = [{"url": public_url}]

        resp = requests.post(
            f"{ZERNIO_BASE}/posts",
            headers=self.headers,
            data=json.dumps(body),
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        # Late returns {"post": {"_id": "..."}} on success
        if "post" in data:
            return data["post"]["_id"]
        return data.get("id", data.get("_id", ""))

    def delete_post(self, post_id):
        """Delete a scheduled post."""
        resp = requests.delete(
            f"{ZERNIO_BASE}/posts/{post_id}",
            headers=self.headers,
            timeout=30,
        )
        resp.raise_for_status()

    def get_post_status(self, post_id):
        """Check post status. Returns status string."""
        resp = requests.get(
            f"{ZERNIO_BASE}/posts/{post_id}",
            headers=self.headers,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["status"]

    # ── Profile management ──────────────────────────────────────────

    def create_profile(self, name, description="", color="#ffeda0"):
        """Create a new profile. Returns the profile dict."""
        resp = requests.post(
            f"{ZERNIO_BASE}/profiles",
            headers=self.headers,
            data=json.dumps({"name": name, "description": description, "color": color}),
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["profile"]

    def list_profiles(self):
        """List all profiles. Returns list of profile dicts."""
        resp = requests.get(
            f"{ZERNIO_BASE}/profiles",
            headers=self.headers,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["profiles"]

    def get_profile(self, profile_id):
        """Get a single profile by ID."""
        resp = requests.get(
            f"{ZERNIO_BASE}/profiles/{profile_id}",
            headers=self.headers,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["profile"]

    def update_profile(self, profile_id, **kwargs):
        """Update profile fields (name, description, color, isDefault)."""
        resp = requests.patch(
            f"{ZERNIO_BASE}/profiles/{profile_id}",
            headers=self.headers,
            data=json.dumps(kwargs),
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def delete_profile(self, profile_id):
        """Delete a profile."""
        resp = requests.delete(
            f"{ZERNIO_BASE}/profiles/{profile_id}",
            headers=self.headers,
            timeout=30,
        )
        resp.raise_for_status()

    # ── Account management ──────────────────────────────────────────

    def list_accounts(self):
        """List all connected social accounts."""
        resp = requests.get(
            f"{ZERNIO_BASE}/accounts",
            headers=self.headers,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["accounts"]

    def get_account_health(self, account_id=None):
        """Check connection health. If account_id given, checks one; otherwise all."""
        url = f"{ZERNIO_BASE}/accounts/{account_id}/health" if account_id else f"{ZERNIO_BASE}/accounts/health"
        resp = requests.get(url, headers=self.headers, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def delete_account(self, account_id):
        """Disconnect and remove a social account."""
        resp = requests.delete(
            f"{ZERNIO_BASE}/accounts/{account_id}",
            headers=self.headers,
            timeout=30,
        )
        resp.raise_for_status()

    # ── OAuth connect links ─────────────────────────────────────────

    CONNECT_PLATFORMS = [
        "linkedin", "instagram", "facebook", "threads", "tiktok",
        "youtube", "pinterest", "reddit", "bluesky", "googlebusiness",
    ]

    def get_connect_url(self, platform, profile_id):
        """Get an OAuth connect URL for a platform. Returns {authUrl, state}."""
        resp = requests.get(
            f"{ZERNIO_BASE}/connect/{platform}",
            headers=self.headers,
            params={"profileId": profile_id},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def get_connect_urls(self, profile_id, platforms=None):
        """Get OAuth URLs for multiple platforms. Returns {platform: authUrl} dict."""
        platforms = platforms or self.CONNECT_PLATFORMS
        urls = {}
        for platform in platforms:
            try:
                data = self.get_connect_url(platform, profile_id)
                urls[platform] = data["authUrl"]
            except requests.HTTPError as e:
                log.warning("Failed to get connect URL for %s: %s", platform, e)
        return urls

    def connect_bluesky(self, profile_id, handle, app_password):
        """Connect Bluesky via credentials (no OAuth)."""
        resp = requests.post(
            f"{ZERNIO_BASE}/connect/bluesky/credentials",
            headers=self.headers,
            data=json.dumps({
                "profileId": profile_id,
                "handle": handle,
                "appPassword": app_password,
            }),
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()


POSTBRIDGE_BASE = "https://api.post-bridge.com/v1"


class PostBridgeClient:
    """Client for the Post Bridge API (X/Twitter posting)."""

    def __init__(self, api_key):
        self.api_key = api_key
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def schedule_post(self, text, account_id, scheduled_at=None, media_url=None):
        """Schedule or immediately post to X. Returns post ID."""
        body = {
            "caption": text,
            "social_accounts": [account_id],
        }
        if scheduled_at:
            body["scheduled_at"] = scheduled_at
        if media_url:
            body["media_urls"] = [media_url]

        resp = requests.post(
            f"{POSTBRIDGE_BASE}/posts",
            headers=self.headers,
            data=json.dumps(body),
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["id"]

    def delete_post(self, post_id):
        """Delete a scheduled post."""
        resp = requests.delete(
            f"{POSTBRIDGE_BASE}/posts/{post_id}",
            headers=self.headers,
            timeout=30,
        )
        resp.raise_for_status()

    def get_post_status(self, post_id):
        """Check post status. Returns status string."""
        resp = requests.get(
            f"{POSTBRIDGE_BASE}/posts/{post_id}",
            headers=self.headers,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["status"]
