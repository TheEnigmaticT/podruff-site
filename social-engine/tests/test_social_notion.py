import json
from unittest.mock import patch, MagicMock
from social_notion import NotionClient


def _mock_response(data, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    return resp


def test_query_by_status():
    client = NotionClient(api_key="ntn_test")
    mock_resp = _mock_response({"results": [], "has_more": False})
    with patch("social_notion.requests.post", return_value=mock_resp) as mock_post:
        results = client.query_by_status("db_123", "Approved")
    body = json.loads(mock_post.call_args.kwargs.get("data") or mock_post.call_args[1].get("data"))
    assert body["filter"]["property"] == "Status"
    assert body["filter"]["select"]["equals"] == "Approved"


def test_query_by_status_paginates():
    client = NotionClient(api_key="ntn_test")
    page1 = _mock_response({"results": [{"id": "page_1"}], "has_more": True, "next_cursor": "cursor_abc"})
    page2 = _mock_response({"results": [{"id": "page_2"}], "has_more": False})
    with patch("social_notion.requests.post", side_effect=[page1, page2]):
        results = client.query_by_status("db_123", "Approved")
    assert len(results) == 2


def test_query_with_external_ids():
    client = NotionClient(api_key="ntn_test")
    mock_resp = _mock_response({"results": [{"id": "page_1"}], "has_more": False})
    with patch("social_notion.requests.post", return_value=mock_resp) as mock_post:
        results = client.query_with_external_ids("db_123")
    body = json.loads(mock_post.call_args.kwargs.get("data") or mock_post.call_args[1].get("data"))
    assert body["filter"]["property"] == "External Post IDs"
    assert body["filter"]["rich_text"]["is_not_empty"] is True
    assert len(results) == 1


def test_update_page_properties():
    client = NotionClient(api_key="ntn_test")
    mock_resp = _mock_response({"id": "page_1"})
    with patch("social_notion.requests.patch", return_value=mock_resp) as mock_patch:
        client.update_page("page_1", {"Status": {"select": {"name": "Scheduled"}}})
    assert "page_1" in mock_patch.call_args[0][0]


def test_set_status():
    client = NotionClient(api_key="ntn_test")
    with patch.object(client, "update_page") as mock_update:
        client.set_status("page_1", "Scheduled")
    mock_update.assert_called_once_with(
        "page_1", {"Status": {"select": {"name": "Scheduled"}}}
    )


def test_set_external_ids():
    client = NotionClient(api_key="ntn_test")
    with patch.object(client, "update_page") as mock_update:
        client.set_external_ids("page_1", {"linkedin": "abc123"})
    props = mock_update.call_args[0][1]
    text = props["External Post IDs"]["rich_text"][0]["text"]["content"]
    assert json.loads(text) == {"linkedin": "abc123"}


def test_set_posting_error():
    client = NotionClient(api_key="ntn_test")
    with patch.object(client, "update_page") as mock_update:
        client.set_posting_error("page_1", "Rate limited")
    props = mock_update.call_args[0][1]
    assert props["Posting Errors"]["rich_text"][0]["text"]["content"] == "Rate limited"
    assert props["Status"]["select"]["name"] == "To Review"


def test_set_last_scheduled_date():
    client = NotionClient(api_key="ntn_test")
    with patch.object(client, "update_page") as mock_update:
        client.set_last_scheduled_date("page_1", "2026-03-10")
    props = mock_update.call_args[0][1]
    assert props["Last Scheduled Date"]["date"]["start"] == "2026-03-10"


def test_clear_scheduling_fields():
    client = NotionClient(api_key="ntn_test")
    with patch.object(client, "update_page") as mock_update:
        client.clear_scheduling_fields("page_1")
    props = mock_update.call_args[0][1]
    assert props["External Post IDs"]["rich_text"] == []
    assert props["Last Scheduled Date"]["date"] is None
    assert props["Posting Errors"]["rich_text"] == []


def test_extract_row_fields():
    client = NotionClient(api_key="ntn_test")
    page = {
        "id": "page_1",
        "properties": {
            "Title": {"title": [{"plain_text": "My Post"}]},
            "Status": {"select": {"name": "Approved"}},
            "Post Text": {"rich_text": [{"plain_text": "Hello world"}]},
            "LinkedIn Text": {"rich_text": [{"plain_text": "LI specific"}]},
            "X Text": {"rich_text": []},
            "Platforms": {"multi_select": [{"name": "linkedin"}, {"name": "twitter"}]},
            "Publish Date": {"date": {"start": "2026-03-10T12:00:00.000Z"}},
            "Clip URL": {"url": "https://r2.example.com/video.mp4"},
            "External Post IDs": {"rich_text": []},
            "Last Scheduled Date": {"date": None},
            "Posting Errors": {"rich_text": []},
        },
    }
    row = client.extract_row(page)
    assert row["id"] == "page_1"
    assert row["Title"] == "My Post"
    assert row["Post Text"] == "Hello world"
    assert row["LinkedIn Text"] == "LI specific"
    assert row["X Text"] == ""
    assert row["Platforms"] == ["linkedin", "twitter"]
    assert row["Publish Date"] == "2026-03-10T12:00:00.000Z"
    assert row["Clip URL"] == "https://r2.example.com/video.mp4"
    assert row["External Post IDs"] == ""


def test_extract_row_missing_properties():
    """extract_row handles missing properties gracefully."""
    client = NotionClient(api_key="ntn_test")
    page = {"id": "page_2", "properties": {}}
    row = client.extract_row(page)
    assert row["id"] == "page_2"
    assert row["Title"] == ""
    assert row["Platforms"] == []
    assert row["Clip URL"] == ""


def test_replace_page_body_deletes_old_and_appends_new():
    """replace_page_body should DELETE existing blocks then POST new ones."""
    client = NotionClient(api_key="test")

    old_blocks = {"results": [
        {"id": "block-1", "type": "paragraph"},
        {"id": "block-2", "type": "paragraph"},
    ], "has_more": False}

    new_blocks = [
        {"type": "heading_2", "heading_2": {"rich_text": [{"text": {"content": "LinkedIn"}}]}},
        {"type": "paragraph", "paragraph": {"rich_text": [{"text": {"content": "Post text here"}}]}},
    ]

    with patch("social_notion.requests.get") as mock_get, \
         patch("social_notion.requests.delete") as mock_del, \
         patch("social_notion.requests.patch") as mock_patch:
        mock_get.return_value = _mock_response(old_blocks)
        mock_del.return_value = _mock_response({})
        mock_patch.return_value = _mock_response({})

        client.replace_page_body("page-123", new_blocks)

    assert mock_del.call_count == 2
    mock_patch.assert_called_once()
    patch_body = json.loads(mock_patch.call_args.kwargs.get("data") or mock_patch.call_args[1]["data"])
    assert len(patch_body["children"]) == 2


def test_get_parent_video_id():
    """get_parent_video_id should extract relation page ID."""
    client = NotionClient(api_key="test")
    page_data = {
        "properties": {
            "Parent Video": {"relation": [{"id": "video-page-1"}]},
        }
    }
    with patch("social_notion.requests.get") as mock_get:
        mock_get.return_value = _mock_response(page_data)
        result = client.get_parent_video_id("clip-page-1")
    assert result == "video-page-1"


def test_get_parent_video_id_empty():
    """get_parent_video_id returns None when no relation set."""
    client = NotionClient(api_key="test")
    page_data = {"properties": {"Parent Video": {"relation": []}}}
    with patch("social_notion.requests.get") as mock_get:
        mock_get.return_value = _mock_response(page_data)
        result = client.get_parent_video_id("clip-page-1")
    assert result is None


def test_get_page_returns_extracted_row():
    """get_page should GET a page and return an extracted row dict."""
    client = NotionClient(api_key="test")
    page_data = {
        "id": "page-abc",
        "properties": {
            "Title": {"title": [{"plain_text": "Test"}]},
            "Status": {"select": {"name": "Draft"}},
            "Platforms": {"multi_select": [{"name": "linkedin"}]},
            "Publish Date": {"date": {"start": "2026-04-01"}},
        },
    }
    with patch("social_notion.requests.get") as mock_get:
        mock_get.return_value = _mock_response(page_data)
        row = client.get_page("page-abc")
    assert row["id"] == "page-abc"
    assert row["Title"] == "Test"
    assert row["Platforms"] == ["linkedin"]


def test_create_page():
    """create_page should POST to /pages with database parent and properties."""
    client = NotionClient(api_key="test")
    props = {
        "Title": {"title": [{"text": {"content": "Test Post"}}]},
        "Status": {"select": {"name": "Draft"}},
    }
    with patch("social_notion.requests.post") as mock_post:
        mock_post.return_value = _mock_response({"id": "new-page-1"})
        result = client.create_page("db-123", props)
    assert result["id"] == "new-page-1"
    body = json.loads(mock_post.call_args.kwargs.get("data") or mock_post.call_args[1]["data"])
    assert body["parent"]["database_id"] == "db-123"
    assert body["properties"]["Title"]["title"][0]["text"]["content"] == "Test Post"


def test_query_by_title_and_type():
    """query_by_title_and_type should filter by Title and Post Type."""
    client = NotionClient(api_key="test")
    mock_resp = _mock_response({"results": [{"id": "page-1"}], "has_more": False})
    with patch("social_notion.requests.post", return_value=mock_resp) as mock_post:
        results = client.query_by_title_and_type("db-123", "The Fire Drill", "Video Clip")
    body = json.loads(mock_post.call_args.kwargs.get("data") or mock_post.call_args[1]["data"])
    assert body["filter"]["and"][0]["property"] == "Title"
    assert body["filter"]["and"][1]["property"] == "Post Type"
    assert len(results) == 1


def test_extract_row_includes_last_rendered():
    """extract_row should include Last Rendered date field."""
    page = {
        "id": "page-1",
        "properties": {
            "Title": {"title": []},
            "Status": {"select": None},
            "Last Rendered": {"date": {"start": "2026-04-01T10:00:00Z"}},
            "Last edited by": {"last_edited_by": {"id": "bot-user-123"}},
            "Last edited time": {"last_edited_time": "2026-04-01T10:05:00Z"},
            "Platforms": {"multi_select": []},
            "Publish Date": {"date": None},
        },
    }
    row = NotionClient.extract_row(page)
    assert row["Last Rendered"] == "2026-04-01T10:00:00Z"
    assert row["Last edited by"] == "bot-user-123"
    assert row["Last edited time"] == "2026-04-01T10:05:00Z"


def test_extract_row_includes_post_type_and_generation_status():
    """extract_row should include Post Type and Generation Status select fields."""
    page = {
        "id": "page-5",
        "properties": {
            "Title": {"title": []},
            "Status": {"select": None},
            "Platforms": {"multi_select": []},
            "Publish Date": {"date": None},
            "Post Type": {"select": {"name": "Video Clip"}},
            "Generation Status": {"select": {"name": "Generated"}},
        },
    }
    row = NotionClient.extract_row(page)
    assert row["Post Type"] == "Video Clip"
    assert row["Generation Status"] == "Generated"


def test_extract_row_post_type_missing():
    """extract_row returns empty string when Post Type is absent."""
    page = {"id": "page-6", "properties": {}}
    row = NotionClient.extract_row(page)
    assert row["Post Type"] == ""
    assert row["Generation Status"] == ""
