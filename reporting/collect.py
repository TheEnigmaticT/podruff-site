from googleapiclient.discovery import build
from auth import get_credentials


def fetch_ga4(creds, property_id: str, start_date: str, end_date: str) -> dict:
    service = build("analyticsdata", "v1beta", credentials=creds)
    response = service.properties().runReport(
        property=property_id,
        body={
            "dateRanges": [{"startDate": start_date, "endDate": end_date}],
            "dimensions": [
                {"name": "sessionDefaultChannelGroup"},
                {"name": "pagePath"},
            ],
            "metrics": [
                {"name": "sessions"},
                {"name": "engagedSessions"},
                {"name": "conversions"},
                {"name": "newUsers"},
            ],
            "limit": 100,
        },
    ).execute()
    return response


def fetch_search_console(creds, site_url: str, start_date: str, end_date: str) -> dict:
    service = build("searchconsole", "v1", credentials=creds)
    response = service.searchanalytics().query(
        siteUrl=site_url,
        body={
            "startDate": start_date,
            "endDate": end_date,
            "dimensions": ["query", "page"],
            "rowLimit": 100,
        },
    ).execute()
    return response


def fetch_youtube(creds, channel_id: str, start_date: str, end_date: str) -> dict:
    service = build("youtubeAnalytics", "v2", credentials=creds)
    response = service.reports().query(
        ids=f"channel=={channel_id}",
        startDate=start_date,
        endDate=end_date,
        metrics="views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage",
        dimensions="video",
        sort="-views",
        maxResults=25,
    ).execute()
    return response


def fetch_google_ads(creds, customer_id: str, start_date: str, end_date: str) -> dict:
    raise NotImplementedError(
        "Google Ads fetch requires the google-ads library. "
        "Add it to requirements.txt and implement using GoogleAdsClient."
    )


def fetch_all(client: dict, start_date: str, end_date: str) -> dict:
    creds = get_credentials(client["credential"])
    channels = client.get("channels", [])
    result = {}

    if "ga4" in channels:
        try:
            result["ga4"] = fetch_ga4(creds, client["ga4_property"], start_date, end_date)
        except Exception as e:
            import sys
            print(f"WARNING: GA4 fetch failed for {client.get('name', 'unknown')}: {e}", file=sys.stderr)
            result["ga4"] = {"error": str(e)}

    if "search_console" in channels:
        try:
            result["search_console"] = fetch_search_console(creds, client["gsc_url"], start_date, end_date)
        except Exception as e:
            import sys
            print(f"WARNING: search_console fetch failed for {client.get('name', 'unknown')}: {e}", file=sys.stderr)
            result["search_console"] = {"error": str(e)}

    if "youtube" in channels:
        try:
            result["youtube"] = fetch_youtube(creds, client["yt_channel_id"], start_date, end_date)
        except Exception as e:
            import sys
            print(f"WARNING: youtube fetch failed for {client.get('name', 'unknown')}: {e}", file=sys.stderr)
            result["youtube"] = {"error": str(e)}

    if "google_ads" in channels:
        try:
            result["google_ads"] = fetch_google_ads(creds, client["ads_customer_id"], start_date, end_date)
        except Exception as e:
            import sys
            print(f"WARNING: google_ads fetch failed for {client.get('name', 'unknown')}: {e}", file=sys.stderr)
            result["google_ads"] = {"error": str(e)}

    return result
