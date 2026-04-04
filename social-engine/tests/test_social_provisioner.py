import json
import os
from unittest.mock import patch, MagicMock


def test_load_manifest(tmp_path):
    """load_manifest should parse final/manifest.json into clip dicts."""
    from social_provisioner import load_manifest

    manifest = {
        "clips": [
            {"filename": "01-growth.mp4", "story_id": "growth", "clip_url": "https://example.com/growth.mp4", "thumbnail_url": "https://example.com/growth.png", "short_url": ""},
            {"filename": "02-revenue.mp4", "story_id": "revenue", "clip_url": "https://example.com/revenue.mp4", "thumbnail_url": "", "short_url": ""},
        ]
    }
    final_dir = tmp_path / "final"
    final_dir.mkdir()
    (final_dir / "manifest.json").write_text(json.dumps(manifest))

    result = load_manifest(str(tmp_path))
    assert len(result) == 2
    assert result[0]["story_id"] == "growth"
    assert result[1]["clip_url"] == "https://example.com/revenue.mp4"


def test_load_manifest_fallback_scans_mp4s(tmp_path):
    """load_manifest should fall back to scanning *.mp4 when no manifest.json exists."""
    from social_provisioner import load_manifest

    final_dir = tmp_path / "final"
    final_dir.mkdir()
    (final_dir / "01-growth-short-en.mp4").write_text("fake")
    (final_dir / "02-revenue-short-en.mp4").write_text("fake")

    result = load_manifest(str(tmp_path))
    assert len(result) == 2
    assert result[0]["story_id"] == "growth"
    assert result[0]["clip_url"] == ""


def test_load_stories(tmp_path):
    """load_stories should parse editorial/stories.json."""
    from social_provisioner import load_stories

    stories = {
        "stories": [
            {"id": "growth", "title": "Surprising Growth", "engagement_score": 9, "hook_candidates": [{"text": "We grew 10x"}], "standalone_rationale": "Concrete growth story", "start": 45.2, "end": 120.5},
            {"id": "revenue", "title": "Revenue Dropped", "engagement_score": 6, "hook_candidates": [{"text": "Revenue dropped 40%"}], "standalone_rationale": "Warning story", "start": 200.0, "end": 280.0},
        ]
    }
    ed_dir = tmp_path / "editorial"
    ed_dir.mkdir()
    (ed_dir / "stories.json").write_text(json.dumps(stories))

    result = load_stories(str(tmp_path))
    assert len(result) == 2
    assert result[0]["id"] == "growth"


def test_load_stories_missing_file(tmp_path):
    """load_stories should return empty list when stories.json doesn't exist."""
    from social_provisioner import load_stories
    result = load_stories(str(tmp_path))
    assert result == []


def test_select_text_insights():
    """select_text_insights should pick top N stories with score >= 7."""
    from social_provisioner import select_text_insights

    stories = [
        {"id": "a", "engagement_score": 9},
        {"id": "b", "engagement_score": 8},
        {"id": "c", "engagement_score": 7},
        {"id": "d", "engagement_score": 6},
        {"id": "e", "engagement_score": 5},
    ]

    result = select_text_insights(stories, target_count=3)
    assert len(result) == 3
    assert result[0]["id"] == "a"
    assert result[2]["id"] == "c"


def test_select_text_insights_fewer_than_target():
    """select_text_insights should return fewer if not enough qualify."""
    from social_provisioner import select_text_insights

    stories = [
        {"id": "a", "engagement_score": 8},
        {"id": "b", "engagement_score": 5},
    ]

    result = select_text_insights(stories, target_count=5)
    assert len(result) == 1


def test_slug_from_filename():
    """slug_from_filename should extract story ID from final filenames."""
    from social_provisioner import slug_from_filename

    assert slug_from_filename("01-revenue-dropped-short-en.mp4") == "revenue-dropped"
    assert slug_from_filename("02-growth-long-en.mp4") == "growth"
    assert slug_from_filename("custom-name.mp4") == "custom-name"


def test_build_video_clip_properties():
    """build_card_properties should construct Notion properties for a video clip."""
    from social_provisioner import build_card_properties

    props = build_card_properties(
        title="Surprising Growth",
        post_type="Video Clip",
        hook="We grew 10x",
        description="Concrete growth story",
        platforms=["linkedin", "twitter", "instagram"],
        clip_url="https://example.com/growth.mp4",
        thumbnail_url="https://example.com/thumb.png",
        short_url="",
    )

    assert props["Title"]["title"][0]["text"]["content"] == "Surprising Growth"
    assert props["Status"]["select"]["name"] == "Draft"
    assert props["Post Type"]["select"]["name"] == "Video Clip"
    assert props["Generation Status"]["select"]["name"] == "Pending"
    assert props["Hook Sentence"]["rich_text"][0]["text"]["content"] == "We grew 10x"
    assert props["Clip URL"]["url"] == "https://example.com/growth.mp4"
    assert len(props["Platforms"]["multi_select"]) == 3


def test_build_text_insight_properties():
    """build_card_properties for text insight should include timestamp tag and no clip URL."""
    from social_provisioner import build_card_properties

    props = build_card_properties(
        title="Revenue Dropped",
        post_type="Text Insight",
        hook="Revenue dropped 40%",
        description="Warning story",
        platforms=["linkedin"],
        clip_start=200.0,
        clip_end=280.0,
    )

    assert props["Post Type"]["select"]["name"] == "Text Insight"
    assert props["Generation Status"]["select"]["name"] == "Pending"
    desc_text = props["Description"]["rich_text"][0]["text"]["content"]
    assert "[clip:200.0-280.0]" in desc_text
    assert props["Clip URL"]["url"] is None


def test_provision_video_clips_creates_cards():
    """provision_video_clips should create a card for each manifest clip."""
    from social_provisioner import provision_video_clips

    clips = [
        {"filename": "01-growth.mp4", "story_id": "growth", "clip_url": "https://example.com/g.mp4", "thumbnail_url": "", "short_url": ""},
    ]
    stories = [
        {"id": "growth", "title": "Growth Story", "hook_candidates": [{"text": "We grew"}], "standalone_rationale": "Good story", "start": 10.0, "end": 50.0, "engagement_score": 9},
    ]

    mock_notion = MagicMock()
    mock_notion.query_by_title_and_type.return_value = []
    mock_notion.create_page.return_value = {"id": "new-page-1"}

    result = provision_video_clips(clips, stories, "db-123", ["linkedin"], mock_notion)
    assert len(result) == 1
    assert result[0] == "new-page-1"
    mock_notion.create_page.assert_called_once()


def test_provision_video_clips_skips_duplicates():
    """provision_video_clips should skip cards that already exist."""
    from social_provisioner import provision_video_clips

    clips = [{"filename": "01-growth.mp4", "story_id": "growth", "clip_url": "", "thumbnail_url": "", "short_url": ""}]
    stories = [{"id": "growth", "title": "Growth Story", "hook_candidates": [{"text": "Hook"}], "standalone_rationale": "", "start": 0, "end": 0, "engagement_score": 9}]

    mock_notion = MagicMock()
    mock_notion.query_by_title_and_type.return_value = [{"id": "existing-page"}]

    result = provision_video_clips(clips, stories, "db-123", ["linkedin"], mock_notion)
    assert len(result) == 0
    mock_notion.create_page.assert_not_called()


def test_provision_text_insights_creates_cards():
    """provision_text_insights should create cards for selected moments."""
    from social_provisioner import provision_text_insights

    stories = [
        {"id": "growth", "title": "Growth Story", "hook_candidates": [{"text": "We grew"}], "standalone_rationale": "Great", "start": 10.0, "end": 50.0, "engagement_score": 9},
    ]

    mock_notion = MagicMock()
    mock_notion.query_by_title_and_type.return_value = []
    mock_notion.create_page.return_value = {"id": "new-text-1"}

    result = provision_text_insights(stories, 1, "db-123", ["linkedin"], mock_notion)
    assert len(result) == 1
    assert result[0] == "new-text-1"


def test_generate_content_for_cards_calls_copywriter():
    """generate_content_for_cards should call generate_social_copy for each page ID."""
    from social_provisioner import generate_content_for_cards

    mock_notion = MagicMock()
    mock_notion.update_page.return_value = {}
    mock_notion.get_page.return_value = {"Generation Status": "Pending", "Posting Errors": ""}

    config = {"anthropic_api_key": "sk-test"}

    with patch("social_provisioner.generate_social_copy") as mock_gen:
        generate_content_for_cards(["page-1", "page-2"], mock_notion, config)

    assert mock_gen.call_count == 2


def test_generate_content_skips_completed_cards():
    """generate_content_for_cards should skip cards with Generation Status = Complete."""
    from social_provisioner import generate_content_for_cards

    mock_notion = MagicMock()
    mock_notion.get_page.return_value = {"Generation Status": "Complete", "Posting Errors": ""}

    config = {"anthropic_api_key": "sk-test"}

    with patch("social_provisioner.generate_social_copy") as mock_gen:
        generate_content_for_cards(["page-1"], mock_notion, config)

    mock_gen.assert_not_called()


def test_generate_content_handles_failure():
    """generate_content_for_cards should set Failed status on error and continue."""
    from social_provisioner import generate_content_for_cards

    mock_notion = MagicMock()
    mock_notion.get_page.return_value = {"Generation Status": "Pending", "Posting Errors": ""}

    config = {"anthropic_api_key": "sk-test"}

    with patch("social_provisioner.generate_social_copy", side_effect=[Exception("API error"), None]):
        generate_content_for_cards(["page-fail", "page-ok"], mock_notion, config)

    # First card should be marked Failed
    fail_call = mock_notion.update_page.call_args_list[0]
    assert fail_call[0][0] == "page-fail"
    props = fail_call[0][1]
    assert props["Generation Status"]["select"]["name"] == "Failed"


def test_provision_session_full_flow(tmp_path):
    """provision_session should run all three phases."""
    from social_provisioner import provision_session

    manifest = {"clips": [
        {"filename": "01-growth.mp4", "story_id": "growth", "clip_url": "https://example.com/g.mp4", "thumbnail_url": "", "short_url": ""},
    ]}
    final_dir = tmp_path / "final"
    final_dir.mkdir()
    (final_dir / "manifest.json").write_text(json.dumps(manifest))

    stories_data = {"stories": [
        {"id": "growth", "title": "Growth Story", "hook_candidates": [{"text": "We grew"}], "standalone_rationale": "Good", "start": 10.0, "end": 50.0, "engagement_score": 9},
        {"id": "insight", "title": "Deep Insight", "hook_candidates": [{"text": "Here's the thing"}], "standalone_rationale": "Insightful", "start": 100.0, "end": 160.0, "engagement_score": 8},
    ]}
    ed_dir = tmp_path / "editorial"
    ed_dir.mkdir()
    (ed_dir / "stories.json").write_text(json.dumps(stories_data))

    mock_notion = MagicMock()
    mock_notion.query_by_title_and_type.return_value = []
    mock_notion.create_page.side_effect = [{"id": "vid-1"}, {"id": "txt-1"}]
    mock_notion.get_page.return_value = {"Generation Status": "Pending", "Posting Errors": ""}

    config = {
        "anthropic_api_key": "sk-test",
        "clients": {
            "testclient": {
                "notion_database_id": "db-123",
                "platforms": {"linkedin": {}, "twitter": {}},
            }
        }
    }

    with patch("social_provisioner.generate_social_copy"), \
         patch("social_provisioner.generate_graphic_for_card"), \
         patch("social_provisioner.NotionClient", return_value=mock_notion):
        provision_session(str(tmp_path), "testclient", config)

    assert mock_notion.create_page.call_count == 2
